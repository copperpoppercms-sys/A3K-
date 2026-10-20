"""Control-flow structuring: basic blocks with constant successors -> if / while code (no goto needed).

Blocks come from a devirtualized program or decompiled bytecode: each block is a statement list plus a terminator whose
leaves are constant block ids. The result only uses if/else, while true/break and return; anything that cannot be
expressed that way raises StructureError so the caller can keep the (always correct) dispatch-loop form."""
from . import luaast as A
from . import simplify as S


class StructureError(Exception):
    pass


RET = "ret"
EXIT = ("<exit>",)


def split_term(stmts, pc_local, cur=None):
    """Residual path (statements ending in `pc = const` or return, possibly a tail if) -> (prefix, term).
    term: ("jump", pc) | ("ret",) | ("cond", expr, (prefix, term), (prefix, term))."""
    prefix = []
    for s in stmts:
        t = type(s)
        if t is A.Assign and len(s.targets) == 1 and type(s.targets[0]) is A.Name and s.targets[0].local is pc_local:
            v = s.exprs[0]
            if type(v) is not A.Num:
                raise StructureError("computed jump")
            cur = v.value
        elif t is A.Return:
            prefix.append(s)
            return prefix, ("ret",)
        elif t is A.If:
            cond, body = s.clauses[0]
            rest = [A.If(s.clauses[1:], s.orelse)] if len(s.clauses) > 1 else (s.orelse or [])
            return prefix, ("cond", cond, split_term(body, pc_local, cur), split_term(rest, pc_local, cur))
        elif t is A.Do:
            sub_prefix, sub_term = split_term(s.body, pc_local, cur)
            if sub_term[0] == "jump":
                # a plain do-block: keep scoping, continue after it
                prefix.append(s) if False else prefix.extend(sub_prefix)
                cur = sub_term[1]
            else:
                return prefix + sub_prefix, sub_term
        else:
            prefix.append(s)
    if cur is None:
        raise StructureError("path ends without a jump")
    return prefix, ("jump", cur)


def normalise(blocks):
    """Give nested conditional arms their own node ids so every term is jump/ret/cond over ids."""
    out = {}

    def add(node_id, prefix, term):
        if term[0] == "cond":
            _, cond, (pt, tt), (pf, tf) = term

            def arm(tag, pre, tm):
                if not pre and tm[0] == "jump":
                    return tm[1]  # an arm that only jumps needs no node of its own
                aid = (node_id, tag)
                add(aid, pre, tm)
                return aid

            tid, fid = arm("t", pt, tt), arm("f", pf, tf)
            out[node_id] = (prefix, ("cond", cond, tid, fid))
        else:
            out[node_id] = (prefix, term)

    for pc, (prefix, term) in blocks.items():
        add(pc, prefix, term)
    return merge_twin_conds(dedupe(out))


def dedupe(blocks, protect=()):
    """Hash-cons identical blocks (same statements, same terminator) so equivalent arms become one node."""
    changed = True
    while changed:
        changed = False
        canon, remap = {}, {}
        for n in sorted(blocks, key=str):
            prefix, term = blocks[n]
            if term[0] == "cond":
                tkey = ("cond", A.dump([A.CallStmt(A.Call(A.Name(None, "c"), [term[1]]))]), str(term[2]), str(term[3]))
            else:
                tkey = (term[0],) + tuple(str(x) for x in term[1:])
            key = (A.dump(prefix), tkey)
            if key in canon and n not in protect:
                remap[n] = canon[key]
            else:
                canon.setdefault(key, n)
        if remap:
            changed = True
            for n in remap:
                del blocks[n]
            for n, (prefix, term) in list(blocks.items()):
                if term[0] == "jump":
                    blocks[n] = (prefix, ("jump", remap.get(term[1], term[1])))
                elif term[0] == "cond":
                    blocks[n] = (prefix, ("cond", term[1], remap.get(term[2], term[2]), remap.get(term[3], term[3])))
    return blocks


