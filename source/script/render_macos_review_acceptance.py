#!/usr/bin/env python3
"""Render production Mac views using synthetic data without activating or installing an app."""
import argparse
import json
from pathlib import Path
import plistlib
import shutil
import subprocess
import uuid

SOURCE = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--states", nargs="+", help="Only render these fixture states; defaults to the complete state map")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists() or not str(output).startswith("/private/tmp/amplifai-mac-visual."):
        parser.error("Use a new /private/tmp/amplifai-mac-visual.* directory")
    output.mkdir()
    package = SOURCE / "collector/macos"
    framework_dir = Path(subprocess.check_output(["swift", "build", "--package-path", str(package),
        "--configuration", "release", "--show-bin-path"], text=True, timeout=120).strip())
    app = output / "AMPLIFai Visual Fixture.app"
    contents = app / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    (contents / "Frameworks").mkdir()
    resources = contents / "Resources"
    resources.mkdir()
    shutil.copy2(SOURCE / "public/brand/amplifai-by-nexus-original.png", resources / "amplifai-logo.png")
    shutil.copytree(SOURCE / "public/brand/fonts", resources / "Fonts")
    subprocess.run(["ditto", "--noextattr", "--norsrc", str(framework_dir / "Sparkle.framework"),
                    str(contents / "Frameworks/Sparkle.framework")], check=True, timeout=120)
    (contents / "Info.plist").write_bytes(plistlib.dumps({"CFBundleExecutable": "RenderAcceptance",
        "CFBundleIdentifier": "ai.satoris.amplifai.phone.visual-fixture." + uuid.uuid4().hex,
        "CFBundleName": "AMPLIFai Visual Fixture", "CFBundleVersion": "1", "CFBundlePackageType": "APPL", "LSUIElement": True}))
    sources = sorted(p for p in (package / "Sources").rglob("*.swift") if "App" not in p.parts)
    executable = contents / "MacOS/RenderAcceptance"
    subprocess.run(["swiftc", "-whole-module-optimization", "-parse-as-library", "-F", str(framework_dir), *map(str, sources),
        str(package / "Tests/Fixtures/SimulatedPhoneRunServer.swift"),
        str(package / "Tests/Fixtures/SimulatedPhoneSetupServer.swift"),
        str(package / "Tests/Fixtures/RenderMacReviewAcceptance.swift"), "-Xlinker", "-rpath", "-Xlinker",
        "@executable_path/../Frameworks", "-o", str(executable)], check=True, timeout=300)
    subprocess.run([str(executable), str(output), *(args.states or [])], check=True, timeout=600)
    receipt = json.loads((output / "mac-review-render-receipt.json").read_text())
    if receipt["font"] != "OpenSans-Regular":
        raise ValueError("Bundled body font did not load")
    print(output)


if __name__ == "__main__":
    main()
