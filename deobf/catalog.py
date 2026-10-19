"""Additional community protectors. Evidence and limitations: docs/DETECTION.md.

Markers identify a claimed producer, not authenticity or guaranteed recovery.
Structural signatures deliberately require several features together.
"""


def _brand(name):
    # Avoid identifying ordinary variables or a casual mention as a protector.
    return (rf"\b(?:protected|obfuscated|encrypted|generated)\s+(?:by|with|using)\s+\[?{name}\b|\b{name}\s+(?:obfuscator|protected\s+runtime|v\d)",
            85, "protector name in protection/version marker")


EXTRA_ENGINES = [
    ("luarmor", "Luarmor (loader service)", True, [
        (r"https?://api\.luarmor\.net/files/v\d+/(?:loaders|l)/", 95, "Luarmor hosted loader URL"), _brand("luarmor")]),
    ("psu", "PSU / Perth", True, [
        (r"\bpsu\.dev\b", 90, "PSU service domain"), _brand("psu")]),
    ("synapsexen", "Synapse Xen", True, [
        _brand(r"synapse\s*xen"), (r"\bSynapseXen_\w+", 70, "SynapseXen namespace")]),
    ("securelua", "Synapse SecureLua", True, [_brand(r"(?:synapse\s+)?securelua")]),
    ("aztupbrew", "AztupBrew", True, [
        (r"\bAztupBrew\s*\(Fork of IronBrew2\)", 99, "AztupBrew fork watermark"), _brand("aztupbrew")]),
    ("xfuscator", "XFuscator", False, [
        _brand("xfuscator"),
        (r"(?s:____\s*=\s*\{\s*function\(\.\.\.\).*?math\.frexp.*?1234567890.*?return\s+____\[11\])", 75, "XFuscator Step2 dispatch table and loader")]),
    ("luaseel", "LuaSeel", False, [
        _brand("luaseel"),
        (r"(?s:function\s+IllIlllIllIlllIlllIlllIll\(IllIlllIllIllIll\).*?919\s*\+\s*636.*?IllIIllIIllIII\s*=\s*loadstring)", 75, "LuaSeel boolean helper and loader aliases")]),
    ("luaxen", "LuaXen", True, [_brand("luaxen")]),
    ("nodet", "Nodet / lvm-protect", True, [_brand(r"nodet(?:\s+obfuscator)?")]),
    ("essafuscator", "EssaFuscator", True, [_brand("essafuscator")]),
    ("zartha", "Zartha", True, [
        _brand("zartha"), (r"File generated using \[Zartha\]", 95, "Zartha default watermark")]),
    ("opiens", "Opiens", True, [
        _brand("opiens"),
        (r"(?s:local function DecryptSTR\(b\).*?local function DecryptINT\(b\))", 65, "Opiens paired string/integer decryptor templates")]),
    ("muna", "Muna", True, [_brand("muna")]),
    ("rbxluaobfuscator", "RBXLuaObfuscator (kosuke14)", False, [
        _brand("rbxluaobfuscator"),
        (r"(?s:function\s+\w*IllIlllIllIlllIlllIlllIll1\(.*?312\s*\+\s*456.*?IlIlIlIlIlIlIlIlII\s*=\s*\{)", 75, "RBXLuaObfuscator boolean helper and byte table")]),
    ("bluesecure", "blueSecure", False, [
        _brand("bluesecure"),
        (r"\b(?:ProtectedByBlueSecure|loadBlueSecSmellyBallz)\b", 90, "blueSecure runtime integrity identifier")]),
    ("luacry", "luacry (external key)", False, [
        _brand("luacry"),
        (r"(?s:argument #1 must be the key.*?0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ.*?invalid length.*?\(loadstring or load\)\(xor\(dec\()", 80, "luacry keyed XOR/Base85 loader")]),
    ("bytelua", "ByteLuaObfuscator", True, [_brand("byteluaobfuscator")]),
    ("zenfus", "Zenfus (Lua 5.4)", True, [_brand("zenfus")]),
    ("fusgato", "FusGato", True, [
        (r"\blocal\s+FusGato\s*=\s*[\"']discord\.gg/cWQAy6Z697", 99, "FusGato default watermark"), _brand("fusgato")]),
    ("shittyobfuscator", "ShittyObfuscator (KuriWasTaken)", False, [
        _brand(r"shitty?obfuscator"),
        (r"(?s:function dec\(data\).*?return\(function\(\.\.\.\).*?getfenv\(\)\[dec\(.*?end\)\(\{\},getfenv,table\.remove\))", 65, "ShittyObfuscator Base64 call wrapper")]),
    ("luarh", "LuaRH V4", False, [_brand("luarh")]),
    ("joker", "Joker Obfuscator", True, [
        _brand("joker"), (r"\bjokerobfuscator\.com\b", 90, "Joker service domain")]),
    ("azurevm", "AzureVM", True, [
        _brand("azurevm"), (r"\bAVM_OBFUSCATED\b", 90, "AzureVM runtime macro")]),
    ("zero", "Zero Obfuscator", True, [_brand(r"zero(?:[ -]lua)?")]),
    ("novela", "Novela", True, [_brand("novela")]),
]
