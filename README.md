# A3K Deobfuscator

Lua / Luau deobfuscator with a Discord bot front-end (`.deobf`, `.luraph`, `.wearedevs`, `.l`, `.get`, ...).

**39 detector profiles**, including 25 additional Lua/Luau community protectors and services.
See [detection coverage](docs/DETECTION.md) for the full list, signature evidence, and limits.
All profiles use the existing recovery pipeline on a best-effort basis; a detector entry does
not mean a dedicated devirtualizer. Detection cannot be guaranteed for every release or a
script whose identifying markers have been removed. Match scores are heuristic, not probabilities.

## How it works

1. **detect** - signature + structure scoring per obfuscator (`deobf/detect.py`, add engines in `ENGINES`).
2. **static passes** - string-literal-aware: escape decoding, `string.char` folding, concat folding, hex/arith folding, string-table inlining, literal `loadstring("...")()` unwrap.
3. **sandbox** - the script runs in a separate, resource-limited Lua 5.1 process with no io/network/file access. `loadstring`/`load` chunks are captured (each one is the next layer), every Roblox/executor API is a recording proxy. Layers repeat until nothing new is captured.
4. **logger scan** - webhook / telegram / cookie / ip-grab / inventory-harvest rules; `Remove logger` rewrites exfil URLs and neutralises POST statements, keeping the script compilable.

Literal loaders can also use long-bracket strings, a literal chunk name, `assert`,
and nested `string.reverse`, `string.sub`, `string.char`, `string.lower/upper`,
`string.rep`, or `table.concat` expressions. These are evaluated from a restricted
syntax tree without executing input code, including in `--static` / `.static` mode.
Static table folding checks every use and declines tables that are mutated,
aliased, passed to a function, or shadowed. Malformed escapes stay intact for diagnosis.

Each result records layer history, an output kind, elapsed time, and a stop reason.
Repeated layers, excessive decoded sizes, and exhausted budgets return partial
results with an explanation. Logger findings from outer layers are retained.
Behavior traces retain their label and any runtime/timeout comments, including
when the VM payload was inside another loader.
Output headers say **Analyzed**, rather than claiming every file was deobfuscated.
Reports include `recovery_status`: `behavior-only`, `not-recovered`, `partial`,
`source-output`, or `unchanged`. Sources that retain a high heuristic obfuscation
score without a usable trace are labeled `still-obfuscated-source`. This score
can misclassify code; neither a low score nor a clean run proves original source
recovery. Discord uses `trace_` and `unrecovered_` attachment names and explicit
recovery messages for those outcomes.

When a loader requests a remote script but no response is available, the original
loader is retained and reported as `partial-source` / `unresolved-http`. An empty
sandbox HTTP response is never counted as recovered source. JSON reports list
`unresolved_urls`; sandbox traces list `unresolved_gets`. Nested fetching remains
opt-in (`--fetch` or `ALLOW_SCRIPT_FETCH=1`), and successful authorized retrieval
does not imply a key system has been satisfied or a VM has been devirtualized.

The sandbox supports Lua 5.3-style floor division (`//`) through a syntax-aware
compatibility pass on the execution copy. It preserves precedence, evaluates each
operand once, and captures the standard floor operation before the input can
shadow it. Compatibility notes are included in JSON reports. The original
deliverable is not rewritten just to make it execute. This uses Lua 5.1 double
precision, rejects zero divisors, and is not a full Lua 5.3/5.4 or Luau runtime.
Very large integer arithmetic and protected `__idiv` metatables are not emulated.
At most 256 floor-division operators are translated in one chunk.

Luau compound assignments (`+=`, `-=`, `*=`, `/=`, `//=`, `%=`, `^=`, `..=`)
are also translated in the sandbox copy, up to 256 per chunk. Indexed targets,
keys, and their old values are evaluated once before the right-hand expression.
Source line numbers are preserved to avoid breaking line-sensitive integrity
checks. Sequence and color keypoint constructors are available as recording
proxies, like the other Roblox API stubs.

Unresolved syntax errors now produce `syntax-error` / `unexecuted-source`, rather
than presenting minor static edits as successful payload analysis.

## Source recovery for non-vm obfuscation

When a script is not a vm protector, the bot parses it (`deobf/luaast.py`, a full Lua 5.1 parser with scope resolution), simplifies it (`deobf/simplify.py`) and prints readable source:

- constant folding and flow-sensitive constant propagation, opaque predicates and dead branches removed, one-shot `while ... break` loops unrolled
- junk locals/functions/assignments removed, builtin aliases (`local p = print`) inlined, constant tables indexed away
- inline string decryptors evaluated in the sandbox, integrity-check blocks removed, random identifiers renamed

The cleaned source is only used after **both versions are run in the sandbox and behave identically** (same calls, prints and errors); otherwise the original is kept and the note says why. The simplifier itself is fuzzed: 9,000 random programs (loops, closures, tables, shadowing, early exits) must print the same before and after, and `tests/test_simplify.py` keeps 300 + 200 of them plus 28 handwritten edge cases. Real example: Hercules *heavy* (12 KB of anti-tamper, control-flow flattening, junk code, encoded strings) comes back as 954 bytes of source.

