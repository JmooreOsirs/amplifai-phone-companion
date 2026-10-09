import io
import json
import hashlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from session import Session, ProtocolError
from helper_process import HelperProcess

BINDING = "11111111-1111-4111-8111-111111111111"


def captured(state):
    state.handle({"kind": "capture", "contacts": [{"id": 1, "name": "Avery Lindqvist",
                  "phoneCount": 1, "phoneEnds": ["0123"]}], "query": "", "cursor": 0,
                  "nextCursor": None, "totalContacts": 1,
                  "availableCalls": 2, "availableMessages": 3, "missing": []})
    state.update_visible_selection([1])


def review_result(state, *, calls=2, messages=3):
    command = state.review([1])
    state.handle({"kind": "review-page", "reviewId": command["reviewId"], "nextCursor": 1})
    packet = {"kind": "review", "reviewId": command["reviewId"],
              "selected_contacts": 1, "selection_sha256": hashlib.sha256(b"1\n").hexdigest(),
              "matched_calls": calls, "matched_messages": messages, "missing_sources": []}
    state.handle(packet)
    return packet


def inspected():
    state = Session()
    state.begin("inspect")
    state.handle({"kind": "residue", "sessions": []})
    state.exited(0)
    return state


def reviewed():
    state = inspected()
    state.approve(True)
    state.begin("connect")
    captured(state)
    review_result(state)
    return state


def paired():
    state = reviewed()
    state.pair()
    state.handle({"kind": "pairing", "pairCode": "1234567890", "port": 48751, "expiresInSeconds": 300, "handoffId": BINDING, "payloadSha256": "a" * 64})
    return state


