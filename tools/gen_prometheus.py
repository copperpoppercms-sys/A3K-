"""Generate real Prometheus output for tests.
usage: python tools/gen_prometheus.py <Minify|Weak|Medium|Strong> in.lua out.lua
needs: git clone https://github.com/prometheus-lua/prometheus into /home/user/prometheus-lua/prometheus (or edit ROOT)."""
import os
ROOT = os.environ.get("PROMETHEUS_ROOT", "/home/user/prometheus-lua/prometheus")
import sys
from lupa.lua51 import LuaRuntime
preset, inp, out = sys.argv[1:4]
inp, out = os.path.abspath(inp), os.path.abspath(out)
os.chdir(ROOT)
lua = LuaRuntime(unpack_returned_tuples=True)
lua.execute('package.path = "./src/?.lua;" .. package.path')
g = lua.globals()
arg = lua.table_from({0: "cli.lua"})
for i, a in enumerate(["--preset", preset, inp, "--out", out], 1):
    arg[i] = a
g.arg = arg
lua.execute('dofile("cli.lua")')
