"""The structurer turns block graphs into if/while code and refuses what it cannot express."""
import pytest

from deobf import luaast as A, structure
from deobf.structure import StructureError, lift_dispatch_loops

from test_simplify import run_plain


def lift(src):
    chunk = A.parse(src)
    done, failed = lift_dispatch_loops(chunk)
    return A.dump(chunk), done, failed


def machine(blocks, pre="local a = 0"):
    """Hand-written state machine in the dispatch-loop form the decompilers emit."""
    body = "\n".join(
        ("if" if i == 0 else "elseif") + " pc == %d then\n%s" % (k, v) for i, (k, v) in enumerate(sorted(blocks.items()))
    )
    return "local function f()\n%s\nlocal pc = 1\nwhile true do\n%s\nelse return end\nend\nend\nprint(f())" % (pre, body)


CASES = {
    "diamond": {1: "if a == 0 then pc = 2 else pc = 3 end", 2: "a = a + 10 pc = 4", 3: "a = a + 20 pc = 4", 4: "return a"},
    "while": {1: "a = 0 pc = 2", 2: "if a < 5 then pc = 3 else pc = 4 end", 3: "a = a + 1 pc = 2", 4: "return a"},
    "break_inside": {
        1: "a = 0 pc = 2",
        2: "a = a + 1 if a > 3 then pc = 4 else pc = 3 end",
        3: "if a % 2 == 0 then pc = 2 else pc = 5 end",
        4: "return a",
        5: "a = a + 100 pc = 2",
    },
    "nested_loops": {
        1: "a = 0 pc = 2",
        2: "if a < 3 then pc = 3 else pc = 8 end",
        3: "a = a + 1 pc = 4",
        4: "if a < 100 then pc = 5 else pc = 8 end",
        5: "a = a * 2 if a > 20 then pc = 8 else pc = 4 end",
        8: "return a",
    },
    "two_exits_from_loop": {
        1: "a = 0 pc = 2",
        2: "a = a + 1 if a == 4 then pc = 5 elseif a > 9 then pc = 6 else pc = 2 end",
        5: "a = a + 1000 pc = 7",
        6: "a = a + 2000 pc = 7",
        7: "return a",
    },
    "and_or_chain": {
        1: "if a == 0 then pc = 2 else pc = 5 end",
        2: "a = a + 1 if a == 1 then pc = 3 else pc = 5 end",
        3: "a = a + 5 pc = 4",
        4: "return a",
        5: "a = -1 pc = 4",
    },
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_structured_form_prints_the_same(name):
    src = machine(CASES[name])
    out, done, failed = lift(src)
    assert run_plain(out) == run_plain(src), out
    assert failed == 0 and done >= 1
    assert "pc ==" not in out


def test_unstructurable_flow_is_left_as_a_dispatch_loop():
    # two entries into the middle of a loop body (irreducible)
    src = machine({1: "if a == 0 then pc = 2 else pc = 3 end", 2: "a = a + 1 pc = 3", 3: "a = a + 1 if a < 6 then pc = 2 else pc = 4 end", 4: "return a"})
    out, done, failed = lift(src)
    assert run_plain(out) == run_plain(src)


def test_split_term_rejects_computed_jumps():
    chunk = A.parse("local pc = 1\npc = other()")
    pc = chunk[0].vars[0]
    with pytest.raises(StructureError):
        structure.split_term(chunk[1:], pc)
