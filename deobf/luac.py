"""Lua 5.1 binary chunk (luac / string.dump) parser. Pure parsing: nothing is executed."""
import struct
from dataclasses import dataclass, field

MAGIC = b"\x1bLua"

OPNAMES = [
    "MOVE", "LOADK", "LOADBOOL", "LOADNIL", "GETUPVAL", "GETGLOBAL", "GETTABLE", "SETGLOBAL", "SETUPVAL", "SETTABLE",
    "NEWTABLE", "SELF", "ADD", "SUB", "MUL", "DIV", "MOD", "POW", "UNM", "NOT", "LEN", "CONCAT", "JMP", "EQ", "LT", "LE",
    "TEST", "TESTSET", "CALL", "TAILCALL", "RETURN", "FORLOOP", "FORPREP", "TFORLOOP", "SETLIST", "CLOSE", "CLOSURE", "VARARG",
]
OP = {n: i for i, n in enumerate(OPNAMES)}
# iABx / iAsBx instructions; everything else is iABC
_ABX = {OP["LOADK"], OP["GETGLOBAL"], OP["SETGLOBAL"], OP["CLOSURE"]}
_ASBX = {OP["JMP"], OP["FORLOOP"], OP["FORPREP"]}
BITRK = 256


class LuacError(Exception):
    pass


@dataclass
class Instr:
    pc: int  # 1-based index in the function
    op: int
    a: int
    b: int
    c: int
    bx: int
    sbx: int
    raw: int = 0

    @property
    def name(self):
        return OPNAMES[self.op] if self.op < len(OPNAMES) else "OP%d" % self.op


@dataclass
class Proto:
    source: str = ""
    linedefined: int = 0
    lastlinedefined: int = 0
    nups: int = 0
    numparams: int = 0
    is_vararg: int = 0
    maxstack: int = 0
    code: list = field(default_factory=list)
    consts: list = field(default_factory=list)
    protos: list = field(default_factory=list)
    locvars: list = field(default_factory=list)  # (name, startpc, endpc)
    upnames: list = field(default_factory=list)


class _Reader:
    def __init__(self, data):
        self.d, self.p = data, 0

    def take(self, n):
        if n < 0 or self.p + n > len(self.d):
            raise LuacError("truncated chunk")
        b = self.d[self.p:self.p + n]
        self.p += n
        return b

    def byte(self):
        return self.take(1)[0]


def decode(word, pc):
    op = word & 0x3F
    a = (word >> 6) & 0xFF
    c = (word >> 14) & 0x1FF
    b = (word >> 23) & 0x1FF
    bx = (word >> 14) & 0x3FFFF
    return Instr(pc, op, a, b, c, bx, bx - 131071, word)


def parse(data):
    """Parse a Lua 5.1 chunk (bytes) and return the main Proto."""
    if isinstance(data, str):
        data = data.encode("latin-1")
    if data[:4] != MAGIC:
        raise LuacError("not a Lua chunk")
    r = _Reader(data)
    r.take(4)
    if r.byte() != 0x51:
        raise LuacError("only Lua 5.1 bytecode is supported")
    if r.byte() != 0:
        raise LuacError("unsupported chunk format")
    little = r.byte() == 1
    size_int, size_t, size_instr, size_num, integral = r.byte(), r.byte(), r.byte(), r.byte(), r.byte()
    if size_instr != 4 or size_int not in (4, 8) or size_t not in (4, 8) or size_num not in (4, 8):
        raise LuacError("unsupported chunk layout")
    e = "<" if little else ">"
    fint = e + {4: "i", 8: "q"}[size_int]
    fsize = e + {4: "I", 8: "Q"}[size_t]
    fnum = e + ("i" if integral and size_num == 4 else "q" if integral else {4: "f", 8: "d"}[size_num])

    def rint():
        return struct.unpack(fint, r.take(size_int))[0]

    def rsize():
        return struct.unpack(fsize, r.take(size_t))[0]

    def rstring():
        n = rsize()
        if n == 0:
            return None
        s = r.take(n)
        return s[:-1].decode("latin-1")

    def func(depth=0):
        if depth > 200:
            raise LuacError("nesting too deep")
        p = Proto()
        p.source = rstring() or ""
        p.linedefined, p.lastlinedefined = rint(), rint()
        p.nups, p.numparams, p.is_vararg, p.maxstack = r.byte(), r.byte(), r.byte(), r.byte()
        n = rint()
        if n < 0 or n > 5_000_000:
            raise LuacError("bad instruction count")
        raw = r.take(4 * n)
        words = struct.unpack(e + "%dI" % n, raw)
        p.code = [decode(w, i + 1) for i, w in enumerate(words)]
        for _ in range(rint()):
            t = r.byte()
            if t == 0:
                p.consts.append(None)
            elif t == 1:
                p.consts.append(r.byte() != 0)
            elif t == 3:
                v = struct.unpack(fnum, r.take(size_num))[0]
                p.consts.append(int(v) if isinstance(v, float) and v == v and abs(v) < 2 ** 53 and v == int(v) else v)
            elif t == 4:
                p.consts.append(rstring() or "")
            else:
                raise LuacError("bad constant type %d" % t)
        for _ in range(rint()):
            p.protos.append(func(depth + 1))
        for _ in range(rint()):  # line info
            rint()
        for _ in range(rint()):
            name = rstring() or ""
            p.locvars.append((name, rint(), rint()))
        for _ in range(rint()):
            p.upnames.append(rstring() or "")
        return p

    main = func()
    return main


def find_chunks(data):
    """Offsets of every embedded Lua 5.1 header in a byte string."""
    out, i = [], 0
    while True:
        i = data.find(MAGIC + b"\x51\x00", i)
        if i < 0:
            return out
        out.append(i)
        i += 1
