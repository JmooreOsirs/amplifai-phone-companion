#!/usr/bin/env python3
"""Prepare unsigned disposable Sparkle fixture apps; root owns signing/feed promotion."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import platform
import plistlib
import re
import shutil
import subprocess
import uuid

SOURCE = Path(__file__).resolve().parents[1]
PUBLIC_KEY = "vtvaNfb6+dVMP4tz6PeLMXTeVQH7lK7o1zzy4WeqUGs="


def prepare(output: Path, framework: Path, feed_url: str):
    output = output.resolve()
    framework = framework.resolve(strict=True)
    if output.exists() or not str(output).startswith("/private/tmp/amplifai-sparkle-fixture."):
        raise ValueError("Fixture output must be a new /private/tmp/amplifai-sparkle-fixture.* path")
    if not re.fullmatch(r"http://127\.0\.0\.1:[0-9]{1,5}/appcast\.xml", feed_url):
        raise ValueError("Fixture feed must be its explicit loopback appcast URL")
    identifier = "ai.satoris.amplifai.phone.update-fixture." + uuid.uuid4().hex
    output.mkdir(parents=True)
    binary = output / "Fixture"
    subprocess.run(["clang", "-fobjc-arc", "-mmacosx-version-min=14.0", "-arch", platform.machine(),
                    "-F", str(framework.parent), "-framework", "AppKit", "-framework", "Sparkle",
                    "-Wl,-rpath,@executable_path/../Frameworks",
                    str(SOURCE / "collector/macos/Tests/Fixtures/SparkleInstallFixture.m"), "-o", str(binary)], check=True)
    apps = []
    for role, build in (("old-host", "100"), ("update", "101")):
        app = output / role / "AMPLIFai Update Fixture.app"
        contents = app / "Contents"
        (contents / "MacOS").mkdir(parents=True)
        (contents / "Frameworks").mkdir()
        shutil.copy2(binary, contents / "MacOS/Fixture")
        subprocess.run(["ditto", "--noextattr", "--norsrc", str(framework), str(contents / "Frameworks/Sparkle.framework")], check=True)
        info = {"CFBundleExecutable": "Fixture", "CFBundleIdentifier": identifier,
                "CFBundleName": "AMPLIFai Update Fixture", "CFBundlePackageType": "APPL",
                "CFBundleVersion": build, "CFBundleShortVersionString": "fixture-" + build,
                "LSMinimumSystemVersion": "14.0", "LSUIElement": True,
                "SUFeedURL": feed_url, "SUPublicEDKey": PUBLIC_KEY, "SUEnableAutomaticChecks": False,
                "SUAutomaticallyUpdate": False, "SUAllowsAutomaticUpdates": False,
                "SURequireSignedFeed": True, "SUSignedFeedFailureExpirationInterval": 0,
                "SUVerifyUpdateBeforeExtraction": True, "AMPLIFaiFixtureRoot": str(output),
                "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True}}
        (contents / "Info.plist").write_bytes(plistlib.dumps(info))
        apps.append(str(app))
    receipt = {"kind": "isolated-sparkle-install-fixture", "bundleId": identifier,
               "architecture": platform.machine(), "apps": apps, "feedUrl": feed_url,
               "binarySha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
               "signed": False, "frameworkInstallVerified": False, "relaunchVerified": False,
               "productionCollectorIncluded": False, "ownerDefaultsDomainUsed": False}
    (output / "prepare-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sparkle-framework", type=Path, required=True)
    parser.add_argument("--feed-url", required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.output, args.sparkle_framework, args.feed_url), sort_keys=True))


if __name__ == "__main__":
    main()