class SessionTests(unittest.TestCase):
    def test_unchecked_and_stale_approval_cannot_launch_collection(self):
        state = inspected()
        self.assertFalse(state.can_connect)
        state.approve(False)
        with self.assertRaises(ProtocolError):
            state.begin("connect")
        state.approve(True)
        self.assertTrue(state.can_connect)
        state.begin("connect")
        state.exited(1)
        self.assertFalse(state.can_connect)

    def test_selection_must_be_reviewed_and_changed_selection_invalidates_pairing(self):
        state = reviewed()
        command = state.review([1])
        with self.assertRaises(ProtocolError):
            state.pair()
        with self.assertRaises(ProtocolError):
            state.review([999])
        state.handle({"kind": "review-page", "reviewId": command["reviewId"], "nextCursor": 1})
        state.handle({"kind": "review", "reviewId": command["reviewId"],
                      "selected_contacts": 1, "selection_sha256": hashlib.sha256(b"1\n").hexdigest(),
                      "matched_calls": 2, "matched_messages": 3, "missing_sources": []})
        self.assertEqual(state.pair(), {"action": "pair"})

    def test_received_is_not_saved_and_foreign_ack_cannot_complete(self):
        state = paired()
        state.poll()
        state.handle({"kind": "handoff", "state": "received", "handoffId": BINDING})
        self.assertFalse(state.saved_acknowledged)
        with self.assertRaises(ProtocolError):
            state.finish()
        state.poll()
        with self.assertRaises(ProtocolError):
            state.handle({"kind": "handoff", "state": "saved", "handoffId": "22222222-2222-4222-8222-222222222222"})
        self.assertFalse(state.saved_acknowledged)

    def test_paged_preview_selects_more_than_25000_without_retaining_old_pages(self):
        state = inspected()
        state.approve(True)
        state.begin("connect")
        total = 25_001

        def page(cursor):
            end = min(cursor + 200, total)
            return {"contacts": [{"id": index + 1, "name": f"Person {index + 1}",
                                   "phoneCount": 1, "phoneEnds": ["0123"]}
                                  for index in range(cursor, end)],
                    "query": "", "cursor": cursor,
                    "nextCursor": end if end < total else None, "totalContacts": total}

        state.handle({"kind": "capture", **page(0), "availableCalls": 0,
                      "availableMessages": 0, "missing": []})
        for cursor in range(0, total, 200):
            if cursor:
                self.assertEqual(state.request_contacts("", cursor),
                                 {"action": "contacts", "query": "", "cursor": cursor})
                state.handle({"kind": "contacts", **page(cursor)})
            state.update_visible_selection([item["id"] for item in state.contacts])
            self.assertLessEqual(len(state.contacts), 200)
        self.assertEqual(len(state.selected_ids), total)
        command = state.review(sorted(state.selected_ids))
        sent = 0
        while command is not None:
            self.assertEqual(command["cursor"], sent)
            self.assertLessEqual(len(command["ids"]), 1000)
            sent += len(command["ids"])
            command = state.handle({"kind": "review-page", "reviewId": command["reviewId"],
                                    "nextCursor": sent})
        self.assertEqual(sent, total)
        digest = hashlib.sha256("".join(f"{index}\n" for index in range(1, total + 1)).encode("ascii")).hexdigest()
        state.handle({"kind": "review", "reviewId": state._pending_review_id,
                      "selection_sha256": digest, "selected_contacts": total,
                      "matched_calls": 0, "matched_messages": 0, "missing_sources": []})
        self.assertTrue(state.can_pair)

    def test_saved_parts_pending_final_browser_receipt_cannot_finish(self):
        state = paired()
        state.poll()
        state.handle({"kind": "handoff", "state": "saved_pending_browser_receipt",
                      "handoffId": BINDING})
        self.assertFalse(state.saved_acknowledged)
        with self.assertRaises(ProtocolError):
            state.finish()

    def test_active_handoff_cannot_be_replaced_without_revocation(self):
        state = paired()
        with self.assertRaises(ProtocolError):
            state.review([1])
        self.assertEqual(state.handoff_id, BINDING)
        self.assertEqual(state.update_visible_selection([]), {"action": "revoke"})
        self.assertEqual(state.handoff_id, "")
        self.assertFalse(state.can_pair)

    def test_stale_page_and_review_digest_cannot_grant_pairing(self):
        state = reviewed()
        command = state.search_contacts("Avery")
        self.assertEqual(command, {"action": "contacts", "query": "Avery", "cursor": 0})
        state.handle({"kind": "contacts", "contacts": [], "query": "older", "cursor": 0,
                      "nextCursor": None, "totalContacts": 1})
        self.assertEqual(state.contact_query, "")
        state.handle({"kind": "contacts", "contacts": [{"id": 1, "name": "Avery Lindqvist",
                      "phoneCount": 1, "phoneEnds": ["0123"]}], "query": "Avery", "cursor": 0,
                      "nextCursor": None, "totalContacts": 1})
        command = state.review([1])
        state.handle({"kind": "review-page", "reviewId": command["reviewId"], "nextCursor": 1})
        with self.assertRaises(ProtocolError):
            state.handle({"kind": "review", "reviewId": command["reviewId"],
                          "selection_sha256": "0" * 64, "selected_contacts": 1,
                          "matched_calls": 0, "matched_messages": 0, "missing_sources": []})
        self.assertFalse(state.can_pair)

    def test_complete_requires_matching_saved_ack_clean_exit_and_residue_inspection(self):
        state = paired()
        state.poll()
        state.handle({"kind": "handoff", "state": "saved", "handoffId": BINDING})
        self.assertEqual(state.finish(), {"action": "finish", "handoffId": BINDING})
        self.assertIsNone(state.poll())
        state.handle({"kind": "state", "state": "completed"})
        self.assertNotEqual(state.phase, "completed")
        state.exited(0)
        self.assertNotEqual(state.phase, "completed")
        state.begin("inspect")
        self.assertIsNone(state.poll())
        state.handle({"kind": "residue", "sessions": []})
        state.exited(0)
        self.assertEqual(state.phase, "completed")

    def test_force_stop_never_claims_cleanup_or_allows_immediate_reconnect(self):
        state = reviewed()
        state.request_stop()
        state.force_stopped()
        state.exited(1)
        self.assertTrue(state.needs_inspection)
        self.assertFalse(state.can_connect)
        self.assertIn("unconfirmed", state.status)
        state.begin("inspect")
        state.handle({"kind": "residue", "sessions": [{"name": "session-" + "a" * 32, "bytes": 4096}]})
        state.exited(0)
        state.approve(True)
        self.assertFalse(state.can_connect)

    def test_password_is_written_only_to_private_stdin_without_session_storage(self):
        state = inspected()
        state.approve(True)
        state.begin("connect")
        state.handle({"kind": "state", "state": "password_required"})
        writer = io.StringIO()
        state.write_password("synthetic-value", writer)
        self.assertEqual(json.loads(writer.getvalue()), {"action": "password", "value": "synthetic-value"})
        self.assertNotIn("synthetic-value", repr(vars(state)))

    def test_actual_pipe_runs_synthetic_helper_without_real_phone_or_secret(self):
        events = []
        helper = HelperProcess([sys.executable, str(Path(__file__).with_name("fake_helper.py"))], events.append)
        helper.start("inspect")
        helper.join(3)
        self.assertEqual(events, [{"kind": "residue", "sessions": []}, {"kind": "process-exit", "code": 0}])
        helper.start("connect")
        helper.write({"action": "disconnect"})
        helper.join(3)
        self.assertEqual(events[-1], {"kind": "process-exit", "code": 0})
        self.assertTrue(any(event.get("state") == "disconnected" for event in events))

    def test_cooperative_stop_can_recover_only_after_a_new_clean_inspection(self):
        state = reviewed()
        self.assertEqual(state.request_stop(), {"action": "disconnect"})
        state.exited(0)
        state.begin("inspect")
        state.handle({"kind": "residue", "sessions": []})
        state.exited(0)
        self.assertTrue(state.inspected)
        self.assertFalse(state.needs_inspection)
        self.assertNotEqual(state.phase, "completed")
        self.assertFalse(state.can_connect)
        state.approve(True)
        self.assertTrue(state.can_connect)

    def test_inflight_review_is_serialized_and_invalidated_response_is_not_approval(self):
        state = reviewed()
        command = state.review([1])
        with self.assertRaises(ProtocolError):
            state.review([1])
        state.invalidate_selection()
        state.handle({"kind": "review-page", "reviewId": command["reviewId"], "nextCursor": 1})
        state.handle({"kind": "review", "reviewId": command["reviewId"],
                      "selected_contacts": 1, "selection_sha256": hashlib.sha256(b"1\n").hexdigest(),
                      "matched_calls": 2, "matched_messages": 3, "missing_sources": []})
        self.assertFalse(state.reviewed)
        self.assertEqual(state.selection, ())
        with self.assertRaises(ProtocolError):
            state.pair()
        state.update_visible_selection([1])
        review_result(state)
        self.assertTrue(state.reviewed)

    def test_inflight_pairing_is_revoked_and_late_code_is_not_presented(self):
        state = reviewed()
        state.pair()
        self.assertEqual(state.invalidate_selection(), {"action": "revoke"})
        state.handle({"kind": "pairing", "pairCode": "1234567890", "port": 48751,
                      "expiresInSeconds": 300, "handoffId": BINDING,
                      "payloadSha256": "a" * 64})
        self.assertFalse(state.reviewed)
        self.assertEqual(state.pair_code, "")
        self.assertEqual(state.handoff_id, "")
        self.assertFalse(state.saved_acknowledged)

    def test_late_saved_ack_for_revoked_selection_never_finishes(self):
        state = paired()
        state.poll()
        self.assertEqual(state.invalidate_selection(), {"action": "revoke"})
        state.handle({"kind": "handoff", "state": "saved", "handoffId": BINDING})
        self.assertFalse(state.saved_acknowledged)
        with self.assertRaises(ProtocolError):
            state.finish()

    def test_pairing_requires_exact_loopback_port_and_string_ascii_code(self):
        for changes in ({"port": 48752}, {"pairCode": 1234567890},
                        {"pairCode": "１２３４５６７８９０"}, {"expiresInSeconds": 300.0}):
            with self.subTest(changes=changes):
                state = reviewed()
                state.pair()
                packet = {"kind": "pairing", "pairCode": "1234567890", "port": 48751,
                          "expiresInSeconds": 300, "handoffId": BINDING,
                          "payloadSha256": "a" * 64, **changes}
                with self.assertRaises(ProtocolError):
                    state.handle(packet)
                self.assertFalse(state.handoff_id)

    def test_current_storage_marker_error_has_specific_no_phone_data_state(self):
        state = inspected()
        state.approve(True)
        state.begin("connect")
        state.handle({"kind": "error", "code": "workspace_marker"})
        self.assertIn("empty or marker-only", state.status)
        self.assertIn("No phone data was written", state.status)
        self.assertFalse(state.saved_acknowledged)


if __name__ == "__main__":
    unittest.main()