## Coverage (read before trusting it)

Validated against **real output** of obfuscators built from their public source (`tests/samples/`):

| obfuscator | result |
| --- | --- |
| Prometheus Weak / Medium / Strong (VM + encrypted constant array) | full behaviour reconstruction, every API call + arguments + prints, decoded strings |
| Hercules light / balanced / heavy | light: behaviour; balanced: source recovered (string decoder inlined); heavy: behaviour up to the point Hercules' own output crashes |
| IronBrew 2 (default settings) | full behaviour reconstruction |

One user-provided sample carrying a WeAreDevs watermark now produces an observable
GUI behavior trace after compound-assignment compatibility and sequence-keypoint
stubs were added. Its original source was not recovered; the watermark does not
prove who produced it. The third-party script is not included in this repository.

Not tested against real output (no samples reachable): Luraph (any version), MoonSec, Moonveil, IronVeil, MWP, Luast. They go through the same pipeline, but "works on every Luraph" is **not** claimed - Luraph changes its VM between releases and may detect the sandbox. Send real samples to extend this table.

Also covered by tests: a gui script with button callbacks, input handlers, spawned loops, `wait()` inside `pcall`, `Heartbeat` and an infinite loop, obfuscated with Prometheus Strong, IronBrew 2 (string encryption) and Hercules heavy, plus IronBrew 2 layered over Prometheus. A third-party sample (Rerubi VM loader from an archived obfuscator repo) also recovers correctly.

How VM protectors are handled: there is no bytecode lifter. The script is run in the sandbox under a small cooperative scheduler (`spawn`/`wait`/`delay`/`pcall` behave like Roblox) and everything it does is recorded: API calls in order with arguments, urls, POSTs, prints, decoded strings. Event handlers (`:Connect`, `On*` properties) are fired once afterwards, with the second argument true and false, so code behind buttons and key presses shows up. Created objects get variables (`local v1 = Instance.new(...)`) and repeated service/property chains get local aliases. `--fetch` / `ALLOW_SCRIPT_FETCH=1` additionally downloads urls passed to `HttpGet` (public addresses only) so `loadstring(game:HttpGet(url))()` loaders can be followed.

Limits: VM-based protectors (Luraph, IronBrew, MoonSec, ...) may never hand their payload to `loadstring`, so results can be a behavior trace (`.l`) rather than source. Unsupported Luau syntax (`continue`, type annotations) still does not compile in the Lua 5.1 sandbox; static passes run and the dynamic stage reports a syntax error. Traces contain only observations from the bounded sandbox run and simulated callbacks; they are not guaranteed complete or runnable scripts. Signatures are heuristics.

## Run

```
pip install -r requirements.txt
python -m deobf script.lua                 # auto-detect + deobfuscate
python -m deobf script.lua -e luraph       # force engine
python -m deobf script.lua --detect
python -m deobf script.lua --trace         # observable behaviour
python -m deobf script.lua --static        # literal recovery without running input
python -m deobf script.lua --json -o report.json
python -m deobf script.lua --detect --json
python -m deobf script.lua --max-layers 16 --seconds 5 --timeout 30
python -m pytest
```

## Discord bot

1. Create an app at https://discord.com/developers, enable **Message Content Intent**, copy the bot token, invite with `bot` scope + send messages / attach files.
2. `DISCORD_TOKEN=... python -m bot.bot` (optional: `BOT_PREFIX=.`).

Commands: `.deobf` `.keydeobf` `.<engine>` `.static` `.report` `.status` `.detect` `.l`
`.get <url>` `.envlog` `.engines` `.help`. Input = up to 10 attachments, a code block,
a url, or a reply to a sample. `.get` only fetches public addresses (redirects re-checked)
up to 2 MB. A failed file does not discard successful results from the same batch.

Analysis jobs run in disposable subprocesses, with a bounded queue, a wall-clock
deadline, and a Linux memory ceiling. Cancelling or timing out a job kills its
process group, including Lua workers. Worker environments exclude the Discord token.
Defaults: `MAX_JOBS=1`, `MAX_QUEUE=8`, `JOB_TIMEOUT=75`, `JOB_MEMORY_MB=512`.
The analysis process and its descendants inherit that per-process memory ceiling;
`SANDBOX_MEMORY_MB` can lower it for Lua workers. These are per-process limits,
not a cap on the total memory of your hosting service. Start with one concurrent
job on Railway; raising concurrency can increase both RAM usage and billing.
Reports/attachments larger than 7 MB are sent as gzip files.

Use `.keydeobf <URL>` (or attach a Lua file) to enable public nested downloads for
that job without changing Railway variables. It returns the best-effort source or
trace and a JSON report together. Engine-picker retries retain this fetching mode.
It uses the same recovery engine and limits as `.deobf`; it does not supply keys,
authenticate to licensing services, or guarantee original source recovery. A valid
authorized session may still be required before a server supplies its payload.

Missing/invalid bot tokens and disabled Message Content Intent get explicit startup
messages. A Discord bot does not need an HTTP health-check path. See
[Railway operation](docs/RAILWAY.md) for deployment and troubleshooting.
