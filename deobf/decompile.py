"""Lua 5.1 bytecode -> Lua source.

`translate` is exact for any bytecode: every register becomes a local, every basic block a branch of a dispatch loop,
closures capture boxed registers (so loops that create closures keep Lua's per-iteration semantics). `decompile` then
tries to turn that into structured code and falls back to the exact form (see structure.py)."""
import math
import re

from . import luac, passes
from .luac import BITRK, OP

_KEYWORDS = frozenset(
    "and break do else elseif end false for function goto if in local nil not or repeat return then true until while".split()
)
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
PRELUDE = 'local pack, unpack = function(...) return {n = select("#", ...), ...} end, unpack or table.unpack\n'
MAX_LOCAL_REGS = 150


class DecompileError(Exception):
    pass


def lit(v):
    if v is None:
        return "nil"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, str):
        return passes.emit_str(v)
    if isinstance(v, float):
        if v != v:
            return "(0/0)"
        if v in (math.inf, -math.inf):
            return "(1/0)" if v > 0 else "(-1/0)"
        if v == int(v) and abs(v) < 2 ** 53:
            return "(%d)" % int(v) if v < 0 else str(int(v))
        return "(%r)" % v if v < 0 else repr(v)
    return "(%d)" % v if v < 0 else str(v)


def global_ref(name):
    return name if _IDENT.match(name) and name not in _KEYWORDS else "_G[%s]" % lit(name)


