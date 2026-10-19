"""Signature + structure based obfuscator identification. Signatures are heuristics; extend ENGINES to add more."""
import re
from dataclasses import dataclass, field

from .catalog import EXTRA_ENGINES, _brand


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
    _e("wearedevs", "WeAreDevs", False, _brand("wearedevs"), (r"wearedevs\.net/obfuscator", 90, "header wearedevs.net/obfuscator")),
    _e("luraph", "Luraph", True, _brand("luraph"), (r"lura\.ph", 85, "header lura.ph"), (r"\bLPH[_!]", 70, "Luraph runtime macro")),
    _e("prometheus", "Prometheus", False, _brand("prometheus"), (r"levno[_ ]?710", 90, "Prometheus author watermark"), (r"getfenv and getfenv\(\)\s*or _ENV", 55, "getfenv and getfenv() or _ENV prologue"), (r"unpack or table\[", 30, "unpack or table[...] lookup"), (r"while\s+\w+\[1\]\s*<\s*\w+\[2\]\s*do", 40, "constant array rotation loop")),
    _e("clyde", "Clyde", False, _brand("clyde")),
    _e("hercules", "Hercules", False, _brand("hercules"), (r"Lua 5\.3\+ / Luau compatibility polyfills", 70, "polyfill header comment"), (r"Tamper Detected! Reason", 40, "Tamper Detected message")),
    _e("moonsec", "MoonSec", True, _brand("moonsec")),
    _e("moonveil", "Moonveil", True, _brand("moonveil")),
    _e("goofyscator", "Goofyscator", False, _brand("goofyscator")),
    _e("ironbrew", "IronBrew", True, _brand("ironbrew"), (r"\bironbrew(?:2|:tm:)?\s*[:;]?\s*obfuscation", 85, "IronBrew watermark"), (r"[\"']LOL!", 55, "IronBrew packed bytecode prefix"), (r"\bStk\b.*\bInst\b", 35), (r"\bUpvalues\b.*\bVararg\b", 25), (r"\bStk\b", 20)),
    _e("mwp", "MWP", True, _brand("mwp")),
    _e("ironveil", "IronVeil", True, _brand("ironveil")),
    _e("luaobfuscator", "Luaobfuscator.com", False, _brand("luaobfuscator"), (r"luaobfuscator\.com", 90, "Luaobfuscator service domain")),
    _e("rerubi", "Rerubi VM loader", True, (r"OpArgR", 60, "OpArgR opcode table"), (r"OpArgK.{0,80}OpArgN", 30)),
    _e("luast", "Luast", True, _brand("luast")),
]
ENGINES.extend(_e(name, label, vm, *sigs) for name, label, vm, sigs in EXTRA_ENGINES)
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


def _matches(src):
    ranked = []
    for e in ENGINES:
        total, why = 0, []
        for rx, w, reason in e.sigs:
            if rx.search(src):
                total += w
                why.append(reason)
        if total >= MIN_REPORT:
            ranked.append((e.name, min(total, 99), why))
    ranked.sort(key=lambda x: -x[1])
    # Some AzureVM modes deliberately carry Luraph-looking headers. A runtime
    # namespace provides stronger attribution than that compatibility watermark.
    if re.search(r"\bAVM_OBFUSCATED\b", src):
        ranked.sort(key=lambda x: x[0] != "azurevm")
    return ranked


def detect(src):
    """Return ranked evidence scores (not probabilities), plus obfuscation score."""
    return [(n, c) for n, c, _ in _matches(src)], obfuscation_score(src)


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
    engines = _matches(src)
    tags = techniques(src)
    vm = bool(engines and BY_NAME[engines[0][0]].vm) or any(n in ("vm", "vm-dispatch") for n, _ in tags)
    ambiguous = bool(len(engines) > 1 and engines[0][1] - engines[1][1] < 10)
    return {"engines": engines, "techniques": tags, "score": obfuscation_score(src), "vm": vm,
            "ambiguous": ambiguous, "score_kind": "heuristic evidence, not probability"}
