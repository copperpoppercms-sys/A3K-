"""Static, string-literal-aware rewrite passes. Source is handled as latin-1 so every char is one byte."""
import re

_LONG_OPEN = re.compile(r"\[(=*)\[")
_KEYWORDS = {"return", "and", "or", "not", "then", "do", "else", "in", "until", "if", "while", "elseif", "local"}


def lua_split(src):
    """Split source into ('str'|'comment'|'other', text) tokens."""
    out, i, n, start = [], 0, len(src), 0

    def flush(end):
        if end > start:
            out.append(("other", src[start:end]))

    while i < n:
        c = src[i]
        if c in "\"'":
            flush(i)
            j = i + 1
            while j < n and src[j] != c:
                j += 2 if src[j] == "\\" else 1
            j = min(j + 1, n)
            out.append(("str", src[i:j]))
            i = start = j
        elif src.startswith("--", i):
            flush(i)
            m = _LONG_OPEN.match(src, i + 2)
            if m:
                close = "]" + m.group(1) + "]"
                j = src.find(close, m.end())
                j = n if j < 0 else j + len(close)
            else:
                j = src.find("\n", i)
                j = n if j < 0 else j
            out.append(("comment", src[i:j]))
            i = start = j
        elif c == "[" and _LONG_OPEN.match(src, i):
            m = _LONG_OPEN.match(src, i)
            flush(i)
            close = "]" + m.group(1) + "]"
            j = src.find(close, m.end())
            j = n if j < 0 else j + len(close)
            out.append(("str", src[i:j]))
            i = start = j
        else:
            i += 1
    flush(n)
    return out


def _join(toks):
    return "".join(t for _, t in toks)


def is_quoted(lit):
    return lit[:1] in "\"'"


