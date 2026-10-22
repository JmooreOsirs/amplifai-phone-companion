"""Compare a private MSI install with its frozen input, without opening the app."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from build_windows_candidate import file_records
from build_windows_msi import PROTOCOL_KEY


def verify_setup_protocol(install: Path, *, removed: bool) -> dict:
    import winreg

    expected = (
        (PROTOCOL_KEY, "", "URL:AMPLIFai Phone setup"),
        (PROTOCOL_KEY, "URL Protocol", ""),
        (PROTOCOL_KEY + r"\shell\open\command", "", f'"{install / "AmplifaiPhone.exe"}" "%1"'),
    )
    for key, name, value in expected:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as handle:
                actual, kind = winreg.QueryValueEx(handle, name)
        except FileNotFoundError:
            if removed:
                continue
            raise ValueError("installed setup protocol registration is missing") from None
        if removed:
            raise ValueError("setup protocol registration remains after uninstall")
        if actual != value or kind != winreg.REG_SZ:
            raise ValueError("installed setup protocol registration differs")
    return {"setup_protocol_registered": not removed}


def verify(install: Path, receipt_path: Path, *, removed: bool) -> dict:
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    protocol = receipt.get("native_setup_protocol")
    if protocol not in (None, "phone-native-setup-v2"):
        raise ValueError("unsupported installed setup protocol")
    registration = verify_setup_protocol(install, removed=removed) if protocol else {}
    if removed:
        if install.exists() and any(install.rglob("*")):
            raise ValueError("installed package files remain after uninstall")
        return {"uninstalled_package_visible": False, **registration}
    expected = receipt.get("files")
    if not isinstance(expected, list) or file_records(install) != expected:
        raise ValueError("installed package differs from frozen receipt")
    return {"installed_package_files": len(expected), "installed_package_bytes": receipt["package_bytes"], **registration}


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
