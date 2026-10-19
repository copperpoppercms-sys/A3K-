"""The simplifier must never change what a plain Lua program prints."""
import re

import pytest
from lupa.lua51 import LuaRuntime

from deobf import luaast, simplify

PROGRAMS = {
    "closure_counter": """
        local n = 0
        local function inc() n = n + 1 return n end
        print(inc(), inc(), n)
    """,
    "shadowing": """
        local a = 1
        do local a = 2 print(a) end
        local a = a + 10
        print(a)
    """,
    "while_const_cond_modified": """
        local i, t = 0, 5
        while i < t do i = i + 1 if i == 3 then t = 4 end end
        print(i, t)
    """,
    "repeat_scope": """
        local k = 0
        repeat local done = k >= 2 k = k + 1 until done
        print(k)
    """,
    "multi_assign_swap": """
        local a, b = 1, 2
        a, b = b, a
        print(a, b)
        local x, y, z = (function() return 7, 8 end)()
        print(x, y, z)
    """,
    "varargs": """
        local function f(...) local a, b = ... return select('#', ...), a, b end
        print(f(1, nil, 3))
        print((f(4, 5)))
    """,
    "string_arith_and_concat": """
        local s = "10" + 1
        print(s, "a" .. 1 .. 2, 1 .. "", 2^3, 7 % 3, -7 % 3)
    """,
    "numeric_formats": """
        print(1/3, 10/2, 3.0, 1e15, 2^53, 0.1 + 0.2, 100 * 1.5)
    """,
    "opaque_predicates": """
        local x = 5
        if x * 0 + 47 == 47 then print("real") else print("junk") end
        if not (28 >= 97) then print("also real") end
        while false do print("never") end
    """,
    "one_shot_loop": """
        local t1, t2, c = 10, 10, 0
        while t1 == t2 and c < 1 do
            t1 = t1 + 1 c = c + 1
            if t1 == t2 then print("junk") else print("real", t1, c) end
            break
        end
        print("after", t1, c)
    """,
    "loop_modified_upvalue": """
        local flag = true
        local function off() flag = false end
        if flag then print("on") end
        off()
        if flag then print("still on") else print("off") end
    """,
    "nested_breaks": """
        for i = 1, 3 do
            while true do
                if i == 2 then break end
                print("i", i)
                break
            end
        end
    """,
    "goto_free_continue_like": """
        local out = {}
        for i = 1, 5 do
            if i % 2 == 0 then else out[#out + 1] = i end
        end
        print(table.concat(out, ","))
    """,
    "method_and_tables": """
        local obj = {n = 1}
        function obj:add(k) self.n = self.n + k return self end
        print(obj:add(2):add(3).n, #{1, 2, nil, 4} >= 2)
        local t = {"a", "b", [10] = "c", x = 1}
        print(t[1], t[2], t[10], t.x, t[3])
    """,
    "const_table_lookup": """
        local T = {[1] = "x", [2] = "y"}
        print(T[1] .. T[2], T[3])
    """,
    "const_table_mutated": """
        local T = {"a", "b"}
        T[1] = "z"
        print(T[1], T[2])
    """,
    "iife": """
        local v = 1
        ;(function() v = v + 1 print("iife", v) end)()
        print(v)
        local r = (function(a, b) return a .. b end)("p", "q")
        print(r)
    """,
    "unused_with_side_effect": """
        local function boom() print("boom") return 1 end
        local unused = boom()
        local also_unused = 5
        print("done")
    """,
    "and_or_values": """
        local a = nil
        print(a and 1 or 2, false or nil, 0 and "zero is true", nil == false)
    """,
    "string_methods": """
        local s = "Hello"
        print(s:upper(), ("x"):rep(3), #s, s:sub(2, 3))
    """,
    "nested_function_upvalues": """
        local function make()
            local c = 0
            return function() c = c + 1 return c end
        end
        local a, b = make(), make()
        print(a(), a(), b())
    """,
}


def run_plain(src):
    # luac folds constant expressions at compile time but the VM computes -0 at run time; the sign of zero is not behaviour
    return re.sub(r"(?<![\w.])-0(?![\w.])", "0", _run_plain(src))


