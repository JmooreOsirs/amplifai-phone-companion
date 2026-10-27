from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from amplifai_phone.agent import run_connect
from amplifai_phone.helper_protocol import configure_protocol_streams
from amplifai_phone.ios_backup import IPhoneCapture
from amplifai_phone.metadata import Contact, SourceResult
from amplifai_phone.record_store import RecordStore


class HelperProtocolEncodingTests(unittest.TestCase):
    def test_legacy_host_streams_preserve_password_contact_page_and_query(self):
        """The exact post-capture path must not depend on ANSI-representable data."""
        password = "Fictional-only-密碼-🔒"
        name = "演示 📞 Καλημέρα"
        commands = [
            {"action": "password", "value": password},
            {"action": "contacts", "query": "演示", "cursor": 0},
            {"action": "disconnect"},
        ]
        encoded = ("\r\n".join(json.dumps(item, ensure_ascii=False) for item in commands) + "\r\n").encode("utf-8")
        source = io.TextIOWrapper(io.BytesIO(encoded), encoding="cp1252", errors="strict")
        output = io.BytesIO()
        sink = io.TextIOWrapper(output, encoding="cp1252", errors="strict", newline="\r\n")
        with tempfile.TemporaryDirectory(prefix="fictional-helper-protocol-") as directory:
            store = RecordStore(Path(directory) / "fictional.sqlite3")
            for index in range(250):
                store.add("contacts", Contact(index + 1, name if index == 1 else f"Demo {index}", (), ()))
            contacts, calls, messages = (store.collection(category) for category in ("contacts", "calls", "messages"))

            async def collector(**options):
                self.assertEqual(options["password_provider"](), password)
                return IPhoneCapture(SourceResult(250, contacts, 0), SourceResult(0, calls, 0),
                                     SourceResult(0, messages, 0), "2026-10-27T15:00:00Z",
                                     "2001-01-01T00:00:00Z", (), record_store=store)

            self.assertEqual(run_connect(source, sink, collector), 0)
        payload = output.getvalue()
        self.assertNotIn(b"\r", payload)
        self.assertIn(name.encode("utf-8"), payload)
        self.assertNotIn(password.encode("utf-8"), payload)
        events = [json.loads(line) for line in payload.decode("utf-8").splitlines()]
        capture = next(event for event in events if event["kind"] == "capture")
        self.assertEqual((capture["totalContacts"], len(capture["contacts"]), capture["nextCursor"]),
                         (250, 200, 200))
        self.assertEqual(capture["contacts"][1]["name"], name)
        query = next(event for event in events if event["kind"] == "contacts")
        self.assertEqual([contact["name"] for contact in query["contacts"]], [name])
        self.assertEqual(events[-1], {"kind": "state", "state": "disconnected"})

    def test_disabling_stream_configuration_reproduces_exact_post_capture_failure(self):
        async def collector(**_options):
            return IPhoneCapture(SourceResult(1, (Contact(1, "演示 📞", (), ()),), 0),
                                 SourceResult(0, (), 0), SourceResult(0, (), 0),
                                 "2026-10-27T15:00:00Z", "2001-01-01T00:00:00Z", ())

        output = io.BytesIO()
        sink = io.TextIOWrapper(output, encoding="cp1252", errors="strict", write_through=True)
        with patch("amplifai_phone.agent.configure_protocol_streams"):
            self.assertEqual(run_connect(io.StringIO('{"action":"disconnect"}\n'), sink, collector), 1)
        events = [json.loads(line) for line in output.getvalue().decode("cp1252").splitlines()]
        self.assertEqual([event["kind"] for event in events], ["state", "error"])
        self.assertEqual(events[-1], {"kind": "error", "code": "collection_failed", "stage": "review",
                                     "exceptionClass": "unicode_encode", "reviewStep": "capture_packet"})
        self.assertNotIn("演示", str(events))

    def test_malformed_utf8_command_has_safe_classification_without_private_bytes(self):
        async def collector(**_options):
            return IPhoneCapture(SourceResult(0, (), 0), SourceResult(0, (), 0), SourceResult(0, (), 0),
                                 "2026-10-27T15:00:00Z", "2001-01-01T00:00:00Z", ())

        source = io.TextIOWrapper(io.BytesIO(b'{"action":"contacts","query":"private-\xff"}\n'),
                                  encoding="cp1252", errors="strict")
        sink = io.StringIO()
        self.assertEqual(run_connect(source, sink, collector), 1)
        event = json.loads(sink.getvalue().splitlines()[-1])
        self.assertEqual(event, {"kind": "error", "code": "collection_failed", "stage": "review",
                                 "exceptionClass": "unicode_decode", "reviewStep": "commands"})
        self.assertNotIn("private", sink.getvalue())

    def test_post_capture_error_class_and_step_never_forward_private_exception_details(self):
        async def collector(**_options):
            return IPhoneCapture(SourceResult(0, (), 0), SourceResult(0, (), 0), SourceResult(0, (), 0),
                                 "2026-10-27T15:00:00Z", "2001-01-01T00:00:00Z", ())

        for target, step in (("capture_source_diagnostics", "diagnostics"),
                             ("call_accounting", "call_accounting"),
                             ("_contact_page", "contact_page")):
            with self.subTest(step=step):
                sink = io.StringIO()
                failure = type("PrivatePhonePasswordClass", (Exception,), {})("private phone path, password, token")
                with patch("amplifai_phone.agent." + target, side_effect=failure):
                    self.assertEqual(run_connect(io.StringIO(), sink, collector), 1)
                event = json.loads(sink.getvalue().splitlines()[-1])
                self.assertEqual(event, {"kind": "error", "code": "collection_failed", "stage": "review",
                                         "exceptionClass": "other", "reviewStep": step})
                self.assertNotIn("private", sink.getvalue().lower())

    def test_in_memory_protocol_streams_remain_usable(self):
        source, sink = io.StringIO(), io.StringIO()
        configure_protocol_streams(source, sink)
        sink.write("fictional 演示\n")
        self.assertEqual(sink.getvalue(), "fictional 演示\n")


if __name__ == "__main__":
    unittest.main()
