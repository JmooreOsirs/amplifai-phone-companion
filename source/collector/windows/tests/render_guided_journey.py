"""Actual Windows guided-stage evidence with only synthetic authority and helper inputs."""
from __future__ import annotations

import argparse
from collections import deque
from contextlib import contextmanager
import _tkinter
import faulthandler
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
import traceback
from tkinter import font, ttk
from unittest.mock import Mock, patch

import app
from build_windows_candidate import FONT, GUI_FILES, LOCK, LOGO, gui_digest
from journey import journey
from native_setup import DISCLOSURE
from native_setup_fixture import (collecting, exercise_controls, exercise_group_controls, require_full_text_scroll,
                                  scenarios, text_scroll_evidence)
from render_candidate import capture, create_root, descendants, place_viewport, visible_snapshot
from render_contact_workspace import (bounds, check_keyboard, computed_styles, keyboard_traversal,
                                      require_bounds, rounded_fill_pixels, workspace_geometry)
from test_native_app import SyntheticWorker
from test_native_run import started
from test_native_setup import CLIENT
from test_session import inspected


class EventPumpGuard:
    """Bound synthetic Tk drains while retaining the real event ordering."""
    def __init__(self, root, *, timeout=10.0):
        self.root, self.timeout = root, timeout
        self.events = deque(maxlen=128)
        self.event_count = 0
        self.last_failure = None
        root.bind_all("<Configure>", self.record, add="+")
        root.update = lambda: self.drain(_tkinter.ALL_EVENTS, "update")
        root.update_idletasks = lambda: self.drain(_tkinter.IDLE_EVENTS, "update_idletasks")

    def record(self, event):
        self.event_count += 1
        self.events.append({"widget": str(event.widget), "width": event.width, "height": event.height})

    def drain(self, flags, operation):
        started = time.perf_counter()
        processed = 0
        while self.root.tk.dooneevent(flags | _tkinter.DONT_WAIT):
            processed += 1
            if time.perf_counter() - started >= self.timeout:
                self.last_failure = {"operation": operation, "timeoutSeconds": self.timeout,
                                     "processedEvents": processed, "configureEvents": self.event_count,
                                     "recentConfigureEvents": list(self.events)}
                raise RuntimeError(f"Tk {operation} did not settle within {self.timeout:g}s; "
                                   f"processed {processed} events")


VIEWPORTS = ((1180, 820, 4 / 3), (920, 749, 4 / 3), (680, 680, 4 / 3), (680, 680, 1.75))
EXPECTED_STAGE = {
    "account-idle": "account", "approval-unchecked": "approval", "approval-checked": "approval",
    "approval-uncertain": "approval", "prepared": "prepare", "start-uncertain": "prepare",
    "backup-encrypted": "prepare", "backup-unencrypted": "prepare",
    "backup-unknown": "prepare", "backup-checking": "prepare",
    "connecting": "collection", "collecting": "collection", "password": "collection", "processing": "collection",
    "contacts": "contacts", "contacts-empty": "contacts", "contacts-partial": "contacts",
    "contacts-select-all": "contacts", "reviewed-contacts": "contacts", "continue-uncertain": "attention",
    "bind-uncertain": "attention", "bound": "save", "preview-received": "save", "pending-receipt": "save",
    "saved": "complete", "recovery": "contacts", "collection-error": "attention", "long-scope-error": "approval",
    "authority-paused": "attention", "authority-refused": "attention", "authority-cancelled": "attention",
    "authority-expired": "attention", "handoff-expired": "attention",
    "collection-failed-clean": "attention", "collection-stopped-clean": "attention", "collection-failed-residue": "attention",
    "collection-processing-failed-clean": "attention",
    "legacy-awaiting-destination": "save", "legacy-pending-destination": "save",
    "group-main": "contacts", "group-review": "contacts", "group-all": "contacts",
    "group-empty-review": "contacts", "group-empty-search": "contacts", "group-next-page": "contacts",
    "group-scoped-selection": "contacts", "group-organize-marked": "contacts", "group-moved": "contacts",
    "group-undo": "contacts", "group-continue-pending": "save", "group-bind-pending": "save",
}
CONTACT_LAYOUT_DIAGNOSTIC = frozenset((
    "collecting", "contacts", "contacts-empty", "contacts-partial", "contacts-select-all", "reviewed-contacts",
    "group-main", "group-review", "group-all", "group-empty-search", "group-organize-marked",
))


@contextmanager
def synthetic_view(root, logo):
    with callback_timings(root), synthetic_window(root, logo) as view:
        yield view


@contextmanager
def synthetic_window(root, logo):
    helper = Mock()
    with (patch.object(app, "HelperProcess", return_value=helper),
          patch.object(app, "CompanionStatusServer"), patch.object(app, "cleanup_owned_updates"),
          patch.object(app, "AuthorityWorker", return_value=SyntheticWorker()),
          patch.object(app.webbrowser, "open", side_effect=AssertionError("Fixture must not open a browser")),
          patch("socket.create_connection", side_effect=AssertionError("Fixture must not use a network")),
          patch.object(app.PhoneWindow, "pump")):
        view = app.PhoneWindow(root, Path("synthetic-helper-not-launched"), logo)
    if view._pump_after is not None:
        root.after_cancel(view._pump_after)
        view._pump_after = None
    view._startup_update_pending = False
    view.update_state = "current"
    try:
        # These fences remain active while production callbacks are exercised.
        with (patch.object(app.webbrowser, "open", side_effect=AssertionError("Fixture must not open a browser")),
              patch("socket.create_connection", side_effect=AssertionError("Fixture must not use a network"))):
            yield view
    finally:
        if view._pump_after is not None:
            root.after_cancel(view._pump_after)
            view._pump_after = None
        view.native_worker.close()
        view.close_source_details()


