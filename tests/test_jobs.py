import asyncio
import os
from pathlib import Path

import pytest

from bot.jobs import JobError, ProcessJobs, QueueFull


def test_queue_capacity_cancellation_and_reuse():
    async def check():
        jobs = ProcessJobs(workers=1, max_pending=1)
        started, finish = asyncio.Event(), asyncio.Event()

        async def execute(*args):
            started.set()
            await finish.wait()
            return "done"

        jobs._execute = execute
        first = asyncio.create_task(jobs.run("analyze", "first"))
        await asyncio.wait_for(started.wait(), 1)
        second = asyncio.create_task(jobs.run("analyze", "second"))
        await asyncio.sleep(0)
        assert (jobs.active, jobs.waiting) == (1, 1)
        with pytest.raises(QueueFull):
            await jobs.run("analyze", "overflow")
        second.cancel()
        with pytest.raises(asyncio.CancelledError):
            await second
        assert jobs.waiting == 0
        finish.set()
        assert await first == "done"
        assert jobs.active == jobs.waiting == jobs._admitted == 0
        assert await jobs.run("analyze", "again") == "done"

    asyncio.run(check())


def test_real_worker_response_and_error_isolation():
    async def check():
        jobs = ProcessJobs(timeout=10)
        report = await jobs.run("deobfuscate", 'loadstring("print(42)")()', dynamic=False)
        assert report["source"] == "print(42)" and report["layers"] == 1
        with pytest.raises(JobError, match="ValueError"):
            await jobs.run("deobfuscate", "print(1)", seconds=99)
        assert (await jobs.run("analyze", 'print("fine")'))["engines"] == []
        assert jobs.active == jobs.waiting == 0

    asyncio.run(check())


def test_discord_job_round_trip_on_a_real_vm_sample():
    from bot.bot import _job
    from deobf import deobfuscate

    async def check():
        source = (Path(__file__).parent / "samples" / "hard_prom_Strong.lua").read_text()
        result = await _job(deobfuscate, source)
        assert result.output_kind == "behavior-trace"
        assert "MouseButton1Click" in result.best
        assert "after wait inside pcall" in result.best
        assert result.to_report()["elapsed_seconds"] > 0

    asyncio.run(check())


def test_hard_job_timeout_kills_worker_and_recovers_queue(monkeypatch):
    async def check():
        jobs = ProcessJobs(timeout=0.15)
        killed = []
        original = jobs._kill

        def kill(proc):
            killed.append(proc.pid)
            original(proc)

        monkeypatch.setattr(jobs, "_kill", kill)
        with pytest.raises(JobError, match="time limit"):
            await jobs.run("trace", "while true do end")
        assert killed and jobs.active == jobs.waiting == 0
        if os.name == "posix":
            with pytest.raises(ProcessLookupError):
                os.kill(killed[0], 0)
        jobs.timeout = 10
        assert (await jobs.run("analyze", "print(1)"))["engines"] == []

    asyncio.run(check())


def test_bot_token_is_not_inherited_by_analysis_workers(monkeypatch):
    async def check():
        monkeypatch.setenv("DISCORD_TOKEN", "private-test-canary")
        original = asyncio.create_subprocess_exec
        environments = []

        async def spawn(*args, **kwargs):
            environments.append(kwargs["env"])
            return await original(*args, **kwargs)

        monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
        await ProcessJobs(timeout=10).run("analyze", "print(1)")
        assert environments and all("DISCORD_TOKEN" not in env for env in environments)

    asyncio.run(check())


def test_batch_keeps_successful_files_when_one_fails(monkeypatch):
    from bot import bot as front

    async def fake(fn, source, **options):
        if source == "bad":
            raise JobError("bad input")
        return "success"

    monkeypatch.setattr(front, "_job", fake)

    async def check():
        results = [row async for row in front._batch([("bad.lua", "bad"), ("good.lua", "good")], lambda: None)]
        by_name = {name: result for name, _, result in results}
        assert isinstance(by_name["bad.lua"], JobError)
        assert by_name["good.lua"] == "success"

    asyncio.run(check())


def test_cancelling_an_active_job_releases_its_process_and_slot(monkeypatch):
    async def check():
        jobs = ProcessJobs(timeout=10)
        original = asyncio.create_subprocess_exec
        spawned = asyncio.Event()
        processes = []

        async def spawn(*args, **kwargs):
            proc = await original(*args, **kwargs)
            processes.append(proc)
            spawned.set()
            return proc

        monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
        task = asyncio.create_task(jobs.run("trace", "while true do end"))
        await asyncio.wait_for(spawned.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert processes[0].returncode is not None
        assert jobs.active == jobs.waiting == jobs._admitted == 0

    asyncio.run(check())
