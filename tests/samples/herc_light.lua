-- Lua 5.3+ / Luau compatibility polyfills
if not math.ldexp then math.ldexp = function(x, n) return x * 2 ^ n end end
if not math.frexp then math.frexp = function(x)
    if x == 0 then return 0, 0 end
    local exp = math.floor(math.log(math.abs(x)) / math.log(2)) + 1
    local mantissa = x / 2 ^ exp
    return mantissa, exp
end end
if not loadstring and load then loadstring = load end
if not loadstring then loadstring = function(s) return load(s) end end

local gHZTFwtF;gHZTFwtF=print;local nxghHSlv=game:GetService("Players");local LiUoYJSTTv=nxghHSlv.LocalPlayer;local IJjSpmYryUND="Hello, "..LiUoYJSTTv.Name.."!";gHZTFwtF(IJjSpmYryUND);for i=1,3 do gHZTFwtF("iteration",i*2) end local function zxQikBmBOxs(n)if n<2 then return n end return zxQikBmBOxs(n-1)+zxQikBmBOxs(n-2)end gHZTFwtF("fib(10) =",zxQikBmBOxs(10));game:GetService("StarterGui"):SetCore("SendNotification",{Title="Test",Text="ok"})