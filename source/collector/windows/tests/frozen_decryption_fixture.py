"""Non-shipped qualification of real encrypted-backup parsing through helper IPC.

Only physical backup acquisition is replaced: the fixture creates a wholly
fictional encrypted backup, then uses the unchanged production parser, record
store, protocol, review and cleanup. No decryption function is mocked. The
separate frozen launcher must match the ordinary helper's production code.
It is never an input to the consumer MSI and never opens a device or account.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import marshal
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import types

from frozen_protocol_fixture import code_hash, sha256, verify_frozen_modules


NAME = "AmplifaiDecryptionQualification"
MODULES = {
    "agent_entry": "collector/agent_entry.py",
    **{f"amplifai_phone.{name}": f"collector/amplifai_phone/{name}.py" for name in (
        "agent", "helper_protocol", "capture_runtime", "backup_files", "ios_backup", "backup_stream", "backup_watchdog",
        "metadata", "record_store", "workspace", "source_diagnostics", "call_accounting", "__main__",
    )},
}
SAME_BACKUP_CASES = (
    "same-backup-wrong-correct", "same-backup-max-attempts", "same-backup-cancel",
    "same-backup-keybag-corrupt", "same-backup-manifest-invalid",
)
CASES = (
    "valid", "unicode", "combining", "spaces", "maximum-password",
    "wrong_password", "keybag_corrupt",
    "manifest_key", "manifest_short_key", "manifest_missing_class", "manifest_truncated_cipher",
    "contacts_key", "contacts_short_key", "contacts_missing_class", "contacts_wal_key", "contacts_shm_key",
    *(f"{source}_{failure}" for source in ("calls", "messages")
      for failure in ("key", "short_key", "missing_class", "padding", "wal_key", "shm_key")),
    "both_optional_keys",
    *SAME_BACKUP_CASES,
)
PASSWORDS = {
    "unicode": "Fictional café 漢字 🔐",
    "combining": "Fictional cafe\u0301",
    "spaces": " fictional leading and trailing ",
    "maximum-password": "x" * 512,
}
DEFAULT_PASSWORD = "Fictional decryption only! 42"
WRONG_PASSWORD = "Wrong fictional password"
ROOT_MARKER_BYTES = b"AMPLIFAI_PHONE_SESSIONS_V1\n"


def password_for(case: str) -> str:
    return PASSWORDS.get(case, DEFAULT_PASSWORD)


def expected_error(case: str) -> str | None:
    if case in {"wrong_password", "keybag_corrupt", "same-backup-max-attempts", "same-backup-keybag-corrupt"}:
        return "backup_password"
    if case.startswith("manifest_") or case == "same-backup-manifest-invalid":
        return "backup_control_invalid"
    if case.startswith("contacts_"):
        return "selected_payload_crypto"
    return None


def unavailable_sources(case: str) -> list[str]:
    return (["calls", "messages"] if case == "both_optional_keys" else
            [case.split("_", 1)[0]] if case.startswith(("calls_", "messages_")) else [])


def commands(case: str) -> bytes:
    """Use the wrapper's real password writer; subprocess transport is an OS pipe."""
    from windows.session import Session

    session = Session()
    session.begin("inspect")
    session.handle({"kind": "residue", "sessions": []})
    session.exited(0)
    session.approve(True)
    session.begin("connect")
    stream = io.StringIO()
    if case in SAME_BACKUP_CASES:
        for attempt, password in enumerate(same_backup_passwords(case), 1):
            session.handle({"kind": "state", "state": "password_required", "attempt": attempt,
                            "maxAttempts": 3, **({"reason": "backup_unlock_failed"} if attempt > 1 else {})})
            session.write_password(password, stream)
        if case in {"same-backup-max-attempts", "same-backup-manifest-invalid"}:
            # Deliberate extra input must remain unread after the bounded/fatal failure.
            stream.write(json.dumps({"action": "password", "value": DEFAULT_PASSWORD}) + "\n")
        if case == "same-backup-cancel":
            stream.write('{"action":"disconnect"}\n')
            return stream.getvalue().encode("utf-8")
    else:
        session.handle({"kind": "state", "state": "password_required"})
        session.write_password(WRONG_PASSWORD if case == "wrong_password" else password_for(case), stream)
    for frame in ({"action": "review", "ids": [1]}, {"action": "disconnect"}):
        stream.write(json.dumps(frame, ensure_ascii=False) + "\n")
    return stream.getvalue().encode("utf-8")


