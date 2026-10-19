"""Pipeline: static passes -> static loadstring unwrap -> sandboxed execution (layer capture) -> repeat."""
from dataclasses import dataclass, field

from . import decrypt, logger, passes, patches, sandbox
from . import fetch as fetchmod

MAX_FETCH = 8

# text is handled as latin-1 (one char per byte), so the utf-8 bytes of the emoji are carried as latin-1 chars
HEADER = "-- Deobfused By Rift \U0001F595 (Get better \U0001F62D\U0001FAF5)".encode("utf-8").decode("latin-1")


def with_header(text):
    return HEADER + "\n" + text
from .detect import BY_NAME, detect, obfuscation_score


@dataclass
class Result:
    source: str
    engine: str = "unknown"
    confidence: int = 0
    candidates: list = field(default_factory=list)
    obf_score: int = 0
    layers: int = 0
    notes: list = field(default_factory=list)
    findings: list = field(default_factory=list)
    urls: list = field(default_factory=list)
    posts: list = field(default_factory=list)
    trace: list = field(default_factory=list)
    prints: list = field(default_factory=list)
    constants: list = field(default_factory=list)
    recon: str = ""
    obf_after: int = 0
    error: str = ""
    changed: bool = False

    @property
    def best(self):
        """Readable output: recovered source, or the behaviour reconstruction for vm payloads that never reach loadstring."""
        vm = BY_NAME.get(self.engine) and BY_NAME[self.engine].vm
        if self.recon and (not self.changed or (not self.layers and (vm or self.obf_after >= 25))):
            code = [ln for ln in self.recon.splitlines() if not ln.startswith("--")]
            return with_header("\n".join(code) if code else self.recon)
        return with_header(self.source)

    @property
    def is_logger(self):
        return logger.is_logger(self.findings)


def _resolve_engine(src, forced):
    ranked, score = detect(src)
    if forced:
        conf = dict(ranked).get(forced, 0)
        return forced, conf, ranked, score
    if ranked:
        return ranked[0][0], ranked[0][1], ranked, score
    return "unknown", 0, ranked, score


def deobfuscate(src, engine=None, dynamic=True, max_layers=8, seconds=10, fetch=False):
    """fetch=True lets scripts that `loadstring(game:HttpGet(url))()` pull their next stage: the url is downloaded
    by this process (public addresses only, see fetch.py) and handed to the sandbox as the HttpGet result."""
    if engine and engine not in BY_NAME:
        raise ValueError("unknown engine %r" % engine)
    original = cur = src
    eng, conf, ranked, score = _resolve_engine(cur, engine)
    res = Result(source=cur, engine=eng, confidence=conf, candidates=ranked, obf_score=score)
    ran = None
    http = {}
    for layer in range(max_layers):
        cur, notes = passes.run_static(cur)
        res.notes += ["static:" + n for n in notes]
        if dynamic:
            for _ in range(3):
                folded, n = decrypt.fold(cur)
                if not n:
                    break
                cur, notes = passes.run_static(folded)
                res.notes.append("decoder: inlined %d call(s)" % n)
        inner = passes.unwrap_static(cur)
        if inner is not None:
            cur, res.layers = inner, res.layers + 1
            res.notes.append("unwrapped literal loadstring")
            continue
        if not dynamic:
            break
        exec_src, fixed = patches.apply(cur)
        for name in fixed:
            note = "patched obfuscator bug: " + name
            if note not in res.notes:
                res.notes.append(note)
        ran = sandbox.run(exec_src, seconds, http=http)
        while fetch and len(http) < MAX_FETCH:
            new = [u for u in ran.gets if u not in http][: MAX_FETCH - len(http)]
            if not new:
                break
            for u in new:
                try:
                    http[u] = fetchmod.fetch(u)
                    res.notes.append("fetched " + u)
                except fetchmod.FetchError as e:
                    http[u] = ""
                    res.notes.append("fetch failed %s: %s" % (u, e))
            ran = sandbox.run(exec_src, seconds, http=http)
        if ran.captures and ran.captures[-1] != cur:
            cur, res.layers = ran.captures[-1], res.layers + 1
            res.notes.append("captured loadstring layer %d" % res.layers)
            continue
        break
    if ran is not None:
        res.urls, res.posts, res.trace, res.prints, res.constants = ran.urls, ran.posts, ran.trace, ran.prints, ran.constants
        res.recon = reconstruct(ran)
        res.error = ran.error
        if ran.timed_out:
            res.notes.append("sandbox hit wall-clock limit")
    if res.layers and not engine:
        # re-detect on the innermost payload only when the outer wrapper told us nothing
        e2, c2, r2, s2 = _resolve_engine(cur, None)
        if not conf and c2:
            res.engine, res.confidence, res.candidates = e2, c2, r2
    if BY_NAME.get(res.engine) and BY_NAME[res.engine].vm and not res.layers:
        res.notes.append("vm-based protector: payload never reaches loadstring, see the behaviour trace (.l)")
    res.source = cur
    res.changed = cur != original
    res.obf_after = obfuscation_score(cur)
    res.findings = logger.scan(cur)
    return res


def reconstruct(r):
    """Observable behaviour of a sandbox run as a pseudo-Luau script."""
    if not (r.trace or r.urls or r.constants or r.captures or r.posts):
        return ""
    out = ["-- reconstructed observable behaviour (api calls the script made, in order)"]
    for u in r.urls:
        out.append("-- url: " + u)
    out += r.trace
    for i, c in enumerate(r.captures, 1):
        out.append("-- loadstring chunk %d (%d bytes)" % (i, len(c)))
    for p in r.posts:
        out.append("-- POST " + p)
    if r.error:
        out.append("-- stopped: " + r.error)
    if r.timed_out:
        out.append("-- stopped: wall-clock limit")
    if r.constants:
        out.append("-- decoded strings (%d):" % len(r.constants))
        out += ["--   " + c.replace("\n", "\\n")[:200] for c in r.constants[:400]]
    return "\n".join(out)


def trace_behavior(src, seconds=10):
    """Run in the recording sandbox; returns (reconstruction, Run)."""
    src, _ = passes.run_static(src)
    r = sandbox.run(src, seconds)
    return reconstruct(r) or "-- nothing observable happened", r
