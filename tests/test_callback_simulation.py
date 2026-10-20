import json

from deobf import deobfuscate, recon, sandbox, trace_behavior


def test_unrelated_callbacks_replay_with_fresh_closure_state():
    source = '''local closed=false; local close=Instance.new("TextButton"); local copy=Instance.new("TextButton")
close.MouseButton1Click:Connect(function() closed=true end)
copy.MouseButton1Click:Connect(function() if not closed then setclipboard("https://example.com/invite") end end)'''
    result = sandbox.run(source, seconds=2)
    assert not result.error and not result.task_errors
    assert len(result.callback_runs) == 4
    assert result.trace.count('setclipboard("https://example.com/invite")') == 2
    assert any('fresh runtime' in assumption for assumption in result.assumptions)


def test_frame_delta_and_mouse_vectors_have_real_numeric_components():
    source = '''local input=game:GetService("UserInputService")
game:GetService("RunService").RenderStepped:Connect(function(dt)
 assert(type(dt)=="number")
 local delta=(input:GetMouseLocation()-Vector2.new(4,6))/2
 assert(typeof(delta)=="Vector2" and delta.X==-2 and delta.Y==-3)
 print("frame ok")
end)'''
    result = sandbox.run(source, seconds=2)
    assert not result.error and not result.task_errors
    assert result.prints == ['frame ok', 'frame ok']
    assert any('delta time' in assumption for assumption in result.assumptions)


def test_vector_math_udim_fields_and_immutable_value_types():
    source = '''local a=Vector3.new(1,2,3)+Vector3.new(3,2,1)
local b=2*a-Vector3.one
local p=UDim2.new(0.5,12,0.25,8)
assert(a.X==4 and b.Z==7 and p.X.Scale==0.5 and p.Y.Offset==8)
assert(Vector2.new(1,0):Dot(Vector2.new(3,4))==3)
local c=Vector2.zero:Lerp(Vector2.new(4,8),0.5)
assert(c.X==2 and c.Y==4)
local ok=pcall(function() a.X=9 end); assert(not ok)
assert(Vector3.one~=Vector3.zero)
assert(Enum.KeyCode.E~=Enum.KeyCode.Q)
print("values ok")'''
    result = sandbox.run(source)
    assert not result.error and result.prints == ['values ok']


def test_input_objects_and_processed_variants_are_explicit():
    source = '''game:GetService("UserInputService").InputBegan:Connect(function(input,processed)
 assert(typeof(input.Position)=="Vector3")
 assert(input.UserInputType==Enum.UserInputType.MouseButton1)
 if not processed and input.KeyCode==Enum.KeyCode.E then print("E pressed") end
end)'''
    result = sandbox.run(source, seconds=2)
    assert not result.error and not result.task_errors
    assert result.prints == ['E pressed']
    assert len(result.callback_runs) == 2
    assert all(item['arguments'] for item in result.callback_runs)
    assert any('synthetic InputObject' in assumption for assumption in result.assumptions)


def test_nested_callbacks_replay_the_parent_registration_path():
    source = '''local b=Instance.new("TextButton")
b.MouseButton1Click:Connect(function()
 local value="nested"
 b.MouseEnter:Connect(function() print(value) end)
end)'''
    result = sandbox.run(source, seconds=2)
    assert not result.error and not result.task_errors and not result.simulation_limits
    assert any(item['started'] and len(item['path']) == 2 for item in result.callback_runs)
    assert 'nested' in result.prints


def test_disconnected_callbacks_are_not_forced_to_run():
    result = sandbox.run('''local b=Instance.new("TextButton")
local c=b.MouseButton1Click:Connect(function() error("disconnected") end)
assert(c.Connected); c:Disconnect(); assert(not c.Connected)''', seconds=2)
    assert not result.error and not result.task_errors
    assert not result.callback_runs


