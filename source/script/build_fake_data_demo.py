#!/usr/bin/env python3
"""Build the clickable FAKE DATA DEMO app: production Mac views, the real model and a fake helper.

The bundle is separately identified, lives only under /private/tmp, carries no real collector runtime,
update feed or valid diagnostic build, and is checked for isolation before the launch command is printed.
"""
import argparse
import json
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import uuid

SOURCE = Path(__file__).resolve().parents[1]
FORBIDDEN_HELPER_SYMBOLS = re.compile(
    r"urlsession|nsurlconnection|_socket$|_connect$|getaddrinfo|ioservice|amdevice|usbmux|nsworkspace|posix_spawn|_execv|nstask|_fork$",
    re.IGNORECASE)
PRODUCTION_IDS = {"ai.satoris.amplifai.phone.candidate", "ai.satoris.amplifai.phone.intel-candidate"}


def run(command, **kwargs):
    return subprocess.run(command, check=True, text=True, timeout=600, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists() or not str(output).startswith("/private/tmp/amplifai-fake-demo."):
        parser.error("Use a new /private/tmp/amplifai-fake-demo.* directory")
    output.mkdir()
    package = SOURCE / "collector/macos"
    demo = package / "Tests/Fixtures/FakeDataDemo"
    framework_dir = Path(subprocess.check_output(["swift", "build", "--package-path", str(package),
        "--configuration", "release", "--show-bin-path"], text=True, timeout=600).strip())
    run(["swift", "build", "--package-path", str(package), "--configuration", "release"], capture_output=True)

    app = output / "AMPLIFai FAKE DATA DEMO.app"
    contents = app / "Contents"
    resources = contents / "Resources"
    helper_dir = resources / "helper"
    for directory in (contents / "MacOS", contents / "Frameworks", helper_dir):
        directory.mkdir(parents=True)
    shutil.copy2(SOURCE / "public/brand/amplifai-by-nexus-original.png", resources / "amplifai-logo.png")
    shutil.copytree(SOURCE / "public/brand/fonts", resources / "Fonts")
    run(["ditto", "--noextattr", "--norsrc", str(framework_dir / "Sparkle.framework"),
         str(contents / "Frameworks/Sparkle.framework")])
    identifier = "ai.satoris.amplifai.fake-data-demo." + uuid.uuid4().hex[:12]
    (contents / "Info.plist").write_bytes(plistlib.dumps({
        "CFBundleExecutable": "AmplifaiFakeDataDemo", "CFBundleIdentifier": identifier,
        "CFBundleName": "AMPLIFai FAKE DATA DEMO", "CFBundleDisplayName": "AMPLIFai FAKE DATA DEMO",
        "CFBundleShortVersionString": "fake-demo", "CFBundleVersion": "fake-demo",
        "CFBundlePackageType": "APPL", "LSMinimumSystemVersion": "14.0", "NSHighResolutionCapable": True}))

    helper = helper_dir / "amplifai-agent"
    run(["swiftc", "-O", "-parse-as-library", str(demo / "FakeDataSet.swift"), str(demo / "FakeHelper.swift"),
         "-o", str(helper)])
    (helper_dir / "FAKE_HELPER_MARKER").write_text("amplifai-fake-data-helper\n")
    sources = sorted(p for p in (package / "Sources").rglob("*.swift") if "App" not in p.parts)
    run(["swiftc", "-parse-as-library", "-F", str(framework_dir), *map(str, sources),
         str(demo / "FakeDataSet.swift"), str(demo / "FakeDataDemoApp.swift"),
         "-Xlinker", "-rpath", "-Xlinker", "@executable_path/../Frameworks",
         "-o", str(contents / "MacOS/AmplifaiFakeDataDemo")])

    # Isolation evidence, checked before any launch.
    helper_imports = subprocess.check_output(["nm", "-u", str(helper)], text=True).splitlines()
    forbidden = [line for line in helper_imports if FORBIDDEN_HELPER_SYMBOLS.search(line.strip())]
    helper_links = subprocess.check_output(["otool", "-L", str(helper)], text=True)
    info = plistlib.loads((contents / "Info.plist").read_bytes())
    profile = resources / "deny-network.sb"
    profile.write_text("(version 1)\n(allow default)\n(deny network*)\n")
    executable = contents / "MacOS/AmplifaiFakeDataDemo"
    # Started unsandboxed, as a double-click would be: the app must re-enter its own no-network sandbox.
    self_test = subprocess.run([str(executable), "--sandbox-self-test"], capture_output=True, text=True, timeout=60)
    probe = subprocess.run(["sandbox-exec", "-f", str(profile), "/usr/bin/nc", "-v", "-z", "-w", "1", "127.0.0.1", "9"],
                           capture_output=True, text=True, timeout=30)
    evidence = {
        "bundle": str(app), "bundleIdentifier": identifier,
        "notProductionIdentity": identifier not in PRODUCTION_IDS,
        "outsideApplications": not str(app).startswith("/Applications/"),
        "onlyFakeHelper": sorted(p.name for p in helper_dir.iterdir()) == ["FAKE_HELPER_MARKER", "amplifai-agent"],
        "noRealRuntime": not (resources / "uvx").exists() and not (resources / "collector").exists(),
        "noUpdateFeed": not any(key.startswith("SU") for key in info),
        "diagnosticBuildInvalid": not re.fullmatch(r"[1-9][0-9]{0,11}", info["CFBundleVersion"]),
        "helperForbiddenImports": forbidden,
        "helperForbiddenFrameworks": [line.strip() for line in helper_links.splitlines()
                                      if re.search(r"/(Network|IOKit|CFNetwork|MobileDevice)\.framework", line)],
        "sandboxDeniesNetwork": probe.returncode != 0 and "Operation not permitted" in (probe.stderr + probe.stdout),
        "appSelfSandboxes": self_test.returncode == 0 and "network-outbound denied = true" in self_test.stdout,
        "launch": ["open", "-n", str(app)],
        "launchWithoutLaunchServices": [str(executable)],
    }
    checks = ["notProductionIdentity", "outsideApplications", "onlyFakeHelper", "noRealRuntime", "noUpdateFeed",
              "diagnosticBuildInvalid", "sandboxDeniesNetwork", "appSelfSandboxes"]
    passed = all(evidence[key] for key in checks) and not forbidden and not evidence["helperForbiddenFrameworks"]
    evidence["isolationPassed"] = passed
    (output / "fake-demo-isolation.json").write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    if not passed:
        raise SystemExit(f"Isolation check failed; see {output / 'fake-demo-isolation.json'}")
    print(json.dumps(evidence, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
