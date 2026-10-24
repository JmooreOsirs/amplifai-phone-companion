"""Synthetic fixture production must not leak a key or accept the real publisher."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import android_build
import android_release


class UpdaterFixtureTests(unittest.TestCase):
    def exercise(self, *, signing_fails=False, publisher=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            manifest = project / "app/src/androidTest/fixtures/wrong-signer/AndroidManifest.xml"
            manifest.parent.mkdir(parents=True)
            manifest.write_text("synthetic manifest")
            scratch = root / "keys"
            clean = {"PATH": "/synthetic/bin", "ANDROID_HOME": "/synthetic/sdk", "JAVA_HOME": "/synthetic/java"}

            def run(args, **kwargs):
                self.assertEqual(kwargs["env"], clean)
                if "keytool" in str(args[0]):
                    (scratch / "synthetic-only.p12").write_bytes(b"synthetic private key")
                if "sign" in args:
                    if signing_fails:
                        raise subprocess.CalledProcessError(1, args)
                    Path(args[args.index("--out") + 1]).write_bytes(b"synthetic APK")

            output = "Number of signers: 1\n" + (android_release.CERTIFICATE if publisher else "test certificate")
            with patch.object(android_build.release, "download", side_effect=lambda _url, path, *_args: path.write_bytes(b"x" * 108317)), \
                    patch.object(android_build.subprocess, "run", side_effect=run), \
                    patch.object(android_build.subprocess, "check_output", return_value=output):
                error = subprocess.CalledProcessError if signing_fails else ValueError
                with self.assertRaises(error):
                    android_build.updater_fixtures(project, scratch, clean)
            self.assertFalse(scratch.exists(), "test private key survived failed fixture preparation")

    def test_failed_signing_removes_synthetic_key(self):
        self.exercise(signing_fails=True)

    def test_publisher_certificate_is_rejected_and_key_removed(self):
        self.exercise(publisher=True)


if __name__ == "__main__":
    unittest.main()
