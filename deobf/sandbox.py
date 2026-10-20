"""Parent side of the sandbox: spawns _worker.py in an isolated, resource-limited subprocess."""
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field

from .budget import remaining
from .compat import prepare

_WORKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_worker.py")


@dataclass
class Run:
    captures: list = field(default_factory=list)  # loadstring/load chunks, in order
    trace: list = field(default_factory=list)  # recorded API calls
    urls: list = field(default_factory=list)
    gets: list = field(default_factory=list)  # urls fetched through HttpGet / GET request()
    posts: list = field(default_factory=list)
    prints: list = field(default_factory=list)
    dec: list = field(default_factory=list)  # (name, args, result) recorded from instrumented decoders
    constants: list = field(default_factory=list)  # decoded strings built via char/concat/gsub/format
    error: str = ""
    timed_out: bool = False
    completed: bool = False
    unresolved_gets: list = field(default_factory=list)
    compatibility: list = field(default_factory=list)
    assumptions: list = field(default_factory=list)
    callback_runs: list = field(default_factory=list)
    callback_captures: list = field(default_factory=list)
    task_errors: list = field(default_factory=list)
    simulation_limits: list = field(default_factory=list)

    @property
    def callback_errors(self):
        return [item for item in self.task_errors if item.get("phase") == "callback"]


def _spawn(job, timeout):
    try:
        p = subprocess.run(
            [sys.executable, "-I", _WORKER], input=json.dumps(job).encode(), capture_output=True,
            timeout=timeout, env={"PATH": os.environ.get("PATH", "")},
        )
        out = p.stdout
        if p.returncode:
            reason = "signal %d (possibly memory limit)" % -p.returncode if p.returncode < 0 else "exit code %d" % p.returncode
            out += (json.dumps({"k": "error", "v": "sandbox worker stopped: " + reason}) + "\n").encode()
        return out, False
    except subprocess.TimeoutExpired as e:
        return e.stdout or b"", True
    except OSError as e:
        return (json.dumps({"k": "error", "v": "sandbox worker could not start: " + type(e).__name__}) + "\n").encode(), False


def _lines(raw):
    for line in raw.decode("utf-8", "replace").splitlines():
        try:
            yield json.loads(line)
        except ValueError:
            continue


def _read_enc(buf, pos):
    colon = buf.index(":", pos)
    n = int(buf[pos:colon])
    return buf[colon + 1:colon + 1 + n], colon + 1 + n


def _parse_dec(payload):
    try:
        name, p = _read_enc(payload, 0)
        blob, p = _read_enc(payload, p)
        result, _ = _read_enc(payload, p)
        args, q = [], 0
        while q < len(blob):
            kind = blob[q]
            val, q = _read_enc(blob, q + 1)
            args.append(("s", val) if kind == "s" else ("n", float(val)))
        return name, tuple(args), result
    except (ValueError, IndexError):
        return None


