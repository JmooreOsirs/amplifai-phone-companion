"""Real engine review packets; injected synthetic capture, never a device test."""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from amplifai_phone.agent import run_connect
from amplifai_phone.ios_backup import IPhoneCapture
from amplifai_phone.metadata import Contact, Interaction, SourceResult
from amplifai_phone.record_store import RecordStore
from session import ProtocolError, Session

REVIEW_ID = "00000000-0000-4000-8000-000000000001"

def engine_review(*, missing: tuple[str, ...] = ()) -> list[dict]:
    with tempfile.TemporaryDirectory(prefix="amplifai-windows-wire-") as temporary:
        store = RecordStore(Path(temporary) / "sanitized.sqlite3")
        for source_id, name, phone in ((1, "Avery Synthetic", "+12025550101"),
                                       (2, "Owen Synthetic", "+12025550102")):
            store.add("contacts", Contact(source_id, name, (phone,), ()))
        if "calls" not in missing:
            for index, phone in enumerate(("+12025550101", "+12025550102"), 1):
                store.add("calls", Interaction(index, "call", "call",
                          "2026-09-30T12:00:00+00:00", "outgoing", (phone,), None))
        store.add("messages", Interaction(1, "message", "sms",
                  "2026-09-30T13:00:00+00:00", "incoming", ("+12025550101",), None))
        capture = IPhoneCapture(
            SourceResult(2, store.collection("contacts"), 0),
            SourceResult(0 if "calls" in missing else 2, store.collection("calls"), 0),
            SourceResult(1, store.collection("messages"), 0),
            "2026-10-01T00:00:00+00:00", "2001-01-01T00:00:00+00:00", missing,
            None, store,
        )

        async def collect_synthetic(**_callbacks):
            return capture

        commands = "".join(json.dumps(command) + "\n" for command in (
            {"action": "review-page", "reviewId": REVIEW_ID, "cursor": 0, "total": 1,
             "ids": [1]},
            {"action": "disconnect"},
        ))
        sink = io.StringIO()
        if run_connect(io.StringIO(commands), sink, collector=collect_synthetic) != 0:
            raise AssertionError("Synthetic engine review failed")
        return [json.loads(line) for line in sink.getvalue().splitlines()]


def reviewed_session(events: list[dict]) -> Session:
    state = Session()
    state.begin("inspect")
    state.handle({"kind": "residue", "sessions": []})
    state.exited(0)
    state.approve(True)
    state.begin("connect")
    for event in events:
        if event["kind"] == "capture":
            state.handle(event)
            state.update_visible_selection([1])
            with patch("session.uuid.uuid4", return_value=UUID(REVIEW_ID)):
                state.review([1])
            continue
        state.handle(event)
    return state


class EngineWireTests(unittest.TestCase):
    def test_actual_engine_review_is_usable_without_rewriting_its_field_names(self):
        events = engine_review()
        packet = next(event for event in events if event["kind"] == "review")
        self.assertEqual(packet["selected_contacts"], 1)
        state = reviewed_session(events)
        self.assertEqual(state.review_counts, (1, 1, 1))
        self.assertTrue(state.can_pair)
        self.assertFalse(state.saved_acknowledged)
        with self.assertRaises(ProtocolError):
            state.finish()

    def test_actual_engine_missing_sources_remain_visible_and_not_zero_activity(self):
        state = reviewed_session(engine_review(missing=("calls",)))
        self.assertEqual(state.review_counts, (1, 0, 1))
        self.assertEqual(state.missing, ["calls"])
        self.assertFalse(state.saved_acknowledged)

    def test_camel_only_or_missing_source_packets_cannot_grant_review(self):
        events = engine_review()
        packet = next(event for event in events if event["kind"] == "review")
        for replacement in (
            {"kind": "review", "selectedContacts": 1, "matchedCalls": 1,
             "matchedMessages": 1, "missingSources": []},
            {key: value for key, value in packet.items() if key != "missing_sources"},
        ):
            with self.subTest(keys=tuple(replacement)):
                state = reviewed_session([event for event in events if event["kind"] != "review"])
                with self.assertRaises(ProtocolError):
                    state.handle(replacement)
                self.assertFalse(state.reviewed)
                self.assertFalse(state.saved_acknowledged)


if __name__ == "__main__":
    unittest.main()
