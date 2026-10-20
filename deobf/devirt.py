"""Devirtualizer for interpreter-style Lua VM obfuscators (IronBrew 2 / FiOne / Rerubi family).

The obfuscated script contains a Lua interpreter loop:  inst = code[pc]; op = inst[1]; <dispatch on op>; pc = pc + 1.
Instead of recognising opcodes, the interpreter's own handler code is partially evaluated against every concrete
instruction of the program (obtained by running the VM once and dumping the deserialized chunk). What remains is the
program itself, written in terms of the VM's registers, with the dispatch loop specialised to the original pc."""
from . import luaast as A
from . import passes, sandbox, structure, simplify as S


import re

_VM_HINT = re.compile(r"while\s+true\s+do\s+\w+\s*=\s*\w+\[\w+\]\s*;?\s*\w+\s*=\s*\w+\[\d+\]")
MAX_DEVIRT_BYTES = 600_000


class DevirtError(Exception):
    pass


def looks_like_vm(src):
    """Cheap pre-check so ordinary scripts are not parsed for nothing."""
    return len(src) <= MAX_DEVIRT_BYTES and bool(_VM_HINT.search(src))


# ---------------------------------------------------------------- locating the interpreter
class VM:
    """Pieces of a recognised interpreter."""

    def __init__(self):
        self.wrap = None  # Func: function(chunk, upvals, env) returning the closure
        self.wrap_name = None  # Local the Wrap function is bound to
        self.closure = None  # Func: function(...) containing the dispatch loop
        self.loop = None  # While node
        self.inst = self.arr = self.pc = self.enum = None  # Locals
        self.dispatch = []  # statements between the fetch and the pc increment
        self.prologue = []  # closure statements before the loop
        self.wrap_pre = []  # wrap statements before `return function(...)`
        self.top_index = None  # index of the wrap definition among top-level statements
        self.wrap_local = None
        self.spec_local = A.Local("SPEC", 0)


def _is_true(e):
    return type(e) is A.Bool and e.value is True or (type(e) is A.Num and e.value not in (0,))


def _name_local(e):
    return e.local if type(e) is A.Name and e.local is not None else None


def _match_fetch(body):
    """body[0]: inst = arr[pc]; body[1]: enum = inst[1]  ->  (inst, arr, pc, enum) Locals or None."""
    if len(body) < 3:
        return None
    s0, s1 = body[0], body[1]
    if type(s0) is not A.Assign or len(s0.targets) != 1 or len(s0.exprs) != 1:
        return None
    inst = _name_local(s0.targets[0])
    e0 = s0.exprs[0]
    if inst is None or type(e0) is not A.Index:
        return None
    arr, pc = _name_local(e0.obj), _name_local(e0.key)
    if arr is None or pc is None:
        return None
    if type(s1) is not A.Assign or len(s1.targets) != 1 or len(s1.exprs) != 1:
        return None
    enum = _name_local(s1.targets[0])
    e1 = s1.exprs[0]
    if enum is None or type(e1) is not A.Index or _name_local(e1.obj) is not inst or type(e1.key) is not A.Num:
        return None
    last = body[-1]
    if not (type(last) is A.Assign and len(last.targets) == 1 and _name_local(last.targets[0]) is pc):
        return None
    return inst, arr, pc, enum


def _functions(body, parent=None, out=None):
    """All Func nodes with their enclosing Func (None at top level)."""
    out = [] if out is None else out

    def expr(e, par):
        for x in S.walk_exprs(e):
            if type(x) is A.Func:
                out.append((x, par))
                _functions(x.body, x, out)

    for s in body:
        for e in S._stmt_exprs(s):
            expr(e, parent)
        for sub in S._stmt_blocks(s):
            _functions(sub, parent, out)
        if type(s) in (A.LocalFunc, A.FuncStmt):
            out.append((s.func, parent))
            _functions(s.func.body, s.func, out)
    return out