def _run_plain(src):
    lua = LuaRuntime(encoding="latin-1")
    lua.globals().SRC = src
    return lua.execute(
        """
        local out = {}
        local env = setmetatable({print = function(...)
            local t = {}
            for i = 1, select('#', ...) do t[#t + 1] = tostring((select(i, ...))) end
            out[#out + 1] = table.concat(t, "\\t")
        end}, {__index = _G})
        local f, err = loadstring(SRC)
        if not f then return "COMPILE ERROR: " .. tostring(err) end
        setfenv(f, env)
        debug.sethook(function() error("instruction limit", 0) end, "", 200000)
        local ok, e = pcall(f)
        debug.sethook()
        return table.concat(out, "\\n") .. (ok and "" or "\\nERR: " .. tostring(e):gsub("^.-:%d+: ", ""))
        """
    )


@pytest.mark.parametrize("name", sorted(PROGRAMS))
def test_simplifier_preserves_output(name):
    src = PROGRAMS[name]
    expected = run_plain(src)
    body = luaast.parse(src)
    body, _ = simplify.simplify(body)
    out = luaast.dump(body)
    assert run_plain(out) == expected, "\n--- simplified ---\n" + out


@pytest.mark.parametrize("name", sorted(PROGRAMS))
def test_printer_round_trip_preserves_output(name):
    src = PROGRAMS[name]
    assert run_plain(luaast.dump(luaast.parse(src))) == run_plain(src)


MORE = {
    "captured_then_assigned": """
        local x = 5
        local f = function() return x end
        x = 6
        print(f(), x)
    """,
    "branch_merge": """
        local a = 1
        local c = os.time() > 0
        if c then a = 2 else a = 3 end
        print(a)
        local b = 1
        if c then b = 1 end
        print(b)
    """,
    "loop_changes_var": """
        local n = 0
        for i = 1, 3 do n = n + i end
        print(n)
        local m = 10
        while m > 7 do m = m - 1 end
        print(m)
    """,
    "closure_called_in_loop": """
        local acc = 0
        local function add(v) acc = acc + v end
        for i = 1, 4 do add(i) end
        print(acc)
        if acc == 10 then print("ten") end
    """,
    "repeat_until_const": """
        local i = 0
        repeat i = i + 1 until i >= 3
        print(i)
    """,
    "nested_if_const_chain": """
        local a = 3
        if a == 1 then print("one") elseif a == 3 then print("three") else print("other") end
        if a == 4 then print("four") elseif a == 5 then print("five") end
        print("end")
    """,
    "recursive_local_function": """
        local function fact(n) if n <= 1 then return 1 end return n * fact(n - 1) end
        print(fact(5))
        local unused = function() return fact end
    """,
}


@pytest.mark.parametrize("name", sorted(MORE))
def test_more_programs(name):
    src = MORE[name]
    expected = run_plain(src)
    body, _ = simplify.simplify(luaast.parse(src))
    out = luaast.dump(body)
    assert run_plain(out) == expected, "\n--- simplified ---\n" + out


def _gen_program(rng):
    names = "abcde"
    lines = ["local %s = %d" % (n, rng.randint(-3, 9)) for n in names]
    lines.append("local guard = 0")

    def expr(depth=0):
        r = rng.random()
        if depth > 2 or r < 0.3:
            return str(rng.randint(-5, 9)) if rng.random() < 0.5 else rng.choice(names)
        op = rng.choice(["+", "-", "*"])
        return "(%s %s %s)" % (expr(depth + 1), op, expr(depth + 1))

    def cond():
        return "%s %s %s" % (expr(1), rng.choice(["==", "~=", "<", "<=", ">", ">="]), expr(1))

    def stmts(depth):
        out = []
        for _ in range(rng.randint(1, 4)):
            r = rng.random()
            if r < 0.45 or depth > 1:
                out.append("%s = %s" % (rng.choice(names), expr()))
            elif r < 0.7:
                els = (" else " + " ".join(stmts(depth + 1))) if rng.random() < 0.6 else ""
                out.append("if %s then %s%s end" % (cond(), " ".join(stmts(depth + 1)), els))
            elif r < 0.85:
                g = "g%d" % rng.randint(0, 10 ** 6)
                out.append("do local %s = 0 while %s < %d do %s = %s + 1 %s end end" % (g, g, rng.randint(0, 3), g, g, " ".join(stmts(depth + 1))))
            else:
                out.append("do local %s = %s %s end" % (rng.choice(names), expr(), " ".join(stmts(depth + 1))))
        return out

    lines += stmts(0)
    lines.append("print(%s)" % ", ".join(names))
    return "\n".join(lines)


