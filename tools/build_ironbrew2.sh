#!/bin/sh
# Build IronBrew 2 (MIT, Trollicus/ironbrew-2) on Linux and obfuscate a script. Needs apt: dotnet-sdk-8.0 lua5.1 luajit
# set IB2_ENCRYPT=1 to also encrypt strings
# usage: tools/build_ironbrew2.sh /path/to/ironbrew-2 in.lua out.lua
set -e
SRC="$1"; IN="$(realpath "$2")"; OUT="$(realpath -m "$3")"
ln -sf /usr/bin/luac5.1 /usr/bin/luac
W=$(mktemp -d)
cp -r "$SRC/IronBrew2" "$SRC/IronBrew2 CLI" "$SRC/Lua" "$W/"
rm -rf "$W/IronBrew2/bin" "$W/IronBrew2/obj" "$W/IronBrew2 CLI/bin" "$W/IronBrew2 CLI/obj"
sed -i 's/netcoreapp3.1/net8.0/' "$W/IronBrew2/IronBrew2.csproj" "$W/IronBrew2 CLI/IronBrew2 CLI.csproj"
if [ -n "$IB2_ENCRYPT" ]; then sed -i 's/EncryptStrings = false/EncryptStrings = true/; s/EncryptImportantStrings = false/EncryptImportantStrings = true/' "$W/IronBrew2/Obfuscator/ObfuscationSettings.cs"; fi
(cd "$W/IronBrew2 CLI" && dotnet build -c Release >/dev/null)
mkdir "$W/run" && cd "$W/run" && cp "$IN" in.lua
dotnet "../IronBrew2 CLI/bin/Release/net8.0/IronBrew2 CLI.dll" in.lua
cp out.lua "$OUT"