def same_backup_passwords(case: str) -> list[str]:
    return {
        "same-backup-wrong-correct": [WRONG_PASSWORD, DEFAULT_PASSWORD],
        "same-backup-max-attempts": [WRONG_PASSWORD] * 3,
        "same-backup-cancel": [WRONG_PASSWORD],
        "same-backup-keybag-corrupt": [DEFAULT_PASSWORD] * 3,
        "same-backup-manifest-invalid": [DEFAULT_PASSWORD],
    }[case]


def fictional_live_acquisition(case: str, sessions_root: Path, state: dict):
    """Keep production collection/retry orchestration; replace USB acquisition only."""
    from unittest.mock import patch
    from amplifai_phone.ios_backup import parse_selected_backup
    from synthetic_encrypted_backup_fixture import acquisition

    variant = {"same-backup-keybag-corrupt": "keybag_corrupt",
               "same-backup-manifest-invalid": "manifest_key"}.get(case, "valid")
    state.update(acquisitionCount=0, parseAttempts=0, passwordPrompts=0,
                 sameCompletedBackup=True, passwordUnchanged=True)
    expected_passwords = same_backup_passwords(case)

    async def collect(**options):
        provider = options["password_provider"]

        def password_provider():
            index = state["passwordPrompts"]
            state["passwordPrompts"] += 1
            supplied = provider()
            state["passwordPipeConsumed"] = True
            state["passwordUnchanged"] &= (index < len(expected_passwords) and supplied == expected_passwords[index])
            return supplied

        def parse(*args, **kwargs):
            state["parseAttempts"] += 1
            path = args[0]
            if "backupPath" in state:
                state["sameCompletedBackup"] &= path == state["backupPath"]
            else:
                state["backupPath"] = path
            state["paths"].append(path.parent)
            return parse_selected_backup(*args, **kwargs)

        options["password_provider"] = password_provider
        with acquisition(sessions_root, variant, DEFAULT_PASSWORD) as (collector, service), patch(
            "amplifai_phone.ios_backup.parse_selected_backup", new=parse
        ):
            try:
                state["capture"] = await collector(**options)
                return state["capture"]
            finally:
                state["acquisitionCount"] = service.backup.await_count
    return collect


def fictional_acquisition(case: str, sessions_root: Path, state: dict):
    from dataclasses import replace
    from datetime import UTC, datetime

    from amplifai_phone.ios_backup import parse_selected_backup
    from amplifai_phone.record_store import RecordStore
    from amplifai_phone.workspace import SessionWorkspace
    from synthetic_encrypted_backup_fixture import fixture

    async def collect(**options):
        raw = SessionWorkspace(sessions_root)
        with raw as root:
            state["paths"].append(root)
            backup = root / "fictional-backup"
            fixture(backup, password_for(case), case)
            supplied = options["password_provider"]()
            state["passwordPipeConsumed"] = True
            state["passwordUnchanged"] = supplied == (
                WRONG_PASSWORD if case == "wrong_password" else password_for(case))
            review = SessionWorkspace(sessions_root)
            review_root = review.__enter__()
            state["paths"].append(review_root)
            store = None
            try:
                store = RecordStore(review_root / "sanitized.sqlite3", review)
                parsed = parse_selected_backup(
                    backup, supplied, now=datetime(2026, 11, 6, tzinfo=UTC),
                    workspace=raw, bound_callback=raw.check_bound,
                    transfer_callback=options["transfer_callback"],
                    payload_diagnostic_callback=options["payload_diagnostic_callback"],
                    record_store=store,
                )
                capture = replace(parsed, review_workspace=review, record_store=store)
                state["capture"] = capture
                return capture
            except BaseException:
                if store is not None:
                    store.close()
                review.__exit__(None, None, None)
                raise
    return collect


def clean_sessions(root: Path) -> bool:
    from amplifai_phone.workspace import ROOT_MARKER

    return (root.is_dir() and not root.is_symlink()
            and set(root.iterdir()) == {root / ROOT_MARKER}
            and (root / ROOT_MARKER).read_bytes() == ROOT_MARKER_BYTES)


