"""Qualify the ARM64 helper's frozen unlock code with fictional data and OS pipes.

Run with the portable build's hash-synced Python 3.12.12 environment. The
separate fixture launcher is never placed in the companion app.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import selectors
import subprocess
import sys
import tempfile
import time
from unittest.mock import AsyncMock, patch

COLLECTOR = Path(__file__).resolve().parents[1]
SOURCE = COLLECTOR.parent
NAME = "AmplifaiMacUnlockQualification"
MANIFEST = "macos-unlock-manifest.json"
PASSWORD = "  Fictional cafe\u0301 漢字 🔐  "
WRONG = "Fictional incorrect input"
UNUSED = "Fictional unread terminal input"
# variant, passwords supplied, terminal error, explicit cancel
CASES = {
    "wrong-then-correct": ("valid", (WRONG, PASSWORD), None, False),
    "attempt-limit": ("valid", (WRONG,) * 3, "backup_password", False),
    "corrupt-second-class": ("keybag_second_class_corrupt", (PASSWORD,) * 3, "backup_password", False),
    "manifest-key-failure": ("manifest_key", (PASSWORD,), "backup_control_invalid", False),
    "retry-eof": ("valid", (WRONG,), None, False),
    "retry-cancel": ("valid", (WRONG,), None, True),
}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


# Reuse the existing test-only custody tools. No Windows build or session code runs.
if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(COLLECTOR / "windows/tests"))
    sys.path.insert(0, str(COLLECTOR))
from frozen_decryption_fixture import clean_sessions, production_manifest
from frozen_protocol_fixture import sha256, verify_frozen_modules


def frame(**fields):
    return (json.dumps(fields, ensure_ascii=False) + "\n").encode("utf-8")


def fixture_case(case, directory, manifest):
    import agent_entry
    from amplifai_phone import agent, ios_backup
    from amplifai_phone.call_accounting import CallAccountingHistory
    from amplifai_phone.source_diagnostics import DiagnosticHistory
    from synthetic_encrypted_backup_fixture import acquisition

    modules = verify_frozen_modules(manifest) if manifest else []
    root = directory / "sessions"
    variant, passwords, error, _ = CASES[case]
    state = {"passwordReads": 0, "unchanged": True, "unreadTerminalInput": False}
    paths = []
    for stream in (sys.stdin, sys.stdout):
        stream.reconfigure(encoding="cp1252", errors="strict")

    original_connect = agent.run_connect
    original_parse = ios_backup.parse_selected_backup
    from pymobiledevice3 import lockdown

    def observe_parse(backup, *args, **kwargs):
        paths.append(backup)
        return original_parse(backup, *args, **kwargs)

    with acquisition(root, variant, PASSWORD) as (collect, service):
        # The synthetic lockdown reports the setting expected by the Mac preflight.
        lockdown.create_using_usbmux.return_value.get_value = AsyncMock(return_value=True)

        async def capture(**options):
            provider = options["password_provider"]

            def password_provider():
                value = provider()
                index = state["passwordReads"]
                state["passwordReads"] += 1
                state["unchanged"] &= index < len(passwords) and value == passwords[index]
                return value

            return await collect(**{**options, "password_provider": password_provider})

        def connect(source, sink, **options):
            result = original_connect(source, sink, collector=capture, **{
                **options, "diagnostic_history": DiagnosticHistory(root),
                "call_accounting_history": CallAccountingHistory(root),
            })
            if error:
                state["unreadTerminalInput"] = source.read() == frame(action="password", value=UNUSED).decode()
            return result

        with patch.object(agent, "run_connect", new=connect), patch.object(
            ios_backup, "parse_selected_backup", new=observe_parse
        ), patch.object(sys, "argv", [sys.argv[0], "connect", "--retry-backup-unlock",
                                    "--require-encrypted-backup"]):
            status = agent_entry.main()
        acquisitions = service.backup.await_count
    proof = {**state, "acquisitions": acquisitions, "parseAttempts": len(paths),
             "sameCompletedBackup": len(set(paths)) == 1,
             "ownedCleanup": clean_sessions(root) and all(not path.exists() for path in paths),
             "strictUTF8": all(stream.encoding == "utf-8" and stream.errors == "strict"
                               for stream in (sys.stdin, sys.stdout)),
             "productionModules": modules}
    (directory / "proof.json").write_text(json.dumps(proof), encoding="utf-8")
    return status


def environment(directory):
    return {"HOME": str(directory), "TMPDIR": str(directory), "PATH": "/usr/bin:/bin",
            "LANG": "en_US.UTF-8", "PYTHONNOUSERSITE": "1"}


def check_case(case, result, directory):
    require(not result.stderr, f"{case}: unexpected fixture stderr")
    require((directory / "proof.json").is_file(), f"{case}: fixture proof missing")
    artifacts = [result.stdout, (directory / "proof.json").read_bytes()]
    diagnostics = directory / "diagnostics"
    if diagnostics.exists():
        artifacts.extend(path.read_bytes() for path in diagnostics.iterdir() if path.is_file())
    for private in (PASSWORD, WRONG, UNUSED, str(directory)):
        escaped = json.dumps(private, ensure_ascii=True)[1:-1]
        require(all(value.encode() not in blob for value in (private, escaped) for blob in artifacts),
                f"{case}: private input escaped its boundary")
    proof = json.loads(artifacts[1])
    _, passwords, error, cancel = CASES[case]
    cancelled = cancel or case == "retry-eof"
    expected_exit = 130 if cancelled else 1 if error else 0
    require(result.returncode == expected_exit, f"{case}: wrong exit code")
    require(proof["acquisitions"] == 1 and proof["parseAttempts"] == len(passwords)
            and proof["passwordReads"] == len(passwords)
            and all(proof[field] for field in ("unchanged", "sameCompletedBackup", "ownedCleanup", "strictUTF8"))
            and (not error or proof["unreadTerminalInput"]),
            f"{case}: backup, password, or cleanup proof failed")
    packets = [json.loads(line) for line in result.stdout.splitlines()]
    prompts = [packet for packet in packets if packet.get("state") == "password_required"]
    require(prompts == [
        {"kind": "state", "state": "password_required", "attempt": attempt, "maxAttempts": 3,
         **({"reason": "backup_unlock_failed"} if attempt > 1 else {})}
        for attempt in range(1, len(passwords) + int(cancelled) + 1)
    ], f"{case}: unexpected password prompt sequence")
    captures, reviews, failures = (
        [packet for packet in packets if packet.get("kind") == kind]
        for kind in ("capture", "review", "error")
    )
    if cancelled or error:
        require(not captures and not reviews, f"{case}: terminal failure released records")
        terminal = ({"kind": "state", "state": "cancelled"} if cancelled else
                    {"kind": "error", "stage": "processing", "code": error})
        require(packets[-1] == terminal and failures == ([terminal] if error else []),
                f"{case}: wrong terminal event")
    else:
        require(not failures and len(captures) == len(reviews) == 1
                and all(captures[0].get(field) == 1 for field in ("totalContacts", "availableCalls", "availableMessages"))
                and captures[0].get("backupEncrypted") is True
                and all(reviews[0].get(field) == 1 for field in ("selected_contacts", "matched_calls", "matched_messages")),
                f"{case}: unlock did not produce the expected local review")
    return {"case": case, "exitCode": expected_exit, "capturePackets": len(captures),
            "reviewPackets": len(reviews), "promptCount": len(prompts), "errorCode": error, **proof}


def exchange(launcher, case, directory):
    _, passwords, error, cancel = CASES[case]
    command = [*launcher, "--fixture-case", case, "--fixture-root", str(directory)]
    deadline = time.monotonic() + 60
    stdout, stderr, pending = bytearray(), bytearray(), bytearray()
    sent = 0
    with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, bufsize=0, cwd=directory,
                          env=environment(directory)) as child, selectors.DefaultSelector() as selector:
        try:
            selector.register(child.stdout, selectors.EVENT_READ, stdout)
            selector.register(child.stderr, selectors.EVENT_READ, stderr)
            while selector.get_map():
                ready = selector.select(max(0, deadline - time.monotonic()))
                require(ready and time.monotonic() < deadline, f"{case}: pipe exchange timed out")
                for key, _ in ready:
                    chunk = os.read(key.fd, 8192)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    key.data.extend(chunk)
                    require(len(key.data) <= 1024 * 1024, f"{case}: unexpected fixture output volume")
                    if key.fileobj is child.stderr:
                        continue
                    pending.extend(chunk)
                    while b"\n" in pending:
                        end = pending.index(b"\n")
                        packet = json.loads(pending[:end])
                        del pending[:end + 1]
                        if packet.get("state") == "password_required":
                            require(packet.get("attempt") == sent + 1 and not child.stdin.closed,
                                    f"{case}: replayed or out-of-order password prompt")
                            if sent < len(passwords):
                                child.stdin.write(frame(action="password", value=passwords[sent]))
                                sent += 1
                                if error and sent == len(passwords):
                                    # The terminal frame must leave this extra password unread.
                                    child.stdin.write(frame(action="password", value=UNUSED))
                                    child.stdin.close()
                            else:
                                require(case in {"retry-cancel", "retry-eof"}, f"{case}: extra password prompt")
                                if cancel:
                                    child.stdin.write(frame(action="disconnect"))
                                child.stdin.close()
                        elif packet.get("kind") == "capture":
                            child.stdin.write(frame(action="review", ids=[1]))
                        elif packet.get("kind") == "review":
                            child.stdin.write(frame(action="disconnect"))
                            child.stdin.close()
            require(not pending, f"{case}: incomplete fixture frame")
            status = child.wait(timeout=max(0, deadline - time.monotonic()))
        finally:
            if child.poll() is None:
                child.kill()
    return subprocess.CompletedProcess(command, status, bytes(stdout), bytes(stderr))


def matrix(launcher):
    records = []
    for case in CASES:
        with tempfile.TemporaryDirectory(prefix="amplifai-fictional-unlock-") as temporary:
            directory = Path(temporary).resolve()
            records.append(check_case(case, exchange(launcher, case, directory), directory))
    return records


def qualify(helper, expected_hash, output):
    require(sys.platform == "darwin" and platform.machine() == "arm64"
            and sys.version_info[:3] == (3, 12, 12) and sys.flags.optimize == 0,
            "Use the unoptimized pinned macOS ARM64 Python 3.12.12 build environment")
    lock = COLLECTOR / "portable-requirements.lock"
    inputs = json.loads((COLLECTOR / "macos-release-source-inputs.json").read_text())["channels"]["macos-arm64"]
    require(sha256(lock) == inputs["portableLockSha256"], "Portable lock hash differs from the release input")
    require(sha256(helper) == expected_hash, "Helper hash differs from the selected portable app")
    require(subprocess.check_output(["/usr/bin/lipo", "-archs", str(helper)], text=True).split() == ["arm64"],
            "Selected helper must be ARM64")
    manifest = production_manifest(SOURCE, helper)
    output.mkdir(mode=0o700)
    manifest_path = output / MANIFEST
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir",
               "--log-level", "WARN", "--target-arch", "arm64", "--name", NAME, "--optimize", "0",
               "--exclude-module", "PIL", "--paths", str(COLLECTOR), "--paths", str(COLLECTOR / "tests"),
               "--paths", str(COLLECTOR / "windows/tests"), "--distpath", str(output / "dist"),
               "--workpath", str(output / "work"), "--specpath", str(output / "spec")]
    for record in manifest["productionModules"]:
        command.extend(("--hidden-import", record["module"]))
    command.extend(("--add-data", f"{manifest_path}:.", str(Path(__file__).resolve())))
    with (output / "freeze.log").open("wb") as log:
        subprocess.run(command, cwd=output, env=environment(output), stdout=log,
                       stderr=subprocess.STDOUT, check=True, timeout=360)
    launcher = output / "dist" / NAME / NAME
    cases = matrix([str(launcher)])
    modules = cases[0]["productionModules"]
    require(len(modules) == len(manifest["productionModules"])
            and all(case.pop("productionModules") == modules for case in cases),
            "Frozen module custody differs between scenarios")
    require(production_manifest(SOURCE, helper) == manifest, "Helper or source changed during qualification")
    receipt = {**manifest, "schema": 1, "status": "pass", "architecture": "arm64",
               "portableLockSha256": sha256(lock), "fixtureExecutableSha256": sha256(launcher),
               "fixtureModules": modules, "cases": cases, "fictionalOnly": True,
               "unmodifiedInstalledExecutableProved": False, "physicalPhoneOrAccountProved": False,
               "boundary": "Non-shipped launcher with matched frozen production code, synthetic USB acquisition and private temporary diagnostics."}
    (output / "frozen-macos-unlock-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(f"Passed {len(cases)} frozen ARM64 unlock scenarios")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--helper", type=Path)
    parser.add_argument("--expected-helper-sha256")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--source-check", action="store_true")
    parser.add_argument("--fixture-case", choices=CASES, help=argparse.SUPPRESS)
    parser.add_argument("--fixture-root", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    frozen = getattr(sys, "frozen", False)
    if args.fixture_case and args.fixture_root and not (args.helper or args.output or args.source_check):
        directory = args.fixture_root.resolve(strict=True)
        require(directory == Path.cwd() and not any(directory.iterdir()), "Fixture needs a fresh owned directory")
        manifest = json.loads((Path(sys._MEIPASS) / MANIFEST).read_text()) if frozen else None
        return fixture_case(args.fixture_case, directory, manifest)
    if not frozen and args.source_check and not (args.helper or args.output or args.fixture_case):
        cases = matrix([sys.executable, str(Path(__file__).resolve())])
        print(f"Passed {len(cases)} source-only unlock scenarios")
        return 0
    if not frozen and args.helper and args.expected_helper_sha256 and args.output and not args.source_check:
        qualify(args.helper.resolve(strict=True), args.expected_helper_sha256, args.output.resolve())
        return 0
    parser.error("Use --source-check or supply --helper, --expected-helper-sha256, and a new --output")


if __name__ == "__main__":
    raise SystemExit(main())
