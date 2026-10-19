"""Signature + structure based obfuscator identification. Signatures are heuristics; extend ENGINES to add more."""
import re
from dataclasses import dataclass, field


@dataclass
class Engine:
    name: str
    label: str
    vm: bool = False
    sigs: list = field(default_factory=list)  # (regex, weight)


def _e(name, label, vm, *sigs):
    return Engine(name, label, vm, [(re.compile(p, re.I), w) for p, w in sigs])


ENGINES = [
    _e("wearedevs", "WeAreDevs", False, (r"wearedevs\.net/obfuscator", 90), (r"wearedevs", 50), (r"^\s*--\[\[[^\]]*wearedevs[^\]]*\]\]\s*return\s*\(\s*function\s*\(\s*\.\.\.\s*\)", 40)),
    _e("luraph", "Luraph", True, (r"luraph", 80), (r"lura\.ph", 85), (r"\bLPH[_!]", 70), (r"protected using luraph", 90)),
    _e("prometheus", "Prometheus", False, (r"prometheus obfuscator", 90), (r"levno[_ ]?710", 90), (r"prometheus", 40), (r"getfenv and getfenv\(\)\s*or _ENV", 55), (r"unpack or table\[", 30), (r"while\s+\w+\[1\]\s*<\s*\w+\[2\]\s*do", 40)),
    _e("clyde", "Clyde", False, (r"\bclyde\b", 60), (r"clyde\s*(?:obfuscator|v\d)", 80)),
    _e("hercules", "Hercules", False, (r"Lua 5\.3\+ / Luau compatibility polyfills", 70), (r"Tamper Detected! Reason", 40), (r"hercules\s*(?:obfuscator|v\d|protected)", 90), (r"\bhercules\b", 55)),
    _e("moonsec", "MoonSec", True, (r"moonsec", 85), (r"protected using moonsec", 95)),
    _e("moonveil", "Moonveil", True, (r"moonveil", 85)),
    _e("goofyscator", "Goofyscator", False, (r"goofyscator", 90), (r"\bgoofy\b", 40)),
    _e("ironbrew", "IronBrew", True, (r"ironbrew", 85), (r"[\"']LOL!", 55), (r"\bStk\b.*\bInst\b", 35), (r"\bUpvalues\b.*\bVararg\b", 25), (r"\bStk\b", 20)),
    _e("mwp", "MWP", True, (r"\bmwp\b", 70), (r"mwp\s*obfuscator", 90)),
    _e("ironveil", "IronVeil", True, (r"ironveil", 90)),
    _e("luaobfuscator", "Luaobfuscator.com", False, (r"luaobfuscator\.com", 90), (r"luaobfuscator", 60)),
    _e("luast", "Luast", True, (r"\bluast\b", 85)),
]
BY_NAME = {e.name: e for e in ENGINES}
MIN_REPORT = 30


def obfuscation_score(src):
    """0-100 structural guess that the script is obfuscated at all."""
    n = max(len(src), 1)
    s = 0
    s += min(40, 40 * len(re.findall(r"\\x[0-9a-fA-F]{2}|\\\d{2,3}", src)) // max(n // 40, 1))
    s += min(20, len(re.findall(r"\blocal\s+[A-Za-z_]\w{0,2}\d*\s*=", src)) // 5)
    if n > 2000 and src.count("\n") < n // 400:
        s += 20
    if re.search(r"\b(?:loadstring|load)\s*\(", src):
        s += 10
    if re.search(r"\bgetfenv\b|\bsetfenv\b|string\.byte|string\.char|\bbit32?\b", src):
        s += 10
    return min(s, 100)


def detect(src):
    """Return (ranked [(name, confidence)], obfuscation_score)."""
    ranked = []
    for e in ENGINES:
        total = 0
        for rx, w in e.sigs:
            if rx.search(src):
                total += w
        if total >= MIN_REPORT:
            ranked.append((e.name, min(total, 99)))
    ranked.sort(key=lambda x: -x[1])
    return ranked, obfuscation_score(src)