def run_case(case: str, proof: Path, sessions_root: Path, manifest: dict | None = None) -> int:
    import agent_entry
    from amplifai_phone import agent

    modules = verify_frozen_modules(manifest) if manifest is not None else []
    for stream in (sys.stdin, sys.stdout):
        stream.reconfigure(encoding="cp1252", errors="strict")
    state = {"paths": [], "passwordPipeConsumed": False, "passwordUnchanged": False}
    original = agent.run_connect

    def injected(source, sink, **kwargs):
        from amplifai_phone.call_accounting import CallAccountingHistory
        from amplifai_phone.source_diagnostics import DiagnosticHistory

        # The ordinary entry creates default history objects. Confine their
        # actual storage to this fixture's root before they can perform I/O.
        kwargs["diagnostic_history"] = DiagnosticHistory(sessions_root)
        kwargs["call_accounting_history"] = CallAccountingHistory(sessions_root)
        collector = (fictional_live_acquisition(case, sessions_root, state) if case in SAME_BACKUP_CASES
                     else fictional_acquisition(case, sessions_root, state))
        result = original(source, sink, collector=collector, **kwargs)
        if case in {"same-backup-max-attempts", "same-backup-manifest-invalid"}:
            state["unusedPasswordNotRead"] = DEFAULT_PASSWORD in source.read()
        return result

    agent.run_connect = injected
    original_argv = sys.argv
    sys.argv = [sys.argv[0], "connect", *(["--retry-backup-unlock"] if case in SAME_BACKUP_CASES else [])]
    try:
        result = agent_entry.main()
    finally:
        agent.run_connect = original
        sys.argv = original_argv
        cleaned = all(not path.exists() for path in state["paths"]) and clean_sessions(sessions_root)
        # On a failed harness, remove only the fixture's marked capture using
        # its own production owner; preserve the failure in ownedCleanup.
        capture = state.get("capture")
        if not cleaned and capture is not None:
            capture.close()
        proof.write_text(json.dumps({
            "case": case, "sourceOnly": manifest is None,
            "passwordPipeConsumed": state["passwordPipeConsumed"],
            "passwordUnchanged": state["passwordUnchanged"], "ownedCleanup": cleaned,
            "forcedInitialEncoding": "cp1252", "finalStdinEncoding": sys.stdin.encoding,
            "finalStdoutEncoding": sys.stdout.encoding, "productionModules": modules,
            **{key: state[key] for key in ("acquisitionCount", "parseAttempts", "passwordPrompts",
                                          "sameCompletedBackup", "unusedPasswordNotRead") if key in state},
        }, sort_keys=True), encoding="utf-8")
    return result


def check_case(case: str, result: subprocess.CompletedProcess, proof: dict) -> dict:
    if result.stderr:
        raise AssertionError("Decryption fixture wrote unexpected stderr")
    for private in {*PASSWORDS.values(), DEFAULT_PASSWORD, WRONG_PASSWORD}:
        if private.encode("utf-8") in result.stdout:
            raise AssertionError("Decryption protocol exposed a fictional password")
    packets = [json.loads(line) for line in result.stdout.decode("utf-8").splitlines()]
    errors = [packet for packet in packets if packet.get("kind") == "error"]
    captures = [packet for packet in packets if packet.get("kind") == "capture"]
    reviews = [packet for packet in packets if packet.get("kind") == "review"]
    code = expected_error(case)
    expected_exit = 130 if case == "same-backup-cancel" else 1 if code else 0
    if result.returncode != expected_exit:
        raise AssertionError("Decryption fixture exit differs from expected")
    if not all(proof[key] for key in ("passwordPipeConsumed", "passwordUnchanged", "ownedCleanup")):
        raise AssertionError("Password transmission or owned workspace cleanup failed")
    if proof["finalStdinEncoding"] != "utf-8" or proof["finalStdoutEncoding"] != "utf-8":
        raise AssertionError("Production entry did not enforce strict UTF-8 pipes")
    recovery = {}
    if case in SAME_BACKUP_CASES:
        passwords = same_backup_passwords(case)
        prompt_count = len(passwords) + int(case == "same-backup-cancel")
        recovery = {"acquisitionCount": 1, "parseAttempts": len(passwords),
                    "passwordPrompts": prompt_count, "sameCompletedBackup": True}
        if any(proof.get(key) != value for key, value in recovery.items()):
            raise AssertionError("Recovery did not use bounded attempts against one completed backup")
        prompts = [packet for packet in packets if packet.get("state") == "password_required"]
        expected_prompts = [{"kind": "state", "state": "password_required", "attempt": attempt,
                             "maxAttempts": 3, **({"reason": "backup_unlock_failed"} if attempt > 1 else {})}
                            for attempt in range(1, prompt_count + 1)]
        if prompts != expected_prompts:
            raise AssertionError("Recovery prompt sequence changed")
        if case in {"same-backup-max-attempts", "same-backup-manifest-invalid"} and not proof.get("unusedPasswordNotRead"):
            raise AssertionError("Recovery consumed a password after its terminal boundary")
    if case == "same-backup-cancel":
        if errors or captures or reviews or packets[-1] != {"kind": "state", "state": "cancelled"}:
            raise AssertionError("Retry cancellation did not stop without partial capture")
        return {"case": case, "exitCode": 130, "capturePackets": 0,
                "passwordPipeUnchanged": True, "ownedCleanup": True, **recovery}
    if code:
        if errors != [{"kind": "error", "code": code, "stage": "processing"}] or captures or reviews:
            raise AssertionError("Required decryption failure did not remain fatal and stage-bound")
        return {"case": case, "exitCode": 1, "error": errors[0], "capturePackets": 0,
                "passwordPipeUnchanged": True, "ownedCleanup": True, **recovery}
    missing = unavailable_sources(case)
    reasons = {source: "selected_payload_crypto" for source in missing}
    if errors or len(captures) != 1 or len(reviews) != 1:
        raise AssertionError("Valid remaining data did not reach one local review")
    capture, review = captures[0], reviews[0]
    expected = {"totalContacts": 1, "availableCalls": int("calls" not in missing),
                "availableMessages": int("messages" not in missing), "missing": missing,
                "unavailableReasons": reasons, "backupEncrypted": True}
    if any(capture.get(key) != value for key, value in expected.items()):
        raise AssertionError("Capture lost explicit optional unavailability or valid source counts")
    for source in missing:
        component = ("wal" if case.endswith("_wal_key") else
                     "shm" if case.endswith("_shm_key") else "database")
        if not any(packet.get("kind") == "selected-payload-diagnostic"
                   and packet.get("source") == source and packet.get("reason") == "selected_payload_crypto"
                   and packet.get("component") == component
                   and packet.get("result") in {"copy_failed", "unavailable"} for packet in packets):
            raise AssertionError("Optional decryption failure lacks unavailable diagnostic")
    if (review.get("missing_sources") != missing or review.get("selected_contacts") != 1
            or review.get("matched_calls") != int("calls" not in missing)
            or review.get("matched_messages") != int("messages" not in missing)):
        raise AssertionError("Local selection review lost optional unavailability")
    return {"case": case, "exitCode": 0, "capturePackets": 1, "reviewPackets": 1,
            "missingSources": missing, "unavailableReasons": reasons,
            "passwordPipeUnchanged": True, "ownedCleanup": True, **recovery}


