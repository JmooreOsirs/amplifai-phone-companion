"""Injected render-gate failures; no native window or capture is created here."""
from contextlib import ExitStack, contextmanager, redirect_stdout
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import render_guided_journey as renderer


class GuidedGateTests(unittest.TestCase):
    def run_injected(self, output, *, collect_all, at_exit=False):
        roots = []

        def create_root():
            root = Mock()
            root.tk.call.return_value = 4 / 3
            root.tk.splitlist.return_value = ()
            roots.append(root)
            return root

        def inject(root):
            root.report_callback_exception(RuntimeError, RuntimeError("injected startup callback"), None)

        @contextmanager
        def synthetic_view(root, _logo):
            if not at_exit:
                inject(root)
            yield Mock()
            if at_exit:
                inject(root)

        state = SimpleNamespace(selected_ids=set(), saved_acknowledged=False)
        with ExitStack() as stack:
            stack.enter_context(patch.object(renderer.sys, "platform", "win32"))
            stack.enter_context(patch.dict(renderer.os.environ, {"GITHUB_ACTIONS": "true", "RUNNER_OS": "Windows"}))
            stack.enter_context(redirect_stdout(io.StringIO()))
            replacements = {
                "VIEWPORTS": ((1180, 820, 4 / 3), (680, 680, 4 / 3)),
                "EXPECTED_STAGE": {"account-idle": "account", "saved": "complete"},
                "source_proof": Mock(return_value={"sourceCommit": "injected-head"}),
                "gui_digest": Mock(return_value="injected-digest"),
                "create_root": create_root, "synthetic_view": synthetic_view,
                "scenarios": lambda: iter((name, Mock(), state) for name in ("account-idle", "saved")),
                "journey": Mock(return_value=SimpleNamespace(stage="account")),
                "capture": Mock(side_effect=lambda *_args: {"width": 1180, "height": 820, "path": "injected.png"}),
                "rounded_fill_pixels": Mock(return_value=[]),
                "failure_context": Mock(return_value={}),
                "visible_snapshot": Mock(return_value={"windowDpi": 96}),
            }
            for name in ("place_viewport", "exercise_controls", "exercise_group_controls", "assert_stage",
                         "assert_actions", "stage_geometry", "computed_styles", "measure_pump"):
                replacements[name] = Mock(return_value={})
            for name, replacement in replacements.items():
                stack.enter_context(patch.object(renderer, name, replacement))
            args = SimpleNamespace(source=Path("unused-source"), logo=Path("unused-logo"), output=output,
                                   collect_all_failures=collect_all)
            with self.assertRaisesRegex(RuntimeError, "callback|failed"):
                renderer.render(args)
        return roots

    def test_startup_callback_fails_strict_gate_and_tears_down(self):
        with TemporaryDirectory() as folder:
            output = Path(folder) / "strict"
            roots = self.run_injected(output, collect_all=False)
            failures = json.loads((output / "failures.json").read_text(encoding="utf-8"))
            self.assertEqual(len(roots), 1)
            self.assertEqual(len(failures), 1)
            self.assertEqual(failures[0]["callbackErrors"], ["RuntimeError: injected startup callback"])
            roots[0].destroy.assert_called_once()

    def test_startup_callback_fails_collect_all_after_complete_census(self):
        self.assert_complete_failed_census()

    def test_teardown_callback_is_not_lost_outside_inner_checks(self):
        self.assert_complete_failed_census(at_exit=True)

    def assert_complete_failed_census(self, *, at_exit=False):
        with TemporaryDirectory() as folder:
            output = Path(folder) / "census"
            roots = self.run_injected(output, collect_all=True, at_exit=at_exit)
            receipt = json.loads((output / "render-guided-receipt.json").read_text(encoding="utf-8"))
            failures = json.loads((output / "failures.json").read_text(encoding="utf-8"))
            self.assertFalse(receipt["passed"])
            self.assertEqual(receipt["failureCount"], 2)
            self.assertEqual(len(receipt["screenshots"]), 4)
            self.assertEqual(len(receipt["scenarioResults"]), receipt["plannedScenarioCount"])
            self.assertTrue(all(item["callbackErrors"] == ["RuntimeError: injected startup callback"] for item in failures))
            self.assertEqual(len(roots), 2)
            for root in roots:
                root.destroy.assert_called_once()


if __name__ == "__main__":
    unittest.main()
