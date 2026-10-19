"""Sandbox worker. Run as `python -I _worker.py`; reads one JSON job on stdin, streams JSON lines on stdout.
Executes untrusted Lua 5.1 in a stubbed environment: no io/os.execute/network/file access, every Roblox/executor API is a recording proxy."""
import json
import sys

BOOT = r'''
local emit, real_loadstring, real_pcall, real_setfenv, real_clock, sethook, real_cocreate, real_cowrap, LIMIT, DEADLINE = ...
local HTTP = rawget(_G, "__HTTP_MAP") or {}
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
  if t == "string" then return (string.gsub(string.format("%q", v), "\\\n", "\\n")) end
  if t == "function" then return "function() end" end
  if t == "table" then return "{...}" end
  return tostring(v)
end
local function qt(v, depth)
  if type(v) ~= "table" or proxies[v] then return q(v) end
  if depth <= 0 then return "{...}" end
  local parts, n = {}, 0
  for k, x in pairs(v) do
    n = n + 1
    if n > 8 then parts[#parts + 1] = "..." break end
    local key = type(k) == "string" and k:match("^[%a_][%w_]*$") and k or "[" .. q(k) .. "]"
    parts[#parts + 1] = (type(k) == "number" and "" or key .. " = ") .. qt(x, depth - 1)
  end
  return "{" .. table.concat(parts, ", ") .. "}"
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
  for i = first, args.n do parts[#parts + 1] = qt(args[i], 2) end
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
      if m == "HttpGet" or m == "HttpGetAsync" then
        local url = args[1]
        if proxies[url] then url = args[2] end
        if type(url) == "string" then
          emit("get", url)
          if HTTP[url] then return HTTP[url] end
        end
        return ""
      end
      if m == "HttpPost" or m == "PostAsync" or m == "GetAsync" or m == "RequestAsync" then return "" end
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
  T("request(" .. qt(opts, 2) .. ")")
  if type(opts) == "table" then
    note_urls({n = 1, opts})
    if opts.Body then emit("post", tostring(opts.Url) .. " <- " .. tostring(opts.Body):sub(1, 400)) end
  end
  local body = ""
  if type(opts) == "table" and type(opts.Url) == "string" and (opts.Method == nil or tostring(opts.Method):upper() == "GET") then
    emit("get", opts.Url)
    body = HTTP[opts.Url] or ""
  end
  return {Body = body, StatusCode = 200, Success = true, Headers = {}}
end

for _, k in ipairs({"assert", "error", "ipairs", "next", "pairs", "pcall", "xpcall", "rawequal", "rawget", "rawset", "select", "tonumber", "tostring", "type", "unpack", "setmetatable", "getmetatable", "collectgarbage"}) do
  env[k] = _G[k]
end
local real_str = {}
for k, v in pairs(string) do real_str[k] = v end
local seen_const, nconst = {}, 0
local function note_const(r)
  if type(r) ~= "string" or #r < 3 or #r > 4000 or seen_const[r] or nconst >= 3000 then return r end
  local n, good = math.min(#r, 200), 0
  for i = 1, n do
    local b = real_str.byte(r, i)
    if (b >= 32 and b < 127) or b == 10 or b == 9 then good = good + 1 end
  end
  if good / n >= 0.9 then seen_const[r] = true; nconst = nconst + 1; emit("const", r) end
  return r
end
-- wrapped string/table/math: record decoded constants and add the Luau surface VM loaders probe for
local ws, wt, wm = {}, {}, {}
for k, v in pairs(string) do ws[k] = v end
for k, v in pairs(table) do wt[k] = v end
for k, v in pairs(math) do wm[k] = v end
ws.char = function(...) local r = real_str.char(...) if select("#", ...) >= 3 then note_const(r) end return r end
ws.gsub = function(...) local r, n = real_str.gsub(...) note_const(r) return r, n end
ws.reverse = function(x) return note_const(real_str.reverse(x)) end
ws.format = function(...) return note_const(real_str.format(...)) end
ws.dump = function() return "" end
ws.rep = function(x, n, ...) if type(n) == "number" and #tostring(x) * n > 1000000 then error("sandbox: string.rep too large", 0) end return real_str.rep(x, n, ...) end
wt.maxn = function(t) local n = 0 for k in pairs(t) do if type(k) == "number" and k > n then n = k end end return n end
local real_concat = table.concat
wt.concat = function(...) return note_const(real_concat(...)) end
wt.unpack, wt.pack = unpack, function(...) return {n = select("#", ...), ...} end
wt.find = function(t, v, init) for i = init or 1, #t do if t[i] == v then return i end end end
wt.create = function(n, v) local t = {} if v ~= nil then for i = 1, n do t[i] = v end end return t end
wt.clear = function(t) for k in pairs(t) do t[k] = nil end end
wt.freeze = function(t) return t end
wt.isfrozen = function() return false end
wt.clone = function(t) local c = {} for k, v in pairs(t) do c[k] = v end return setmetatable(c, getmetatable(t)) end
wt.move = function(a, f, e, t, b) b = b or a if t > f then for i = e - f, 0, -1 do b[t + i] = a[f + i] end else for i = 0, e - f do b[t + i] = a[f + i] end end return b end
wm.clamp = function(x, lo, hi) return math.min(math.max(x, lo), hi) end
wm.sign = function(x) return x > 0 and 1 or x < 0 and -1 or 0 end
wm.round = function(x) return x >= 0 and math.floor(x + 0.5) or math.ceil(x - 0.5) end
wm.noise = function() return 0 end
getmetatable("").__index = ws
env.string, env.table, env.math = ws, wt, wm
env.typeof = function(v) if proxies[v] then return "Instance" end return type(v) end
env.newproxy = function(m) local p = {} if m then setmetatable(p, {}) end return p end
env.gcinfo = function() return collectgarbage("count") end
env.coroutine = {
  create = function(f) local co = real_cocreate(f); sethook(co, LIMIT, "", 10000); return co end,
  wrap = function(f) local co = real_cocreate(f); sethook(co, LIMIT, "", 10000); return function(...) local r = {coroutine.resume(co, ...)}; if not r[1] then error(r[2], 0) end; return unpack(r, 2) end end,
  resume = coroutine.resume, yield = coroutine.yield, status = coroutine.status, running = coroutine.running, isyieldable = function() return false end,
}
env.os = {time = os.time, clock = os.clock, date = os.date, difftime = os.difftime,
  getenv = function() return nil end, tmpname = function() return "" end, setlocale = function() return "C" end,
  exit = function() T("os.exit()") error("sandbox: os.exit", 0) end,
  execute = function(c) T("os.execute(" .. q(c) .. ")") return nil end,
  remove = function(f) T("os.remove(" .. q(f) .. ")") return nil, "denied" end,
  rename = function(a, b) T("os.rename(" .. q(a) .. ", " .. q(b) .. ")") return nil, "denied" end}
env.io = {write = function() end, read = function() end, open = function(f) T("io.open(" .. q(f) .. ")") return nil, "denied" end,
  lines = function() return function() end end, close = function() end, stdout = {write = function() end}, stderr = {write = function() end}}
env.package = {loaded = {}, preload = {}, path = "", cpath = "", loaders = {}, searchers = {}}
env.loadfile = function() return nil, "denied" end
env.dofile = function() error("denied", 0) end
env.module = function() end
local M32 = 4294967296
local function n32(x) return x % M32 end
local function bitop(a, b, f)
  a, b = n32(a), n32(b)
  local r, p = 0, 1
  while a > 0 or b > 0 do
    local x, y = a % 2, b % 2
    if f(x, y) then r = r + p end
    a, b, p = (a - x) / 2, (b - y) / 2, p * 2
  end
  return r
end
local function fold(start, f)
  return function(...)
    local r = start
    for i = 1, select("#", ...) do r = bitop(r, (select(i, ...)), f) end
    return r
  end
end
local b32 = {
  band = fold(M32 - 1, function(x, y) return x == 1 and y == 1 end),
  bor = fold(0, function(x, y) return x == 1 or y == 1 end),
  bxor = fold(0, function(x, y) return x ~= y end),
  bnot = function(a) return M32 - 1 - n32(a) end,
  lshift = function(a, n) if n >= 32 then return 0 end if n < 0 then return math.floor(n32(a) / 2 ^ -n) end return (n32(a) % 2 ^ (32 - n)) * 2 ^ n end,
  rshift = function(a, n) if n >= 32 then return 0 end if n < 0 then return (n32(a) % 2 ^ (32 + n)) * 2 ^ -n end return math.floor(n32(a) / 2 ^ n) end,
}
b32.btest = function(...) return b32.band(...) ~= 0 end
b32.arshift = function(a, n) a = n32(a) local r = b32.rshift(a, n) if a >= 2147483648 and n > 0 then r = r + (M32 - 2 ^ (32 - math.min(n, 32))) end return n32(r) end
b32.lrotate = function(a, n) n = n % 32 return b32.bor(b32.lshift(a, n), b32.rshift(a, 32 - n)) end
b32.rrotate = function(a, n) return b32.lrotate(a, -n) end
b32.extract = function(a, f, w) return b32.band(b32.rshift(a, f), 2 ^ (w or 1) - 1) end
env.bit32, env.bit = b32, b32
local real_debug = debug
local native = setmetatable({}, {__mode = "k"})
local function stub_info(f, what)
  if type(f) == "function" then
    if native[f] then
      return {what = "C", short_src = "[C]", source = "=[C]", currentline = -1, linedefined = -1, lastlinedefined = -1, nups = 0, namewhat = "", func = f}
    end
    return real_debug.getinfo(f, what)
  end
  return {what = "Lua", short_src = "obf", source = "=obf", currentline = 1, linedefined = 1, lastlinedefined = 1, nups = 0, namewhat = "", name = "?"}
end
env.debug = {
  info = function(f, w) local i = stub_info(f, "S") if w and w:find("s") then return i.short_src end return "" end,
  getinfo = stub_info, traceback = function(m) return tostring(m or "") end,
  sethook = function() end, gethook = function() end,
  getlocal = function() end, setlocal = function() end, getupvalue = function() end, setupvalue = function() end,
  upvalueid = function() end, upvaluejoin = function() end, getregistry = function() return {} end,
  getmetatable = getmetatable, setmetatable = setmetatable, getfenv = function() return env end, setfenv = function(f) return f end,
}
env.loadstring = capture_load
env.load = function(x, name)
  if type(x) == "function" then
    local parts = {}
    while true do local piece = x(); if piece == nil or piece == "" then break end; parts[#parts + 1] = piece end
    x = table.concat(parts)
  end
  return capture_load(x, name)
end
local function enc(v) v = tostring(v) return #v .. ":" .. v end
local ndec, seen_dec = 0, {}
local decoders = {}
env.__REC = function(f, name)
  decoders[name] = f
  return function(...)
    local res = {f(...)}
    local r = res[1]
    if type(r) == "string" and ndec < 30000 then
      local parts = {}
      for i = 1, select("#", ...) do
        local a = (select(i, ...))
        if type(a) == "string" then parts[#parts + 1] = "s" .. enc(a)
        elseif type(a) == "number" then parts[#parts + 1] = "n" .. enc(string.format("%.17g", a))
        else parts = nil break end
      end
      if parts then
        local payload = enc(name) .. enc(table.concat(parts)) .. enc(r)
        if not seen_dec[payload] then seen_dec[payload] = true ndec = ndec + 1 emit("dec", payload) end
      end
    end
    return unpack(res)
  end
end
-- evaluate a recorded decoder (with its real upvalues) for call sites the run never reached
env.__QUERY = function(name, ...)
  local f = decoders[name]
  if not f then return nil end
  local ok, r = real_pcall(f, ...)
  if ok and type(r) == "string" then return r end
end
env.getfenv = function() return env end
env.setfenv = function(f, e) if type(f) == "function" then return f end return f end
env.print = function(...)
  local t, shown = {}, {}
  for i = 1, select("#", ...) do local v = (select(i, ...)) t[#t + 1] = tostring(v) shown[#shown + 1] = qt(v, 2) end
  emit("print", table.concat(t, "\t"))
  T("print(" .. table.concat(shown, ", ") .. ")")
end
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

local function mark(t, seen)
  if seen[t] then return end
  seen[t] = true
  for _, v in pairs(t) do
    if type(v) == "function" then native[v] = true elseif type(v) == "table" and not proxies[v] then mark(v, seen) end
  end
end
mark(env, {})
for _, k in ipairs({"string", "table", "math", "os", "io", "debug", "coroutine", "bit32", "package"}) do env.package.loaded[k] = env[k] end
env.package.loaded._G = env

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
end, env, function(s) DEADLINE = real_clock() + s end
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
    g[b"__HTTP_MAP"] = lua.table_from({k.encode("latin-1"): v.encode("latin-1") for k, v in (job.get("http") or {}).items()})
    runner, env, extend = loader(
        BOOT.encode(), emit, g.loadstring, g.pcall, g.setfenv, clock, g.debug.sethook,
        g.coroutine.create, g.coroutine.wrap, None, deadline,
    )
    runner(src)
    queries = job.get("queries") or []
    if queries:
        extend(job.get("query_seconds", 10))
        query = env[b"__QUERY"]
        for name, args in queries:
            largs = [a[1].encode("latin-1") if a[0] == "s" else a[1] for a in args]
            r = query(name.encode(), *largs)
            if r is not None:
                emit("qres", json.dumps([name, args, r.decode("latin-1")]))
    emit("done", "")


if __name__ == "__main__":
    try:
        main()
    except BaseException as ex:  # report, never raise: parent parses stdout
        sys.stdout.write(json.dumps({"k": "error", "v": "worker: %s: %s" % (type(ex).__name__, ex)}) + "\n")
