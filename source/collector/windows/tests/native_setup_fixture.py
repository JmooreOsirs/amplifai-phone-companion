"""Synthetic authority scenarios and real button exercises for the isolated renderer."""
from dataclasses import replace
from unittest.mock import patch

import app
from native_run import NativeRun, Outcome
from render_candidate import HANDOFF_ID, selected_review
from render_contact_workspace import captured
from session import BACKUP_CHECK_MODE
from test_native_app import SyntheticWorker, tick
from test_native_run import Clock, accept, prepared, started
from test_native_setup import CLIENT, LINK_TEXT
from test_session import inspected


def pending():
    clock = Clock()
    run = NativeRun(CLIENT, now=lambda: clock.time, monotonic=lambda: clock.seconds)
    accept(run, run.claim_link(LINK_TEXT))
    return run


def review_state():
    state = selected_review()
    state.handoff_id = state.pair_code = ""
    state._clear_destination()
    return state


def collecting():
    state = inspected()
    state.approve(True)
    state.begin("connect")
    state.handle({"kind": "state", "state": "connecting"})
    return state


def scenarios():
    yield "account-idle", NativeRun(CLIENT), inspected()
    yield "approval-unchecked", pending(), inspected()
    run = pending()
    run.agree(True)
    yield "approval-checked", run, inspected()
    run = pending()
    run.agree(True)
    run.complete(run.approve(), Outcome("uncertain"))
    yield "approval-uncertain", run, inspected()
    run, _ = prepared()
    yield "prepared", run, inspected()
    for encrypted in (True, False):
        run, _ = prepared()
        state = inspected()
        state.begin(BACKUP_CHECK_MODE)
        state.handle({"kind": BACKUP_CHECK_MODE, "backupEncrypted": encrypted})
        state.exited(0)
        state.begin("inspect")
        state.handle({"kind": "residue", "sessions": []})
        state.exited(0)
        yield "backup-encrypted" if encrypted else "backup-unencrypted", run, state
    run, _ = prepared()
    run.complete(run.start(), Outcome("uncertain"))
    yield "start-uncertain", run, inspected()
    for name in ("connecting", "collecting", "password", "processing"):
        run, _ = started()
        state = collecting()
        if name != "connecting":
            state.handle({"kind": "transfer", "stage": "backup", "receivedBytes": 72_208_910_467,
                          "retainedBytes": 1_210_988_643, "discardedBytes": 70_997_921_824,
                          "filesReceived": 81_371, "elapsedSeconds": 11_803.0, "bytesPerSecond": 6_117_860.0})
            state.handle({"kind": "progress", "value": 100})
        if name == "password":
            state.handle({"kind": "state", "state": "password_required"})
        if name == "processing":
            state.handle({"kind": "state", "state": "processing"})
            state.handle({"kind": "transfer", "stage": "processing", "processedBytes": 812_441_600})
        yield name, run, state
    for name in ("contacts", "contacts-empty", "contacts-partial", "contacts-select-all"):
        run, _ = started()
        state = captured(empty=name == "contacts-empty", partial=name == "contacts-partial")
        if name == "contacts-select-all":
            state.select_all_contacts()
        yield name, run, state
    run, _ = started()
    yield "reviewed-contacts", run, review_state()
    run, _ = started()
    state = review_state()
    run.complete(run.continue_review(tuple(sorted(state.selected_ids))), Outcome("uncertain"))
    yield "continue-uncertain", run, state
    for name in ("bind-uncertain", "bound", "preview-received", "pending-receipt", "saved"):
        run, _ = started()
        state = selected_review()
        accept(run, run.continue_review(tuple(sorted(state.selected_ids))))
        operation = accept(run, run.pair_received(HANDOFF_ID, "a" * 64))
        if name == "bind-uncertain":
            run.complete(operation, Outcome("uncertain"))
        else:
            accept(run, operation)
            acknowledgment = {"preview-received": "received", "pending-receipt": "saved_pending_browser_receipt", "saved": "saved"}.get(name)
            if acknowledgment:
                state.poll()
                state.handle({"kind": "handoff", "state": acknowledgment, "handoffId": HANDOFF_ID})
            if name == "saved":
                run.record_saved(HANDOFF_ID)
                state.finish()
                state.handle({"kind": "state", "state": "completed"})
                state.exited(0)
        yield name, run, state
    run, _ = started()
    yield "recovery", run, captured(partial=True)
    run, _ = started()
    run.authority = "paused"
    yield "authority-paused", run, captured()
    run, _ = started()
    run.complete(run.refresh(), Outcome("refused"))
    yield "authority-refused", run, captured()
    run, _ = started()
    run.cancel()
    yield "authority-cancelled", run, captured()
    run, clock = started()
    clock.advance((run.run.expires_at - clock.time).total_seconds() + 1)
    yield "authority-expired", run, captured()
    run, _ = started()
    run.handoff_expired()
    yield "handoff-expired", run, captured()
    state = selected_review()
    state._clear_destination()
    yield "legacy-awaiting-destination", NativeRun(CLIENT), state
    yield "legacy-pending-destination", NativeRun(CLIENT), selected_review()
    run, _ = started()
    state = collecting()
    state.phase = "error"
    state.status = "Synthetic local source is unavailable. Keep this review open and check Details & recovery. " * 12
    yield "collection-error", run, state
    run = pending()
    scope = replace(run.claim.scope, email="synthetic." + "x" * 180 + "@example.test", course="Synthetic course " * 9)
    run.claim = replace(run.claim, scope=scope)
    run.message = ("Synthetic confirmation is unavailable. Keep the original account tab open for recovery. " * 30).rstrip()
    yield "long-scope-error", run, inspected()


