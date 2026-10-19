from pathlib import Path

import pytest

from deobf import analyze, deobfuscate, sandbox


SAMPLES = Path(__file__).parent / "samples" / "additional"


@pytest.mark.parametrize("filename,engine", [
    ("luaseel.lua", "luaseel"),
    ("xfuscator_step2.lua", "xfuscator"),
])
def test_actual_generated_output_recovers_payload_and_behavior(filename, engine):
    src = (SAMPLES / filename).read_text()
    r = deobfuscate(src)
    original = (SAMPLES / "original.lua").read_text()
    assert r.engine == engine and r.layers >= 1 and not r.error
    assert r.source.strip() == original.strip()
    before, after = sandbox.run(original), sandbox.run(r.source)
    assert before.prints == after.prints == ["A3K sample"]
    assert before.trace == after.trace
    assert not before.error and not after.error


def test_luaseel_still_detected_after_watermark_removal():
    src = (SAMPLES / "luaseel.lua").read_text()
    report = analyze("\n".join(src.splitlines()[1:]))
    assert report["engines"][0][0] == "luaseel"
    assert any("helper" in why for why in report["engines"][0][2])
