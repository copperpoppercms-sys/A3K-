"""Inline string-decoder calls: find a local function that is called with literal arguments many times
(XOR/rotation string decryptors), evaluate it in the sandbox, replace each call with the decoded literal."""
import re

from . import passes, sandbox

_DEF_RE = re.compile(
    r"local\s+function\s+([A-Za-z_]\w*)\s*\(([^)]*)\)|local\s+([A-Za-z_]\w*)\s*=\s*function\s*\(([^)]*)\)"
)
_WRAPPER_RE = re.compile(r"^(\s*(?:--\[\[.*?\]\]\s*)?)return\s*\(\s*function\s*\(\s*\.\.\.\s*\)", re.S)
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


def _fold_one(src, skel, strs, name, def_end):
    call_re = re.compile(r"(?<![\w.:])%s\s*\(\s*(%s(?:\s*,\s*%s)*)\s*\)" % (re.escape(name), _ARG, _ARG))
    matches = [m for m in call_re.finditer(skel) if m.start() >= 0]
    if len(matches) < MIN_CALLS:
        return None
    tuples = []
    for m in matches:
        tuples.append(tuple(_lit(t, strs) for t in re.split(r"\s*,\s*", m.group(1).strip())))
    uniq = list(dict.fromkeys(tuples))[:MAX_CALLS]
    prefix = src[:def_end]
    w = _WRAPPER_RE.match(prefix)
    if w:
        prefix = w.group(1) + prefix[w.end():]
    res = sandbox.decrypt(prefix, name, [list(map(list, t)) for t in uniq])
    if not res:
        return None
    table = dict(zip(uniq, res))
    hits = sum(1 for v in table.values() if v is not None)
    if hits * 2 < len(uniq):  # most calls failed: this is not a pure decoder
        return None

    def sub(m):
        t = tuple(_lit(x, strs) for x in re.split(r"\s*,\s*", m.group(1).strip()))
        v = table.get(t)
        return passes.emit_str(v) if v is not None else m.group(0)

    return call_re.sub(sub, skel), hits


def fold(src):
    """Return (new_source, calls_inlined)."""
    total = 0
    for m in list(_DEF_RE.finditer(src)):
        name = m.group(1) or m.group(3)
        params = (m.group(2) if m.group(1) else m.group(4)).strip()
        if not params or params.count(",") > 2 or "..." in params:
            continue
        fstart = src.index("function", m.start())
        end = block_end(src, fstart)
        if end is None:
            continue
        skel, strs = _skeleton(src)
        got = _fold_one(src, skel, strs, name, end)
        if not got:
            continue
        new_skel, hits = got
        src = re.sub(r"\x01(\d+)\x01", lambda x: strs[int(x.group(1))], new_skel)
        total += hits
    return src, total
