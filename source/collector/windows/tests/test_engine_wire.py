"""Real engine review packets; injected synthetic capture, never a device test."""
from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from amplifai_phone.agent import run_connect
from amplifai_phone.ios_backup import IPhoneCapture
from amplifai_phone.metadata import Contact, Interaction, SourceResult
from session import ProtocolError, Session


def engine_review(*, missing: tuple[str, ...] = ()) -> list[dict]:
    capture = IPhoneCapture(
        SourceResult(2, (
            Contact(1, "Avery Synthetic", ("+12025550101",), ()),
            Contact(2, "Owen Synthetic", ("+12025550102",), ()),
        ), 0),
        SourceResult(0 if "calls" in missing else 2, tuple(
            Interaction(index, "call", "call", "2026-09-30T12:00:00+00:00",
                        "outgoing", (phone,), None)
            for index, phone in enumerate(("+12025550101", "+12025550102"), 1)
        ) if "calls" not in missing else (), 0),
        SourceResult(1, (Interaction(
            1, "message", "sms", "2026-09-30T13:00:00+00:00",
            "incoming", ("+12025550101",), None,
        ),), 0),
        "2026-10-01T00:00:00+00:00", "2001-01-01T00:00:00+00:00", missing,
    )

    async def collect_synthetic(**_callbacks):
        return capture

    source = io.StringIO('{"action":"review","ids":[1]}\n{"action":"disconnect"}\n')
    sink = io.StringIO()
    if run_connect(source, sink, collector=collect_synthetic) != 0:
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
        if event["kind"] == "review":
            state.review([1])
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
                state.review([1])
                with self.assertRaises(ProtocolError):
                    state.handle(replacement)
                self.assertFalse(state.reviewed)
                self.assertFalse(state.saved_acknowledged)


if __name__ == "__main__":
    unittest.main()
