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
    """sigs: (regex, weight) or (regex, weight, human readable reason)"""
    return Engine(name, label, vm, [(re.compile(x[0], re.I), x[1], x[2] if len(x) > 2 else x[0][:48]) for x in sigs])


ENGINES = [
    _e("wearedevs", "WeAreDevs", False, (r"wearedevs\.net/obfuscator", 90, "header wearedevs.net/obfuscator"), (r"wearedevs", 50), (r"^\s*--\[\[[^\]]*wearedevs[^\]]*\]\]\s*return\s*\(\s*function\s*\(\s*\.\.\.\s*\)", 40)),
    _e("luraph", "Luraph", True, (r"luraph", 80), (r"lura\.ph", 85, "header lura.ph"), (r"\bLPH[_!]", 70), (r"protected using luraph", 90)),
    _e("prometheus", "Prometheus", False, (r"prometheus obfuscator", 90), (r"levno[_ ]?710", 90), (r"prometheus", 40), (r"getfenv and getfenv\(\)\s*or _ENV", 55, "getfenv and getfenv() or _ENV prologue"), (r"unpack or table\[", 30, "unpack or table[...] lookup"), (r"while\s+\w+\[1\]\s*<\s*\w+\[2\]\s*do", 40, "constant array rotation loop")),
    _e("clyde", "Clyde", False, (r"\bclyde\b", 60), (r"clyde\s*(?:obfuscator|v\d)", 80)),
    _e("hercules", "Hercules", False, (r"Lua 5\.3\+ / Luau compatibility polyfills", 70, "polyfill header comment"), (r"Tamper Detected! Reason", 40, "Tamper Detected message"), (r"hercules\s*(?:obfuscator|v\d|protected)", 90), (r"\bhercules\b", 55)),
    _e("moonsec", "MoonSec", True, (r"moonsec", 85), (r"protected using moonsec", 95)),
    _e("moonveil", "Moonveil", True, (r"moonveil", 85)),
    _e("goofyscator", "Goofyscator", False, (r"goofyscator", 90), (r"\bgoofy\b", 40)),
    _e("ironbrew", "IronBrew", True, (r"ironbrew", 85), (r"[\"']LOL!", 55), (r"\bStk\b.*\bInst\b", 35), (r"\bUpvalues\b.*\bVararg\b", 25), (r"\bStk\b", 20)),
    _e("mwp", "MWP", True, (r"\bmwp\b", 70), (r"mwp\s*obfuscator", 90)),
    _e("ironveil", "IronVeil", True, (r"ironveil", 90)),
    _e("luaobfuscator", "Luaobfuscator.com", False, (r"luaobfuscator\.com", 90), (r"luaobfuscator", 60)),
    _e("rerubi", "Rerubi VM loader", True, (r"OpArgR", 60, "OpArgR opcode table"), (r"OpArgK.{0,80}OpArgN", 30)),
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
        for rx, w, _why in e.sigs:
            if rx.search(src):
                total += w
        if total >= MIN_REPORT:
            ranked.append((e.name, min(total, 99)))
    ranked.sort(key=lambda x: -x[1])
    return ranked, obfuscation_score(src)


# techniques seen in the script, independent of which obfuscator produced them
_TECHNIQUES = [
    ("vm", r"\bStk\b|\bUpvalues\b|OpArg|\bInstr?\b.{0,40}\bConst", "bytecode virtual machine"),
    ("vm-dispatch", r"(?:elseif\s*\(?\s*\w+\s*==\s*\d+\s*\)?\s*then[^e]{0,400}){12,}", "opcode dispatch chain"),
    ("string-encryption", r"bxor|string\.byte.{0,300}string\.char|\\\d{3}\\\d{3}\\\d{3}|(?:\\x[0-9a-f]{2}){6}", "encrypted / escaped string constants"),
    ("constant-array", r"while\s+\w+\[1\]\s*<\s*\w+\[2\]\s*do|ipairs\(\{\{\d+[;,]\d+\}", "rotated constant array"),
    ("anti-tamper", r"debug\.(?:getinfo|info)|tamper|getfenv and getfenv\(\)\s*or _ENV", "integrity / environment checks"),
    ("control-flow-flattening", r"local\s+\w+\s*=\s*\d+\s*;\s*local\s+\w+\s*=\s*\d+\s*;\s*local\s+\w+\s*=\s*0\s*;\s*while\s+\w+\s*==\s*\w+\s+and", "opaque loop predicates"),
    ("junk-code", r"(?:local\s+_\s*=\s*\d+\s*){3,}|if\s+false\s+then", "dead code insertion"),
    ("wrapped-in-function", r"^\s*(?:--\[\[.*?\]\]\s*)?return\s*\(\s*function\s*\(\s*\.\.\.\s*\)", "whole script wrapped in a function"),
    ("loadstring-layer", r"\b(?:loadstring|load)\s*\(", "loads further code at run time"),
    ("minified", None, "everything on a handful of lines"),
]


def techniques(src):
    out = []
    for name, rx, why in _TECHNIQUES:
        if name == "minified":
            if len(src) > 1500 and src.count("\n") < len(src) // 600:
                out.append((name, why))
        elif re.search(rx, src, re.I | re.S):
            out.append((name, why))
    return out


def analyze(src):
    """Full report: engines with the reasons that matched, techniques, obfuscation score."""
    engines = []
    for e in ENGINES:
        total, why = 0, []
        for rx, w, reason in e.sigs:
            if rx.search(src):
                total += w
                why.append(reason)
        if total >= MIN_REPORT:
            engines.append((e.name, min(total, 99), why))
    engines.sort(key=lambda x: -x[1])
    tags = techniques(src)
    vm = bool(engines and BY_NAME[engines[0][0]].vm) or any(n in ("vm", "vm-dispatch") for n, _ in tags)
    return {"engines": engines, "techniques": tags, "score": obfuscation_score(src), "vm": vm}
