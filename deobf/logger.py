"""Item-stealer / data-exfil detection and neutralisation."""
import re
from dataclasses import dataclass

from . import sandbox

PLACEHOLDER_URL = "https://127.0.0.1/logger-removed"

_URL_RE = re.compile(
    r"https?://(?:[\w-]+\.)?(?:discord(?:app)?\.com/api/webhooks/\d+/[\w-]+|api\.telegram\.org/bot[\w:-]+|"
    r"(?:canary\.|ptb\.)?discord\.com/api/webhooks/\d+/[\w-]+|hooks\.slack\.com/services/[\w/]+|webhook\.site/[\w-]+|"
    r"[\w-]+\.ngrok(?:-free)?\.(?:io|app|dev)[^\s\"']*|grabify\.link/[\w-]+|iplogger\.\w+/[\w-]+)",
    re.I,
)

# (id, regex, severity, description)
_RULES = [
    ("webhook", _URL_RE, "high", "exfil endpoint (webhook / tunnel / logger url)"),
    ("post", re.compile(r"\b(?:HttpPost|PostAsync)\s*\(", re.I), "medium", "outbound POST"),
    ("request-post", re.compile(r"\b(?:syn\.)?(?:http_)?request\s*\(\s*\{[^}]*Method\s*=\s*[\"']POST", re.I | re.S), "medium", "executor request() POST"),
    ("cookie", re.compile(r"\.ROBLOSECURITY|_\|WARNING:-DO-NOT-SHARE|RobloxSecurity", re.I), "high", "roblox cookie access"),
    ("ip-grab", re.compile(r"api\.ipify\.org|ip-api\.com|ipinfo\.io|ifconfig\.me|checkip\.", re.I), "medium", "public ip lookup"),
    ("inventory", re.compile(r"GetRobuxBalance|inventory.{0,40}(?:rap|value)|\bRAP\b.{0,40}(?:total|sum)", re.I), "medium", "inventory / value harvesting"),
    ("teleport-queue", re.compile(r"queue_on_teleport", re.I), "low", "persists across teleports"),
]


@dataclass
class Finding:
    id: str
    severity: str
    line: int
    snippet: str
    desc: str


def scan(src):
    findings = []
    for rid, rx, sev, desc in _RULES:
        for m in rx.finditer(src):
            line = src.count("\n", 0, m.start()) + 1
            snippet = src[m.start():m.start() + 120].splitlines()[0]
            findings.append(Finding(rid, sev, line, snippet, desc))
    return findings


def is_logger(findings):
    sev = {f.severity for f in findings}
    ids = {f.id for f in findings}
    return "high" in sev or ("post" in ids or "request-post" in ids) and ("ip-grab" in ids or "inventory" in ids or "cookie" in ids)


def _line_is_self_contained(line):
    return (
        line.count("(") == line.count(")") and line.count("{") == line.count("}")
        and not re.search(r"\b(?:then|do|function|repeat|else)\s*$", line.strip())
        and not re.match(r"\s*(?:if|for|while|elseif|else|function|local\s+function)\b", line)
    )


def remove(src):
    """Return (clean_source, notes). Replace exfil URLs, then comment out self-contained POST lines when it still compiles."""
    notes = []
    out, n = _URL_RE.subn(PLACEHOLDER_URL, src)
    if n:
        notes.append("replaced %d exfil url(s)" % n)
    baseline_ok = sandbox.syntax_ok(out)
    lines = out.split("\n")
    post_rx = re.compile(r"\b(?:HttpPost|PostAsync)\s*\(|\b(?:syn\.)?(?:http_)?request\s*\(|\.ROBLOSECURITY", re.I)
    removed = 0
    for i, line in enumerate(lines):
        if not post_rx.search(line) or line.lstrip().startswith("--") or not _line_is_self_contained(line):
            continue
        # a statement that assigns a value others depend on is replaced by nil instead of dropped
        m = re.match(r"(\s*local\s+[\w,\s]+=)\s*.+", line)
        new = (m.group(1) + " nil -- logger removed") if m else "-- logger removed: " + line.strip()
        trial = lines[:i] + [new] + lines[i + 1:]
        if baseline_ok and not sandbox.syntax_ok("\n".join(trial)):
            continue
        lines = trial
        removed += 1
    if removed:
        notes.append("neutralised %d logger statement(s)" % removed)
    return "\n".join(lines), notes