def assert_stage(root, view, name):
    current = journey(view.state, view.native)
    if current.stage != EXPECTED_STAGE[name]:
        raise RuntimeError(f"Wrong user stage for {name}: {current.stage}")
    expected_panel = ("details" if name == "recovery" else
                      "save" if current.stage == "complete" else "approval" if current.stage == "prepare" else current.stage)
    # Tk retains a child's mapped flag when a stage ancestor is hidden.
    # Viewable checks the complete ancestor chain, as actual visibility requires.
    visible = [key for key, panel in view.stage_panels.items() if panel.winfo_viewable()]
    expected = [] if expected_panel == "contacts" else [expected_panel]
    if visible != expected or bool(view.contact_workspace.winfo_viewable()) != (expected_panel == "contacts"):
        raise RuntimeError(f"Unrelated stage is visible in {name}: {visible}")
    labels = [item.cget("text") for item in view.journey_steps]
    if labels != ["Approve", "Collect", "Choose", "Save & report"] or any(isinstance(child, ttk.Button) for child in descendants(view.navigation)):
        raise RuntimeError("The guided four-step journey reverted to workspace navigation")
    step = 4 if current.stage == "complete" else current.step
    if view.journey.current != step:
        raise RuntimeError("Connected journey is not on the current stage")
    for index, item in enumerate(view.journey_steps):
        visible_label = not view.journey._compact or index == min(3, step)
        if bool(item.winfo_viewable()) != visible_label:
            raise RuntimeError("Compact journey hid the current label or exposed unrelated labels")
        if visible_label:
            require_bounds(root, item, "journey step")
        require_bounds(root, view.journey.circles[index], "journey circle")
        if (item.cget("style") == "Current.Journey.TLabel") != (index <= step):
            raise RuntimeError("Journey indicator does not match the active stage")
    for connector in view.journey.connectors:
        if connector.winfo_width() < 1 or not connector.winfo_viewable():
            raise RuntimeError("The connected journey lost a stage connector")
    for attribute in ("legacy_consent", "agreement", "approve", "connect"):
        widget = getattr(view, attribute, None)
        if widget is not None and widget.winfo_viewable():
            raise RuntimeError("Legacy local approval leaked into the guided flow")
    if expected_panel != "details":
        for widget in (view.inspect, view.clear, view.copy_code_button, view.open_account_button, view.force_button):
            if widget.winfo_viewable():
                raise RuntimeError("Manual recovery controls leaked into a normal stage")
    if current.stage == "collection":
        for widget in (view.native_agreement, view.native_approve, view.native_start, view.contacts, view.pair_button):
            if widget.winfo_viewable():
                raise RuntimeError("Unrelated approval/selection control is visible while collecting")
        if bool(view.password.winfo_viewable()) != (name == "password"):
            raise RuntimeError("Backup password entry is shown outside the password stage")
        if [item.cget("text") for item in view.collection_stage_labels] != ["Connect iPhone", "Receive backup", "Read locally", "Clean up"]:
            raise RuntimeError("Collection lost its local substage hierarchy")
        active = 2 if name in ("password", "processing") else 1 if name == "collecting" else 0
        if view.collection_eyebrow.cget("text") != f"ON THIS PC · STAGE {active + 1} OF 4":
            raise RuntimeError("Collection substage does not describe the current local operation")
        for index, item in enumerate(view.collection_stage_labels):
            expected_style = "Completed.Stage.TLabel" if index < active else "Active.Stage.TLabel" if index == active else "Stage.TLabel"
            if item.cget("style") != expected_style or not item.winfo_viewable():
                raise RuntimeError("Collection substage indicator is incorrect")
        metric = name != "connecting"
        if bool(view.collection_metric_well.winfo_viewable()) != metric:
            raise RuntimeError("Collection invents or hides its actual byte measurement")
        if metric:
            expected_value, expected_label = ("774.8 MiB", "PROCESSED") if name == "processing" else ("67.2 GiB", "RECEIVED")
            if view.collection_metric_value.cget("text") != expected_value or view.collection_metric_label.cget("text") != expected_label:
                raise RuntimeError("Received and locally processed bytes were conflated")
    if current.stage == "approval":
        text = view.native_disclosure.get("1.0", "end-1c")
        if (DISCLOSURE["sentences"][0] not in text
                or view.native_agreement.cget("text") != DISCLOSURE["agreement"]):
            raise RuntimeError("The exact approved disclosure or agreement is missing")
        line = font.Font(root=root, font=view.native_disclosure.cget("font")).metrics("linespace")
        if view.native_disclosure.winfo_height() < 2 * line + 20:
            raise RuntimeError("Approval leaves fewer than two readable disclosure rows")
    return current, expected_panel


