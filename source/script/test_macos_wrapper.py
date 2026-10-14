"""Real Mach-O regression for the SwiftPM developer-framework run path."""
from pathlib import Path
import platform
import subprocess
import tempfile
import unittest

from normalize_macos_wrapper import normalize
from verify_macos_architecture import parse_load_commands, run_tool


@unittest.skipUnless(platform.system() == "Darwin", "Mach-O normalization requires macOS developer tools")
class WrapperRunPathTests(unittest.TestCase):
    def test_only_active_developer_run_path_is_removed(self):
        developer = run_tool(["/usr/bin/xcode-select", "-p"]).strip()
        forbidden = developer + "/Library/Developer/Frameworks"
        with tempfile.TemporaryDirectory(prefix="amplifai-wrapper-rpath-") as directory:
            root = Path(directory)
            source = root / "main.c"
            source.write_text("int main(void) { return 0; }\n")
            binary = root / "Fixture"
            subprocess.run(["/usr/bin/clang", "-mmacosx-version-min=14.0", str(source), "-o", str(binary),
                "-Wl,-rpath," + forbidden, "-Wl,-rpath,@executable_path/../Frameworks"], check=True)
            before = parse_load_commands(run_tool(["/usr/bin/otool", "-h", "-l", str(binary)]))
            self.assertIn(forbidden, before["rpaths"])
            receipt = normalize(binary, platform.machine())
            self.assertEqual(receipt["removedDeveloperRunPaths"], [forbidden])
            self.assertEqual(receipt["remainingRunPaths"], ["@executable_path/../Frameworks"])
            self.assertEqual(normalize(binary, platform.machine())["removedDeveloperRunPaths"], [])
            subprocess.run(["/usr/bin/xcrun", "install_name_tool", "-add_rpath", "/private/tmp/foreign-frameworks", str(binary)], check=True)
            with self.assertRaisesRegex(ValueError, "Unexpected external"):
                normalize(binary, platform.machine())


if __name__ == "__main__":
    unittest.main()
