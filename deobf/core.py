"""Pipeline: static passes -> static loadstring unwrap -> sandboxed execution (layer capture) -> repeat."""
from dataclasses import asdict, dataclass, field
from time import monotonic

from . import astclean, decrypt, logger, passes, patches, recon, sandbox
from . import fetch as fetchmod
from .budget import remaining, time_budget

MAX_FETCH = 8
MAX_CLEAN_BYTES = 400_000
MAX_SOURCE_BYTES = 2 * 1024 * 1024

HEADER = "-- Analyzed by A3K Deobfuscator"


def with_header(text, output_kind=None):
    return HEADER + (" | " + output_kind if output_kind else "") + "\n" + text
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
    payload_engine: str = "unknown"
    layer_history: list = field(default_factory=list)
    elapsed_seconds: float = 0
    stop_reason: str = "complete"
    unresolved_urls: list = field(default_factory=list)
    compatibility: list = field(default_factory=list)
    assumptions: list = field(default_factory=list)
    callback_runs: list = field(default_factory=list)
    callback_captures: list = field(default_factory=list)
    task_errors: list = field(default_factory=list)
    simulation_limits: list = field(default_factory=list)

    @property
    def callback_errors(self):
        return [item for item in self.task_errors if item.get("phase") == "callback"]

    @property
    def shows_recon(self):
        vm = BY_NAME.get(self.payload_engine) and BY_NAME[self.payload_engine].vm
        return bool(self.recon and (vm or self.obf_after >= 25))

    @property
    def body(self):
        """The deliverable without the header line."""
        if self.shows_recon:
            return self.recon
        return self.source

    @property
    def best(self):
        """Readable output: recovered source, or the code the script ran when a vm payload never reaches loadstring."""
        warning = ""
        if self.recovery_status == "not-recovered":
            warning = "-- Readable source was not recovered. The source below is retained for analysis.\n"
        return with_header(warning + self.body, self.output_kind)

    @property
    def is_logger(self):
        return logger.is_logger(self.findings)

    @property
    def output_kind(self):
        if self.stop_reason == "syntax-error":
            return "unexecuted-source"
        if self.shows_recon:
            return "behavior-trace"
        if self.unresolved_urls:
            return "partial-source"
        if self.obf_after >= 25:
            return "still-obfuscated-source"
        if self.layers:
            return "unwrapped-source"
        return "cleaned-source" if self.changed else "unchanged-source"

    @property
    def recovery_status(self):
        if self.output_kind in ("unexecuted-source", "still-obfuscated-source"):
            return "not-recovered"
        if self.shows_recon:
            return "behavior-only"
        if self.unresolved_urls or self.stop_reason != "complete":
            return "partial"
        return "source-output" if self.changed or self.layers else "unchanged"

    def to_report(self):
        report = asdict(self)
        report.update(output_kind=self.output_kind, recovery_status=self.recovery_status, callback_errors=self.callback_errors, score_kind="heuristic evidence, not probability")
        return report


def _resolve_engine(src, forced):
    ranked, score = detect(src)
    if forced:
        conf = dict(ranked).get(forced, 0)
        return forced, conf, ranked, score
    if ranked:
        return ranked[0][0], ranked[0][1], ranked, score
    return "unknown", 0, ranked, score


def deobfuscate(src, engine=None, dynamic=True, max_layers=8, seconds=10, fetch=False, timeout=60):
    if len(src) > MAX_SOURCE_BYTES:
        raise ValueError("script exceeds 2 MB input limit")
    if not 1 <= max_layers <= 32 or not 0 < seconds <= 30 or not 0 < timeout <= 300:
        raise ValueError("limits: max_layers 1-32, seconds >0 to 30, timeout >0 to 300")
    started = monotonic()
    with time_budget(timeout):
        result = _deobfuscate(src, engine, dynamic, max_layers, seconds, fetch)
    result.elapsed_seconds = round(monotonic() - started, 3)
    return result


