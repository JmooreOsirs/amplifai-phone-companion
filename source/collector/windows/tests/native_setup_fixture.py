"""Synthetic authority scenarios and real button exercises for the isolated renderer."""
from dataclasses import replace
import hashlib
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
from test_windows_source_diagnostics import diagnostic


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


def grouped_state(*, main_only=False, reviewed=False):
    """Complete synthetic preview pages through the production verified-snapshot path."""
    previews = [{"id": index, "name": f"Synthetic {'contact' if main_only or index <= 400 else 'LLC'} {index}",
                 "phoneCount": 1, "phoneEnds": ["0000"]} for index in range(1, 406)]
    state = collecting()
    def page(cursor):
        end = min(cursor + 200, len(previews))
        return {"contacts": previews[cursor:end], "query": "", "cursor": cursor,
                "nextCursor": end if end < len(previews) else None, "totalContacts": len(previews)}
    state.handle({"kind": "capture", **page(0), "availableCalls": 0, "availableMessages": 0, "missing": []})
    command = state.prepare_contact_workspace()
    for cursor in range(0, len(previews), 200):
        if command is None or command["cursor"] != cursor:
            raise RuntimeError("Synthetic grouping scan lost its verified page sequence")
        command = state.handle({"kind": "contacts", **page(cursor), "scanId": command["scanId"]})
    if command is not None or state.contact_workspace is None:
        raise RuntimeError("Complete synthetic scan did not publish the local contact workspace")
    if reviewed:
        command = state.review(sorted(state.selected_ids))
        while command is not None:
            command = state.handle({"kind": "review-page", "reviewId": command["reviewId"],
                                    "nextCursor": command["cursor"] + len(command["ids"])})
        digest = hashlib.sha256("".join(f"{item}\n" for item in sorted(state.selected_ids)).encode()).hexdigest()
        state.handle({"kind": "review", "reviewId": state._pending_review_id, "selection_sha256": digest,
                      "selected_contacts": len(state.selected_ids), "matched_calls": 0, "matched_messages": 0,
                      "missing_sources": []})
    return state


def group_scenarios():
    for name in ("group-main", "group-review", "group-all", "group-empty-review", "group-empty-search",
                 "group-next-page", "group-scoped-selection", "group-organize-marked", "group-moved", "group-undo",
                 "group-continue-pending", "group-bind-pending"):
        run, _ = started()
        state = grouped_state(main_only=name == "group-empty-review",
                              reviewed=name in ("group-continue-pending", "group-bind-pending"))
        if name in ("group-review", "group-empty-review"):
            state.show_contact_group("Review")
        elif name == "group-all":
            state.show_contact_group("All")
        elif name == "group-empty-search":
            state.search_contacts("No synthetic name matches this query")
        elif name == "group-next-page":
            state.next_contacts()
        elif name == "group-scoped-selection":
            state.select_workspace_contacts(False, scoped=False)
            state.search_contacts("Synthetic contact")
            state.select_workspace_contacts(True)
            state.next_contacts()
        elif name == "group-organize-marked":
            state.mark_contact_for_move(1)
            state.next_contacts()
            state.mark_contact_for_move(201)
        elif name in ("group-moved", "group-undo"):
            state.move_contact(1, "Review")
            if name == "group-undo":
                state.undo_group_move()
        elif name in ("group-continue-pending", "group-bind-pending"):
            operation = run.continue_review(tuple(sorted(state.selected_ids)))
            if name == "group-bind-pending":
                accept(run, operation)
                state.pair()
                state.handle({"kind": "pairing", "pairCode": "1234567890", "port": 48751,
                              "expiresInSeconds": 300, "handoffId": HANDOFF_ID, "payloadSha256": "a" * 64})
                accept(run, run.pair_received(HANDOFF_ID, "a" * 64))
        yield name, run, state


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
    for encrypted in (True, False, None):
        run, _ = prepared()
        run.check_backup_setting()
        state = inspected()
        state.begin(BACKUP_CHECK_MODE)
        if encrypted is not None:
            state.handle({"kind": BACKUP_CHECK_MODE, "backupEncrypted": encrypted})
        state.exited(0)
        run.checked_backup_setting(state.checked_backup_setting)
        state.begin("inspect")
        state.handle({"kind": "residue", "sessions": []})
        state.exited(0)
        yield "backup-encrypted" if encrypted is True else "backup-unencrypted" if encrypted is False else "backup-unknown", run, state
    run, _ = prepared()
    run.check_backup_setting()
    state = inspected()
    state.begin(BACKUP_CHECK_MODE)
    yield "backup-checking", run, state
    run, _ = prepared()
    run.check_backup_setting()
    run.checked_backup_setting(True)
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
    for name in ("bind-uncertain", "bound", "browser-opened", "preview-received", "pending-receipt", "saved"):
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
    for name in ("collection-failed-clean", "collection-stopped-clean", "collection-failed-residue",
                 "collection-processing-failed-clean"):
        run, _ = started()
        state = collecting()
        state.handle({"kind": "transfer", "stage": "processing", "processedBytes": 1211527184})
        if name == "collection-stopped-clean":
            run.cancel()
            state.request_stop()
        else:
            run.fail()
            if name == "collection-processing-failed-clean":
                # Synthetic reporting failure only; this does not claim to
                # reproduce the cause of a real encrypted-backup read failure.
                state.handle({"kind": "error", "code": "collection_failed", "stage": "processing"})
            else:
                state.handle({"kind": "selected-payload-diagnostic", **diagnostic(
                    declaredBytes=794714112, rawBytes=794722320, copiedBytes=794722304,
                    validationCode="unsupported_size_growth", copyDigest="matched")})
                state.handle({"kind": "error", "code": "selected_payload_length"})
            state.cooperative_failure()
        state.exited(0 if name == "collection-stopped-clean" else 1)
        state.begin("inspect")
        residue = [{"name": "session-" + "a" * 32, "bytes": 4096}] if name.endswith("residue") else []
        state.handle({"kind": "residue", "sessions": residue})
        state.exited(0)
        yield name, run, state
    run = pending()
    scope = replace(run.claim.scope, email="synthetic." + "x" * 180 + "@example.test", course="Synthetic course " * 9)
    run.claim = replace(run.claim, scope=scope)
    run.message = ("Synthetic confirmation is unavailable. Keep the original account tab open for recovery. " * 30).rstrip()
    yield "long-scope-error", run, inspected()
    yield from group_scenarios()


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


