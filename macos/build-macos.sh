#!/bin/bash
set -euo pipefail

project_root="$(cd "$(dirname "$0")" && pwd)"
cd "$project_root"

swift test
swift build -c release
bin_path="$(swift build -c release --show-bin-path)"

dist_dir="$project_root/../dist"
app_path="$dist_dir/GPTTranscribe.app"
dmg_path="$dist_dir/GPTTranscribe.dmg"
rm -rf "$app_path" "$dmg_path"
mkdir -p "$app_path/Contents/MacOS" "$app_path/Contents/Resources"
cp "$bin_path/GPTTranscribeMac" "$app_path/Contents/MacOS/GPTTranscribe"
cp "$project_root/Resources/Info.plist" "$app_path/Contents/Info.plist"
chmod 755 "$app_path/Contents/MacOS/GPTTranscribe"

# Ad-hoc signing makes the locally built bundle launchable while keeping
# distribution signing/notarization available to a release pipeline.
codesign --force --deep --sign - "$app_path"

if command -v hdiutil >/dev/null 2>&1; then
    hdiutil create -volname "GPT Transcribe" -srcfolder "$app_path" -ov -format UDZO "$dmg_path" >/dev/null
fi

echo "Built $app_path"
if [ -f "$dmg_path" ]; then
    echo "Built $dmg_path"
fi
