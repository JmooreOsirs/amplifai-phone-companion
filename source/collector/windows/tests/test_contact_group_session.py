"""Complete-snapshot grouping, selection custody and reentrant native callbacks."""
import unittest
from unittest.mock import Mock, patch

from session import ProtocolError
from test_session import inspected
from test_native_app import native_window
from test_native_run import started


def previews(total=405):
    return [{"id": index, "name": f"Person {index}" if index <= total - 5 else f"Company {index} LLC",
             "phoneCount": 1, "phoneEnds": ["0123"]} for index in range(1, total + 1)]


def capture(items):
    state = inspected()
    state.approve(True)
    state.begin("connect")
    state.handle({"kind": "capture", "contacts": items[:200], "query": "", "cursor": 0,
                  "nextCursor": 200 if len(items) > 200 else None, "totalContacts": len(items),
                  "availableCalls": 0, "availableMessages": 0, "missing": []})
    return state


def page(items, command):
    cursor = command["cursor"]
    end = min(cursor + 200, len(items))
    return {"kind": "contacts", "scanId": command["scanId"], "query": "", "cursor": cursor,
            "nextCursor": end if end < len(items) else None, "totalContacts": len(items),
            "contacts": items[cursor:end]}


def grouped(total=405):
    items = previews(total)
    state = capture(items)
    command = state.prepare_contact_workspace()
    while command:
        command = state.handle(page(items, command))
    return state


