"""Run parser, capacity and optional large-wire regressions from selected PYZ code.

The production helper has no arbitrary input/test mode. Extracted engine bytecode
runs in the matching audit interpreter; its dependencies come from that exact
locked audit venv. This proves the selected binary's compiled engine seam, not a
phone acquisition, encrypted device, GUI, browser transport or account save.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.abc
import importlib.util
import io
import json
import marshal
import runpy
import sys
import types
import unittest
from pathlib import Path

from PyInstaller.archive.readers import CArchiveReader

PREFIX = "amplifai_phone"
REQUIRED = (PREFIX, PREFIX + ".metadata", PREFIX + ".ios_backup",
            PREFIX + ".__main__", PREFIX + ".agent", PREFIX + ".bridge",
            PREFIX + ".workspace")
CAPACITY_REQUIRED = (PREFIX + ".backup_files", PREFIX + ".backup_stream",
                     PREFIX + ".backup_watchdog", PREFIX + ".windows_storage")
CAPACITY_TESTS = ("test_backup_capacity", "test_backup_stream",
                  "test_backup_watchdog", "test_backup_files", "test_workspace")


class SelectedHelperCode(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def __init__(self, helper: Path, required: tuple[str, ...]):
        archive = CArchiveReader(str(helper))
        self.pyz = archive.open_embedded_archive("PYZ.pyz")
        self.loaded: dict[str, str] = {}
        if any(name not in self.pyz.toc for name in required):
            raise ValueError("Selected helper lacks a required compiled engine module")

    def find_spec(self, fullname: str, path=None, target=None):
        if fullname != PREFIX and not fullname.startswith(PREFIX + "."):
            return None
        if fullname not in self.pyz.toc:
            raise ImportError("Compiled engine module absent; readable source fallback forbidden")
        kind = self.pyz.toc[fullname][0]
        if kind not in (0, 1):
            raise ImportError("Expected compiled engine module or package")
        return importlib.util.spec_from_loader(fullname, self, is_package=kind == 1)

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        code = self.pyz.extract(module.__name__)
        if not isinstance(code, types.CodeType):
            raise TypeError("Expected selected helper bytecode")
        module.__file__ = "selected-helper-pyz:" + module.__name__
        self.loaded[module.__name__] = hashlib.sha256(marshal.dumps(code)).hexdigest()
        exec(code, module.__dict__)  # noqa: S102 - selected owned helper code, never source fallback


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--helper", type=Path, required=True)
    parser.add_argument("--expected-helper-sha256", required=True)
    parser.add_argument("--tests", type=Path, required=True)
    parser.add_argument("--capacity-tests", action="store_true",
                        help="also run the bounded capacity regressions against selected PYZ code")
    parser.add_argument("--large-probe-receipt", type=Path,
                        help="run the >1 GiB synthetic fixed stream through selected PYZ code")
    args = parser.parse_args()
    helper_sha256 = hashlib.sha256(args.helper.read_bytes()).hexdigest()
    if helper_sha256 != args.expected_helper_sha256:
        raise ValueError("Selected helper bytes do not match the expected artifact hash")
    if any(name == PREFIX or name.startswith(PREFIX + ".") for name in sys.modules):
        raise ValueError("Use a fresh interpreter; existing engine imports would invalidate the proof")
    required = REQUIRED + CAPACITY_REQUIRED if args.capacity_tests or args.large_probe_receipt else REQUIRED
    reader = SelectedHelperCode(args.helper, required)
    sys.meta_path.insert(0, reader)
    sys.path.insert(0, str(args.tests.resolve(strict=True)))
    test_modules = ("test_nonfinite_duration_pipeline",)
    if args.capacity_tests:
        test_modules += CAPACITY_TESTS
    suite = unittest.defaultTestLoader.loadTestsFromNames(test_modules)
    output = io.StringIO()
    result = unittest.TextTestRunner(stream=output, verbosity=0).run(suite)
    ready = result.wasSuccessful() and (result.testsRun > 4 if args.capacity_tests else result.testsRun == 4)
    ready = ready and all(name in reader.loaded for name in required)
    large_probe = None
    if ready and args.large_probe_receipt:
        original_argv = sys.argv
        try:
            sys.argv = [str(args.tests / "large_backup_probe.py"), "--receipt",
                        str(args.large_probe_receipt), "--skip-legacy"]
            runpy.run_path(sys.argv[0], run_name="__main__")
        finally:
            sys.argv = original_argv
        fixed = json.loads(args.large_probe_receipt.read_text())["fixed"]
        large_probe = {"receipt": str(args.large_probe_receipt),
                       "wire_payload_bytes": fixed["wire_payload_bytes"],
                       "retained_logical_bytes": fixed["retained_logical_bytes"],
                       "max_requested_receive_bytes": fixed["max_requested_receive_bytes"]}
        ready = (fixed["wire_payload_bytes"] > 1024**3
                 and fixed["retained_logical_bytes"] > 1024**3
                 and fixed["max_requested_receive_bytes"] <= 128 * 1024
                 and all(name in reader.loaded for name in required))
    receipt = {"kind": "selected-helper-compiled-regression",
               "helper_sha256": helper_sha256,
               "tests": result.testsRun, "failures": len(result.failures),
               "errors": len(result.errors), "test_modules": test_modules,
               "compiled_modules": reader.loaded,
               "nonfinite_overflow_observed": any("OverflowError: cannot convert float infinity" in details
                                                  for _test, details in result.errors),
               "readable_engine_fallback": False,
               "audit_dependency_runtime": "matching locked CPython venv; not frozen loader proof",
               "large_probe": large_probe, "ready": ready}
    print(json.dumps(receipt, sort_keys=True))
    return 0 if receipt["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
