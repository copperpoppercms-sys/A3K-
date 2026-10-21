import pytest
from lupa.lua51 import LuaRuntime

from deobf import luaast, passes, sandbox
from deobf.compat import prepare


@pytest.mark.parametrize("literal,expected", [
    ("0b1000", 8), ("0B100010__0", 68), ("0x3Ff_fa0", 4194208),
    ("0X2__ffFE7", 3145703), ("0x11A7_", 4519), ("1_234", 1234),
    ("1_2.5_0", 12.5), ("1_0e+2_0", 1e21), (".1_25", .125),
])
def test_luau_literals_are_lowered_in_execution_copy(literal, expected):
    lowered, notes = prepare("return " + literal)
    assert LuaRuntime().execute(lowered) == expected
    assert notes and "numeric literal" in notes[0]
    assert luaast.parse("return " + literal)


def test_numeric_literals_in_comments_strings_and_names_are_preserved():
    source = '-- 0b101 and 0Xf_f\nlocal abc0b101="0b11"; local x=[=[0x1_0]=]; return abc0b101,x'
    assert passes.normalize_numbers(source) == (source, 0)
    assert prepare(source) == (source, [])


@pytest.mark.parametrize("source", ["return 0b102", "return 0b1_xyz", "return 0x1_foo", "return 12_foo"])
def test_malformed_numeric_tokens_are_not_repaired(source):
    assert passes.normalize_numbers(source) == (source, 0)


@pytest.mark.parametrize("source,expected", [
    ('local n=0; for i=1,6 do if i%2==0 then continue end n=n+i end return n', 9),
    ('local n=0; for i=1,9 do if i==5 then break end if i%2==0 then continue end n=n+i end return n', 4),
    ('local n=0; local i=0; while i<8 do i=i+1; if i<4 then continue end if i==7 then break end n=n+i end return n', 15),
    ('local n=0; for i=1,3 do for j=1,4 do if j==2 then continue end if j==4 then break end n=n+1 end if i==2 then continue end n=n+10 end return n', 26),
    ('local n=0; for _,v in ipairs({1,2,3,4}) do if v==3 then continue end n=n+v end return n', 7),
    ('for i=1,4 do if i==1 then continue end return i,99 end', (2, 99)),
    ('local n=0; for i=1,3 do continue; n=n+99 end return n', 0),
    ('local n=0; for i=1,3 do if false then continue end break end return n', 0),
    ('local n=0; for i=1,3 do repeat n=n+1; break until false; continue end return n', 3),
    ('local __a3k_loop_break=123; for i=1,3 do continue end return __a3k_loop_break', 123),
    ('local calls=0; local function cond() calls=calls+1; return calls<4 end; while cond() do continue end return calls', 4),
    ('local fs={}; for i=1,3 do local value=i; fs[i]=function() return value end; continue end return fs[1](),fs[2](),fs[3]()', (1,2,3)),
    ('local n=0; repeat n=n+1; if n<3 then continue end until n==3; return n', 3),
    ('local n=0; local function cond() n=n+99; return false end; repeat if n<2 then n=n+1; continue end break until cond(); return n', 100),
])
def test_continue_and_break_preserve_control_flow(source, expected):
    lowered, notes = prepare(source)
    assert LuaRuntime().execute(lowered) == expected
    assert any("continue statement" in note for note in notes)
    assert lowered.count("\n") == source.count("\n")


def test_continue_does_not_cross_function_boundary():
    source = 'for i=1,3 do local function f() continue end end'
    lowered, notes = prepare(source)
    assert lowered == source
    assert any("outside a lexical loop" in note for note in notes)


def test_repeat_continue_is_explicitly_unsupported_to_preserve_condition_scope():
    source = 'repeat local x=1; continue until x==1'
    lowered, notes = prepare(source)
    assert lowered == source
    assert any("repeat/until is not supported" in note for note in notes)


def test_compound_numeric_and_continue_compatibility_work_together():
    result = sandbox.run('local n=0; for i=1,0b100 do if i==0x2_ then continue end n+=i end print(n)')
    assert not result.error and result.prints == ["8"]
    assert len(result.compatibility) == 3