class ContactGroupSessionTests(unittest.TestCase):
    def test_expanded_memory_budget_falls_back_before_default_or_partial_publication(self):
        for total in (10, 405):
            with self.subTest(total=total):
                items = previews(total)
                state = capture(items)
                state.update_visible_selection([1])
                original_page = list(state.contacts)
                with patch("session.MAX_CONTACT_WORKSPACE_MEMORY_BYTES", 1):
                    command = state.prepare_contact_workspace()
                    if command:
                        self.assertIsNone(state.handle(page(items, command)))
                self.assertIsNone(state.contact_workspace)
                self.assertEqual(state.selected_ids, {1})
                self.assertEqual(state.contacts, original_page)
                self.assertEqual(state._workspace_previews, [])
                self.assertIsNone(state.pending_contact)
                self.assertTrue(state.can_review)
                self.assertIn("paged All contacts", state.status)

    def test_workspace_allocation_failure_preserves_manual_review(self):
        items = previews(10)
        state = capture(items)
        state.update_visible_selection([1])
        with patch("contact_workspace.ContactWorkspace", side_effect=MemoryError):
            self.assertIsNone(state.prepare_contact_workspace())
        self.assertIsNone(state.contact_workspace)
        self.assertEqual(state.selected_ids, {1})
        self.assertEqual(state.contacts, items)
        self.assertTrue(state.can_review)

    def test_expanded_budget_discards_staged_pages_without_publishing_defaults(self):
        from contact_workspace import preview_peak_bytes
        from session import CONTACT_WORKSPACE_MEMORY_RESERVE
        items = previews()
        state = capture(items)
        budget = CONTACT_WORKSPACE_MEMORY_RESERVE + sum(preview_peak_bytes(row) for row in items[:210])
        with patch("session.MAX_CONTACT_WORKSPACE_MEMORY_BYTES", budget):
            command = state.handle(page(items, state.prepare_contact_workspace()))
            self.assertEqual(len(state._workspace_previews), 200)
            self.assertIsNone(state.handle(page(items, command)))
        self.assertIsNone(state.contact_workspace)
        self.assertEqual(state.selected_ids, set())
        self.assertEqual(state.contacts, items[:200])
        self.assertEqual(state._workspace_previews, [])
        self.assertTrue(state.can_review)

    def test_complete_scan_is_atomic_and_only_fresh_main_starts_selected(self):
        items = previews()
        state = capture(items)
        original_page = list(state.contacts)
        command = state.prepare_contact_workspace()
        for _ in range(2):
            command = state.handle(page(items, command))
            self.assertIsNone(state.contact_workspace)
            self.assertEqual(state.selected_ids, set())
            self.assertEqual(state.contacts, original_page)
            self.assertFalse(state.can_review)
        self.assertIsNone(state.handle(page(items, command)))
        self.assertEqual(state.selected_ids, set(range(1, 401)))
        self.assertEqual(state.contact_workspace.group, "Main")
        self.assertEqual(len(state.contacts), 200)
        self.assertTrue(state.can_review)
        self.assertEqual(state._workspace_previews, [])
        state.clear_all_contacts()
        state.prepare_contact_workspace()
        self.assertEqual(state.selected_ids, set())

    def test_explicit_selection_or_explicit_empty_is_never_replaced_by_default(self):
        for selected in ([1], []):
            with self.subTest(selected=selected):
                items = previews()
                state = capture(items)
                state.update_visible_selection(selected)
                command = state.prepare_contact_workspace()
                while command:
                    command = state.handle(page(items, command))
                self.assertEqual(state.selected_ids, set(selected))
                self.assertIn("existing selection is unchanged", state.status)

    def test_unfresh_or_reviewed_capture_does_not_gain_defaults(self):
        state = capture(previews(10))
        state._fresh_workspace_capture = False
        state.prepare_contact_workspace()
        self.assertEqual(state.selected_ids, set())
        state = capture(previews(10))
        state.update_visible_selection([1])
        state.review([1])
        self.assertIsNone(state.prepare_contact_workspace())
        self.assertIsNone(state.contact_workspace)
        self.assertEqual(state.selected_ids, {1})

    def test_all_review_and_empty_collections_are_valid_without_implicit_selection(self):
        for items in ([], previews(5)):
            with self.subTest(total=len(items)):
                state = capture(items)
                self.assertIsNone(state.prepare_contact_workspace())
                self.assertEqual(state.contact_workspace.main_count, 0)
                self.assertEqual(state.selected_ids, set())
                self.assertEqual(state.contacts, [])

    def test_cancel_stop_and_abnormal_exit_discard_scan_cache_without_changing_selection(self):
        for ending in ("cancel", "stop", "exit", "failure"):
            with self.subTest(ending=ending):
                items = previews()
                state = capture(items)
                state.update_visible_selection([1])
                command = state.prepare_contact_workspace()
                command = state.handle(page(items, command))
                self.assertEqual(len(state._workspace_previews), 200)
                if ending == "cancel":
                    state.cancel_select_all()
                    state.handle(page(items, command))
                elif ending == "stop":
                    state.request_stop()
                elif ending == "exit":
                    state.exited(1)
                else:
                    state.cooperative_failure()
                self.assertIsNone(state.contact_workspace)
                self.assertEqual(state.selected_ids, {1})
                self.assertEqual(state._workspace_previews, [])
                self.assertEqual(state._workspace_preview_bytes, 0)
                self.assertIsNone(state.pending_contact)

    def test_malformed_or_duplicate_scan_cannot_publish_a_partial_workspace(self):
        for fault in ("duplicate", "short", "total", "cursor", "scan"):
            with self.subTest(fault=fault):
                items = previews()
                state = capture(items)
                state.update_visible_selection([1])
                command = state.handle(page(items, state.prepare_contact_workspace()))
                event = page(items, command)
                if fault == "duplicate":
                    event["contacts"] = [items[0], *event["contacts"][1:]]
                elif fault == "short":
                    event["contacts"] = event["contacts"][:-1]
                elif fault == "total":
                    event["totalContacts"] += 1
                elif fault == "cursor":
                    event["cursor"] += 1
                else:
                    event["scanId"] = "stale-scan"
                with self.assertRaises(ProtocolError):
                    state.handle(event)
                self.assertIsNone(state.contact_workspace)
                self.assertEqual(state.selected_ids, {1})
                self.assertEqual(state.contacts, items[:200])
                self.assertEqual(state._workspace_previews, [])
                self.assertIsNone(state.pending_contact)

    def test_capacity_fallback_preserves_manual_paging_and_explicit_retry(self):
        items = previews()
        state = capture(items)
        state.update_visible_selection([1])
        with patch("session.MAX_CONTACT_WORKSPACE_BYTES", 1):
            state.handle(page(items, state.prepare_contact_workspace()))
        self.assertIsNone(state.contact_workspace)
        self.assertEqual(state.selected_ids, {1})
        self.assertTrue(state.can_review)
        command = state.next_contacts()
        ordinary = page(items, {**command, "scanId": "unused"})
        ordinary.pop("scanId")
        state.handle(ordinary)
        self.assertEqual(state.contact_cursor, 200)
        command = state.prepare_contact_workspace()
        while command:
            command = state.handle(page(items, command))
        self.assertEqual(state.selected_ids, {1})
        self.assertIsNotNone(state.contact_workspace)
        state = capture(items)
        with patch("session.MAX_CONTACT_WORKSPACE_PREVIEWS", 400):
            self.assertIsNone(state.prepare_contact_workspace())
        self.assertFalse(state.selecting_all)
        self.assertTrue(state.can_review)

    def test_scoped_selection_and_marks_span_pages_without_selecting_review(self):
        state = grouped()
        state.select_workspace_contacts(False)
        state.search_contacts("Person 2")
        matches = set(state.contact_workspace.filtered_ids)
        state.select_workspace_contacts(True)
        self.assertEqual(state.selected_ids, matches)
        state.search_contacts("")
        state.mark_contact_for_move(1)
        state.next_contacts()
        state.mark_contact_for_move(201)
        state.search_contacts("Person 2")
        self.assertEqual(state.marked_group_ids, {1, 201})
        self.assertEqual(state.selected_ids, matches)
        state.show_contact_group("Review")
        self.assertEqual(state.marked_group_ids, set())
        self.assertEqual(state.selected_ids, matches)

    def test_move_to_review_deselects_and_undo_restores_exact_prior_selection(self):
        state = grouped(10)
        state.update_visible_selection([1, 3])
        state.mark_contact_for_move(1)
        state.mark_contact_for_move(2)
        move = state.begin_group_move(marked=True)
        self.assertEqual(state.selected_ids, {1, 3})
        state.confirm_group_move(move)
        self.assertEqual(state.selected_ids, {3})
        self.assertTrue({1, 2} <= state.contact_workspace.review_ids)
        state.undo_group_move()
        self.assertEqual(state.selected_ids, {1, 3})
        self.assertTrue({1, 2} <= state.contact_workspace.main_ids)
        self.assertIsNone(state.group_undo)

    def test_move_to_main_never_selects_and_selection_edit_retires_undo(self):
        state = grouped(10)
        state.move_contact(6, "Main")
        self.assertNotIn(6, state.selected_ids)
        self.assertIn(6, state.contact_workspace.main_ids)
        state.select_workspace_contacts(True)
        self.assertIn(6, state.selected_ids)
        self.assertIsNone(state.group_undo)
        with self.assertRaises(ProtocolError):
            state.undo_group_move()

    def test_bulk_confirmation_rejects_changed_query_group_marks_generation_and_selection(self):
        for change in ("query", "group", "marks", "generation", "selection"):
            with self.subTest(change=change):
                state = grouped(10)
                state.mark_contact_for_move(1)
                move = state.begin_group_move(marked=change == "marks")
                original = state.contact_workspace.review_ids
                if change == "query":
                    state.search_contacts("Person 1")
                elif change == "group":
                    state.show_contact_group("Review")
                elif change == "marks":
                    state.mark_contact_for_move(2)
                elif change == "generation":
                    state.contact_generation = "new-capture"
                else:
                    state.clear_all_contacts()
                with self.assertRaises(ProtocolError):
                    state.confirm_group_move(move)
                self.assertEqual(state.contact_workspace.review_ids, original)
                self.assertIsNone(state.group_undo)

    def test_all_group_mutations_lock_during_helper_review(self):
        state = grouped(10)
        state.move_contact(1, "Review")
        state.review(sorted(state.selected_ids))
        before = (state.contact_workspace.review_ids, set(state.selected_ids), set(state.marked_group_ids))
        operations = [lambda: state.move_contact(2, "Review"), state.undo_group_move,
                      lambda: state.select_workspace_contacts(False), lambda: state.mark_contact_for_move(2),
                      state.mark_all_for_move, state.clear_move_marks, state.begin_group_move,
                      lambda: state.show_contact_group("Review"), lambda: state.search_contacts("Person")]
        for operation in operations:
            with self.assertRaises(ProtocolError):
                operation()
            self.assertEqual((state.contact_workspace.review_ids, state.selected_ids, state.marked_group_ids), before)


