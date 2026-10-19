"""Bounded queue, cancellable subprocess jobs, and live queue statistics."""
import asyncio
import json
import os
import signal
import sys


class JobError(Exception):
    pass


class QueueFull(JobError):
    pass


class ProcessJobs:
    def __init__(self, workers=1, max_pending=8, timeout=75):
        if workers < 1 or max_pending < 0 or timeout <= 0:
            raise ValueError("job configuration must be positive (queue size may be zero)")
        self.limit, self.max_pending, self.timeout = workers, max_pending, timeout
        self.active = self.waiting = 0
        self._admitted = 0
        self._slots = asyncio.Semaphore(workers)

    async def run(self, action, source, **options):
        if len(source) > 2 * 1024 * 1024:
            raise JobError("script exceeds 2 MB input limit")
        if self._admitted >= self.limit + self.max_pending:
            raise QueueFull("The queue is full. Try again after a job finishes.")
        self._admitted += 1
        self.waiting += 1
        acquired = False
        try:
            await self._slots.acquire()
            acquired = True
            self.waiting -= 1
            self.active += 1
            return await self._execute(action, source, options)
        finally:
            self._admitted -= 1
            if acquired:
                self.active -= 1
                self._slots.release()
            else:
                self.waiting -= 1

    async def _execute(self, action, source, options):
        # Never inherit Discord tokens, cloud credentials, or .env variables.
        env = {key: os.environ[key] for key in (
            "PATH", "SYSTEMROOT", "JOB_MEMORY_MB", "SANDBOX_MEMORY_MB",
            "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "all_proxy", "no_proxy",
            "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE",
        ) if key in os.environ}
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "bot.worker", stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                env=env, start_new_session=os.name == "posix",
            )
            data = json.dumps({"action": action, "source": source, "options": options}, ensure_ascii=True).encode()
            communication = asyncio.create_task(proc.communicate(data))
            try:
                stdout, _ = await asyncio.wait_for(asyncio.shield(communication), self.timeout)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._kill(proc)
                await communication  # Reap the worker and its sandbox descendants.
                raise
            if proc.returncode:
                reason = "memory/resource limit" if proc.returncode < 0 else "worker failure"
                raise JobError("Analysis stopped (%s). Try .static or a smaller script." % reason)
            try:
                message = json.loads(stdout)
            except (ValueError, UnicodeError):
                raise JobError("The analysis worker returned an incomplete response.") from None
            if not message.get("ok"):
                raise JobError(message.get("error", "Analysis failed"))
            return message["result"]
        except asyncio.TimeoutError:
            raise JobError("Analysis exceeded the job time limit. Try .static or a smaller script.") from None
        except OSError as e:
            raise JobError("Could not start the analysis worker: " + type(e).__name__) from e
        finally:
            if proc is not None:
                # Kill any descendants left behind even after a normal wrapper exit.
                if os.name == "posix":
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                elif proc.returncode is None:
                    proc.kill()
                if proc.returncode is None:
                    await proc.wait()

    @staticmethod
    def _kill(proc):
        try:
            if os.name == "posix":
                os.killpg(proc.pid, signal.SIGKILL)
            elif proc.returncode is None:
                proc.kill()
        except ProcessLookupError:
            pass