def _deobfuscate(src, engine, dynamic, max_layers, seconds, fetch):
    """fetch=True lets scripts that `loadstring(game:HttpGet(url))()` pull their next stage: the url is downloaded
    by this process (public addresses only, see fetch.py) and handed to the sandbox as the HttpGet result."""
    if engine and engine not in BY_NAME:
        raise ValueError("unknown engine %r" % engine)
    original = src
    cur = src[3:] if src.startswith("\xef\xbb\xbf") else src
    eng, conf, ranked, score = _resolve_engine(cur, engine)
    res = Result(source=cur, engine=eng, confidence=conf, candidates=ranked, obf_score=score)
    ran = None
    http = {}
    seen = {cur}
    findings = logger.scan(cur)
    unresolved = []

    def advance(inner, method):
        if len(inner) > MAX_SOURCE_BYTES:
            res.stop_reason = "decoded-size-limit"
            res.notes.append("decoded layer exceeds 2 MB; kept previous layer")
            return False
        if inner in seen:
            res.stop_reason = "layer-cycle"
            res.notes.append("repeated layer detected; stopped decoding")
            return False
        seen.add(inner)
        candidates, score = detect(cur)
        res.layer_history.append({"index": res.layers + 1, "method": method,
                                  "engine": candidates[0][0] if candidates else "unknown",
                                  "candidates": candidates, "score": score,
                                  "input_bytes": len(cur), "output_bytes": len(inner)})
        res.layers += 1
        findings.extend(logger.scan(inner))
        return True

    for layer in range(max_layers):
        if remaining() <= 0:
            res.stop_reason = "time-budget"
            res.notes.append("analysis time budget exhausted; returning partial result")
            break
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
            if not advance(inner, "literal-loader"):
                break
            cur, ran = inner, None
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
        res.compatibility += [note for note in ran.compatibility if note not in res.compatibility]
        findings.extend(logger.scan_runtime("\n".join(ran.urls + ran.posts + ran.constants)))
        while fetch and len(http) < MAX_FETCH and remaining() > 0:
            new = [u for u in ran.gets if u not in http][: MAX_FETCH - len(http)]
            if not new:
                break
            for u in new:
                if remaining() <= 0:
                    break
                try:
                    http[u] = fetchmod.fetch(u, timeout=remaining(15))
                    res.notes.append("fetched " + u)
                except fetchmod.FetchError as e:
                    http[u] = ""
                    res.notes.append("fetch failed %s: %s" % (u, e))
            ran = sandbox.run(exec_src, seconds, http=http)
            res.compatibility += [note for note in ran.compatibility if note not in res.compatibility]
            findings.extend(logger.scan_runtime("\n".join(ran.urls + ran.posts + ran.constants)))
        unresolved.extend(url for url in ran.unresolved_gets if url not in unresolved)
        if ran.unresolved_gets and ran.captures and not ran.captures[-1].strip():
            res.stop_reason = "unresolved-http"
            res.notes.append("remote payload was not retrieved; kept the loader instead of treating an empty response as recovered source")
            break
        if ran.captures and ran.captures[-1] != cur:
            inner = ran.captures[-1]
            if not advance(inner, "sandbox-capture"):
                break
            if len(set(ran.captures)) > 1:
                res.notes.append("multiple runtime chunks captured; following the last chunk")
            cur, ran = inner, None
            res.notes.append("captured loadstring layer %d" % res.layers)
            continue
        break
    else:
        res.stop_reason = "layer-limit"
        res.notes.append("maximum layer count reached; remaining payload kept")
    if ran is not None:
        res.urls, res.posts, res.trace, res.prints, res.constants = ran.urls, ran.posts, ran.trace, ran.prints, ran.constants
        res.assumptions, res.callback_runs, res.task_errors, res.simulation_limits = ran.assumptions, ran.callback_runs, ran.task_errors, ran.simulation_limits
        res.callback_captures = ran.callback_captures
        for capture in ran.callback_captures:
            findings.extend(logger.scan(capture["source"]))
        res.recon = reconstruct(ran)
        res.error = ran.error
        if ran.timed_out:
            res.notes.append("sandbox hit wall-clock limit")
            res.stop_reason = "time-budget" if remaining() <= 0 else "sandbox-time-limit"
        elif ran.error:
            res.stop_reason = "syntax-error" if ran.error.startswith("syntax:") else "runtime-error"
            if res.stop_reason == "syntax-error":
                res.notes.append("script did not execute; static changes are not verified payload recovery")
        elif ran.task_errors:
            res.stop_reason = "simulation-errors"
            res.notes.append("%d task/callback error(s) occurred during simulation; see task_errors and callback_runs in the report" % len(ran.task_errors))
        elif ran.simulation_limits:
            res.stop_reason = "simulation-limit"
        res.notes.extend(ran.simulation_limits)
    res.unresolved_urls = unresolved
    if unresolved:
        if res.stop_reason == "complete":
            res.stop_reason = "unresolved-http"
        res.notes.append("%d remote URL(s) have no supplied response; analysis is partial" % len(unresolved))
        if not fetch:
            res.notes.append("nested downloads are disabled; use --fetch in the CLI or analyze the downloaded Lua file directly")
    if res.layers and not engine:
        # re-detect on the innermost payload only when the outer wrapper told us nothing
        e2, c2, r2, s2 = _resolve_engine(cur, None)
        if not conf and c2:
            res.engine, res.confidence, res.candidates = e2, c2, r2
    res.payload_engine = _resolve_engine(cur, None)[0]
    if BY_NAME.get(res.payload_engine) and BY_NAME[res.payload_engine].vm:
        res.notes.append("vm-based protector: payload never reaches loadstring, see the behaviour trace (.l)")
    if dynamic and remaining() > 0 and not unresolved:
        vm_engine = BY_NAME.get(res.payload_engine) and BY_NAME[res.payload_engine].vm
        if not vm_engine and len(cur) < MAX_CLEAN_BYTES:
            cleaned, info = astclean.clean(cur, seconds=seconds)
            if cleaned is None:
                res.notes.append("ast cleanup skipped: " + info)
            elif info["nodes_after"] <= 0.85 * info["nodes_before"] or info["renamed"] >= 3 or info["evaluated"]:
                cur = cleaned
                res.notes.append(
                    "ast cleanup (verified by re-running): removed %d, evaluated %d, renamed %d" % (info["removed"], info["evaluated"], info["renamed"])
                )
    res.source = cur
    res.changed = cur != original
    res.obf_after = obfuscation_score(cur)
    if res.output_kind == "still-obfuscated-source":
        res.notes.append("readable source was not recovered; remaining source still has obfuscation indicators")
    res.findings = logger.scan(cur)
    blob = "\n".join([res.recon] + ["-- url: " + u for u in res.urls] + res.constants + res.posts)
    merged = {}
    for f in findings + res.findings + logger.scan_runtime(blob):
        merged.setdefault((f.id, f.snippet), f)
    res.findings = list(merged.values())
    res.notes.extend("compatibility: " + note for note in res.compatibility)
    return res


