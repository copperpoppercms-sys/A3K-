# A3K Deobfuscator architecture

Pipeline in `deobf/core.py::deobfuscate`:

1. `detect.py` + `catalog.py` - 39 detector profiles and technique detection (`analyze()` returns engines with reasons, techniques, score). Coverage and provisional marker-only entries are documented in `docs/DETECTION.md`.
2. `passes.py` + `literals.py` - text-level cleanup, scope-checked constant tables, bounded evaluation of literal loader expressions without running them.
3. `decrypt.py` - inlines calls to string-decoder functions by instrumenting them (`__REC`) and running the script.
4. `sandbox.py` + `_worker.py` - runs untrusted Lua 5.1 in a subprocess (memory/time limits, no io/network, Roblox API proxies,
   cooperative scheduler for spawn/wait/pcall, event handlers fired afterwards). Records calls, urls, POSTs, prints, decoded strings.
5. `patches.py` - repairs known obfuscator bugs in the sandbox copy only (e.g. Hercules v1.6 operand clobber).
6. `luaast.py` / `simplify.py` / `astclean.py` - parser, simplifier and the verified cleanup (runs before/after, keeps original if behaviour differs).
7. `recon.py` - turns the raw call trace into script-like output; `logger.py` - stealer/exfil rules and removal; `fetch.py` - SSRF-guarded downloads.

`compat.py` rewrites floor-division expression spans only in sandbox copies. The
same preparation applies to `sandbox.run` and compile-only `sandbox.syntax_ok`;
`syntax_ok(source, compatible=False)` checks the raw Lua 5.1 syntax. It preserves
source text outside those expressions and uses a fresh local helper with captured
standard functions. This provides double-precision compatibility, not a full
Luau/Lua 5.3 implementation. Syntax failures are labeled as unexecuted source.

`budget.py` shares an analysis deadline across decoder, sandbox, and AST verification
runs. `Result.to_report()` provides serializable results and layer history. Worker
completion is required for AST behavior verification; identical worker failures
are never accepted as proof of equivalence.

Bot: `bot/bot.py` (discord.py, prefix `.`). `jobs.py` admits a bounded number of jobs
and runs each in `bot/worker.py`; timeouts/cancellations kill the worker process
group. Analysis worker environments contain no Discord token. Batch results are
independent. Config via env or `.env` (see `.env.example`). Never commit a token.

Tests: `python -m pytest`. Real obfuscator output lives in `tests/samples/` (see its READMEs); `tools/` regenerates the original samples from the open-source obfuscators.
`tests/test_simplify.py` fuzzes the simplifier: it must never change what a program prints.

Known gaps: no bytecode lifter for vm protectors (they get a behaviour reconstruction), Luraph/MoonSec/Moonveil/WeAreDevs-vm untested on real output.
