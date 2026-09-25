from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from amplifai_phone.agent import run_admin, run_connect
from amplifai_phone.ios_backup import IPhoneCapture
from amplifai_phone.metadata import Contact, Interaction, SourceResult
from amplifai_phone.workspace import SessionWorkspace


def synthetic_capture() -> IPhoneCapture:
    return IPhoneCapture(
        SourceResult(1, (Contact(1, "Ada Synthetic", ("+15551234567",), ()),), 0),
        SourceResult(
            1,
            (
                Interaction(
                    1,
                    "call",
                    "call",
                    "2026-09-01T12:00:00+00:00",
                    "outgoing",
                    ("+15551234567",),
                    60,
                ),
            ),
            0,
        ),
        SourceResult(
            1,
            (
                Interaction(
                    1,
                    "message",
                    "sms",
                    "2026-09-01T13:00:00+00:00",
                    "incoming",
                    ("+15551234567",),
                    None,
                ),
            ),
            0,
        ),
        "2026-09-23T00:00:00+00:00",
        "2026-03-23T00:00:00+00:00",
        (),
    )


class AgentProtocolTest(unittest.TestCase):
    def test_runtime_check_imports_capture_dependencies_without_device(self) -> None:
        sink = io.StringIO()
        self.assertEqual(run_admin("runtime-check", sink), 0)
        self.assertEqual(
            json.loads(sink.getvalue()), {"kind": "runtime", "ready": True}
        )

    def test_synthetic_process_protocol_requires_selection(self) -> None:
        async def fake_collector(**kwargs: object) -> IPhoneCapture:
            kwargs["progress_callback"](42)
            return synthetic_capture()

        source = io.StringIO('{"action":"review","ids":[1]}\n{"action":"disconnect"}\n')
        sink = io.StringIO()
        self.assertEqual(run_connect(source, sink, fake_collector), 0)
        events = [json.loads(line) for line in sink.getvalue().splitlines()]
        self.assertEqual(
            [item["kind"] for item in events],
            ["state", "progress", "capture", "review", "state"],
        )
        self.assertEqual(events[3]["matched_calls"], 1)
        self.assertEqual(events[3]["matched_messages"], 1)
        self.assertEqual(
            events[3]["observed_call_earliest"], "2026-09-01T12:00:00+00:00"
        )
        self.assertEqual(
            events[3]["observed_message_latest"], "2026-09-01T13:00:00+00:00"
        )
        self.assertEqual(events[2]["contacts"][0]["phoneEnds"], ["4567"])
        self.assertNotIn("+15551234567", sink.getvalue())

    def test_password_prompt_stays_in_pipe_and_is_not_echoed(self) -> None:
        async def fake_collector(**kwargs: object) -> IPhoneCapture:
            self.assertEqual(kwargs["password_provider"](), "synthetic-secret")
            return synthetic_capture()

        source = io.StringIO(
            '{"action":"password","value":"synthetic-secret"}\n'
            '{"action":"disconnect"}\n'
        )
        sink = io.StringIO()
        self.assertEqual(run_connect(source, sink, fake_collector), 0)
        self.assertIn("password_required", sink.getvalue())
        self.assertNotIn("synthetic-secret", sink.getvalue())

    def test_transport_event_reports_existing_wifi_path_without_phone_details(
        self,
    ) -> None:
        async def fake_collector(**kwargs: object) -> IPhoneCapture:
            kwargs["connection_callback"]("wifi")
            return synthetic_capture()

        source = io.StringIO('{"action":"disconnect"}\n')
        sink = io.StringIO()
        self.assertEqual(run_connect(source, sink, fake_collector), 0)
        events = [json.loads(line) for line in sink.getvalue().splitlines()]
        self.assertEqual(events[1], {"kind": "connection", "transport": "wifi"})
        self.assertNotIn("+15551234567", sink.getvalue())

    def test_browser_pairing_requires_review_and_closes_on_disconnect(self) -> None:
        async def fake_collector(**_kwargs: object) -> IPhoneCapture:
            return synthetic_capture()

        source = io.StringIO(
            '{"action":"pair"}\n'
            '{"action":"review","ids":[1]}\n'
            '{"action":"pair"}\n'
            '{"action":"disconnect"}\n'
        )
        sink = io.StringIO()
        bridge = MagicMock()
        bridge.code = "1234567890"
        bridge.port = 48751
        with patch("amplifai_phone.agent.BridgeServer", return_value=bridge) as factory:
            self.assertEqual(run_connect(source, sink, fake_collector), 0)
        events = [json.loads(line) for line in sink.getvalue().splitlines()]
        self.assertEqual(
            [event["kind"] for event in events],
            ["state", "capture", "error", "review", "pairing", "state"],
        )
        self.assertEqual(events[4]["pairCode"], "1234567890")
        factory.assert_called_once()
        self.assertEqual(factory.call_args.args[1], {1})
        bridge.start.assert_called_once()
        bridge.close.assert_called_once()

    def test_pairing_bind_failure_keeps_local_review_available(self) -> None:
        async def fake_collector(**_kwargs: object) -> IPhoneCapture:
            return synthetic_capture()

        source = io.StringIO(
            '{"action":"review","ids":[1]}\n'
            '{"action":"pair"}\n'
            '{"action":"disconnect"}\n'
        )
        sink = io.StringIO()
        with patch("amplifai_phone.agent.BridgeServer", side_effect=OSError("in use")):
            self.assertEqual(run_connect(source, sink, fake_collector), 0)
        events = [json.loads(line) for line in sink.getvalue().splitlines()]
        self.assertEqual(events[-2], {"kind": "error", "code": "bridge_unavailable"})
        self.assertEqual(events[-1], {"kind": "state", "state": "disconnected"})

    def test_admin_residue_is_read_only_until_clear_command(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amplifai-agent-test-") as temporary:
            root = Path(temporary) / "sessions"
            workspace = SessionWorkspace(root)
            directory = workspace.__enter__()
            (directory / "fixture").write_bytes(b"synthetic")
            with patch("amplifai_phone.workspace._pid_alive", return_value=False):
                inspect = io.StringIO()
                self.assertEqual(run_admin("inspect", inspect, root), 0)
                self.assertTrue(directory.exists())
                self.assertEqual(
                    json.loads(inspect.getvalue())["sessions"][0]["name"],
                    directory.name,
                )
                clear = io.StringIO()
                self.assertEqual(run_admin("clear-residue", clear, root), 0)
                self.assertFalse(directory.exists())


if __name__ == "__main__":
    unittest.main()