def merge_twin_conds(blocks):
    """cond c -> (cond a -> P/Q) | (cond b -> P/Q)  becomes  cond (c and a) or (not c and b) -> P/Q
    (what numeric-for loop checks look like: one variant per sign of the step)."""
    changed = True
    while changed:
        changed = False
        for n, (prefix, term) in list(blocks.items()):
            if term[0] != "cond":
                continue
            _, c, tid, fid = term
            if tid not in blocks or fid not in blocks or tid == fid:
                continue
            (pa, ta), (pb, tb) = blocks[tid], blocks[fid]
            if pa or pb or ta[0] != "cond" or tb[0] != "cond":
                continue
            if (ta[2], ta[3]) != (tb[2], tb[3]):
                continue
            only_us = all(
                sum(1 for m, (_, tm) in blocks.items() if tm[0] in ("jump", "cond") and x in tm[1:] if m != n) == 0 for x in (tid, fid)
            )
            if not only_us:
                continue
            expr = A.Bin("or", A.Bin("and", c, ta[1]), A.Bin("and", A.Un("not", S.clone(c)), tb[1]))
            blocks[n] = (prefix, ("cond", expr, ta[2], ta[3]))
            del blocks[tid], blocks[fid]
            changed = True
    return blocks


class _Loop:
    def __init__(self, header, nodes, exit_, outer=None):
        self.header, self.nodes, self.exit, self.outer = header, nodes, exit_, outer
        self.secondary, self.selector = {}, None