class ContactGroupNativeCallbackTests(unittest.TestCase):
    def window(self):
        view, _ = native_window()
        view.native, _ = started()
        view.state = grouped(10)
        view.render_contacts = Mock()
        view.include_all = Mock()
        view.include_all.get.return_value = True
        view.organizing = True
        return view

    def test_pending_continue_and_continued_binding_lock_every_contact_callback(self):
        for binding in (False, True):
            view = self.window()
            view.state.move_contact(1, "Review")
            view.review(continue_to_account=True)
            if binding:
                view.native.continued_selection = view.state.selection
                view._pending_continue = None
            before = (view.state.contact_workspace.review_ids, set(view.state.selected_ids), set(view.state.marked_group_ids))
            writes = view.helper.write.call_count
            operations = [lambda: view.show_contact_group("Review"), lambda: view.select_contact_scope(False),
                          view.prepare_contact_groups, view.toggle_organizing, lambda: view.mark_contact_for_move(0),
                          lambda: view.move_contact(0, "Review"), view.move_contact_group,
                          view.undo_contact_group, view.toggle_all_contacts]
            for operation in operations:
                operation()
                self.assertEqual((view.state.contact_workspace.review_ids, view.state.selected_ids,
                                  view.state.marked_group_ids), before)
                self.assertEqual(view.helper.write.call_count, writes)
            self.assertTrue(view.organizing)

    def test_dialog_reentrancy_cannot_apply_stale_or_newly_locked_move(self):
        for change in ("query", "continue", "cancel"):
            with self.subTest(change=change):
                view = self.window()
                before = (view.state.contact_workspace.review_ids, set(view.state.selected_ids))
                def reply(*_args):
                    if change == "query":
                        view.state.search_contacts("Person 1")
                    elif change == "continue":
                        view.review(continue_to_account=True)
                    return change != "cancel"
                dialog = type(view).move_contact_group.__globals__["messagebox"]
                with patch.object(dialog, "askyesno", side_effect=reply):
                    view.move_contact_group()
                self.assertEqual((view.state.contact_workspace.review_ids, view.state.selected_ids), before)
                self.assertIsNone(view.state.pending_group_move)


if __name__ == "__main__":
    unittest.main()