def stage_geometry(root, view, panel):
    if panel == "contacts":
        records = workspace_geometry(root, view)
        records["journey"] = [bounds(root, circle) for circle in view.journey.circles]
        return records
    container = view.stage_panels[panel]
    x, _y, width, _height = bounds(root, container)
    if x < 0 or width <= 1 or x + width > root.winfo_width():
        raise RuntimeError("Current stage panel is clipped horizontally")
    records = {"panel": bounds(root, container), "recovery_toggle": require_bounds(root, view.recovery_toggle, "recovery toggle"),
               "journey": [bounds(root, circle) for circle in view.journey.circles]}
    # Scrollable setup/recovery controls must be horizontally sound and become
    # fully visible when reached by native keyboard focus.
    for widget in descendants(container):
        if isinstance(widget, (ttk.Button, ttk.Menubutton, ttk.Checkbutton, ttk.Entry)) and widget.winfo_viewable():
            box = bounds(root, widget)
            if box[0] < x or box[0] + box[2] > x + width or box[2] < widget.winfo_reqwidth():
                raise RuntimeError(f"Current-stage control is horizontally clipped: {widget}: {box}")
            records[str(widget)] = box
    if panel == "collection":
        for name in ("collection_heading", "collection_detail", "collection_eyebrow", "collection_illustration_well",
                     "collection_stages", "collection_metric_well", "collection_metric_value", "collection_next"):
            widget = getattr(view, name)
            if widget.winfo_viewable():
                box = bounds(root, widget)
                if box[0] < x or box[0] + box[2] > x + width:
                    raise RuntimeError(f"Collection hierarchy is horizontally clipped: {name}: {box}")
                records[name] = box
    return records


def assert_actions(view, name):
    if name in ("approval-unchecked", "approval-checked"):
        expected = "normal" if name == "approval-checked" else "disabled"
        if str(view.native_approve.cget("state")) != expected:
            raise RuntimeError("Approval primary action does not respect the unchecked agreement")
    if name == "prepared" and str(view.native_start.cget("state")) != "normal":
        raise RuntimeError("Prepared run has no enabled Start action")
    if name == "prepared" and "No manual backup is required" not in view.native_scope.get():
        raise RuntimeError("Prepared screen hides automatic backup ownership before Start")
    if name in ("backup-encrypted", "backup-unencrypted", "backup-unknown", "backup-checking"):
        partial = name in ("backup-unencrypted", "backup-unknown")
        if bool(view.native_available.winfo_ismapped()) != partial:
            raise RuntimeError("Available-data action escaped the Off/Unknown preparation state")
        if partial and (str(view.native_available.cget("state")) != "normal" or "without confirmed call history" not in view.native_status.get()):
            raise RuntimeError("Partial-data action is disabled or missing its call-history limitation")
        if name == "backup-checking" and str(view.native_start.cget("state")) != "disabled":
            raise RuntimeError("A backup check permits a duplicate Start")
    if name == "contacts-empty" and str(view.review_button.cget("state")) != "disabled":
        raise RuntimeError("Empty selection exposes enabled Continue")
    if name in ("contacts", "reviewed-contacts"):
        primary = view.review_button if name == "contacts" else view.pair_button
        if primary.cget("style") != "Primary.TButton" or str(primary.cget("state")) != "normal":
            raise RuntimeError("The contact action dock has no enabled branded primary Continue")
    if name in ("continue-uncertain", "bind-uncertain"):
        if not view.attention_action.winfo_viewable() or view.attention_action.cget("text") != "Retry confirmation" or str(view.contacts.cget("state")) != "disabled":
            raise RuntimeError("Uncertain Continue lost recovery or unlocked the approved selection")
    if name == "start-uncertain" and (view.native_agreement.winfo_viewable() or not view.native_retry.winfo_viewable()):
        raise RuntimeError("Uncertain Start re-asked approval or hid retry")
    if name == "authority-paused" and not view.attention_action.winfo_viewable():
        raise RuntimeError("Paused retained review lost confirmation recovery")
    if name in ("collection-failed-clean", "collection-stopped-clean", "collection-failed-residue",
                "collection-processing-failed-clean"):
        if "ready" in view.window.title() or view.journey.current != 1 or view.state.transfer_status:
            raise RuntimeError("Terminal collection reverted to ready, approval or live read progress")
        if name.endswith("clean") and (view.attention_action.winfo_viewable() or "original account tab" not in view.attention_detail.cget("text")):
            raise RuntimeError("Clean terminal state lost the single original-account return")
        if name.endswith("clean") and (not view.attention_account.winfo_viewable() or view.attention_account.cget("style") != "Quiet.TButton"):
            raise RuntimeError("Clean terminal state lost its explicit closed-tab fallback")
        if name.endswith("residue") and (not view.attention_action.winfo_viewable() or view.attention_action.cget("text") != "Remove marked temporary data"):
            raise RuntimeError("Terminal residue state lost its cleanup action")
    if name == "collection-processing-failed-clean":
        if (view.state.diagnostic_details or "collection_failed" not in view.attention_detail.cget("text")
                or "processing" not in view.attention_detail.cget("text")
                or str(view.diagnostic_details_button.cget("state")) != "normal"
                or view.diagnostic_details_button.cget("text") != "Failure diagnostic details"):
            raise RuntimeError("Processing failure lost local diagnostics after a clean inspection without source history")
    if name.startswith("legacy-") and view.save_heading.cget("text") != "Connect your account":
        raise RuntimeError("Legacy local handoff incorrectly claims an approved account connection")
    if name == "pending-receipt" and view.state.saved_acknowledged:
        raise RuntimeError("Pending receipt was presented as an account Save")
    if name == "saved" and view.save_account_button.cget("text") != "Open report":
        raise RuntimeError("Saved receipt has no bound report action")
    if name == "collecting" and ("backup transfer only" not in view.progress_caption.get()
                                  or "not been read" not in view.coverage_status.get()):
        raise RuntimeError("Device 100% is incorrectly presented as complete source or account coverage")