class Structurer:
    def __init__(self, blocks, entry, max_nodes=300_000):
        self.blocks = normalise(blocks)
        self.entry = entry
        self.budget = max_nodes
        self.emitted = set()
        self.selectors = 0
        self._analyse()

    # ------------------------------------------------------------ graph analysis
    def succ(self, n):
        term = self.blocks[n][1]
        if term[0] == "jump":
            return [term[1]]
        if term[0] == "cond":
            return [term[2], term[3]]
        return []

    def _analyse(self):
        for n in list(self.blocks):
            for s in self.succ(n):
                if s not in self.blocks:
                    raise StructureError("jump to a missing block %r" % (s,))
        if self.entry not in self.blocks:
            raise StructureError("entry block missing")
        order, state, back = [], {}, set()
        stack = [(self.entry, iter(self.succ(self.entry)))]
        state[self.entry] = 1
        while stack:
            n, it = stack[-1]
            for s in it:
                if s not in state:
                    state[s] = 1
                    stack.append((s, iter(self.succ(s))))
                    break
                if state[s] == 1:
                    back.add((n, s))
            else:
                state[n] = 2
                order.append(n)
                stack.pop()
        self.reachable = set(order)
        self.rpo = {n: i for i, n in enumerate(reversed(order))}
        self.back = back
        self.preds = {n: [] for n in self.reachable}
        for n in self.reachable:
            for s in self.succ(n):
                self.preds[s].append(n)
        # natural loops (merged by header)
        self.loops = {}
        for u, h in back:
            nodes = self.loops.setdefault(h, {h})
            todo = [u]
            while todo:
                x = todo.pop()
                if x in nodes:
                    continue
                nodes.add(x)
                todo.extend(p for p in self.preds[x] if p in self.reachable)
        # post-dominator tree of the forward graph (back edges removed): the join of a conditional is its ipdom
        self.loop_exit = {}
        for h, nodes in self.loops.items():
            exits = {s for n in nodes for s in self.succ(n) if s not in nodes}
            self.loop_exit[h] = min(exits, key=lambda x: self.rpo.get(x, 10 ** 9)) if exits else None
        self.pdom, depth = {}, {EXIT: 0}
        for n in order:  # postorder: successors first
            # a back edge means "run the loop again", i.e. eventually reach the loop's exit
            succs = []
            for s in self.succ(n):
                if (n, s) in back:
                    s = self.loop_exit.get(s)
                if s is not None and s in depth:
                    succs.append(s)
            if not succs:
                parent = EXIT
            else:
                parent = succs[0]
                for s in succs[1:]:
                    a_, b_ = parent, s
                    while a_ != b_:
                        if depth[a_] >= depth[b_]:
                            a_ = self.pdom[a_] if a_ != EXIT else EXIT
                        else:
                            b_ = self.pdom[b_] if b_ != EXIT else EXIT
                        if a_ == EXIT and b_ == EXIT:
                            break
                    parent = a_
            self.pdom[n] = parent
            depth[n] = depth[parent] + 1 if parent != EXIT else 1

    def join(self, n, lc):
        j = self.pdom.get(n, EXIT)
        if j == EXIT or (lc is not None and j not in lc.nodes):
            return None
        return j

    # ------------------------------------------------------------ emission
    def take(self, n):
        prefix = self.blocks[n][0]
        self.budget -= 1 + len(prefix)
        if self.budget < 0:
            raise StructureError("structured form would be too large")
        if n in self.emitted:
            return S.clone(prefix)
        self.emitted.add(n)
        return prefix

    def run(self):
        stmts, ev = self.emit(self.entry, frozenset(), None, True)
        if ev[0] not in ("ret", "dead"):
            raise StructureError("program falls off the end (%s)" % (ev,))
        return stmts

    def emit(self, pc, stop, lc, first=False):
        """Statements from block pc up to an event.
        events: ("dead",) code after is unreachable (return/break emitted) | ("cont",) | ("join", pc) | ("ret",)"""
        out = []
        while True:
            if lc is not None and not first:
                if pc == lc.header:
                    return out, ("cont",)
                if pc not in lc.nodes:
                    if pc == lc.exit:
                        out.append(A.Break())
                        return out, ("dead",)
                    if pc in lc.secondary:
                        out.append(A.Assign([A.Name(lc.selector, lc.selector.name)], [A.Num(lc.secondary[pc])]))
                        out.append(A.Break())
                        return out, ("dead",)
                    raise StructureError("loop exits to an unknown block")
            if pc in stop and not first:
                return out, ("join", pc)
            if pc in self.loops and not (lc is not None and pc == lc.header and first):
                lstmts, state = self.emit_loop(pc, lc, stop)
                out += lstmts
                if state[0] == "event":
                    return out, state[1]
                pc = state[1]
                first = False
                continue
            first = False
            out += self.take(pc)
            term = self.blocks[pc][1]
            kind = term[0]
            if kind == "ret":
                return out, ("dead",)
            if kind == "jump":
                pc = term[1]
                continue
            cond, t, f = term[1], term[2], term[3]
            j = self.join(pc, lc)
            stop2 = stop | {j} if j is not None else stop
            t_out, t_ev = self.emit(t, stop2, lc)
            f_out, f_ev = self.emit(f, stop2, lc)
            t_dead, f_dead = t_ev == ("dead",), f_ev == ("dead",)
            if t_dead or f_dead or t_ev == f_ev:
                ev = f_ev if t_dead else t_ev
                out.append(A.If([(cond, t_out)], f_out or None))
                if ev[0] == "join" and ev[1] == j:
                    pc = j
                    continue
                return out, ev
            # different fall-through events: pull the shared continuation into the arm that reaches it
            if t_ev[0] == "join" and t_ev[1] == j:
                rest, rev = self.emit(j, stop, lc)
                t_out = t_out + rest
                if rev != f_ev and rev != ("dead",):
                    raise StructureError("conflicting continuations")
                out.append(A.If([(cond, t_out)], f_out or None))
                return out, f_ev if rev == ("dead",) else rev
            if f_ev[0] == "join" and f_ev[1] == j:
                rest, rev = self.emit(j, stop, lc)
                f_out = f_out + rest
                if rev != t_ev and rev != ("dead",):
                    raise StructureError("conflicting continuations")
                out.append(A.If([(cond, t_out)], f_out or None))
                return out, t_ev if rev == ("dead",) else rev
            raise StructureError("unstructured conditional")

    def emit_loop(self, h, lc_outer, stop):
        nodes = self.loops[h]
        exits = set()
        for n in nodes:
            for s in self.succ(n):
                if s not in nodes:
                    exits.add(s)
        ordered = sorted(exits, key=lambda x: self.rpo.get(x, 10 ** 9))
        primary = ordered[0] if ordered else None
        lc = _Loop(h, nodes, primary, lc_outer)
        pre = []
        if len(ordered) > 1:
            # every other exit leaves through a selector that is dispatched right after the loop
            self.selectors += 1
            lc.selector = A.Local("exit_%d" % self.selectors, 0)
            lc.secondary = {x: i + 1 for i, x in enumerate(ordered[1:])}
            pre.append(A.LocalStmt([lc.selector], [A.Nil()]))
        body, ev = self.emit(h, frozenset(), lc, True)
        if ev[0] not in ("cont", "dead"):
            raise StructureError("loop body does not end at the header")
        stmts = pre + [A.While(A.Bool(True), body)]
        if not lc.secondary:
            return stmts, (("event", ("dead",)) if primary is None else ("next", primary))
        arms, falls = [], set()
        for x, idx in lc.secondary.items():
            a_out, a_ev = self.emit(x, stop, lc_outer)
            arms.append((A.Bin("==", A.Name(lc.selector, lc.selector.name), A.Num(idx)), a_out))
            if a_ev != ("dead",):
                falls.add(a_ev)
        if primary is not None:
            r_out, r_ev = self.emit(primary, stop, lc_outer)
            if r_ev != ("dead",):
                falls.add(r_ev)
        else:
            r_out = []
        if len(falls) > 1:
            raise StructureError("loop exits continue in different places")
        stmts.append(A.If(arms, r_out or None))
        return stmts, ("event", falls.pop() if falls else ("dead",))


