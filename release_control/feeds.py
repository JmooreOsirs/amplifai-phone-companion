"""Reuse the qualified feed generator with the reviewed source and existing key."""
import copy
import json
import os
from pathlib import Path
import sys
from typing import Callable

import release

sys.path.insert(0, str(release.ROOT / "source/script"))
import desktop_update_release as existing


def names(source: dict) -> dict:
    cpu = "Apple-Silicon" if source["channel"] == "macos-arm64" else "Intel"
    stem = "AMPLIFai-Phone-" + cpu + "-" + source["version"]
    return {"package": stem + ".dmg", "source": stem + "-Source.zip",
            "tag": "macos-" + source["version"] + ("-release" if cpu == "Apple-Silicon" else "-test")}


def prepare(source: dict, package: Path, source_zip: Path, output: Path, tools: Path, openssl: str, *,
            keychain_signer: Callable[[Path], str] | None = None) -> dict:
    policy = release.exact_json((release.ROOT / "release_control/policy.json").read_bytes())
    channel = source["channel"]
    planned = names(source)
    if package.name != planned["package"] or source_zip.name != planned["source"]:
        raise ValueError("publication names differ from reviewed channel/version")
    pins = copy.deepcopy(release.exact_json((release.ROOT / "docs/DESKTOP-UPDATE-SOURCE-PINS.json").read_bytes()))
    arch, bundle, _, feed_name = release.CHANNELS[channel]
    pins["plannedBuilds"][channel] = {"version": source["version"], "build": source["build"], "bundleId": bundle}
    spec = {"schema": 1, "platform": "macos", "architecture": arch, "channel": channel,
            "version": source["version"], "build": source["build"], "bundleId": bundle,
            "sourceBaseCommit": pins["macosBaseCommit"],
            "baselineSourceArchiveSha256": pins["macosArmSourceArchiveSha256" if arch == "arm64" else "macosIntelSourceArchiveSha256"],
            "installerUrl": "https://github.com/" + policy["repository"] + "/releases/download/" + planned["tag"] + "/" + package.name}
    for key, path in (("package", package), ("source", source_zip)):
        spec[key] = {"name": path.name, "bytes": path.stat().st_size, "sha256": release.digest(path)}
    previous = output.parent / ("previous-" + feed_name)
    approved = policy["previousFeeds"][channel]
    release.download("https://amplifai-database-engine.vercel.app/phone/macos/" + feed_name,
                     previous, approved["sha256"], approved["bytes"])
    existing.validate_appcast(previous.read_bytes(), channel, existing.FEEDS[channel][4], openssl=openssl)

    class ExistingKeySigner(existing.SparkleSigner):
        def sign(self, path):
            if keychain_signer is not None:
                return keychain_signer(path)
            return release.command([self.directory / "bin/sign_update", "--ed-key-file", "-",
                                    "--disable-signing-warning", "-p", path],
                                   input_data=os.environ["SPARKLE_PRIVATE_KEY"].encode())

    receipt = existing.prepare(spec, package, source_zip, output, ExistingKeySigner(tools), pins, previous, openssl)
    (output.parent / "release-descriptor.json").write_text(json.dumps(spec, indent=2, sort_keys=True) + "\n")
    return receipt
