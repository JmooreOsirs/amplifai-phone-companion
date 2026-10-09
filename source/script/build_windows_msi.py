"""Author and build a private, unsigned, per-user WiX 3 MSI from a frozen receipt."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

from build_windows_candidate import file_records

NS = "http://schemas.microsoft.com/wix/2006/wi"
ET.register_namespace("", NS)
UPGRADE_CODE = "0C4BE78C-C5E1-4EA9-841B-4D8D93F45871"
VERSION = "1.0.13"


def tag(name: str) -> str:
    return f"{{{NS}}}{name}"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def checked_records(package: Path, receipt_path: Path) -> tuple[dict, list[dict]]:
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if receipt.get("status") != "disposable-windows-candidate-not-a-release":
        raise ValueError("wrong frozen candidate receipt")
    expected = receipt.get("files")
    actual = file_records(package)
    if not isinstance(expected, list) or not actual or expected != actual:
        raise ValueError("frozen package does not match receipt")
    encoded = json.dumps(actual, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if receipt.get("package_inventory_sha256") != hashlib.sha256(encoded).hexdigest():
        raise ValueError("frozen package inventory digest changed")
    if receipt.get("package_files") != len(actual) or receipt.get("package_bytes") != sum(item["bytes"] for item in actual):
        raise ValueError("frozen package count or bytes changed")
    seen: set[str] = set()
    for item in actual:
        relative = item["path"]
        parts = Path(relative).parts
        if not parts or any(part in (".", "..") for part in parts) or any("\\" in part for part in parts):
            raise ValueError("unsafe package path")
        lowered = relative.casefold()
        if lowered in seen:
            raise ValueError("case-folding package collision")
        seen.add(lowered)
    if "amplifaiphone.exe" not in seen:
        raise ValueError("missing Windows launcher")
    for legal in ("COPYRIGHT", "LICENSE", "THIRD-PARTY-NOTICES.txt"):
        if f"resources/legal/{legal}".casefold() not in seen:
            raise ValueError("missing staged legal notice")
    if "resources/phone-helper/amplifaiphonehelper.exe" not in seen:
        raise ValueError("missing bundled helper")
    return receipt, actual


def author_xml(records: list[dict], package: Path) -> ET.ElementTree:
    root = ET.Element(tag("Wix"))
    product = ET.SubElement(root, tag("Product"), {
        "Id": "*", "Name": "AMPLIFai Phone Windows candidate", "Language": "1033",
        "Version": VERSION, "Manufacturer": "Satoris", "UpgradeCode": UPGRADE_CODE,
    })
    ET.SubElement(product, tag("Package"), {
        "InstallerVersion": "500", "Compressed": "yes", "InstallScope": "perUser",
        "InstallPrivileges": "limited", "Description": "Private unsigned Windows qualification candidate",
    })
    ET.SubElement(product, tag("MediaTemplate"), {"EmbedCab": "yes"})
    ET.SubElement(product, tag("MajorUpgrade"), {"DowngradeErrorMessage": "A newer AMPLIFai Phone candidate is installed."})
    target = ET.SubElement(product, tag("Directory"), {"Id": "TARGETDIR", "Name": "SourceDir"})
    local = ET.SubElement(target, tag("Directory"), {"Id": "LocalAppDataFolder"})
    app = ET.SubElement(local, tag("Directory"), {"Id": "INSTALLFOLDER", "Name": "AMPLIFaiPhone"})
    program_menu = ET.SubElement(target, tag("Directory"), {"Id": "ProgramMenuFolder"})
    ET.SubElement(program_menu, tag("Directory"), {"Id": "ApplicationProgramsFolder", "Name": "AMPLIFai Phone"})
    paths = {""}
    for item in records:
        parent = Path(item["path"]).parent
        if parent == Path("."):
            continue
        current = ""
        for part in parent.parts:
            current = part if not current else current + "/" + part
            paths.add(current)
    directories = {"": app}
    for path in sorted(paths - {""}, key=lambda value: (value.count("/"), value.casefold())):
        parent, _, name = path.rpartition("/")
        identifier = "D" + hashlib.sha256(path.casefold().encode()).hexdigest()[:20]
        directories[path] = ET.SubElement(directories[parent], tag("Directory"), {
            "Id": identifier, "Name": name,
        })
    components = {}
    for path in sorted(paths, key=lambda value: (value.count("/"), value.casefold())):
        identity = hashlib.sha256((path or "root").casefold().encode()).hexdigest()[:20]
        component_id = "C" + identity
        component = ET.SubElement(directories[path], tag("Component"), {
            "Id": component_id,
            "Guid": str(uuid.uuid5(uuid.UUID(UPGRADE_CODE), "directory:" + path.casefold())).upper(),
        })
        ET.SubElement(component, tag("RegistryValue"), {
            "Root": "HKCU", "Key": r"Software\Satoris\AMPLIFaiPhone\Components",
            "Name": identity, "Value": "1", "Type": "integer", "KeyPath": "yes",
        })
        ET.SubElement(component, tag("RemoveFolder"), {
            "Id": "R" + identity, "Directory": directories[path].attrib["Id"], "On": "uninstall",
        })
        components[path] = component
    for index, item in enumerate(records, 1):
        relative = item["path"]
        parent_name = Path(relative).parent.as_posix()
        parent_name = "" if parent_name == "." else parent_name
        component = components[parent_name]
        ET.SubElement(component, tag("File"), {
            "Id": f"F{index:06d}", "Name": Path(relative).name,
            "Source": str(package / Path(relative)),
        })
        if relative == "AmplifaiPhone.exe":
            ET.SubElement(component, tag("Shortcut"), {
                "Id": "LaunchShortcut", "Directory": "ApplicationProgramsFolder",
                "Name": "AMPLIFai Phone", "WorkingDirectory": "INSTALLFOLDER",
                "Advertise": "no",
            })
            ET.SubElement(component, tag("RemoveFolder"), {
                "Id": "RemoveApplicationProgramsFolder", "Directory": "ApplicationProgramsFolder",
                "On": "uninstall",
            })
    feature = ET.SubElement(product, tag("Feature"), {"Id": "Complete", "Title": "AMPLIFai Phone", "Level": "1"})
    for component in components.values():
        ET.SubElement(feature, tag("ComponentRef"), {"Id": component.attrib["Id"]})
    return ET.ElementTree(root)


def checked_tool(path: Path, name: str) -> Path:
    path = path.resolve(strict=True)
    if path.name.lower() != name or not path.is_file():
        raise ValueError("wrong WiX compiler/linker")
    return path


def build(package: Path, receipt_path: Path, output: Path, candle: Path, light: Path) -> dict:
    if os.name != "nt":
        raise RuntimeError("WiX build requires Windows")
    receipt, records = checked_records(package, receipt_path)
    candle = checked_tool(candle, "candle.exe")
    light = checked_tool(light, "light.exe")
    if candle.parent != light.parent:
        raise ValueError("WiX compiler and linker directories differ")
    if output.exists():
        raise ValueError("output already exists")
    output.mkdir(parents=True)
    xml_path = output / "AmplifaiPhone.wxs"
    author_xml(records, package).write(xml_path, encoding="utf-8", xml_declaration=True)
    object_path = output / "AmplifaiPhone.wixobj"
    msi_path = output / "AmplifaiPhone-rc13-unsigned.msi"
    for command, label in (
        ([str(candle), "-nologo", "-arch", "x64", "-out", str(object_path), str(xml_path)], "compile"),
        ([str(light), "-nologo", "-out", str(msi_path), str(object_path)], "link"),
    ):
        result = subprocess.run(command, check=False, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, timeout=600)
        (output / f"wix-{label}.log").write_bytes(result.stdout)
        if result.returncode:
            raise RuntimeError(f"WiX {label} failed ({result.returncode}); inspect bounded local log")
    result = {
        "status": "private-unsigned-per-user-candidate",
        "msi_sha256": sha256(msi_path), "msi_bytes": msi_path.stat().st_size,
        "wxs_sha256": sha256(xml_path),
        "package_inventory_sha256": receipt["package_inventory_sha256"],
        "package_files": len(records), "package_bytes": receipt["package_bytes"],
        "gui_source_sha256": receipt["gui_source_sha256"],
        "entry_sha256": receipt["entry_sha256"],
        "legal_sha256": receipt["legal_sha256"],
    }
    (output / "msi-receipt.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("package", "receipt", "output", "candle", "light"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.package, args.receipt, args.output, args.candle, args.light), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
