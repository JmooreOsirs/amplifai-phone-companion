"""Fast helper drains must not multiply idle renders, polls or owned timers."""
import unittest
from unittest.mock import Mock, patch

from test_gui_guards import source_window


class PumpCadenceTests(unittest.TestCase):
    def window(self):
        view = source_window()
        view.pump_native = Mock()
        view.state.poll = Mock(return_value=None)
        self.timers = {}
        def after(delay, callback):
            token = object()
            self.timers[token] = (delay, callback)
            return token
        view.window.after.side_effect = after
        view.window.after_cancel.side_effect = lambda token: self.timers.pop(token, None)
        return view

    def test_twenty_idle_drains_have_one_render_one_poll_and_one_timer(self):
        view = self.window()
        now = [0.0]
        with patch.object(view.pump.__globals__["time"], "monotonic", side_effect=lambda: now[0]):
            for index in range(20):
                now[0] = index / 20
                view.pump()
                self.assertEqual(len(self.timers), 1)
                self.assertEqual(next(iter(self.timers.values()))[0], 50)
        view.refresh.assert_called_once()
        view.state.poll.assert_called_once()
        self.assertEqual(view.pump_native.call_count, 20)

    def test_event_drains_and_renders_without_waiting_for_visual_heartbeat(self):
        view = self.window()
        view._next_visual_refresh = view._next_handoff_poll = 10.0
        view.events.put({"kind": "transfer", "stage": "backup", "receivedBytes": 200,
                         "retainedBytes": 100, "discardedBytes": 100, "filesReceived": 2,
                         "elapsedSeconds": 0.5, "bytesPerSecond": 400.0})
        with patch.object(view.pump.__globals__["time"], "monotonic", return_value=0.25):
            view.pump()
        self.assertTrue(view.events.empty())
        self.assertIn("200 bytes", view.state.transfer_status)
        view.refresh.assert_called_once()
        view.state.poll.assert_not_called()

    def test_shutdown_cancels_the_owned_continuation(self):
        view = self.window()
        view.pump()
        self.assertEqual(len(self.timers), 1)
        view.shutdown()
        self.assertEqual(self.timers, {})
        self.assertIsNone(view._pump_after)
        view.window.destroy.assert_called_once()


if __name__ == "__main__":
    unittest.main()
