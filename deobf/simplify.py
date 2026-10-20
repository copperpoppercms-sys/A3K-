"""AST simplifier for non-vm obfuscation: constant folding, flow-sensitive constant propagation, opaque-predicate and
dead-branch removal, one-shot loop unrolling, junk removal, readable renaming. Pure Lua semantics only; every use
is meant to be checked by comparing runs before and after (see astclean)."""
import math
import re

from . import luaast as A

_MISSING = object()


# ------------------------------------------------------------------ constants
def const_of(e):
    """(True, value) for nil/bool/number/string literals. nil is represented by None."""
    t = type(e)
    if t is A.Nil:
        return True, None
    if t is A.Bool:
        return True, e.value
    if t is A.Num:
        return True, e.value
    if t is A.Str:
        return True, e.value
    return False, None


def make_const(v):
    if v is None:
        return A.Nil()
    if v is True or v is False:
        return A.Bool(v)
    if isinstance(v, str):
        return A.Str(v)
    return A.Num(_norm_num(v))


def _neg_zero(v):
    return isinstance(v, float) and v == 0 and math.copysign(1.0, v) < 0


def _norm_num(v):
    if _neg_zero(v):
        return v  # Lua numbers are doubles: -0 prints as "-0"
    if isinstance(v, float) and v == v and abs(v) < 2 ** 53 and v == int(v):
        return int(v)
    return v


def truthy(v):
    return not (v is None or v is False)


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _num_str(v):
    if _neg_zero(v):
        return "-0"
    if isinstance(v, float) and v != int(v):
        return "%.14g" % v
    return A.fmt_num(v)