def test_random_programs_keep_their_output():
    import random

    rng = random.Random(1234)
    for n in range(300):
        src = _gen_program(rng)
        expected = run_plain(src)
        body, _ = simplify.simplify(luaast.parse(src))
        out = luaast.dump(body)
        assert run_plain(out) == expected, "program %d\n%s\n--- simplified ---\n%s" % (n, src, out)


def _gen_program2(rng):
    """Functions, closures, tables, strings, numeric for, repeat, early return."""
    names = "abcde"
    lines = ["local %s = %d" % (n, rng.randint(-3, 9)) for n in names]
    fn_count = [0]
    tbl_count = [0]

    def expr(depth=0, extra=()):
        pool = list(names) + list(extra)
        r = rng.random()
        if depth > 2 or r < 0.3:
            return str(rng.randint(-5, 9)) if rng.random() < 0.5 else rng.choice(pool)
        return "(%s %s %s)" % (expr(depth + 1, extra), rng.choice(["+", "-", "*"]), expr(depth + 1, extra))

    def cond(extra=()):
        return "%s %s %s" % (expr(1, extra), rng.choice(["==", "~=", "<", "<=", ">", ">="]), expr(1, extra))

    def stmts(depth, in_func=False):
        out = []
        for _ in range(rng.randint(1, 3)):
            r = rng.random()
            if depth > 1 or r < 0.25:
                out.append("%s = %s" % (rng.choice(names), expr()))
            elif r < 0.4:
                out.append("if %s then %s end" % (cond(), " ".join(stmts(depth + 1, in_func))))
            elif r < 0.5:
                i = "i%d" % rng.randint(0, 999)
                out.append("for %s = 1, %d do %s = %s + %s %s end" % (i, rng.randint(0, 3), rng.choice(names), rng.choice(names), i, " ".join(stmts(depth + 1, in_func))))
            elif r < 0.6:
                g = "r%d" % rng.randint(0, 99999)
                out.append("do local %s = 0 repeat %s = %s + 1 %s until %s >= %d end" % (g, g, g, " ".join(stmts(depth + 1, in_func)), g, rng.randint(1, 3)))
            elif r < 0.72:
                fn_count[0] += 1
                f = "f%d" % fn_count[0]
                out.append("local function %s(p, q) local t = p * 2 %s return t + q + %s end" % (f, " ".join(stmts(depth + 1, True)), rng.choice(names)))
                out.append("%s = %s(%s, %s)" % (rng.choice(names), f, expr(), expr()))
            elif r < 0.82:
                fn_count[0] += 1
                m = "m%d" % fn_count[0]
                out.append("local function %s() local c = %d return function() c = c + %s return c end end" % (m, rng.randint(0, 5), rng.randint(1, 3)))
                out.append("do local k = %s() %s = k() + k() end" % (m, rng.choice(names)))
            elif r < 0.92:
                tbl_count[0] += 1
                t = "T%d" % tbl_count[0]
                out.append("local %s = {%d, %d, %d}" % (t, rng.randint(0, 9), rng.randint(0, 9), rng.randint(0, 9)))
                if rng.random() < 0.5:
                    out.append("%s[%d] = %s" % (t, rng.randint(1, 3), expr()))
                out.append("%s = %s[%d] + #%s" % (rng.choice(names), t, rng.randint(1, 3), t))
            else:
                out.append('do local s = "v" .. %s %s = #s end' % (rng.choice(names), rng.choice(names)))
        return out

    lines += stmts(0)
    lines.append("print(%s)" % ", ".join(names))
    return "\n".join(lines)


def test_random_programs_with_functions_and_tables():
    import random

    rng = random.Random(4321)
    for n in range(200):
        src = _gen_program2(rng)
        expected = run_plain(src)
        body, _ = simplify.simplify(luaast.parse(src))
        out = luaast.dump(body)
        assert run_plain(out) == expected, "program %d\n%s\n--- simplified ---\n%s" % (n, src, out)
