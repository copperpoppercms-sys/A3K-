"""Parse -> simplify -> print, with the result checked by running both versions in the sandbox."""
import re

from . import luaast, passes, patches, sandbox, simplify


def eval_exprs(exprs, seconds=10):
    """Evaluate closed Lua expressions in the sandbox; returns a list of str/number/bool/None."""
    parts = []
    for i, e in enumerate(exprs):
        parts.append(
            "do local ok, r = pcall(function() return %s end) "
            "if ok and (type(r) == 'string' or type(r) == 'number' or type(r) == 'boolean') then "
            "print('@@', %d, type(r), string.format('%%q', tostring(r))) end end" % (e, i)
        )
    run = sandbox.run("\n".join(parts), seconds)
    out = [None] * len(exprs)
    for line in run.prints:
        f = line.split("\t", 3)
        if len(f) != 4 or f[0] != "@@":
            continue
        try:
            i, kind, raw = int(f[1]), f[2], passes.parse_quoted(f[3])
        except (ValueError, IndexError):
            continue
        if kind == "string":
            out[i] = raw
        elif kind == "boolean":
            out[i] = raw == "true"
        elif kind == "number":
            try:
                v = float(raw)
            except ValueError:
                continue
            out[i] = int(v) if v == int(v) and abs(v) < 2 ** 53 else v
    return out


def _norm(run):
    return (run.trace, run.prints, re.sub(r"obf:\d+:", "obf:N:", run.error), run.captures)


def clean(src, verify=True, seconds=10):
    """Return (new_src, stats) or (None, reason). The cleaned source is only returned when it behaves like the original."""
    try:
        body = luaast.parse(src)
    except luaast.ParseError as e:
        return None, "parse: %s" % e
    except RecursionError:
        return None, "parse: nesting too deep"
    try:
        body, stats = simplify.simplify(body, evaluator=lambda ex: eval_exprs(ex, seconds))
        out = luaast.dump(body)
    except RecursionError:
        return None, "simplify: nesting too deep"
    if verify:
        a = sandbox.run(patches.apply(src)[0], seconds)
        b = sandbox.run(patches.apply(out)[0], seconds)
        if a.timed_out or b.timed_out or not a.completed or not b.completed:
            return None, "verification incomplete (worker/time limit), kept original"
        if a.unresolved_gets or b.unresolved_gets:
            return None, "verification incomplete (remote response unavailable), kept original"
        if _norm(a) != _norm(b):
            return None, "behaviour changed by simplification, kept original"
    return out, stats
