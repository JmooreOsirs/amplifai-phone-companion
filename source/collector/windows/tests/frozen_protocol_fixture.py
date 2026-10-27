"""Runner-only frozen helper protocol proof using fictional, disk-backed records.

The production executable has no synthetic collection entry. This separate
launcher substitutes only the collector, then calls the production entry/main
and run_connect with actual redirected stdin/stdout. Frozen production code is
matched against both source and the ordinary packaged helper's PYZ archive.
No launcher or fixture is added to the consumer MSI.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import marshal
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import types


NAME = "AmplifaiProtocolQualification"
MODULES = {
    "agent_entry": "collector/agent_entry.py",
    "amplifai_phone.agent": "collector/amplifai_phone/agent.py",
    "amplifai_phone.helper_protocol": "collector/amplifai_phone/helper_protocol.py",
    "amplifai_phone.capture_runtime": "collector/amplifai_phone/capture_runtime.py",
    "amplifai_phone.record_store": "collector/amplifai_phone/record_store.py",
    "amplifai_phone.ios_backup": "collector/amplifai_phone/ios_backup.py",
    "amplifai_phone.metadata": "collector/amplifai_phone/metadata.py",
    "amplifai_phone.__main__": "collector/amplifai_phone/__main__.py",
}
UNICODE_NAME = "Fictional \u6f14\u793a \U0001f4de"
UNICODE_QUERY = "\u6f14\u793a"
UNICODE_PASSWORD = "fictional-only-\u5bc6\u7801-\U0001f512"
ASCII_PASSWORD = "fictional-only-ascii"
PRIVATE_ERROR = "fictional-private-password-path-token-sentinel"
CONTACTS = 250
CASES = ("legacy-ascii", "legacy-unicode", "fixed-ascii", "fixed-unicode",
         "invalid-utf8", "private-error")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def code_record(code: types.CodeType) -> dict:
    """Ignore only the freezer's relocated filename; retain executable details."""
    def constant(value):
        if isinstance(value, types.CodeType):
            return {"code": code_record(value)}
        if isinstance(value, (tuple, frozenset)):
            values = [constant(item) for item in value]
            if isinstance(value, frozenset):
                values.sort(key=lambda item: json.dumps(item, sort_keys=True))
            return {type(value).__name__: values}
        if isinstance(value, bytes):
            return {"bytes": value.hex()}
        return {type(value).__name__: repr(value)}
    result = {key: getattr(code, key) for key in (
        "co_argcount", "co_posonlyargcount", "co_kwonlyargcount", "co_nlocals", "co_stacksize",
        "co_flags", "co_name", "co_qualname", "co_firstlineno", "co_names", "co_varnames",
        "co_freevars", "co_cellvars")}
    result.update({key: getattr(code, key).hex() for key in ("co_code", "co_linetable", "co_exceptiontable")})
    result["co_consts"] = [constant(value) for value in code.co_consts]
    return result