def assert_group_workspace(root, view, name):
    state, workspace = view.state, view.state.contact_workspace
    main = set(range(1, 406 if name == "group-empty-review" else 401))
    review = set(range(401, 406)) if name != "group-empty-review" else set()
    if name == "group-moved":
        main.remove(1)
        review.add(1)
    if (workspace.known_ids != set(range(1, 406)) or workspace.main_ids != main or workspace.review_ids != review
            or state.selected_ids != main or len(state.contacts) > 200
            or state.contacts != list(workspace.visible)):
        raise RuntimeError("Group membership, preserved selection, or bounded current page differs from the synthetic review")
    group = "Review" if name in ("group-review", "group-empty-review") else "All" if name == "group-all" else "Main"
    if workspace.group != group:
        raise RuntimeError("The displayed contact group changed unexpectedly")
    expected_marks = {1, 201} if name == "group-organize-marked" else set()
    if view.organizing != (name == "group-organize-marked") or not view.organize_button.cget("text").startswith("Organizing" if view.organizing else "Organize"):
        raise RuntimeError("Organize mode or its visible feedback differs from the current workspace")
    if state.marked_group_ids != expected_marks:
        raise RuntimeError("Organizing marks were lost or confused with contact selection")
    expected_page = 200 if name in ("group-next-page", "group-scoped-selection", "group-organize-marked") else 0
    if workspace.page_start != expected_page:
        raise RuntimeError("Group/search paging did not retain the expected local page")
    if name in ("group-empty-review", "group-empty-search"):
        if state.contacts or not view.contact_empty_label.winfo_viewable():
            raise RuntimeError("Empty group or search has no visible explanation")
        require_bounds(root, view.contact_empty_label, "empty contact explanation")
        text = view.contact_empty_label.cget("text").lower()
        if not text or ("review" not in text if name == "group-empty-review" else "match" not in text):
            raise RuntimeError("Empty group/search explanation does not describe the current scope")
    locked = name in ("group-continue-pending", "group-bind-pending")
    controls = (*view.group_buttons.values(), view.select_all_button, view.organize_button,
                view.scope_select_button, view.scope_clear_button, view.scope_menu_button, view.search_entry)
    if locked and (not view.native_selection_locked or any(not item.instate(("disabled",)) for item in controls)):
        raise RuntimeError("Pending Continue/bind leaves an editable group or selection control")
    if not locked:
        for item in (*view.group_buttons.values(), view.organize_button, view.select_all_button):
            if not item.winfo_viewable() or item.instate(("disabled",)):
                raise RuntimeError("An editable contact review hides or disables group controls")
        for group_name, button in view.group_buttons.items():
            if (button.cget("style") == "Selected.Group.TButton") != (group_name == group):
                raise RuntimeError("Selected group has no accurate native state")
        for contact, card in zip(state.contacts, view.contacts._cards, strict=True):
            if len(contact["name"]) <= 30 and len(str(card.button.cget("text")).splitlines()) > 2:
                raise RuntimeError("Compact card options leave too little width for a normal contact name")
            if card.variable.get() != (contact["id"] in state.selected_ids):
                raise RuntimeError("Contact checkbox disagrees with the preserved selection")
            if card.marked.get() != (contact["id"] in expected_marks):
                raise RuntimeError("Contact move mark disagrees with independent organization state")
            if bool(card.mark_button.winfo_viewable()) != (name == "group-organize-marked"):
                raise RuntimeError("Quiet contact cards expose move marks outside Organize")
    return {"group": workspace.group, "query": workspace.query, "mainCount": len(main), "reviewCount": len(review),
            "filteredCount": workspace.filtered_count, "pageStart": workspace.page_start,
            "visibleIds": [item["id"] for item in state.contacts], "selectedCount": len(state.selected_ids),
            "markedIds": sorted(state.marked_group_ids), "undoAvailable": state.group_undo is not None,
            "nativeSelectionLocked": locked}


@contextmanager
def callback_timings(root):
    """Attribute deferred Tk work in this synthetic harness, without product hooks."""
    metrics = {}
    root.qualification_callback_timings = metrics
    original = app.tk.CallWrapper.__call__

    def measured(callback, *args):
        function = callback.func
        if getattr(function, "__qualname__", "").endswith(".callit") and function.__closure__:
            for name, cell in zip(function.__code__.co_freevars, function.__closure__):
                if name == "func":
                    function = cell.cell_contents
                    break
        name = getattr(function, "__qualname__", type(function).__name__)
        entry = metrics.setdefault(name, {"callback": name, "count": 0, "totalSeconds": 0, "maxSeconds": 0})
        started = time.perf_counter()
        try:
            return original(callback, *args)
        finally:
            elapsed = time.perf_counter() - started
            entry["count"] += 1
            entry["totalSeconds"] += elapsed
            entry["maxSeconds"] = max(entry["maxSeconds"], elapsed)

    with patch.object(app.tk.CallWrapper, "__call__", measured):
        yield metrics


