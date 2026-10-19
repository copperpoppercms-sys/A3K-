"""Sandbox worker. Run as `python -I _worker.py`; reads one JSON job on stdin, streams JSON lines on stdout.
Executes untrusted Lua 5.1 in a stubbed environment: no io/os.execute/network/file access, every Roblox/executor API is a recording proxy."""
import json
import sys

BOOT = r'''
local emit, real_loadstring, real_pcall, real_setfenv, real_clock, sethook, real_cocreate, real_cowrap, LIMIT, DEADLINE = ...
local env = {}
local proxies = setmetatable({}, {__mode = "k"})
local ntrace = 0
local function T(s)
  ntrace = ntrace + 1
  if ntrace <= 4000 then emit("trace", s) end
end
local function q(v)
  local t = type(v)
  if proxies[v] then return proxies[v] end
  if t == "string" then return (string.format("%q", v):gsub("\\\n", "\\n")) end
  if t == "function" then return "function() end" end
  if t == "table" then return "{...}" end
  return tostring(v)
end
local function note_urls(args)
  for i = 1, args.n do
    local a = args[i]
    if type(a) == "string" and a:find("://", 1, true) then emit("url", a) end
    if type(a) == "table" and not proxies[a] then
      for _, v in pairs(a) do if type(v) == "string" and v:find("://", 1, true) then emit("url", v) end end
    end
  end
end
local mk
local function call_text(name, args)
  local parts, first = {}, 1
  local base, meth = name:match("^(.*)%.([^.%(%)]+)$")
  local sep = "."
  if base and args.n > 0 and proxies[args[1]] == base then first = 2; sep = ":" end
  for i = first, args.n do parts[#parts + 1] = q(args[i]) end
  local shown = base and (base .. sep .. meth) or name
  return shown .. "(" .. table.concat(parts, ", ") .. ")"
end
mk = function(name)
  local p = {}
  proxies[p] = name
  return setmetatable(p, {
    __index = function(_, k)
      if k == "Name" or k == "DisplayName" then return "Player" end
      return mk(name .. "." .. tostring(k))
    end,
    __newindex = function(_, k, v) T(name .. "." .. tostring(k) .. " = " .. q(v)) end,
    __call = function(_, ...)
      local args = table.pack and table.pack(...) or {n = select("#", ...), ...}
      local text = call_text(name, args)
      T(text)
      note_urls(args)
      local m = name:match("([^.]+)$")
      if m == "HttpGet" or m == "HttpGetAsync" or m == "HttpPost" or m == "PostAsync" or m == "GetAsync" or m == "RequestAsync" then return "" end
      return mk(text)
    end,
    __tostring = function() return name end,
    __concat = function(a, b) return tostring(a) .. tostring(b) end,
    __add = function() return 0 end, __sub = function() return 0 end, __mul = function() return 0 end,
    __div = function() return 0 end, __mod = function() return 0 end, __pow = function() return 0 end,
    __unm = function() return 0 end, __len = function() return 0 end,
    __lt = function() return false end, __le = function() return false end,
  })
end

local function capture_load(s, chunkname)
  if type(s) ~= "string" then return nil, "bad chunk" end
  emit("capture", s)
  local f, err = real_loadstring(s, chunkname)
  if f then real_setfenv(f, env) end
  return f, err
end

local function http_request(opts)
  T("request(" .. q(opts) .. ")")
  if type(opts) == "table" then
    note_urls({n = 1, opts})
    if opts.Body then emit("post", tostring(opts.Url) .. " <- " .. tostring(opts.Body):sub(1, 400)) end
  end
  return {Body = "", StatusCode = 200, Success = true, Headers = {}}
end

for _, k in ipairs({"assert", "error", "ipairs", "next", "pairs", "pcall", "xpcall", "rawequal", "rawget", "rawset", "select", "tonumber", "tostring", "type", "unpack", "setmetatable", "getmetatable", "collectgarbage"}) do
  env[k] = _G[k]
end
env.string, env.table, env.math = string, table, math
env.coroutine = {
  create = function(f) local co = real_cocreate(f); sethook(co, LIMIT, "", 10000); return co end,
  wrap = function(f) local co = real_cocreate(f); sethook(co, LIMIT, "", 10000); return function(...) local r = {coroutine.resume(co, ...)}; if not r[1] then error(r[2], 0) end; return unpack(r, 2) end end,
  resume = coroutine.resume, yield = coroutine.yield, status = coroutine.status, running = coroutine.running, isyieldable = function() return false end,
}
env.os = {time = os.time, clock = os.clock, date = os.date, difftime = os.difftime}
env.bit32 = bit or {band = function(a, b) return 0 end, bor = function(a, b) return 0 end, bxor = function(a, b) return 0 end}
env.bit = env.bit32
env.debug = {traceback = function() return "" end, getinfo = function() return {short_src = "=?", source = "=?", currentline = 0, what = "Lua"} end, sethook = function() end, gethook = function() end, getlocal = function() end, getupvalue = function() end, getmetatable = getmetatable}
env.loadstring = capture_load
env.load = function(x, name)
  if type(x) == "function" then
    local parts = {}
    while true do local piece = x(); if piece == nil or piece == "" then break end; parts[#parts + 1] = piece end
    x = table.concat(parts)
  end
  return capture_load(x, name)
end
env.getfenv = function() return env end
env.setfenv = function(f, e) if type(f) == "function" then return f end return f end
env.print = function(...) local t = {} for i = 1, select("#", ...) do t[#t + 1] = tostring((select(i, ...))) end emit("print", table.concat(t, "\t")) end
env.warn = env.print
env.wait = function() return 0.03, 0 end
env.delay = function(_, f) if type(f) == "function" then real_pcall(f) end end
env.spawn = function(f, ...) if type(f) == "function" then real_pcall(f, ...) end end
env.tick = os.clock
env.time = os.clock
env.elapsedTime = os.clock
env.require = function(x) T("require(" .. q(x) .. ")") return mk("require()") end
env.request, env.http_request = http_request, http_request
env.syn = {request = http_request, protect_gui = function() end}
env.http = {request = http_request}
env.getgenv = function() return env end
env.getrenv = function() return env end
env.getreg = function() return {} end
env.getgc = function() return {} end
env.hookfunction = function(a, b) T("hookfunction(" .. q(a) .. ", " .. q(b) .. ")") return a end
env.hookmetamethod = function(...) T("hookmetamethod(...)") return function() end end
env.newcclosure = function(f) return f end
env.checkcaller = function() return true end
env.identifyexecutor = function() return "Synapse X", "1.0" end
env.getexecutorname = env.identifyexecutor
env.setclipboard = function(s) T("setclipboard(" .. q(s) .. ")") end
env.writefile = function(n, s) T("writefile(" .. q(n) .. ", " .. q(s) .. ")") end
env.readfile = function(n) T("readfile(" .. q(n) .. ")") return "" end
env.isfile = function() return false end
env.isfolder = function() return false end
env.makefolder = function() end
env.queue_on_teleport = function(s) T("queue_on_teleport(" .. q(s) .. ")") end
env.shared, env._G, env._VERSION = {}, {}, "Luau"
env._G._G = env._G
for _, k in ipairs({"game", "workspace", "script", "Instance", "Vector3", "Vector2", "CFrame", "Color3", "BrickColor", "UDim2", "UDim", "Enum", "Ray", "Random", "TweenInfo", "NumberSequence", "ColorSequence", "Rect", "Region3", "Drawing", "task", "Players", "RunService", "UserInputService", "HttpService", "TweenService", "Lighting", "ReplicatedStorage", "StarterGui", "CoreGui", "TeleportService", "MarketplaceService", "Stats", "Workspace"}) do
  env[k] = mk(k)
end
env.task = {wait = env.wait, spawn = env.spawn, delay = env.delay, defer = env.spawn}

local hook = function()
  if real_clock() > DEADLINE then error("sandbox: time limit", 0) end
end
LIMIT = hook
sethook(hook, "", 10000)

return function(source)
  local f, err = real_loadstring(source, "=obf")
  if not f then emit("error", "syntax: " .. tostring(err)) return end
  real_setfenv(f, env)
  local ok, e = real_pcall(f)
  if not ok then emit("error", tostring(e)) end
end
'''


