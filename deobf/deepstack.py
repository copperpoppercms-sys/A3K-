"""Run deeply recursive analysis (huge nested ASTs) on a thread with a big stack and a raised recursion limit."""
import contextvars
import sys
import threading

STACK_BYTES = 256 * 1024 * 1024
LIMIT = 60000


def call(fn, *args, **kwargs):
    box = {}
    ctx = contextvars.copy_context()

    def run():
        try:
            box["value"] = ctx.run(fn, *args, **kwargs)
        except BaseException as e:  # re-raised in the caller
            box["error"] = e

    old_limit, old_stack = sys.getrecursionlimit(), threading.stack_size()
    sys.setrecursionlimit(LIMIT)
    try:
        threading.stack_size(STACK_BYTES)
        t = threading.Thread(target=run, daemon=True)
        t.start()
    finally:
        threading.stack_size(old_stack)
    t.join()
    sys.setrecursionlimit(old_limit)
    if "error" in box:
        raise box["error"]
    return box["value"]
