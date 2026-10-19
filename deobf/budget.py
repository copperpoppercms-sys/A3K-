"""A per-job deadline shared by nested sandbox and decoder calls."""
from contextlib import contextmanager
from contextvars import ContextVar
from time import monotonic

_deadline = ContextVar("deobfuscation_deadline", default=None)


@contextmanager
def time_budget(seconds):
    parent = _deadline.get()
    deadline = monotonic() + seconds
    token = _deadline.set(min(deadline, parent) if parent is not None else deadline)
    try:
        yield
    finally:
        _deadline.reset(token)


def remaining(cap=60):
    deadline = _deadline.get()
    return max(0, min(cap, deadline - monotonic())) if deadline is not None else cap
