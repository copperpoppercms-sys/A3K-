# deobfuscator-

Lua / Luau deobfuscator with a Discord bot front-end (`.deobf`, `.luraph`, `.wearedevs`, `.l`, `.get`, ...).

## How it works

1. **detect** - signature + structure scoring per obfuscator (`deobf/detect.py`, add engines in `ENGINES`).
2. **static passes** - string-literal-aware: escape decoding, `string.char` folding, concat folding, hex/arith folding, string-table inlining, literal `loadstring("...")()` unwrap.
3. **sandbox** - the script runs in a separate, resource-limited Lua 5.1 process with no io/network/file access. `loadstring`/`load` chunks are captured (each one is the next layer), every Roblox/executor API is a recording proxy. Layers repeat until nothing new is captured.
4. **logger scan** - webhook / telegram / cookie / ip-grab / inventory-harvest rules; `Remove logger` rewrites exfil URLs and neutralises POST statements, keeping the script compilable.

## Coverage (read before trusting it)

| kind | result |
| --- | --- |
| loadstring-layered / string-decoder protectors (wearedevs-style xor decoder, string tables, literal `loadstring`) | recovered source |
| VM protectors (Luraph, IronBrew, MoonSec, Moonveil, IronVeil, MWP, Luast) | **not decompiled** - you get the reconstructed behaviour: every API call, url, POST, and every string the VM decoded (via `string.char`/`table.concat`/`gsub`/`format`) |

There is no per-version bytecode lifter for any VM protector. "Works on every Luraph" is not true: Luraph changes its VM between releases and may detect the sandbox. Tested only on synthetic samples written from the known output shapes, not on real protected scripts.

Limits: VM-based protectors (Luraph, IronBrew, MoonSec, ...) never hand their payload to `loadstring`, so you get the behaviour trace (`.l`), not source. Luau-only syntax (`+=`, `continue`, type annotations) does not compile in the Lua 5.1 sandbox; static passes still run, the dynamic stage reports a syntax error. Signatures are heuristics.

## Run

```
pip install -r requirements.txt
python -m deobf script.lua                 # auto-detect + deobfuscate
python -m deobf script.lua -e luraph       # force engine
python -m deobf script.lua --detect
python -m deobf script.lua --trace         # observable behaviour
pytest
```

## Discord bot

1. Create an app at https://discord.com/developers, enable **Message Content Intent**, copy the bot token, invite with `bot` scope + send messages / attach files.
2. `DISCORD_TOKEN=... python -m bot.bot` (optional: `BOT_PREFIX=.`, `MAX_JOBS=4`).

Commands: `.deobf` `.<engine>` `.detect` `.l` `.get <url>` `.envlog` `.engines` `.help`. Input = up to 10 attachments, a code block, a url, or a reply to a sample. `.get` only fetches public addresses (redirects re-checked) up to 2 MB.
