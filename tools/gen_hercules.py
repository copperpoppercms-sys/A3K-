"""Generate real Hercules output for tests.
usage: python tools/gen_hercules.py <light|balanced|heavy|maximum> in.lua out.lua
needs: git clone https://github.com/zeusssz/hercules-obfuscator (set HERCULES_ROOT) and lupa with Lua 5.4."""
import os, sys, shutil
from lupa.lua54 import LuaRuntime
preset, inp, out = sys.argv[1:4]
root = os.environ.get("HERCULES_ROOT", "/home/user/zeusssz/hercules-obfuscator") + "/src"
os.chdir(root)
work = "/tmp/herc_work_%s.lua" % preset
shutil.copy(inp, work)
lua = LuaRuntime(unpack_returned_tuples=True)
lua.execute('package.path = "%s/?.lua;%s/modules/?.lua;" .. package.path' % (root, root))
g = lua.globals()
arg = lua.table_from({0: "hercules.lua"})
for i, a in enumerate([work, "--" + preset, "--overwrite", "--no-watermark"], 1):
    arg[i] = a
g.arg = arg
try:
    lua.execute('dofile("hercules.lua")')
except Exception as e:
    print("ERR", str(e)[:300])
shutil.copy(work, out)
