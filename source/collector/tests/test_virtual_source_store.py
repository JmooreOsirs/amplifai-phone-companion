"""Offline DeviceLink file-operation contract for a future selected-source store."""

from __future__ import annotations

import hashlib
import tempfile
import tracemalloc
import unittest
from pathlib import Path

from amplifai_phone.virtual_source_store import (
    INPUT_CHUNK_BYTES,
    OBJECT_CHUNK_BYTES,
    SelectedSourceStore,
    VirtualStoreError,
)


class FileObjectBackend:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.max_put_bytes = 0
        self.fail_put = False
        self.fail_delete = False
        self.corrupt_read = False

    def put(self, key: str, payload: bytes) -> None:
        self.max_put_bytes = max(self.max_put_bytes, len(payload))
        if self.fail_put:
            raise OSError("synthetic backend failure")
        target = self.root / key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)

    def get(self, key: str) -> bytes:
        value = (self.root / key).read_bytes()
        return value[:-1] + b"?" if self.corrupt_read and value else value

    def delete(self, key: str) -> None:
        if self.fail_delete:
            raise OSError("synthetic cleanup failure")
        (self.root / key).unlink(missing_ok=True)

    def keys(self) -> set[str]:
        return {
            str(path.relative_to(self.root))
            for path in self.root.rglob("*")
            if path.is_file()
        }


class SelectedSourceStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="amplifai-virtual-store-")
        self.addCleanup(self.temporary.cleanup)
        self.backend = FileObjectBackend(Path(self.temporary.name))
        self.store = SelectedSourceStore(self.backend)

    def upload(self, path: str, payload: bytes, *, selected: bool = True) -> None:
        with self.store.open_write(path, selected=selected) as writer:
            for offset in range(0, len(payload), INPUT_CHUNK_BYTES):
                writer.write(payload[offset : offset + INPUT_CHUNK_BYTES])

    def test_selected_upload_readback_listing_and_discarded_placeholder(self) -> None:
        selected = b"synthetic selected source" * 400_000
        self.store.create_directory("Snapshot")
        self.upload("Snapshot/selected.db", selected)
        self.upload(
            "Snapshot/unrelated.bin", b"unrelated private bytes", selected=False
        )

        readback = tuple(self.store.read_chunks("Snapshot/selected.db"))
        self.assertEqual(b"".join(readback), selected)
        self.assertLessEqual(max(map(len, readback)), INPUT_CHUNK_BYTES)
        self.assertEqual(
            b"".join(self.store.read_chunks("Snapshot/unrelated.bin")), b""
        )
        listed = self.store.list_directory("Snapshot")
        self.assertEqual(listed["selected.db"]["DLFileType"], "DLFileTypeRegular")
        self.assertEqual(listed["selected.db"]["DLFileSize"], len(selected))
        self.assertEqual(listed["unrelated.bin"]["DLFileSize"], 0)
        self.assertEqual(len(self.backend.keys()), 2)
        self.assertLessEqual(self.backend.max_put_bytes, OBJECT_CHUNK_BYTES)
        self.assertEqual(
            self.store.file_sha256("Snapshot/selected.db"),
            hashlib.sha256(selected).hexdigest(),
        )

    def test_irregular_frames_never_cross_configured_capture_buffer(self) -> None:
        bound = 8 * INPUT_CHUNK_BYTES
        store = SelectedSourceStore(self.backend, object_chunk_bytes=bound)
        pieces = [b"a" * 17, b"b" * INPUT_CHUNK_BYTES] * 10
        with store.open_write("Snapshot/main.db", selected=True) as writer:
            for piece in pieces:
                writer.write(piece)
        self.assertEqual(
            b"".join(store.read_chunks("Snapshot/main.db")), b"".join(pieces)
        )
        self.assertLessEqual(store.max_buffered_bytes, bound)
        self.assertLessEqual(self.backend.max_put_bytes, bound)

    def test_directory_copy_move_remove_and_file_replacement(self) -> None:
        self.upload("Snapshot/selected.db", b"first")
        self.store.copy_item("Snapshot", "Copy")
        self.store.move_items({"Copy": "Moved"})
        self.assertEqual(
            b"".join(self.store.read_chunks("Moved/selected.db")), b"first"
        )
        self.upload("Snapshot/selected.db", b"second")
        self.assertEqual(
            b"".join(self.store.read_chunks("Moved/selected.db")), b"first"
        )
        self.assertEqual(
            b"".join(self.store.read_chunks("Snapshot/selected.db")), b"second"
        )
        self.store.remove_items(["Moved"])
        self.assertEqual(self.store.list_directory("Moved"), {})
        self.assertEqual(len(self.backend.keys()), 1)
        self.store.remove_items(["Snapshot"])
        self.assertEqual(self.backend.keys(), set())

    def test_failed_replacement_keeps_old_file_and_cleans_staging(self) -> None:
        self.upload("Snapshot/selected.db", b"original")
        original_keys = self.backend.keys()
        self.backend.fail_put = True
        with self.assertRaises(VirtualStoreError) as failure:
            self.upload("Snapshot/selected.db", b"replacement")
        self.assertEqual(failure.exception.code, "virtual_storage")
        self.assertEqual(
            b"".join(self.store.read_chunks("Snapshot/selected.db")), b"original"
        )
        self.assertEqual(self.backend.keys(), original_keys)

    def test_aborted_multichunk_replacement_keeps_original_and_removes_staged_part(
        self,
    ) -> None:
        self.upload("Snapshot/selected.db", b"original")
        original_keys = self.backend.keys()
        with (
            self.assertRaisesRegex(RuntimeError, "synthetic interruption"),
            self.store.open_write("Snapshot/selected.db", selected=True) as writer,
        ):
            block = b"a" * INPUT_CHUNK_BYTES
            for _ in range(OBJECT_CHUNK_BYTES // INPUT_CHUNK_BYTES):
                writer.write(block)
            raise RuntimeError("synthetic interruption")
        self.assertEqual(
            b"".join(self.store.read_chunks("Snapshot/selected.db")), b"original"
        )
        self.assertEqual(self.backend.keys(), original_keys)

    def test_corrupt_readback_never_commits_and_later_corruption_is_detected(
        self,
    ) -> None:
        self.backend.corrupt_read = True
        with self.assertRaises(VirtualStoreError) as failure:
            self.upload("Snapshot/selected.db", b"synthetic bytes")
        self.assertEqual(failure.exception.code, "virtual_integrity")
        self.assertEqual(self.store.list_directory("Snapshot"), {})
        self.assertEqual(self.backend.keys(), set())

        self.backend.corrupt_read = False
        self.upload("Snapshot/selected.db", b"synthetic bytes")
        self.backend.corrupt_read = True
        with self.assertRaises(VirtualStoreError) as failure:
            tuple(self.store.read_chunks("Snapshot/selected.db"))
        self.assertEqual(failure.exception.code, "virtual_integrity")

    def test_failed_cleanup_is_visible_and_retryable(self) -> None:
        self.upload("Snapshot/selected.db", b"old")
        self.backend.fail_delete = True
        with self.assertRaises(VirtualStoreError) as failure:
            self.upload("Snapshot/selected.db", b"new")
        self.assertEqual(failure.exception.code, "virtual_cleanup")
        self.assertEqual(
            b"".join(self.store.read_chunks("Snapshot/selected.db")), b"new"
        )
        self.assertEqual(self.store.pending_cleanup_count, 1)
        self.backend.fail_delete = False
        self.store.collect_garbage()
        self.assertEqual(self.store.pending_cleanup_count, 0)
        self.assertEqual(len(self.backend.keys()), 1)

    def test_paths_cannot_escape_or_replace_private_controls(self) -> None:
        for path in (
            "../outside",
            "/absolute",
            "Snapshot/../../outside",
            "a\\b",
            ".amplifai-owned-session.json",
            "pairing/secret",
            "bad\ud800name",
        ):
            with (
                self.subTest(path=path),
                self.assertRaises(VirtualStoreError) as failure,
            ):
                self.store.open_write(path, selected=True)
            self.assertEqual(failure.exception.code, "virtual_path")
        self.assertEqual(self.backend.keys(), set())

    def test_active_replacement_blocks_mutation_until_abort(self) -> None:
        self.upload("Snapshot/selected.db", b"old")
        writer = self.store.open_write("Snapshot/selected.db", selected=True)
        for operation in (
            lambda: self.store.remove_items(["Snapshot"]),
            lambda: self.store.move_items({"Snapshot": "Moved"}),
            lambda: self.store.copy_item("Snapshot", "Copy"),
        ):
            with self.assertRaises(VirtualStoreError) as failure:
                operation()
            self.assertEqual(failure.exception.code, "virtual_busy")
        writer.abort()
        self.assertEqual(
            b"".join(self.store.read_chunks("Snapshot/selected.db")), b"old"
        )

    def test_large_stream_keeps_local_memory_bounded(self) -> None:
        block = b"p" * INPUT_CHUNK_BYTES
        total_bytes = 64 * 1024 * 1024
        tracemalloc.start()
        try:
            with self.store.open_write("Snapshot/selected.db", selected=True) as writer:
                for _ in range(total_bytes // INPUT_CHUNK_BYTES):
                    writer.write(block)
            _current, peak_bytes = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertLess(peak_bytes, 40 * 1024 * 1024)
        self.assertEqual(self.store.file_size("Snapshot/selected.db"), total_bytes)
        self.assertEqual(self.backend.max_put_bytes, OBJECT_CHUNK_BYTES)


if __name__ == "__main__":
    unittest.main()
