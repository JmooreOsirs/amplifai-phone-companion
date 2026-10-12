#!/usr/bin/env python3
"""Collect exact source archives/notices for the pinned Intel Python runtime.

Downloads and reads archives only. Does not install or execute the downloaded
interpreter or sources. Source URLs/checksums come from the matching immutable
python-build-standalone release commit, not from the older ARM source receipt.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import shutil
import tarfile
import urllib.parse

from prepare_intel_dependencies import download_verified

DEFAULT_MANIFEST = Path(__file__).resolve().parents[1] / "collector/intel-python-inputs.json"
ALLOWED_HOSTS = {"github.com", "codeload.github.com", "astral-sh.github.io", "www.python.org",
                 "www.sqlite.org", "sourceforge.net", "prdownloads.sourceforge.net"}


def digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def validate_manifest(manifest: dict) -> None:
    if (manifest["schema"] != 1 or manifest["python_version"] != "3.12.15"
            or manifest["target_triple"] != "x86_64-apple-darwin"
            or manifest["release"] != "20261009"):
        raise ValueError("Unsupported Python source manifest")
    metadata = manifest["metadata_evidence"]
    if (metadata["python_version"] != manifest["python_version"] or
            metadata["target_triple"] != manifest["target_triple"] or
            metadata["build_options"] != "pgo+lto"):
        raise ValueError("Python build metadata does not match runtime identity")
    for item in [manifest["runtime"], manifest["provider_source"], *manifest["sources"]]:
        url = urllib.parse.urlsplit(item["url"])
        if (url.scheme != "https" or url.hostname not in ALLOWED_HOSTS or
                Path(item["filename"]).name != item["filename"] or
                not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])):
            raise ValueError("Invalid source URL, filename or checksum")
    keys = {item["provider_key"] for item in manifest["sources"]}
    if len(keys) != len(manifest["sources"]) or "cpython-3.12" not in keys:
        raise ValueError("Missing CPython or duplicate source input")
    required = {link["name"] for variants in metadata["linked_extensions"].values()
                for variant in variants for link in variant["links"]
                if not link.get("system") and not link.get("framework")}
    mapping = manifest["native_dependency_mapping"]
    if required != set(mapping) or not set(mapping.values()).issubset(keys):
        raise ValueError("Native dependency source coverage is incomplete")
    if not set(manifest["bundled_dynamic_library_sources"].values()).issubset(keys):
        raise ValueError("Bundled dynamic library source coverage is incomplete")


def provider_file(bundle: tarfile.TarFile, prefix: str, relative: str) -> bytes:
    member = bundle.getmember(prefix + "/" + relative)
    if not member.isfile() or member.size > 1024 * 1024:
        raise ValueError("Unexpected provider metadata or notice member")
    return bundle.extractfile(member).read()


def collect(manifest: dict, output: Path, runtime: Path,
            reuse: Path | None = None) -> dict:
    validate_manifest(manifest)
    if digest(runtime) != manifest["runtime"]["sha256"]:
        raise ValueError("The actual build runtime archive differs from the pinned Intel runtime")
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError("Use a new empty output directory")
    archives = output / "source-archives"
    notices = output / "notices"
    archives.mkdir()
    notices.mkdir()
    provider = download_verified(manifest["provider_source"], archives)
    prefix = "python-build-standalone-" + manifest["provider_source"]["version"]
    notice_records = []
    with tarfile.open(provider, "r:gz") as bundle:
        raw_downloads = provider_file(bundle, prefix, "pythonbuild/downloads.json")
        downloads = json.loads(raw_downloads)
        (output / "provider-downloads.json").write_bytes(raw_downloads)
        for item in manifest["sources"]:
            upstream = downloads[item["provider_key"]]
            if any(item[key] != upstream[key] for key in ("version", "sha256", "url", "license_file")):
                raise ValueError(f"Source input differs from provider commit: {item['provider_key']}")
        # Copy exact notices used by the full Intel distribution. All were
        # hash-compared with that release's python/licenses directory during
        # manifest authoring, including notices covering system-linked modules.
        for name, expected in manifest["metadata_evidence"]["provider_license_sha256"].items():
            if Path(name).name != name:
                raise ValueError("Invalid notice filename")
            content = provider_file(bundle, prefix, name)
            actual = hashlib.sha256(content).hexdigest()
            if actual != expected:
                raise ValueError("Provider notice differs from the reviewed full runtime")
            (notices / name).write_bytes(content)
            notice_records.append({"filename": name, "sha256": actual})
        (notices / "LICENSE.python-build-standalone.txt").write_bytes(provider_file(bundle, prefix, "LICENSE"))

    def fetch(item):
        if reuse is not None and (reuse / item["filename"]).is_file():
            existing = reuse / item["filename"]
            if digest(existing) != item["sha256"]:
                raise ValueError("Reused source input does not match its pinned checksum")
            shutil.copyfile(existing, archives / item["filename"])
        else:
            download_verified(item, archives)

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(fetch, manifest["sources"]))
    metadata = manifest["metadata_evidence"]
    (output / "reviewed-runtime-metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    (output / "intel-python-inputs.json").write_text(json.dumps(manifest, indent=2) + "\n")
    receipt = {
        "kind": "intel-python-corresponding-source", "status": "downloaded-and-hash-verified",
        "python_version": manifest["python_version"], "target_triple": manifest["target_triple"],
        "runtime_archive_sha256": digest(runtime), "provider_commit": manifest["provider_source"]["version"],
        "provider_source_sha256": digest(provider),
        "source_archives": [{"name": item["name"], "version": item["version"], "filename": item["filename"],
                             "sha256": digest(archives / item["filename"])} for item in manifest["sources"]],
        "notices": notice_records, "native_dependency_mapping": manifest["native_dependency_mapping"],
        "metadata_evidence": {"full_archive_sha256": metadata["full_archive_sha256"],
                              "metadata_sha256": metadata["metadata_sha256"],
                              "verification": "Full metadata reviewed at manifest authoring; pinned provider source checked in this run"},
        "downloaded_code_executed": False, "coverage_note": manifest["coverage_note"],
    }
    (output / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    with (output / "INTEL-PYTHON-NOTICES.txt").open("w") as combined:
        combined.write("Intel Python 3.12.15 / python-build-standalone 20261009\n"
                       "Exact provider notices; corresponding source hashes are in receipt.json.\n\n")
        for path in sorted(notices.iterdir()):
            combined.write("--- " + path.name + " ---\n")
            combined.write(path.read_text())
            combined.write("\n\n")
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--runtime-archive", type=Path, required=True,
                        help="Exact install_only_stripped archive used to build the helper")
    parser.add_argument("--reuse-source-dir", type=Path,
                        help="Optional already verified OpenSSL source-input directory")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    receipt = collect(manifest, args.output_dir, args.runtime_archive, args.reuse_source_dir)
    print(json.dumps({"status": receipt["status"], "source_archives": len(receipt["source_archives"]),
                      "notices": len(receipt["notices"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
