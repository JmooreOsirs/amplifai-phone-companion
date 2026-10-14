"""Focused custody regressions for exact-helper reuse; no app or signing tools run."""
import io
from pathlib import Path
import plistlib
import stat
import tarfile
import tempfile
import unittest
import zipfile

import refresh_macos_intel_wrapper as refresh


class WrapperRefreshCustodyTests(unittest.TestCase):
    def test_external_zip_digest_and_all_qualified_assets_are_required(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            names = [refresh.ZIP_NAME, refresh.SOURCE_NAME, "candidate-receipt.json",
                     "architecture-receipt.json", "status-receipt.json", "frozen-worker-receipt.json",
                     "frozen-metadata-receipt.json", "dependency-receipt.json"]
            for name in names:
                (root / name).write_bytes(name.encode())
            (root / "SHA256SUMS").write_text("".join(
                f"{refresh.sha256(root / name)}  ./{name}\n" for name in names))
            digest = refresh.sha256(root / refresh.ZIP_NAME)
            self.assertEqual(len(refresh.checksum_assets(root, digest)), len(names))
            with self.assertRaisesRegex(ValueError, "independently verified"):
                refresh.checksum_assets(root, "0" * 64)
            (root / "frozen-metadata-receipt.json").write_text("tampered")
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                refresh.checksum_assets(root, digest)

    def test_baseline_receipt_binds_actual_helper_and_intel_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            app = Path(temporary) / refresh.APP_NAME
            helper = app / "Contents/Resources/helper/amplifai-agent"
            helper.parent.mkdir(parents=True)
            helper.write_bytes(b"qualified Intel fixture bytes")
            original = {"SUPublicEDKey": "vtvaNfb6+dVMP4tz6PeLMXTeVQH7lK7o1zzy4WeqUGs=",
                        "SURequireSignedFeed": True, "SUVerifyUpdateBeforeExtraction": True}
            info = refresh.candidate_plist(original, enable_published_updates=True)
            (app / "Contents/Info.plist").write_bytes(plistlib.dumps(info))
            receipt = {"kind": "macos-intel-candidate", "architecture": "x86_64",
                       "git_commit": refresh.BASELINE_COMMIT, "build": "261014251",
                       "version": "2026.10.14-rc25-intel1", "updater_enabled": True,
                       "bundle_id": "ai.satoris.amplifai.phone.intel-candidate",
                       "helper_sha256": refresh.sha256(helper)}
            self.assertEqual(refresh.validate_baseline(receipt, app), info)
            with self.assertRaisesRegex(ValueError, "fixed qualified"):
                refresh.validate_baseline({**receipt, "git_commit": "1" * 40}, app)
            helper.write_bytes(b"ARM or changed helper")
            with self.assertRaisesRegex(ValueError, "helper does not match"):
                refresh.validate_baseline(receipt, app)

    def test_zip_and_source_reject_escaping_links_and_traversal(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "app.zip"
            link = zipfile.ZipInfo(refresh.APP_NAME + "/Contents/Frameworks/Current")
            link.external_attr = (stat.S_IFLNK | 0o755) << 16
            with zipfile.ZipFile(archive, "w") as stream:
                stream.writestr(link, "Versions/A")
            refresh.validate_zip(archive)
            with zipfile.ZipFile(archive, "w") as stream:
                stream.writestr(link, "../../../../outside")
            with self.assertRaisesRegex(ValueError, "Unsafe candidate ZIP symlink"):
                refresh.validate_zip(archive)
            source = root / "source.tar.gz"
            with tarfile.open(source, "w:gz") as stream:
                item = tarfile.TarInfo("intel-source/intel-python-source-inputs/../../escape")
                item.size = 1
                stream.addfile(item, io.BytesIO(b"x"))
            with self.assertRaisesRegex(ValueError, "Unsafe baseline"):
                refresh.extract_source_inputs(source, root / "extracted")
            self.assertFalse((root / "escape").exists())

    def test_preserved_tree_records_modes_bytes_and_links(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            helper = root / "amplifai-agent"
            helper.write_bytes(b"fixture")
            (root / "alias").symlink_to("amplifai-agent")
            baseline = refresh.tree_manifest(root)
            helper.chmod(helper.stat().st_mode ^ 0o100)
            self.assertNotEqual(refresh.tree_manifest(root), baseline)
            (root / "alias").unlink()
            (root / "alias").symlink_to(root.parent)
            with self.assertRaisesRegex(ValueError, "escaping"):
                refresh.tree_manifest(root)

    def test_source_reuse_keeps_exact_inputs_and_excludes_old_authored_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "source.tar.gz"
            with tarfile.open(archive, "w:gz") as stream:
                for name in (*refresh.SOURCE_DIRS, "source"):
                    item = tarfile.TarInfo(f"intel-source/{name}/marker")
                    item.size = 5
                    stream.addfile(item, io.BytesIO(b"exact"))
            refresh.extract_source_inputs(archive, root / "out")
            for name in refresh.SOURCE_DIRS:
                self.assertEqual((root / "out/intel-source" / name / "marker").read_bytes(), b"exact")
            self.assertFalse((root / "out/intel-source/source").exists())


if __name__ == "__main__":
    unittest.main()
