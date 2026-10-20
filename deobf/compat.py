"""Targeted Luau operator lowering for sandbox copies. Never executes input."""
import re
from dataclasses import dataclass

from . import luaast as a, passes


@dataclass
class FloorSpan:
    start: int
    op: int
    right: int
    end: int


@dataclass
class CompoundSpan:
    start: int
    op: int
    right: int
    end: int
    operator: str
    object_end: int = None
    key_start: int = None
    key_end: int = None
    key_literal: str = None
    target_name: str = None
    target_trivia: str = ""


class SpanParser(a.Parser):
    def __init__(self, source):
        super().__init__(source)
        self.source = source
        self.floor_spans = []
        self.compound_spans = []

    def exprstat(self):
        start = self.peek()[2]
        target_start = self.p
        target = self.suffixedexp()
        token = self.peek()
        if token[0] == "op" and token[1] in a._COMPOUND:
            if not isinstance(target, (a.Name, a.Index)):
                raise a.ParseError("cannot assign to expression")
            target_end = self.p
            self.next()
            right_start = self.peek()[2]
            right = self.expr()
            span = CompoundSpan(start, token[2], right_start, self.peek()[2], a._COMPOUND[token[1]])
            if isinstance(target, a.Name):
                span.target_name = target.name
            if isinstance(target, a.Index):
                if self.toks[target_end - 1][1] == "]":
                    depth, opening = 1, target_end - 2
                    while opening >= target_start:
                        t = self.toks[opening]
                        if t[0] == "op":
                            depth += (t[1] == "]") - (t[1] == "[")
                        if depth == 0:
                            break
                        opening -= 1
                    if opening < target_start:
                        raise a.ParseError("compound target index unavailable")
                    span.object_end = self.toks[opening][2]
                    span.key_start = span.object_end + 1
                    span.key_end = self.toks[target_end - 1][2]
                    span.target_trivia = self.source[span.key_end + 1:span.op]
                else:
                    span.object_end = self.toks[target_end - 2][2]
                    span.key_literal = passes.emit_str(self.toks[target_end - 1][1])
                    key_token = self.toks[target_end - 1]
                    span.target_trivia = self.source[span.object_end + 1:key_token[2]] + self.source[key_token[2] + len(key_token[1]):span.op]
            self.compound_spans.append(span)
            if len(self.compound_spans) > 256:
                raise a.ParseError("compound-assignment operator limit (256)")
            return a.Assign([target], [a.Bin(span.operator, target, right)])
        if self.check("op", "=") or self.check("op", ","):
            targets = [target]
            while self.accept("op", ","):
                targets.append(self.suffixedexp())
            self.expect("op", "=")
            if any(not isinstance(t, (a.Name, a.Index)) for t in targets):
                raise a.ParseError("cannot assign to expression")
            return a.Assign(targets, self.exprlist())
        if not isinstance(target, (a.Call, a.Method)):
            raise a.ParseError("syntax error near %r at %d" % (token[1], token[2]))
        return a.CallStmt(target)

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
    operator_re = re.compile(r"//|(?:\+|-|\*|/|%|\^|\.\.)=")
    if source.startswith("\x1b") or not operator_re.search(source):
        return source, []
    if not any(operator_re.search(text) for kind, text in passes.lua_split(source) if kind == "other"):
        return source, []
    if len(source) > 2 * 1024 * 1024:
        return source, ["operator compatibility skipped: input exceeds 2 MB"]
    try:
        parser = SpanParser(source)
        parser.parse_chunk()
    except (a.ParseError, ValueError, OverflowError, RecursionError) as e:
        return source, ["operator compatibility unavailable: " + str(e)[:150]]
    if not parser.floor_spans and not parser.compound_spans:
        return source, []
    reserved = {token[1] for token in parser.toks if token[0] == "name"}
    def fresh(base):
        name, suffix = base, 0
        while name in reserved:
            suffix += 1
            name = "%s_%d" % (base, suffix)
        reserved.add(name)
        return name

    name = fresh("__a3k_floor_div")
    obj, key, old = (fresh("__a3k_" + suffix) for suffix in ("object", "key", "old"))
    need_floor = bool(parser.floor_spans) or any(span.operator == "//" for span in parser.compound_spans)
    edits = []
    for span in sorted(parser.floor_spans + parser.compound_spans, key=lambda s: s.end - s.start):
        if isinstance(span, FloorSpan):
            left = _splice(source, span.start, span.op, edits)
            right = source[span.op + 2:span.right] + _splice(source, span.right, span.end, edits)
            text = name + "(" + _operand(left) + "," + _operand(right) + ")"
        else:
            # Keep the original expressions and their line numbers. Evaluate an
            # indexed target, its key and its old value exactly once, before RHS.
            rhs = _operand(_splice(source, span.right, span.end, edits))
            trivia = source[span.op + len(span.operator) + 1:span.right]
            def operation(left):
                return name + "(" + left + "," + rhs + ")" if span.operator == "//" else left + " " + span.operator + " " + rhs
            if span.object_end is None:
                lhs = source[span.start:span.op]
                text = "do " + lhs + "=" + trivia + operation(span.target_name) + "; end "
            else:
                object_source = _operand(_splice(source, span.start, span.object_end, edits))
                key_source = span.key_literal if span.key_literal is not None else _operand(_splice(source, span.key_start, span.key_end, edits))
                text = "do local %s=%s; local %s=%s; local %s=%s[%s]; %s[%s]=" % (obj, object_source, key, key_source, old, obj, key, obj, key)
                text += span.target_trivia + trivia + operation(old) + "; end "
        # Nested replacements are already embedded in this parent. Do not keep
        # copies of every intermediate expression for deeply nested input.
        edits = [edit for edit in edits if not (span.start <= edit[0] and edit[1] <= span.end)]
        edits.append((span.start, span.end, text))
    lowered = (_HELPER.format(name=name) if need_floor else "") + _splice(source, 0, len(source), edits)
    notes = []
    if parser.floor_spans:
        notes.append("lowered %d floor-division operator(s) in sandbox copy (double precision)" % len(parser.floor_spans))
    if parser.compound_spans:
        notes.append("lowered %d compound-assignment operator(s) in sandbox copy" % len(parser.compound_spans))
    return lowered, notes
