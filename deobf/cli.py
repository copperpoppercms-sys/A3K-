import argparse
import json
import sys

from . import analyze, deobfuscate, trace_behavior
from .detect import BY_NAME


def main(argv=None):
    ap = argparse.ArgumentParser(prog="deobf", description="A3K Deobfuscator — Lua/Luau script analysis and recovery")
    ap.add_argument("file", help="input script, or - for stdin")
    ap.add_argument("-e", "--engine", choices=sorted(BY_NAME), help="force an engine instead of auto-detect")
    ap.add_argument("-o", "--out", help="write result here (default stdout)")
    ap.add_argument("--detect", action="store_true", help="only identify the obfuscator")
    ap.add_argument("--trace", action="store_true", help="print observable behaviour instead of deobfuscating")
    ap.add_argument("--fetch", action="store_true", help="download HttpGet urls the script requests (public addresses only) and follow them")
    ap.add_argument("--static", action="store_true", help="skip sandbox execution")
    ap.add_argument("--json", action="store_true", help="write a machine-readable report instead of Lua")
    ap.add_argument("--seconds", type=float, default=10, help="CPU seconds per sandbox run (up to 30)")
    ap.add_argument("--timeout", type=float, default=60, help="shared analysis budget in seconds (up to 300)")
    ap.add_argument("--max-layers", type=int, default=8, help="maximum decoded layers (1-32)")
    a = ap.parse_args(argv)
    if not 0 < a.seconds <= 30 or not 0 < a.timeout <= 300 or not 1 <= a.max_layers <= 32:
        ap.error("--seconds must be >0 to 30, --timeout >0 to 300, --max-layers 1-32")
    if a.trace and a.static:
        ap.error("--trace executes the script and cannot be combined with --static")
    from .core import MAX_SOURCE_BYTES
    try:
        if a.file == "-":
            raw = sys.stdin.buffer.read(MAX_SOURCE_BYTES + 1)
        else:
            with open(a.file, "rb") as stream:
                raw = stream.read(MAX_SOURCE_BYTES + 1)
    except OSError as e:
        ap.error(str(e))
    if len(raw) > MAX_SOURCE_BYTES:
        ap.error("script exceeds 2 MB input limit")
    src = raw.decode("latin-1")
    if a.detect:
        rep = analyze(src)
        if a.json:
            text = json.dumps(rep, indent=2, ensure_ascii=True)
            _write(text, a.out)
            return
        print("obfuscation score: %d/100" % rep["score"])
        for n, c, why in rep["engines"] or [("unknown", 0, [])]:
            print("%-14s %d/100  %s" % (n, c, "; ".join(why)))
        if rep["ambiguous"]:
            print("overlapping signatures; producer attribution is uncertain")
        print("techniques: " + (", ".join("%s (%s)" % t for t in rep["techniques"]) or "none seen"))
        return
    if a.trace:
        from dataclasses import asdict
        from .budget import time_budget
        with time_budget(a.timeout):
            text, run = trace_behavior(src, seconds=a.seconds)
        if a.json:
            text = json.dumps({"output_kind": "behavior-trace", "trace": text, "run": asdict(run)}, indent=2, ensure_ascii=True)
    else:
        r = deobfuscate(src, engine=a.engine, dynamic=not a.static, fetch=a.fetch, seconds=a.seconds, timeout=a.timeout, max_layers=a.max_layers)
        print("engine=%s (match %d/100) layers=%d status=%s notes=%s" % (r.engine, r.confidence, r.layers, r.stop_reason, ",".join(r.notes) or "-"), file=sys.stderr)
        for f in r.findings:
            print("logger-scan [%s] line %d: %s" % (f.severity, f.line, f.desc), file=sys.stderr)
        text = json.dumps(r.to_report(), indent=2, ensure_ascii=True) if a.json else r.best
    _write(text, a.out)


def _write(text, path):
    data = text.encode("latin-1", "replace")
    if path:
        with open(path, "wb") as stream:
            stream.write(data)
    else:
        sys.stdout.buffer.write(data + b"\n")


if __name__ == "__main__":
    main()
