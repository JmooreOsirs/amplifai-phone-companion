"""Compare a private MSI install with its frozen input, without opening the app."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from build_windows_candidate import file_records


def verify(install: Path, receipt_path: Path, *, removed: bool) -> dict:
    if removed:
        if install.exists() and any(install.rglob("*")):
            raise ValueError("installed package files remain after uninstall")
        return {"uninstalled_package_visible": False}
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    expected = receipt.get("files")
    if not isinstance(expected, list) or file_records(install) != expected:
        raise ValueError("installed package differs from frozen receipt")
    return {"installed_package_files": len(expected), "installed_package_bytes": receipt["package_bytes"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", required=True, type=Path)
    parser.add_argument("--receipt", required=True, type=Path)
    parser.add_argument("--removed", action="store_true")
    args = parser.parse_args()
    print(json.dumps(verify(args.install, args.receipt, removed=args.removed), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
