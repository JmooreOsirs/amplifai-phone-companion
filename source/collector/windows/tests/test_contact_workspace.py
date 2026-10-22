"""Headless workspace navigation and truthful summaries; no Tk geometry proof."""
import unittest
from unittest.mock import Mock

from test_gui_guards import source_window
from test_session import BINDING, paired


class ContactWorkspaceTests(unittest.TestCase):
    def window(self):
        view = source_window()
        for name in ("workspace_coverage", "workspace_status", "review_button", "pair_button", "contacts",
                     "details_pane", "contact_workspace", "native_pane", "details_canvas", "details_content",
                     "account_details_label", "status_details_label"):
            setattr(view, name, Mock())
        view.workspace_buttons = {name: Mock() for name in ("contacts", "setup", "details")}
        view.details_content.winfo_height.return_value = 1000
        view.status_details_label.winfo_y.return_value = 400
        view.account_details_label.winfo_y.return_value = 700
        view._workspace_capture = False
        return view

    def summary(self, state):
        return type(source_window()).refresh_workspace.__globals__["workspace_summary"](state)

    def test_unavailable_and_readable_zero_remain_distinct(self):
        state = paired()
        state.source_counts = (0, 0)
        state.missing = ["calls"]
        coverage, _ = self.summary(state)
        self.assertEqual(coverage, "Calls unavailable · Messages: 0 readable")
        state.missing = []
        self.assertEqual(self.summary(state)[0], "Calls: 0 readable · Messages: 0 readable")

    def test_received_pending_and_acknowledged_are_separate(self):
        for received, expected in (("received", "Preview received. Account Save is still required."),
                                   ("saved_pending_browser_receipt", "Save receipt pending. Keep this app open."),
                                   ("saved", "Account Save acknowledged. See recovery for cleanup.")):
            with self.subTest(received=received):
                state = paired()
                state.poll()
                state.handle({"kind": "handoff", "state": received, "handoffId": BINDING})
                self.assertEqual(self.summary(state)[1], expected)
                self.assertEqual(state.saved_acknowledged, received == "saved")

    def test_long_error_does_not_expand_workspace_or_replace_full_evidence(self):
        state = paired()
        state.phase = "error"
        state.status = "Synthetic error detail. " * 200
        original = state.status
        self.assertEqual(self.summary(state)[1], "Collection needs attention. Open Details & recovery.")
        self.assertEqual(state.status, original)

    def test_navigation_preserves_selection_destination_and_save_authority(self):
        view = self.window()
        view.state = paired()
        before = (set(view.state.selected_ids), view.state.selection, view.state.handoff_id,
                  view.state.destination_intent_id, view.state.approval, view.state.saved_acknowledged)
        for name in ("contacts", "setup", "details", "contacts"):
            view.show_workspace(name)
        self.assertEqual(before, (view.state.selected_ids, view.state.selection, view.state.handoff_id,
                                  view.state.destination_intent_id, view.state.approval, view.state.saved_acknowledged))
        view.helper.write.assert_not_called()
        view.helper.start.assert_not_called()
        view.details_pane.grid_remove.assert_called()
        view.contact_workspace.grid.assert_called()
        view.show_workspace("details", account=True)
        view.details_canvas.yview_moveto.assert_called_with(0.7)

    def test_refresh_enters_contacts_once_without_stealing_details_focus(self):
        view = self.window()
        view.state = paired()
        type(view).refresh_workspace(view)
        self.assertEqual(view._visible_workspace, "contacts")
        view.contacts.focus_set.assert_called_once()
        view.show_workspace("details")
        type(view).refresh_workspace(view)
        self.assertEqual(view._visible_workspace, "details")
        view.contacts.focus_set.assert_called_once()
        view.review_button.grid_remove.assert_called()
        view.pair_button.grid.assert_called()

    def test_focus_reveals_scrolled_detail_action(self):
        view = self.window()
        view.details_pane.winfo_ismapped.return_value = True
        view.details_content.winfo_rooty.return_value = 0
        view.details_canvas.canvasy.return_value = 0
        view.details_canvas.winfo_height.return_value = 500
        button = Mock()
        button.winfo_ismapped.return_value = True
        button.winfo_rooty.return_value = 800
        button.winfo_height.return_value = 50
        view.reveal_detail_focus(button)
        view.details_canvas.yview_moveto.assert_called_once_with(0.35)
        view.details_pane.winfo_ismapped.return_value = False
        view.reveal_detail_focus(button)
        view.details_canvas.yview_moveto.assert_called_once()

    def test_replaced_primary_action_retains_keyboard_focus(self):
        view = self.window()
        view.state = paired()
        view._workspace_capture = True
        view.window.focus_get.return_value = view.review_button
        type(view).refresh_workspace(view)
        view.pair_button.focus_set.assert_called_once()
        view.show_workspace("details", account=True)
        view.workspace_buttons["details"].focus_set.assert_called_once()


if __name__ == "__main__":
    unittest.main()