def production_manifest(source: Path, helper: Path) -> dict:
    from PyInstaller.archive.readers import CArchiveReader

    archive = CArchiveReader(str(helper))
    pyz_names = [name for name, record in archive.toc.items() if record[-1] == "z"]
    if len(pyz_names) != 1:
        raise RuntimeError("Packaged helper must contain exactly one Python archive")
    pyz = archive.open_embedded_archive(pyz_names[0])
    records = []
    for module, relative in MODULES.items():
        contents = (source / relative).read_bytes()
        source_code = compile(contents, relative, "exec", dont_inherit=True, optimize=0)
        frozen = (marshal.loads(archive.extract("agent_entry")) if module == "agent_entry"
                  else pyz.extract(module))
        if not isinstance(frozen, types.CodeType) or code_hash(frozen) != code_hash(source_code):
            raise RuntimeError("Packaged decryption module differs from source: " + module)
        records.append({"module": module, "path": relative,
                        "sourceSha256": hashlib.sha256(contents).hexdigest(),
                        "codeSha256": code_hash(source_code), "packagedHelperCodeMatchesSource": True})
    return {"schema": 1, "sourceCommit": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=source, text=True).strip(),
        "python": platform.python_version(), "packagedHelperSha256": sha256(helper),
        "productionModules": records}


def run_matrix(executable: list[str], environment: dict) -> list[dict]:
    cases = []
    for case in CASES:
        with tempfile.TemporaryDirectory(prefix="fictional-decryption-") as temporary:
            directory = Path(temporary)
            proof = directory / "proof.json"
            result = subprocess.run([*executable, "--case", case, "--proof", str(proof),
                                     "--sessions-root", str(directory / "sessions")],
                                    input=commands(case), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    env=environment, timeout=60, check=False)
            record = json.loads(proof.read_text(encoding="utf-8"))
            if cases and record["productionModules"] != cases[0]["productionModules"]:
                raise RuntimeError("Frozen production custody changed across cases")
            cases.append({**check_case(case, result, record), "productionModules": record["productionModules"]})
    # Same process launcher and sessions root, new local collection after a
    # fatal password error. There is no reuse of decrypted files or authority.
    with tempfile.TemporaryDirectory(prefix="fictional-decryption-retry-") as temporary:
        directory = Path(temporary)
        for index, case in enumerate(("wrong_password", "valid")):
            proof = directory / f"retry-{index}.json"
            result = subprocess.run([*executable, "--case", case, "--proof", str(proof),
                                     "--sessions-root", str(directory / "sessions")],
                                    input=commands(case), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    env=environment, timeout=60, check=False)
            record = json.loads(proof.read_text(encoding="utf-8"))
            checked = check_case(case, result, record)
            if record["productionModules"] != cases[0]["productionModules"]:
                raise RuntimeError("Retry production custody changed")
            cases.append({**checked, "case": "retry-" + case, "productionModules": record["productionModules"]})
    return cases


