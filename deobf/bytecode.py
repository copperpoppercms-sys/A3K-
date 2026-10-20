"""Find Lua 5.1 bytecode inside a script (string literals, byte arrays) and turn it back into source."""
import re

from . import decompile, luac, passes

_ARRAY = re.compile(r"(?:\d{1,3}\s*[,;]\s*){40,}\d{1,3}")
MAX_CHUNK = 4_000_000


def _candidates(src):
    """Yield byte strings that start with a Lua 5.1 header."""
    for kind, text in passes.lua_split(src):
        if kind != "str" or len(text) < 40:
            continue
        try:
            data = passes.parse_quoted(text) if passes.is_quoted(text) else text
            raw = data.encode("latin-1")
        except (UnicodeError, ValueError):
            continue
        for off in luac.find_chunks(raw):
            yield raw[off:]
    for m in _ARRAY.finditer(src):
        nums = [int(x) for x in re.findall(r"\d+", m.group(0))]
        if max(nums) <= 255 and bytes(nums[:5]) == luac.MAGIC + b"\x51":
            yield bytes(nums)


def to_source(chunk):
    """Decompile one chunk of bytecode to Lua source. Raises on anything unsupported."""
    try:
        return decompile.decompile(chunk)
    except (luac.LuacError, decompile.DecompileError, RecursionError, ValueError, IndexError, KeyError) as e:
        raise decompile.DecompileError(str(e))


def carve_best(src):
    """(source, description) for the largest embedded chunk that decompiles, or None."""
    best = None
    for data in _candidates(src):
        if len(data) > MAX_CHUNK:
            continue
        if best is None or len(data) > len(best):
            best = data
    if best is None:
        return None
    try:
        return to_source(best), "%d bytes of bytecode" % len(best)
    except decompile.DecompileError:
        return None
