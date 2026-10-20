"""IronBrew 2 output must come back as the program it was running, behaving identically."""
import os

import pytest

from deobf import deobfuscate, devirt, luaast, sandbox, deepstack

HERE = os.path.join(os.path.dirname(__file__), "samples")


def load(name):
    with open(os.path.join(HERE, name), "rb") as f:
        return f.read().decode("latin-1")


def behaviour(src):
    run = sandbox.run(src, 15)
    return run.trace, run.prints, run.error


@pytest.mark.parametrize("name", ["ib2_basic.lua", "hard_ib2_enc.lua", "hard_nested_ib2_over_prom.lua"])
def test_lifted_program_behaves_like_the_vm(name):
    src = load(name)
    lifted = deepstack.call(devirt.devirtualize, src, 15)
    assert behaviour(lifted) == behaviour(src)
    assert "Stk" not in lifted and len(lifted) < len(src) * 5


def test_interpreter_loop_is_found():
    vm = devirt.find_vm(luaast.parse(load("ib2_basic.lua")))
    assert vm is not None and vm.wrap_local is not None and len(vm.dispatch) >= 1


def test_plain_scripts_are_left_alone():
    assert not devirt.looks_like_vm(load("src1.lua"))
    with pytest.raises(devirt.DevirtError):
        devirt.devirtualize(load("src1.lua"))


def test_basic_ironbrew_comes_back_structured():
    lifted = devirt.devirtualize(load("ib2_basic.lua"))
    assert "for e_" in lifted or "while true do" in lifted
    assert lifted.count("SPEC[") >= 3  # two protos plus the entry call
    assert "elseif" not in lifted.split("SPEC[2]")[0]  # no dispatch chain in the main function


def test_pipeline_devirtualizes_and_reports_it():
    r = deobfuscate(load("ib2_basic.lua"))
    assert any(h["method"] == "devirtualized" for h in r.layer_history)
    assert any("devirtualized the interpreter loop" in n for n in r.notes)
    assert '"Hello, "' in r.source and '"fib(10) ="' in r.source and "SetCore" in r.source


def test_pipeline_rejects_a_lift_that_changes_behaviour(monkeypatch):
    real = devirt.devirtualize
    monkeypatch.setattr(devirt, "devirtualize", lambda src, seconds=10: real(src, seconds).replace('"Hello, "', '"Bye, "'))
    r = deobfuscate(load("ib2_basic.lua"))
    assert any("behaved differently" in n for n in r.notes)
    assert not any(h["method"] == "devirtualized" for h in r.layer_history)