def find_vm(chunk):
    for f, parent in _functions(chunk):
        for i, s in enumerate(f.body):
            if type(s) is A.While and _is_true(s.cond):
                m = _match_fetch(s.body)
                if m is None:
                    continue
                vm = VM()
                vm.inst, vm.arr, vm.pc, vm.enum = m
                vm.loop, vm.closure = s, f
                vm.dispatch = s.body[2:]
                vm.prologue = f.body[:i]
                # the closure is returned by Wrap(chunk, upvals, env)
                for w, wp in _functions(chunk):
                    if any(type(st) is A.Return and len(st.exprs) == 1 and st.exprs[0] is f for st in w.body):
                        vm.wrap = w
                        vm.wrap_pre = [st for st in w.body if not (type(st) is A.Return)]
                        break
                if vm.wrap is None or len(vm.wrap.params) < 1:
                    continue
                for ti, st in enumerate(chunk):
                    if type(st) is A.LocalFunc and st.func is vm.wrap:
                        vm.wrap_local, vm.top_index = st.var, ti
                if vm.wrap_local is None:
                    raise DevirtError("the vm Wrap function is not a top-level local function")
                return vm
    return None


# ---------------------------------------------------------------- dumping the runtime program
def instrument(chunk, vm):
    """Insert __VMDUMP(<chunk parameter>) at the start of the Wrap function."""
    call = A.CallStmt(A.Call(A.Name(None, "__VMDUMP"), [A.Name(vm.wrap.params[0], vm.wrap.params[0].name)]))
    vm.wrap.body.insert(0, call)


def to_py(node):
    """AST constant expression (from a dumped table constructor) -> Python dict/str/number/bool/None."""
    t = type(node)
    if t is A.Num:
        return node.value
    if t is A.Str:
        return node.value
    if t is A.Bool:
        return node.value
    if t is A.Nil:
        return None
    if t is A.Paren:
        return to_py(node.e)
    if t is A.Un and node.op == "-":
        return -to_py(node.e)
    if t is A.Bin and node.op == "/":
        a, b = to_py(node.l), to_py(node.r)
        return float("nan") if a == 0 and b == 0 else float("inf") * (1 if a > 0 else -1)
    if t is A.Table:
        d = {}
        for kind, k, v in node.items:
            if kind != "expr":
                raise DevirtError("unexpected dump format")
            key = to_py(k)
            if isinstance(key, float) and key == int(key):
                key = int(key)
            d[key] = to_py(v)
        return d
    raise DevirtError("unexpected node %s in dump" % t.__name__)


def parse_dump(text):
    body = A.parse("return " + text)
    return to_py(body[0].exprs[0])


def dump_chunk(source_text, seconds):
    """Run the instrumented script and return the dumped top-level chunk as Python data."""
    run = sandbox.run(source_text, seconds)
    if not run.vmchunks:
        raise DevirtError("the vm never started (%s)" % (run.error or "no chunk was dumped"))
    return parse_dump(run.vmchunks[0])


# ---------------------------------------------------------------- partial evaluation
class KTab:
    """A table whose contents are known at analysis time (the program's instructions, constants, protos)."""

    __slots__ = ("tab",)

    def __init__(self, tab):
        self.tab = tab


class SetConst:
    """Internal statement: bind a local to a known value (used when unrolling loops)."""

    __slots__ = ("local", "value")

    def __init__(self, local, value):
        self.local, self.value = local, value


def wrap_value(v):
    if v is None:
        return A.Nil()
    if v is True or v is False:
        return A.Bool(v)
    if isinstance(v, str):
        return A.Str(v)
    if isinstance(v, dict):
        return KTab(v)
    return A.Num(v)


def is_const(v):
    return type(v) in (A.Nil, A.Bool, A.Num, A.Str)


def is_value(v):
    return is_const(v) or type(v) is KTab


def _truthy(v):
    if type(v) is KTab:
        return True
    return S.truthy(S.const_of(v)[1])


MAX_UNROLL = 1024


