"""Pipeline: static passes -> static loadstring unwrap -> sandboxed execution (layer capture) -> repeat."""
from dataclasses import dataclass, field

from . import decrypt, logger, passes, sandbox
from .detect import BY_NAME, detect


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
    error: str = ""
    changed: bool = False

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


def deobfuscate(src, engine=None, dynamic=True, max_layers=8, seconds=10):
    if engine and engine not in BY_NAME:
        raise ValueError("unknown engine %r" % engine)
    original = cur = src
    eng, conf, ranked, score = _resolve_engine(cur, engine)
    res = Result(source=cur, engine=eng, confidence=conf, candidates=ranked, obf_score=score)
    ran = None
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
        ran = sandbox.run(cur, seconds)
        if ran.captures and ran.captures[-1] != cur:
            cur, res.layers = ran.captures[-1], res.layers + 1
            res.notes.append("captured loadstring layer %d" % res.layers)
            continue
        break
    if ran is not None:
        res.urls, res.posts, res.trace, res.prints = ran.urls, ran.posts, ran.trace, ran.prints
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
    res.findings = logger.scan(cur)
    return res


def trace_behavior(src, seconds=10):
    """Run in the recording sandbox and return observable behaviour as a pseudo-Luau script."""
    src, _ = passes.run_static(src)
    r = sandbox.run(src, seconds)
    out = ["-- reconstructed observable behaviour (api calls the script made, in order)"]
    for u in r.urls:
        out.append("-- url: " + u)
    out += r.trace
    for i, c in enumerate(r.captures, 1):
        out.append("-- loadstring chunk %d (%d bytes)" % (i, len(c)))
    for p in r.prints:
        out.append("-- print: " + p)
    for p in r.posts:
        out.append("-- POST " + p)
    if r.error:
        out.append("-- stopped: " + r.error)
    if r.timed_out:
        out.append("-- stopped: wall-clock limit")
    return "\n".join(out), r
