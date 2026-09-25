#!/usr/bin/env python3
"""Hash the manually verified native-source candidates for local review."""

from __future__ import annotations

import argparse
import hashlib
import json
import tarfile
from pathlib import Path


# GitHub URLs are pinned to tag-resolved commit hashes, not floating branches.
# A candidate may match an upstream version without proving byte-for-byte
# rebuild correspondence to the frozen binary. See status on each record.
SOURCES = [
    ("CPython", "3.12.12", "Python-3.12.12.tar.xz", "https://www.python.org/ftp/python/3.12.12/Python-3.12.12.tar.xz", "binary version and Python.org release; standalone patches separately staged", "version-matched"),
    ("python-build-standalone", "20260203", "python-build-standalone-20260203.tar.gz", "https://codeload.github.com/astral-sh/python-build-standalone/tar.gz/0f1d12e309d6705556d4fdcfdd85bd6b38b06bb1", "uv interpreter BUILD marker and resolved upstream tag", "version-matched"),
    ("libusb", "1.0.29", "libusb-1.0.29.tar.bz2", "https://github.com/libusb/libusb/releases/download/v1.0.29/libusb-1.0.29.tar.bz2", "bundled Mach-O UUID matches local Homebrew libusb 1.0.29; GitHub release SHA-256 also matched", "version-matched"),
    ("libavif", "1.4.2", "libavif-1.4.2.tar.gz", "https://codeload.github.com/AOMediaCodec/libavif/tar.gz/c5240fc79fe5c2407e10afd35f5505ef6333ea49", "Pillow wheel SBOM", "version-matched"),
    ("Brotli", "1.2.0", "brotli-1.2.0.tar.gz", "https://codeload.github.com/google/brotli/tar.gz/028fb5a23661f123017c060daa546b55cf4bde29", "bundled dylib filenames; not in Pillow SBOM", "version-candidate"),
    ("FreeType", "2.14.3", "freetype-2.14.3.tar.gz", "https://codeload.github.com/freetype/freetype/tar.gz/0a0221a1347e2f1e07c395263540026e9a0aa7c7", "Pillow wheel SBOM", "version-matched"),
    ("HarfBuzz", "14.2.1", "harfbuzz-14.2.1.tar.gz", "https://codeload.github.com/harfbuzz/harfbuzz/tar.gz/56feae4035bdd48f62ba2b8d8c16232d4d89b3a4", "Pillow wheel SBOM and binary string", "version-matched"),
    ("libjpeg-turbo", "3.1.4.1", "libjpeg-turbo-3.1.4.1.tar.gz", "https://codeload.github.com/libjpeg-turbo/libjpeg-turbo/tar.gz/9217719d3a58633923b096af4c1d50d304768a64", "Pillow wheel SBOM", "version-matched"),
    ("Little CMS 2", "2.19.1", "little-cms-2.19.1.tar.gz", "https://codeload.github.com/mm2/Little-CMS/tar.gz/21c582a594fe5279f90c0b93437c398f93bf62b0", "Pillow wheel SBOM", "version-matched"),
    ("xz/liblzma", "5.8.3", "xz-5.8.3.tar.gz", "https://codeload.github.com/tukaani-project/xz/tar.gz/4b73f2ec19a99ef465282fbce633e8deb33691b3", "bundled binary string; not in Pillow SBOM", "version-matched"),
    ("OpenJPEG", "2.5.4", "openjpeg-2.5.4.tar.gz", "https://codeload.github.com/uclouvain/openjpeg/tar.gz/6c4a29b00211eb0430fa0e5e890f1ce5c80f409f", "Pillow wheel SBOM", "version-matched"),
    ("libpng", "1.6.58", "libpng-1.6.58.tar.gz", "https://codeload.github.com/pnggroup/libpng/tar.gz/3061454d980de7d53608f594194cfac722721d2a", "bundled binary string; not in Pillow SBOM", "version-matched"),
    ("libtiff", "4.7.1", "libtiff-4.7.1.tar.gz", "https://download.osgeo.org/libtiff/tiff-4.7.1.tar.gz", "Pillow wheel SBOM", "version-matched"),
    ("libwebp", "1.6.0", "libwebp-1.6.0.tar.gz", "https://codeload.github.com/webmproject/libwebp/tar.gz/4fa21912338357f89e4fd51cf2368325b59e9bd9", "Pillow wheel SBOM; covers libwebp, demux, mux, and sharpyuv", "version-matched"),
    ("libxcb", "1.17.0", "libxcb-1.17.0.tar.xz", "https://xorg.freedesktop.org/archive/individual/lib/libxcb-1.17.0.tar.xz", "Pillow wheel SBOM", "version-matched"),
    ("zlib-ng", "2.3.3", "zlib-ng-2.3.3.tar.gz", "https://codeload.github.com/zlib-ng/zlib-ng/tar.gz/12731092979c6d07f42da27da673a9f6c7b13586", "bundled binary string; Pillow SBOM labels this zlib 2.3.3", "version-matched"),
    ("libXau", "1.0.12", "libXau-1.0.12-UNCONFIRMED.tar.xz", "https://xorg.freedesktop.org/archive/individual/lib/libXau-1.0.12.tar.xz", "version not stated in Pillow SBOM or bundled filename; local Homebrew UUID differs", "unconfirmed-version"),
]
INDEPENDENT_DIGESTS = {
    "Python-3.12.12.tar.xz": ("md5", "04feb01316c7bb1b448001adbc63dd23"),
    "libusb-1.0.29.tar.bz2": ("sha256", "5977fc950f8d1395ccea9bd48c06b3f808fd3c2c961b44b0c2e6e29fc3a70a85"),
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archives", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    records = []
    for name, version, filename, url, evidence, status in SOURCES:
        path = args.archives / filename
        if not path.is_file():
            raise FileNotFoundError(path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        independent = INDEPENDENT_DIGESTS.get(filename)
        if independent:
            algorithm, expected = independent
            actual = hashlib.new(algorithm, path.read_bytes()).hexdigest()
            if actual != expected:
                raise ValueError(f"independent release digest mismatch: {filename}")
        with tarfile.open(path, "r:*") as archive:
            members = archive.getmembers()
        notice_paths = sorted(
            item.name for item in members
            if item.isfile() and Path(item.name).name.upper().startswith(
                ("LICENSE", "LICENCE", "COPYING", "NOTICE")
            )
        )
        records.append({
            "name": name, "version": version, "filename": filename,
            "upstream_url": url, "sha256": digest, "size": path.stat().st_size,
            "independent_release_digest": independent,
            "binary_version_evidence": evidence, "source_status": status,
            "embedded_notice_paths": notice_paths,
        })
    args.manifest.write_text(json.dumps({
        "purpose": "local native-source review; not binary-rebuild or license clearance",
        "records": records,
    }, indent=2, sort_keys=True) + "\n")
    print(f"recorded {len(records)} native/interpreter source candidates")


if __name__ == "__main__":
    main()