class PE:
    def __init__(self, vm):
        self.vm = vm
        self.chunks = []  # python chunk dicts, index = spec id
        self.chunk_ids = {}
        self.counter = [0]
        self.renamed = set()

    def register(self, tab):
        key = id(tab)
        if key not in self.chunk_ids:
            self.chunk_ids[key] = len(self.chunks) + 1
            self.chunks.append(tab)
        return self.chunk_ids[key]

    # ------------------------------------------------------------ expressions
    def expr(self, e, env):
        t = type(e)
        if t in (A.Nil, A.Bool, A.Num, A.Str, A.Vararg):
            return type(e)(*[getattr(e, f) for f in e.__slots__])
        if t is A.Name:
            if e.local is not None and e.local in env:
                return env[e.local]
            return A.Name(e.local, e.name)
        if t is A.Paren:
            inner = self.expr(e.e, env)
            return inner if is_value(inner) else A.Paren(inner)
        if t is A.Index:
            obj, key = self.expr(e.obj, env), self.expr(e.key, env)
            if type(obj) is KTab:
                if not is_const(key):
                    raise DevirtError("symbolic index into a known table")
                k = S.const_of(key)[1]
                if isinstance(k, float) and k == int(k):
                    k = int(k)
                return wrap_value(obj.tab.get(k))
            if type(key) is KTab:
                raise DevirtError("known table used as a key")
            return A.Index(obj, key)
        if t in (A.Call, A.Method):
            return self.call(e, env)
        if t is A.Table:
            items = []
            for kind, k, v in e.items:
                kk = self.expr(k, env) if k is not None else None
                vv = self.expr(v, env)
                if type(kk) is KTab or type(vv) is KTab:
                    raise DevirtError("known table stored into a residual table")
                items.append((kind, kk, vv))
            return A.Table(items)
        if t is A.Un:
            inner = self.expr(e.e, env)
            if is_const(inner):
                r = S.fold_un(e.op, S.const_of(inner)[1])
                if r is not S._MISSING:
                    return S.make_const(r)
            if type(inner) is KTab:
                raise DevirtError("operator applied to a known table")
            return A.Un(e.op, inner)
        if t is A.Bin:
            lhs = self.expr(e.l, env)
            if e.op in ("and", "or") and is_value(lhs):
                if e.op == "and":
                    return self.expr(e.r, env) if _truthy(lhs) else lhs
                return lhs if _truthy(lhs) else self.expr(e.r, env)
            rhs = self.expr(e.r, env)
            if is_const(lhs) and is_const(rhs):
                r = S.fold_bin(e.op, S.const_of(lhs)[1], S.const_of(rhs)[1])
                if r is not S._MISSING:
                    return S.make_const(r)
            if type(lhs) is KTab or type(rhs) is KTab:
                raise DevirtError("operator applied to a known table")
            return A.Bin(e.op, lhs, rhs)
        if t is A.Func:
            return self.func(e, env)
        raise DevirtError("unsupported expression %s" % t.__name__)

    def func(self, f, env):
        declared = S._declared_in(f)
        for blk in [f.body]:
            for e in _all_exprs(blk):
                for x in S.walk_exprs(e):
                    if type(x) is A.Name and x.local is not None and x.local in env and x.local not in declared:
                        raise DevirtError("closure reads analysis-time variable %s" % x.name)
        return _clone_func(f)

    def call(self, e, env):
        if type(e) is A.Call:
            fn = self.expr(e.func, env)
            args = [self.expr(a, env) for a in e.args]
            if type(fn) is KTab:
                raise DevirtError("call on a known table")
            if args and type(fn) is A.Name and fn.local is self.vm.wrap_local:
                # Wrap(chunk, upvalues, env): a known chunk is a nested proto, anything else is the top-level entry
                idx = self.register(args[0].tab) if type(args[0]) is KTab else 1
                return A.Call(A.Index(A.Name(self.vm.spec_local, "SPEC"), A.Num(idx)), args[1:])
            if any(type(a) is KTab for a in args):
                raise DevirtError("known table passed to a call")
            return A.Call(fn, args)
        obj = self.expr(e.obj, env)
        args = [self.expr(a, env) for a in e.args]
        if type(obj) is KTab or any(type(a) is KTab for a in args):
            raise DevirtError("known table used in a method call")
        return A.Method(obj, e.name, args)

    # ------------------------------------------------------------ statements
    def stmts(self, stmts, i, env):
        stmts = list(stmts)
        out = []
        while i < len(stmts):
            s = stmts[i]
            i += 1
            t = type(s)
            if t is SetConst:
                env[s.local] = s.value
            elif t is A.LocalStmt:
                vals = [self.expr(e, env) for e in s.exprs]
                multi = bool(s.exprs) and type(s.exprs[-1]) in (A.Call, A.Method, A.Vararg) and len(s.exprs) < len(s.vars)
                keep_vars, keep_vals = [], []
                for j, v in enumerate(s.vars):
                    val = vals[j] if j < len(vals) and not (multi and j >= len(vals) - 1) else None
                    if val is not None and is_value(val):
                        env[v] = val
                        if type(val) is not KTab:
                            keep_vars.append(v)
                            keep_vals.append(val)
                    else:
                        env.pop(v, None)
                        keep_vars.append(v)
                        if j < len(vals):
                            keep_vals.append(vals[j])
                if multi:
                    keep_vars, keep_vals = list(s.vars), vals
                if keep_vars:
                    out.append(A.LocalStmt(keep_vars, keep_vals if (keep_vals or not s.exprs) else []))
            elif t is A.Assign:
                vals = [self.expr(e, env) for e in s.exprs]
                multi = bool(s.exprs) and type(s.exprs[-1]) in (A.Call, A.Method, A.Vararg)
                if len(s.targets) == 1 and len(vals) == 1:
                    tg = s.targets[0]
                    val = vals[0]
                    if type(tg) is A.Name:
                        if tg.local is not None:
                            if is_value(val) and not multi:
                                env[tg.local] = val
                                if type(val) is KTab:
                                    continue
                            else:
                                env.pop(tg.local, None)
                        out.append(A.Assign([A.Name(tg.local, tg.name)], [val]))
                    else:
                        if type(val) is KTab:
                            raise DevirtError("known table stored")
                        obj, key = self.expr(tg.obj, env), self.expr(tg.key, env)
                        if type(obj) is KTab or type(key) is KTab:
                            raise DevirtError("store into a known table")
                        out.append(A.Assign([A.Index(obj, key)], [val]))
                else:
                    targets = []
                    for tg in s.targets:
                        if type(tg) is A.Name:
                            if tg.local is not None:
                                env.pop(tg.local, None)
                            targets.append(A.Name(tg.local, tg.name))
                        else:
                            targets.append(A.Index(self.expr(tg.obj, env), self.expr(tg.key, env)))
                    if any(type(v) is KTab for v in vals):
                        raise DevirtError("known table in multiple assignment")
                    out.append(A.Assign(targets, vals))
            elif t is A.CallStmt:
                out.append(A.CallStmt(self.call(s.call, env)))
            elif t is A.Return:
                vals = [self.expr(e, env) for e in s.exprs]
                if any(type(v) is KTab for v in vals):
                    raise DevirtError("known table returned")
                out.append(A.Return(vals))
                return out
            elif t is A.Break:
                out.append(A.Break())
                return out
            elif t is A.Do:
                stmts[i:i] = list(s.body)
            elif t is A.If:
                rest = stmts[i:]
                return out + self.if_stmt(s, rest, env)
            elif t is A.NumFor:
                start, stop = self.expr(s.start, env), self.expr(s.stop, env)
                step = self.expr(s.step, env) if s.step is not None else A.Num(1)
                if is_const(start) and is_const(stop) and is_const(step) and not _has_exit(s.body):
                    a, b, c = (S.const_of(x)[1] for x in (start, stop, step))
                    if all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in (a, b, c)) and c != 0:
                        n = int((b - a) // c) + 1 if (c > 0 and b >= a) or (c < 0 and b <= a) else 0
                        if n <= MAX_UNROLL:
                            seq = []
                            for k in range(n):
                                seq.append(SetConst(s.var, A.Num(a + k * c)))
                                seq.extend(s.body)
                            stmts[i:i] = seq
                            continue
                if any(type(x) is KTab for x in (start, stop, step)):
                    raise DevirtError("known table as loop bound")
                gone = S.assigned_locals(s.body)
                self._check_state(env, gone)
                inner_env = {k: v for k, v in env.items() if k not in gone}
                body = self.stmts(s.body, 0, inner_env)
                out.append(A.NumFor(s.var, start, stop, step if s.step is not None else None, body))
                for g in gone:
                    env.pop(g, None)
            elif t in (A.While, A.Repeat, A.GenFor):
                gone = S.assigned_locals(s.body)
                self._check_state(env, gone)
                inner_env = {k: v for k, v in env.items() if k not in gone}
                if t is A.GenFor:
                    exprs = [self.expr(e, env) for e in s.exprs]
                    body = self.stmts(s.body, 0, inner_env)
                    out.append(A.GenFor(s.vars, exprs, body))
                elif t is A.While:
                    cond = self.expr(s.cond, inner_env)
                    body = self.stmts(s.body, 0, inner_env)
                    out.append(A.While(cond, body))
                else:
                    body = self.stmts(s.body, 0, inner_env)
                    out.append(A.Repeat(body, self.expr(s.cond, inner_env)))
                for g in gone:
                    env.pop(g, None)
            elif t is A.LocalFunc:
                env.pop(s.var, None)
                out.append(A.LocalFunc(s.var, self.func(s.func, env)))
            elif t is A.FuncStmt:
                out.append(A.FuncStmt(self.expr(s.target, env) if type(s.target) is A.Index else A.Name(s.target.local, s.target.name), self.func(s.func, env)))
            else:
                raise DevirtError("unsupported statement %s" % t.__name__)
        return out

    def _check_state(self, env, gone):
        for g in gone:
            if g in env and type(env[g]) is KTab or g in (self.vm.pc, self.vm.inst, self.vm.enum):
                raise DevirtError("interpreter state is assigned inside a residual loop")

    def if_stmt(self, s, rest, env):
        """CPS: the code after the if is evaluated inside each branch, so every path ends with a known pc."""
        clauses = list(s.clauses)
        i = 0
        while i < len(clauses):
            cond = self.expr(clauses[i][0], env)
            if is_value(cond):
                if _truthy(cond):
                    return self.stmts(list(clauses[i][1]) + rest, 0, env)
                i += 1
                continue
            then = self.stmts(list(clauses[i][1]) + rest, 0, dict(env))
            tail = A.If(clauses[i + 1:], s.orelse) if i + 1 < len(clauses) else None
            if tail is not None:
                other = self.if_stmt(tail, rest, dict(env))
            elif s.orelse is not None:
                other = self.stmts(list(s.orelse) + rest, 0, dict(env))
            else:
                other = self.stmts(rest, 0, dict(env))
            return [A.If([(cond, then)], other if other else None)]
        if s.orelse is not None:
            return self.stmts(list(s.orelse) + rest, 0, env)
        return self.stmts(rest, 0, env)


def _has_exit(body):
    for s in body:
        t = type(s)
        if t in (A.Break, A.Return, A.Continue, A.Goto):
            return True
        for sub in S._stmt_blocks(s):
            if t in (A.While, A.Repeat, A.NumFor, A.GenFor):
                # a break inside a nested loop only leaves that loop
                if any(type(x) in (A.Return, A.Goto) for x in _flat(sub)):
                    return True
            elif _has_exit(sub):
                return True
    return False


def _flat(body):
    for s in body:
        yield s
        for sub in S._stmt_blocks(s):
            yield from _flat(sub)


def _all_exprs(body):
    for s in body:
        for e in S._stmt_exprs(s):
            yield e
        for sub in S._stmt_blocks(s):
            yield from _all_exprs(sub)
        if type(s) in (A.LocalFunc, A.FuncStmt):
            yield from _all_exprs(s.func.body)


def _clone_func(f):
    return S.clone(f)


# ---------------------------------------------------------------- assembling the specialised program
def _final_pcs(stmts, pc_local, cur, out):
    """Values of the pc at the end of every path through a residual statement list (cur = pc on entry)."""
    for s in stmts:
        t = type(s)
        if t is A.Assign and len(s.targets) == 1 and type(s.targets[0]) is A.Name and s.targets[0].local is pc_local:
            v = s.exprs[0]
            if type(v) is not A.Num:
                raise DevirtError("computed jump")
            cur = v.value
        elif t is A.Return:
            return
        elif t is A.If:
            for _, b in s.clauses:
                _final_pcs(b, pc_local, cur, out)
            _final_pcs(s.orelse or [], pc_local, cur, out)
            return
        elif t is A.Do:
            _final_pcs(s.body, pc_local, cur, out)
            return
    out.add(cur)


def _uniquify(stmts, counter, done):
    """Flattened handlers share one scope, so locals that only had distinct scopes before must get distinct names
    (an inner `local n` must never shadow the pc)."""
    def declare(l):
        if id(l) not in done:
            done.add(id(l))
            counter[0] += 1
            l.name = "%s_%d" % (l.name, counter[0])

    for s in stmts:
        t = type(s)
        if t is A.LocalStmt:
            for v in s.vars:
                declare(v)
        elif t is A.LocalFunc:
            declare(s.var)
        elif t is A.NumFor:
            declare(s.var)
        elif t is A.GenFor:
            for v in s.vars:
                declare(v)
        for sub in S._stmt_blocks(s):
            _uniquify(sub, counter, done)


def _build_spec(vm, pe, chunk_py, max_pcs=200000):
    env = {vm.wrap.params[0]: KTab(chunk_py)}
    pre = pe.stmts(vm.wrap_pre, 0, env)
    prologue = pe.stmts(vm.prologue, 0, env)
    code = chunk_py.get(1)
    if not isinstance(code, dict):
        raise DevirtError("chunk has no instruction table")
    branches, todo, seen = [], [1], set()
    while todo:
        k = todo.pop(0)
        if k in seen:
            continue
        seen.add(k)
        if len(seen) > max_pcs:
            raise DevirtError("program too large")
        ins = code.get(k)
        if ins is None:
            continue  # falling off the end: handled by the final return
        envk = dict(env)
        envk[vm.inst], envk[vm.pc], envk[vm.enum] = KTab(ins), A.Num(k), wrap_value(ins.get(1))
        res = pe.stmts(vm.dispatch, 0, envk)
        _uniquify(res, pe.counter, pe.renamed)
        succ = set()
        _final_pcs(res, vm.pc, k, succ)
        todo.extend(sorted(succ))
        branches.append((k, res))
    branches.sort(key=lambda b: b[0])
    try:
        blocks = {k: structure.split_term(body, vm.pc, k) for k, body in branches}
        code = structure.structure(blocks, 1)
    except structure.StructureError:
        clauses = [(A.Bin("==", A.Name(vm.pc, vm.pc.name), A.Num(k)), body) for k, body in branches]
        code = [A.While(A.Bool(True), [A.If(clauses, [A.Return([])])])]
    inner = A.Func(list(vm.closure.params), vm.closure.vararg, prologue + code, False, vm.closure.fid)
    outer = A.Func(list(vm.wrap.params[1:]), False, pre + [A.Return([inner])], False, vm.wrap.fid)
    return outer


def _prune_scaffolding(body):
    """Drop the vm's loader machinery (decompressor, byte string, deserializer, ...) once nothing references it."""
    while True:
        S.Analysis(body)
        keep = []
        for st in body:
            t = type(st)
            if t is A.LocalStmt and all(v.reads == 0 and v.writes == 0 for v in st.vars):
                continue
            if t is A.LocalFunc and st.var.reads == 0 and st.var.writes == 0:
                continue
            keep.append(st)
        if len(keep) == len(body):
            return body
        body = keep


def devirtualize(src, seconds=10):
    """Return Lua source in which the interpreter loop is replaced by the program it was running."""
    chunk = A.parse(src)
    vm = find_vm(chunk)
    if vm is None:
        raise DevirtError("no interpreter loop of the supported shape was found")
    instrument(chunk, vm)
    text = A.dump(chunk)
    vm.wrap.body.pop(0)
    root = dump_chunk(text, seconds)
    pe = PE(vm)
    pe.register(root)
    specs, done = {}, 0
    while done < len(pe.chunks):
        specs[done + 1] = _build_spec(vm, pe, pe.chunks[done])
        done += 1
    head = chunk[: vm.top_index]
    tail = pe.stmts(chunk[vm.top_index + 1:], 0, {})
    body = list(head)
    body.append(A.LocalStmt([vm.spec_local], [A.Table([])]))
    for idx in sorted(specs):
        body.append(A.Assign([A.Index(A.Name(vm.spec_local, "SPEC"), A.Num(idx))], [specs[idx]]))
    body.extend(tail)
    body = _prune_scaffolding(body)
    return A.dump(body)
