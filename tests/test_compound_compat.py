import pytest
from lupa.lua51 import LuaRuntime

from deobf import deobfuscate, sandbox
from deobf.compat import prepare


@pytest.mark.parametrize("operator,initial,rhs,expected", [
    ("+=", "7", "2*3", 13),
    ("-=", "7", "2+3", 2),
    ("*=", "7", "2+3", 35),
    ("/=", "7", "2+3", 1.4),
    ("//=", "-7", "2+1", -3),
    ("%=", "7", "2+3", 2),
    ("^=", "2", "2+1", 8),
    ("..=", '"a"', '"b".."c"', "abc"),
])
@pytest.mark.parametrize("target", ["value", "t.value", 't["value"]'])
def test_compound_operators_preserve_expression_precedence(operator, initial, rhs, expected, target):
    source = f'local value={initial}; local t={{value={initial}}}; {target}{operator}{rhs}; return {target}'
    lowered, notes = prepare(source)
    assert LuaRuntime().execute(lowered) == expected
    assert any("compound-assignment" in note for note in notes)


def test_indexed_target_key_and_read_are_evaluated_once_before_rhs():
    source = '''
local log={}; local stored
local t=setmetatable({}, {
    __index=function() log[#log+1]="read"; return 7 end,
    __newindex=function(_,k,v) log[#log+1]="write"; stored=v end
})
local function object() log[#log+1]="object"; return t end
local function key() log[#log+1]="key"; return "value" end
local function rhs() log[#log+1]="rhs"; return 3,999 end
object()[key()] += rhs()
return stored,table.concat(log,",")
'''
    lowered, _ = prepare(source)
    assert LuaRuntime(unpack_returned_tuples=True).execute(lowered) == (10, "object,key,read,rhs,write")


def test_target_is_captured_before_rhs_reassigns_its_variables():
    source = '''local original={4}; local t=original; local key=1
local function rhs() t={999}; key=2; return 3 end
t[key] += rhs()
return original[1], t[1], key'''
    lowered, _ = prepare(source)
    assert LuaRuntime(unpack_returned_tuples=True).execute(lowered) == (7, 999, 2)


@pytest.mark.parametrize("statement", [
    'n+=2',
    't["n"]+=2',
    't.n+=2',
    'n -- lhs\n += -- op\n 2',
    't -- obj\n [-- key\n "n"] -- lhs\n += -- op\n 2',
    't. -- key\n n -- lhs\n += -- op\n 2',
])
def test_comments_and_original_line_numbers_are_preserved(statement):
    source = 'local n=7; local t={n=7}; ' + statement + '; print(n,t.n)'
    lowered, _ = prepare(source)
    assert source.count('\n') == lowered.count('\n')
    actual = sandbox.run(source)
    assert actual.completed and not actual.error
    assert actual.prints == (["9\t7"] if statement.startswith('n') else ["7\t9"])


def test_eof_comment_cannot_swallow_the_generated_assignment():
    lowered, _ = prepare('local t={n=7}; t.n += 2 -- EOF')
    assert LuaRuntime().execute(lowered) is None
    assert sandbox.run('local n=1; n += 1 -- EOF').error == ""


def test_nested_compound_statements_and_floor_expressions_are_lowered_together():
    source = '''local t={9}; local count=0
(function() count += 1; return t end)()[3//2] //= (function() count += 1; return 7//2 end)()
return t[1],count'''
    lowered, notes = prepare(source)
    assert LuaRuntime(unpack_returned_tuples=True).execute(lowered) == (3, 2)
    assert any("lowered 3 compound-assignment" in note for note in notes)
    assert any("lowered 2 floor-division" in note for note in notes)


def test_fresh_temporary_names_do_not_shadow_input_locals():
    source = '''local __a3k_object={n=2}; local __a3k_key="n"; local __a3k_old=3
__a3k_object[__a3k_key] += __a3k_old
return __a3k_object.n'''
    lowered, _ = prepare(source)
    assert LuaRuntime().execute(lowered) == 5


@pytest.mark.parametrize("source", [
    'print("a+=1", [=[t.n ..= "x"]=]) -- n //= 2',
    '-- += -= *= /= %= ^= ..= //=\nprint(1)',
])
def test_compound_like_text_in_strings_and_comments_is_untouched(source):
    assert prepare(source) == (source, [])


def test_operator_limit_declines_the_whole_translation():
    source = 'local n=0;' + 'n+=1;' * 257
    lowered, notes = prepare(source)
    assert lowered == source
    assert any('operator limit' in note for note in notes)


def test_sequence_keypoint_constructors_record_without_real_roblox_access():
    source = '''local n=0; n+=1
local a=NumberSequence.new({NumberSequenceKeypoint.new(0,n),NumberSequenceKeypoint.new(1,0)})
local b=ColorSequence.new({ColorSequenceKeypoint.new(0,Color3.fromRGB(1,2,3))})
print(n)'''
    result = sandbox.run(source)
    assert result.completed and not result.error and result.prints == ['1']
    assert any('NumberSequenceKeypoint.new(0, 1)' in line for line in result.trace)
    assert any('ColorSequenceKeypoint.new(' in line for line in result.trace)


def test_minified_line_sensitive_source_can_now_produce_a_trace():
    source = '-- WeAreDevs https://wearedevs.net/obfuscator\n' + (
        'local pad="%s"; local env=getfenv(); local n=1; n+=1; '
        'local function fail() error("probe") end; '
        'local ok,msg=pcall(fail); assert(msg:match(":2:"),"line numbers changed"); '
        'local g=Instance.new("UIGradient"); '
        'g.Transparency=NumberSequence.new({NumberSequenceKeypoint.new(0,n)}); print(n)'
    ) % ('x' * 2400)
    result = deobfuscate(source, seconds=2)
    assert not result.error
    assert result.source == source
    assert result.output_kind == 'behavior-trace'
    assert result.recovery_status == 'behavior-only'
    assert 'NumberSequenceKeypoint.new(0, 2)' in result.best
    assert 'partial observations, not original source' in result.best
