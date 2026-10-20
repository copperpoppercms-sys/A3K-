"""Sandbox worker. Run as `python -I _worker.py`; reads one JSON job on stdin, streams JSON lines on stdout.
Executes untrusted Lua 5.1 in a stubbed environment: no io/os.execute/network/file access, every Roblox/executor API is a recording proxy."""
import json
import sys
import time

BOOT = r'''
local emit, real_loadstring, real_pcall, real_setfenv, real_clock, sethook, real_cocreate, real_cowrap, LIMIT, DEADLINE = ...
local HTTP = rawget(_G, "__HTTP_MAP") or {}
local env = {}
local proxies = setmetatable({}, {__mode = "k"})
local values = setmetatable({}, {__mode = "k"})
local properties = setmetatable({}, {__mode = "k"})
local classes = setmetatable({}, {__mode = "k"})
local assumptions = {}
local function assume(text)
  if not assumptions[text] then assumptions[text]=true; emit("assumption", text) end
end
local ntrace, tcount = 0, {}
local function T(s)
  local c = (tcount[s] or 0) + 1
  tcount[s] = c
  if c > 3 then return end
  ntrace = ntrace + 1
  if ntrace <= 4000 then emit("trace", s) end
end
local function q(v)
  local t = type(v)
  if proxies[v] then return proxies[v] end
  if t == "string" then return (string.gsub(string.format("%q", v), "\\\n", "\\n")) end
  if t == "function" then return "<function body unavailable>" end
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
local mk, vector, udim, udim2
local function call_text(name, args)
  local parts, first = {}, 1
  local base, meth = name:match("^(.*)%.([^.%(%)]+)$")
  local sep = "."
  if base and args.n > 0 and proxies[args[1]] == base then first = 2; sep = ":" end
  for i = first, args.n do parts[#parts + 1] = qt(args[i], 2) end
  local shown = base and (base .. sep .. meth) or name
  return shown .. "(" .. table.concat(parts, ", ") .. ")"
end
local callbacks, connections, nvar = {}, {}, 0
local gui_rotation = {Frame=true,TextLabel=true,TextButton=true,ImageLabel=true,ImageButton=true,ScrollingFrame=true,CanvasGroup=true,VideoFrame=true,ViewportFrame=true,UIGradient=true}
local function proxy_equal(a,b)
  local x,y=values[a],values[b]
  if x and y and x.kind==y.kind then
    for k,v in pairs(x) do if y[k]~=v then return false end end
    return true
  end
  return proxies[a]~=nil and proxies[a]==proxies[b]
end
vector = function(name,kind,x,y,z)
  local p=mk(name); values[p]={kind=kind,X=x or 0,Y=y or 0}
  if kind=="Vector3" then values[p].Z=z or 0 end
  return p
end
udim = function(name,scale,offset)
  local p=mk(name); values[p]={kind="UDim",Scale=scale or 0,Offset=offset or 0}; return p
end
udim2 = function(name,xs,xo,ys,yo)
  local p=mk(name); values[p]={kind="UDim2",X=udim(name..".X",xs,xo),Y=udim(name..".Y",ys,yo)}; return p
end
local function arithmetic(a,b,op)
  local x,y=values[a],values[b]
  local v=x or y
  if not v or (v.kind~="Vector2" and v.kind~="Vector3") then
    error("sandbox: unsupported proxy arithmetic "..q(a).." "..op.." "..q(b),0)
  end
  if x and y and x.kind~=y.kind then error("sandbox: vector dimensions differ",0) end
  if (not x and type(a)~="number") or (not y and type(b)~="number") then error("sandbox: invalid vector operand",0) end
  if (op=="+" or op=="-") and (not x or not y) then error("sandbox: vector addition/subtraction requires vectors",0) end
  local function component(k)
    local l,r=x and x[k] or a,y and y[k] or b
    if op=="+" then return l+r elseif op=="-" then return l-r elseif op=="*" then return l*r else return l/r end
  end
  return vector("("..q(a).." "..op.." "..q(b)..")",v.kind,component("X"),component("Y"),v.kind=="Vector3" and component("Z") or nil)
end
mk = function(name)
  local p = {}
  proxies[p] = name
  properties[p] = {}
  return setmetatable(p, {
    __index = function(_, k)
      local value=values[p]
      if value then
        if value[k]~=nil and k~="kind" then return value[k] end
        if value.kind=="Vector2" or value.kind=="Vector3" then
          local magnitude=math.sqrt(value.X^2+value.Y^2+(value.Z or 0)^2)
          if k=="Magnitude" then return magnitude end
          if k=="Unit" then return vector(name..".Unit",value.kind,value.X/(magnitude~=0 and magnitude or 1),value.Y/(magnitude~=0 and magnitude or 1),(value.Z or 0)/(magnitude~=0 and magnitude or 1)) end
          if k=="Lerp" then return function(_,other,alpha) return arithmetic(p,arithmetic(arithmetic(other,p,"-"),alpha,"*"),"+") end end
          if k=="Dot" then return function(_,other) local v=values[other]; if not v or v.kind~=value.kind then error("invalid vector",0) end return value.X*v.X+value.Y*v.Y+(value.Z or 0)*(v.Z or 0) end end
        end
      end
      if properties[p][k]~=nil then return properties[p][k] end
      if (k=="Rotation" or k=="AbsoluteRotation") and gui_rotation[classes[p]] then return 0 end
      if k=="Connected" and connections[name] then return connections[name].active end
      if (name=="Vector2" or name=="Vector3") and (k=="zero" or k=="one") then local v=k=="one" and 1 or 0; return vector(name.."."..k,name,v,v,v) end
      if k=="AbsolutePosition" or k=="AbsoluteSize" or k=="ViewportSize" then
        assume("Screen geometry is simulated: origin (0,0), GUI size (400,280), viewport (1920,1080).")
        return vector(name.."."..k,"Vector2",k=="ViewportSize" and 1920 or k=="AbsoluteSize" and 400 or 0,k=="ViewportSize" and 1080 or k=="AbsoluteSize" and 280 or 0)
      end
      if k == "Name" or k == "DisplayName" then return "Player" end
      return mk(name .. "." .. tostring(k))
    end,
    __newindex = function(_, k, v)
      if values[p] then error("sandbox: value types are immutable",0) end
      properties[p][k]=v
      if type(v) == "function" and type(k) == "string" and (k:match("^On") or k:match("Callback$") or k:match("Handler$")) and #callbacks < 60 then
        callbacks[#callbacks + 1] = {fn = v, label = name .. "." .. k, active = true}
        T("-- callback registered: " .. name .. "." .. k .. " (body unavailable)")
      else
        T(name .. "." .. tostring(k) .. " = " .. q(v))
      end
    end,
    __call = function(_, ...)
      local args = table.pack and table.pack(...) or {n = select("#", ...), ...}
      local m = name:match("([^.]+)$")
      if m == "Connect" or m == "connect" or m == "Once" or m == "ConnectParallel" then
        local fn
        for i = 1, args.n do if type(args[i]) == "function" then fn = args[i] break end end
        local sig = name:match("^(.*)%.[^.]+$") or name
        T("-- callback registered: " .. sig .. ":" .. m .. " (body unavailable)")
        if not fn then error("sandbox: Connect expects a function",0) end
        if #callbacks >= 60 then emit("simulation_limit","Callback registration limit (60) reached; some handlers were not explored."); return mk("untracked_connection") end
        local cb={fn=fn,label=sig,active=true,once=m=="Once"}
        callbacks[#callbacks+1]=cb
        local connection="connection"..#callbacks
        connections[connection]=cb
        return mk(connection)
      end
      local text = call_text(name, args)
      note_urls(args)
      local owner = name:match("^(.*)%.[^.]+$")
      if (m == "new" and (owner == "Instance" or owner == "Drawing")) or m == "Clone" then
        nvar = nvar + 1
        local v = "v" .. nvar
        T("local " .. v .. " = " .. text)
        local instance=mk(v); classes[instance]=args[1]; return instance
      end
      T(text)
      if m=="Disconnect" and connections[owner] then connections[owner].active=false; return end
      if m=="new" and (owner=="Vector2" or owner=="Vector3") then return vector(text,owner,args[1],args[2],args[3]) end
      if m=="new" and owner=="UDim" then return udim(text,args[1],args[2]) end
      if owner=="UDim2" then
        if m=="new" then return udim2(text,args[1],args[2],args[3],args[4]) end
        if m=="fromOffset" then return udim2(text,0,args[1],0,args[2]) end
        if m=="fromScale" then return udim2(text,args[1],0,args[2],0) end
      end
      if m=="GetMouseLocation" then
        assume("Mouse position is simulated as (0,0); no real user input is available.")
        return vector(text,"Vector2",0,0)
      end
      if m == "HttpGet" or m == "HttpGetAsync" then
        local url = args[1]
        if proxies[url] then url = args[2] end
        if type(url) == "string" then
          emit("get", url)
          if HTTP[url] then return HTTP[url] end
        end
        return ""
      end
      if m == "RequestAsync" then
        note_urls(args)
        return {Body = "", StatusCode = 200, Success = true, Headers = {}}
      end
      if m == "HttpPost" or m == "PostAsync" or m == "GetAsync" then return "" end
      if m=="Play" or m=="Pause" or m=="Stop" or m=="Cancel" or m=="Destroy" or m=="Disconnect" then return end
      return mk(text)
    end,
    __tostring = function() return name end,
    __concat = function(a, b) return tostring(a) .. tostring(b) end,
    __add = function(a,b) return arithmetic(a,b,"+") end, __sub = function(a,b) return arithmetic(a,b,"-") end, __mul = function(a,b) return arithmetic(a,b,"*") end,
    __div = function(a,b) return arithmetic(a,b,"/") end, __mod = function() error("sandbox: unsupported proxy modulo",0) end, __pow = function() error("sandbox: unsupported proxy exponent",0) end,
    __unm = function(a) return arithmetic(a,-1,"*") end, __len = function() return 0 end,
    __lt = function() return false end, __le = function() return false end, __eq = proxy_equal,
  })
end

local function capture_load(s, chunkname)
  if type(s) ~= "string" then return nil, "bad chunk" end
  if s:byte(1) == 27 then emit("bincapture", s) return nil, "binary chunks are rejected" end
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
local function fix_format_args(fmt, ...)
  local n, args, i = select("#", ...), {...}, 0
  for flags, conv in real_str.gmatch(fmt, "%%([%-%+ #0%d%.]*)([%a%%])") do
    if conv ~= "%" then
      i = i + 1
      if proxies[args[i]] then
        if real_str.find("diouxXeEfgGc", conv, 1, true) then args[i] = 0 else args[i] = proxies[args[i]] end
      end
    end
  end
  return unpack(args, 1, n)
end
ws.format = function(fmt, ...)
  local ok, r = real_pcall(real_str.format, fmt, ...)
  if not ok then r = real_str.format(fmt, fix_format_args(fmt, ...)) end
  return note_const(r)
end
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
env.typeof = function(v) if values[v] then return values[v].kind end if proxies[v] then return "Instance" end return type(v) end
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
-- cooperative scheduler: spawn/delay/defer/wait run as coroutines, the script itself is thread 1
local managed = setmetatable({}, {__mode = "k"})
local threads = {}
local function newco(f)
  local co = real_cocreate(f)
  sethook(co, LIMIT, "", 10000)
  return co
end
local function spawn_thread(f, ...)
  local th = {co = newco(f)}
  managed[th.co] = true
  threads[#threads + 1] = th
  local ok, err = coroutine.resume(th.co, ...)
  if not ok then T("-- error in task: " .. tostring(err)); emit("task_error",tostring(err)) end
  return th
end
env.wait = function()
  local co = coroutine.running()
  if co and managed[co] then coroutine.yield() end
  return 0.03, 0
end
env.spawn = function(f, ...) if type(f) == "function" then return spawn_thread(f, ...) end end
local function later(f, ...) if type(f) == "function" then return spawn_thread(function(...) coroutine.yield() return f(...) end, ...) end end
env.delay = function(_, f, ...) return later(f, ...) end
-- pcall that lets wait() yield through it (Lua 5.1 cannot yield across a C pcall)
local function copcall(f, ...)
  if type(f) ~= "function" then return false, "attempt to call a " .. type(f) .. " value" end
  local outer = coroutine.running()
  local co = newco(f)
  managed[co] = outer and managed[outer] or nil
  local res = {coroutine.resume(co, ...)}
  while true do
    if coroutine.status(co) == "dead" then return unpack(res) end
    local back = {coroutine.yield(unpack(res, 2))}
    res = {coroutine.resume(co, unpack(back))}
  end
end
env.pcall = copcall
env.xpcall = function(f, h, ...)
  local res = {copcall(f, ...)}
  if res[1] then return unpack(res) end
  return false, h(res[2])
end
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
local function rec(name, ret)
  env[name] = function(...)
    local args = {n = select("#", ...), ...}
    T(call_text(name, args))
    note_urls(args)
    if type(ret) == "function" then return ret(args) end
    return ret
  end
end
for _, n in ipairs({"fireclickdetector", "firetouchinterest", "fireproximityprompt", "firesignal", "setfpscap", "setreadonly", "make_writeable",
  "setrawmetatable", "mouse1click", "mouse1press", "mouse1release", "mousemoverel", "mousescroll", "keypress", "keyrelease", "keyclick",
  "rconsoleprint", "rconsolewarn", "rconsoleerr", "rconsoleinfo", "rconsoleclear", "rconsolename", "consoleprint", "consoleclear",
  "setthreadidentity", "setidentity", "setthreadcontext", "appendfile", "delfile", "delfolder", "saveinstance", "setscriptable",
  "sethiddenproperty", "setsimulationradius", "replicatesignal", "toclipboard", "printidentity"}) do rec(n, nil) end
for _, n in ipairs({"getconnections", "getscripts", "getloadedmodules", "getinstances", "getnilinstances", "listfiles", "getallthreads"}) do rec(n, function() return {} end) end
for _, n in ipairs({"cloneref", "gethui", "getcallingscript", "getsenv", "getscriptclosure", "getproperties"}) do rec(n, function(a) return a[1] or mk(n .. "()") end) end
for _, n in ipairs({"getcustomasset", "getsynasset", "decompile", "getscriptbytecode", "getscripthash"}) do rec(n, "") end
for _, n in ipairs({"isrbxactive", "isexecutorclosure", "is_synapse_function", "iscclosure", "islclosure", "compareinstances", "isreadonly"}) do rec(n, false) end
rec("getthreadidentity", 7)
env.getidentity, env.getthreadcontext = env.getthreadidentity, env.getthreadidentity
env.getrawmetatable = function(o) return getmetatable(o) or {} end
env.clonefunction = function(f) return f end
env.messagebox = function(...) T(call_text("messagebox", {n = select("#", ...), ...})) return 1 end
env.shared, env._G, env._VERSION = {}, {}, "Luau"
env._G._G = env._G
for _, k in ipairs({"game", "workspace", "script", "Instance", "Vector3", "Vector2", "CFrame", "Color3", "BrickColor", "UDim2", "UDim", "Enum", "Ray", "Random", "TweenInfo", "NumberSequence", "NumberSequenceKeypoint", "ColorSequence", "ColorSequenceKeypoint", "Rect", "Region3", "Drawing", "task", "Players", "RunService", "UserInputService", "HttpService", "TweenService", "Lighting", "ReplicatedStorage", "StarterGui", "CoreGui", "TeleportService", "MarketplaceService", "Stats", "Workspace"}) do
  env[k] = mk(k)
end
env.task = {wait = env.wait, spawn = env.spawn, delay = env.delay, defer = function(f, ...) return later(f, ...) end, cancel = function() end}

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

local inputs={}
local function callback_args(cb,variant)
  local event=cb.label:match("([^.]+)$")
  if event=="RenderStepped" or event=="Heartbeat" or event=="PreRender" then
    assume("Frame events use a simulated delta time of 1/60 second.")
    return {n=1,1/60}
  end
  if event=="Stepped" then return {n=2,0,1/60} end
  if event=="MouseButton1Click" or event=="MouseButton2Click" then return {n=0} end
  if event=="MouseEnter" or event=="MouseLeave" or event=="MouseMoved" then return {n=2,0,0} end
  if event=="InputBegan" or event=="InputChanged" or event=="InputEnded" then
    assume("Input events use a synthetic InputObject at (0,0,0), KeyCode.E, and MouseButton1/MouseMovement; processed-input true/false variants are separate simulations.")
    local p=mk("arg1"); inputs[#inputs+1]=p
    properties[p].Position=vector("arg1.Position","Vector3",0,0,0)
    properties[p].Delta=vector("arg1.Delta","Vector3",0,0,0)
    properties[p].KeyCode=mk("Enum.KeyCode.E")
    properties[p].UserInputType=mk("Enum.UserInputType."..(event=="InputChanged" and "MouseMovement" or "MouseButton1"))
    properties[p].UserInputState=mk("Enum.UserInputState."..(event=="InputEnded" and "End" or event=="InputChanged" and "Change" or "Begin"))
    return {n=2,p,not variant}
  end
  if event=="Changed" then
    for _,p in ipairs(inputs) do properties[p].UserInputState=mk("Enum.UserInputState.End") end
    return {n=1,"UserInputState"}
  end
  assume("Unknown callback signatures receive synthetic arg1/arg2/arg3 recording proxies; this is exploratory input.")
  return variant and {n=3,mk("arg1"),false,false} or {n=3,mk("arg1"),mk("arg2"),mk("arg3")}
end

return function(source,scenario)
  if source:byte(1) == 27 then emit("error", "syntax: binary chunk rejected") return end
  local f, err = real_loadstring(source, "=obf")
  if not f then emit("error", "syntax: " .. tostring(err)) return end
  real_setfenv(f, env)
  local main = {co = newco(f)}
  local main_failed=false
  managed[main.co] = true
  threads[1] = main
  local function step(th)
    local ok, e = coroutine.resume(th.co)
    if not ok then
      if th == main then main_failed=true; emit("error", tostring(e)) else T("-- error in task: " .. tostring(e)); emit("task_error",tostring(e)) end
    end
  end
  local function pump(ticks)
    for _ = 1, ticks do
      if real_clock() > DEADLINE then return end
      local alive = false
      for i = 1, #threads do
        local th = threads[i]
        if coroutine.status(th.co) == "suspended" then alive = true step(th) end
      end
      if not alive then return end
    end
  end
  step(main)
  pump(60)
  if scenario then
    if main_failed then emit("simulation_error","Startup failed during callback replay; callback actions were not observed."); return callbacks end
    threads={}
    assume("Callback scenarios replay startup in a fresh runtime; setup background threads are paused during the selected callback.")
    for index=1,#scenario do
      local selected=scenario[index]
      local cb=callbacks[selected.index]
      if not cb or cb.label~=selected.label then emit("simulation_error","Callback registration changed or was unavailable during replay."); return callbacks end
      if not cb.active then emit("simulation_skip","Callback connection was disconnected before this scenario step."); return callbacks end
      if cb.once then cb.active=false end
      if index==#scenario then
        emit("callback_start",cb.label)
        ntrace,tcount=0,{}
      end
      local args=callback_args(cb,selected.variant)
      emit("callback_args",qt(args,2))
      spawn_thread(cb.fn,unpack(args,1,args.n))
      pump(20)
    end
  end
  local extra = {}
  for text, c in pairs(tcount) do if c > 3 then extra[#extra + 1] = text .. "  -- repeated " .. c .. " times" end end
  table.sort(extra)
  for _, line in ipairs(extra) do emit("trace", line) end
  return callbacks
end, env, function(s) DEADLINE = real_clock() + s end
'''


