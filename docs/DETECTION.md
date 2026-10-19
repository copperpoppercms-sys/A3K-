# Detection coverage

A3K Deobfuscator has 39 profiles: the original 14 plus the 25 below.
The additions include established Roblox protectors, loader services, and smaller
open-source Lua projects; this is not a ranking of the 25 most popular products.

## What detection means

A match identifies a **claimed producer or recognizable template**. A watermark can
be forged, stripped, or changed, and similar forks share code. Scores (0–99) are
heuristic evidence weights, not measured probabilities. Unknown is a valid result.
No tool can definitely identify every version of all these protectors.

**Marker only** means the profile recognizes an explicit `protected/obfuscated/
encrypted using NAME`, `NAME Obfuscator`, or `NAME vVERSION` marker; it does not
mean such a marker is necessarily emitted by the original tool. An ordinary
identifier named after a new profile is not sufficient. Those profiles are
provisional until real outputs provide a stable fingerprint.

All 39 names are available through `.NAME`, `.detect`, `.engines`, the grouped
Discord engine picker, and `python -m deobf input.lua -e NAME`. Forcing an engine
is a hint to the unified pipeline, not a dedicated decoder or access-key bypass.

## Additional profiles

| Command / CLI name | Protector and source reference | Recognition and limitations |
| --- | --- | --- |
| `luarmor` | [Luarmor](https://github.com/treeofplant/luarmor/tree/1edf2dbe1d3e1af0fbc8ea0a4eb5c5d8ab7dccb7) | Hosted loader URL; identifies the loader service, not the underlying VM. Fetching remains opt-in. |
| `psu` | [PSU / Perth](https://github.com/endo4x/PSUSourceCode/tree/7a10610df0b973397b4e166604e5a79941a0d162) | PSU domain and protection marker. No dedicated PSU bytecode lifter. |
| `synapsexen` | [Synapse Xen](https://github.com/lovesyko/SynapseXenDeobfuscator/tree/8043b602dcf8956404596d466d6b18ff48d1e078) | Version marker and SynapseXen namespace. |
| `securelua` | [Synapse SecureLua](https://github.com/SnowyShiro/Synapse-SecureLua-Deobfuscator/tree/5eedd014a1308d5dbcae9a4ab8ae3309ff7bf895) | Marker only; actual Alpha/Beta/Charlie VM payload recognition unvalidated. |
| `aztupbrew` | [AztupBrew](https://github.com/noahcoolboy/bad-aztupbrew-deobfuscator/tree/210e76ae23a0610e0f0c40322eb209cc09f0398a) | AztupBrew fork watermark; kept distinct from IronBrew. |
| `xfuscator` | [XFuscator](https://github.com/efrederickson/XFuscator/tree/7e58176d3fe3cd9349c64652c61fed1cf2092fa1) | Marker or combined Step2 dispatch table/loader fingerprint. |
| `luaseel` | [LuaSeel](https://github.com/Direnta/LuaSeel/tree/2f46b9fd71165a791fa44e0a29e08e9ec0c4bf71) | Default watermark or fixed boolean helper plus loader aliases. |
| `luaxen` | [LuaXen](https://github.com/luauminati/LuaXen/tree/a6e66ac7307a124f876975fae44693c6ba0fc388) | Marker only; no reliable fingerprint yet for its different backends. |
| `nodet` | [Nodet / lvm-protect](https://github.com/nodeiwnl/lvm-protect/tree/b944e30a2257b478150a5cb6ee612f9a79646792) | Default Nodet protection marker. Binary Lua chunks remain rejected by the sandbox. |
| `essafuscator` | [EssaFuscator](https://github.com/EssaPrime/EssaFuscatorOpenSource/tree/475f0afa1cd46b486a4d8aa676db4cc9a4b597ca) | Marker only; output VM unvalidated. |
| `zartha` | [Zartha](https://github.com/TimOdemchuk/Zartha-Obfuscator/tree/6335129e6bb029ac4654ca04d8a31d31da19c5cf) | Default File generated using [Zartha] watermark, which is customizable. |
| `opiens` | [Opiens](https://github.com/mmaamaaa/Opiens/tree/d37679249d866e399365672661946f707513bd71) | Marker or paired DecryptSTR/DecryptINT templates; renamed helpers may not match. |
| `muna` | [Muna](https://github.com/Akka0/Muna/tree/b177e0b9b1c6adfef16b8c78699db8c3f503f2fc) | Marker only; generated code has no verified stable producer marker. |
| `rbxluaobfuscator` | [RBXLuaObfuscator](https://github.com/kosuke14/RBXLuaObfuscator/tree/e7a21ecdbeca076d7e314d6da2db6fa644b34fb6) | Marker or combined fixed boolean helper and byte-table fingerprint. |
| `bluesecure` | [blueSecure](https://github.com/KuriWasTaken/blueSecure/tree/64f1b7eec662509c5f1b04423ee4e4dbc395d5ad) | Protection marker and distinctive runtime integrity identifiers. |
| `luacry` | [luacry](https://github.com/UlisseMini/luacry/tree/063a3b0a274617108fde2039ddbe5791013a46d9) | Marker or combined external-key prompt/Base85/XOR loader. External key is not recovered; Lua 5.3 string.pack is unavailable in the Lua 5.1 sandbox. |
| `bytelua` | [ByteLuaObfuscator](https://github.com/jmesfo0/ByteLuaObfuscator/tree/691820a3665ebd1d402b61ccc08e24c3baa5d592) | Marker only; default output need not contain the producer name. |
| `zenfus` | [Zenfus](https://github.com/Zaenalos/Zenfus/tree/94d4ae19edfc388e6badce3d2bd44126945c6e11) | Marker only; Lua 5.4 syntax cannot execute in the Lua 5.1 sandbox. |
| `fusgato` | [FusGato](https://github.com/adzwang/fusgato-v1/tree/fa8c93d6c13e5cadaa26b0477a319eef697e373a) | Default FusGato Discord watermark; kept distinct from its IronBrew header. |
| `shittyobfuscator` | [ShittyObfuscator](https://github.com/KuriWasTaken/ShittyObfuscator/tree/4c1143b0d7924423bd024bf38d5ab660c0013b83) | Marker or combined Base64 decoder/getfenv call wrapper. |
| `luarh` | [LuaRH V4](https://github.com/CrxyScripter/LuaRH-V4/tree/1844b94659db82748684883ff4205e24d12a9d5a) | Encrypted with LuaRH default marker. |
| `joker` | [Joker Obfuscator](https://github.com/WiiZARDD/JokerObfuscator/tree/2c57b391532c14a3e9102c8537ea3f287b5ffb07) | Domain/protection marker only; no validated VM sample. |
| `azurevm` | [AzureVM](https://github.com/LionHub-Pino/AzureVM/tree/e0bf0c08a808962506d86421edf40b231a3adea0) | Protection marker or AVM_OBFUSCATED macro. Some historical modes emit Luraph decoy headers. |
| `zero` | [Zero Obfuscator](https://github.com/FakeModz/Zero-Lua-Roblox-Obfuscator/tree/284d24c864bbc18400c567289419788bfe969143) | Marker only; no validated output sample or structural fingerprint. |
| `novela` | [Novela](https://github.com/JustASocial/Novela/tree/e5edc440393eb4d242683bdb7a462229930a3d23) | Marker only; no validated backend output sample or structural fingerprint. |

## Recovery and validation

Every added profile can attempt existing escape/string/constant cleanup, static
loadstring unwrapping, sandbox layer capture, and verified AST simplification. VM
profiles can produce observable behavior traces; these are not the original source
and cover only executed paths. Unknown or stripped-marker scripts still use the
same best-effort pipeline through `.deobf`.

`tests/test_catalog.py` checks all 25 explicit markers, evidence reports, case
handling, ordinary-identifier false positives, static recovery routing, all Discord
commands, picker capacity, and help/catalog message limits. These minimal marker
fixtures are synthetic, not proof of successful recovery of each product's output.
`tests/test_additional_samples.py` separately checks generated LuaSeel and XFuscator
Step2 output, including behavior and layer recovery. Existing real-output regression
tests for Prometheus, Hercules, IronBrew, and Rerubi remain in place.