def measure_pump(root, view):
    """Measure actual production pump, plus an explicitly synthetic eager-render comparator."""
    view.state, view.native = inspected(), app.NativeRun(CLIENT)
    view.refresh()
    root.update()
    measurements = {}
    root.qualification_pump_measurements = measurements
    for eager in (True, False):
        callbacks = root.qualification_callback_timings
        callbacks.clear()
        durations, beats, renders, refresh_durations, eager_job = [], [], [], [], [None]
        original_refresh = view.refresh
        start_wall, start_cpu = time.perf_counter(), time.process_time()
        def refresh():
            started = time.perf_counter()
            renders.append(started)
            try:
                return original_refresh()
            finally:
                refresh_durations.append(time.perf_counter() - started)
        def heartbeat():
            now = time.perf_counter()
            if beats:
                durations.append(now - beats[-1])
            beats.append(now)
            if now - start_wall < 3.0:
                root.after(10, heartbeat)
        def eager_refresh():
            refresh()
            eager_job[0] = root.after(50, eager_refresh)
        with patch.object(view, "refresh", side_effect=refresh):
            view._next_visual_refresh = 0.0
            view.pump()
            if eager:
                eager_refresh()
            heartbeat()
            root.after(3100, root.quit)
            root.mainloop()
            scheduled = root.tk.splitlist(root.tk.call("after", "info"))
            if view._pump_after is None or scheduled.count(view._pump_after) != 1:
                raise RuntimeError("Production pump does not own exactly one scheduled continuation")
            root.after_cancel(view._pump_after)
            view._pump_after = None
            if eager_job[0] is not None:
                root.after_cancel(eager_job[0])
        wall = time.perf_counter() - start_wall
        measurement = {"wallSeconds": wall, "cpuSeconds": time.process_time() - start_cpu,
                       "fullRenders": len(renders), "heartbeatCount": len(beats),
                       "refreshMaxSeconds": max(refresh_durations, default=0),
                       "heartbeatMaxSeconds": max(durations),
                       "heartbeatP95Seconds": sorted(durations)[math.floor((len(durations) - 1) * .95)],
                       "deferredCallbacks": [dict(value) for value in sorted(callbacks.values(), key=lambda value: value["totalSeconds"], reverse=True)[:16]],
                       "singlePumpContinuation": True}
        measurements["syntheticEager20HzComparator" if eager else "productionDirtyRefresh"] = measurement
        if not eager and not 2 <= len(renders) <= math.ceil(wall) + 1:
            raise RuntimeError("Idle production pump performs unnecessary full rendering")
        if not eager and measurement["heartbeatMaxSeconds"] > .5:
            raise RuntimeError("Windows UI heartbeat stalled for more than 500ms")
    view.state, (view.native, _) = collecting(), started()
    view.refresh()
    queued, received = [], []
    handle = view.state.handle
    def observed(event):
        if event == {"kind": "progress", "value": 42}:
            received.append(time.perf_counter())
        return handle(event)
    with patch.object(view.state, "handle", side_effect=observed):
        view.pump()
        root.after(10, lambda: (queued.append(time.perf_counter()), view.events.put_nowait({"kind": "progress", "value": 42})))
        root.after(250, root.quit)
        root.mainloop()
        root.after_cancel(view._pump_after)
        view._pump_after = None
    if len(queued) != 1 or len(received) != 1 or received[0] - queued[0] > .2 or view.state.progress != 42:
        raise RuntimeError("Production pump failed to drain a queued progress event within 200ms")
    measurements["queuedEventLatencySeconds"] = received[0] - queued[0]
    measurements["comparisonBoundary"] = "Same frozen current UI; eager comparator is synthetic, not a historical RC21 benchmark."
    return measurements


def widget_diagnostic(root, widget):
    record = {"widget": str(widget), "class": widget.winfo_class(),
              "pythonType": type(widget).__name__, "bounds": bounds(root, widget),
              "requestedSize": [widget.winfo_reqwidth(), widget.winfo_reqheight()],
              "mapped": bool(widget.winfo_ismapped()), "viewable": bool(widget.winfo_viewable())}
    for key in ("takefocus", "state", "text", "style", "scrollregion"):
        if key in widget.keys():
            record[key] = str(widget.cget(key))[:512]
    record["bindings"] = list(widget.bind())
    if hasattr(widget, "yview"):
        record["yview"] = list(widget.yview())
    return record


def failure_context(root, view):
    """Describe only this runner-owned synthetic window, including clipping ancestors."""
    result = {}
    readers = {
        "clientSize": lambda: [root.winfo_width(), root.winfo_height()],
        "eventPump": lambda: root.qualification_event_guard.last_failure,
        "scheduledCallbacks": lambda: [list(root.tk.splitlist(root.tk.call("after", "info", job)))
                                         for job in root.tk.splitlist(root.tk.call("after", "info"))],
        "keyboardTraversal": lambda: getattr(root, "qualification_keyboard_trace", []),
        "pumpMeasurements": lambda: getattr(root, "qualification_pump_measurements", {}),
        "tkFocusOK": lambda: str(root.tk.call("info", "body", "::tk::FocusOK")),
        "controls": lambda: [widget_diagnostic(root, widget) for widget in descendants(root)
                             if isinstance(widget, (ttk.Button, ttk.Menubutton, ttk.Checkbutton, ttk.Entry))],
    }
    def focus_chain():
        widget = root.focus_get()
        chain = []
        while widget is not None:
            chain.append(widget_diagnostic(root, widget))
            widget = widget.master
        return chain
    readers["focusedWidgetAndAncestors"] = focus_chain
    if view is not None:
        readers["stagePanels"] = lambda: {
            name: {"panel": widget_diagnostic(root, panel),
                   "content": widget_diagnostic(root, panel.content)}
            for name, panel in view.stage_panels.items()}
        readers["scrollViewports"] = lambda: {
            name: widget_diagnostic(root, widget) for name, widget in
            (("details", view.details_canvas), ("contacts", view.contacts._canvas))}
    for name, reader in readers.items():
        try:
            result[name] = reader()
        except Exception as error:
            result[name] = {"diagnosticError": f"{type(error).__name__}: {error}"}
    return result


