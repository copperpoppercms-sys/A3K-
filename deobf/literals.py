"""Bounded evaluation of literal loader expressions. Never executes input code."""
import math

MAX_LITERAL_BYTES = 2 * 1024 * 1024


class NotLiteral(ValueError):
    pass


def _bounded(value):
    if isinstance(value, str) and len(value) > MAX_LITERAL_BYTES:
        raise NotLiteral("literal output limit")
    return value


def _integer(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value != int(value):
        raise NotLiteral("integer required")
    return int(value)


def _text(value):
    if isinstance(value, str):
        return value
    # Only integers with an unambiguous Lua 5.1 string representation.
    if isinstance(value, (int, float)) and not isinstance(value, bool) and abs(value) < 10 ** 14 and value == int(value):
        if value == 0 and math.copysign(1, value) < 0:
            raise NotLiteral("negative zero representation")
        return str(int(value))
    raise NotLiteral("string required")


def _slice(s, first, last):
    first, last = _integer(first), _integer(last)
    if first < 0:
        first += len(s) + 1
    if last < 0:
        last += len(s) + 1
    return s[max(first, 1) - 1:max(last, 0)]


def evaluate(node, depth=0):
    from . import luaast as a

    if depth > 64:
        raise NotLiteral("literal nesting limit")
    ev = lambda n: evaluate(n, depth + 1)
    if isinstance(node, (a.Str, a.Num)):
        return _bounded(node.value)
    if isinstance(node, a.Paren):
        return ev(node.e)
    if isinstance(node, a.Un) and node.op in ("-", "#"):
        value = ev(node.e)
        if node.op == "#" and isinstance(value, (str, list)):
            return len(value)
        if node.op == "-" and isinstance(value, (float, int)):
            return -value
    if isinstance(node, a.Bin):
        left, right = ev(node.l), ev(node.r)
        if node.op == "..":
            return _bounded(_text(left) + _text(right))
        if isinstance(left, (int, float)) and isinstance(right, (int, float)):
            # Evaluate the tree directly: formatting (-1)^2 as -1^2 changes
            # precedence. Lua 5.1 arithmetic uses doubles rather than big ints.
            left, right = float(left), float(right)
            operations = {"+": lambda: left + right, "-": lambda: left - right,
                          "*": lambda: left * right, "/": lambda: left / right,
                          "%": lambda: left % right, "^": lambda: left ** right}
            if node.op not in operations:
                raise NotLiteral("unsupported arithmetic")
            value = operations[node.op]()
            if not isinstance(value, complex) and math.isfinite(value) and abs(value) <= 2 ** 53:
                return value
    if isinstance(node, a.Table):
        if any(kind != "pos" for kind, _, _ in node.items):
            raise NotLiteral("array required")
        return [ev(value) for _, _, value in node.items]
    if isinstance(node, a.Call):
        fn = node.func
        if isinstance(fn, a.Index) and isinstance(fn.obj, a.Name) and fn.obj.local is None and isinstance(fn.key, a.Str):
            name = fn.obj.name + "." + fn.key.value
            args = [ev(arg) for arg in node.args]
            if name == "string.char":
                numbers = [_integer(v) for v in args]
                if all(0 <= n <= 255 for n in numbers):
                    return _bounded("".join(map(chr, numbers)))
            if name == "string.reverse" and len(args) == 1 and isinstance(args[0], str):
                return args[0][::-1]
            if name == "string.sub" and 2 <= len(args) <= 3 and isinstance(args[0], str):
                return _slice(args[0], args[1], args[2] if len(args) == 3 else -1)
            if name in ("string.lower", "string.upper") and len(args) == 1 and isinstance(args[0], str):
                # Lua operates on bytes; Python's Unicode case conversion differs.
                table = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")
                if name == "string.upper":
                    table = str.maketrans("abcdefghijklmnopqrstuvwxyz", "ABCDEFGHIJKLMNOPQRSTUVWXYZ")
                return args[0].translate(table)
            if name == "string.rep" and len(args) == 2 and isinstance(args[0], str):
                n = max(0, _integer(args[1]))
                if n * len(args[0]) > MAX_LITERAL_BYTES:
                    raise NotLiteral("literal output limit")
                return args[0] * min(n, MAX_LITERAL_BYTES)
            if name == "table.concat" and 1 <= len(args) <= 4 and isinstance(args[0], list):
                array = args[0]
                sep = _text(args[1]) if len(args) > 1 else ""
                first = _integer(args[2]) if len(args) > 2 else 1
                last = _integer(args[3]) if len(args) > 3 else len(array)
                if first <= last and (first < 1 or last > len(array)):
                    raise NotLiteral("array bounds")
                items = [_text(v) for v in array[first - 1:last]] if first <= last else []
                if sum(map(len, items)) + len(sep) * max(0, len(items) - 1) > MAX_LITERAL_BYTES:
                    raise NotLiteral("literal output limit")
                return sep.join(items)
    raise NotLiteral("expression depends on runtime values")


def unwrap(src):
    """Only unwrap a complete, immediately invoked literal loader chunk."""
    from . import luaast as a

    # Most inputs are not literal loaders. Avoid parsing their entire VM here.
    if len(src) > MAX_LITERAL_BYTES or not any(word in src[:4096] for word in ("loadstring", "load")):
        return None
    try:
        body = a.parse(src)
        if len(body) != 1 or not isinstance(body[0], (a.CallStmt, a.Return)):
            return None
        if isinstance(body[0], a.Return):
            if len(body[0].exprs) != 1:
                return None
            outer = body[0].exprs[0]
        else:
            outer = body[0].call

        def unparen(n):
            while isinstance(n, a.Paren):
                n = n.e
            return n

        outer = unparen(outer)
        if not isinstance(outer, a.Call) or outer.args:
            return None
        loader = unparen(outer.func)
        if isinstance(loader, a.Call) and isinstance(loader.func, a.Name) and loader.func.name == "assert" and len(loader.args) == 1:
            loader = unparen(loader.args[0])
        if not isinstance(loader, a.Call) or not isinstance(loader.func, a.Name) or loader.func.name not in ("load", "loadstring") or not 1 <= len(loader.args) <= 2:
            return None
        # A chunk-name argument is harmless only when it is itself a string literal.
        if len(loader.args) == 2 and not isinstance(evaluate(loader.args[1]), str):
            return None
        value = evaluate(loader.args[0])
        return value if isinstance(value, str) and not value.startswith("\x1b") else None
    except (a.ParseError, ValueError, TypeError, ArithmeticError, RecursionError):
        return None