class FuncGen:
    def __init__(self, p, ups, depth=0):
        self.p, self.ups, self.depth = p, ups, depth
        self.code = p.code
        self.by_pc = {i.pc: i for i in p.code}
        self.table_regs = p.maxstack > MAX_LOCAL_REGS
        self.skip = self._data_pcs()
        self.boxed = self._boxed_regs()
        self.mv_n = 0

    # ------------------------------------------------------------ analysis
    def _data_pcs(self):
        """Instruction slots that are operands of another instruction (closure captures, SETLIST count)."""
        skip = set()
        for i in self.code:
            if i.op == OP["CLOSURE"]:
                for k in range(self.p.protos[i.bx].nups):
                    skip.add(i.pc + 1 + k)
            elif i.op == OP["SETLIST"] and i.c == 0:
                skip.add(i.pc + 1)
        return skip

    def _boxed_regs(self):
        boxed = set()
        for i in self.code:
            if i.op == OP["CLOSURE"]:
                for k in range(self.p.protos[i.bx].nups):
                    cap = self.by_pc.get(i.pc + 1 + k)
                    if cap is not None and cap.op == OP["MOVE"]:
                        boxed.add(cap.b)
        return boxed

    # ------------------------------------------------------------ names
    def r(self, n):
        base = "R[%d]" % n if self.table_regs else "r%d" % n
        return base + "[1]" if n in self.boxed else base

    def rk(self, x):
        if x >= BITRK:
            return lit(self.p.consts[x - BITRK])
        return self.r(x)

    def K(self, i):
        return lit(self.p.consts[i])

    def up(self, i):
        return "%s[1]" % self.ups[i]

    # ------------------------------------------------------------ blocks
    def leaders(self):
        L = {1}
        n = len(self.code)
        for i in self.code:
            if i.pc in self.skip:
                continue
            o = i.op
            if o == OP["JMP"]:
                L.add(i.pc + 1 + i.sbx)
                L.add(i.pc + 1)
            elif o in (OP["EQ"], OP["LT"], OP["LE"], OP["TEST"], OP["TESTSET"]):
                L.add(i.pc + 2)
            elif o == OP["LOADBOOL"] and i.c:
                L.add(i.pc + 2)
            elif o in (OP["FORLOOP"], OP["FORPREP"]):
                L.add(i.pc + 1 + i.sbx)
                L.add(i.pc + 1)
            elif o == OP["TFORLOOP"]:
                L.add(i.pc + 2)
            elif o in (OP["RETURN"], OP["TAILCALL"]):
                L.add(i.pc + 1)
        return sorted(x for x in L if 1 <= x <= n and x not in self.skip)

    def call_args(self, a, b, mv):
        """Argument list text for a call whose function is in register a."""
        if b == 0:
            if mv is None:
                raise DecompileError("open call without producer")
            start, name = mv
            fixed = [self.r(x) for x in range(a + 1, start)]
            return ", ".join(fixed + ["unpack(%s, 1, %s.n)" % (name, name)])
        return ", ".join(self.r(x) for x in range(a + 1, a + b))

    def closure(self, i):
        sub = self.p.protos[i.bx]
        caps = []
        for k in range(sub.nups):
            cap = self.by_pc[i.pc + 1 + k]
            if cap.op == OP["MOVE"]:
                caps.append("R[%d]" % cap.b if self.table_regs else "r%d" % cap.b)
            elif cap.op == OP["GETUPVAL"]:
                caps.append(self.ups[cap.b])
            else:
                raise DecompileError("bad closure capture")
        names = ["up%d_%d" % (self.depth + 1, j) for j in range(sub.nups)]
        inner = translate_proto(sub, names, self.depth + 1)
        if not names:
            return inner
        return "(function(%s) return %s end)(%s)" % (", ".join(names), inner, ", ".join(caps))

    def block(self, start, end_pc, labels):
        """Statements for instructions [start, end_pc) of one basic block; returns (lines, terminated)."""
        out = []
        mv = None
        pc = start
        terminated = False
        while pc < end_pc:
            if pc in self.skip:
                pc += 1
                continue
            i = self.by_pc[pc]
            o = i.op
            a, b, c = i.a, i.b, i.c
            R = self.r
            if o == OP["MOVE"]:
                out.append("%s = %s" % (R(a), R(b)))
            elif o == OP["LOADK"]:
                out.append("%s = %s" % (R(a), self.K(i.bx)))
            elif o == OP["LOADBOOL"]:
                out.append("%s = %s" % (R(a), "true" if b else "false"))
                if c:
                    out.append("pc = %d" % (pc + 2))
                    terminated = True
            elif o == OP["LOADNIL"]:
                for x in range(a, b + 1):
                    out.append("%s = nil" % R(x))
            elif o == OP["GETUPVAL"]:
                out.append("%s = %s" % (R(a), self.up(b)))
            elif o == OP["GETGLOBAL"]:
                out.append("%s = %s" % (R(a), global_ref(self.p.consts[i.bx])))
            elif o == OP["GETTABLE"]:
                out.append("%s = %s[%s]" % (R(a), R(b), self.rk(c)))
            elif o == OP["SETGLOBAL"]:
                out.append("%s = %s" % (global_ref(self.p.consts[i.bx]), R(a)))
            elif o == OP["SETUPVAL"]:
                out.append("%s = %s" % (self.up(b), R(a)))
            elif o == OP["SETTABLE"]:
                out.append("%s[%s] = %s" % (R(a), self.rk(b), self.rk(c)))
            elif o == OP["NEWTABLE"]:
                out.append("%s = {}" % R(a))
            elif o == OP["SELF"]:
                out.append("local self_obj = %s" % R(b))
                out.append("%s = self_obj" % R(a + 1))
                out.append("%s = self_obj[%s]" % (R(a), self.rk(c)))
            elif o in (OP["ADD"], OP["SUB"], OP["MUL"], OP["DIV"], OP["MOD"], OP["POW"]):
                sym = {OP["ADD"]: "+", OP["SUB"]: "-", OP["MUL"]: "*", OP["DIV"]: "/", OP["MOD"]: "%", OP["POW"]: "^"}[o]
                out.append("%s = %s %s %s" % (R(a), self.rk(b), sym, self.rk(c)))
            elif o == OP["UNM"]:
                out.append("%s = -%s" % (R(a), R(b)))
            elif o == OP["NOT"]:
                out.append("%s = not %s" % (R(a), R(b)))
            elif o == OP["LEN"]:
                out.append("%s = #%s" % (R(a), R(b)))
            elif o == OP["CONCAT"]:
                out.append("%s = %s" % (R(a), " .. ".join(R(x) for x in range(b, c + 1))))
            elif o == OP["JMP"]:
                out.append("pc = %d" % (pc + 1 + i.sbx))
                terminated = True
            elif o in (OP["EQ"], OP["LT"], OP["LE"]):
                jmp = self.by_pc[pc + 1]
                sym = {OP["EQ"]: "==", OP["LT"]: "<", OP["LE"]: "<="}[o]
                if o == OP["EQ"]:
                    cond = "%s %s %s" % (self.rk(b), "==" if a else "~=", self.rk(c))
                else:
                    cond = "%s%s %s %s%s" % ("" if a else "not (", self.rk(b), sym, self.rk(c), "" if a else ")")
                out.append("if %s then pc = %d else pc = %d end" % (cond, pc + 2 + jmp.sbx, pc + 2))
                terminated = True
                pc += 1
            elif o == OP["TEST"]:
                jmp = self.by_pc[pc + 1]
                cond = R(a) if c else "not " + R(a)
                out.append("if %s then pc = %d else pc = %d end" % (cond, pc + 2 + jmp.sbx, pc + 2))
                terminated = True
                pc += 1
            elif o == OP["TESTSET"]:
                jmp = self.by_pc[pc + 1]
                cond = R(b) if c else "not " + R(b)
                out.append("if %s then %s = %s pc = %d else pc = %d end" % (cond, R(a), R(b), pc + 2 + jmp.sbx, pc + 2))
                terminated = True
                pc += 1
            elif o in (OP["CALL"], OP["TAILCALL"]):
                fn = "%s(%s)" % (R(a), self.call_args(a, b, mv))
                mv = None
                if o == OP["TAILCALL"]:
                    out.append("do return %s end" % fn)
                    terminated = True
                elif c == 1:
                    out.append(fn)
                elif c == 0:
                    self.mv_n += 1
                    name = "mv%d" % self.mv_n
                    out.append("local %s = pack(%s)" % (name, fn))
                    mv = (a, name)
                else:
                    out.append("%s = %s" % (", ".join(R(x) for x in range(a, a + c - 1)), fn))
            elif o == OP["RETURN"]:
                if b == 1:
                    out.append("do return end")
                elif b == 0:
                    if mv is None:
                        raise DecompileError("open return without producer")
                    start, name = mv
                    vals = [R(x) for x in range(a, start)] + ["unpack(%s, 1, %s.n)" % (name, name)]
                    out.append("do return %s end" % ", ".join(vals))
                else:
                    out.append("do return %s end" % ", ".join(R(x) for x in range(a, a + b - 1)))
                terminated = True
            elif o == OP["FORPREP"]:
                out.append("%s = %s - %s" % (R(a), R(a), R(a + 2)))
                out.append("pc = %d" % (pc + 1 + i.sbx))
                terminated = True
            elif o == OP["FORLOOP"]:
                out.append("%s = %s + %s" % (R(a), R(a), R(a + 2)))
                out.append(
                    "if (%s > 0 and %s <= %s) or (%s <= 0 and %s >= %s) then %s = %s pc = %d else pc = %d end"
                    % (R(a + 2), R(a), R(a + 1), R(a + 2), R(a), R(a + 1), R(a + 3), R(a), pc + 1 + i.sbx, pc + 1)
                )
                terminated = True
            elif o == OP["TFORLOOP"]:
                jmp = self.by_pc[pc + 1]
                targets = ", ".join(R(x) for x in range(a + 3, a + 3 + c))
                out.append("%s = %s(%s, %s)" % (targets, R(a), R(a + 1), R(a + 2)))
                out.append(
                    "if %s ~= nil then %s = %s pc = %d else pc = %d end" % (R(a + 3), R(a + 2), R(a + 3), pc + 2 + jmp.sbx, pc + 2)
                )
                terminated = True
                pc += 1
            elif o == OP["SETLIST"]:
                cnum = c
                if c == 0:
                    cnum = self.by_pc[pc + 1].raw
                base = (cnum - 1) * 50
                if b == 0:
                    if mv is None:
                        raise DecompileError("open setlist without producer")
                    start, name = mv
                    for x in range(a + 1, start):
                        out.append("%s[%d] = %s" % (R(a), base + x - a, R(x)))
                    out.append("for k = 1, %s.n do %s[%d + k + %d] = %s[k] end" % (name, R(a), base, start - a - 1, name))
                    mv = None
                else:
                    for x in range(1, b + 1):
                        out.append("%s[%d] = %s" % (R(a), base + x, R(a + x)))
            elif o == OP["CLOSE"]:
                for x in sorted(self.boxed):
                    if x >= a:
                        out.append("%s = {}" % (("R[%d]" % x) if self.table_regs else ("r%d" % x)))
            elif o == OP["CLOSURE"]:
                out.append("%s = %s" % (R(a), self.closure(i)))
            elif o == OP["VARARG"]:
                if b == 0:
                    self.mv_n += 1
                    name = "mv%d" % self.mv_n
                    out.append("local %s = pack(...)" % name)
                    mv = (a, name)
                elif b > 1:
                    out.append("%s = ..." % ", ".join(R(x) for x in range(a, a + b - 1)))
            else:
                raise DecompileError("unsupported opcode %s" % i.name)
            if terminated:
                break
            pc += 1
        return out, terminated

    def body(self):
        p = self.p
        L = self.leaders()
        starts = {s: (L[k + 1] if k + 1 < len(L) else len(self.code) + 1) for k, s in enumerate(L)}
        lines = []
        # registers
        params = [("R[%d]" if self.table_regs else "r%d") % x for x in range(p.numparams)]
        if self.table_regs:
            lines.append("local R = {%s}" % ", ".join(("[%d] = r%d" % (x, x)) for x in range(p.numparams)))
            sig = ["r%d" % x for x in range(p.numparams)]
        else:
            others = ["r%d" % x for x in range(p.numparams, p.maxstack)]
            if others:
                lines.append("local %s" % ", ".join(others))
            sig = params
        for x in sorted(self.boxed):
            name = "R[%d]" % x if self.table_regs else "r%d" % x
            lines.append("%s = {%s}" % (name, name if x < p.numparams else ""))
        lines.append("local pc = %d" % (L[0] if L else 1))
        lines.append("while true do")
        first = True
        for s in L:
            e = starts[s]
            stmts, term = self.block(s, e, None)
            if not term:
                stmts.append("pc = %d" % e)
            lines.append("%s pc == %d then" % ("if" if first else "elseif", s))
            first = False
            lines += ["  " + x for x in stmts]
        if not first:
            lines.append("else return end")
        lines.append("end")
        return sig, lines


def translate_proto(p, ups, depth=0):
    g = FuncGen(p, ups, depth)
    sig, lines = g.body()
    if p.is_vararg & 2 or p.is_vararg:
        sig = sig + ["..."]
    return "function(%s)\n%s\nend" % (", ".join(sig), "\n".join("  " + x for x in lines))


def translate(chunk):
    """Exact translation of a whole chunk into a Lua function expression statement list."""
    p = luac.parse(chunk) if isinstance(chunk, (bytes, str)) else chunk
    return PRELUDE + "return (" + translate_proto(p, []) + ")(...)"


def decompile(chunk):
    """Bytecode (bytes) -> Lua source text."""
    return translate(chunk)
