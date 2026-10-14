#!/usr/bin/env python3
"""Remove only SwiftPM's active developer-tool run paths from a staged wrapper."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess

from verify_macos_architecture import is_system, parse_load_commands, run_tool


def normalize(binary: Path, architecture: str):
    binary = binary.resolve(strict=True)
    developer = Path(run_tool(["/usr/bin/xcode-select", "-p"]).strip()).resolve(strict=True)
    metadata = parse_load_commands(run_tool(["/usr/bin/otool", "-arch", architecture, "-h", "-l", str(binary)]))
    removed = []
    for raw in dict.fromkeys(metadata["rpaths"]):
        if not raw.startswith("/") or is_system(Path(raw)):
            continue
        target = Path(raw).resolve()
        if developer not in target.parents or not raw.endswith("/Frameworks"):
            raise ValueError("Unexpected external run path in staged wrapper")
        subprocess.run(["/usr/bin/xcrun", "install_name_tool", "-delete_rpath", raw, str(binary)], check=True)
        removed.append(raw)
    final = parse_load_commands(run_tool(["/usr/bin/otool", "-arch", architecture, "-h", "-l", str(binary)]))
    return {"binary": binary.name, "architecture": architecture, "removedDeveloperRunPaths": removed,
            "remainingRunPaths": final["rpaths"], "resigningRequired": bool(removed)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=Path)
    parser.add_argument("--architecture", choices=("arm64", "x86_64"), required=True)
    args = parser.parse_args()
    print(json.dumps(normalize(args.binary, args.architecture), sort_keys=True))


if __name__ == "__main__":
    main()
