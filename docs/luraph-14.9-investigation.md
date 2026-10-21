# User sample: MM2-Halloween2026.luau

Inspected on 2026-10-21, from the user-provided public URL:
https://cdn.project-reverse.org/MM2-Halloween2026.luau

- Size: 285,444 bytes, three physical lines.
- SHA-256: `de6dd9372734628d78f4e2db4936cde0cc3929758da5d1767412f28265243bd3`.
- Header identifies Luraph Obfuscator v14.9. This is an identification claim,
  not independent proof of the producer or configuration.
- Initial baseline: `syntax: obf:3: malformed number near '0b1000'`.
- After numeric lowering: rejected `continue` statements.
- After loop lowering: missing `bit32.countlz` / `countrz`, then missing buffer.
- After bounded buffer support: VM execution reached generalized table iteration.
- After generalized iteration support: 31 strings observed; no captured source
  layers, payload API trace entries, or callback registrations. The sample still
  exhausted a 30-second execution budget.

The executable sandbox copy lowers 694 numeric literals, 41 compound assignments,
98 continue statements, and 35 generic iteration headers when starting directly
from the original bytes. In the normal pipeline, the static numbers pass handles
the literals first, so its compatibility list does not repeat that count.

The remaining blocker is unresolved. The observed timeout alone does not prove
whether it is integrity checking, a missing environment behavior, or expensive
VM work. It is not evidence that payload source was recovered. The bot has no
Luraph VM decompiler, and no version-wide recovery claim is made.

All execution used the disposable resource-limited worker with nested HTTP
downloads disabled. The third-party script and its full source are intentionally
not copied into this repository. The new regression tests exercise the relevant
language and library semantics with small, independently written programs,
including IEEE-754 buffer reads checked against Python's `struct` library.

To reproduce locally after downloading the same public sample, use:

```sh
python -m deobf MM2-Halloween2026.luau --seconds 30 --timeout 60 --json -o report.json
```

Nested downloads stay disabled unless `--fetch` is supplied. Do not interpret
a diagnostic trace or a decoded constant list as decompiled source.
