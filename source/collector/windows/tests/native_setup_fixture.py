"""Explicit synthetic Windows renderer; unreachable from the production app entrypoint."""
import json
import platform
from dataclasses import replace
from pathlib import Path
from tkinter import font, ttk
from unittest.mock import patch

import app
from build_windows_candidate import gui_digest
from native_run import NativeRun, Outcome
from native_setup import DISCLOSURE
from render_candidate import HANDOFF_ID, capture, descendants, place_viewport, selected_review
from render_contact_workspace import bounds, computed_styles, workspace_geometry
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


def scenarios():
    yield "approval", pending(), inspected(), "setup"
    run = pending()
    run.agree(True)
    run.complete(run.approve(), Outcome("uncertain"))
    yield "approval-uncertain", run, inspected(), "setup"
    run, _ = prepared()
    yield "prepared", run, inspected(), "setup"
    run, _ = prepared()
    run.complete(run.start(), Outcome("uncertain"))
    yield "start-uncertain", run, inspected(), "setup"
    run, _ = started()
    yield "contact-review", run, review_state(), "contacts"
    run, _ = started()
    state = review_state()
    run.complete(run.continue_review(tuple(sorted(state.selected_ids))), Outcome("uncertain"))
    yield "continue-uncertain", run, state, "contacts"
    for name in ("bind-uncertain", "browser-return", "pending-receipt", "saved-recovery"):
        run, _ = started()
        state = selected_review()
        accept(run, run.continue_review(tuple(sorted(state.selected_ids))))
        operation = accept(run, run.pair_received(HANDOFF_ID, "a" * 64))
        if name == "bind-uncertain":
            run.complete(operation, Outcome("uncertain"))
            pane = "setup"
        else:
            accept(run, operation)
            if name != "browser-return":
                state.poll()
                state.handle({"kind": "handoff", "state": "saved" if name == "saved-recovery" else "saved_pending_browser_receipt",
                              "handoffId": HANDOFF_ID})
            if name == "saved-recovery":
                run.record_saved(HANDOFF_ID)
                state.finish()
                state.handle({"kind": "state", "state": "completed"})
                state.exited(0)
            pane = "contacts"
        yield name, run, state, pane
    run = pending()
    scope = replace(run.claim.scope, email="synthetic." + "x" * 180 + "@example.test", course="Synthetic course " * 9)
    run.claim = replace(run.claim, scope=scope)
    run.message = ("Synthetic confirmation is unavailable. Keep the original account tab open for recovery. " * 30).rstrip()
    yield "long-scope-error", run, inspected(), "setup"


def text_scroll_evidence(root, widget):
    # Tk 8.6 caches wrapped-line heights asynchronously; update() alone need not
    # finish that calculation. Force the documented count update before yview.
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


def native_geometry(root, view):
    controls = {str(widget): widget for widget in descendants(view.native_pane)
                if isinstance(widget, ttk.Button) or widget in (view.native_disclosure, view.native_agreement)}
    records = {}
    for name, widget in controls.items():
        x, y, width, height = records[name] = bounds(root, widget)
        if (min(x, y) < 0 or width <= 1 or height <= 1 or x + width > root.winfo_width()
                or y + height > root.winfo_height()):
            raise RuntimeError("Native setup control is clipped: " + name)
        if isinstance(widget, ttk.Button) and width < widget.winfo_reqwidth():
            raise RuntimeError("Native setup button text is clipped: " + name)
    line = font.Font(root=root, font=view.native_disclosure.cget("font")).metrics("linespace")
    if view.native_disclosure.winfo_height() < 2 * line + 20:
        raise RuntimeError("Native setup leaves fewer than two readable disclosure rows")
    if view.native_agreement.cget("text") != DISCLOSURE["agreement"]:
        raise RuntimeError("Native agreement copy changed")
    if DISCLOSURE["sentences"][0] not in view.native_disclosure.get("1.0", "end-1c"):
        raise RuntimeError("Pinned disclosure is missing")
    if view.legacy_consent.winfo_ismapped():
        raise RuntimeError("Legacy disclosure leaked into native setup")
    return records


def keyboard(root, view):
    root.focus_force()
    view.workspace_buttons["setup"].focus_set()
    root.update()
    seen = []
    for _ in range(35):
        widget = root.focus_get()
        if widget is None or str(widget) in seen:
            break
        seen.append(str(widget))
        widget.event_generate("<KeyPress-Tab>")
        widget.event_generate("<KeyRelease-Tab>")
        root.update()
    for widget in (view.native_disclosure, view.native_agreement, view.native_approve, view.native_cancel):
        if str(widget) not in seen:
            raise RuntimeError("Keyboard cannot reach native setup control: " + str(widget))
    return seen