def main():
    job = json.loads(sys.stdin.read())
    from lupa.lua51 import LuaRuntime

    def emit(kind, payload):
        kind = kind.decode("latin-1") if isinstance(kind, bytes) else kind
        if isinstance(payload, bytes):
            payload = payload.decode("latin-1")
        sys.stdout.write(json.dumps({"k": kind, "v": payload}) + "\n")
        sys.stdout.flush()

    src = job["source"].encode("latin-1")
    if job.get("mode") == "check":
        lua = LuaRuntime(encoding=None, register_eval=False, register_builtins=False, unpack_returned_tuples=True)
        ok, err = lua.eval("function(s) local f, e = loadstring(s) return f ~= nil, e or '' end")(src)
        emit("ok" if ok else "error", err)
        return
    lua = LuaRuntime(encoding=None, register_eval=False, register_builtins=False, unpack_returned_tuples=True)
    g = lua.globals()
    clock = lua.eval("os.clock")
    deadline = clock() + job.get("seconds", 10)
    loader = lua.eval("function(code, ...) return assert(loadstring(code))(...) end")
    runner = loader(
        BOOT.encode(), emit, g.loadstring, g.pcall, g.setfenv, clock, g.debug.sethook,
        g.coroutine.create, g.coroutine.wrap, None, deadline,
    )
    runner(src)
    emit("done", "")


if __name__ == "__main__":
    try:
        main()
    except BaseException as ex:  # report, never raise: parent parses stdout
        sys.stdout.write(json.dumps({"k": "error", "v": "worker: %s: %s" % (type(ex).__name__, ex)}) + "\n")