def main():
    job = json.loads(sys.stdin.read())
    # Apply limits inside the fresh worker, avoiding preexec_fn in a threaded bot.
    try:
        import resource
        memory = max(64, min(2048, int(job.get("memory_mb", 768)))) * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except (ImportError, OSError, ValueError):
        pass
    from lupa.lua51 import LuaRuntime

    output_bytes = 0

    def emit(kind, payload):
        nonlocal output_bytes
        kind = kind.decode("latin-1") if isinstance(kind, bytes) else kind
        if isinstance(payload, bytes):
            payload = payload.decode("latin-1")
        line = json.dumps({"k": kind, "v": payload}) + "\n"
        output_bytes += len(line)
        if output_bytes > 8 * 1024 * 1024:
            raise RuntimeError("sandbox output limit exceeded")
        sys.stdout.write(line)
        sys.stdout.flush()

    src = job["source"].encode("latin-1")
    if job.get("mode") == "check":
        lua = LuaRuntime(encoding=None, register_eval=False, register_builtins=False, unpack_returned_tuples=True)
        ok, err = lua.eval("function(s) local f, e = loadstring(s) return f ~= nil, e or '' end")(src)
        emit("ok" if ok else "error", err)
        return
    deadline = time.process_time() + job.get("seconds", 10)

    def runtime(sink):
        state = LuaRuntime(encoding=None, register_eval=False, register_builtins=False, unpack_returned_tuples=True)
        g = state.globals()
        loader = state.eval("function(code, ...) return assert(loadstring(code))(...) end")
        g[b"__HTTP_MAP"] = state.table_from({k.encode("latin-1"): v.encode("latin-1") for k, v in (job.get("http") or {}).items()})
        runner, env, extend = loader(
            BOOT.encode(), sink, g.loadstring, g.pcall, g.setfenv, state.eval("os.clock"), g.debug.sethook,
            g.coroutine.create, g.coroutine.wrap, None, deadline,
        )
        return state, runner, env, extend

    def text(value):
        return value.decode("latin-1") if isinstance(value, bytes) else value

    def startup_emit(kind, value):
        if text(kind) == "task_error":
            emit("task_error", {"phase": "startup", "error": text(value)})
        else:
            emit(kind, value)

    lua, runner, env, extend = runtime(startup_emit)
    registered = runner(src)
    initial_count = len(registered) if registered is not None else 0
    initial = [(i, text(registered[i][b"label"])) for i in range(1, initial_count + 1) if registered[i][b"active"]]
    if initial:
        emit("assumption", "Callback exploration uses limited synthetic inputs; cross-signal interactions and original control flow are not reconstructed.")
    plans = [[{"index": i, "label": label, "variant": variant}] for i, label in initial for variant in (False, True)]
    cursor = 0
    max_runs = 48
    while cursor < len(plans) and cursor < max_runs and time.process_time() < deadline:
        path = plans[cursor]
        cursor += 1
        selected = path[-1]
        started, errors, argument_description, skipped = False, [], "", False

        def replay_emit(kind, value):
            nonlocal started, argument_description, skipped
            kind, value = text(kind), text(value)
            if kind == "callback_start":
                started = True
                label = value.replace("\n", "\\n").replace("\r", "\\r")
                emit("trace", "-- callback: " + label + (" [second arg false]" if selected["variant"] else ""))
            elif kind == "simulation_error":
                errors.append(value)
                emit("simulation_limit", value)
            elif kind == "simulation_skip":
                skipped = True
            elif kind == "assumption":
                emit(kind, value)
            elif started:
                if kind == "callback_args":
                    argument_description = value
                elif kind == "capture":
                    emit("callback_capture", {"callback": selected["label"], "scenario": cursor, "source": value})
                elif kind == "task_error":
                    errors.append(value)
                    emit("task_error", {"phase": "callback", "callback": selected["label"], "scenario": cursor, "error": value})
                else:
                    emit(kind, value)

        replay_lua, replay, _, _ = runtime(replay_emit)
        scenario = replay_lua.table_from([
            replay_lua.table_from({key.encode(): value.encode("latin-1") if isinstance(value, str) else value for key, value in step.items()})
            for step in path
        ])
        discovered = replay(src, scenario)
        emit("callback_run", {"callback": selected["label"], "path": path, "arguments": argument_description,
                              "started": started, "errors": errors, "status": "errors" if errors else "disconnected" if skipped else "observed" if started else "unavailable"})
        if discovered is not None and started and not errors:
            known = {step["index"] for step in path}
            for i in range(initial_count + 1, len(discovered) + 1):
                if i in known or not discovered[i][b"active"]:
                    continue
                label = text(discovered[i][b"label"])
                for variant in (False, True):
                    if len(plans) < max_runs + 1:
                        plans.append(path + [{"index": i, "label": label, "variant": variant}])
        del discovered, replay, replay_lua, scenario
    if cursor < len(plans):
        emit("simulation_limit", "Callback exploration stopped at the shared CPU budget or 48-scenario limit; coverage is incomplete.")
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
