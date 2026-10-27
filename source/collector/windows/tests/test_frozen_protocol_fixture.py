"""Local integration checks for the isolated Windows frozen protocol harness."""
from __future__ import annotations

import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from amplifai_phone import agent
from frozen_protocol_fixture import (CASES, PRIVATE_ERROR, check_case, code_hash, commands,
                                     fictional_capture)


class FrozenProtocolHarnessTest(unittest.TestCase):
    def test_frozen_code_identity_ignores_only_relocated_filename(self):
        source = "def boundary(value):\n    return value + 1\n"
        first = compile(source, "source/module.py", "exec", dont_inherit=True, optimize=0)
        frozen = compile(source, "relocated/module.py", "exec", dont_inherit=True, optimize=0)
        changed = compile(source.replace("+ 1", "+ 2"), "relocated/module.py", "exec", dont_inherit=True, optimize=0)
        self.assertEqual(code_hash(first), code_hash(frozen))
        self.assertNotEqual(code_hash(first), code_hash(changed))

    def test_real_agent_disk_backed_cases_have_required_before_and_after(self):
        for case in CASES:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                raw = io.BytesIO()
                sink = io.TextIOWrapper(raw, encoding="cp1252", errors="strict", write_through=True)
                source = io.TextIOWrapper(io.BytesIO(commands(case)), encoding="cp1252", errors="strict")
                state = {"passwordRoundTrip": False}
                configure = agent.configure_protocol_streams
                if case.startswith("legacy-"):
                    configure = lambda source, sink: None
                with patch.object(agent, "configure_protocol_streams", configure):
                    result = agent.run_connect(source, sink, collector=fictional_capture(
                        case, Path(temporary) / "sessions", state))
                proof = {"ownedCleanup": "workspacePath" not in state or not state["workspacePath"].exists(),
                         "passwordRoundTrip": state["passwordRoundTrip"],
                         "forcedInitialEncoding": "cp1252", "finalStdinEncoding": source.encoding,
                         "finalStdoutEncoding": sink.encoding}
                checked = check_case(case, subprocess.CompletedProcess([], result, raw.getvalue(), b""), proof)
                self.assertTrue(checked["ownedCleanup"])
                self.assertNotIn(PRIVATE_ERROR, json.dumps(checked))
                source.close()
                sink.close()

    def test_case_validator_rejects_wrong_return_and_private_diagnostics(self):
        proof = {"ownedCleanup": True}
        with self.assertRaisesRegex(AssertionError, "private value"):
            check_case("private-error", subprocess.CompletedProcess([], 1, PRIVATE_ERROR.encode(), b""), proof)
        with self.assertRaisesRegex(AssertionError, "stderr"):
            check_case("private-error", subprocess.CompletedProcess([], 1, b"", b"unbounded exception"), proof)


if __name__ == "__main__":
    unittest.main()
