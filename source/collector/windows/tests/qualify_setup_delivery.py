"""Actual Windows IPC qualification, synthetic data, no authority or collector imports."""
import argparse
import json
import platform
import sys
import unittest
from pathlib import Path

from test_setup_delivery import WindowsPipeRuntimeTests


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("This qualification requires actual Windows")
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(WindowsPipeRuntimeTests))
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "setup-delivery-receipt.json").write_text(json.dumps({
        "schema": 1, "status": "passed" if result.wasSuccessful() else "failed",
        "tests": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
        "python": platform.python_version(), "platform": platform.platform(), "syntheticOnly": True,
        "authorityTransportCreated": False, "collectorCreated": False,
        "scenarios": ["protected-logon-dacl", "primary-exclusion", "plain-open", "delivery", "duplicate", "empty-root-path-delivery",
                      "busy", "ui-timeout", "late-delivery-denial", "idle-cancellation", "primary-release"]
    }, indent=2) + "\n", encoding="utf-8")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