def run(source, seconds=10, queries=None, http=None):
    """queries: [(decoder_name, [("s"|"n", value), ...])] evaluated against instrumented decoders after the script ends."""
    seconds = remaining(seconds)
    if seconds <= 0:
        return Run(error="analysis time budget exhausted", timed_out=True)
    source, compatibility = prepare(source)
    try:
        memory_mb = max(64, min(2048, int(os.environ.get("SANDBOX_MEMORY_MB", "768"))))
    except ValueError:
        memory_mb = 768
    job = {"source": source, "seconds": seconds, "query_seconds": min(10, seconds), "memory_mb": memory_mb,
           "queries": [[n, [list(a) for a in args]] for n, args in queries or []], "http": http or {}}
    out, timed_out = _spawn(job, remaining(seconds + (min(10, seconds) if queries else 0) + 3))
    r = Run(timed_out=timed_out, compatibility=compatibility)
    for ev in _lines(out):
        k, v = ev.get("k"), ev.get("v", "")
        if k == "capture":
            r.captures.append(v)
        elif k == "trace":
            r.trace.append(v)
        elif k == "url" and v not in r.urls:
            r.urls.append(v)
        elif k == "get":
            if v not in r.gets:
                r.gets.append(v)
        elif k == "post":
            r.posts.append(v)
        elif k == "dec":
            d = _parse_dec(v)
            if d:
                r.dec.append(d)
        elif k == "qres":
            name, args, res = json.loads(v)
            r.dec.append((name, tuple((a[0], float(a[1]) if a[0] == "n" else a[1]) for a in args), res))
        elif k == "const":
            r.constants.append(v)
        elif k == "print":
            r.prints.append(v)
        elif k == "error":
            r.error = v
        elif k == "done":
            r.completed = True
        elif k == "callback_run":
            r.callback_runs.append(v)
        elif k == "callback_capture":
            r.callback_captures.append(v)
        elif k == "task_error":
            r.task_errors.append(v)
        elif k == "assumption" and v not in r.assumptions:
            r.assumptions.append(v)
        elif k == "simulation_limit" and v not in r.simulation_limits:
            r.simulation_limits.append(v)
    if timed_out:
        r.error = r.error or "sandbox: wall-clock limit"
    elif not r.completed and not r.error:
        r.error = "sandbox worker exited without a completion event"
    r.unresolved_gets = [url for url in r.gets if not (http or {}).get(url)]
    r.constants = _clean_constants(r.constants)
    return r


_ERR_RE = re.compile(r"^(?:obf|\[string)[^\n]{0,40}?:\d+:")
_RANDOM_NAME_RE = re.compile(r"^(?=[A-Za-z0-9]*\d)(?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]{6,20}$")


_STD_NAMES = frozenset("""
assert collectgarbage dofile error getfenv getmetatable ipairs load loadfile loadstring next pairs pcall print rawequal rawget rawset
select setfenv setmetatable tonumber tostring type unpack xpcall coroutine create resume yield status wrap running isyieldable
math abs acos asin atan atan2 ceil cos cosh deg exp floor fmod frexp huge ldexp log log10 max min modf pi pow rad random randomseed sin sinh sqrt tan tanh
os clock date difftime execute exit getenv remove rename setlocale time tmpname
string byte char dump find format gmatch gsub len lower match rep reverse sub upper
table concat insert maxn remove sort unpack pack
debug getinfo getlocal getupvalue setupvalue sethook gethook traceback getregistry info
package loaded preload path cpath io _G _ENV what source short_src currentline linedefined lastlinedefined nups
__index __newindex __call __metatable __gc __len __mode __tostring __concat __eq __lt __le __unm __add __sub __mul __div __mod __pow
""".split())
_TAMPER_RE = re.compile(r"tamper|suspicious|modified|replaced or wrapped|debug library", re.I)


def _clean_constants(items):
    """Drop fragments of longer strings and short random-looking junk produced while a vm assembles its string pool."""
    uniq = list(dict.fromkeys(items))
    big = "\x00".join(uniq)
    keep = []
    for c in uniq:
        if big.count(c) > 1:
            continue  # substring of another captured string
        if len(c) <= 8 and any(ch.isdigit() for ch in c) and any(ch.isalpha() for ch in c) and not c.isidentifier():
            continue
        if c in _STD_NAMES or _TAMPER_RE.search(c):
            continue  # the protector's own integrity checks, not the script's data
        if _ERR_RE.match(c) or _RANDOM_NAME_RE.match(c):
            continue  # sandbox error text / a protector's generated identifiers
        keep.append(c)
    return keep


def syntax_ok(source, compatible=True):
    """Compile only. Optionally apply the same compatibility as sandbox.run."""
    if compatible:
        source, _ = prepare(source)
    out, _ = _spawn({"source": source, "mode": "check"}, 10)
    return any(ev.get("k") == "ok" for ev in _lines(out))
