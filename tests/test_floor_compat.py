import asyncio
import random

import pytest
from lupa.lua51 import LuaRuntime as Lua51
from lupa.lua54 import LuaRuntime as Lua54

from deobf import deobfuscate, passes, sandbox
from deobf.compat import prepare


@pytest.mark.parametrize("expression", [
    "8//3*2", "8//(3*2)", "8*3//2", "21//4//2", "21//(4//2)",
    "-7//2", "7//-2", "-7//-2", "2^5//3", "-2^3//3", "3//2^2",
    "20%7//2", "1+20//(3//2)", "(1+20)//3-2", "3*(11//4)+5//2",
])
def test_precedence_and_negative_values_match_native_floor_division(expression):
    source = "return " + expression
    lowered, notes = prepare(source)
    assert notes and Lua51().execute(lowered) == Lua54().execute(source)


def test_numeric_floor_division_matches_native_double_precision():
    source = "return function(a,b) return a//b end"
    lowered, _ = prepare(source)
    actual = Lua51().execute(lowered)
    reference = Lua54().execute(source)
    rng = random.Random(813)
    for _ in range(500):
        left, right = rng.uniform(-100000, 100000), rng.uniform(-1000, 1000)
        if abs(right) > 0.001:
            assert actual(left, right) == reference(left, right)


@pytest.mark.parametrize("source,expected", [
    ('local n=0; local function a() n=n+1; return 7,999 end; local function b() n=n*10; return 2,999 end; print(a()//b(),n)', ["3\t10"]),
    ('local math={floor=function() error("shadowed") end}; print(7//2)', ["3"]),
    ('math.floor=function() return 999 end; print(7//2)', ["3"]),
    ('local __a3k_floor_div=99; print(7//2,__a3k_floor_div)', ["3\t99"]),
    ('local t=setmetatable({}, {__idiv=function(a,b) return "left" end}); print(t//2)', ["left"]),
    ('local t=setmetatable({}, {__idiv=function(a,b) return "right" end}); print(2//t)', ["right"]),
    ('print("7"//"2")', ["3"]),
])
def test_sandbox_evaluates_once_and_handles_scopes(source, expected):
    result = sandbox.run(source)
    assert result.completed and not result.error
    assert result.prints == expected and result.compatibility


@pytest.mark.parametrize("source", [
    'return 7//2 -- EOF comment',
    'return 7 -- left\n//2',
    'return 7// -- after operator\n2',
    'return 7//--[=[ long comment ]=]2',
    'return 7//2 --[[ trailing long comment ]]',
    'return (7//2)--EOF',
])
def test_comments_are_preserved_and_cannot_hide_generated_parentheses(source):
    lowered, _ = prepare(source)
    assert Lua51().execute(lowered) == Lua54().execute(source) == 3


@pytest.mark.parametrize("source", [
    '-- // is a comment\nprint("https://example.invalid/a//b")',
    'print([=[7//2]=])',
    'local s="7//2"; print(s)',
])
def test_literals_and_comments_are_not_treated_as_operators(source):
    assert prepare(source) == (source, [])


def test_compilation_check_uses_the_same_compatibility_as_execution():
    source = "local a,b=7,2; print(a//b)"
    assert not sandbox.syntax_ok(source, compatible=False)
    assert sandbox.syntax_ok(source)
    assert sandbox.run(source).prints == ["3"]


def test_compatibility_applies_to_the_sandbox_copy_not_the_deliverable():
    source = "-- MoonSec Obfuscator\nlocal a,b=7,2; print(a//b)"
    result = deobfuscate(source)
    assert result.source == source
    assert result.compatibility and any("floor-division" in note for note in result.notes)
    assert result.prints == ["3"]


def test_captured_chunks_also_get_compatibility_when_analyzed_as_a_layer():
    inner = 'local a,b=7,2; print(a//b)'
    result = deobfuscate('loadstring(%s)()' % passes.emit_str(inner))
    assert result.layers == 1 and result.prints == ["3"]
    assert not result.error and result.compatibility


def test_unsupported_syntax_stays_an_explicit_unexecuted_result():
    result = deobfuscate('local x: number=2; print(x//2)')
    assert result.stop_reason == "syntax-error"
    assert result.output_kind == "unexecuted-source"
    assert not result.trace and not result.prints
    assert any("did not execute" in note for note in result.notes)


def test_a_zero_divisor_is_rejected_instead_of_reporting_success():
    result = sandbox.run("print(7//0)")
    assert "floor division by zero" in result.error and not result.prints


def test_excessive_operator_counts_are_declined_without_partial_rewrites():
    source = "print(1000" + "//2" * 257 + ")"
    lowered, notes = prepare(source)
    assert lowered == source
    assert any("operator limit" in note for note in notes)


def test_nested_operator_count_is_reported_accurately():
    lowered, notes = prepare("return 1000//3//2//4")
    assert Lua51().execute(lowered) == 41
    assert any("lowered 3 " in note for note in notes)


def test_discord_worker_preserves_compatibility_metadata():
    from bot.bot import _job

    async def check():
        result = await _job(deobfuscate, 'print(7//2)')
        assert result.prints == ["3"]
        assert result.compatibility and result.to_report()["compatibility"]
        assert not result.error

    asyncio.run(check())


def test_discord_summary_identifies_an_unexecuted_script():
    from bot.bot import _summary

    result = deobfuscate('local x: number=2; print(x)')
    embed = _summary("sample.lua", result)
    assert any(field.name == "execution" and "did not execute" in field.value for field in embed.fields)