def source_proof(source, frozen_identity):
    paths = (*GUI_FILES, FONT, LOGO, LOCK, "collector/windows/tests/fixtures/native-setup-v2.json",
             *("collector/windows/tests/" + name for name in
               ("render_guided_journey.py", "render_contact_workspace.py", "native_setup_fixture.py", "frozen_guided_fixture.py")))
    return {"sourceCommit": frozen_identity["sourceCommit"] if frozen_identity else
            subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip(),
            "sha256ByPath": {path: hashlib.sha256((source / path).read_bytes()).hexdigest() for path in paths}}


def render(args, *, contacts_only=False, frozen_identity=None):
    if sys.platform != "win32" or os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("RUNNER_OS") != "Windows":
        raise RuntimeError("Guided fixture requires the disposable actual Windows CI desktop")
    collect_all = bool(getattr(args, "collect_all_failures", False))
    focused = bool(getattr(args, "diagnostic_contact_layout", False))
    if focused and (not collect_all or frozen_identity):
        raise RuntimeError("Focused diagnostics cannot replace the strict source/frozen qualification")
    if collect_all and frozen_identity:
        raise RuntimeError("Collect-all diagnostics are source-only; frozen qualification remains strict")
    args.output.mkdir(parents=True, exist_ok=False)
    identity = source_proof(args.source, frozen_identity)
    (args.output / "source-proof.json").write_text(json.dumps(identity, indent=2) + "\n", encoding="utf-8")
    records, styles, interactions, lifecycle, performance = [], [], [], [], None
    checks, failures, cases = [], [], []
    root = view = None
    errors = []
    reported_callbacks = 0
    width = height = scaling = None

    def check(name, phase, operation):
        nonlocal reported_callbacks
        metadata = {"scenario": name, "phase": phase, "viewport": [width, height], "tkScaling": scaling}
        started = time.perf_counter()
        if getattr(args, "trace_stalls", False):
            faulthandler.dump_traceback_later(60, repeat=True)
        print(json.dumps({**metadata, "checkStarted": True}), flush=True)
        try:
            result = operation()
            if len(errors) > reported_callbacks:
                raise RuntimeError("Tk callback failed: " + "; ".join(errors[reported_callbacks:]))
        except Exception as error:
            if not collect_all and getattr(error, "_guided_diagnostic_recorded", False):
                raise
            if not collect_all:
                error._guided_diagnostic_recorded = True
            failure = {**metadata, "errorType": type(error).__name__, "error": str(error),
                       "traceback": traceback.format_exc(), "callbackErrors": errors[reported_callbacks:],
                       "durationSeconds": time.perf_counter() - started}
            # Keep every callback in the viewport log, but attribute it once to
            # the nearest check, including callbacks before or outside a check.
            reported_callbacks = len(errors)
            if root is not None:
                failure["window"] = failure_context(root, view)
                try:
                    failure["capture"] = capture(root, args.output,
                        f"FAILED-{width}x{height}-scale-{scaling:.2f}-{name}-{phase}")
                except Exception as capture_error:
                    # Never mask the original failure when the visible-window guard
                    # correctly refuses an unavailable or off-desktop capture.
                    failure["captureError"] = f"{type(capture_error).__name__}: {capture_error}"
            failures.append(failure)
            checks.append({**metadata, "passed": False, "failureIndex": len(failures) - 1})
            for filename, contents in (("failure.json", failure), ("failures.json", failures)):
                (args.output / filename).write_text(json.dumps(contents, indent=2) + "\n", encoding="utf-8")
            print(json.dumps({**metadata, "failed": str(error)}), flush=True)
            if not collect_all:
                raise
            return False, None
        checks.append({**metadata, "passed": True, "durationSeconds": time.perf_counter() - started})
        return True, result

    for width, height, scaling in VIEWPORTS:
        root = view = None
        errors = []
        reported_callbacks = 0
        created, root = check("viewport", "create-root", create_root)
        if not created:
            continue
        if getattr(args, "trace_stalls", False):
            root.qualification_event_guard = EventPumpGuard(root)
        root.report_callback_exception = lambda kind, value, trace: errors.append(f"{kind.__name__}: {value}")

        def run_viewport():
            nonlocal view, performance
            root.tk.call("tk", "scaling", scaling)
            with synthetic_view(root, args.logo) as current_view:
                view = current_view
                place_viewport(root, width, height)
                check("viewport", "startup-callbacks", lambda: None)
                if not contacts_only:
                    passed, result = check("viewport", "button-interactions", lambda: exercise_controls(view))
                    interactions.append({"viewport": [width, height], "tkScaling": scaling,
                                         "passed": passed, "checks": result})
                    if (width, height, scaling) == VIEWPORTS[0]:
                        passed, result = check("viewport", "group-button-interactions", lambda: exercise_group_controls(view))
                        interactions.append({"viewport": [width, height], "tkScaling": scaling,
                                             "kind": "contact-groups", "passed": passed, "checks": result})
                for name, native, state in scenarios():
                    if focused and name not in CONTACT_LAYOUT_DIAGNOSTIC:
                        continue
                    if contacts_only and name not in ("contacts", "contacts-empty", "contacts-partial", "contacts-select-all", "reviewed-contacts"):
                        continue
                    case_start = len(failures)
                    stage = EXPECTED_STAGE[name]
                    panel = ("details" if name == "recovery" else
                             "save" if stage == "complete" else "approval" if stage == "prepare" else stage)
                    def prepare():
                        root.qualification_keyboard_trace = []
                        view.native_worker = SyntheticWorker()
                        view.native, view.state = native, state
                        view.guide_step = 2 if name in ("backup-unencrypted", "backup-unknown") else 0
                        view.render_guide()
                        view.native_checked.set(name == "approval-checked")
                        view.organizing = name == "group-organize-marked"
                        view._visible_workspace = "setup"
                        view._journey_stage = None
                        view.render_contacts()
                        view.refresh()
                        if name == "recovery":
                            view.show_workspace("details")
                        root.update()
                    prepared, _ = check(name, "render", prepare)
                    geometry, focus, scroll, groups, guide = None, [], None, None, None
                    if prepared:
                        check(name, "stage", lambda: assert_stage(root, view, name))
                        check(name, "actions", lambda: assert_actions(view, name))
                        if name.startswith("group-"):
                            _, groups = check(name, "contact-groups", lambda: assert_group_workspace(root, view, name))
                        _, geometry = check(name, "geometry", lambda: stage_geometry(root, view, panel))
                        if panel == "contacts":
                            def contact_scroll():
                                selected = set(state.selected_ids)
                                dock = {key: bounds(root, getattr(view, key)) for key in
                                        ("contacts", "contact_actions", "workspace_status_label")}
                                view.contacts.yview_moveto(1)
                                root.update()
                                if dock != {key: bounds(root, getattr(view, key)) for key in dock} or selected != state.selected_ids:
                                    raise RuntimeError("Contact scroll moved the fixed action dock or changed selection")
                                if len(state.contacts) > 20 and view.contacts.yview()[0] <= 0:
                                    raise RuntimeError("Contact cards did not scroll independently")
                                view.contacts.yview_moveto(0)
                                root.update()
                            check(name, "contact-scroll", contact_scroll)
                            if name in ("contacts", "group-main", "group-review", "group-empty-search", "group-organize-marked"):
                                _, focus = check(name, "keyboard", lambda: check_keyboard(root, view))
                        elif name == "approval-checked":
                            _, focus = check(name, "keyboard", lambda: keyboard_traversal(root,
                                (view.native_agreement, view.native_approve, view.native_cancel), start=view.native_disclosure))
                        elif name in ("backup-unencrypted", "backup-unknown"):
                            _, focus = check(name, "preparation-keyboard", lambda: keyboard_traversal(root,
                                (view.native_start, view.native_available, view.native_cancel, view.backup_check), start=view.native_start))
                            def guide_evidence():
                                proof = []
                                for step in (2, 3):
                                    view.guide_step = step
                                    view.render_guide()
                                    root.update()
                                    view.details_canvas.yview_moveto(1)
                                    root.update()
                                    require_bounds(root, view.guide_text, "backup guide text")
                                    for position in (0, 1):
                                        view.guide_text.yview_moveto(position)
                                        root.update()
                                        index = "1.0" if position == 0 else "end-2c"
                                        character = view.guide_text.bbox(index)
                                        if not character or min(character[:2]) < 0 or character[0] + character[2] > view.guide_text.winfo_width() or character[1] + character[3] > view.guide_text.winfo_height():
                                            raise RuntimeError("Backup guide text is not fully reachable")
                                        shot = capture(root, args.output, f"guide-{width}x{height}-scale-{scaling:.2f}-{name}-step-{step + 1}-{position}")
                                        proof.append({"step": step + 1, "position": position, "lastCharacterVisible": True, "screenshot": shot})
                                view.guide_step = 2
                                view.render_guide()
                                view.guide_text.yview_moveto(0)
                                return proof
                            _, guide = check(name, "encryption-guide", guide_evidence)
                        elif panel == "collection":
                            required = [view.stop_button, view.collection_details_button]
                            if name == "password":
                                required.extend((view.password, view.password_button))
                            _, focus = check(name, "keyboard", lambda: keyboard_traversal(root, required, start=view.stop_button))
                            def collection_scroll():
                                before = list(view.details_canvas.yview())
                                view.details_canvas.yview_moveto(1)
                                root.update()
                                return {"before": before, "after": list(view.details_canvas.yview()),
                                        "lastExplanation": require_bounds(root, view.collection_next, "collection next step")}
                            _, scroll = check(name, "collection-scroll", collection_scroll)
                        if name == "long-scope-error":
                            def disclosure_scroll():
                                content = view.native_disclosure.get("1.0", "end-1c")
                                evidence = text_scroll_evidence(root, view.native_disclosure)
                                evidence["containsFullScopeAndError"] = all(value in content for value in
                                    (native.claim.scope.email, native.claim.scope.course, native.message))
                                require_full_text_scroll(evidence)
                                view.native_disclosure.yview_moveto(0)
                                return evidence
                            _, scroll = check(name, "disclosure-scroll", disclosure_scroll)
                        def reset_scroll():
                            view.contacts.yview_moveto(0)
                            view.details_canvas.yview_moveto(0)
                            root.update()
                        check(name, "reset-scroll", reset_scroll)
                    # Capture is deliberately inside the diagnostic boundary too.
                    captured, record = check(name, "capture", lambda: capture(root, args.output,
                        f"guided-{width}x{height}-scale-{scaling:.2f}-{name}"))
                    if captured:
                        _, pixels = check(name, "rounded-fill-pixels", lambda: rounded_fill_pixels(root, view, args.output / record["path"]))
                        record["roundedFillPixels"] = pixels
                    passed = prepared and len(failures) == case_start
                    cases.append({"scenario": name, "viewport": [width, height], "tkScaling": scaling, "passed": passed})
                    if captured:
                        record.update(scenario=name, stage=journey(view.state, view.native).stage, panel=panel,
                                      geometry=geometry, tkScaling=float(root.tk.call("tk", "scaling")),
                                      keyboardTraversal=focus, scrollEvidence=scroll, selectedContacts=len(state.selected_ids),
                                      accountSaveAcknowledged=state.saved_acknowledged, contactGroups=groups,
                                      encryptionGuide=guide, qualified=passed)
                        records.append(record)
                def resolve_styles():
                    return {"viewport": [width, height], "tkScaling": float(root.tk.call("tk", "scaling")),
                            "nativeWindowDpi": visible_snapshot(root)["windowDpi"], "styles": computed_styles(root, view)}
                passed, resolved = check("viewport", "computed-styles", resolve_styles)
                if passed:
                    styles.append(resolved)
                if not contacts_only and (width, height, scaling) == VIEWPORTS[0]:
                    _, performance = check("viewport", "pump", lambda: measure_pump(root, view))
        try:
            check("viewport", "window", run_viewport)
        finally:
            view = None
            def destroy():
                root.destroy()
                pending_callbacks = root.tk.splitlist(root.tk.call("after", "info"))
                if pending_callbacks:
                    raise RuntimeError("Destroyed guided window retained scheduled callbacks: " + str(pending_callbacks))
                return {"viewport": [width, height], "tkScaling": scaling, "callbacksAfterDestroy": 0}
            passed, destroyed = check("viewport", "destroy", destroy)
            if passed:
                lifecycle.append(destroyed)

    status = ("actual-windows-source-guided-diagnostic" if collect_all else
              "actual-windows-frozen-synthetic-guided-ui" if frozen_identity else "actual-windows-source-synthetic-guided-ui")
    receipt = {"schema": 3, "status": status, "passed": not failures, "collectAllFailures": collect_all,
               "diagnosticScope": "contact-layout" if focused else "all",
               "sourceProof": identity, "plannedScenarioCount": (5 if contacts_only else len(CONTACT_LAYOUT_DIAGNOSTIC) if focused else len(EXPECTED_STAGE)) * len(VIEWPORTS),
               "guiSourceSha256": gui_digest(args.source), "productionIdentity": {"version": app.VERSION, "build": app.BUILD,
               "channel": app.CHANNEL, "bundleId": app.BUNDLE_ID}, "screenshots": records, "computedStylesByViewport": styles,
               "scenarioResults": cases, "checks": checks, "failureCount": len(failures),
               "failuresFile": "failures.json" if failures else None,
               "buttonInteractions": interactions, "windowLifecycle": lifecycle,
               "pumpMeasurement": performance, "python": platform.python_version(),
               "frozenFixture": frozen_identity, "realHelperCreated": False, "realAuthorityTransportCreated": False,
               "customerDataOrNetworkUsed": False, "physicalDpiAcceptance": False,
               "scaleBoundary": "Actual runner DPI recorded; 96dpi and 126dpi-equivalent Tk text scales are synthetic layout coverage.",
               "installedProductExecutableOrPhysicalPhoneAccountProved": False}
    destination = args.output / "render-guided-receipt.json"
    destination.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": status, "passed": not failures, "screenshots": len(records),
                      "failures": len(failures), "guiSourceSha256": receipt["guiSourceSha256"]}))
    if failures:
        raise RuntimeError(f"Guided source diagnostic failed {len(failures)} checks; see {destination}")
    return receipt


def main():
    parser = argparse.ArgumentParser()
    for name in ("source", "logo", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--collect-all-failures", action="store_true",
                        help="Source-only bounded census; retain every failure and exit nonzero if any check fails")
    parser.add_argument("--diagnostic-contact-layout", action="store_true",
                        help="Source diagnostic only: affected contact layouts, collection, and measured pump callbacks")
    args = parser.parse_args()
    args.trace_stalls = True
    # A stalled Tk callback must leave its Python stack in the CI log before
    # the step deadline; this only runs in the synthetic Windows harness.
    faulthandler.dump_traceback_later(60, repeat=True)
    try:
        render(args)
    finally:
        faulthandler.cancel_dump_traceback_later()


if __name__ == "__main__":
    main()
