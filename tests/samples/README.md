Real obfuscator output, generated from `tests/samples/src1.lua` with the open-source obfuscators:

- `prom_*.lua`: Prometheus (prometheus-lua/Prometheus) presets Weak / Medium / Strong
- `herc_*.lua`: Hercules 2.0.1 (zeusssz/hercules-obfuscator) presets light / balanced / heavy

`herc_heavy.lua` is kept because Hercules' own output raises `attempt to perform arithmetic on global 'i'` at run time
(its garbage-code pass moves a loop body out of scope); the tests assert we report that faithfully.
