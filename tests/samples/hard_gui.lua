local Players = game:GetService("Players")
local RunService = game:GetService("RunService")
local UIS = game:GetService("UserInputService")
local lp = Players.LocalPlayer

local gui = Instance.new("ScreenGui")
gui.Name = "RiftHub"
gui.Parent = game:GetService("CoreGui")

local frame = Instance.new("Frame")
frame.Size = UDim2.new(0, 200, 0, 100)
frame.Parent = gui

local btn = Instance.new("TextButton")
btn.Text = "Speed"
btn.Parent = frame
btn.MouseButton1Click:Connect(function()
    local hum = lp.Character and lp.Character:FindFirstChildOfClass("Humanoid")
    if hum then hum.WalkSpeed = 50 end
    game:GetService("StarterGui"):SetCore("SendNotification", {Title = "Speed", Text = "enabled", Duration = 3})
end)

UIS.InputBegan:Connect(function(input, gp)
    if gp then return end
    if input.KeyCode == Enum.KeyCode.E then
        print("E pressed")
    end
end)

task.spawn(function()
    for i = 1, 3 do
        print("tick", i)
        task.wait(1)
    end
end)

local ok, err = pcall(function()
    task.wait(0.5)
    print("after wait inside pcall")
end)

RunService.Heartbeat:Connect(function(dt)
    lp.Character.Humanoid.Health = 100
end)

while true do
    wait(1)
    print("loop")
end