def text_scroll_evidence(root, widget):
    # Force Tk's lazy wrapped-line metrics before judging the scroll range.
    cached_pixels = int(widget.count("1.0", "end", "ypixels")[0])
    content_pixels = int(widget.count("1.0", "end", "update", "ypixels"))
    widget.yview_moveto(0)
    root.update()
    before = list(widget.yview())
    widget.yview_moveto(1)
    root.update()
    return {"cachedContentPixels": cached_pixels, "contentPixels": content_pixels,
            "widgetHeight": widget.winfo_height(), "widgetWidth": widget.winfo_width(),
            "before": before, "after": list(widget.yview()),
            "lastLine": widget.dlineinfo("end-2c"), "lastCharacter": widget.bbox("end-2c")}


def require_full_text_scroll(evidence):
    if evidence["containsFullScopeAndError"] is not True:
        raise RuntimeError("The full authenticated scope or recovery error is missing")
    if evidence["contentPixels"] <= evidence["widgetHeight"]:
        raise RuntimeError("Long scope/error fixture did not exceed the text viewport")
    if evidence["after"][0] <= evidence["before"][0]:
        raise RuntimeError("Full scope and error could not scroll after line-metric synchronization")
    line, character = evidence["lastLine"], evidence["lastCharacter"]
    if (not line or not character or line[1] < 0 or line[1] + line[3] > evidence["widgetHeight"]
            or min(character[:2]) < 0 or character[0] + character[2] > evidence["widgetWidth"]):
        raise RuntimeError("The final scope/error line is not fully reachable")


def exercise_controls(view):
    """Invoke production Tk callbacks with explicit synthetic transport/helper boundaries."""
    clock = Clock()
    view.state = inspected()
    view.native = NativeRun(CLIENT)
    view.native_worker = SyntheticWorker()
    view._startup_update_pending = False
    view.helper.start.reset_mock()
    factory = lambda _identity: NativeRun(CLIENT, now=lambda: clock.time, monotonic=lambda: clock.seconds)
    with patch.object(app, "NativeRun", side_effect=factory):
        if view.receive_setup(LINK_TEXT) != "accepted":
            raise RuntimeError("Fresh setup was not accepted")
    tick(view)
    view.refresh()
    view.native_approve.invoke()
    if len(view.native_worker.operations) != 1:
        raise RuntimeError("Unchecked agreement sent an approval")
    view.native_agreement.invoke()
    view.native_approve.invoke()
    first = view.native_worker.waiting.body
    tick(view, Outcome("uncertain"))
    view.refresh()
    view.native_retry.invoke()
    if view.native_worker.waiting.body != first:
        raise RuntimeError("Approval retry changed request bytes")
    tick(view)
    if view.receive_setup(LINK_TEXT) != "duplicate" or view.native.started:
        raise RuntimeError("Duplicate delivery changed prepared authority")
    view.helper.start.assert_not_called()
    view.refresh()
    view.native_start.invoke()
    first = view.native_worker.waiting.body
    tick(view, Outcome("uncertain"))
    view.refresh()
    view.native_retry.invoke()
    if view.native_worker.waiting.body != first:
        raise RuntimeError("Start retry changed operation")
    tick(view)
    view.helper.start.assert_called_once_with("connect")
    view.state = review_state()
    view.render_contacts()
    view.refresh()
    view.pair_button.invoke()
    selected = set(view.state.selected_ids)
    tick(view, Outcome("uncertain"))
    view.refresh()
    view.clear_all_button.invoke()
    if view.state.selected_ids != selected:
        raise RuntimeError("Continue uncertainty released the frozen selection")
    view.recovery_retry_button.invoke()
    tick(view)
    if not view.state._pairing:
        raise RuntimeError("Confirmed Continue did not request a handoff")
    with patch.object(app.messagebox, "askyesno", return_value=True):
        view.cancel_native()
    if view.native.phase != "cancelled" or not view.state._stopping:
        raise RuntimeError("Native cancellation did not stop locally")
    return ["fresh-delivery", "unchecked-approval-denied", "exact-approval-retry", "duplicate-delivery",
            "separate-start", "exact-start-retry", "continue-uncertainty-visible-retry", "frozen-selection", "local-cancel"]