def build_and_qualify(source: Path, helper: Path, output: Path) -> dict:
    from build_windows_candidate import clean_child_environment

    if output.exists():
        raise RuntimeError("Decryption output must be new and runner-owned")
    output.mkdir(parents=True)
    manifest = production_manifest(source, helper)
    manifest_path = output / "decryption-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--console",
               "--noupx", "--contents-directory", "_internal", "--name", NAME, "--optimize", "0",
               "--paths", str(source / "collector"), "--paths", str(source / "collector/tests"),
               "--paths", str(source / "collector/windows"), "--paths", str(source / "collector/windows/tests"),
               "--distpath", str(output / "dist"), "--workpath", str(output / "work"), "--specpath", str(output / "spec")]
    for module in MODULES:
        command.extend(("--hidden-import", module))
    command.extend(("--add-data", str(manifest_path) + os.pathsep + ".", str(Path(__file__).resolve())))
    with (output / "freeze.log").open("w", encoding="utf-8") as log:
        subprocess.run(command, check=True, stdout=log, stderr=subprocess.STDOUT,
                       env=clean_child_environment(), timeout=360)
    if production_manifest(source, helper) != manifest:
        raise RuntimeError("Helper/source changed during decryption qualification freeze")
    executable = output / "dist" / NAME / (NAME + ".exe")
    environment = clean_child_environment()
    environment.update({"PYTHONIOENCODING": "cp1252", "PYTHONUTF8": "0"})
    cases = run_matrix([str(executable)], environment)
    if production_manifest(source, helper) != manifest:
        raise RuntimeError("Helper/source changed during decryption qualification")
    receipt = {"status": "pass", "schema": 1, "sourceCommit": manifest["sourceCommit"],
               "packagedHelperSha256": manifest["packagedHelperSha256"],
               "fixtureExecutableSha256": sha256(executable), "productionModules": cases[0]["productionModules"],
               "cases": [{key: value for key, value in record.items() if key != "productionModules"} for record in cases],
               "fictionalOnly": True, "normalInstalledExecutableProved": False,
               "physicalPhoneOrAccountProved": False,
               "boundary": "Non-shipped frozen launcher replaces physical acquisition only; production backup parsing/decryption and helper protocol code match the packaged helper."}
    (output / "frozen-decryption-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--helper", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--case", choices=CASES)
    parser.add_argument("--proof", type=Path)
    parser.add_argument("--sessions-root", type=Path)
    parser.add_argument("--source-case", action="store_true", help="Developer source-only IPC test; never frozen proof")
    args = parser.parse_args()
    frozen = getattr(sys, "frozen", False)
    if args.case and args.proof and args.sessions_root and args.source_case and not frozen:
        return run_case(args.case, args.proof.resolve(), args.sessions_root.resolve())
    if (sys.platform != "win32" or os.environ.get("GITHUB_ACTIONS") != "true"
            or os.environ.get("RUNNER_OS") != "Windows" or sys.version_info[:3] != (3, 12, 10)):
        raise RuntimeError("Frozen decryption qualification requires the exact disposable Windows runner")
    if frozen and args.case and args.proof and args.sessions_root and not args.source_case:
        manifest = json.loads((Path(sys._MEIPASS) / "decryption-manifest.json").read_text(encoding="utf-8"))
        return run_case(args.case, args.proof.resolve(), args.sessions_root.resolve(), manifest)
    if not frozen and args.source and args.helper and args.output:
        receipt = build_and_qualify(args.source.resolve(), args.helper.resolve(strict=True), args.output.resolve())
        print(json.dumps({"status": receipt["status"], "cases": len(receipt["cases"]), "fictionalOnly": True}))
        return 0
    parser.error("Use builder inputs or the separate fixture case launcher")


if __name__ == "__main__":
    raise SystemExit(main())
