#!/usr/bin/env python3
"""Audit a complete macOS app's native closure without launching its code.

This is an architecture/deployment-target gate, not a code-signing, notarization,
phone-collection or runtime compatibility claim. Requires Apple's lipo/otool.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import re
import subprocess
from pathlib import Path
from typing import Callable

MAGICS = {
    b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca", b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca",
}
LOAD_COMMANDS = {
    "LC_LOAD_DYLIB", "LC_LOAD_WEAK_DYLIB", "LC_REEXPORT_DYLIB",
    "LC_LOAD_UPWARD_DYLIB", "LC_LAZY_LOAD_DYLIB",
}
HELPER = "Contents/Resources/helper/amplifai-agent"


def version(value: str) -> tuple[int, int, int]:
    if not re.fullmatch(r"\d+(?:\.\d+){0,2}", value):
        raise ValueError(f"Invalid macOS version: {value}")
    parts = tuple(int(part) for part in value.split("."))
    return (parts + (0, 0))[:3]


def run_tool(args: list[str]) -> str:
    return subprocess.run(args, check=True, text=True, capture_output=True).stdout


def parse_load_commands(output: str) -> dict:
    """Parse selected-slice `otool -h -l`; reject missing/foreign platforms."""
    header = re.search(
        r"^\s*magic\s+cputype\s+cpusubtype\s+caps\s+filetype[^\n]*\n\s*(\S+(?:\s+\S+){4})",
        output, re.MULTILINE,
    )
    if not header:
        raise ValueError("Mach-O header could not be parsed")
    filetype = int(header.group(1).split()[4], 0)
    minima, platforms, rpaths, dependencies = [], [], [], []
    for block in re.split(r"^Load command \d+\s*$", output, flags=re.MULTILINE)[1:]:
        match = re.search(r"^\s*cmd\s+(\S+)", block, re.MULTILINE)
        if not match:
            continue
        command = match.group(1)
        if command == "LC_BUILD_VERSION":
            platform = re.search(r"^\s*platform\s+(\S+)", block, re.MULTILINE)
            minimum = re.search(r"^\s*minos\s+(\S+)", block, re.MULTILINE)
            if not platform or not minimum:
                raise ValueError("Incomplete LC_BUILD_VERSION")
            platforms.append(platform.group(1))
            minima.append(minimum.group(1))
        elif command == "LC_VERSION_MIN_MACOSX":
            minimum = re.search(r"^\s*version\s+(\S+)", block, re.MULTILINE)
            if not minimum:
                raise ValueError("Incomplete LC_VERSION_MIN_MACOSX")
            platforms.append("macos")
            minima.append(minimum.group(1))
        elif command == "LC_RPATH":
            path = re.search(r"^\s*path\s+(.+)\s+\(offset \d+\)", block, re.MULTILINE)
            if not path:
                raise ValueError("Incomplete LC_RPATH")
            rpaths.append(path.group(1))
        elif command in LOAD_COMMANDS:
            name = re.search(r"^\s*name\s+(.+)\s+\(offset \d+\)", block, re.MULTILINE)
            if not name:
                raise ValueError(f"Incomplete {command}")
            dependencies.append(name.group(1))
    if not minima:
        raise ValueError("Missing macOS minimum-version load command")
    if any(platform.lower() not in {"1", "macos", "macosx"} for platform in platforms):
        raise ValueError("Non-macOS Mach-O platform")
    return {"filetype": filetype, "minimum_macos": max(minima, key=version),
            "rpaths": rpaths, "dependencies": dependencies}


def is_system(path: Path) -> bool:
    return any(path == root or root in path.parents for root in
               (Path("/usr/lib"), Path("/System/Library")))


def contained(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def loader_path(path: Path) -> Path:
    # Apple's absolute install names are stable even when a live system resolves
    # them into a Cryptex volume. Normalize '..' first, but do not turn trusted
    # /System/Library or /usr/lib install names into host-specific physical paths.
    # App paths still resolve symlinks so an in-bundle path cannot hide an escape.
    normalized = Path(os.path.normpath(path))
    return normalized if is_system(normalized) else normalized.resolve()


def sha256(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def audit(app: Path, arch: str, minimum_macos: str,
          runner: Callable[[list[str]], str] = run_tool) -> dict:
    ceiling = version(minimum_macos)
    errors: list[str] = []
    root = app.resolve(strict=True)
    if not root.is_dir() or root.suffix != ".app" or app.is_symlink():
        raise ValueError("Supply a real .app directory, not a symlink")
    receipt = {"kind": "macos-native-architecture-audit", "schema_version": 1,
               "app": root.name, "required_architecture": arch,
               "minimum_macos": minimum_macos, "launches_app": False,
               "scope": "Static native architecture, deployment target and bundled dependency closure only",
               "files": [], "symlinks": [], "errors": errors, "passed": False}
    paths: list[Path] = []
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in sorted(dirs + files):
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                try:
                    target = path.resolve(strict=True)
                    if not contained(target, root):
                        raise ValueError("target escapes app")
                    receipt["symlinks"].append({"path": relative,
                                                "target": target.relative_to(root).as_posix()})
                except (OSError, RuntimeError, ValueError):
                    errors.append(f"Unsafe or broken symlink: {relative}")
            elif path.is_file():
                paths.append(path)
            elif not path.is_dir():
                errors.append(f"Unsupported special file: {relative}")
    main = None
    try:
        info = plistlib.loads((root / "Contents/Info.plist").read_bytes())
        executable = info["CFBundleExecutable"]
        if not isinstance(executable, str) or not executable or Path(executable).name != executable:
            raise ValueError("Invalid CFBundleExecutable")
        main = root / "Contents/MacOS" / executable
        declared = info.get("LSMinimumSystemVersion")
        declared_by_arch = info.get("LSMinimumSystemVersionByArchitecture", {}).get(arch)
        for value in (declared, declared_by_arch):
            if value and version(value) > ceiling:
                errors.append("Info.plist minimum macOS exceeds requested deployment target")
        allowed = info.get("LSArchitecturePriority")
        if allowed is not None and arch not in allowed:
            errors.append("Info.plist LSArchitecturePriority omits required architecture")
    except (KeyError, OSError, TypeError, ValueError, plistlib.InvalidFileException):
        errors.append("Missing or invalid Contents/Info.plist")
    nodes = {}
    for path in sorted(paths):
        with path.open("rb") as source:
            magic = source.read(4)
        if magic not in MAGICS:
            continue
        relative = path.relative_to(root).as_posix()
        record = {"path": relative, "sha256": sha256(path), "architectures": [],
                  "resolved_dependencies": []}
        receipt["files"].append(record)
        try:
            architectures = runner(["/usr/bin/lipo", "-archs", str(path)]).strip().split()
            record["architectures"] = architectures
            if arch not in architectures:
                errors.append(f"Missing {arch} slice: {relative}")
                continue
            details = parse_load_commands(runner(["/usr/bin/otool", "-arch", arch, "-h", "-l", str(path)]))
            record.update(details)
            if version(details["minimum_macos"]) > ceiling:
                errors.append(f"Requires macOS {details['minimum_macos']}, above {minimum_macos}: {relative}")
            nodes[path] = record
        except (OSError, ValueError, subprocess.CalledProcessError) as exc:
            errors.append(f"Native inspection failed ({type(exc).__name__}): {relative}")
    if not receipt["files"]:
        errors.append("No Mach-O files found")
    for label, path in (("main app", main), ("frozen helper", root / HELPER)):
        if path is None or not path.is_file() or not os.access(path, os.X_OK):
            errors.append(f"Missing executable {label}")
        elif path.resolve() not in nodes or nodes[path.resolve()].get("filetype") != 2:
            errors.append(f"Required {label} is not a valid {arch} Mach-O executable")

    def expand(raw: str, loader: Path, executable: Path) -> Path:
        for token, base in (("@loader_path", loader.parent), ("@executable_path", executable.parent)):
            if raw == token or raw.startswith(token + "/"):
                return loader_path(base / raw[len(token):].lstrip("/"))
        if raw.startswith("/"):
            return loader_path(Path(raw))
        raise ValueError("Unsupported relative loader path")

    # Walk each executable's dyld closure with its own executable directory and
    # inherited run-path stack. This matters for Sparkle's nested app/XPC helpers.
    checked: set[Path] = set()
    explored: set[tuple[Path, Path, tuple[Path, ...]]] = set()
    def walk(path: Path, executable: Path, inherited: tuple[Path, ...], chain: tuple[Path, ...]):
        if path in chain:
            return
        if len(chain) > 128:
            errors.append("Native dependency chain exceeds audit bound")
            return
        checked.add(path)
        node = nodes[path]
        local = []
        for raw in node["rpaths"]:
            try:
                target = expand(raw, path, executable)
                if not contained(target, root) and not is_system(target):
                    raise ValueError("External run path")
                local.append(target)
            except ValueError:
                errors.append(f"Unsafe run path {raw}: {node['path']}")
        search = tuple(dict.fromkeys((*local, *inherited)))
        context = (path, executable, search)
        if context in explored:
            return
        explored.add(context)
        for dependency in node["dependencies"]:
            try:
                if dependency.startswith("@rpath/"):
                    candidates = [loader_path(base / dependency[7:]) for base in search]
                else:
                    candidates = [expand(dependency, path, executable)]
                # System libraries can live only in the dyld cache, so do not
                # require on-disk presence for the two system-only directories.
                target = next((candidate for candidate in candidates
                               if is_system(candidate) or (contained(candidate, root) and candidate.is_file())), None)
                if target is None:
                    raise ValueError("Unresolved or external dependency")
                system = is_system(target)
                if not system and target not in nodes:
                    raise ValueError("Dependency lacks a verified native slice")
                resolved = {"name": dependency,
                            "resolved": str(target) if system else target.relative_to(root).as_posix(),
                            "executable": executable.relative_to(root).as_posix()}
                if resolved not in node["resolved_dependencies"]:
                    node["resolved_dependencies"].append(resolved)
                if not system:
                    walk(target, executable, search, (*chain, path))
            except (ValueError, OSError, RuntimeError):
                errors.append(f"Unresolved or external dependency {dependency}: {node['path']}")

    for path, node in nodes.items():
        if node["filetype"] == 2:
            walk(path, path, (), ())
    # Python loads extension modules dynamically; they need the helper's run
    # paths even though no LC_LOAD_DYLIB from its executable names them.
    helper = root / HELPER
    for path in nodes:
        if path in checked:
            continue
        executable = helper if contained(path, helper.parent) and helper in nodes else main
        if executable not in nodes:
            errors.append(f"Cannot identify loader context: {nodes[path]['path']}")
            continue
        inherited = []
        for raw in nodes[executable]["rpaths"]:
            try:
                inherited.append(expand(raw, executable, executable))
            except ValueError:
                pass  # Reported during executable traversal above.
        walk(path, executable, tuple(inherited), ())
    receipt["errors"] = sorted(set(errors))
    receipt["mach_o_count"] = len(receipt["files"])
    manifest = [{"path": entry["path"], "sha256": entry["sha256"]} for entry in receipt["files"]]
    receipt["native_manifest_sha256"] = hashlib.sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    receipt["passed"] = not receipt["errors"]
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app", type=Path)
    parser.add_argument("--arch", choices=("x86_64", "arm64"), required=True)
    parser.add_argument("--minimum-macos", default="14.0")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        receipt = audit(args.app, args.arch, args.minimum_macos)
    except (OSError, ValueError) as exc:
        receipt = {"kind": "macos-native-architecture-audit", "passed": False,
                   "errors": [f"Invalid audit input ({type(exc).__name__})"]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"passed": receipt["passed"], "mach_o_count": receipt.get("mach_o_count", 0),
                      "errors": receipt["errors"]}, sort_keys=True))
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
