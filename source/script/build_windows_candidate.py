"""Build and inspect a disposable Windows onedir candidate; never publish it."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import struct
import subprocess
import sys
from pathlib import Path

GUI_FILES = (
    "collector/windows/app.py",
    "collector/windows/session.py",
    "collector/windows/helper_process.py",
)
LOGO = "public/brand/amplifai-by-nexus-original.png"
LOCK = "collector/windows-requirements.lock"
ENTRY = "collector/agent_entry.py"
EXPECTED_LOGO_SHA256 = "07dabb73a6c03cb400079b05fc2ac7b26042529674f0fe56f261f2028e731dce"
EXPECTED_LOCK_SHA256 = "aa8ee933827681ebda483c2a889dc6156f7d9de906fe21d90f055fd06939c95d"
MAX_HELPER_PACKET_BYTES = 65536
REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def _checked(path: Path, *, directory: bool) -> None:
    item = path.lstat()
    if stat.S_ISLNK(item.st_mode) or getattr(item, "st_file_attributes", 0) & REPARSE_POINT:
        raise ValueError(f"linked package input: {path}")
    if not (stat.S_ISDIR(item.st_mode) if directory else stat.S_ISREG(item.st_mode)):
        raise ValueError(f"unexpected package input kind: {path}")


def _source_file(source: Path, relative: str) -> Path:
    _checked(source, directory=True)
    current = source
    components = Path(relative).parts
    if not components or any(part in {"..", "."} for part in components):
        raise ValueError("invalid source input path")
    for component in components[:-1]:
        current /= component
        _checked(current, directory=True)
    current /= components[-1]
    _checked(current, directory=False)
    return current


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def gui_digest(source: Path) -> str:
    digest = hashlib.sha256()
    for relative in GUI_FILES:
        path = _source_file(source, relative)
        digest.update(relative.encode("utf-8") + b"\0")
        digest.update(_sha256(path).encode("ascii") + b"\n")
    return digest.hexdigest()


def clean_child_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return environment


def freezer_command(*, python: Path, source: Path, build: Path, window: bool) -> list[str]:
    name = "AmplifaiPhone" if window else "AmplifaiPhoneHelper"
    part = "wrapper" if window else "helper"
    return [
        str(python), "-m", "PyInstaller", "--noconfirm", "--clean",
        "--onedir", "--contents-directory", "_internal",
        "--windowed" if window else "--console", "--noupx", "--name", name,
        "--paths", str(source / ("collector/windows" if window else "collector")),
        "--distpath", str(build / f"{part}-dist"),
        "--workpath", str(build / f"{part}-work"),
        "--specpath", str(build / f"{part}-spec"),
        str(source / ("collector/windows/app.py" if window else ENTRY)),
    ]


def parse_helper_packet(data: bytes, mode: str) -> dict:
    if mode not in {"runtime-check", "inspect"}:
        raise ValueError("invalid inspection mode")
    if len(data) > MAX_HELPER_PACKET_BYTES or data.count(b"\n") != 1 or not data.endswith(b"\n"):
        raise ValueError("invalid helper frame")
    try:
        packet = json.loads(data.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("invalid helper JSON") from error
    if not isinstance(packet, dict):
        raise ValueError("invalid helper packet")
    if mode == "runtime-check" and (packet.get("kind") != "runtime" or packet.get("ready") is not True):
        raise ValueError("frozen helper runtime is not ready")
    if mode == "inspect" and (packet.get("kind") != "residue" or packet.get("sessions") != []):
        raise ValueError("frozen helper has residue or could not inspect it")
    return packet


def file_records(root: Path) -> list[dict[str, str | int]]:
    _checked(root, directory=True)
    records: list[dict[str, str | int]] = []

    def unreadable(error: OSError) -> None:
        raise error

    for parent, directories, files in os.walk(root, followlinks=False, onerror=unreadable):
        for name in directories:
            _checked(Path(parent) / name, directory=True)
        for name in files:
            path = Path(parent) / name
            _checked(path, directory=False)
            records.append({
                "path": path.relative_to(root).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            })
    return sorted(records, key=lambda record: str(record["path"]))


def _run(command: list[str], *, cwd: Path, log: Path | None, timeout_seconds: int) -> bytes:
    try:
        result = subprocess.run(
            command, cwd=cwd, env=clean_child_environment(), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(f"bounded build/inspection timed out: {log.name if log else command[-1]}") from error
    if log is not None:
        log.write_bytes(result.stdout)
    if result.returncode != 0:
        raise RuntimeError(f"build/inspection failed with exit {result.returncode}: {log.name if log else command[-1]}")
    return result.stdout


def build_candidate(*, source: Path, python: Path, output: Path) -> dict:
    if sys.platform != "win32" or sys.version_info[:2] != (3, 12) or struct.calcsize("P") != 8:
        raise RuntimeError("candidate build requires Windows x64 CPython 3.12")
    source = source.absolute()
    python = python.absolute()
    output = output.absolute()
    if output == source or source in output.parents:
        raise ValueError("candidate output must be outside source")
    _checked(source, directory=True)
    _checked(python, directory=False)
    _checked(output.parent, directory=True)
    logo = _source_file(source, LOGO)
    lock = _source_file(source, LOCK)
    _source_file(source, ENTRY)
    source_digest = gui_digest(source)
    if _sha256(logo) != EXPECTED_LOGO_SHA256:
        raise ValueError("Original logo bytes differ from the fixed package input")
    if _sha256(lock) != EXPECTED_LOCK_SHA256:
        raise ValueError("Windows requirements bytes differ from the hash-pinned package input")
    output.mkdir()
    for window in (False, True):
        part = "wrapper" if window else "helper"
        _run(
            freezer_command(python=python, source=source, build=output, window=window),
            cwd=source, log=output / f"{part}-freeze.log",
            timeout_seconds=180 if window else 360,
        )
    helper = output / "helper-dist/AmplifaiPhoneHelper"
    package = output / "wrapper-dist/AmplifaiPhone"
    for tree, executable in ((helper, "AmplifaiPhoneHelper.exe"), (package, "AmplifaiPhone.exe")):
        _checked(tree, directory=True)
        _checked(tree / executable, directory=False)
        _checked(tree / "_internal", directory=True)
        file_records(tree)
    resources = package / "resources"
    resources.mkdir()
    shutil.copytree(helper, resources / "phone-helper")
    shutil.copy2(logo, resources / Path(LOGO).name)
    if _sha256(resources / Path(LOGO).name) != EXPECTED_LOGO_SHA256:
        raise ValueError("staged original logo bytes changed")
    frozen_helper = resources / "phone-helper/AmplifaiPhoneHelper.exe"
    for mode in ("runtime-check", "inspect"):
        packet = _run(
            [str(frozen_helper), mode], cwd=frozen_helper.parent,
            log=None, timeout_seconds=60,
        )
        parse_helper_packet(packet, mode)
    records = file_records(package)
    inventory_sha256 = hashlib.sha256(
        json.dumps(records, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    receipt = {
        "status": "disposable-windows-candidate-not-a-release",
        "gui_source_sha256": source_digest,
        "entry_sha256": _sha256(source / ENTRY),
        "lock_sha256": EXPECTED_LOCK_SHA256,
        "logo_sha256": EXPECTED_LOGO_SHA256,
        "package_files": len(records),
        "package_bytes": sum(int(record["bytes"]) for record in records),
        "package_inventory_sha256": inventory_sha256,
        "files": records,
    }
    (output / "candidate-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build_candidate(source=args.source, python=args.python, output=args.output)
    print(json.dumps({key: value for key, value in result.items() if key != "files"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
