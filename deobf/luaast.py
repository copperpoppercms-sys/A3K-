"""Lua 5.1 (+ `continue`, compound assignment, `//`) parser with scope resolution, and a pretty printer.

Source text is latin-1 (one char per byte). Every identifier that resolves to a `local` points at a shared `Local`
object, so passes can reason about reads, writes and captures instead of guessing from names."""
import re

from . import passes

KEYWORDS = frozenset(
    "and break do else elseif end false for function goto if in local nil not or repeat return then true until while continue".split()
)


class ParseError(Exception):
    pass


class Local:
    __slots__ = ("name", "func", "reads", "writes", "captured", "assigned_in_closure", "uid")
    _n = 0

    def __init__(self, name, func):
        self.name, self.func = name, func
        self.reads = self.writes = 0
        self.captured = self.assigned_in_closure = False
        Local._n += 1
        self.uid = Local._n


class Node:
    __slots__ = ()

    def __repr__(self):
        return "%s(%s)" % (type(self).__name__, ", ".join("%s=%r" % (k, getattr(self, k)) for k in self.__slots__))


def _node(name, *fields):
    def init(self, *a):
        for f, v in zip(fields, a):
            setattr(self, f, v)

    return type(name, (Node,), {"__slots__": fields, "__init__": init})


# expressions
Nil = _node("Nil")
Bool = _node("Bool", "value")
Num = _node("Num", "value")
Str = _node("Str", "value")
Vararg = _node("Vararg")
Func = _node("Func", "params", "vararg", "body", "is_method", "fid")
Name = _node("Name", "local", "name")  # local is a Local or None (global)
Index = _node("Index", "obj", "key")
Call = _node("Call", "func", "args")
Method = _node("Method", "obj", "name", "args")
Table = _node("Table", "items")  # items: ("pos", None, v) | ("named", Str, v) | ("expr", k, v)
Bin = _node("Bin", "op", "l", "r")
Un = _node("Un", "op", "e")
Paren = _node("Paren", "e")
# statements
LocalStmt = _node("LocalStmt", "vars", "exprs")
Assign = _node("Assign", "targets", "exprs")
CallStmt = _node("CallStmt", "call")
Do = _node("Do", "body")
While = _node("While", "cond", "body")
Repeat = _node("Repeat", "body", "cond")
If = _node("If", "clauses", "orelse")  # clauses: [(cond, body)]
NumFor = _node("NumFor", "var", "start", "stop", "step", "body")
GenFor = _node("GenFor", "vars", "exprs", "body")
FuncStmt = _node("FuncStmt", "target", "func")
LocalFunc = _node("LocalFunc", "var", "func")
Return = _node("Return", "exprs")
Break = _node("Break")
Continue = _node("Continue")
Goto = _node("Goto", "label")
Label = _node("Label", "label")

_TOKEN = re.compile(
    r"""(?P<ws>[ \t\r\n]+)
    |(?P<num>""" + passes.NUMBER_PATTERN + r""")
    |(?P<name>[A-Za-z_][A-Za-z0-9_]*)
    |(?P<op>\.\.\.|\.\.=|\.\.|==|~=|!=|<=|>=|::|//=|//|\+=|-=|\*=|/=|%=|\^=|[-+*/%^#<>=(){}\[\];:,.])""",
    re.X,
)
_LONG = re.compile(r"\[(=*)\[")


def lex(src):
    toks, i, n = [], 0, len(src)
    while i < n:
        c = src[i]
        if src.startswith("--", i):
            m = _LONG.match(src, i + 2)
            if m:
                close = "]" + m.group(1) + "]"
                j = src.find(close, m.end())
                i = n if j < 0 else j + len(close)
            else:
                j = src.find("\n", i)
                i = n if j < 0 else j
            continue
        if c in "\"'":
            j = i + 1
            while j < n and src[j] != c:
                if src[j] == "\n":
                    raise ParseError("unfinished string")
                j += 2 if src[j] == "\\" else 1
            if j >= n:
                raise ParseError("unfinished string")
            try:
                value = passes.parse_quoted(src[i:j + 1])
            except ValueError as e:
                raise ParseError(str(e)) from e
            toks.append(("str", value, i))
            i = j + 1
            continue
        if c == "[":
            m = _LONG.match(src, i)
            if m:
                close = "]" + m.group(1) + "]"
                j = src.find(close, m.end())
                if j < 0:
                    raise ParseError("unfinished long string")
                body = src[m.end():j]
                if body.startswith("\r\n"):
                    body = body[2:]
                elif body.startswith("\n"):
                    body = body[1:]
                toks.append(("str", body, i))
                i = j + len(close)
                continue
        m = _TOKEN.match(src, i)
        if not m:
            raise ParseError("bad character %r at %d" % (c, i))
        kind = m.lastgroup
        text = m.group(kind)
        if kind == "num":
            toks.append(("num", _number(text), i))
        elif kind == "name":
            toks.append(("kw" if text in KEYWORDS else "name", text, i))
        elif kind == "op":
            toks.append(("op", text, i))
        i = m.end()
    toks.append(("eof", "<eof>", n))
    return toks


