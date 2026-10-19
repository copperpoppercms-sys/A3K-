import asyncio
import importlib
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from deobf import Result


front = importlib.import_module("bot.bot")


@asynccontextmanager
async def typing():
    yield


def context(attachments=()):
    return SimpleNamespace(
        message=SimpleNamespace(attachments=list(attachments), reference=None),
        typing=typing,
        reply=AsyncMock(),
    )


@pytest.mark.parametrize("input_kind", ["url", "attachment"])
def test_keydeobf_fetches_per_job_and_returns_source_and_report(monkeypatch, input_kind):
    monkeypatch.setattr(front, "FETCH", False)
    source = 'loadstring(game:HttpGet("https://example.com/payload.lua"))()'
    result = Result(source=source, unresolved_urls=["https://example.com/payload.lua"])
    worker = AsyncMock(return_value=result)
    monkeypatch.setattr(front, "_job", worker)
    fetch = Mock(return_value=source)
    monkeypatch.setattr(front, "fetch", fetch)
    if input_kind == "url":
        ctx, arg = context(), "https://example.com/loader.lua"
        filename = "fetched.lua"
    else:
        attachment = SimpleNamespace(filename="sample.lua", size=len(source), read=AsyncMock(return_value=source.encode()))
        ctx, arg = context([attachment]), ""
        filename = "sample.lua"

    asyncio.run(front.keydeobf_cmd.callback(ctx, arg=arg))

    worker.assert_awaited_once_with(front.deobfuscate, source, engine=None, fetch=True, dynamic=True)
    if input_kind == "url":
        fetch.assert_called_once_with(arg)
    else:
        fetch.assert_not_called()
    response = ctx.reply.call_args.kwargs
    files = response["files"]
    assert [f.filename for f in files] == ["deobf_" + filename, "report_" + filename + ".json"]
    assert files[0].fp.read().decode() == result.best
    report = json.loads(files[1].fp.read())
    assert report["output_kind"] == "partial-source"
    assert report["unresolved_urls"] == result.unresolved_urls
    assert all(picker.follow for picker in response["view"].children)
    assert front.FETCH is False
    for file in files:
        file.close()
    response["view"].stop()


@pytest.mark.parametrize("fetch_enabled", [False, True])
def test_deobf_preserves_global_fetch_default(monkeypatch, fetch_enabled):
    monkeypatch.setattr(front, "FETCH", fetch_enabled)
    worker = AsyncMock(return_value=Result(source="print(42)"))
    monkeypatch.setattr(front, "_job", worker)
    ctx = context()
    asyncio.run(front.deobf_cmd.callback(ctx, arg="print(42)"))
    assert worker.await_args.kwargs["fetch"] is fetch_enabled
    response = ctx.reply.call_args.kwargs
    assert len(response["files"]) == 1
    assert all(picker.follow is fetch_enabled for picker in response["view"].children)
    response["files"][0].close()
    response["view"].stop()


def test_keydeobf_picker_retains_public_fetching(monkeypatch):
    monkeypatch.setattr(front, "FETCH", False)
    worker = AsyncMock(return_value=Result(source="print(42)"))
    monkeypatch.setattr(front, "_job", worker)
    interaction = SimpleNamespace(response=SimpleNamespace(defer=AsyncMock()), followup=SimpleNamespace(send=AsyncMock()))

    async def rerun():
        view = front.ResultView("sample.lua", "decode()", Result(source="decode()"), follow=True)
        picker = view.children[0]
        picker._values = [picker.options[0].value]
        await picker.callback(interaction)
        view.stop()
        return picker.values[0]

    engine = asyncio.run(rerun())
    worker.assert_awaited_once_with(front.deobfuscate, "decode()", engine=engine, fetch=True)
    interaction.followup.send.call_args.kwargs["file"].close()


def test_keydeobf_failed_file_does_not_discard_success(monkeypatch):
    monkeypatch.setattr(front, "gather_inputs", AsyncMock(return_value=[("bad.lua", "bad()"), ("good.lua", "print(42)")]))
    monkeypatch.setattr(front, "_job", AsyncMock(side_effect=[ValueError("invalid sample"), Result(source="print(42)")]))
    ctx = context()
    asyncio.run(front.keydeobf_cmd.callback(ctx))
    assert ctx.reply.await_count == 2
    failures = [call for call in ctx.reply.call_args_list if call.args]
    successes = [call for call in ctx.reply.call_args_list if "files" in call.kwargs]
    assert "bad.lua" in failures[0].args[0]
    assert len(successes[0].kwargs["files"]) == 2
    for file in successes[0].kwargs["files"]:
        file.close()
    successes[0].kwargs["view"].stop()