def code_hash(code: types.CodeType) -> str:
    return hashlib.sha256(json.dumps(code_record(code), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def production_manifest(source: Path, helper: Path) -> dict:
    from PyInstaller.archive.readers import CArchiveReader

    archive = CArchiveReader(str(helper))
    pyz_names = [name for name, record in archive.toc.items() if record[-1] == "z"]
    if len(pyz_names) != 1:
        raise RuntimeError("Packaged helper must contain exactly one Python module archive")
    pyz = archive.open_embedded_archive(pyz_names[0])
    records = []
    for module, relative in MODULES.items():
        contents = (source / relative).read_bytes()
        source_code = compile(contents, relative, "exec", dont_inherit=True, optimize=0)
        if module == "agent_entry":
            frozen_code = marshal.loads(archive.extract("agent_entry"))
        else:
            frozen_code = pyz.extract(module)
        if not isinstance(frozen_code, types.CodeType) or code_hash(frozen_code) != code_hash(source_code):
            raise RuntimeError("Packaged helper production module differs from exact source: " + module)
        records.append({"module": module, "path": relative,
                        "sourceSha256": hashlib.sha256(contents).hexdigest(),
                        "codeSha256": code_hash(source_code), "packagedHelperCodeMatchesSource": True})
    return {"schema": 1, "sourceCommit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=source, text=True).strip(),
            "python": platform.python_version(), "packagedHelperSha256": sha256(helper),
            "productionModules": records}


def verify_frozen_modules(manifest: dict) -> list[dict]:
    if not getattr(sys, "frozen", False) or sys.flags.optimize != 0:
        raise RuntimeError("Protocol proof requires the unoptimized frozen fixture")
    bundle = Path(sys._MEIPASS).resolve()
    if manifest["python"] != platform.python_version():
        raise RuntimeError("Source and frozen Python versions differ")
    records = []
    for expected in manifest["productionModules"]:
        module = importlib.import_module(expected["module"])
        if not Path(module.__file__).resolve().is_relative_to(bundle):
            raise RuntimeError("Fixture loaded a production module outside its frozen bundle")
        code = module.__loader__.get_code(expected["module"])
        if not isinstance(code, types.CodeType) or code_hash(code) != expected["codeSha256"]:
            raise RuntimeError("Fixture production code differs from packaged helper")
        records.append({"module": expected["module"], "codeSha256": expected["codeSha256"],
                        "fixtureCodeMatchesPackagedHelper": True})
    return records


def fictional_capture(case: str, sessions_root: Path, state: dict):
    """The only substituted boundary: no Apple service or account is invoked."""
    from amplifai_phone.ios_backup import IPhoneCapture
    from amplifai_phone.metadata import Contact, Interaction, SourceResult
    from amplifai_phone.record_store import RecordStore
    from amplifai_phone.workspace import SessionWorkspace

    async def collect(**options):
        password = options["password_provider"]()
        expected = UNICODE_PASSWORD if case == "fixed-unicode" else ASCII_PASSWORD
        if password != expected:
            raise RuntimeError("Fictional Unicode input did not round-trip")
        state["passwordRoundTrip"] = True
        if case == "private-error":
            raise RuntimeError(PRIVATE_ERROR)
        workspace = SessionWorkspace(sessions_root)
        workspace_path = workspace.__enter__()
        state["workspace"] = workspace
        state["workspacePath"] = workspace_path
        store = RecordStore(workspace_path / "fictional-review.sqlite3", workspace=workspace)
        state["store"] = store
        for index in range(CONTACTS):
            name = UNICODE_NAME if "unicode" in case and index == 0 else "Fictional Contact " + str(index)
            store.add("contacts", Contact(index + 1, name, ("+12025550123",), ()))
        store.add("calls", Interaction(1, "call", "call", "2026-10-01T00:00:00+00:00",
                                       "outgoing", ("+12025550123",), 60))
        store.add("messages", Interaction(1, "message", "sms", "2026-10-01T00:00:00+00:00",
                                          "incoming", ("+12025550123",), None))
        return IPhoneCapture(SourceResult(CONTACTS, store.collection("contacts"), 0),
                             SourceResult(1, store.collection("calls"), 0),
                             SourceResult(1, store.collection("messages"), 0),
                             "2026-10-01T00:00:00+00:00", "2001-01-01T00:00:00+00:00", (),
                             review_workspace=workspace, record_store=store, backup_encrypted=True)
    return collect


def run_frozen_case(case: str, proof: Path) -> int:
    import agent_entry
    from amplifai_phone import agent, helper_protocol

    manifest = json.loads((Path(sys._MEIPASS) / "protocol-manifest.json").read_text(encoding="utf-8"))
    modules = verify_frozen_modules(manifest)
    before = {"stdin": sys.stdin.encoding, "stdout": sys.stdout.encoding}
    # Both cases start with the concrete failing Windows boundary, regardless
    # of the disposable runner's machine-wide UTF-8 beta setting.
    for stream in (sys.stdin, sys.stdout):
        stream.reconfigure(encoding="cp1252", errors="strict")
    if case.startswith("legacy-"):
        def disabled(source, sink):
            return source, sink
        helper_protocol.configure_protocol_streams = disabled
        agent.configure_protocol_streams = disabled
        agent_entry.configure_protocol_streams = disabled
    state = {"passwordRoundTrip": False}
    real_connect = agent.run_connect
    def injected(source, sink, **kwargs):
        # Main/entry, the actual protocol streams and all result-building code
        # remain production code. Only the physical-device collector changes.
        return real_connect(source, sink, collector=fictional_capture(case, proof.parent / "sessions", state), **kwargs)
    agent.run_connect = injected
    sys.argv = [sys.argv[0], "connect"]
    try:
        result = agent_entry.main()
    finally:
        workspace = state.get("workspace")
        workspace_path = state.get("workspacePath")
        store = state.get("store")
        cleaned = workspace_path is None or not workspace_path.exists()
        if not cleaned:
            # Fixture-only fallback after an unexpected harness failure. This
            # marked runner-owned workspace contains no real phone input.
            if store is not None:
                store.close()
            workspace.__exit__(None, None, None)
        proof.write_text(json.dumps({"case": case, "inheritedEncoding": before,
                         "forcedInitialEncoding": "cp1252", "finalStdinEncoding": sys.stdin.encoding,
                         "finalStdoutEncoding": sys.stdout.encoding,
                         "passwordRoundTrip": state["passwordRoundTrip"], "ownedCleanup": cleaned,
                         "productionModules": modules}, sort_keys=True), encoding="utf-8")
    return result


def commands(case: str) -> bytes:
    password = UNICODE_PASSWORD if case == "fixed-unicode" else ASCII_PASSWORD
    frames = [{"action": "password", "value": password}]
    if case == "invalid-utf8":
        return b'{"action":"password","value":"\xff"}\n'
    if not case.startswith("legacy-"):
        frames.append({"action": "contacts", "query": UNICODE_QUERY if "unicode" in case else "Fictional", "cursor": 0})
        frames.append({"action": "review", "ids": [1]})
    frames.append({"action": "disconnect"})
    return ("\n".join(json.dumps(frame, ensure_ascii=False) for frame in frames) + "\n").encode("utf-8")


def check_case(case: str, result: subprocess.CompletedProcess, proof: dict) -> dict:
    # Raw private IPC is not printed or retained in the receipt/logs.
    if result.stderr:
        raise AssertionError("Frozen protocol fixture wrote unexpected stderr")
    if PRIVATE_ERROR.encode() in result.stdout or UNICODE_PASSWORD.encode() in result.stdout or ASCII_PASSWORD.encode() in result.stdout:
        raise AssertionError("Protocol diagnostics exposed a fictional private value")
    packets = [json.loads(line) for line in result.stdout.decode("utf-8").splitlines()]
    errors = [packet for packet in packets if packet["kind"] == "error"]
    captures = [packet for packet in packets if packet["kind"] == "capture"]
    expected_failure = case in {"legacy-unicode", "invalid-utf8", "private-error"}
    if result.returncode != (1 if expected_failure else 0) or not proof["ownedCleanup"]:
        raise AssertionError("Frozen protocol result or owned cleanup differs from expected")
    if case == "legacy-unicode":
        if captures or len(errors) != 1 or errors[0].get("code") != "collection_failed" or errors[0].get("stage") != "review":
            raise AssertionError("Frozen negative control did not reproduce the observed review failure")
        if errors[0].get("exceptionClass") != "unicode_encode" or errors[0].get("reviewStep") != "capture_packet":
            raise AssertionError("Frozen negative control did not fail at the Unicode packet boundary")
    elif expected_failure:
        if captures or len(errors) != 1 or errors[0].get("code") != "collection_failed":
            raise AssertionError("Frozen failure did not remain a safe bounded protocol event")
        expected_class = "unicode_decode" if case == "invalid-utf8" else "runtime"
        if errors[0].get("exceptionClass") != expected_class:
            raise AssertionError("Frozen failure lost its safe exception classification")
    else:
        if errors or len(captures) != 1 or captures[0]["totalContacts"] != CONTACTS or len(captures[0]["contacts"]) != 200:
            raise AssertionError("Frozen helper did not deliver the bounded disk-backed contact page")
        if case == "fixed-unicode" and captures[0]["contacts"][0]["name"] != UNICODE_NAME:
            raise AssertionError("Unicode first-page contact did not round-trip unchanged")
        if not case.startswith("legacy-"):
            pages = [packet for packet in packets if packet["kind"] == "contacts"]
            reviews = [packet for packet in packets if packet["kind"] == "review"]
            if len(pages) != 1 or len(reviews) != 1 or reviews[0]["selected_contacts"] != 1 or reviews[0]["matched_calls"] != 1 or reviews[0]["matched_messages"] != 1:
                raise AssertionError("Fictional query/selection review did not round-trip")
            if case == "fixed-unicode" and (len(pages[0]["contacts"]) != 1 or pages[0]["contacts"][0]["name"] != UNICODE_NAME):
                raise AssertionError("Unicode query did not return the unchanged fictional contact")
    if not case.startswith("legacy-") and (proof["finalStdinEncoding"] != "utf-8" or proof["finalStdoutEncoding"] != "utf-8"):
        raise AssertionError("Production entry did not override legacy protocol encodings")
    if case.startswith("legacy-") and (proof["finalStdinEncoding"] != "cp1252" or proof["finalStdoutEncoding"] != "cp1252"):
        raise AssertionError("Negative control did not retain the legacy protocol encodings")
    return {"case": case, "exitCode": result.returncode, "capturePackets": len(captures),
            "safeErrors": errors, "fictionalPasswordRoundTrip": proof["passwordRoundTrip"],
            "ownedCleanup": proof["ownedCleanup"], "forcedInitialEncoding": proof["forcedInitialEncoding"],
            "finalStdinEncoding": proof["finalStdinEncoding"], "finalStdoutEncoding": proof["finalStdoutEncoding"],
            "unicodeResultAndInputRoundTrip": case == "fixed-unicode"}


def build_and_qualify(source: Path, helper: Path, output: Path) -> dict:
    from build_windows_candidate import clean_child_environment

    if output.exists():
        raise RuntimeError("Protocol qualification output must be new and runner-owned")
    output.mkdir(parents=True)
    manifest = production_manifest(source, helper)
    manifest_path = output / "protocol-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--console",
               "--noupx", "--contents-directory", "_internal", "--name", NAME, "--optimize", "0",
               "--paths", str(source / "collector"), "--paths", str(source / "collector/windows"),
               "--distpath", str(output / "dist"), "--workpath", str(output / "work"), "--specpath", str(output / "spec")]
    for module in MODULES:
        command.extend(("--hidden-import", module))
    command.extend(("--add-data", str(manifest_path) + os.pathsep + ".", str(Path(__file__).resolve())))
    with (output / "freeze.log").open("w", encoding="utf-8") as log:
        subprocess.run(command, check=True, stdout=log, stderr=subprocess.STDOUT,
                       env=clean_child_environment(), timeout=360)
    if production_manifest(source, helper) != manifest:
        raise RuntimeError("Production helper or source changed during protocol qualification")
    executable = output / "dist" / NAME / (NAME + ".exe")
    # Exercise the ordinary, unmodified customer helper as well. Its public
    # runtime-check intentionally has no contact/device fixture input, so this
    # proves packaged entry/import readiness, not a consumer phone collection.
    with tempfile.TemporaryDirectory(prefix="packaged-helper-protocol-") as temporary:
        environment = clean_child_environment()
        environment.update({"PYTHONIOENCODING": "cp1252", "PYTHONUTF8": "0", "LOCALAPPDATA": temporary})
        result = subprocess.run([str(helper), "runtime-check"], input=b"", stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, cwd=helper.parent, env=environment,
                                timeout=45, check=False)
        if result.returncode != 0 or result.stderr or result.stdout.count(b"\n") != 1 or json.loads(result.stdout.decode("utf-8")) != {"kind": "runtime", "ready": True}:
            raise AssertionError("Ordinary packaged helper was not ready under legacy environment overrides")
        packaged_runtime = {"exitCode": 0, "runtimeReady": True,
                            "environment": {"PYTHONIOENCODING": "cp1252", "PYTHONUTF8": "0"},
                            "contactOrPasswordInputExercised": False}
    cases = []
    frozen_modules = None
    for case in CASES:
        with tempfile.TemporaryDirectory(prefix="fictional-protocol-") as temporary:
            directory = Path(temporary)
            proof_path = directory / "proof.json"
            environment = clean_child_environment()
            environment.update({"PYTHONIOENCODING": "cp1252", "PYTHONUTF8": "0", "LOCALAPPDATA": str(directory / "local")})
            result = subprocess.run([str(executable), "--case", case, "--proof", str(proof_path)],
                                    input=commands(case), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    cwd=executable.parent, env=environment, timeout=45, check=False)
            proof = json.loads(proof_path.read_text(encoding="utf-8"))
            if frozen_modules is not None and proof["productionModules"] != frozen_modules:
                raise AssertionError("Frozen production code custody changed across cases")
            frozen_modules = proof["productionModules"]
            cases.append(check_case(case, result, proof))
    if sha256(helper) != manifest["packagedHelperSha256"]:
        raise RuntimeError("Ordinary packaged helper changed during qualification")
    receipt = {"status": "pass", "schema": 1, "sourceCommit": manifest["sourceCommit"],
               "packagedHelperSha256": manifest["packagedHelperSha256"],
               "ordinaryPackagedHelperRuntimeCheck": packaged_runtime,
               "fixtureExecutableSha256": sha256(executable), "productionModules": frozen_modules,
               "cases": cases, "fictionalOnly": True, "normalInstalledExecutableProved": False,
               "physicalPhoneOrAccountProved": False,
               "boundary": "Separate non-shipped frozen launcher substitutes collector only; exact production protocol code matches the normal packaged helper archive."}
    (output / "frozen-protocol-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return receipt


def main() -> int:
    if (sys.platform != "win32" or os.environ.get("GITHUB_ACTIONS") != "true"
            or os.environ.get("RUNNER_OS") != "Windows" or sys.version_info[:3] != (3, 12, 10)):
        raise RuntimeError("Protocol qualification requires the exact disposable Windows runner")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--helper", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--case", choices=CASES)
    parser.add_argument("--proof", type=Path)
    args = parser.parse_args()
    if getattr(sys, "frozen", False) and args.case and args.proof:
        return run_frozen_case(args.case, args.proof.resolve())
    if not getattr(sys, "frozen", False) and args.source and args.helper and args.output:
        receipt = build_and_qualify(args.source.resolve(), args.helper.resolve(strict=True), args.output.resolve())
        print(json.dumps({"status": receipt["status"], "packagedHelperSha256": receipt["packagedHelperSha256"],
                          "cases": len(receipt["cases"]), "fictionalOnly": True}))
        return 0
    parser.error("Use source builder inputs or the separate frozen case launcher")


if __name__ == "__main__":
    raise SystemExit(main())
