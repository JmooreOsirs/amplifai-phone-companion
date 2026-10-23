"""Build/run a separate, non-shipped PyInstaller UI fixture with production code custody."""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import types

from build_windows_candidate import (EXPECTED_FONT_SHA256, EXPECTED_LOCK_SHA256, EXPECTED_LOGO_SHA256,
                                     FONT, GUI_FILES, LOCK, LOGO, clean_child_environment, gui_digest)

NAME = "AmplifaiGuidedQualification"
AUTHORITY_FIXTURE = "collector/windows/tests/fixtures/native-setup-v2.json"
HARNESS_FILES = tuple("collector/windows/tests/" + name for name in (
    "frozen_guided_fixture.py", "render_guided_journey.py", "render_contact_workspace.py", "native_setup_fixture.py",
    "render_candidate.py", "visible_desktop.py", "test_native_app.py", "test_native_run.py",
    "test_native_setup.py", "test_gui_guards.py", "test_session.py"))


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def code_record(code):
    def constant(value):
        if isinstance(value, types.CodeType):
            return {"code": code_record(value)}
        if isinstance(value, (tuple, frozenset)):
            members = [constant(item) for item in value]
            if isinstance(value, frozenset):
                members.sort(key=lambda item: json.dumps(item, sort_keys=True))
            return {type(value).__name__: members}
        if isinstance(value, bytes):
            return {"bytes": value.hex()}
        return {type(value).__name__: repr(value)}
    # PyInstaller changes co_filename; all executable code, constants, names,
    # signatures and line/exception tables must still match the pinned source.
    values = {key: getattr(code, key) for key in (
        "co_argcount", "co_posonlyargcount", "co_kwonlyargcount", "co_nlocals", "co_stacksize", "co_flags",
        "co_name", "co_qualname", "co_firstlineno", "co_names", "co_varnames", "co_freevars", "co_cellvars")}
    values.update({key: getattr(code, key).hex() for key in ("co_code", "co_linetable", "co_exceptiontable")})
    values["co_consts"] = [constant(value) for value in code.co_consts]
    return values