def structure(blocks, entry):
    """blocks: {id: (prefix_stmts, term)} -> structured statement list, or raises StructureError."""
    try:
        return Structurer(blocks, entry).run()
    except RecursionError:
        raise StructureError("nesting too deep")


# ---------------------------------------------------------------- dispatch-loop form -> structured form
def _match_dispatch(f):
    """Function body ending in `local pc = N  while true do if pc == k then ... elseif ... else return end end`."""
    body = f.body
    if not body or type(body[-1]) is not A.While or not (type(body[-1].cond) is A.Bool and body[-1].cond.value):
        return None
    loop = body[-1]
    if len(loop.body) != 1 or type(loop.body[0]) is not A.If:
        return None
    ifs = loop.body[0]
    pc_local, blocks = None, {}
    for cond, stmts in ifs.clauses:
        if not (type(cond) is A.Bin and cond.op == "==" and type(cond.l) is A.Name and cond.l.local is not None and type(cond.r) is A.Num):
            return None
        if pc_local is None:
            pc_local = cond.l.local
        elif cond.l.local is not pc_local:
            return None
        blocks[cond.r.value] = stmts
    if pc_local is None:
        return None
    init = None
    for i in range(len(body) - 2, -1, -1):
        st = body[i]
        if type(st) is A.LocalStmt and pc_local in st.vars and len(st.vars) == len(st.exprs):
            e = st.exprs[st.vars.index(pc_local)]
            if type(e) is A.Num:
                init = (i, e.value)
            break
    if init is None:
        return None
    return pc_local, blocks, init


def lift_dispatch_loops(chunk):
    """Structure every dispatch-loop function in the chunk in place; returns (structured, failed) counts."""
    from . import simplify as S_

    done = failed = 0
    funcs = [f for f, _ in _all_funcs(chunk)]
    for f in funcs:
        m = _match_dispatch(f)
        if m is None:
            continue
        pc_local, blocks, (init_idx, entry) = m
        try:
            parts = {k: split_term(b, pc_local, k) for k, b in blocks.items()}
            code = structure(parts, entry)
        except StructureError:
            failed += 1
            continue
        f.body = f.body[:init_idx] + f.body[init_idx + 1:-1] + code
        done += 1
    return done, failed


def _all_funcs(chunk):
    from . import devirt

    return devirt._functions(chunk)
