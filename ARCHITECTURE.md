# How this repo works (for anyone picking it up)

Pipeline in `deobf/core.py::deobfuscate`:

1. `detect.py` - signature + technique detection (`analyze()` returns engines with reasons, techniques, score).
2. `passes.py` - text-level cleanup (escapes, `string.char`, concat, arithmetic, string tables). String-literal aware.
3. `decrypt.py` - inlines calls to string-decoder functions by instrumenting them (`__REC`) and running the script.
4. `sandbox.py` + `_worker.py` - runs untrusted Lua 5.1 in a subprocess (memory/time limits, no io/network, Roblox API proxies,
   cooperative scheduler for spawn/wait/pcall, event handlers fired afterwards). Records calls, urls, POSTs, prints, decoded strings.
5. `patches.py` - repairs known obfuscator bugs in the sandbox copy only (e.g. Hercules v1.6 operand clobber).
6. `luaast.py` / `simplify.py` / `astclean.py` - parser, simplifier and the verified cleanup (runs before/after, keeps original if behaviour differs).
7. `recon.py` - turns the raw call trace into script-like output; `logger.py` - stealer/exfil rules and removal; `fetch.py` - SSRF-guarded downloads.

Bot: `bot/bot.py` (discord.py, prefix `.`). Config via env or `.env` (`DISCORD_TOKEN`, `BOT_PREFIX`, `MAX_JOBS`, `ALLOW_SCRIPT_FETCH`). Never commit a token.

Tests: `pytest` (98 tests). Real obfuscator output lives in `tests/samples/` (see its README); `tools/` regenerates it from the open-source obfuscators.
`tests/test_simplify.py` fuzzes the simplifier: it must never change what a program prints.

Known gaps: no bytecode lifter for vm protectors (they get a behaviour reconstruction), Luraph/MoonSec/Moonveil/WeAreDevs-vm untested on real output.
