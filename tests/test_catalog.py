"""Detector fixtures exercise markers, not claims of full VM recovery."""
import asyncio
from unittest.mock import AsyncMock

import pytest

from deobf import HEADER, analyze, deobfuscate, detect
from deobf.catalog import EXTRA_ENGINES
from deobf.detect import BY_NAME, ENGINES


# Observed markers where available; explicit protection markers for profiles
# documented as marker-only. These are synthetic minimal examples, not VM output.
MARKERS = {
    "luarmor": 'loadstring(game:HttpGet("https://api.luarmor.net/files/v3/loaders/abc.lua"))()',
    "psu": '-- This file was obfuscated using PSU Obfuscator 4.0.A | https://www.psu.dev/',
    "synapsexen": '--[[ Synapse Xen v1.1.2 by Synapse GP ]]',
    "securelua": '-- Protected using Synapse SecureLua',
    "aztupbrew": '--[[ AztupBrew(Fork of IronBrew2): obfuscation; Version 2.7.2 ]]',
    "xfuscator": '-- Obfuscated with XFuscator',
    "luaseel": '--// Obfuscated with LuaSeel 1.1',
    "luaxen": '-- Protected using LuaXen',
    "nodet": '--= This script was obfuscated with Nodet Obfuscator v1.0.0 =--',
    "essafuscator": '-- Protected using EssaFuscator',
    "zartha": 'return (("File generated using [Zartha]") and (function() end)())',
    "opiens": '-- Protected using Opiens',
    "muna": '-- Protected using Muna',
    "rbxluaobfuscator": '-- Obfuscated using RBXLuaObfuscator',
    "bluesecure": '--Protected by blueSecure(1.3)',
    "luacry": '-- Encrypted using luacry',
    "bytelua": '-- Obfuscated using ByteLuaObfuscator',
    "zenfus": '-- Obfuscated using Zenfus',
    "fusgato": '--[[ IronBrew:tm: obfuscation; Version 2.7.0 ]]\nlocal FusGato = "discord.gg/cWQAy6Z697";',
    "shittyobfuscator": '-- Obfuscated using ShittyObfuscator',
    "luarh": '-- Encrypted with LuaRH',
    "joker": '-- Joker Obfuscator',
    "azurevm": '-- AzureVM protected runtime v1.0\nlocal AVM_OBFUSCATED = true',
    "zero": '-- Protected using Zero Obfuscator',
    "novela": '-- Protected using Novela',
}


def test_catalog_adds_25_distinct_profiles():
    assert len(EXTRA_ENGINES) == 25
    assert set(MARKERS) == {e[0] for e in EXTRA_ENGINES}
    assert len(ENGINES) == len(BY_NAME) == 39


@pytest.mark.parametrize("name,marker", MARKERS.items())
def test_detection_reports_evidence_and_is_case_insensitive(name, marker):
    for text in (marker, marker.swapcase()):
        ranked, score = detect(text)
        report = analyze(text)
        assert ranked[0][0] == name
        assert report["engines"][0][:2] == ranked[0]
        assert report["engines"][0][2]
        assert 0 <= score <= 100


@pytest.mark.parametrize("name", MARKERS)
def test_new_names_are_not_matched_in_ordinary_identifiers(name):
    assert name not in dict(detect('local %s = "hello"; print(%s)' % (name, name))[0])


@pytest.mark.parametrize("name", MARKERS)
def test_all_profiles_can_attempt_static_layer_recovery(name):
    r = deobfuscate('return loadstring("print(42)")()', engine=name, dynamic=False)
    assert r.engine == name
    assert r.layers == 1 and r.source == "print(42)"
    assert r.best.startswith(HEADER + "\n")


def test_unknown_input_does_not_invent_a_producer():
    assert not analyze('print("hello world")')["engines"]


def test_discord_commands_and_picker_cover_every_engine():
    from bot.bot import ResultView, bot

    async def check():
        r = deobfuscate('print(42)', dynamic=False)
        view = ResultView("sample.lua", "print(42)", r)
        selects = [child for child in view.children if hasattr(child, "options")]
        assert len(selects) == 2
        assert all(1 <= len(s.options) <= 25 for s in selects)
        assert {o.value for s in selects for o in s.options} == set(BY_NAME)
        assert all(bot.get_command(name) is not None for name in BY_NAME)
        # A logger-removal button must still fit alongside both selects.
        r.findings = deobfuscate('request({Url="https://discord.com/api/webhooks/1/abc",Method="POST"})', dynamic=False).findings
        assert len(ResultView("sample.lua", "", r).children) == 3

    asyncio.run(check())


def test_discord_help_and_engine_list_fit_message_limit():
    from bot.bot import engines_cmd, help_cmd

    async def check():
        for command in (engines_cmd, help_cmd):
            ctx = AsyncMock()
            await command.callback(ctx)
            messages = [call.args[0] for call in ctx.reply.call_args_list]
            assert all(0 < len(m) <= 1900 for m in messages)
            joined = "".join(messages)
            assert "A3K Deobfuscator" in joined
            assert all("`.%s`" % name in joined for name in BY_NAME)

    asyncio.run(check())
