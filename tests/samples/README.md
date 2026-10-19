Real obfuscator output, generated from `tests/samples/src1.lua` with the open-source obfuscators:

- `prom_*.lua`: Prometheus (prometheus-lua/Prometheus) presets Weak / Medium / Strong
- `herc_*.lua`: Hercules 2.0.1 (zeusssz/hercules-obfuscator) presets light / balanced / heavy

`herc_heavy.lua` is kept because Hercules' own output raises `attempt to perform arithmetic on global 'i'` at run time
(its garbage-code pass moves a loop body out of scope); the tests assert we report that faithfully.
- `ib2_basic.lua`: IronBrew 2 (Trollicus/ironbrew-2, built locally on .NET 8 with Lua 5.1 tooling), default settings
- `herc_web_162_congrats.lua`: Hercules v1.6.2 (web version) on the "congrats" test script. The obfuscated script itself crashes in plain Lua 5.1 too (`attempt to index field '?'` when building the notification table), so the tests only assert what is recoverable: engine, the decoded constants and the first API call.
