local Players = game:GetService("Players")
local lp = Players.LocalPlayer
local greeting = "Hello, " .. lp.Name .. "!"
print(greeting)
for i = 1, 3 do
    print("iteration", i * 2)
end
local function fib(n) if n < 2 then return n end return fib(n-1) + fib(n-2) end
print("fib(10) =", fib(10))
game:GetService("StarterGui"):SetCore("SendNotification", {Title = "Test", Text = "ok"})
