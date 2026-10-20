"""Bytecode -> source must behave exactly like the original program."""
import random

import pytest
from lupa.lua51 import LuaRuntime

from deobf import bytecode, decompile, deobfuscate, luac
from test_simplify import MORE, PROGRAMS, _gen_program, _gen_program2, run_plain

_lua = LuaRuntime(encoding=None)
_dump = _lua.eval("function(s) local f, e = loadstring(s) if not f then error(e) end return string.dump(f) end")


def dump(src):
    return _dump(src.encode("latin-1"))


ALL = dict(PROGRAMS)
ALL.update(MORE)


@pytest.mark.parametrize("name", sorted(ALL))
def test_translation_preserves_output(name):
    src = ALL[name]
    assert run_plain(decompile.translate(dump(src))) == run_plain(src)


def test_random_programs_survive_bytecode_round_trip():
    for gen, seed in ((_gen_program, 11), (_gen_program2, 12)):
        rng = random.Random(seed)
        for n in range(120):
            src = gen(rng)
            out = decompile.translate(dump(src))
            assert run_plain(out) == run_plain(src), "%s program %d\n%s" % (gen.__name__, n, src)


def test_parser_reads_header_constants_and_nested_functions():
    p = luac.parse(dump('local function f(x) return x .. "!" end print(f("hi"), 3)'))
    assert "print" in p.consts and 3 in p.consts and len(p.protos) == 1 and p.protos[0].numparams == 1


@pytest.mark.parametrize("junk", [b"", b"\x1bLua", b"\x1bLuaR\x00\x01", b"\x1bLua\x51\x00\x01\x04\x08\x04\x08\x00\xff"])
def test_parser_rejects_malformed_chunks(junk):
    with pytest.raises(luac.LuacError):
        luac.parse(junk)


PAYLOAD = 'local t = {} for i = 1, 3 do t[#t + 1] = i * 2 end print("sum", t[1] + t[2] + t[3])'


def test_embedded_byte_array_is_decompiled_and_layer_followed():
    bc = dump(PAYLOAD)
    r = deobfuscate("return Loader({%s})()" % ",".join(str(b) for b in bc))
    assert any("decompiled embedded Lua bytecode" in n for n in r.notes)
    assert "sum\t12" in r.prints


def test_embedded_escaped_string_passed_to_loadstring():
    esc = "".join("\\%d" % b for b in dump(PAYLOAD))
    r = deobfuscate('local f = loadstring("%s") f()' % esc)
    assert any("decompiled embedded Lua bytecode" in n for n in r.notes)
    assert "sum\t12" in r.prints


def test_bytecode_built_at_run_time_is_captured_and_decompiled_not_executed():
    # the chunk only exists after concatenation at run time, so static carving cannot see it
    raw = dump(PAYLOAD)
    half = len(raw) // 2
    a = "".join("\\%d" % b for b in raw[:half])
    b = "".join("\\%d" % b for b in raw[half:])
    r = deobfuscate('local f = loadstring(table.concat({"%s", "%s"})) f()' % (a, b))
    assert any("decompiled bytecode passed to loadstring" in n for n in r.notes)
    assert "sum\t12" in r.prints


def test_decompiler_refuses_unsupported_opcode_cleanly():
    p = luac.parse(dump("return 1"))
    p.code[0].op = 63
    with pytest.raises(decompile.DecompileError):
        decompile.translate(p)
