"""Parent side of the sandbox: spawns _worker.py in an isolated, resource-limited subprocess."""
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field

_WORKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_worker.py")
_MEM_BYTES = 768 * 1024 * 1024


@dataclass
class Run:
    captures: list = field(default_factory=list)  # loadstring/load chunks, in order
    trace: list = field(default_factory=list)  # recorded API calls
    urls: list = field(default_factory=list)
    posts: list = field(default_factory=list)
    prints: list = field(default_factory=list)
    error: str = ""
    timed_out: bool = False


def _limits():
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (_MEM_BYTES, _MEM_BYTES))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except Exception:
        pass


def _spawn(job, timeout):
    kw = {"preexec_fn": _limits} if os.name == "posix" else {}
    try:
        p = subprocess.run(
            [sys.executable, "-I", _WORKER], input=json.dumps(job).encode(), capture_output=True,
            timeout=timeout, env={"PATH": os.environ.get("PATH", "")}, **kw,
        )
        return p.stdout, False
    except subprocess.TimeoutExpired as e:
        return e.stdout or b"", True


def _lines(raw):
    for line in raw.decode("utf-8", "replace").splitlines():
        try:
            yield json.loads(line)
        except ValueError:
            continue


def run(source, seconds=10):
    out, timed_out = _spawn({"source": source, "seconds": seconds}, seconds + 8)
    r = Run(timed_out=timed_out)
    for ev in _lines(out):
        k, v = ev.get("k"), ev.get("v", "")
        if k == "capture":
            r.captures.append(v)
        elif k == "trace":
            r.trace.append(v)
        elif k == "url" and v not in r.urls:
            r.urls.append(v)
        elif k == "post":
            r.posts.append(v)
        elif k == "print":
            r.prints.append(v)
        elif k == "error":
            r.error = v
    return r


def decrypt(prefix, name, calls, seconds=15):
    """Evaluate decoder `name` (defined by `prefix`) for each argument tuple; None per failed call."""
    out, _ = _spawn({"mode": "decrypt", "source": prefix, "name": name, "calls": calls, "seconds": seconds}, seconds + 8)
    for ev in _lines(out):
        if ev.get("k") == "decrypt":
            return json.loads(ev["v"])
    return None


def syntax_ok(source):
    """True when Lua 5.1 accepts the chunk (Luau-only syntax reports False)."""
    out, _ = _spawn({"source": source, "mode": "check"}, 10)
    return any(ev.get("k") == "ok" for ev in _lines(out))
