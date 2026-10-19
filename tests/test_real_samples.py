import os

import pytest

from deobf import deobfuscate

HERE = os.path.join(os.path.dirname(__file__), "samples")
NOTIFY = 'game:GetService("StarterGui"):SetCore("SendNotification", {Title = "Test", Text = "ok"})'


def load(name):
    with open(os.path.join(HERE, name), "rb") as f:
        return f.read().decode("latin-1")


@pytest.mark.parametrize("name", ["prom_Weak.lua", "prom_Medium.lua", "prom_Strong.lua", "herc_light.lua", "ib2_basic.lua"])
def test_behaviour_recovered(name):
    r = deobfuscate(load(name))
    for line in ('game:GetService("Players")', NOTIFY, 'print("Hello, Player!")', 'print("fib(10) =", 55)'):
        assert line in (r.recon or r.best), (name, line)


@pytest.mark.parametrize("name,engine", [("prom_Weak.lua", "prometheus"), ("prom_Medium.lua", "prometheus"), ("prom_Strong.lua", "prometheus"), ("herc_light.lua", "hercules"), ("herc_heavy.lua", "hercules"), ("ib2_basic.lua", "ironbrew")])
def test_engine_detected(name, engine):
    assert deobfuscate(load(name), dynamic=False).engine == engine


def test_hercules_balanced_decoder_fully_inlined():
    r = deobfuscate(load("herc_balanced.lua"))
    assert "MXYbNQZchcJJ(\"" not in r.source
    assert 'game:GetService("Players")' in r.source and '"fib(10) ="' in r.source


def test_hercules_runtime_error_reported_faithfully():
    r = deobfuscate(load("herc_heavy.lua"))
    assert "attempt to perform arithmetic on global 'i'" in r.error


def test_hercules_web_v162_constants_and_anti_tamper():
    r = deobfuscate(load("herc_web_162_congrats.lua"))
    assert r.engine == "hercules"
    assert 'game:GetService("StarterGui")' in r.recon
    for c in ("SetCore", "SendNotification", "congrats it worked :)!"):
        assert c in r.constants
    assert "Tamper" not in r.recon


def test_hercules_web_v162_bug_patched_so_script_runs():
    r = deobfuscate(load("herc_web_162_congrats.lua"))
    assert r.error == ""
    assert 'game:GetService("StarterGui"):SetCore("SendNotification", {' in r.recon
    assert 'print("congrats it worked :)!")' in r.recon
    assert any("hercules vm operand clobber" in n for n in r.notes)


def test_vm_output_preserves_its_trace_label_and_calls():
    r = deobfuscate(load("herc_web_162_congrats.lua"))
    assert r.output_kind == "behavior-trace"
    assert r.best.splitlines()[1].startswith("-- reconstructed observable behaviour")
    assert [line for line in r.best.splitlines() if not line.startswith("--")] == [
        'game:GetService("StarterGui"):SetCore("SendNotification", {Text = "congrats it worked :)!", Title = "Success", Duration = 5})',
        'print("congrats it worked :)!")',
    ]


def test_header_is_first_line_and_utf8_safe():
    r = deobfuscate(load("herc_web_162_congrats.lua"))
    first = r.best.encode("latin-1").decode("utf-8").splitlines()[0]
    assert first == "-- Deobfuscated by A3K Deobfuscator"


HARD_MARKERS = ['Instance.new("ScreenGui")', "RiftHub", "MouseButton1Click", "SendNotification", "E pressed", '"tick", 3', "after wait inside pcall", "Humanoid.Health = 100"]


@pytest.mark.parametrize("name", ["hard_prom_Strong.lua", "hard_ib2_enc.lua", "hard_herc_heavy.lua", "hard_nested_ib2_over_prom.lua"])
def test_hard_gui_script_fully_recovered(name):
    r = deobfuscate(load(name))
    text = r.recon
    assert r.error == ""
    assert [m for m in HARD_MARKERS if m not in text] == []


def test_hercules_heavy_comes_back_as_clean_source():
    r = deobfuscate(load("herc_heavy.lua"))
    assert any("ast cleanup" in n for n in r.notes)
    assert "Tamper" not in r.source and "BFR" not in r.source
    assert 'local Players = game:GetService("Players")' in r.source
    assert 'game:GetService("StarterGui"):SetCore("SendNotification", {Title = "Test", Text = "ok"})' in r.source
    assert len(r.source) < 1500


def test_hercules_heavy_gui_script_becomes_readable_source():
    r = deobfuscate(load("hard_herc_heavy.lua"))
    for m in ('Instance.new("ScreenGui")', "MouseButton1Click:Connect(function", "task.spawn(function", 'Duration = 3'):
        assert m in r.source, m
    assert len(r.source) < 3000


def test_analyze_reports_reasons_and_techniques():
    from deobf import analyze

    rep = analyze(load("herc_heavy.lua"))
    assert rep["engines"][0][0] == "hercules" and rep["engines"][0][2]
    tags = {t[0] for t in rep["techniques"]}
    assert {"anti-tamper", "control-flow-flattening", "junk-code"} <= tags
    assert analyze(load("src1.lua"))["engines"] == [] and analyze(load("src1.lua"))["score"] < 10


def test_layered_obfuscation_reports_both_layers():
    from deobf import detect

    names = [n for n, _ in detect(load("hard_nested_ib2_over_prom.lua"))[0]]
    assert "ironbrew" in names


def test_plain_script_comes_back_as_its_source():
    r = deobfuscate(load("src1.lua"))
    assert "local Players = game:GetService(\"Players\")" in r.best and not r.shows_recon