_SIMPLE_ESC = {"a": "\a", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v", "\\": "\\", '"': '"', "'": "'", "\n": "\n"}


def parse_quoted(lit):
    if len(lit) < 2 or lit[0] not in "\"'" or lit[-1] != lit[0]:
        raise ValueError("unfinished quoted string")
    body, out, i, n = lit[1:-1] if len(lit) >= 2 else "", [], 0, 0
    n = len(body)
    while i < n:
        c = body[i]
        if c != "\\" or i + 1 >= n:
            out.append(c)
            i += 1
            continue
        e = body[i + 1]
        if e in _SIMPLE_ESC:
            out.append(_SIMPLE_ESC[e])
            i += 2
        elif e == "x" and re.match(r"[0-9a-fA-F]{2}", body[i + 2:i + 4]):
            out.append(chr(int(body[i + 2:i + 4], 16)))
            i += 4
        elif e.isdigit():
            m = re.match(r"\d{1,3}", body[i + 1:])
            value = int(m.group(0))
            if value > 255:
                raise ValueError("decimal escape exceeds 255")
            out.append(chr(value))
            i += 1 + len(m.group(0))
        elif e == "z":
            i += 2
            while i < n and body[i] in " \t\r\n":
                i += 1
        elif e == "u" and body[i + 2:i + 3] == "{":
            j = body.find("}", i)
            if j < 0 or not re.fullmatch(r"[0-9a-fA-F]{1,6}", body[i + 3:j]):
                raise ValueError("invalid unicode escape")
            cp = int(body[i + 3:j], 16)
            if cp > 0x10FFFF or 0xD800 <= cp <= 0xDFFF:
                raise ValueError("invalid unicode code point")
            out.append(chr(cp).encode("utf-8").decode("latin-1"))
            i = j + 1
        else:
            out.append(e)
            i += 2
    return "".join(out)


def emit_str(s):
    try:
        s.encode("latin-1").decode("utf-8")
        utf = True
    except UnicodeError:
        utf = False
    out = ['"']
    for ch in s:
        o = ord(ch)
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif o < 32 or o == 127 or (o >= 128 and not utf):
            out.append("\\%03d" % o)
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def p_escapes(src):
    toks = lua_split(src)
    out = []
    for k, t in toks:
        if k == "str" and is_quoted(t) and "\\" in t:
            try:
                t = emit_str(parse_quoted(t))
            except ValueError:
                pass  # Preserve malformed input; report its syntax error later.
        out.append((k, t))
    return _join(out)


def _map_other(src, fn):
    return _join([(k, fn(t) if k == "other" else t) for k, t in lua_split(src)])


_CHAR_RE = re.compile(r"\bstring\s*\.\s*char\s*\(\s*(\d+(?:\s*,\s*\d+)*)\s*\)")


def _char_sub(m):
    vals = [int(v) for v in re.split(r"\s*,\s*", m.group(1))]
    if any(v > 255 for v in vals):
        return m.group(0)
    return emit_str("".join(map(chr, vals)))


def p_char(src):
    if not _CHAR_RE.search(src):
        return src
    from . import luaast as a
    if len(src) > 400_000:
        return src
    try:
        def safe(node, parent=None, field=None, target=False):
            if isinstance(node, a.Local):
                return node.name != "string"
            if isinstance(node, a.Name) and node.name == "string":
                return not target and node.local is None and isinstance(parent, a.Index) and field == "obj"
            if isinstance(node, a.Node):
                return all(safe(getattr(node, key), node, key, target or isinstance(node, (a.Assign, a.FuncStmt)) and key in ("targets", "target")) for key in node.__slots__)
            if isinstance(node, (list, tuple)):
                return all(safe(child, parent, field, target) for child in node)
            return True

        if not safe(a.parse(src)):
            return src
    except (a.ParseError, ValueError, OverflowError, RecursionError):
        return src

    def fold(text, prefix):
        def sub(match):
            before = (prefix + text[:match.start()]).rstrip()
            if before.endswith(".") and not before.endswith("..") or before.endswith(":") and not before.endswith("::"):
                return match.group(0)
            return _char_sub(match)
        return _CHAR_RE.sub(sub, text)

    out, prefix = [], ""
    for kind, text in lua_split(src):
        out.append(fold(text, prefix) if kind == "other" else text)
        if kind == "other":
            prefix = (prefix + text)[-256:]
        elif kind == "str":
            prefix = "literal"
    return "".join(out)


def p_concat(src):
    toks = lua_split(src)
    out, i = [], 0
    while i < len(toks):
        k, t = toks[i]
        if (
            k == "str" and is_quoted(t) and i + 2 < len(toks)
            and toks[i + 1][0] == "other" and toks[i + 1][1].strip() == ".."
            and toks[i + 2][0] == "str" and is_quoted(toks[i + 2][1])
            and not (out and out[-1][0] == "other" and out[-1][1].rstrip().endswith("#"))
        ):
            toks[i + 2] = ("str", emit_str(parse_quoted(t) + parse_quoted(toks[i + 2][1])))
            i += 2
            continue
        out.append((k, t))
        i += 1
    return _join(out)


_HEX_RE = re.compile(r"\b0[xX]([0-9a-fA-F]+)\b(?![.pP])")
_GROUP_RE = re.compile(r"\(([\d\s+\-*/%^.eE]*)\)")
_NUM_TOK = re.compile(r"\s*((?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?|[-+*/%^])")


def _eval_arith(expr):
    """Evaluate a numeric Lua expression (+ - * / % ^, unary -) or return None."""
    toks, pos = [], 0
    expr = expr.strip()
    if len(expr) > 4096:
        return None
    while pos < len(expr):
        m = _NUM_TOK.match(expr, pos)
        if not m:
            return None
        toks.append(m.group(1))
        pos = m.end()
    i = [0]

    def peek():
        return toks[i[0]] if i[0] < len(toks) else None

    def power_operand():
        # unary minus binds looser than ^
        t = peek()
        if t == "-":
            i[0] += 1
            return -power_operand()
        return power()

    def power():
        base = atom_plain()
        if peek() == "^":
            i[0] += 1
            return base ** power_operand()
        return base

    def atom_plain():
        t = peek()
        if t is None or t in "+-*/%^":
            raise ValueError
        i[0] += 1
        return float(t)

    def unary():
        if peek() == "-":
            i[0] += 1
            return -unary()
        return power()

    def term():
        v = unary()
        while peek() in ("*", "/", "%"):
            op = toks[i[0]]
            i[0] += 1
            r = unary()
            v = v * r if op == "*" else v / r if op == "/" else v % r
        return v

    def sum_():
        v = term()
        while peek() in ("+", "-"):
            op = toks[i[0]]
            i[0] += 1
            r = term()
            v = v + r if op == "+" else v - r
        return v

    try:
        v = sum_()
        if i[0] != len(toks):
            return None
    except (ValueError, ZeroDivisionError, OverflowError, TypeError, RecursionError):
        return None
    if isinstance(v, complex) or v != v or v in (float("inf"), float("-inf")) or abs(v) > 2 ** 53:
        return None
    return v


def _fold_arith(t):
    def sub(m):
        j = m.start() - 1
        while j >= 0 and t[j] in " \t":
            j -= 1
        if j >= 0 and (t[j].isalnum() or t[j] in "_)]}"):
            w = re.search(r"[A-Za-z_]\w*$", t[: j + 1])
            if not (w and w.group(0) in _KEYWORDS):
                return m.group(0)
        if not re.search(r"\d", m.group(1)):
            return m.group(0)
        v = _eval_arith(m.group(1))
        if v is None:
            return m.group(0)
        txt = str(int(v)) if v == int(v) else repr(v)
        if v < 0:
            txt = "(%s)" % txt
        if m.start() > 0 and (t[m.start() - 1].isalnum() or t[m.start() - 1] == "_"):
            txt = " " + txt
        if m.end() < len(t) and (t[m.end()].isalnum() or t[m.end()] == "_"):
            txt += " "
        return txt

    prev = None
    while prev != t:
        prev, t = t, _GROUP_RE.sub(sub, t)
    return t


def p_numbers(src):
    return _map_other(src, lambda t: _fold_arith(_HEX_RE.sub(lambda m: str(int(m.group(1), 16)) if len(m.group(1)) <= 64 else m.group(0), t)))


def _scan_quoted_end(src, i):
    c, j = src[i], i + 1
    while j < len(src) and src[j] != c:
        j += 2 if src[j] == "\\" else 1
    return j + 1


def p_strtable(src):
    tables = {}
    for m in re.finditer(r"local\s+([A-Za-z_]\w*)\s*=\s*\{", src):
        name, pos, items = m.group(1), m.end(), []
        while pos < len(src):
            while pos < len(src) and src[pos] in " \t\r\n,;":
                pos += 1
            if pos >= len(src):
                items = None
                break
            if src[pos] == "}":
                break
            if src[pos] in "\"'":
                end = _scan_quoted_end(src, pos)
                items.append(parse_quoted(src[pos:end]))
                pos = end
            else:
                items = None
                break
        if not items:
            continue
        assigns = len(re.findall(r"\b%s\s*(?:\[[^\]]*\])?\s*=(?!=)" % re.escape(name), src))
        if assigns == 1:
            tables[name] = items
    if not tables:
        return src
    # A table may escape through an alias/call, be shadowed, or be written through
    # a computed index. Prove that every use is a literal index read before folding.
    from . import luaast as a
    if len(src) > 400_000:
        return src
    try:
        body = a.parse(src)
        bindings = {name: set() for name in tables}
        invalid = set()

        def walk(node, parent=None, field=None, target=False):
            if isinstance(node, a.Local):
                if node.name in bindings:
                    bindings[node.name].add(node)
            elif isinstance(node, a.Name):
                if node.name in tables:
                    bindings[node.name].add(node.local)
                    if target or not isinstance(parent, a.Index) or field != "obj" or not isinstance(parent.key, a.Num) or parent.key.value != int(parent.key.value):
                        invalid.add(node.name)
            elif isinstance(node, a.Node):
                for key in node.__slots__:
                    walk(getattr(node, key), node, key, target or isinstance(node, a.Assign) and key == "targets")
            elif isinstance(node, (list, tuple)):
                for child in node:
                    walk(child, parent, field, target)

        walk(body)
        tables = {name: items for name, items in tables.items() if name not in invalid and len(bindings[name]) == 1 and None not in bindings[name]}
    except (a.ParseError, ValueError, OverflowError, RecursionError):
        return src
    if not tables:
        return src
    pat = re.compile(r"\b(%s)\s*\[\s*(\d+)\s*\]" % "|".join(map(re.escape, tables)))

    def sub(m):
        items, idx = tables[m.group(1)], int(m.group(2))
        return emit_str(items[idx - 1]) if 1 <= idx <= len(items) else m.group(0)

    return _map_other(src, lambda t: pat.sub(sub, t))


_WRAP_RE = re.compile(r"^\s*(?:return\s*)?\(?\s*(?:loadstring|load)\s*\(\s*(\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*')\s*\)\s*\)?\s*\(\s*\)\s*;?\s*$", re.S)


def unwrap_static(src):
    from .literals import unwrap

    m = _WRAP_RE.match(src)
    if m:
        try:
            value = parse_quoted(m.group(1))
            return value if not value.startswith("\x1b") else None
        except ValueError:
            return None
    return unwrap(src)


PASSES = [("escapes", p_escapes), ("numbers", p_numbers), ("string.char", p_char), ("concat", p_concat), ("string-table", p_strtable)]


def run_static(src, max_rounds=10):
    notes = []
    for _ in range(max_rounds):
        before = src
        for name, fn in PASSES:
            try:
                new = fn(src)
            except (ValueError, OverflowError, RecursionError):
                continue
            if new != src:
                notes.append(name)
                src = new
        if src == before:
            break
    return src, sorted(set(notes))
