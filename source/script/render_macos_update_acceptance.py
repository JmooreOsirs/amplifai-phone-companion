#!/usr/bin/env python3
"""Render actual current/rc24 ContentView offscreen with synthetic model data."""
from pathlib import Path
import argparse
import json
import plistlib
import shutil
import subprocess
import uuid

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "source"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists() or not str(output).startswith("/private/tmp/amplifai-mac-visual."):
        parser.error("Output must be a new /private/tmp/amplifai-mac-visual.* directory")
    output.mkdir()
    package = SOURCE / "collector/macos"
    framework_dir = Path(subprocess.check_output(["swift", "build", "--package-path", str(package),
        "--configuration", "release", "--show-bin-path"], text=True).strip())
    baseline_full = subprocess.check_output(["git", "show", "cdfc1b9:source/collector/macos/Sources/Views/ContentView.swift"], cwd=ROOT, text=True)
    baseline = baseline_full[:baseline_full.index("struct PanelStyle:")].replace("struct ContentView:", "struct BaselineContentView:", 1)
    baseline_source = output / "BaselineContentView.swift"
    baseline_source.write_text(baseline)
    fixture = package / "Tests/Fixtures"
    objc = output / "visual-item.o"
    subprocess.run(["clang", "-fobjc-arc", "-mmacosx-version-min=14.0", "-F", str(framework_dir),
        "-c", str(fixture / "VisualAppcastItem.m"), "-o", str(objc)], check=True)
    app = output / "AMPLIFai Visual Fixture.app"
    contents = app / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    (contents / "Frameworks").mkdir()
    resources = contents / "Resources"
    resources.mkdir()
    shutil.copy2(SOURCE / "public/brand/amplifai-by-nexus-original.png", resources / "amplifai-logo.png")
    subprocess.run(["ditto", "--noextattr", "--norsrc", str(framework_dir / "Sparkle.framework"),
                    str(contents / "Frameworks/Sparkle.framework")], check=True)
    (contents / "Info.plist").write_bytes(plistlib.dumps({"CFBundleExecutable": "RenderAcceptance",
        "CFBundleIdentifier": "ai.satoris.amplifai.phone.visual-fixture." + uuid.uuid4().hex,
        "CFBundleName": "AMPLIFai Visual Fixture", "CFBundleVersion": "1", "CFBundlePackageType": "APPL",
        "LSUIElement": True}))
    sources = sorted(p for p in (package / "Sources").rglob("*.swift") if "App" not in p.parts)
    executable = contents / "MacOS/RenderAcceptance"
    subprocess.run(["swiftc", "-parse-as-library", "-F", str(framework_dir), "-import-objc-header",
        str(fixture / "VisualAppcastItem.h"), *map(str, sources), str(baseline_source),
        str(fixture / "RenderUpdateAcceptance.swift"), str(objc), "-Xlinker", "-rpath", "-Xlinker",
        "@executable_path/../Frameworks", "-o", str(executable)], check=True)
    subprocess.run([str(executable), str(output)], check=True)
    receipt_path = output / "visual-receipt.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["identicalRenderedStates"] = {f"{name}-{width}":
        (output / f"current-{name}-{width}.png").read_bytes() == (output / f"rc24-{name}-{width}.png").read_bytes()
        for name in ("manual", "current", "deferred") for width in (375, 820)}
    current_source = (package / "Sources/Views/ContentView.swift").read_text()
    receipt["unchangedPanelAndButtonSource"] = current_source[current_source.index("struct PanelStyle:"):] == baseline_full[baseline_full.index("struct PanelStyle:"):]
    receipt["unchangedBrandSource"] = subprocess.check_output(["git", "show", "cdfc1b9:source/collector/macos/Sources/Support/Brand.swift"], cwd=ROOT) == (package / "Sources/Support/Brand.swift").read_bytes()
    if not all(receipt["identicalRenderedStates"].values()) or not receipt["unchangedPanelAndButtonSource"] or not receipt["unchangedBrandSource"]:
        raise ValueError("Matched native reference primitives or unchanged-state renders drifted")
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print(output / "visual-receipt.json")


if __name__ == "__main__":
    main()
