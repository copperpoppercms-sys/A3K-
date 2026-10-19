import argparse
import sys

from . import deobfuscate, detect, trace_behavior
from .detect import BY_NAME


def main(argv=None):
    ap = argparse.ArgumentParser(prog="deobf", description="Lua/Luau deobfuscator")
    ap.add_argument("file", help="input script, or - for stdin")
    ap.add_argument("-e", "--engine", choices=sorted(BY_NAME), help="force an engine instead of auto-detect")
    ap.add_argument("-o", "--out", help="write result here (default stdout)")
    ap.add_argument("--detect", action="store_true", help="only identify the obfuscator")
    ap.add_argument("--trace", action="store_true", help="print observable behaviour instead of deobfuscating")
    ap.add_argument("--static", action="store_true", help="skip sandbox execution")
    a = ap.parse_args(argv)
    raw = sys.stdin.buffer.read() if a.file == "-" else open(a.file, "rb").read()
    src = raw.decode("latin-1")
    if a.detect:
        ranked, score = detect(src)
        print("obfuscation score: %d/100" % score)
        for n, c in ranked or [("unknown", 0)]:
            print("%-14s %d%%" % (n, c))
        return
    if a.trace:
        text = trace_behavior(src)[0]
    else:
        r = deobfuscate(src, engine=a.engine, dynamic=not a.static)
        print("engine=%s (%d%%) layers=%d notes=%s" % (r.engine, r.confidence, r.layers, ",".join(r.notes) or "-"), file=sys.stderr)
        for f in r.findings:
            print("logger-scan [%s] line %d: %s" % (f.severity, f.line, f.desc), file=sys.stderr)
        text = r.source
    data = text.encode("latin-1", "replace")
    if a.out:
        open(a.out, "wb").write(data)
    else:
        sys.stdout.buffer.write(data + b"\n")


if __name__ == "__main__":
    main()
