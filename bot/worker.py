"""One disposable analysis process per bot job. Input and output are JSON."""
import json
import os
import sys


def main():
    try:
        import resource
        limit = max(128, min(2048, int(os.environ.get("JOB_MEMORY_MB", "512")))) * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except (ImportError, OSError, ValueError):
        pass
    from dataclasses import asdict
    from deobf import analyze, deobfuscate, logger, trace_behavior

    job = json.loads(sys.stdin.buffer.read(3 * 1024 * 1024 * 6))
    action, source, options = job["action"], job["source"], job.get("options", {})
    if len(source) > 2 * 1024 * 1024:
        raise ValueError("script exceeds 2 MB input limit")
    if action == "deobfuscate":
        result = asdict(deobfuscate(source, **options))
    elif action == "trace":
        text, run = trace_behavior(source, **options)
        result = [text, asdict(run)]
    elif action == "analyze":
        result = analyze(source)
    elif action == "remove-logger":
        result = logger.remove(source)
    else:
        raise ValueError("unknown job action")
    sys.stdout.write(json.dumps({"ok": True, "result": result}, ensure_ascii=True))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # The Discord process handles this as a per-file failure, without losing
        # other results in a batch. Worker environments contain no bot token.
        sys.stdout.write(json.dumps({"ok": False, "error": type(e).__name__ + ": " + str(e)[:300]}))
