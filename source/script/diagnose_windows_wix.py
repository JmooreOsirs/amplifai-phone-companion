"""Focused WiX 3 diagnosis using only disposable synthetic one-byte files."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
from pathlib import Path

from build_windows_candidate import file_records
from build_windows_msi import build


def synthetic(root: Path, count: int) -> tuple[Path, Path]:
    package = root / "AmplifaiPhone"
    names = ["AmplifaiPhone.exe", "resources/phone-helper/AmplifaiPhoneHelper.exe",
             "resources/legal/COPYRIGHT", "resources/legal/LICENSE",
             "resources/legal/THIRD-PARTY-NOTICES.txt"]
    names += [f"_internal/module-{i // 128:03d}/file-{i:05d}.bin" for i in range(count - len(names))]
    for name in names:
        path = package / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    records = file_records(package)
    digest = hashlib.sha256(json.dumps(records, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    receipt = root / "candidate-receipt.json"
    receipt.write_text(json.dumps({"status": "disposable-windows-candidate-not-a-release",
                                   "package_inventory_sha256": digest, "package_files": len(records),
                                   "package_bytes": len(records), "files": records,
                                   "gui_source_sha256": "synthetic", "entry_sha256": "synthetic",
                                   "legal_sha256": {}}, sort_keys=True))
    return package, receipt


def summarize(root: Path, count: int, candle: Path, light: Path) -> dict:
    package, receipt = synthetic(root, count)
    output = root / "msi"
    try:
        built = build(package, receipt, output, candle, light)
        return {"files": count, "result": "linked", "msi_bytes": built["msi_bytes"]}
    except (ValueError, RuntimeError) as error:
        link_log = output / "wix-link.log"
        lines = link_log.read_text(errors="replace").splitlines() if link_log.exists() else []
        errors = [line.strip()[:500] for line in lines if re.search(r"\berror\b|\bfatal\b|\bexception\b", line, re.I)]
        return {"files": count, "result": type(error).__name__, "reason": str(error)[:200],
                "link_errors": errors[:8], "link_warning_count": sum("warning" in line.lower() for line in lines),
                "link_first_lines": [line.strip()[:300] for line in lines[:5]]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candle", type=Path, required=True)
    parser.add_argument("--light", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="amplifai-wix-diagnostic-") as temporary:
        root = Path(temporary)
        print(json.dumps({"tiny": summarize(root / "tiny", 6, args.candle, args.light)}, sort_keys=True))
        print(json.dumps({"large": summarize(root / "large", 7289, args.candle, args.light)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
