"""Release isolation checks independent of a phone or signing identity."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("intel_builder", Path(__file__).with_name("build_macos_intel_candidate.py"))
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class CandidateIsolationTests(unittest.TestCase):
    def test_arm_update_feed_and_identity_are_not_inherited(self):
        original = {"SUFeedURL": "https://example.invalid/arm.xml", "SUEnableAutomaticChecks": True,
                    "CFBundleIdentifier": "original.production", "CFBundleExecutable": "AmplifaiPhone"}
        info = builder.candidate_plist(original)
        self.assertNotIn("SUFeedURL", info)
        self.assertFalse(info["SUEnableAutomaticChecks"])
        self.assertFalse(info["SUAutomaticallyUpdate"])
        self.assertFalse(info["SUAllowsAutomaticUpdates"])
        self.assertNotEqual(info["CFBundleIdentifier"], original["CFBundleIdentifier"])
        self.assertEqual(info["CFBundleExecutable"], "AmplifaiPhone")
        self.assertEqual(info["LSMinimumSystemVersion"], "14.0")
        self.assertTrue(original["SUEnableAutomaticChecks"])
        self.assertIn("SUFeedURL", original)


if __name__ == "__main__":
    unittest.main()