def _number(text):
    text = text.replace("_", "")
    try:
        if text[:2] in ("0b", "0B"):
            v = int(text[2:], 2)
        elif text[:2] in ("0x", "0X"):
            if "." in text or "p" in text or "P" in text:
                return float.fromhex(text)
            v = int(text, 16)
        elif re.fullmatch(r"\d+", text):
            v = int(text)
        else:
            v = float(text)
    except ValueError:
        raise ParseError("malformed number %r" % text)
    if isinstance(v, int) and v > 2 ** 53:
        v = float(v)
    if isinstance(v, float) and v == v and abs(v) < 2 ** 53 and v == int(v):
        v = int(v)
    return v


_BIN = {
    "or": (1, 1), "and": (2, 2),
    "<": (3, 3), ">": (3, 3), "<=": (3, 3), ">=": (3, 3), "~=": (3, 3), "==": (3, 3),
    "..": (5, 4), "+": (6, 6), "-": (6, 6), "*": (7, 7), "/": (7, 7), "%": (7, 7), "//": (7, 7), "^": (10, 9),
}
_UNARY = 8
_COMPOUND = {"+=": "+", "-=": "-", "*=": "*", "/=": "/", "//=": "//", "%=": "%", "^=": "^", "..=": ".."}


class Parser:
    def __init__(self, src):
        self.toks = lex(src)
        self.p = 0
        self.scopes = [{}]
        self.func = 0
        self.nfunc = 0

    # token helpers
    def peek(self, k=0):
        return self.toks[min(self.p + k, len(self.toks) - 1)]

    def next(self):
        t = self.toks[self.p]
        self.p += 1
        return t

    def check(self, kind, value=None):
        t = self.toks[self.p]
        return t[0] == kind and (value is None or t[1] == value)

    def accept(self, kind, value=None):
        if self.check(kind, value):
            return self.next()
        return None

    def expect(self, kind, value=None):
        t = self.accept(kind, value)
        if not t:
            raise ParseError("expected %s %r near %r at %d" % (kind, value, self.toks[self.p][1], self.toks[self.p][2]))
        return t

    def kw(self, v):
        return self.check("kw", v)

    # scopes
    def declare(self, name):
        loc = Local(name, self.func)
        self.scopes[-1][name] = loc
        return loc

    def resolve(self, name):
        for sc in reversed(self.scopes):
            if name in sc:
                return sc[name]
        return None

    # grammar
    def parse_chunk(self):
        body = self.block()
        if not self.check("eof"):
            raise ParseError("unexpected %r at %d" % (self.peek()[1], self.peek()[2]))
        return body

    def block_follow(self):
        t = self.peek()
        return t[0] == "eof" or (t[0] == "kw" and t[1] in ("end", "else", "elseif", "until"))

    def block(self, scope=True):
        if scope:
            self.scopes.append({})
        out = []
        while not self.block_follow():
            if self.kw("return"):
                out.append(self.retstat())
                break
            st = self.statement()
            if st is not None:
                out.append(st)
        if scope:
            self.scopes.pop()
        return out

    def retstat(self):
        self.next()
        exprs = []
        if not self.block_follow() and not self.check("op", ";"):
            exprs = self.exprlist()
        self.accept("op", ";")
        return Return(exprs)

    def statement(self):
        t = self.peek()
        if t[0] == "op" and t[1] == ";":
            self.next()
            return None
        if t[0] == "op" and t[1] == "::":
            self.next()
            name = self.expect("name")[1]
            self.expect("op", "::")
            return Label(name)
        if t[0] == "kw":
            v = t[1]
            if v == "if":
                return self.ifstat()
            if v == "while":
                self.next()
                cond = self.expr()
                self.expect("kw", "do")
                body = self.block()
                self.expect("kw", "end")
                return While(cond, body)
            if v == "do":
                self.next()
                body = self.block()
                self.expect("kw", "end")
                return Do(body)
            if v == "for":
                return self.forstat()
            if v == "repeat":
                self.next()
                self.scopes.append({})
                body = self.block(scope=False)
                self.expect("kw", "until")
                cond = self.expr()
                self.scopes.pop()
                return Repeat(body, cond)
            if v == "function":
                return self.funcstat()
            if v == "local":
                self.next()
                if self.accept("kw", "function"):
                    name = self.expect("name")[1]
                    var = self.declare(name)
                    return LocalFunc(var, self.funcbody(False))
                names = [self.expect("name")[1]]
                while self.accept("op", ","):
                    names.append(self.expect("name")[1])
                exprs = self.exprlist() if self.accept("op", "=") else []
                return LocalStmt([self.declare(n) for n in names], exprs)
            if v == "break":
                self.next()
                return Break()
            if v == "continue":
                self.next()
                return Continue()
            if v == "goto":
                self.next()
                return Goto(self.expect("name")[1])
            if v == "return":
                raise ParseError("return in the middle of a block")
        return self.exprstat()

    def ifstat(self):
        clauses = []
        self.next()
        cond = self.expr()
        self.expect("kw", "then")
        clauses.append((cond, self.block()))
        orelse = None
        while True:
            if self.accept("kw", "elseif"):
                cond = self.expr()
                self.expect("kw", "then")
                clauses.append((cond, self.block()))
            elif self.accept("kw", "else"):
                orelse = self.block()
                self.expect("kw", "end")
                break
            else:
                self.expect("kw", "end")
                break
        return If(clauses, orelse)

    def forstat(self):
        self.next()
        n1 = self.expect("name")[1]
        if self.check("op", "="):
            self.next()
            start = self.expr()
            self.expect("op", ",")
            stop = self.expr()
            step = self.expr() if self.accept("op", ",") else None
            self.expect("kw", "do")
            self.scopes.append({})
            var = self.declare(n1)
            body = self.block()
            self.scopes.pop()
            self.expect("kw", "end")
            return NumFor(var, start, stop, step, body)
        names = [n1]
        while self.accept("op", ","):
            names.append(self.expect("name")[1])
        self.expect("kw", "in")
        exprs = self.exprlist()
        self.expect("kw", "do")
        self.scopes.append({})
        vars_ = [self.declare(n) for n in names]
        body = self.block()
        self.scopes.pop()
        self.expect("kw", "end")
        return GenFor(vars_, exprs, body)

    def funcstat(self):
        self.next()
        n = self.expect("name")[1]
        target = self.name_expr(n)
        is_method = False
        while True:
            if self.accept("op", "."):
                target = Index(target, Str(self.expect("name")[1]))
            elif self.accept("op", ":"):
                target = Index(target, Str(self.expect("name")[1]))
                is_method = True
                break
            else:
                break
        return FuncStmt(target, self.funcbody(is_method))

    def funcbody(self, is_method):
        self.expect("op", "(")
        self.nfunc += 1
        saved = self.func
        self.func = self.nfunc
        fid = self.nfunc
        self.scopes.append({})
        params, vararg = [], False
        if is_method:
            params.append(self.declare("self"))
        if not self.check("op", ")"):
            while True:
                if self.accept("op", "..."):
                    vararg = True
                    break
                params.append(self.declare(self.expect("name")[1]))
                if not self.accept("op", ","):
                    break
        self.expect("op", ")")
        body = self.block()
        self.expect("kw", "end")
        self.scopes.pop()
        self.func = saved
        return Func(params, vararg, body, is_method, fid)

    def exprstat(self):
        e = self.suffixedexp()
        if self.check("op", "=") or self.check("op", ","):
            targets = [e]
            while self.accept("op", ","):
                targets.append(self.suffixedexp())
            self.expect("op", "=")
            for t in targets:
                if not isinstance(t, (Name, Index)):
                    raise ParseError("cannot assign to expression")
            return Assign(targets, self.exprlist())
        t = self.peek()
        if t[0] == "op" and t[1] in _COMPOUND:
            self.next()
            if not isinstance(e, (Name, Index)):
                raise ParseError("cannot assign to expression")
            return Assign([e], [Bin(_COMPOUND[t[1]], e, self.expr())])
        if not isinstance(e, (Call, Method)):
            raise ParseError("syntax error near %r at %d" % (self.peek()[1], self.peek()[2]))
        return CallStmt(e)

    def exprlist(self):
        out = [self.expr()]
        while self.accept("op", ","):
            out.append(self.expr())
        return out

    def name_expr(self, name):
        loc = self.resolve(name)
        if loc is not None and loc.func != self.func:
            loc.captured = True
        return Name(loc, name)

    def primaryexp(self):
        t = self.next()
        if t[0] == "name":
            return self.name_expr(t[1])
        if t == ("op", "(", t[2]):
            e = self.expr()
            self.expect("op", ")")
            return Paren(e)
        raise ParseError("unexpected symbol %r at %d" % (t[1], t[2]))

    def suffixedexp(self):
        e = self.primaryexp()
        while True:
            t = self.peek()
            if t[0] == "op" and t[1] == ".":
                self.next()
                e = Index(e, Str(self.expect("name")[1]))
            elif t[0] == "op" and t[1] == "[":
                self.next()
                k = self.expr()
                self.expect("op", "]")
                e = Index(e, k)
            elif t[0] == "op" and t[1] == ":":
                self.next()
                name = self.expect("name")[1]
                e = Method(e, name, self.callargs())
            elif (t[0] == "op" and t[1] in ("(", "{")) or t[0] == "str":
                e = Call(e, self.callargs())
            else:
                return e

    def callargs(self):
        t = self.peek()
        if t[0] == "str":
            self.next()
            return [Str(t[1])]
        if t[0] == "op" and t[1] == "{":
            return [self.table()]
        self.expect("op", "(")
        if self.accept("op", ")"):
            return []
        args = self.exprlist()
        self.expect("op", ")")
        return args

    def table(self):
        self.expect("op", "{")
        items = []
        while not self.check("op", "}"):
            t = self.peek()
            if t[0] == "op" and t[1] == "[":
                self.next()
                k = self.expr()
                self.expect("op", "]")
                self.expect("op", "=")
                items.append(("expr", k, self.expr()))
            elif t[0] == "name" and self.peek(1) == ("op", "=", self.peek(1)[2]):
                self.next()
                self.next()
                items.append(("named", Str(t[1]), self.expr()))
            else:
                items.append(("pos", None, self.expr()))
            if not (self.accept("op", ",") or self.accept("op", ";")):
                break
        self.expect("op", "}")
        return Table(items)

    def simpleexp(self):
        t = self.peek()
        k, v = t[0], t[1]
        if k == "num":
            self.next()
            return Num(v)
        if k == "str":
            self.next()
            return Str(v)
        if k == "kw" and v == "nil":
            self.next()
            return Nil()
        if k == "kw" and v == "true":
            self.next()
            return Bool(True)
        if k == "kw" and v == "false":
            self.next()
            return Bool(False)
        if k == "op" and v == "...":
            self.next()
            return Vararg()
        if k == "op" and v == "{":
            return self.table()
        if k == "kw" and v == "function":
            self.next()
            return self.funcbody(False)
        return self.suffixedexp()

    def expr(self, limit=0):
        t = self.peek()
        if (t[0] == "kw" and t[1] == "not") or (t[0] == "op" and t[1] in ("-", "#")):
            self.next()
            e = Un(t[1], self.expr(_UNARY))
        else:
            e = self.simpleexp()
        while True:
            t = self.peek()
            op = t[1] if t[0] in ("op", "kw") else None
            if op == "!=":
                op = "~="
            if op not in _BIN or _BIN[op][0] <= limit:
                return e
            self.next()
            r = self.expr(_BIN[op][1])
            e = Bin(op, e, r)


