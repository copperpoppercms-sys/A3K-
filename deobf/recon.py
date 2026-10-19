"""Turn the raw call trace from the sandbox into something that reads like a script."""
import re

_KEYWORDS = frozenset("and break do else elseif end false for function if in local nil not or repeat return then true until while game workspace script".split())
_REPEAT = re.compile(r"  -- repeated \d+ times$")
_CB = re.compile(r"^-- callback: (.*?)( \[second arg false\])?$")


def _cut_points(line):
    """Indexes in the leading call chain (up to the first space at depth 0) where it can be split into a prefix."""
    pts, depth, i, n = [], 0, 0, len(line)
    while i < n:
        c = line[i]
        if c == '"':
            i += 1
            while i < n and line[i] != '"':
                i += 2 if line[i] == "\\" else 1
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif depth == 0 and c == " ":
            break
        elif depth == 0 and c in ".:" and i > 0:
            pts.append(i)
        i += 1
    return pts


def _alias_name(prefix, taken):
    m = re.search(r'\("([A-Za-z_]\w*)"\)$', prefix) or re.search(r"[.:]([A-Za-z_]\w*)$", prefix) or re.search(r"^([A-Za-z_]\w*)", prefix)
    base = m.group(1) if m else "obj"
    base = re.sub(r"\W", "", base) or "obj"
    if base[0].isdigit():
        base = "_" + base
    name, k = base, 2
    while name in taken or name in _KEYWORDS:
        name, k = base + str(k), k + 1
    taken.add(name)
    return name


def _drop_callback_duplicates(lines):
    """A second-arg-false callback run is kept only when it did something the first run did not."""
    sections, cur = [], None
    out = []
    for ln in lines:
        m = _CB.match(ln)
        if m:
            cur = {"label": m.group(1), "variant": bool(m.group(2)), "head": ln, "body": []}
            sections.append(cur)
            out.append(cur)
        elif cur is not None and not _REPEAT.search(ln):
            cur["body"].append(ln)
        else:
            out.append(ln)
    res, first = [], {}
    for item in out:
        if isinstance(item, str):
            res.append(item)
            continue
        if not item["variant"]:
            first[item["label"]] = set(item["body"])
            if item["body"]:
                res.append(item["head"])
                res += item["body"]
            continue
        extra = [b for b in item["body"] if b not in first.get(item["label"], set())]
        if extra:
            res.append("-- callback: %s (when the second argument is false)" % item["label"])
            res += item["body"]
    return res


def tidy(lines):
    lines = _drop_callback_duplicates(lines)
    # a bare call whose result is used by a later line is not a statement of its own
    keep = []
    for i, ln in enumerate(lines):
        if ln.startswith("--") or ln.startswith("print(") or ln.startswith("local "):
            keep.append(ln)
            continue
        if any(ln != later and ln in later for later in lines[i + 1:] if not later.startswith("--")):
            continue
        keep.append(ln)
    lines = keep
    taken = set()
    for _ in range(30):
        counts = {}
        for ln in lines:
            if ln.startswith("--") or ln.startswith("local ") or ln.startswith("print("):
                continue
            seen = set()
            for i in _cut_points(ln):
                p = ln[:i]
                if "(" in p and p not in seen:
                    seen.add(p)
                    counts[p] = counts.get(p, 0) + 1
            if "(" in ln and " " not in ln:
                counts[ln] = counts.get(ln, 0) + 1
        best = [(c, -len(p), p) for p, c in counts.items() if c >= 2]
        if not best:
            break
        c, _, prefix = max(best)
        name = _alias_name(prefix, taken)
        out, defined = [], False
        for ln in lines:
            if ln == prefix:
                if not defined:
                    out.append("local %s = %s" % (name, prefix))
                    defined = True
                continue
            if not ln.startswith("--") and not ln.startswith("local ") and (ln.startswith(prefix + ".") or ln.startswith(prefix + ":")):
                if not defined:
                    out.append("local %s = %s" % (name, prefix))
                    defined = True
                ln = name + ln[len(prefix):]
            out.append(ln)
        lines = out
    return lines