def test_callback_failures_are_structured_and_do_not_disappear():
    result = deobfuscate('''-- Luraph Obfuscator v14
local b=Instance.new("TextButton")
b.MouseButton1Click:Connect(function() error("intentional callback failure") end)''', seconds=2)
    assert result.error == ''
    assert result.stop_reason == 'simulation-errors'
    assert len(result.callback_errors) == 2
    report = result.to_report()
    assert len(report['callback_errors']) == len(report['task_errors']) == 2
    assert all(item['status'] == 'errors' for item in report['callback_runs'])
    assert 'intentional callback failure' in result.best
    from bot.bot import _summary
    assert any(field.name == 'simulation incomplete' for field in _summary('sample.lua', result).fields)


def test_startup_task_errors_are_distinct_from_callback_errors():
    result = sandbox.run('task.spawn(function() error("background failure") end)')
    assert len(result.task_errors) == 1 and not result.callback_errors
    assert result.task_errors[0]['phase'] == 'startup'


def test_failed_replay_startup_cannot_produce_callback_observations():
    result = sandbox.run('''local b=Instance.new("TextButton")
b.MouseButton1Click:Connect(function() print("must not be observed") end)
error("startup failure")''', seconds=2)
    assert 'startup failure' in result.error
    assert 'must not be observed' not in result.prints
    assert result.simulation_limits
    assert all(not item['started'] for item in result.callback_runs)


def test_callback_chunks_do_not_replace_the_entry_script():
    source = '''-- Luraph Obfuscator v14
local b=Instance.new("TextButton")
b.MouseButton1Click:Connect(function() loadstring('print("branch")')() end)'''
    result = deobfuscate(source, seconds=2)
    assert result.source == source and result.layers == 0
    assert result.output_kind == 'behavior-trace'
    assert len(result.callback_captures) == 2
    assert all(item['source'] == 'print("branch")' for item in result.callback_captures)
    assert result.prints == ['branch','branch']


def test_callback_exploration_limits_are_reported():
    result = sandbox.run('''local b=Instance.new("TextButton")
for i=1,30 do b.MouseButton1Click:Connect(function() print(i) end) end''', seconds=3)
    assert len(result.callback_runs) == 48
    assert result.simulation_limits and not result.error
    assert all(item['status'] == 'observed' for item in result.callback_runs)


def test_trace_contains_no_invented_function_bodies_or_executable_lines():
    source = '''local b=Instance.new("TextButton")
b.MouseButton1Click:Connect(function() print("event") end)
b["field\\nprint('injected')"]=1'''
    text, result = trace_behavior(source, seconds=2)
    assert all(line.startswith('--') for line in text.splitlines())
    assert 'DIAGNOSTIC ONLY' in text
    assert 'function(...) end' not in text
    assert 'callback registered:' in text and 'body unavailable' in text
    assert 'event' in result.prints
    assert sandbox.syntax_ok(text)  # Compiles as comments, never as replay code.


def test_trace_formatting_does_not_alias_or_remove_effectful_calls():
    lines = [
        'TweenService:Create(v1, TweenInfo.new(1), {Rotation = 0})',
        'TweenService:Create(v1, TweenInfo.new(1), {Rotation = 0}):Play()',
        'TweenService:Create(v1, TweenInfo.new(1), {Rotation = 0}):Play()',
        '-- callback: v1.MouseEnter',
        'request({Url = "https://example.com/"})',
        '-- callback: v1.MouseEnter [second arg false]',
        'request({Url = "https://example.com/"})',
    ]
    assert recon.tidy(lines) == lines


def test_void_api_returns_are_not_recorded_as_usable_values():
    result = sandbox.run('''local t=game:GetService("TweenService"):Create(Instance.new("Frame"),TweenInfo.new(1),{})
assert(t:Play()==nil); assert(t:Cancel()==nil); print("void ok")''')
    assert not result.error and result.prints == ['void ok']


def test_real_job_preserves_simulation_metadata():
    import asyncio
    from bot.bot import _job

    async def check():
        result = await _job(deobfuscate, '''-- Luraph Obfuscator v14
local b=Instance.new("TextButton"); b.MouseButton1Click:Connect(function() print("click") end)''')
        report = json.loads(json.dumps(result.to_report()))
        assert len(report['callback_runs']) == 2 and report['assumptions']
        assert result.recovery_status == 'behavior-only'
        assert all(line.startswith('--') for line in result.best.splitlines())
    asyncio.run(check())
