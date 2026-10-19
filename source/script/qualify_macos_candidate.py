#!/usr/bin/env python3
"""Stage the actual raw app/source and run the secret-free delivery producer."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import urllib.request

sys.dont_write_bytecode = True
from stage_release_source_offer import digest, stage, producer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, help="Optional already-downloaded pinned baseline")
    parser.add_argument("--channel", choices=producer.release.CHANNELS, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preview", action="store_true", help="Local/unreviewed qualification; no signing authority")
    mode.add_argument("--policy", type=Path, help="Protected main policy, consumed as data by the producer")
    args = parser.parse_args()
    checkout = Path(__file__).resolve().parents[2]
    if any(os.environ.get(name) for name in ("APPLE_CERTIFICATE_P12_BASE64", "SPARKLE_PRIVATE_KEY", "NOTARY_APP_PASSWORD")):
        parser.error("raw build and qualification must not receive product credentials")
    args.output.mkdir(parents=True, exist_ok=False)
    app_name = producer.release.CHANNELS[args.channel][2] + ".app"
    app = args.output / app_name
    subprocess.run(["ditto", str(args.app.resolve(strict=True)), str(app)], check=True)
    inputs = json.loads((checkout / "source/collector/macos-release-source-inputs.json").read_text())["channels"][args.channel]
    baseline = args.baseline
    if baseline is None:
        baseline = args.output / inputs["baseline"]["name"]
        with urllib.request.urlopen(inputs["baseline"]["url"], timeout=60) as response, baseline.open("xb") as target:
            total = 0
            while data := response.read(1024**2):
                total += len(data)
                if total > inputs["baseline"]["bytes"]:
                    raise ValueError("baseline download exceeds pinned size")
                target.write(data)
        if total != inputs["baseline"]["bytes"] or digest(baseline) != inputs["baseline"]["sha256"]:
            raise ValueError("baseline download differs from pinned source archive")
    source = stage(checkout, app, baseline, args.output / "staging", args.channel)
    commit = subprocess.check_output(["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True).strip()
    if args.preview:
        info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
        candidate = {"sourceCommit": commit, "channel": args.channel,
                     "version": info["CFBundleShortVersionString"], "build": info["CFBundleVersion"],
                     "qualificationProfile": producer.release.PHONE_RUN_QUALIFICATION}
        producer.qualify(candidate, checkout, app, source, args.output / "delivery", int(os.environ.get("GITHUB_RUN_ID", "0")))
    else:
        subprocess.run([sys.executable, str(checkout / "release_control/producer.py"),
            "--checkout", str(checkout), "--app", str(app), "--source-archive", str(source),
            "--output", str(args.output / "delivery"), "--source-commit", commit,
            "--channel", args.channel, "--policy", str(args.policy.resolve(strict=True))], check=True)
    print(json.dumps({"delivery": str(args.output / "delivery"), "sourceCommit": commit,
                      "preview": args.preview, "signed": False, "published": False}))


if __name__ == "__main__":
    main()
