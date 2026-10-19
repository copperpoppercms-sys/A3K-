import json
import subprocess
import sys

import pytest

from deobf import analyze, deobfuscate, detect, passes, sandbox
from deobf import astclean
from deobf.detect import BY_NAME


@pytest.mark.parametrize("source", [
    'return loadstring(string.reverse(")24(tnirp"))()',
    'loadstring(table.concat({"print", "(", "42", ")"}))()',
    'return assert(loadstring(table.concat({"junk", "print(42)"}, "", 2, 2)))()',
    '-- watermark\nreturn loadstring([=[\nprint(42)]=], "@payload")()',
    'loadstring(string.sub("xxprint(42)yy", 3, -3))()',
    'loadstring(string.char(112, 114, 105, 110, 116, 40, 6*8+4, 50, 41))()',
    'return (loadstring("print(" .. table.concat({4, 2}) .. ")"))()',
    'loadstring(string.lower("PRINT(42)"))()',
    'loadstring("print(" .. string.char(((-1)^2)+51) .. "2)")()',
    '\xef\xbb\xbfloadstring("print(42)")()',
])
def test_literal_loaders_recover_without_a_sandbox(source, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("static recovery must not execute the input")
    monkeypatch.setattr(sandbox, "run", forbidden)
    r = deobfuscate(source, dynamic=False)
    assert r.source == "print(42)"
    assert r.layers == 1 and r.output_kind == "unwrapped-source"
    assert r.layer_history[0]["method"] == "literal-loader"


@pytest.mark.parametrize("source", [
    'loadstring(string.rep("x", 999999999))()',
    'loadstring(table.concat({"x"}, "", 0, 1))()',
    'loadstring(custom_decode("abc"))()',
    'loadstring("\\27Lua")()',
    'local loadstring = custom; loadstring("print(42)")()',
    'loadstring("print(42)")(side_effect())',
    'assert(loadstring("print(42)"), side_effect())()',
])
def test_unsafe_or_nonliteral_loaders_are_not_unwrapped(source):
    assert passes.unwrap_static(source) is None


@pytest.mark.parametrize("source,expected", [
    ('local T={"a","b"}; local alias=T; alias[1]="changed"; print(T[1])', ["changed"]),
    ('local T={"a","b"}; local i=1; T[i]="changed"; print(T[1])', ["changed"]),
    ('local T={"a","b"}; do local T={"x","y"}; print(T[1]) end; print(T[1])', ["x", "a"]),
    ('local T={"a"}; local function f(T) print(T[1]) end; f({"x"}); print(T[1])', ["x", "a"]),
    ('local T={"a","b"}; table.remove(T,1); print(T[1])', ["b"]),
    ('local T={"a","b"}; T[1],T[2]="x","y"; print(T[1],T[2])', ["x\ty"]),
])
def test_static_table_folding_preserves_mutation_aliases_and_scopes(source, expected):
    cleaned, _ = passes.run_static(source)
    assert sandbox.run(source).prints == sandbox.run(cleaned).prints == expected


@pytest.mark.parametrize("source,expected", [
    ('local string={char=function() return "custom" end}; print(string.char(65))', ["custom"]),
    ('local function f(string) print(string.char(65)) end; f({char=function() return "param" end})', ["param"]),
    ('local m={string={char=function() return "member" end}}; print(m.string.char(65))', ["member"]),
    ('local m={string={char=function() return "member" end}}; print(m. -- comment\n string.char(65))', ["member"]),
    ('local s=string; s.char=function() return "alias" end; print(string.char(65))', ["alias"]),
])
def test_char_folding_respects_shadowing_members_and_builtin_aliases(source, expected):
    cleaned, _ = passes.run_static(source)
    assert sandbox.run(source).prints == sandbox.run(cleaned).prints == expected


@pytest.mark.parametrize("source", ['print("\\u{")', 'print("\\u{110000}")', 'print("\\u{D800}")', 'print("\\999")'])
def test_malformed_escapes_return_input_instead_of_crashing(source):
    assert deobfuscate(source, dynamic=False).source == source


@pytest.mark.parametrize("name", sorted(BY_NAME))
def test_plain_identifiers_do_not_claim_a_protector(name):
    assert not detect('local %s = "hello"; print(%s)' % (name, name))[0]


def test_specific_namespace_beats_a_compatibility_watermark():
    report = analyze('-- protected using Luraph Obfuscator v14 https://lura.ph/\nlocal AVM_OBFUSCATED=true')
    assert report["engines"][0][0] == "azurevm"
    assert report["ambiguous"]
    assert detect('-- protected using Luraph Obfuscator v14 https://lura.ph/\nlocal AVM_OBFUSCATED=true')[0][0][0] == "azurevm"


def test_layer_limits_return_a_reportable_partial_result():
    source = 'print("final")'
    for _ in range(4):
        source = "loadstring(%s)()" % passes.emit_str(source)
    r = deobfuscate(source, dynamic=False, max_layers=2)
    assert r.layers == len(r.layer_history) == 2
    assert r.stop_reason == "layer-limit"
    assert "loadstring" in r.source


def test_repeated_runtime_layers_stop_instead_of_spinning(monkeypatch):
    def run(source, *args, **kwargs):
        return sandbox.Run(captures=["b()" if source == "a()" else "a()"], completed=True)
    monkeypatch.setattr(sandbox, "run", run)
    r = deobfuscate("a()")
    assert r.layers == 1 and r.stop_reason == "layer-cycle"
    assert r.source == "b()"


def test_outer_layer_findings_are_not_lost():
    source = '-- https://discord.com/api/webhooks/1/abc\nloadstring("print(42)")()'
    r = deobfuscate(source, dynamic=False)
    assert r.source == "print(42)" and r.is_logger


def test_budget_exhaustion_returns_partial_work():
    r = deobfuscate('print("hello")', timeout=1e-9)
    assert r.stop_reason == "time-budget"
    assert any("partial" in note for note in r.notes)


def test_vm_payload_inside_a_literal_wrapper_is_labeled_as_a_trace():
    payload = '-- protected using Luraph Obfuscator v14\ngame:GetService("Players").LocalPlayer.Name="x"'
    r = deobfuscate('loadstring(%s)()' % passes.emit_str(payload))
    assert r.layers == 1 and r.payload_engine == "luraph"
    assert r.shows_recon and r.output_kind == "behavior-trace"
    assert "-- reconstructed observable behaviour" in r.best


def test_failed_workers_cannot_validate_simplification(monkeypatch):
    monkeypatch.setattr(sandbox, "run", lambda *args, **kw: sandbox.Run(error="worker: failed"))
    cleaned, reason = astclean.clean("local x=2; print(x)")
    assert cleaned is None and "incomplete" in reason


def test_worker_kill_is_reported_and_partial_events_survive(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *args, **kw: subprocess.CompletedProcess([], -9, b'{"k":"print","v":"partial"}\n', b""))
    result = sandbox.run("print(1)")
    assert result.prints == ["partial"]
    assert "signal 9" in result.error and not result.completed


def test_missing_worker_response_is_not_a_success(monkeypatch):
    monkeypatch.setattr(sandbox, "_spawn", lambda *args: (b"", False))
    result = sandbox.run("print(1)")
    assert not result.completed and "without a completion" in result.error


def test_decoded_payload_size_is_bounded(monkeypatch):
    monkeypatch.setattr(sandbox, "run", lambda *args, **kw: sandbox.Run(captures=["x" * (2 * 1024 * 1024 + 1)], completed=True))
    result = deobfuscate("decode()")
    assert result.stop_reason == "decoded-size-limit"
    assert result.source == "decode()" and result.layers == 0


def test_excessive_sandbox_output_is_reported():
    result = sandbox.run('local s=string.rep("x", 512*1024); for i=1,30 do print(s) end', seconds=2)
    assert "output limit" in result.error and not result.completed


def test_oversized_input_is_rejected_before_processing():
    with pytest.raises(ValueError, match="2 MB"):
        deobfuscate("x" * (2 * 1024 * 1024 + 1))


@pytest.mark.parametrize("extra", [["--static", "--json"], ["--detect", "--json"]])
def test_cli_json_is_valid_and_does_not_mix_status_with_stdout(tmp_path, extra):
    script = tmp_path / "script.lua"
    script.write_text('loadstring("print(42)")()')
    p = subprocess.run([sys.executable, "-m", "deobf", str(script), *extra], capture_output=True, text=True)
    assert p.returncode == 0
    report = json.loads(p.stdout)
    assert report["score_kind"]
    if "--static" in extra:
        assert report["source"] == "print(42)" and report["layers"] == 1


def test_cli_rejects_contradictory_static_trace_flags(tmp_path):
    script = tmp_path / "script.lua"
    script.write_text("print(1)")
    p = subprocess.run([sys.executable, "-m", "deobf", str(script), "--static", "--trace"], capture_output=True, text=True)
    assert p.returncode == 2 and "cannot be combined" in p.stderr
