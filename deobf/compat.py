"""Targeted floor-division lowering for sandbox copies. Never executes input."""
from dataclasses import dataclass

from . import luaast as a, passes


@dataclass
class FloorSpan:
    start: int
    op: int
    right: int
    end: int


class SpanParser(a.Parser):
    def __init__(self, source):
        super().__init__(source)
        self.floor_spans = []

    def expr(self, limit=0):
        # Parser.expr's precedence grammar, with source ranges retained so only
        # // expressions change. Comments and unrelated syntax remain intact.
        start = self.peek()[2]
        token = self.peek()
        if token[0] == "kw" and token[1] == "not" or token[0] == "op" and token[1] in ("-", "#"):
            self.next()
            node = a.Un(token[1], self.expr(a._UNARY))
        else:
            node = self.simpleexp()
        while True:
            token = self.peek()
            op = token[1] if token[0] in ("op", "kw") else None
            if op == "!=":
                op = "~="
            if op not in a._BIN or a._BIN[op][0] <= limit:
                return node
            self.next()
            right_start = self.peek()[2]
            right = self.expr(a._BIN[op][1])
            node = a.Bin(op, node, right)
            if op == "//":
                self.floor_spans.append(FloorSpan(start, token[2], right_start, self.peek()[2]))
                if len(self.floor_spans) > 256:
                    raise a.ParseError("floor-division operator limit (256)")


# Capture standard functions before the source shadows/replaces them. The fresh
# local helper evaluates each operand once. Lua 5.1 uses double precision.
_HELPER = (
    "local {name}=(function(floor,num,mt,raw,typ,err) return function(x,y) "
    "local a,b=num(x),num(y); if a and b then "
    "if b==0 then err('floor division by zero',2) end; return floor(a/b) end; "
    "local l,r=mt(x),mt(y); local f=typ(l)=='table' and raw(l,'__idiv') or nil; "
    "if f==nil and typ(r)=='table' then f=raw(r,'__idiv') end; "
    "if f~=nil then return f(x,y) end; err('unsupported floor division operands',2) "
    "end end)(math.floor,tonumber,getmetatable,rawget,type,error); "
)


def _splice(source, start, end, edits):
    out, cursor = [], start
    for left, right, replacement in sorted(edits, key=lambda e: (e[0], -e[1])):
        if start <= left and right <= end and left >= cursor:
            out.extend((source[cursor:left], replacement))
            cursor = right
    out.append(source[cursor:end])
    return "".join(out)


def _operand(text):
    tokens = passes.lua_split(text)
    if tokens and tokens[-1][0] == "comment" and not passes._LONG_OPEN.match(tokens[-1][1], 2):
        text += "\n"  # Keep the closing parenthesis out of a line comment.
    return "(" + text + ")"


def prepare(source):
    """Return a lowered sandbox copy and notes; retain original on parse failure.

    Not full Luau/Lua 5.3 support: integer precision beyond 2**53 is not emulated,
    and zero divisors are rejected. Unrelated syntax is not rewritten.
    """
    if source.startswith("\x1b") or "//" not in source:
        return source, []
    if not any("//" in text for kind, text in passes.lua_split(source) if kind == "other"):
        return source, []
    if len(source) > 2 * 1024 * 1024:
        return source, ["floor division compatibility skipped: input exceeds 2 MB"]
    try:
        parser = SpanParser(source)
        parser.parse_chunk()
    except (a.ParseError, ValueError, OverflowError, RecursionError) as e:
        return source, ["floor division compatibility unavailable: " + str(e)[:150]]
    if not parser.floor_spans:
        return source, []
    reserved = {token[1] for token in parser.toks if token[0] == "name"}
    name = "__a3k_floor_div"
    suffix = 0
    while name in reserved:
        suffix += 1
        name = "__a3k_floor_div_%d" % suffix
    edits = []
    for span in sorted(parser.floor_spans, key=lambda s: s.end - s.start):
        left = _splice(source, span.start, span.op, edits)
        right = source[span.op + 2:span.right] + _splice(source, span.right, span.end, edits)
        text = name + "(" + _operand(left) + "," + _operand(right) + ")"
        # Nested replacements are already embedded in this parent. Do not keep
        # copies of every intermediate expression for deeply nested input.
        edits = [edit for edit in edits if not (span.start <= edit[0] and edit[1] <= span.end)]
        edits.append((span.start, span.end, text))
    lowered = _HELPER.format(name=name) + _splice(source, 0, len(source), edits)
    return lowered, ["lowered %d floor-division operator(s) in sandbox copy (double precision)" % len(parser.floor_spans)]
