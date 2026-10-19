# Running A3K Deobfuscator on Railway

Connect the repository and deploy its Dockerfile. The start command is
`python -m bot.bot`. Enable **Message Content Intent** on the application's Bot
page in the Discord Developer Portal. Set `DISCORD_TOKEN` in Railway's service
Variables, not in repository files. Railway must deploy the updated commit before
new commands are available; if automatic deploys are disabled, redeploy manually.

## Small-instance settings

Start with `MAX_JOBS=1` (the default). `.status` shows active/waiting work, and
`.engines` also includes queue counts. `MAX_QUEUE=8` permits eight waiting jobs
in addition to the active one. A full queue rejects new analyses with an explanation.

`JOB_TIMEOUT=75` is the hard wall-clock limit in seconds. Normal recovery also
has a shared execution budget of 60 seconds. `JOB_MEMORY_MB=512` bounds each
analysis process on Linux and is inherited by its Lua subprocesses.
`SANDBOX_MEMORY_MB=384` can further lower the Lua worker limit. Memory limits are
per process; the Discord process, Python analysis process, and Lua worker can
all use memory at once, so their total can exceed either individual limit.
Choose limits that leave room in the Railway service allocation.

## Diagnosing failures

- **Discord rejected DISCORD_TOKEN**: replace the variable with the current bot
  token from Discord's Bot page and redeploy. A reset invalidates the old token.
- **Privileged intents**: enable Message Content Intent and restart.
- **Killed / out of memory**: keep concurrency at one, lower per-worker limits,
  inspect Railway memory metrics, and use `.static` for large literal loaders.
  Very small allocations may need to be increased for VM analysis.
- **Job time limit**: the job is stopped, its worker group is killed, and the queue
  continues. Use `.static`, submit a smaller script, or adjust the time limit within
  the supported 5–300 second range.
- **Syntax error / unexecuted-source**: the script did not run. Minor static edits
  do not prove that the protected payload was reached. The sandbox now translates
  floor division (`//`) in its execution copy; other Luau-only syntax may remain
  unsupported. Check the `compatibility` and `error` fields in `.report`.
- **Health check failed**: remove an HTTP health-check path configured for this
  service. The bot connects outward to Discord; it does not serve a website.
- **Queue full**: let current work finish, then retry. Raising concurrency increases
  memory use and may increase Railway costs.

Use `.report` for JSON containing source, detection candidates, layer history,
findings, output kind, runtime errors, and stop reason. `behavior-trace` means only
observed execution paths were reconstructed; it does not claim original VM source.
Lua 5.1 sandbox limits still apply to Luau-only syntax and unsupported protectors.

## Remote loaders and key systems

`.get` retrieves the supplied URL. That URL may itself return another loader.
With nested fetching disabled, `.report` then lists unresolved URLs instead of
reporting the empty sandbox response as a decoded payload. Submit the public
downloaded Lua file (or its direct URL) to analyze the next stage, or explicitly
enable nested fetching with `ALLOW_SCRIPT_FETCH=1`. Only public-address GETs are
supported, with the existing size and time limits.

An unresolved URL alone is not evidence of a key system. A server-side key check
may require a valid authorized session before it supplies a payload. Missing
responses cannot validate AST cleanup. A partial sandbox result is not evidence
of successful authentication, and the model cannot recover code that was never provided.