def parse(src):
    """Parse a chunk; returns the statement list. Raises ParseError."""
    try:
        return Parser(src).parse_chunk()
    except RecursionError:
        raise ParseError("nesting too deep")


# ---------------------------------------------------------------- printer
_PREC = {"or": 1, "and": 2, "<": 3, ">": 3, "<=": 3, ">=": 3, "~=": 3, "==": 3, "..": 4, "+": 5, "-": 5, "*": 6, "/": 6, "%": 6, "//": 6, "^": 8}
_RIGHT = {"..", "^"}
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def fmt_num(v):
    if isinstance(v, float):
        if v == 0 and str(v).startswith("-"):
            return "-0"
        if v == int(v) and abs(v) < 2 ** 53:
            return str(int(v))
        return repr(v)
    return str(v)


class Printer:
    def __init__(self, indent="    "):
        self.indent = indent
        self.out = []

    def expr(self, e, parent=0, side=None):
        t = type(e)
        if t is Nil:
            return "nil"
        if t is Bool:
            return "true" if e.value else "false"
        if t is Num:
            s = fmt_num(e.value)
            return "(%s)" % s if s.startswith("-") and parent >= 7 else s
        if t is Str:
            return passes.emit_str(e.value)
        if t is Vararg:
            return "..."
        if t is Name:
            return e.local.name if e.local is not None else e.name
        if t is Paren:
            return "(%s)" % self.expr(e.e)
        if t is Index:
            obj = self.prefix(e.obj)
            if type(e.key) is Str and _IDENT.match(e.key.value) and e.key.value not in KEYWORDS:
                return "%s.%s" % (obj, e.key.value)
            return "%s[%s]" % (obj, self.expr(e.key))
        if t is Call:
            return "%s(%s)" % (self.prefix(e.func), ", ".join(self.expr(a) for a in e.args))
        if t is Method:
            return "%s:%s(%s)" % (self.prefix(e.obj), e.name, ", ".join(self.expr(a) for a in e.args))
        if t is Func:
            return self.func(e)
        if t is Table:
            return self.table(e)
        if t is Un:
            inner = self.expr(e.e, 7)
            s = ("not " if e.op == "not" else e.op) + inner
            if e.op == "-" and inner.startswith("-"):
                s = "-(" + inner + ")"
            return "(%s)" % s if parent > 7 else s
        if t is Bin:
            p = _PREC[e.op]
            lp = p + (1 if e.op in _RIGHT else 0)
            rp = p + (0 if e.op in _RIGHT else 1)
            s = "%s %s %s" % (self.expr(e.l, lp), e.op, self.expr(e.r, rp))
            return "(%s)" % s if p < parent or (p == parent and False) else s
        raise TypeError(t)

    def prefix(self, e):
        s = self.expr(e, 100)
        if type(e) in (Name, Index, Call, Method, Paren):
            return s
        return "(%s)" % s

    def table(self, e):
        if not e.items:
            return "{}"
        parts = []
        for kind, k, v in e.items:
            if kind == "pos":
                parts.append(self.expr(v))
            elif kind == "named" and _IDENT.match(k.value) and k.value not in KEYWORDS:
                parts.append("%s = %s" % (k.value, self.expr(v)))
            else:
                parts.append("[%s] = %s" % (self.expr(k.value if False else k), self.expr(v)))
        one = "{" + ", ".join(parts) + "}"
        return one

    def func(self, f, name=""):
        params = [p.name for p in f.params]
        if f.is_method and params and params[0] == "self":
            params = params[1:]
        if f.vararg:
            params.append("...")
        head = "function%s(%s)" % (name, ", ".join(params))
        body = self.block_lines(f.body, 1)
        if not body:
            return head + " end"
        return head + "\n" + "\n".join(body) + "\n" + self.pad(0) + "end"

    def pad(self, depth):
        return self.indent * depth

    _depth = 0

    def block_lines(self, body, depth):
        lines = []
        for st in body:
            new = self.stmt(st, depth)
            # a statement starting with "(" would parse as a call on the previous one; Lua 5.1 only allows ";" after a statement
            if lines and new and new[0].lstrip().startswith("("):
                lines[-1] += ";"
            lines += new
        return lines

    def stmt(self, s, depth):
        pad = self.pad(depth)
        t = type(s)
        # nested bodies print relative to the current depth
        saved = self._depth
        self._depth = depth
        try:
            return self._stmt(s, depth, pad, t)
        finally:
            self._depth = saved

    def _block(self, body, depth):
        return self.block_lines(body, depth + 1)

    def _lead(self, text, pad):
        return [pad + text]

    def _stmt(self, s, depth, pad, t):
        if t is LocalStmt:
            names = ", ".join(v.name for v in s.vars)
            if s.exprs:
                return self._multiline(pad + "local %s = " % names, s.exprs, depth)
            return [pad + "local " + names]
        if t is Assign:
            return self._multiline(pad + ", ".join(self.expr(x) for x in s.targets) + " = ", s.exprs, depth, lead=s.targets)
        if t is CallStmt:
            return self._lead(self._with_depth(self.expr(s.call), depth), pad)
        if t is Do:
            return [pad + "do"] + self._block(s.body, depth) + [pad + "end"]
        if t is While:
            return [pad + "while %s do" % self.expr(s.cond)] + self._block(s.body, depth) + [pad + "end"]
        if t is Repeat:
            return [pad + "repeat"] + self._block(s.body, depth) + [pad + "until " + self.expr(s.cond)]
        if t is If:
            out = []
            for i, (c, b) in enumerate(s.clauses):
                out.append(pad + ("if " if i == 0 else "elseif ") + self.expr(c) + " then")
                out += self._block(b, depth)
            if s.orelse is not None:
                out.append(pad + "else")
                out += self._block(s.orelse, depth)
            out.append(pad + "end")
            return out
        if t is NumFor:
            head = "for %s = %s, %s" % (s.var.name, self.expr(s.start), self.expr(s.stop))
            if s.step is not None:
                head += ", " + self.expr(s.step)
            return [pad + head + " do"] + self._block(s.body, depth) + [pad + "end"]
        if t is GenFor:
            return [pad + "for %s in %s do" % (", ".join(v.name for v in s.vars), ", ".join(self.expr(x) for x in s.exprs))] + self._block(s.body, depth) + [pad + "end"]
        if t is FuncStmt:
            tgt = s.target
            name, parts = "", []
            e = tgt
            while type(e) is Index and type(e.key) is Str:
                parts.append(e.key.value)
                e = e.obj
            if type(e) is Name and len(parts) == len(self._index_chain(tgt)):
                parts.reverse()
                dotted = (e.local.name if e.local is not None else e.name)
                for i, p in enumerate(parts):
                    dotted += (":" if (s.func.is_method and i == len(parts) - 1) else ".") + p
                return self._func_lines("function " + dotted, s.func, depth)
            return self._multiline(pad + self.expr(tgt) + " = ", [s.func], depth)
        if t is LocalFunc:
            return self._func_lines("local function " + s.var.name, s.func, depth)
        if t is Return:
            if not s.exprs:
                return [pad + "return"]
            return self._multiline(pad + "return ", s.exprs, depth)
        if t is Break:
            return [pad + "break"]
        if t is Continue:
            return [pad + "continue"]
        if t is Goto:
            return [pad + "goto " + s.label]
        if t is Label:
            return [pad + "::" + s.label + "::"]
        raise TypeError(t)

    def _index_chain(self, e):
        out = []
        while type(e) is Index and type(e.key) is Str:
            out.append(e)
            e = e.obj
        return out

    def _func_lines(self, head, f, depth):
        pad = self.pad(depth)
        params = [p.name for p in f.params]
        if f.is_method and params and params[0] == "self":
            params = params[1:]
        if f.vararg:
            params.append("...")
        lines = [pad + "%s(%s)" % (head, ", ".join(params))]
        lines += self._block(f.body, depth)
        lines.append(pad + "end")
        return lines

    def _with_depth(self, text, depth):
        """Function literals inside an expression are printed at depth 0; re-indent their continuation lines."""
        if "\n" not in text:
            return text
        pad = self.pad(depth)
        first, *rest = text.split("\n")
        return "\n".join([first] + [pad + ln for ln in rest])

    def _multiline(self, head, exprs, depth, lead=None):
        text = ", ".join(self.expr(x) for x in exprs)
        full = head + self._with_depth(text, depth)
        return [full]


def dump(body):
    """Pretty-print a statement list."""
    return "\n".join(Printer().block_lines(body, 0))
