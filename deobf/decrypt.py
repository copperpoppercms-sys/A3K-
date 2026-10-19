"""Inline string-decoder calls: find a local function that is called with literal arguments many times
(XOR/rotation string decryptors), evaluate it in the sandbox, replace each call with the decoded literal."""
import re

from . import passes, sandbox

_DEF_RE = re.compile(
    r"local\s+function\s+([A-Za-z_]\w*)\s*\(([^)]*)\)|local\s+([A-Za-z_]\w*)\s*=\s*function\s*\(([^)]*)\)"
)
_BLOCK_RE = re.compile(r"\b(function|if|do|repeat|end|until)\b")
_PH = "\x01%d\x01"
_ARG = r"(?:\x01\d+\x01|-?\d+(?:\.\d+)?)"
MIN_CALLS = 3
MAX_CALLS = 5000


def block_end(src, start):
    """Offset just past the `end` closing the `function` keyword at src[start:]."""
    depth, pos = 0, 0
    for kind, text in passes.lua_split(src):
        if kind == "other" and pos + len(text) > start:
            for m in _BLOCK_RE.finditer(text):
                if pos + m.start() < start:
                    continue
                w = m.group(1)
                depth += 1 if w in ("function", "if", "do", "repeat") else -1
                if depth == 0:
                    return pos + m.end()
        pos += len(text)
    return None


def _skeleton(src):
    strs, parts = [], []
    for kind, text in passes.lua_split(src):
        if kind == "str":
            strs.append(text)
            parts.append(_PH % (len(strs) - 1))
        else:
            parts.append(text)
    return "".join(parts), strs


def _lit(tok, strs):
    if tok.startswith("\x01"):
        raw = strs[int(tok.strip("\x01"))]
        return ("s", passes.parse_quoted(raw) if passes.is_quoted(raw) else raw[raw.index("[", 1) + 1:-(raw.index("[", 1) + 1)].lstrip("\n"))
    v = float(tok)
    return ("n", int(v) if v == int(v) else v)


def _norm(args):
    return tuple((k, float(v) if k == "n" else v) for k, v in args)


def fold(src):
    """Return (new_source, calls_inlined).

    Decoders are instrumented in place and the whole script is executed, so every value is the one the
    script itself computed (scripts that mutate the constant pool after defining the getter stay correct).
    Call sites the run never reached are evaluated afterwards against the live decoder closure."""
    cands = []
    for m in _DEF_RE.finditer(src):
        name = m.group(1) or m.group(3)
        params = (m.group(2) if m.group(1) else m.group(4)).strip()
        if not params or params.count(",") > 2 or "..." in params:
            continue
        end = block_end(src, src.index("function", m.start()))
        if end is not None:
            cands.append((name, m.start(), end))
    if not cands:
        return src, 0
    skel, strs = _skeleton(src)
    sites = {}
    for name, _, _ in cands:
        call_re = re.compile(r"(?<![\w:])(?<![^.]\.)%s\s*\(\s*(%s(?:\s*,\s*%s)*)\s*\)" % (re.escape(name), _ARG, _ARG))
        found = [(m, _norm(tuple(_lit(t, strs) for t in re.split(r"\s*,\s*", m.group(1).strip())))) for m in call_re.finditer(skel)]
        if len(found) >= MIN_CALLS:
            sites[name] = (call_re, found)
    if not sites:
        return src, 0
    inst = src
    for name, _, end in sorted(cands, key=lambda c: -c[2]):
        if name in sites:
            inst = inst[:end] + " %s=__REC(%s,%s) " % (name, name, '"%s"' % name) + inst[end:]
    queries = []
    for name, (_, found) in sites.items():
        queries += [(name, t) for t in dict.fromkeys(t for _, t in found)][:MAX_CALLS]
    ran = sandbox.run(inst, 15, queries)
    recorded = {}
    for name, args, result in ran.dec:
        recorded.setdefault((name, _norm(args)), set()).add(result)
    total = 0
    for name, _, _ in cands:
        if name not in sites:
            continue
        call_re, found = sites[name]
        table = {k[1]: next(iter(v)) for k, v in recorded.items() if k[0] == name and len(v) == 1}
        if not table:
            continue

        def sub(m, table=table):
            t = _norm(tuple(_lit(x, strs) for x in re.split(r"\s*,\s*", m.group(1).strip())))
            v = table.get(t)
            return passes.emit_str(v) if v is not None else m.group(0)

        new = call_re.sub(sub, skel)
        hits = sum(1 for _, t in found if t in table)
        if hits:
            skel, total = new, total + hits
    return re.sub(r"\x01(\d+)\x01", lambda x: strs[int(x.group(1))], skel), total