def exercise_controls(view):
    """Invoke actual Tk buttons against explicit fixture transport and helper mocks."""
    clock = Clock()
    view.state = inspected()
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
    view.show_workspace("contacts")
    view.pair_button.invoke()
    selected = set(view.state.selected_ids)
    tick(view, Outcome("uncertain"))
    view.refresh()
    view.clear_all_button.invoke()
    if view.state.selected_ids != selected:
        raise RuntimeError("Continue uncertainty released the frozen selection")
    view.show_workspace("setup")
    view.native_retry.invoke()
    tick(view)
    if not view.state._pairing:
        raise RuntimeError("Confirmed Continue did not request a handoff")
    with patch.object(app.messagebox, "askyesno", return_value=True):
        view.cancel_native()
    if view.native.phase != "cancelled" or not view.state._stopping:
        raise RuntimeError("Native cancellation did not stop locally")
    return ["fresh-delivery", "unchecked-approval-denied", "exact-approval-retry", "duplicate-delivery",
            "separate-start", "exact-start-retry", "continue-uncertainty-setup-retry", "frozen-selection", "local-cancel"]


def render(args, root):
    records, styles, interactions = [], [], []
    for width, height, scaling in ((920, 749, 4 / 3), (680, 680, 4 / 3), (680, 680, 1.75)):
        root.tk.call("tk", "scaling", scaling)
        with patch.object(app, "HelperProcess"), patch.object(app, "CompanionStatusServer"), patch.object(app, "cleanup_owned_updates"), patch.object(app, "AuthorityWorker", return_value=SyntheticWorker()), patch.object(app.PhoneWindow, "pump"):
            view = app.PhoneWindow(root, Path("synthetic-helper-not-launched"), args.logo)
        place_viewport(root, width, height)
        interactions.append({"viewport": [width, height], "scenarios": exercise_controls(view)})
        view.native_worker = SyntheticWorker()
        styles.append({"viewport": [width, height], "tkScaling": scaling, "styles": computed_styles(root, view)})
        for name, run, state, pane in scenarios():
            view.native, view.state = run, state
            view.native_checked.set(name == "approval")
            run.agree(name == "approval")
            view.render_contacts()
            view.refresh()
            view.show_workspace(pane)
            root.update()
            geometry = native_geometry(root, view) if pane == "setup" else workspace_geometry(root, view)
            scrolling = view.native_disclosure if pane == "setup" else view.contacts
            scroll_evidence = text_scroll_evidence(root, scrolling) if pane == "setup" else None
            if pane != "setup":
                scrolling.yview_moveto(1)
                root.update()
            after = native_geometry(root, view) if pane == "setup" else workspace_geometry(root, view)
            if geometry != after:
                raise RuntimeError("Scrolling moved fixed native controls")
            if name == "long-scope-error":
                content = scrolling.get("1.0", "end-1c")
                scroll_evidence["containsFullScopeAndError"] = all(value in content for value in
                    (run.claim.scope.email, run.claim.scope.course, run.message))
                try:
                    require_full_text_scroll(scroll_evidence)
                except RuntimeError as error:
                    diagnostic = capture(root, args.output, f"native-{width}x{height}-scale-{scaling:.2f}-{name}-failure")
                    diagnostic.update(scenario=name, failure=str(error), geometry=geometry, scrollEvidence=scroll_evidence)
                    (args.output / "native-scroll-failure.json").write_text(json.dumps(diagnostic, indent=2) + "\n", encoding="utf-8")
                    raise
            scrolling.yview_moveto(0)
            focus = keyboard(root, view) if name == "approval" else []
            if state.save_receipt_pending:
                if str(view.pair_button.cget("state")) != "disabled":
                    raise RuntimeError("Receipt-pending state permitted another Continue")
            if state.saved_acknowledged:
                if view.pair_button.cget("text") != "Open results" or str(view.pair_button.cget("state")) != "normal":
                    raise RuntimeError("Saved state did not expose bound report recovery")
            record = capture(root, args.output, f"native-{width}x{height}-scale-{scaling:.2f}-{name}")
            record.update(scenario=name, geometry=geometry, keyboardTraversal=focus, fixedDuringScroll=True,
                          scrollEvidence=scroll_evidence)
            records.append(record)
        for widget in root.winfo_children():
            widget.destroy()
    receipt = {"schema": 1, "status": "actual-windows-synthetic-native-setup", "guiSourceSha256": gui_digest(args.source),
               "screenshots": records, "computedStylesByViewport": styles, "python": platform.python_version(),
               "buttonInteractions": interactions,
               "authorityTransportCreated": False, "collectorCreated": False,
               "physicalDpiPhoneAccountOrInstallerProved": False}
    (args.output / "render-native-setup-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    root.destroy()
    print(json.dumps({"status": receipt["status"], "screenshots": len(records), "guiSourceSha256": receipt["guiSourceSha256"]}))
