"""Fixes for known bugs in obfuscator output, applied only to the copy that runs in the sandbox.

Hercules v1.6.x web builds clobber an instruction's A field (the destination register) with the constant used as
operand B whenever B is a constant. Any `t.k = v` / `{k = v}` then crashes with "attempt to index field '?'"
in every Lua runtime. The constant is moved to its own field `K` so the script runs the way its author meant."""
import re

from . import passes

_HERCULES_STORE = re.compile(r"(\b\w+)\.s then \1\.A=(\w+)\.D\[")
_HERCULES_LOAD = re.compile(r"(\b\w+)\.s then (\w+)=(\w+)\.A else")


def _hercules_vm_clobber(src):
    if not _HERCULES_STORE.search(src):
        return src, False
    out = passes._map_other(src, lambda t: _HERCULES_STORE.sub(r"\1.s then \1.K=\2.D[", t))
    out = passes._map_other(out, lambda t: _HERCULES_LOAD.sub(r"\1.s then \2=\3.K else", t))
    return out, out != src


PATCHES = [("hercules vm operand clobber", _hercules_vm_clobber)]


def apply(src):
    """Return (patched_source, [names of patches applied])."""
    applied = []
    for name, fn in PATCHES:
        src, did = fn(src)
        if did:
            applied.append(name)
    return src, applied