def reconstruct(r):
    """Observable behaviour of a sandbox run as a pseudo-Luau script."""
    if not (r.trace or r.urls or r.constants or r.captures or r.posts):
        return ""
    out = ["-- reconstructed observable behaviour (partial observations, not original source)",
           "-- DIAGNOSTIC ONLY: not executable code. Callback bodies were not decompiled.",
           "-- Each callback section is a separate simulation with fresh startup state."]
    out.extend("-- assumption: " + note for note in r.assumptions)
    out.extend("-- simulation limit: " + note for note in r.simulation_limits)
    out.append("-- Startup observations:")
    for u in r.urls:
        out.append("-- url: " + u)
    for u in r.unresolved_gets:
        out.append("-- not retrieved (partial analysis): " + u)
    out += recon.tidy(r.trace)
    for i, c in enumerate(r.captures, 1):
        out.append("-- loadstring chunk %d (%d bytes)" % (i, len(c)))
    for capture in r.callback_captures:
        out.append("-- callback loadstring chunk: %s (%d bytes; source is in the JSON report)" % (capture["callback"], len(capture["source"])))
    for p in r.posts:
        out.append("-- POST " + p)
    if r.error:
        out.append("-- stopped: " + r.error)
    if r.timed_out:
        out.append("-- stopped: wall-clock limit")
    if r.constants:
        out.append("-- decoded strings (%d):" % len(r.constants))
        out += ["--   " + c.replace("\n", "\\n")[:200] for c in r.constants[:400]]
    # Observations cannot reproduce the original control flow. Comment every
    # physical line, including hostile/multiline values, so nothing masquerades
    # as executable source or introduces undefined callback variables.
    return "\n".join(line if line.startswith("--") else "-- " + line for item in out for line in item.splitlines())


def trace_behavior(src, seconds=10):
    """Run in the recording sandbox; returns (reconstruction, Run)."""
    if len(src) > MAX_SOURCE_BYTES:
        raise ValueError("script exceeds 2 MB input limit")
    src, _ = passes.run_static(src)
    r = sandbox.run(src, seconds)
    return reconstruct(r) or "-- nothing observable happened", r