def code_sha256(code):
    return hashlib.sha256(json.dumps(code_record(code), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def module_name(relative):
    if relative.startswith("collector/windows/"):
        return Path(relative).stem
    return relative.removeprefix("collector/").removesuffix(".py").replace("/", ".")


def source_manifest(source):
    assets = {path: {"bytes": (source / path).stat().st_size, "sha256": sha256(source / path)}
              for path in (LOGO, FONT, LOCK, AUTHORITY_FIXTURE, *HARNESS_FILES, "script/build_windows_candidate.py")}
    for path, expected in ((FONT, EXPECTED_FONT_SHA256), (LOGO, EXPECTED_LOGO_SHA256), (LOCK, EXPECTED_LOCK_SHA256)):
        if assets[path]["sha256"] != expected:
            raise RuntimeError("Pinned UI qualification asset differs: " + path)
    modules = []
    for relative in GUI_FILES:
        contents = (source / relative).read_bytes()
        modules.append({"path": relative, "module": module_name(relative), "bytes": len(contents),
                        "sha256": hashlib.sha256(contents).hexdigest(),
                        "codeSha256": code_sha256(compile(contents, relative, "exec", dont_inherit=True, optimize=0))})
    return {"schema": 1, "guiSourceSha256": gui_digest(source), "productionModules": modules,
            "sourceAssetsAndHarness": assets, "python": platform.python_version(),
            "sourceCommit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()}


def verify_frozen_modules(bundle, manifest):
    records = []
    for expected in manifest["productionModules"]:
        raw = bundle / "qualification-source" / expected["path"]
        if sha256(raw) != expected["sha256"] or raw.stat().st_size != expected["bytes"]:
            raise RuntimeError("Bundled production source changed: " + expected["path"])
        module = importlib.import_module(expected["module"])
        origin = Path(module.__file__).resolve()
        if not origin.is_relative_to(bundle) or not hasattr(module.__loader__, "get_code"):
            raise RuntimeError("Production module was not loaded from the frozen bundle: " + expected["module"])
        code = module.__loader__.get_code(expected["module"])
        if not isinstance(code, types.CodeType) or code_sha256(code) != expected["codeSha256"]:
            raise RuntimeError("Frozen production module differs from pinned source: " + expected["module"])
        records.append({**expected, "frozenOrigin": str(origin.relative_to(bundle)),
                        "loader": type(module.__loader__).__name__, "codeMatchesSource": True})
    for relative, expected in manifest["sourceAssetsAndHarness"].items():
        path = bundle / "qualification-source" / relative
        if sha256(path) != expected["sha256"] or path.stat().st_size != expected["bytes"]:
            raise RuntimeError("Bundled qualification asset changed: " + relative)
    if gui_digest(bundle / "qualification-source") != manifest["guiSourceSha256"]:
        raise RuntimeError("Bundled GUI source digest changed")
    return records


def render_frozen(output):
    if not getattr(sys, "frozen", False) or sys.flags.optimize != 0:
        raise RuntimeError("This entry requires the frozen unoptimized qualification executable")
    bundle = Path(sys._MEIPASS).resolve()
    manifest_path = bundle / "qualification-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest["python"] != platform.python_version():
        raise RuntimeError("Fixture source and frozen Python runtimes differ")
    identity = {"executableSha256": sha256(Path(sys.executable)), "executableBytes": Path(sys.executable).stat().st_size,
                "sourceCommit": manifest["sourceCommit"], "manifestSha256": sha256(manifest_path),
                "productionModules": verify_frozen_modules(bundle, manifest),
                "fixtureExecutableName": Path(sys.executable).name, "shippedProductExecutable": False,
                "boundary": "Separate frozen synthetic harness imports exact production classes; normal installed executable qualification remains separate."}
    from render_guided_journey import render
    source = bundle / "qualification-source"
    render(argparse.Namespace(source=source, logo=source / LOGO, output=output), frozen_identity=identity)


def build_and_render(source, output):
    if output.exists():
        raise RuntimeError("Qualification output must be new and runner-owned")
    output.mkdir(parents=True)
    manifest = source_manifest(source)
    source_copy = output / "source"
    for relative in (GUI_FILES + tuple(manifest["sourceAssetsAndHarness"])):
        destination = source_copy / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / relative, destination)
    manifest_path = output / "qualification-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--console", "--noupx",
               "--contents-directory", "_internal", "--name", NAME, "--optimize", "0",
               "--distpath", str(output / "dist"), "--workpath", str(output / "work"), "--specpath", str(output / "spec")]
    for directory in ("collector", "collector/windows", "collector/windows/tests", "script"):
        command.extend(("--paths", str(source / directory)))
    for record in manifest["productionModules"]:
        command.extend(("--hidden-import", record["module"]))
    command.extend(("--add-data", str(source_copy) + os.pathsep + "qualification-source",
                    "--add-data", str(manifest_path) + os.pathsep + ".",
                    "--add-data", str(source / AUTHORITY_FIXTURE) + os.pathsep + "fixtures",
                    str(source / "collector/windows/tests/frozen_guided_fixture.py")))
    with (output / "freeze.log").open("w", encoding="utf-8") as log:
        subprocess.run(command, check=True, stdout=log, stderr=subprocess.STDOUT,
                       env=clean_child_environment(), timeout=360)
    if source_manifest(source) != manifest:
        raise RuntimeError("Source changed while freezing the guided fixture")
    executable = output / "dist" / NAME / (NAME + ".exe")
    package = executable.parent
    inventory = [{"path": item.relative_to(package).as_posix(), "bytes": item.stat().st_size, "sha256": sha256(item)}
                 for item in sorted(package.rglob("*")) if item.is_file()]
    executable_hash = sha256(executable)
    with (output / "render.log").open("w", encoding="utf-8") as log:
        # The 180-case matrix exceeds seven minutes while still advancing;
        # this suite deadline is separate from the renderer's latency guards.
        subprocess.run([str(executable), "--render", "--output", str(output / "render")], check=True,
                       stdout=log, stderr=subprocess.STDOUT, env=clean_child_environment(), timeout=600)
    receipt = json.loads((output / "render/render-guided-receipt.json").read_text(encoding="utf-8"))
    if (receipt["guiSourceSha256"] != manifest["guiSourceSha256"]
            or receipt["frozenFixture"]["executableSha256"] != executable_hash):
        raise RuntimeError("Frozen render receipt does not identify the sealed fixture")
    if inventory != [{"path": item.relative_to(package).as_posix(), "bytes": item.stat().st_size, "sha256": sha256(item)}
                     for item in sorted(package.rglob("*")) if item.is_file()]:
        raise RuntimeError("Frozen fixture package changed while rendering")
    build_receipt = {"status": "isolated-frozen-guided-ui-qualified", "manifest": manifest,
                     "executableSha256": executable_hash, "packageFiles": inventory,
                     "renderReceiptSha256": sha256(output / "render/render-guided-receipt.json"),
                     "normalInstalledExecutableProved": False, "customerPhoneOrAccountProved": False}
    (output / "frozen-fixture-receipt.json").write_text(json.dumps(build_receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": build_receipt["status"], "executableSha256": executable_hash,
                      "guiSourceSha256": manifest["guiSourceSha256"]}))


def main():
    if (sys.platform != "win32" or os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("RUNNER_OS") != "Windows"
            or sys.version_info[:3] != (3, 12, 10)):
        raise SystemExit("Frozen UI qualification requires the exact disposable Windows runner runtime")
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--render", action="store_true")
    args = parser.parse_args()
    if args.render:
        render_frozen(args.output.resolve())
    elif args.source is not None and not getattr(sys, "frozen", False):
        build_and_render(args.source.resolve(), args.output.resolve())
    else:
        parser.error("Use source Python with --source, or the frozen fixture with --render")


if __name__ == "__main__":
    main()