@pytest.mark.parametrize("source,expected", [
    ('local n=0; for k,v in {a=3,b=4} do n=n+v end return n', 7),
    ('local n=0; for k,v in pairs({a=3,b=4}) do n=n+v end return n', 7),
    ('local t=setmetatable({}, {__iter=function(t) return ipairs({4,5}) end}); local n=0; for k,v in t do n=n+v end return n', 9),
    ('local t=setmetatable({}, {__call=function(t,s,n) n=(n or 0)+1; if n<3 then return n,n*10 end end}); local sum=0; for k,v in t do sum=sum+v end return sum', 30),
    ('local calls=0; local function f() calls=calls+1; return {1,2} end; local n=0; for k,v in f() do if k==1 then continue end n=n+v end return n,calls', (2,1)),
    ('local __a3k_iter=42; local n=0; for k in {} do n=n+1 end return n,__a3k_iter', (0,42)),
])
def test_generalized_iteration_preserves_iterators_and_table_iteration(source, expected):
    lowered, notes = prepare(source)
    assert LuaRuntime().execute(lowered) == expected
    assert any("generic iteration" in note for note in notes)
    assert source.count("\n") == lowered.count("\n")


def test_bit_counters_handle_zero_negative_and_each_bit():
    source = 'print(bit32.countlz(0),bit32.countrz(0),bit32.countlz(-1),bit32.countrz(-1)); '
    source += 'for i=0,31 do print(bit32.countlz(2^i),bit32.countrz(2^i)) end'
    result = sandbox.run(source)
    assert not result.error
    assert result.prints == ["32\t32\t0\t0"] + [f"{31-i}\t{i}" for i in range(32)]


def test_buffers_have_little_endian_integer_reads_and_mutable_writes():
    result = sandbox.run('''local b=buffer.create(12)
buffer.writeu32(b,0,4294967295)
buffer.writei16(b,4,-32768)
buffer.writeu8(b,6,257)
print(typeof(b),buffer.len(b),buffer.readu32(b,0),buffer.readi32(b,0),buffer.readi16(b,4),buffer.readu8(b,6),buffer.readu8(b,11))''')
    assert not result.error
    assert result.prints == ["buffer\t12\t4294967295\t-1\t-32768\t1\t0"]


def test_buffer_overlapping_copy_and_string_operations():
    result = sandbox.run('''local b=buffer.fromstring("abcdef")
buffer.copy(b,1,b,0,5); print(buffer.tostring(b))
buffer.copy(b,0,b,1,5); print(buffer.tostring(b))
buffer.writestring(b,0,"XYZ"); buffer.fill(b,3,33,3)
print(buffer.readstring(b,0,6))''')
    assert not result.error
    assert result.prints == ["aabcde", "abcdee", "XYZ!!!"]


@pytest.mark.parametrize("call", [
    'buffer.create(1048577)', 'buffer.readu32(buffer.create(3),0)',
    'buffer.readu8(buffer.create(3),-1)', 'buffer.readu8(buffer.create(3),0.5)',
    'buffer.writestring(buffer.create(3),0,"a",2)', 'buffer.readu8({},0)',
])
def test_buffer_bounds_are_real_errors(call):
    result = sandbox.run(call)
    assert result.error and not result.prints


def test_buffer_float_reads_match_ieee754_reference():
    import math
    import random
    import struct
    rng = random.Random(149)
    expected, statements = [], []
    for format_, reader in (("<f", "readf32"), ("<d", "readf64")):
        values = [0., -0., 1., -1., math.inf, -math.inf, math.nan, 1e-40, 1e-300]
        values += [rng.uniform(-1e20,1e20) for _ in range(20)]
        for value in values:
            packed = struct.pack(format_, value)
            expected.append(struct.unpack(format_, packed)[0])
            statements.append('print(buffer.%s(buffer.fromstring(%s),0))' % (reader, passes.emit_str(packed.decode('latin1'))))
    result = sandbox.run('\n'.join(statements))
    assert not result.error
    assert len(result.prints) == len(expected)
    for output, value in zip(result.prints, expected):
        actual = float(output)
        if math.isnan(value):
            assert math.isnan(actual)
        else:
            assert math.isclose(actual, value, rel_tol=1e-13, abs_tol=0)


def test_cpu_budget_is_reported_as_a_limit_instead_of_generic_runtime_error():
    result = sandbox.run('while true do end', seconds=.05)
    assert result.timed_out
    assert 'time limit' in result.error