def fold_bin(op, a, b):
    """Evaluate a binary op on constants; _MISSING when it would error or is not safely foldable."""
    if op == "and":
        return b if truthy(a) else a
    if op == "or":
        return a if truthy(a) else b
    if op == "==":
        return type(a) is type(b) and a == b if not (_is_num(a) and _is_num(b)) else a == b
    if op == "~=":
        r = fold_bin("==", a, b)
        return not r
    if _is_num(a) and _is_num(b):
        try:
            if op == "+":
                v = a + b
                if v == 0:
                    v = float(a) + float(b)
            elif op == "-":
                v = a - b
                if v == 0:
                    v = float(a) - float(b)
            elif op == "*":
                v = a * b
                if v == 0:
                    v = float(a) * float(b)
            elif op == "/":
                if b == 0:
                    return _MISSING
                v = a / b
            elif op == "%":
                if b == 0:
                    return _MISSING
                v = a - math.floor(a / b) * b if isinstance(a, float) or isinstance(b, float) else a % b
            elif op == "//":
                if b == 0:
                    return _MISSING
                v = math.floor(a / b)
            elif op == "^":
                v = float(a) ** float(b)
            elif op == "<":
                return a < b
            elif op == "<=":
                return a <= b
            elif op == ">":
                return a > b
            elif op == ">=":
                return a >= b
            elif op == "..":
                return _num_str(a) + _num_str(b)
            else:
                return _MISSING
        except (OverflowError, ValueError, ZeroDivisionError):
            return _MISSING
        if isinstance(v, complex) or v != v or v in (math.inf, -math.inf) or abs(v) > 2 ** 53 or _neg_zero(v):
            return _MISSING  # a literal can never produce -0, so leave the expression for the runtime
        return _norm_num(v)
    if isinstance(a, str) and isinstance(b, str):
        if op == "..":
            return a + b
        if op in ("<", "<=", ">", ">="):
            return {"<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[op]
    if op == ".." and ((isinstance(a, str) and _is_num(b)) or (_is_num(a) and isinstance(b, str))):
        return (a if isinstance(a, str) else _num_str(a)) + (b if isinstance(b, str) else _num_str(b))
    return _MISSING


def fold_un(op, a):
    if op == "not":
        return not truthy(a)
    if op == "-" and _is_num(a):
        return _MISSING if a == 0 else _norm_num(-a)
    if op == "#" and isinstance(a, str):
        return len(a)
    return _MISSING


# ------------------------------------------------------------------ analysis
def walk_exprs(e):
    """Yield e and every sub-expression (not descending into function bodies)."""
    yield e
    t = type(e)
    if t in (A.Paren,):
        yield from walk_exprs(e.e)
    elif t is A.Un:
        yield from walk_exprs(e.e)
    elif t is A.Bin:
        yield from walk_exprs(e.l)
        yield from walk_exprs(e.r)
    elif t is A.Index:
        yield from walk_exprs(e.obj)
        yield from walk_exprs(e.key)
    elif t is A.Call:
        yield from walk_exprs(e.func)
        for a in e.args:
            yield from walk_exprs(a)
    elif t is A.Method:
        yield from walk_exprs(e.obj)
        for a in e.args:
            yield from walk_exprs(a)
    elif t is A.Table:
        for _, k, v in e.items:
            if k is not None:
                yield from walk_exprs(k)
            yield from walk_exprs(v)


class Analysis:
    """reads / writes / assigned-in-closure per Local, over the whole chunk."""

    def __init__(self, body):
        for loc in self._locals(body):
            loc.reads = loc.writes = 0
            loc.assigned_in_closure = False
        self.skip = None
        self.stmts(body, 0)

    @staticmethod
    def _locals(body):
        out = []

        def st(b):
            for s in b:
                t = type(s)
                if t is A.LocalStmt:
                    out.extend(s.vars)
                elif t is A.LocalFunc:
                    out.append(s.var)
                    fn(s.func)
                elif t is A.NumFor:
                    out.append(s.var)
                elif t is A.GenFor:
                    out.extend(s.vars)
                for e in _stmt_exprs(s):
                    for x in walk_exprs(e):
                        if type(x) is A.Func:
                            fn(x)
                for sub in _stmt_blocks(s):
                    st(sub)
                if t is A.FuncStmt:
                    fn(s.func)

        def fn(f):
            out.extend(f.params)
            st(f.body)

        st(body)
        return out

    def expr(self, e, fid):
        for x in walk_exprs(e):
            t = type(x)
            if t is A.Name and x.local is not None:
                if x.local is not self.skip:
                    x.local.reads += 1
            elif t is A.Func:
                self.func(x)

    def func(self, f):
        saved = self.skip
        self.stmts(f.body, f.fid)
        self.skip = saved

    def target(self, t, fid):
        if type(t) is A.Name:
            if t.local is not None:
                t.local.writes += 1
                if t.local.func != fid:
                    t.local.assigned_in_closure = True
        else:
            self.expr(t, fid)

    def stmts(self, body, fid):
        for s in body:
            t = type(s)
            if t is A.LocalFunc:
                saved = self.skip
                self.skip = s.var
                self.func(s.func)
                self.skip = saved
                continue
            if t is A.FuncStmt:
                self.target(s.target, fid) if type(s.target) is A.Name else self.expr(s.target, fid)
                self.func(s.func)
                continue
            if t is A.Assign:
                for tg in s.targets:
                    self.target(tg, fid)
                for e in s.exprs:
                    self.expr(e, fid)
                continue
            for e in _stmt_exprs(s):
                self.expr(e, fid)
            for sub in _stmt_blocks(s):
                self.stmts(sub, fid)


def _stmt_exprs(s):
    t = type(s)
    if t is A.LocalStmt or t is A.Return:
        return list(s.exprs)
    if t is A.Assign:
        return list(s.targets) + list(s.exprs)
    if t is A.CallStmt:
        return [s.call]
    if t is A.While:
        return [s.cond]
    if t is A.Repeat:
        return [s.cond]
    if t is A.If:
        return [c for c, _ in s.clauses]
    if t is A.NumFor:
        return [e for e in (s.start, s.stop, s.step) if e is not None]
    if t is A.GenFor:
        return list(s.exprs)
    return []


def _stmt_blocks(s):
    t = type(s)
    if t in (A.Do, A.While, A.Repeat, A.NumFor, A.GenFor):
        return [s.body]
    if t is A.If:
        return [b for _, b in s.clauses] + ([s.orelse] if s.orelse is not None else [])
    return []


BUILTIN_GLOBALS = frozenset("""print pairs ipairs next tostring tonumber type select unpack pcall xpcall error assert setmetatable getmetatable
rawget rawset rawequal rawlen collectgarbage loadstring load tick wait spawn delay warn typeof time""".split())
BUILTIN_LIBS = frozenset("string table math os bit32 bit coroutine utf8".split())


def builtin_ref(e):
    """True for a bare standard global (print) or a standard library member (string.char)."""
    if type(e) is A.Name:
        return e.local is None and e.name in BUILTIN_GLOBALS
    if type(e) is A.Index and type(e.obj) is A.Name and e.obj.local is None and e.obj.name in BUILTIN_LIBS and type(e.key) is A.Str:
        return True
    return False


def pure(e):
    if builtin_ref(e):
        return True
    t = type(e)
    if t in (A.Nil, A.Bool, A.Num, A.Str, A.Func):
        return True
    if t is A.Name:
        return e.local is not None
    if t is A.Paren:
        return pure(e.e)
    if t is A.Table:
        return all((k is None or pure(k)) and pure(v) for _, k, v in e.items)
    return False


def assigned_locals(body):
    """Locals written anywhere inside `body` (including nested functions)."""
    out = set()

    def st(b):
        for s in b:
            t = type(s)
            if t is A.Assign:
                for tg in s.targets:
                    if type(tg) is A.Name and tg.local is not None:
                        out.add(tg.local)
            elif t is A.FuncStmt and type(s.target) is A.Name and s.target.local is not None:
                out.add(s.target.local)
            elif t is A.LocalStmt:
                out.update(s.vars)
            elif t is A.LocalFunc:
                out.add(s.var)
            elif t is A.NumFor:
                out.add(s.var)
            elif t is A.GenFor:
                out.update(s.vars)
            for e in _stmt_exprs(s):
                for x in walk_exprs(e):
                    if type(x) is A.Func:
                        st(x.body)
            for sub in _stmt_blocks(s):
                st(sub)
            if t in (A.LocalFunc, A.FuncStmt):
                st(s.func.body)

    st(body)
    return out


def _has_break(body, depth=0):
    """True when `body` contains a break/continue/goto that could leave an enclosing loop (not counting the last statement)."""
    for s in body:
        t = type(s)
        if t in (A.Break, A.Continue, A.Goto):
            return True
        if t is A.If:
            for _, b in s.clauses:
                if _has_break(b):
                    return True
            if s.orelse is not None and _has_break(s.orelse):
                return True
        elif t is A.Do and _has_break(s.body):
            return True
    return False


def _declares(body):
    return any(type(s) in (A.LocalStmt, A.LocalFunc, A.Label) for s in body)


# ------------------------------------------------------------------ simplifier
class Simplifier:
    def __init__(self):
        self.changed = False
        self.gconst = {}  # Local -> const value, for locals that are never reassigned
        self.alias = {}  # Local -> builtin expression the local only ever holds
        self.ctables = {}  # Local -> {const key: const value} for tables that are only ever indexed with constants

    # expressions ---------------------------------------------------------
    def sub(self, e, env):
        t = type(e)
        if t is A.Name:
            if e.local is not None:
                if e.local in self.alias:
                    self.changed = True
                    return clone(self.alias[e.local])
                if env is not None and e.local in env:
                    self.changed = True
                    return make_const(env[e.local])
                if e.local in self.gconst:
                    self.changed = True
                    return make_const(self.gconst[e.local])
            return e
        if t is A.Paren:
            inner = self.sub(e.e, env)
            # parentheses only matter for truncating a multi-value expression; the printer re-adds them for precedence
            if type(inner) not in (A.Call, A.Method, A.Vararg):
                self.changed = True
                return inner
            e.e = inner
            return e
        if t is A.Un:
            e.e = self.sub(e.e, env)
            ok, v = const_of(e.e)
            if ok:
                r = fold_un(e.op, v)
                if r is not _MISSING:
                    self.changed = True
                    return make_const(r)
            return e
        if t is A.Bin:
            e.l = self.sub(e.l, env)
            if e.op in ("and", "or"):
                ok, v = const_of(e.l)
                if ok:
                    self.changed = True
                    if e.op == "and":
                        return self.sub(e.r, env) if truthy(v) else e.l
                    return e.l if truthy(v) else self.sub(e.r, env)
                e.r = self.sub(e.r, env)
                return e
            e.r = self.sub(e.r, env)
            oka, a = const_of(e.l)
            okb, b = const_of(e.r)
            if oka and okb:
                r = fold_bin(e.op, a, b)
                if r is not _MISSING:
                    self.changed = True
                    return make_const(r)
            return e
        if t is A.Index:
            e.obj = self.sub(e.obj, env)
            e.key = self.sub(e.key, env)
            if type(e.obj) is A.Name and e.obj.local in self.ctables:
                ok, k = const_of(e.key)
                tbl = self.ctables[e.obj.local]
                if ok and k is not None and not isinstance(k, bool) and k in tbl:
                    self.changed = True
                    return make_const(tbl[k])
            return e
        if t is A.Call:
            e.func = self.sub(e.func, env)
            e.args = [self.sub(a, env) for a in e.args]
            return e
        if t is A.Method:
            e.obj = self.sub(e.obj, env)
            e.args = [self.sub(a, env) for a in e.args]
            return e
        if t is A.Table:
            e.items = [(k, self.sub(key, env) if key is not None else None, self.sub(v, env)) for k, key, v in e.items]
            return e
        if t is A.Func:
            self.func(e)
            return e
        return e

    def inlinable(self, c):
        """(function() ... end)() or (function(...) ... end)(...) whose body never returns or reads its arguments."""
        f = c.func
        if f.params or any(type(a) not in (A.Vararg,) for a in c.args):
            return False
        if c.args and not f.vararg:
            return False

        def clean(body):
            for s in body:
                if type(s) is A.Return:
                    return False
                for e in _stmt_exprs(s):
                    for x in walk_exprs(e):
                        if type(x) is A.Vararg:
                            return False
                for sub in _stmt_blocks(s):
                    if not clean(sub):
                        return False
            return True

        return clean(f.body)

    def func(self, f):
        f.body = self.block(f.body, None)

    # statements ----------------------------------------------------------
    def block(self, stmts, env):
        env = {} if env is None else env
        out = []
        for st in stmts:
            out.extend(self.stmt(st, env))
            if out and type(out[-1]) in (A.Return, A.Break, A.Continue, A.Goto):
                if not any(type(s) is A.Label for s in stmts):
                    if len(out) != len(stmts):
                        self.changed = True
                    break
        return out

    def drop_env(self, env, locs):
        for l in locs:
            env.pop(l, None)

    def splice(self, body):
        """A body that replaces a statement in place: wrap in do..end only if it declares locals."""
        if not body:
            return []
        if _declares(body):
            return [A.Do(body)]
        return body

    def stmt(self, s, env):
        t = type(s)
        if t is A.LocalStmt:
            s.exprs = [self.sub(e, env) for e in s.exprs]
            consts = [const_of(e) for e in s.exprs]
            multi = bool(s.exprs) and type(s.exprs[-1]) in (A.Call, A.Method, A.Vararg) and len(s.exprs) < len(s.vars)
            for i, v in enumerate(s.vars):
                env.pop(v, None)
                if i < len(s.exprs) and consts[i][0] and not v.assigned_in_closure and not (multi and i == len(s.exprs) - 1):
                    if v.writes == 0:
                        self.gconst[v] = consts[i][1]
                    env[v] = consts[i][1]
                elif not s.exprs and not v.assigned_in_closure:
                    env[v] = None
            return [s]
        if t is A.Assign:
            s.exprs = [self.sub(e, env) for e in s.exprs]
            for tg in s.targets:
                if type(tg) is A.Index:
                    tg.obj = self.sub(tg.obj, env)
                    tg.key = self.sub(tg.key, env)
            multi = bool(s.exprs) and type(s.exprs[-1]) in (A.Call, A.Method, A.Vararg)
            for i, tg in enumerate(s.targets):
                if type(tg) is A.Name and tg.local is not None:
                    l = tg.local
                    if i < len(s.exprs) and not (multi and i >= len(s.exprs) - 1) and const_of(s.exprs[i])[0] and not l.assigned_in_closure:
                        env[l] = const_of(s.exprs[i])[1]
                    else:
                        env.pop(l, None)
            return [s]
        if t is A.CallStmt:
            c = s.call
            if type(c) is A.Call and type(c.func) is A.Func and self.inlinable(c):
                self.changed = True
                return self.splice(self.block(c.func.body, dict(env)))
            s.call = self.sub(s.call, env)
            return [s]
        if t is A.Return:
            s.exprs = [self.sub(e, env) for e in s.exprs]
            return [s]
        if t is A.Do:
            inner = self.block(s.body, dict(env))
            self.drop_env(env, assigned_locals(s.body))
            if not inner:
                self.changed = True
                return []
            if not _declares(inner):
                self.changed = True
                return inner
            s.body = inner
            return [s]
        if t is A.If:
            return self.if_stmt(s, env)
        if t is A.While:
            return self.while_stmt(s, env)
        if t is A.Repeat:
            gone = assigned_locals(s.body)
            benv = {k: v for k, v in env.items() if k not in gone}
            s.body = self.block(s.body, benv)
            s.cond = self.sub(s.cond, None)
            self.drop_env(env, gone)
            return [s]
        if t is A.NumFor:
            s.start = self.sub(s.start, env)
            s.stop = self.sub(s.stop, env)
            if s.step is not None:
                s.step = self.sub(s.step, env)
            gone = assigned_locals(s.body)
            s.body = self.block(s.body, {k: v for k, v in env.items() if k not in gone})
            self.drop_env(env, gone)
            if not s.body and all(const_of(x)[0] for x in (s.start, s.stop) + ((s.step,) if s.step is not None else ())):
                self.changed = True
                return []
            return [s]
        if t is A.GenFor:
            s.exprs = [self.sub(e, env) for e in s.exprs]
            gone = assigned_locals(s.body)
            s.body = self.block(s.body, {k: v for k, v in env.items() if k not in gone})
            self.drop_env(env, gone)
            return [s]
        if t is A.FuncStmt:
            if type(s.target) is A.Name and s.target.local is not None:
                env.pop(s.target.local, None)
            elif type(s.target) is A.Index:
                s.target.obj = self.sub(s.target.obj, env)
            self.func(s.func)
            return [s]
        if t is A.LocalFunc:
            env.pop(s.var, None)
            self.func(s.func)
            return [s]
        if t is A.Label:
            env.clear()
            return [s]
        return [s]

    def if_stmt(self, s, env):
        kept = []  # (cond, body) clauses that may run
        final_else = s.orelse
        decided = None
        for cond, body in s.clauses:
            cond = self.sub(cond, env)
            ok, v = const_of(cond)
            if ok:
                self.changed = True
                if truthy(v):
                    decided = body
                    break
                continue  # never runs
            kept.append((cond, body))
        if decided is not None:
            final_else = decided
        if not kept:
            body = final_else if final_else is not None else []
            inner = self.block(body, dict(env))
            self.drop_env(env, assigned_locals(body))
            if decided is None and final_else is None:
                return []
            # straight-line code: constants it established stay valid
            return self.splice(inner) if inner else []
        branches = [b for _, b in kept] + ([final_else] if final_else is not None else [])
        envs = []
        new_clauses = []
        for cond, body in kept:
            e2 = dict(env)
            new_clauses.append((cond, self.block(body, e2)))
            envs.append(e2)
        new_else = None
        if final_else is not None:
            e2 = dict(env)
            new_else = self.block(final_else, e2)
            envs.append(e2)
        else:
            envs.append(dict(env))
        for l in list(env):
            if not all(l in e and e[l] == env[l] and type(e[l]) is type(env[l]) for e in envs):
                env.pop(l, None)
        gone = set()
        for b in branches:
            gone |= assigned_locals(b)
        for l in gone:
            env.pop(l, None)
        s.clauses = new_clauses
        s.orelse = new_else if new_else else None
        return [s]

    def while_stmt(self, s, env):
        # first iteration sees the entry state
        entry_cond = self.sub(_clone(s.cond), env)
        ok, v = const_of(entry_cond)
        gone = assigned_locals(s.body)
        if ok and not truthy(v):
            self.changed = True
            return []
        if ok and truthy(v):
            # does the first iteration end in `break` once the opaque predicates inside are resolved?
            saved = dict(self.gconst)
            trial = self.block(clone(s.body), dict(env))
            if trial and type(trial[-1]) is A.Break and not _has_break(trial[:-1]):
                self.changed = True  # runs exactly once
                self.drop_env(env, gone)
                return self.splice(trial[:-1])
            self.gconst = saved  # facts derived from the first-iteration state do not hold for later iterations
        benv = {k: val for k, val in env.items() if k not in gone}
        s.cond = self.sub(s.cond, benv)
        okc, vc = const_of(s.cond)
        if okc and not truthy(vc):
            self.changed = True
            self.drop_env(env, gone)
            return []
        s.body = self.block(s.body, benv)
        self.drop_env(env, gone)
        return [s]


def clone(n):
    """Deep-copy an AST node / list. Local objects are shared so the copy still points at the same variables."""
    if isinstance(n, list):
        return [clone(x) for x in n]
    if isinstance(n, tuple):
        return tuple(clone(x) for x in n)
    if isinstance(n, A.Node):
        c = object.__new__(type(n))
        for f in n.__slots__:
            setattr(c, f, clone(getattr(n, f)))
        return c
    return n


def _clone(e):
    t = type(e)
    if t in (A.Nil, A.Bool, A.Num, A.Str, A.Vararg, A.Name):
        return e if t is not A.Name else A.Name(e.local, e.name)
    if t is A.Paren:
        return A.Paren(_clone(e.e))
    if t is A.Un:
        return A.Un(e.op, _clone(e.e))
    if t is A.Bin:
        return A.Bin(e.op, _clone(e.l), _clone(e.r))
    if t is A.Index:
        return A.Index(_clone(e.obj), _clone(e.key))
    if t is A.Call:
        return A.Call(_clone(e.func), [_clone(a) for a in e.args])
    if t is A.Method:
        return A.Method(_clone(e.obj), e.name, [_clone(a) for a in e.args])
    if t is A.Table:
        return A.Table([(k, _clone(key) if key is not None else None, _clone(v)) for k, key, v in e.items])
    return e  # functions are shared; they are only read when cloned for a condition


# ------------------------------------------------------------------ dead code
def remove_unused(body):
    """Drop locals / local functions / assignments that nothing reads. Returns (body, removed_count)."""
    Analysis(body)
    removed = 0

    def block(b):
        nonlocal removed
        out = []
        for s in b:
            t = type(s)
            if t is A.LocalStmt and all(v.reads == 0 and v.writes == 0 for v in s.vars) and all(pure(e) for e in s.exprs):
                removed += 1
                continue
            if t is A.LocalFunc and s.var.reads == 0 and s.var.writes == 0:
                removed += 1
                continue
            if t is A.Assign and len(s.targets) == 1 and len(s.exprs) == 1 and type(s.targets[0]) is A.Name and s.targets[0].local is not None \
                    and s.targets[0].local.reads == 0 and pure(s.exprs[0]):
                removed += 1
                continue
            for sub_i, sub in enumerate(_stmt_blocks(s)):
                pass
            if t is A.Do:
                s.body = block(s.body)
                if not s.body:
                    removed += 1
                    continue
            elif t in (A.While, A.Repeat, A.NumFor, A.GenFor):
                s.body = block(s.body)
            elif t is A.If:
                s.clauses = [(c, block(bd)) for c, bd in s.clauses]
                if s.orelse is not None:
                    s.orelse = block(s.orelse) or None
            for e in _stmt_exprs(s):
                for x in walk_exprs(e):
                    if type(x) is A.Func:
                        x.body = block(x.body)
            if t in (A.LocalFunc, A.FuncStmt):
                s.func.body = block(s.func.body)
            out.append(s)
        return out

    return block(body), removed


def find_aliases(body):
    """Locals whose single value is a standard builtin (Hercules/Prometheus alias every builtin they use)."""
    Analysis(body)
    alias = {}

    def block(b):
        declared = set()
        for s in b:
            t = type(s)
            if t is A.LocalStmt:
                if not s.exprs:
                    declared.update(s.vars)
                elif len(s.exprs) == len(s.vars):
                    for v, e in zip(s.vars, s.exprs):
                        if builtin_ref(e) and v.writes == 0:
                            alias[v] = e
            elif t is A.Assign and len(s.targets) == len(s.exprs):
                for tg, e in zip(s.targets, s.exprs):
                    if type(tg) is A.Name and tg.local in declared and tg.local.writes == 1 and builtin_ref(e):
                        alias[tg.local] = e
            for sub in _stmt_blocks(s):
                block(sub)
            for e in _stmt_exprs(s):
                for x in walk_exprs(e):
                    if type(x) is A.Func:
                        block(x.body)
            if t in (A.LocalFunc, A.FuncStmt):
                block(s.func.body)

    block(body)
    return alias


def find_const_tables(body):
    """`local t = {k = const, ...}` that is never written and only read as t[const]."""
    Analysis(body)
    cand = {}
    idx_reads = {}

    def visit_expr(e):
        for x in walk_exprs(e):
            if type(x) is A.Index and type(x.obj) is A.Name and x.obj.local is not None and const_of(x.key)[0]:
                idx_reads[x.obj.local] = idx_reads.get(x.obj.local, 0) + 1
            if type(x) is A.Func:
                block(x.body)

    mutated = set()

    def block(b):
        for s in b:
            if type(s) is A.Assign:
                for tg in s.targets:
                    root = tg
                    while type(root) is A.Index:
                        root = root.obj
                    if type(root) is A.Name and root.local is not None and type(tg) is A.Index:
                        mutated.add(root.local)
            if type(s) is A.FuncStmt and type(s.target) is A.Index:
                root = s.target
                while type(root) is A.Index:
                    root = root.obj
                if type(root) is A.Name and root.local is not None:
                    mutated.add(root.local)
            if type(s) is A.LocalStmt and len(s.vars) == 1 and len(s.exprs) == 1 and type(s.exprs[0]) is A.Table:
                tbl, ok, pos = {}, True, 0
                for kind, k, v in s.exprs[0].items:
                    okv, val = const_of(v)
                    if not okv or val is None:
                        ok = False
                        break
                    if kind == "pos":
                        pos += 1
                        tbl[pos] = val
                    else:
                        okk, key = const_of(k)
                        if not okk or key is None or isinstance(key, bool):
                            ok = False
                            break
                        tbl[key] = val
                if ok and tbl and s.vars[0].writes == 0:
                    cand[s.vars[0]] = tbl
            for e in _stmt_exprs(s):
                visit_expr(e)
            for sub in _stmt_blocks(s):
                block(sub)
            if type(s) in (A.LocalFunc, A.FuncStmt):
                block(s.func.body)

    block(body)
    return {l: t for l, t in cand.items() if l not in mutated and idx_reads.get(l, 0) == l.reads}


def strip_antitamper(body):
    """Remove `do ... end` blocks that only run an integrity check (they raise "Tamper Detected" when edited)."""
    removed = 0

    def has_marker(s):
        for e in _stmt_exprs(s):
            for x in walk_exprs(e):
                if type(x) is A.Str and "tamper" in x.value.lower():
                    return True
                if type(x) is A.Func and any(has_marker(st) for st in x.body):
                    return True
        for sub in _stmt_blocks(s):
            if any(has_marker(st) for st in sub):
                return True
        if type(s) in (A.LocalFunc, A.FuncStmt) and any(has_marker(st) for st in s.func.body):
            return True
        return False

    def is_check_block(s):
        """declarations followed by one argument-less call to a local function, with the tamper marker inside"""
        b = s.body
        if len(b) < 2 or not has_marker(s):
            return False
        last = b[-1]
        if not (type(last) is A.CallStmt and type(last.call) is A.Call and not last.call.args and type(last.call.func) is A.Name and last.call.func.local is not None):
            return False
        return all(type(x) in (A.LocalStmt, A.LocalFunc) for x in b[:-1])

    def block(b):
        nonlocal removed
        out = []
        for s in b:
            if type(s) is A.Do and is_check_block(s):
                removed += 1
                continue
            for sub in _stmt_blocks(s):
                sub[:] = block(sub)
            for e in _stmt_exprs(s):
                for x in walk_exprs(e):
                    if type(x) is A.Func:
                        x.body = block(x.body)
            if type(s) in (A.LocalFunc, A.FuncStmt):
                s.func.body = block(s.func.body)
            out.append(s)
        return out

    return block(body), removed


# ------------------------------------------------------------------ evaluating closed pure calls
_PURE_GLOBALS = frozenset("tostring tonumber type select unpack next pairs ipairs rawget rawset rawequal rawlen error assert pcall xpcall setmetatable getmetatable".split())
_PURE_LIBS = frozenset("string table math bit32 bit".split())
_NONDET = frozenset(("random", "randomseed"))


def _declared_in(f):
    """Locals declared inside function f (params, locals, loop variables, nested functions)."""
    out = set(f.params)

    def st(b):
        for s in b:
            t = type(s)
            if t is A.LocalStmt:
                out.update(s.vars)
            elif t is A.LocalFunc:
                out.add(s.var)
                out.update(s.func.params)
                st(s.func.body)
            elif t is A.NumFor:
                out.add(s.var)
            elif t is A.GenFor:
                out.update(s.vars)
            elif t is A.FuncStmt:
                out.update(s.func.params)
                st(s.func.body)
            for e in _stmt_exprs(s):
                for x in walk_exprs(e):
                    if type(x) is A.Func:
                        out.update(x.params)
                        st(x.body)
            for sub in _stmt_blocks(s):
                st(sub)

    st(f.body)
    return out


def is_closed_pure(f):
    """A function literal that touches no outer locals and only standard pure builtins."""
    declared = _declared_in(f)
    ok = True

    def check_expr(e):
        nonlocal ok
        for x in walk_exprs(e):
            t = type(x)
            if t is A.Name:
                if x.local is not None:
                    if x.local not in declared:
                        ok = False
                elif x.name not in _PURE_GLOBALS and x.name not in _PURE_LIBS:
                    ok = False
            elif t is A.Index and type(x.obj) is A.Name and x.obj.local is None and x.obj.name in _PURE_LIBS:
                if type(x.key) is A.Str and x.key.value in _NONDET:
                    ok = False
            elif t is A.Func:
                check_body(x.body)

    def check_body(b):
        for s in b:
            for e in _stmt_exprs(s):
                check_expr(e)
            for sub in _stmt_blocks(s):
                check_body(sub)
            if type(s) in (A.LocalFunc, A.FuncStmt):
                check_body(s.func.body)
            if type(s) is A.FuncStmt:
                check_expr(s.target)

    check_body(f.body)
    return ok


def collect_closed_calls(body):
    """Call nodes of the form (function ... end)(consts...) that can be evaluated on their own."""
    out = []

    def expr(e):
        for x in walk_exprs(e):
            if type(x) is A.Call and type(x.func) is A.Func and all(const_of(a)[0] for a in x.args) and is_closed_pure(x.func):
                out.append(x)
            if type(x) is A.Func:
                block(x.body)

    def block(b):
        for s in b:
            for e in _stmt_exprs(s):
                expr(e)
            for sub in _stmt_blocks(s):
                block(sub)
            if type(s) in (A.LocalFunc, A.FuncStmt):
                block(s.func.body)

    block(body)
    return out


def replace_calls(body, mapping):
    """Replace Call nodes (by identity) with constants; returns number replaced."""
    n = 0

    def rw(e):
        nonlocal n
        t = type(e)
        if t is A.Call:
            if id(e) in mapping:
                n += 1
                return make_const(mapping[id(e)])
            e.func = rw(e.func)
            e.args = [rw(a) for a in e.args]
        elif t is A.Method:
            e.obj = rw(e.obj)
            e.args = [rw(a) for a in e.args]
        elif t is A.Paren:
            e.e = rw(e.e)
        elif t is A.Un:
            e.e = rw(e.e)
        elif t is A.Bin:
            e.l = rw(e.l)
            e.r = rw(e.r)
        elif t is A.Index:
            e.obj = rw(e.obj)
            e.key = rw(e.key)
        elif t is A.Table:
            e.items = [(k, rw(key) if key is not None else None, rw(v)) for k, key, v in e.items]
        elif t is A.Func:
            blk(e.body)
        return e

    def blk(b):
        for s in b:
            t = type(s)
            if t is A.LocalStmt:
                s.exprs = [rw(e) for e in s.exprs]
            elif t is A.Assign:
                s.targets = [rw(x) if type(x) is A.Index else x for x in s.targets]
                s.exprs = [rw(e) for e in s.exprs]
            elif t is A.CallStmt:
                s.call = rw(s.call)
            elif t is A.Return:
                s.exprs = [rw(e) for e in s.exprs]
            elif t is A.While:
                s.cond = rw(s.cond)
            elif t is A.Repeat:
                s.cond = rw(s.cond)
            elif t is A.If:
                s.clauses = [(rw(c), b2) for c, b2 in s.clauses]
            elif t is A.NumFor:
                s.start, s.stop = rw(s.start), rw(s.stop)
                if s.step is not None:
                    s.step = rw(s.step)
            elif t is A.GenFor:
                s.exprs = [rw(e) for e in s.exprs]
            elif t is A.FuncStmt:
                if type(s.target) is A.Index:
                    s.target = rw(s.target)
            for sub in _stmt_blocks(s):
                blk(sub)
            if t in (A.LocalFunc, A.FuncStmt):
                blk(s.func.body)

    blk(body)
    return n


def idioms(body):
    """Turn structured-decompiler shapes into the idiomatic form: `if c then else B end` -> `if not c then B end`,
    `while true do if c then X else break end end` -> `while c do X end`."""
    n = 0

    def neg(c):
        if type(c) is A.Un and c.op == "not":
            return c.e
        return A.Un("not", c)

    def block(b):
        nonlocal n
        for s in b:
            for sub in _stmt_blocks(s):
                sub[:] = block(sub)
            for e in _stmt_exprs(s):
                for x in walk_exprs(e):
                    if type(x) is A.Func:
                        x.body = block(x.body)
            if type(s) in (A.LocalFunc, A.FuncStmt):
                s.func.body = block(s.func.body)
            if type(s) is A.If and len(s.clauses) == 1 and not s.clauses[0][1] and s.orelse:
                s.clauses = [(neg(s.clauses[0][0]), s.orelse)]
                s.orelse = None
                n += 1
            if (
                type(s) is A.While and type(s.cond) is A.Bool and s.cond.value and len(s.body) == 1 and type(s.body[0]) is A.If
                and len(s.body[0].clauses) == 1 and s.body[0].orelse and len(s.body[0].orelse) == 1 and type(s.body[0].orelse[0]) is A.Break
            ):
                cond, inner = s.body[0].clauses[0]
                s.cond, s.body = cond, inner
                n += 1
        return b

    block(body)
    return n


def count_nodes(body):
    n = 0

    def st(b):
        nonlocal n
        for s in b:
            n += 1
            for e in _stmt_exprs(s):
                for x in walk_exprs(e):
                    n += 1
                    if type(x) is A.Func:
                        st(x.body)
            for sub in _stmt_blocks(s):
                st(sub)
            if type(s) in (A.LocalFunc, A.FuncStmt):
                st(s.func.body)

    st(body)
    return n


# ------------------------------------------------------------------ renaming
_COMMON = frozenset("self args arg i j k v n x y z key value index count result results data obj tbl list item items name text player players char character".split())


def looks_random(name):
    """Random identifiers have camel-case segments without vowels (nxghHSlv); real ones read as words."""
    if name in _COMMON or len(name) < 6 or name.startswith("_"):
        return False
    segs = re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+", name)
    for seg in segs:
        if seg.isalpha() and len(seg) >= 2 and not re.search(r"[aeiouAEIOU]", seg) and not (seg.isupper() and len(seg) <= 4):
            return True
    # uppercase letters that do not start a word (not followed by a lowercase letter): SRIdiztEJO
    stray = len(re.findall(r"[A-Z](?![a-z])", name))
    return len(name) >= 8 and stray >= 3 and any(c.islower() for c in name)


def rename(body):
    """Give obfuscator-style random names short readable ones. Returns the number of renamed locals."""
    locs, used = [], set()

    def collect(b):
        for s in b:
            t = type(s)
            if t is A.LocalStmt:
                locs.extend((v, s) for v in s.vars)
            elif t is A.LocalFunc:
                locs.append((s.var, s))
                for p in s.func.params:
                    locs.append((p, None))
                collect(s.func.body)
            elif t is A.NumFor:
                locs.append((s.var, s))
            elif t is A.GenFor:
                locs.extend((v, s) for v in s.vars)
            for e in _stmt_exprs(s):
                for x in walk_exprs(e):
                    if type(x) is A.Func:
                        locs.extend((p, None) for p in x.params)
                        collect(x.body)
                    elif type(x) is A.Name and x.local is None:
                        used.add(x.name)
            for sub in _stmt_blocks(s):
                collect(sub)
            if type(s) is A.FuncStmt:
                locs.extend((p, None) for p in s.func.params)
                collect(s.func.body)

    collect(body)
    for l, _ in locs:
        if not looks_random(l.name):
            used.add(l.name)
    n = 0
    counters = {}
    for l, st in locs:
        if not looks_random(l.name):
            continue
        base = "v"
        if type(st) is A.LocalFunc:
            base = "f"
        elif type(st) is A.LocalStmt and len(st.vars) == len(st.exprs):
            e = st.exprs[st.vars.index(l)]
            m = None
            if type(e) in (A.Call, A.Method) and e.args and type(e.args[0]) is A.Str:
                m = re.fullmatch(r"[A-Za-z_]\w*", e.args[0].value)
                if m:
                    base = e.args[0].value
            elif type(e) is A.Func:
                base = "f"
            elif type(e) is A.Table:
                base = "t"
        elif st is None:
            base = "a"
        counters[base] = counters.get(base, 0) + 1
        name = base if counters[base] == 1 and base not in used and base not in ("v", "a", "f", "t") else "%s%d" % (base, counters[base])
        while name in used or name in A.KEYWORDS:
            counters[base] += 1
            name = "%s%d" % (base, counters[base])
        used.add(name)
        l.name = name
        n += 1
    return n


# ------------------------------------------------------------------ driver
def simplify(body, max_rounds=12, evaluator=None):
    """Return (new_body, stats). The input tree is modified in place and also returned.

    evaluator(list of Lua expression sources) -> list of values (str/number/bool) or None, used to fold closed pure
    calls such as inline string decryptors."""
    before = count_nodes(body)
    stats = {"nodes_before": before, "removed": 0, "renamed": 0, "evaluated": 0}
    body, tamper = strip_antitamper(body)
    stats["removed"] += tamper
    for _outer in range(4):
        for _ in range(max_rounds):
            Analysis(body)
            sim = Simplifier()
            sim.alias = find_aliases(body)
            sim.ctables = find_const_tables(body)
            body = sim.block(body, {})
            body, removed = remove_unused(body)
            stats["removed"] += removed
            if not sim.changed and not removed:
                break
        if evaluator is None:
            break
        calls = collect_closed_calls(body)[:3000]
        if not calls:
            break
        printer = A.Printer()
        values = evaluator([printer.expr(c) for c in calls])
        mapping = {id(c): v for c, v in zip(calls, values) if v is not None}
        n = replace_calls(body, mapping) if mapping else 0
        stats["evaluated"] += n
        if not n:
            break
    stats["removed"] += idioms(body)
    Analysis(body)
    stats["renamed"] = rename(body)
    stats["nodes_after"] = count_nodes(body)
    return body, stats