def pump_fixture_events(view):
    """Drain real callbacks without starting a live pump across static cases."""
    try:
        view.pump()
    finally:
        if view._pump_after is not None:
            view.window.after_cancel(view._pump_after)
            view._pump_after = None


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
    view.helper.start.assert_called_once_with(BACKUP_CHECK_MODE)
    view.events.put({"kind": BACKUP_CHECK_MODE, "backupEncrypted": True})
    view.events.put({"kind": "process-exit", "code": 0})
    pump_fixture_events(view)
    view.events.put({"kind": "residue", "sessions": []})
    view.events.put({"kind": "process-exit", "code": 0})
    pump_fixture_events(view)
    view.helper.start.reset_mock()
    first = view.native_worker.waiting.body
    tick(view, Outcome("uncertain"))
    view.refresh()
    view.native_retry.invoke()
    if view.native_worker.waiting.body != first:
        raise RuntimeError("Start retry changed operation")
    tick(view)
    view.helper.start.assert_called_once_with("connect", require_encrypted_backup=True)
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


def exercise_group_controls(view):
    """Exercise actual production group/card/menu callbacks with synthetic contacts."""
    view.native, _ = started()
    view.native_worker = SyntheticWorker()
    view.state = grouped_state()
    view.organizing = False
    view._pending_continue = None
    view._visible_workspace, view._journey_stage = "setup", None
    view.render_contacts()
    view.refresh()
    view.window.update()
    main, review = set(range(1, 401)), set(range(401, 406))

    def selected(expected):
        if view.state.selected_ids != expected:
            raise RuntimeError("A group control changed contacts outside its explicit selection scope")

    def menu(prefix):
        view.refresh_organize_menu()
        matches = [index for index in range(view.organize_menu.index("end") + 1)
                   if view.organize_menu.type(index) == "command"
                   and view.organize_menu.entrycget(index, "label").startswith(prefix)]
        if len(matches) != 1:
            raise RuntimeError("Organization menu has no unambiguous action: " + prefix)
        view.organize_menu.invoke(matches[0])
        view.window.update()

    selected(main)
    view.group_buttons["Review"].invoke()
    selected(main)
    view.scope_select_button.invoke()
    selected(main | review)
    view.scope_clear_button.invoke()
    selected(main)
    view.group_buttons["Main"].invoke()
    view.contact_query.set("Synthetic contact")
    view.search_button.invoke()
    view.scope_clear_button.invoke()
    selected(set())
    view.next_button.invoke()
    if view.state.contact_cursor != 200:
        raise RuntimeError("The group Next page control did not reach the second local page")
    view.scope_select_button.invoke()
    selected(main)
    view.previous_button.invoke()
    view.select_all_button.invoke()
    selected(main | review)
    view.select_all_button.invoke()
    selected(set())
    view.scope_select_button.invoke()
    selected(main)

    # A single card move is reversible and distinct from the bulk confirmation.
    view.contacts._cards[0].move_menu.invoke(0)
    selected(main - {1})
    if not view.state.contact_workspace.is_in_review(1):
        raise RuntimeError("The card Move menu did not update its local group")
    menu("Undo last group move")
    selected(main)
    menu("Mark contacts to move")
    view.contacts._cards[0].mark_button.invoke()
    selected(main)
    if view.state.marked_group_ids != {1}:
        raise RuntimeError("Mark to move did not remain independent of selection")
    with patch.object(app.messagebox, "askyesno", return_value=False) as confirm:
        menu("Move 1 marked")
        if confirm.call_count != 1 or "1 contacts from Main to Review" not in confirm.call_args.args[1]:
            raise RuntimeError("Marked group move did not ask an explicit count/source/destination confirmation")
    selected(main)
    if view.state.contact_workspace.review_ids != review or view.state.pending_group_move is not None:
        raise RuntimeError("Cancelled move changed groups or retained a pending confirmation")
    with patch.object(app.messagebox, "askyesno", return_value=True):
        menu("Move 1 marked")
    selected(main - {1})
    menu("Undo last group move")
    selected(main)
    view.contact_query.set("Synthetic contact 2")
    view.search_button.invoke()
    scoped = set(view.state.contact_workspace.filtered_ids)
    with patch.object(app.messagebox, "askyesno", return_value=True) as confirm:
        menu(f"Move all {len(scoped):,} matches")
        if confirm.call_count != 1 or f"{len(scoped):,} contacts from Main to Review" not in confirm.call_args.args[1]:
            raise RuntimeError("Scoped move omitted its explicit confirmation")
    selected(main - scoped)
    if view.state.contact_workspace.review_ids != review | scoped:
        raise RuntimeError("Scoped move affected contacts outside the search")
    menu("Undo last group move")
    selected(main)

    # Programmatic callbacks and already-created menu commands must also obey the
    # lock; a disabled button alone is insufficient for a retained command.
    for name, native, state in group_scenarios():
        if name not in ("group-continue-pending", "group-bind-pending"):
            continue
        view.state, view.native = state, native
        view.render_contacts()
        view.refresh()
        before = (set(state.selected_ids), set(state.contact_workspace.review_ids), state.contact_workspace.group,
                  state.contact_workspace.query, state.contact_cursor, set(state.marked_group_ids), view.organizing)
        operations = (lambda: view.show_contact_group("Review"), lambda: view.select_contact_scope(False),
                      view.clear_all_contacts, view.select_all_contacts, view.toggle_organizing,
                      lambda: view.mark_contact_for_move(0), lambda: view.move_contact(0, "Review"),
                      view.move_contact_group, view.undo_contact_group, view.next_contacts, view.search_contacts)
        with patch.object(app.messagebox, "askyesno", side_effect=AssertionError("Locked group move opened confirmation")):
            for operation in operations:
                operation()
        after = (set(state.selected_ids), set(state.contact_workspace.review_ids), state.contact_workspace.group,
                 state.contact_workspace.query, state.contact_cursor, set(state.marked_group_ids), view.organizing)
        if before != after:
            raise RuntimeError("A pending Continue/bind allowed group, search, page, mark or selection mutation")
    return ["main-default-selection", "group-selection-preserved", "scope-across-pages", "explicit-include-all",
            "single-card-move-and-undo", "independent-move-marks", "marked-move-confirmation-cancel-and-accept",
            "search-scoped-move-confirmation-and-undo", "continue-and-bind-lock-every-mutation"]
