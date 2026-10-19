#!/bin/sh
# BUILD ONLY: stage a local real-device test bundle of this branch's Mac app. It never launches anything.
#
# The branch changes no helper (Python) code, so the bundle reuses the installed RC28's signed helper and
# Sparkle unchanged (read-only copy) and replaces only the app binary with this branch's release build.
# The one-approval run arrives by the amplifai-phone:// app link, which RC28's Info.plist does not declare, so
# CFBundleURLTypes is added to the STAGED COPY's Info.plist only. This script never registers the copy with
# LaunchServices (no lsregister), never launches it and never touches /Applications. Note: the copy keeps RC28's
# bundle identifier, so once the copy is opened LaunchServices may associate the scheme with that identifier.
# It keeps RC28's channel, bundle ID, version and build so the website status and diagnostics routes accept
# it, strips the Sparkle feed keys so it can never update itself, and is named and placed so its exact path
# distinguishes it from /Applications. The installed app is only read.
set -eu
source_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
installed="/Applications/AMPLIFai Phone.app"
expected_version="2026.10.16-rc28"
expected_build="26101628"
[ "$(uname -m)" = arm64 ] || { echo "arm64 only" >&2; exit 2; }
version=$(/usr/libexec/PlistBuddy -c "Print :CFBundleShortVersionString" "$installed/Contents/Info.plist")
build=$(/usr/libexec/PlistBuddy -c "Print :CFBundleVersion" "$installed/Contents/Info.plist")
[ "$version" = "$expected_version" ] && [ "$build" = "$expected_build" ] || {
    echo "Installed app is $version ($build), not $expected_version ($expected_build)" >&2; exit 2; }
# A new versioned directory every time; mkdir fails rather than reuse an existing one.
output="/private/tmp/amplifai-real-test.run-approval-v1.$(date +%Y%m%d-%H%M%S)"
mkdir "$output"
app="$output/AMPLIFai Phone LOCAL TEST.app"

swift build --package-path "$source_root/collector/macos" --configuration release --arch arm64 > /dev/null
release_bin=$(swift build --package-path "$source_root/collector/macos" --configuration release --arch arm64 --show-bin-path)
ditto "$installed" "$app"
cp "$release_bin/AmplifaiPhone" "$app/Contents/MacOS/AmplifaiPhone"
# A Command Line Tools build adds the toolchain's Swift back-deployment run path, which the CI normalizer
# (written for Xcode builds) rejects. macOS 14+ provides those libraries in /usr/lib/swift, so drop only it.
developer=$(xcode-select -p)
for rpath in $(otool -l "$app/Contents/MacOS/AmplifaiPhone" | awk '/LC_RPATH/{getline; getline; print $2}'); do
    case "$rpath" in
        "$developer"/usr/lib/swift*) install_name_tool -delete_rpath "$rpath" "$app/Contents/MacOS/AmplifaiPhone" 2>/dev/null
                                    echo "$rpath" >> "$output/removed-toolchain-rpaths.txt" ;;
    esac
done
python3 "$source_root/script/normalize_macos_wrapper.py" "$app/Contents/MacOS/AmplifaiPhone" \
    --architecture arm64 > "$output/wrapper-rpath-receipt.json"

plist="$app/Contents/Info.plist"
for key in $(/usr/libexec/PlistBuddy -c "Print" "$plist" | sed -n 's/^    \(SU[A-Za-z]*\) = .*/\1/p'); do
    /usr/libexec/PlistBuddy -c "Delete :$key" "$plist"
done
/usr/libexec/PlistBuddy -c "Set :CFBundleName AMPLIFai Phone LOCAL TEST" "$plist"
/usr/libexec/PlistBuddy -c "Add :CFBundleDisplayName string AMPLIFai Phone LOCAL TEST" "$plist" 2>/dev/null ||
    /usr/libexec/PlistBuddy -c "Set :CFBundleDisplayName AMPLIFai Phone LOCAL TEST" "$plist"
commit=$(git -C "$source_root" rev-parse HEAD)
/usr/libexec/PlistBuddy -c "Add :AMPLIFaiLocalTestSourceCommit string $commit" "$plist"
# The run link scheme, on the staged copy only (the same declaration as source/collector/macos/Info.plist).
/usr/libexec/PlistBuddy -c "Delete :CFBundleURLTypes" "$plist" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :CFBundleURLTypes array" "$plist"
/usr/libexec/PlistBuddy -c "Add :CFBundleURLTypes:0 dict" "$plist"
/usr/libexec/PlistBuddy -c "Add :CFBundleURLTypes:0:CFBundleURLName string ai.satoris.amplifai.phone.run-link" "$plist"
/usr/libexec/PlistBuddy -c "Add :CFBundleURLTypes:0:CFBundleURLSchemes array" "$plist"
/usr/libexec/PlistBuddy -c "Add :CFBundleURLTypes:0:CFBundleURLSchemes:0 string amplifai-phone" "$plist"

# Ad-hoc sign only the outer app; the nested helper and Sparkle keep their Developer ID signatures.
codesign --force --sign - "$app"
codesign --verify --deep --strict "$app"
python3 "$source_root/script/verify_macos_architecture.py" "$app" --arch arm64 --minimum-macos 14.0 \
    --output "$output/architecture-receipt.json" > /dev/null

python3 -I - "$installed" "$app" "$output" "$commit" "$(git -C "$source_root" status --porcelain -- . | wc -l | tr -d ' ')" <<'EOF'
import hashlib, json, plistlib, sys
from pathlib import Path
installed, app, output, commit, dirty = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4], int(sys.argv[5])
def tree(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file() and not p.is_symlink()}
helper_same = tree(installed / "Contents/Resources/helper") == tree(app / "Contents/Resources/helper")
sparkle_same = tree(installed / "Contents/Frameworks/Sparkle.framework") == tree(app / "Contents/Frameworks/Sparkle.framework")
info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
binary = app / "Contents/MacOS/AmplifaiPhone"
receipt = {
    "app": str(app), "sourceCommit": commit, "sourceTreeClean": dirty == 0,
    "binarySha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
    "installedBinarySha256": hashlib.sha256((installed / "Contents/MacOS/AmplifaiPhone").read_bytes()).hexdigest(),
    "helperIdenticalToInstalledRC28": helper_same, "sparkleIdenticalToInstalledRC28": sparkle_same,
    "bundleIdentifier": info["CFBundleIdentifier"], "version": info["CFBundleShortVersionString"], "build": info["CFBundleVersion"],
    "updateFeedRemoved": not any(k.startswith("SU") for k in info),
    "outsideApplications": not str(app).startswith("/Applications/"),
    "runLinkSchemeDeclared": [t.get("CFBundleURLSchemes") for t in info.get("CFBundleURLTypes", [])] == [["amplifai-phone"]],
    "installedDeclaresRunLinkScheme": "CFBundleURLTypes" in plistlib.loads((installed / "Contents/Info.plist").read_bytes()),
    "registeredWithLaunchServices": False,
    "launched": False,
}
(output / "local-device-test-receipt.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
print(json.dumps(receipt, indent=2, sort_keys=True))
if not (helper_same and sparkle_same and receipt["updateFeedRemoved"] and receipt["outsideApplications"]
        and receipt["runLinkSchemeDeclared"]):
    sys.exit("Staged bundle failed its checks")
EOF
