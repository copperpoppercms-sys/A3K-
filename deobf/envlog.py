"""Executor-side capture loader + analysis of captured loadstring*.txt dumps."""
import re

LOADER = r'''-- Env Log capture loader. Run this in your executor BEFORE the target script.
-- Every string handed to loadstring/load is saved as loadstring1.txt, loadstring2.txt, ... in the workspace folder.
-- Upload the highest-numbered file to the bot (.envlog) to analyse the final layer.
local n = 0
local env = (getgenv and getgenv()) or _G
local real_loadstring, real_load = env.loadstring or loadstring, env.load or load
local function dump(s)
    if type(s) == "string" then
        n = n + 1
        pcall(writefile, "loadstring" .. n .. ".txt", s)
    end
end
env.loadstring = function(s, ...)
    dump(s)
    return real_loadstring(s, ...)
end
if real_load then
    env.load = function(s, ...)
        dump(s)
        return real_load(s, ...)
    end
end
'''


def capture_index(name):
    m = re.search(r"loadstring\D*(\d+)", name, re.I)
    return int(m.group(1)) if m else 0


def pick_final(files):
    """files: [(name, text)] -> deepest layer (highest loadstring index, else the longest)."""
    return max(files, key=lambda f: (capture_index(f[0]), len(f[1])))
