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


def test_best_is_script_only_for_vm_output():
    r = deobfuscate(load("herc_web_162_congrats.lua"))
    assert r.best.splitlines()[1:] == [
        'game:GetService("StarterGui")',
        'game:GetService("StarterGui"):SetCore("SendNotification", {Text = "congrats it worked :)!", Title = "Success", Duration = 5})',
        'print("congrats it worked :)!")',
    ]


def test_header_is_first_line_and_utf8_safe():
    r = deobfuscate(load("herc_web_162_congrats.lua"))
    first = r.best.encode("latin-1").decode("utf-8").splitlines()[0]
    assert first == "-- Deobfused By Rift \U0001F595 (Get better \U0001F62D\U0001FAF5)"
