#!/usr/bin/env python3
"""Synthetic audit regressions; generated binaries are inspected, never run."""

from __future__ import annotations

import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from verify_macos_architecture import HELPER, audit, loader_path, parse_load_commands, is_system


TOOLS_AVAILABLE = sys.platform == "darwin" and all(
    Path(path).exists() for path in ("/usr/bin/clang", "/usr/bin/lipo", "/usr/bin/otool")
)


@unittest.skipUnless(TOOLS_AVAILABLE, "Real Mach-O fixture tests require Apple's macOS developer tools")
class BundleArchitectureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures = tempfile.TemporaryDirectory(prefix="amplifai-macho-fixtures-")
        cls.fixture_root = Path(cls.fixtures.name)
        cls.main_c = cls.fixture_root / "main.c"
        cls.main_c.write_text("int main(void) { return 0; }\n")
        cls.lib_c = cls.fixture_root / "lib.c"
        cls.lib_c.write_text("int amplifai_fixture(void) { return 1; }\n")
        for arch, minimum in (("x86_64", "14.0"), ("arm64", "14.0"), ("x86_64", "15.0")):
            cls.compile(cls.fixture_root / f"main-{arch}-{minimum}", arch=arch, minimum=minimum)

    @classmethod
    def tearDownClass(cls):
        cls.fixtures.cleanup()

    @classmethod
    def compile(cls, output, *, arch="x86_64", minimum="14.0", dylib=False, flags=()):
        output.parent.mkdir(parents=True, exist_ok=True)
        args = ["/usr/bin/clang", "-target", f"{arch}-apple-macos{minimum}",
                str(cls.lib_c if dylib else cls.main_c), "-o", str(output)]
        if dylib:
            args.append("-dynamiclib")
        subprocess.run([*args, *flags], check=True, capture_output=True, text=True)
        return output

    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory(prefix="amplifai-macho-test-")
        self.root = Path(self.workspace.name)
        self.app = self.root / "AmplifaiPhone.app"
        self.main = self.app / "Contents/MacOS/AmplifaiPhone"
        self.helper = self.app / HELPER
        self.main.parent.mkdir(parents=True)
        self.helper.parent.mkdir(parents=True)
        (self.app / "Contents/Info.plist").write_bytes(plistlib.dumps({
            "CFBundleExecutable": "AmplifaiPhone", "LSMinimumSystemVersion": "14.0",
        }))
        self.copy_fixture(self.main)
        self.copy_fixture(self.helper)

    def tearDown(self):
        self.workspace.cleanup()

    def copy_fixture(self, destination, arch="x86_64", minimum="14.0"):
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.fixture_root / f"main-{arch}-{minimum}", destination)

    def check(self, arch="x86_64"):
        return audit(self.app, arch, "14.0")

    def assert_fails(self, expected, arch="x86_64"):
        receipt = self.check(arch)
        self.assertFalse(receipt["passed"], receipt)
        self.assertTrue(any(expected in error for error in receipt["errors"]), receipt["errors"])
        return receipt

    def test_valid_x86_main_helper_and_nested_extension(self):
        extension = self.helper.parent / "_internal/cryptography/fixture.so"
        self.compile(extension, dylib=True)
        receipt = self.check()
        self.assertTrue(receipt["passed"], receipt["errors"])
        self.assertEqual(receipt["mach_o_count"], 3)
        self.assertEqual(len(receipt["native_manifest_sha256"]), 64)
        self.assertTrue(all(len(item["sha256"]) == 64 for item in receipt["files"]))

    def test_nested_arm_only_extension_is_rejected(self):
        extension = self.helper.parent / "_internal/cryptography/fixture.so"
        self.compile(extension, dylib=True, arch="arm64")
        self.assert_fails("Missing x86_64 slice")

    def test_newer_minimum_os_in_nested_binary_is_rejected(self):
        self.copy_fixture(self.helper, minimum="15.0")
        self.assert_fails("Requires macOS 15.0")

    def test_info_plist_newer_minimum_is_rejected(self):
        info = self.app / "Contents/Info.plist"
        value = plistlib.loads(info.read_bytes())
        value["LSMinimumSystemVersion"] = "15.0"
        info.write_bytes(plistlib.dumps(value))
        self.assert_fails("Info.plist minimum")

    def test_missing_helper_is_rejected(self):
        self.helper.unlink()
        self.assert_fails("Missing executable frozen helper")

    def test_shell_main_does_not_count_as_native_app(self):
        self.main.write_text("#!/bin/sh\nexit 0\n")
        self.assert_fails("main app is not a valid")

    def test_zero_native_binaries_is_rejected(self):
        self.main.write_text("#!/bin/sh\nexit 0\n")
        self.helper.write_text("#!/bin/sh\nexit 0\n")
        self.assert_fails("No Mach-O files found")

    def test_non_executable_main_is_rejected(self):
        self.main.chmod(0o600)
        self.assert_fails("Missing executable main app")

    def test_external_symlink_is_rejected(self):
        (self.app / "Contents/Resources/Applications").symlink_to("/Applications")
        self.assert_fails("Unsafe or broken symlink")

    def test_broken_symlink_is_rejected(self):
        (self.app / "Contents/Resources/broken").symlink_to("does-not-exist")
        self.assert_fails("Unsafe or broken symlink")

    def test_internal_framework_symlink_is_allowed(self):
        framework = self.app / "Contents/Frameworks/Fixture.framework"
        binary = framework / "Versions/A/Fixture"
        self.compile(binary, dylib=True)
        (framework / "Versions/Current").symlink_to("A")
        (framework / "Fixture").symlink_to("Versions/Current/Fixture")
        receipt = self.check()
        self.assertTrue(receipt["passed"], receipt["errors"])
        self.assertEqual(len(receipt["symlinks"]), 2)

    def test_external_homebrew_dependency_is_rejected(self):
        subprocess.run(["/usr/bin/install_name_tool", "-change", "/usr/lib/libSystem.B.dylib",
                        "/usr/local/opt/openssl/lib/libcrypto.3.dylib", str(self.helper)],
                       check=True, capture_output=True)
        self.assert_fails("Unresolved or external dependency /usr/local/")

    def test_apple_webkit_install_name_is_allowed(self):
        self.compile(self.main, flags=("-framework", "WebKit"))
        receipt = self.check()
        self.assertTrue(receipt["passed"], receipt["errors"])

    def test_escape_disguised_as_loader_relative_dependency_is_rejected(self):
        outside = self.root / "outside.dylib"
        self.compile(outside, dylib=True)
        subprocess.run(["/usr/bin/install_name_tool", "-change", "/usr/lib/libSystem.B.dylib",
                        "@loader_path/../../../../outside.dylib", str(self.helper)],
                       check=True, capture_output=True)
        self.assert_fails("Unresolved or external dependency @loader_path/")

    def test_nested_sparkle_style_executable_uses_own_runpaths(self):
        nested = self.app / "Contents/Frameworks/Sparkle.framework/Versions/B/Resources/Updater.app/Contents"
        library = nested / "Frameworks/libfixture.dylib"
        self.compile(library, dylib=True, flags=("-Wl,-install_name,@rpath/libfixture.dylib",))
        self.compile(nested / "MacOS/Updater", flags=(str(library), "-Wl,-rpath,@executable_path/../Frameworks"))
        receipt = self.check()
        self.assertTrue(receipt["passed"], receipt["errors"])
        library.unlink()
        self.assert_fails("Unresolved or external dependency @rpath/libfixture.dylib")

    def test_python_dynamic_extension_inherits_helper_runpath(self):
        internal = self.helper.parent / "_internal"
        library = internal / "libfixture.dylib"
        self.compile(library, dylib=True, flags=("-Wl,-install_name,@rpath/libfixture.dylib",))
        self.compile(internal / "package/extension.so", dylib=True, flags=(str(library),))
        self.compile(self.helper, flags=("-Wl,-rpath,@executable_path/_internal",))
        receipt = self.check()
        self.assertTrue(receipt["passed"], receipt["errors"])

    def test_unused_external_runpath_is_rejected(self):
        self.compile(self.helper, flags=("-Wl,-rpath,/usr/local/opt/openssl/lib",))
        self.assert_fails("Unsafe run path")

    def test_universal_main_and_helper_pass_both_architectures(self):
        for path in (self.main, self.helper):
            subprocess.run(["/usr/bin/lipo", "-create",
                            str(self.fixture_root / "main-x86_64-14.0"),
                            str(self.fixture_root / "main-arm64-14.0"), "-output", str(path)],
                           check=True, capture_output=True)
        for arch in ("x86_64", "arm64"):
            receipt = self.check(arch)
            self.assertTrue(receipt["passed"], receipt["errors"])
            self.assertTrue(all(set(item["architectures"]) == {"x86_64", "arm64"}
                                for item in receipt["files"]))

    def test_cli_writes_failure_receipt_and_returns_nonzero(self):
        self.helper.unlink()
        output = self.root / "receipt.json"
        result = subprocess.run([sys.executable, str(Path(__file__).with_name("verify_macos_architecture.py")),
                                 str(self.app), "--arch", "x86_64", "--minimum-macos", "14.0",
                                 "--output", str(output)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertFalse(json.loads(output.read_text())["passed"])


class LoadCommandParserTests(unittest.TestCase):
    HEADER = """Mach header
      magic  cputype cpusubtype  caps    filetype ncmds sizeofcmds flags
 0xfeedfacf 16777223          3  0x00           2    20       1712 0x00200085
"""

    def test_legacy_macos_command(self):
        details = parse_load_commands(self.HEADER + """Load command 0
      cmd LC_VERSION_MIN_MACOSX
  cmdsize 16
  version 10.13
      sdk 15.5
""")
        self.assertEqual(details["minimum_macos"], "10.13")
        self.assertEqual(details["filetype"], 2)

    def test_ios_platform_is_not_macos(self):
        with self.assertRaisesRegex(ValueError, "Non-macOS"):
            parse_load_commands(self.HEADER + """Load command 0
      cmd LC_BUILD_VERSION
  cmdsize 32
 platform 2
    minos 14.0
      sdk 18.0
""")

    def test_no_minimum_version_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Missing macOS"):
            parse_load_commands(self.HEADER)

    def test_system_install_name_does_not_follow_host_cryptex(self):
        install_name = Path("/System/Library/Frameworks/WebKit.framework/Versions/A/WebKit")
        with patch.object(Path, "resolve", side_effect=AssertionError("Must preserve system install name")):
            self.assertEqual(loader_path(install_name), install_name)

    def test_dotdot_cannot_disguise_external_path_as_system_library(self):
        self.assertFalse(is_system(loader_path(Path("/System/Library/../../usr/local/lib/untrusted.dylib"))))


if __name__ == "__main__":
    unittest.main(verbosity=2)
