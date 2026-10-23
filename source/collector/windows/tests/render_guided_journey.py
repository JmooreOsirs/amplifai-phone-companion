"""Actual Windows guided-stage evidence with only synthetic authority and helper inputs."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import platform
import sys
import time
from tkinter import font, ttk
from unittest.mock import Mock, patch

import app
from build_windows_candidate import gui_digest
from journey import journey
from native_setup import DISCLOSURE
from native_setup_fixture import (collecting, exercise_controls, require_full_text_scroll,
                                  scenarios, text_scroll_evidence)
from render_candidate import capture, create_root, descendants, place_viewport, visible_snapshot
from render_contact_workspace import (bounds, check_keyboard, computed_styles, keyboard_traversal,
                                      require_bounds, workspace_geometry)
from test_native_app import SyntheticWorker
from test_native_run import started
from test_native_setup import CLIENT
from test_session import inspected

VIEWPORTS = ((1180, 820, 4 / 3), (920, 749, 4 / 3), (680, 680, 4 / 3), (680, 680, 1.75))
EXPECTED_STAGE = {
    "account-idle": "account", "approval-unchecked": "approval", "approval-checked": "approval",
    "approval-uncertain": "approval", "prepared": "prepare", "start-uncertain": "prepare",
    "backup-encrypted": "prepare", "backup-unencrypted": "prepare",
    "connecting": "collection", "collecting": "collection", "password": "collection", "processing": "collection",
    "contacts": "contacts", "contacts-empty": "contacts", "contacts-partial": "contacts",
    "contacts-select-all": "contacts", "reviewed-contacts": "contacts", "continue-uncertain": "attention",
    "bind-uncertain": "attention", "bound": "save", "preview-received": "save", "pending-receipt": "save",
    "saved": "complete", "recovery": "contacts", "collection-error": "attention", "long-scope-error": "approval",
    "authority-paused": "attention", "authority-refused": "attention", "authority-cancelled": "attention",
    "authority-expired": "attention", "handoff-expired": "attention",
    "legacy-awaiting-destination": "save", "legacy-pending-destination": "save",
}


@contextmanager
def synthetic_view(root, logo):
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
    expected_panel = ("details" if name == "recovery" or current.stage == "attention" else
                      "save" if current.stage == "complete" else "approval" if current.stage == "prepare" else current.stage)
    visible = [key for key, panel in view.stage_panels.items() if panel.winfo_ismapped()]
    expected = [] if expected_panel == "contacts" else [expected_panel]
    if visible != expected or bool(view.contact_workspace.winfo_ismapped()) != (expected_panel == "contacts"):
        raise RuntimeError(f"Unrelated stage is visible in {name}: {visible}")
    labels = [item.cget("text") for item in view.journey_steps]
    if labels != ["1  Approve", "2  Collect", "3  Choose", "4  Save & report"] or any(isinstance(child, ttk.Button) for child in view.navigation.winfo_children()):
        raise RuntimeError("The guided four-step journey reverted to workspace navigation")
    for index, item in enumerate(view.journey_steps):
        require_bounds(root, item, "journey step")
        if (item.cget("style") == "Current.Journey.TLabel") != (index == current.step):
            raise RuntimeError("Journey indicator does not match the active stage")
    for attribute in ("legacy_consent", "agreement", "approve", "connect"):
        widget = getattr(view, attribute, None)
        if widget is not None and widget.winfo_ismapped():
            raise RuntimeError("Legacy local approval leaked into the guided flow")
    if expected_panel != "details":
        for widget in (view.inspect, view.clear, view.copy_code_button, view.open_account_button, view.force_button):
            if widget.winfo_ismapped():
                raise RuntimeError("Manual recovery controls leaked into a normal stage")
    if current.stage == "collection":
        for widget in (view.native_agreement, view.native_approve, view.native_start, view.contacts, view.pair_button):
            if widget.winfo_ismapped():
                raise RuntimeError("Unrelated approval/selection control is visible while collecting")
        if bool(view.password.winfo_ismapped()) != (name == "password"):
            raise RuntimeError("Backup password entry is shown outside the password stage")
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
        return workspace_geometry(root, view)
    container = view.stage_panels[panel]
    x, _y, width, _height = bounds(root, container)
    if x < 0 or width <= 1 or x + width > root.winfo_width():
        raise RuntimeError("Current stage panel is clipped horizontally")
    records = {"panel": bounds(root, container), "recovery_toggle": require_bounds(root, view.recovery_toggle, "recovery toggle")}
    # Scrollable setup/recovery controls must be horizontally sound and become
    # fully visible when reached by native keyboard focus.
    for widget in descendants(container):
        if isinstance(widget, (ttk.Button, ttk.Checkbutton, ttk.Entry)) and widget.winfo_ismapped():
            box = bounds(root, widget)
            if box[0] < x or box[0] + box[2] > x + width or box[2] < widget.winfo_reqwidth():
                raise RuntimeError(f"Current-stage control is horizontally clipped: {widget}: {box}")
            records[str(widget)] = box
    return records


def assert_actions(view, name):
    if name in ("approval-unchecked", "approval-checked"):
        expected = "normal" if name == "approval-checked" else "disabled"
        if str(view.native_approve.cget("state")) != expected:
            raise RuntimeError("Approval primary action does not respect the unchecked agreement")
    if name == "prepared" and str(view.native_start.cget("state")) != "normal":
        raise RuntimeError("Prepared run has no enabled Start action")
    if name == "contacts-empty" and str(view.review_button.cget("state")) != "disabled":
        raise RuntimeError("Empty selection exposes enabled Continue")
    if name in ("contacts", "reviewed-contacts"):
        primary = view.review_button if name == "contacts" else view.pair_button
        if primary.cget("style") != "Primary.TButton" or str(primary.cget("state")) != "normal":
            raise RuntimeError("The contact action dock has no enabled branded primary Continue")
    if name in ("continue-uncertain", "bind-uncertain"):
        if not view.recovery_retry_button.winfo_ismapped() or str(view.contacts.cget("state")) != "disabled":
            raise RuntimeError("Uncertain Continue lost recovery or unlocked the approved selection")
    if name == "start-uncertain" and (view.native_agreement.winfo_ismapped() or not view.native_retry.winfo_ismapped()):
        raise RuntimeError("Uncertain Start re-asked approval or hid retry")
    if name == "authority-paused" and not view.recovery_retry_button.winfo_ismapped():
        raise RuntimeError("Paused retained review lost confirmation recovery")
    if name.startswith("legacy-") and view.save_heading.cget("text") != "Connect your account":
        raise RuntimeError("Legacy local handoff incorrectly claims an approved account connection")
    if name == "pending-receipt" and view.state.saved_acknowledged:
        raise RuntimeError("Pending receipt was presented as an account Save")
    if name == "saved" and view.save_account_button.cget("text") != "Open report":
        raise RuntimeError("Saved receipt has no bound report action")
    if name == "collecting" and ("backup transfer only" not in view.progress_caption.get()
                                  or "not been read" not in view.coverage_status.get()):
        raise RuntimeError("Device 100% is incorrectly presented as complete source or account coverage")


def measure_pump(root, view):
    """Measure actual production pump, plus an explicitly synthetic eager-render comparator."""
    view.state, view.native = inspected(), app.NativeRun(CLIENT)
    view.refresh()
    root.update()
    measurements = {}
    for eager in (True, False):
        durations, beats, renders, eager_job = [], [], [], [None]
        original_refresh = view.refresh
        start_wall, start_cpu = time.perf_counter(), time.process_time()
        def refresh():
            renders.append(time.perf_counter())
            return original_refresh()
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
                       "heartbeatMaxSeconds": max(durations),
                       "heartbeatP95Seconds": sorted(durations)[math.floor((len(durations) - 1) * .95)],
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


def render(args, *, contacts_only=False, frozen_identity=None):
    if sys.platform != "win32" or os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("RUNNER_OS") != "Windows":
        raise RuntimeError("Guided fixture requires the disposable actual Windows CI desktop")
    args.output.mkdir(parents=True, exist_ok=False)
    records, styles, interactions, lifecycle, performance = [], [], [], [], None
    for width, height, scaling in VIEWPORTS:
        root = create_root()
        errors = []
        root.report_callback_exception = lambda kind, value, trace: errors.append(f"{kind.__name__}: {value}")
        try:
            root.tk.call("tk", "scaling", scaling)
            with synthetic_view(root, args.logo) as view:
                place_viewport(root, width, height)
                if not contacts_only:
                    interactions.append({"viewport": [width, height], "tkScaling": scaling,
                                         "checks": exercise_controls(view)})
                for name, native, state in scenarios():
                    if contacts_only and name not in ("contacts", "contacts-empty", "contacts-partial", "contacts-select-all", "reviewed-contacts"):
                        continue
                    view.native_worker = SyntheticWorker()
                    view.native, view.state = native, state
                    view.native_checked.set(name == "approval-checked")
                    view._visible_workspace = "setup"
                    view._journey_stage = None
                    view.render_contacts()
                    view.refresh()
                    if name == "recovery":
                        view.show_workspace("details")
                    root.update()
                    current, panel = assert_stage(root, view, name)
                    assert_actions(view, name)
                    geometry = stage_geometry(root, view, panel)
                    focus, scroll = [], None
                    if panel == "contacts":
                        selected = set(state.selected_ids)
                        view.contacts.yview_moveto(1)
                        root.update()
                        if geometry != stage_geometry(root, view, panel) or selected != state.selected_ids:
                            raise RuntimeError("Contact scroll moved the fixed action dock or changed selection")
                        if len(state.contacts) > 20 and view.contacts.yview()[0] <= 0:
                            raise RuntimeError("Contact cards did not scroll independently")
                        if name == "contacts":
                            focus = check_keyboard(root, view)
                        view.contacts.yview_moveto(0)
                    elif name == "approval-checked":
                        focus = keyboard_traversal(root, (view.native_agreement, view.native_approve, view.native_cancel),
                                                   start=view.native_disclosure)
                    if name == "long-scope-error":
                        content = view.native_disclosure.get("1.0", "end-1c")
                        scroll = text_scroll_evidence(root, view.native_disclosure)
                        scroll["containsFullScopeAndError"] = all(value in content for value in
                            (native.claim.scope.email, native.claim.scope.course, native.message))
                        require_full_text_scroll(scroll)
                        view.native_disclosure.yview_moveto(0)
                    view.details_canvas.yview_moveto(0)
                    root.update()
                    record = capture(root, args.output, f"guided-{width}x{height}-scale-{scaling:.2f}-{name}")
                    record.update(scenario=name, stage=current.stage, panel=panel, geometry=geometry,
                                  tkScaling=float(root.tk.call("tk", "scaling")), keyboardTraversal=focus,
                                  scrollEvidence=scroll, selectedContacts=len(state.selected_ids),
                                  accountSaveAcknowledged=state.saved_acknowledged)
                    records.append(record)
                    if errors:
                        raise RuntimeError("Tk callback failed: " + "; ".join(errors))
                styles.append({"viewport": [width, height], "tkScaling": float(root.tk.call("tk", "scaling")),
                               "nativeWindowDpi": visible_snapshot(root)["windowDpi"], "styles": computed_styles(root, view)})
                if not contacts_only and (width, height, scaling) == VIEWPORTS[0]:
                    performance = measure_pump(root, view)
                if errors:
                    raise RuntimeError("Tk callback failed: " + "; ".join(errors))
        finally:
            root.destroy()
            pending_callbacks = root.tk.splitlist(root.tk.call("after", "info"))
            if pending_callbacks:
                raise RuntimeError("Destroyed guided window retained scheduled callbacks: " + str(pending_callbacks))
            lifecycle.append({"viewport": [width, height], "tkScaling": scaling, "callbacksAfterDestroy": 0})
    receipt = {"schema": 2, "status": "actual-windows-frozen-synthetic-guided-ui" if frozen_identity else "actual-windows-source-synthetic-guided-ui",
               "guiSourceSha256": gui_digest(args.source), "productionIdentity": {"version": app.VERSION, "build": app.BUILD,
               "channel": app.CHANNEL, "bundleId": app.BUNDLE_ID}, "screenshots": records, "computedStylesByViewport": styles,
               "buttonInteractions": interactions, "windowLifecycle": lifecycle,
               "pumpMeasurement": performance, "python": platform.python_version(),
               "frozenFixture": frozen_identity, "realHelperCreated": False, "realAuthorityTransportCreated": False,
               "customerDataOrNetworkUsed": False, "physicalDpiAcceptance": False,
               "scaleBoundary": "Actual runner DPI recorded; 96dpi and 126dpi-equivalent Tk text scales are synthetic layout coverage.",
               "installedProductExecutableOrPhysicalPhoneAccountProved": False}
    destination = args.output / "render-guided-receipt.json"
    destination.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": receipt["status"], "screenshots": len(records), "guiSourceSha256": receipt["guiSourceSha256"]}))
    return receipt


def main():
    parser = argparse.ArgumentParser()
    for name in ("source", "logo", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    render(parser.parse_args())


if __name__ == "__main__":
    main()
