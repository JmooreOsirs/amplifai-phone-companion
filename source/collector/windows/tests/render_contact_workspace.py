"""Actual Windows contact-workspace geometry; synthetic data, no helper/network."""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
from pathlib import Path
from tkinter import font, ttk
from unittest.mock import patch

import app
from build_windows_candidate import gui_digest
from render_candidate import HANDOFF_ID, ImageGrab, capture, create_root, place_viewport, selected_review, visible_snapshot
from session import Session


def captured(*, empty=False, partial=False) -> Session:
    state = Session()
    state.begin("inspect"); state.handle({"kind": "residue", "sessions": []}); state.exited(0)
    state.approve(True); state.begin("connect")
    state.handle({"kind": "capture", "contacts": [] if empty else [
        {"id": index, "name": f"Synthetic contact {index}", "phoneCount": 1, "phoneEnds": ["0000"]}
        for index in range(1, 201)], "query": "", "cursor": 0,
        "nextCursor": None if empty else 200, "totalContacts": 0 if empty else 1_205,
        "availableCalls": 0, "availableMessages": 0, "missing": ["calls"] if partial else [],
        "unavailableReasons": {"calls": "selected_payload_length"} if partial else {}})
    if not empty:
        state.update_visible_selection([1, 200])
    return state


def scenarios() -> dict[str, Session]:
    loading = Session()
    loading.begin("inspect"); loading.handle({"kind": "residue", "sessions": []}); loading.exited(0)
    loading.approve(True); loading.begin("connect")
    states = {"populated-readable-zero": captured(), "empty": captured(empty=True), "loading": loading,
              "partial-unavailable": captured(partial=True), "review": selected_review()}
    selecting = captured()
    selecting.select_all_contacts()
    states["all-page-check"] = selecting
    for name, acknowledgment in (("preview-received", "received"),
                                  ("pending-receipt", "saved_pending_browser_receipt"),
                                  ("saved-recovery", "saved")):
        state = selected_review()
        state.poll()
        state.handle({"kind": "handoff", "state": acknowledgment, "handoffId": HANDOFF_ID})
        if name == "saved-recovery":
            state.finish(); state.handle({"kind": "state", "state": "completed"}); state.exited(0)
            state.begin("inspect")
            state.handle({"kind": "residue", "sessions": [{"name": "session-" + "a" * 32, "bytes": 4096}]})
            state.exited(0)
        states[name] = state
    error = captured(partial=True)
    error.phase = "error"
    error.status = ("Synthetic local read failed. The selected source is unavailable; it is not zero activity. "
                    "Keep the current review open and inspect temporary data after the helper stops. ") * 16
    states["long-error"] = error
    return states


def bounds(root, widget) -> list[int]:
    return [widget.winfo_rootx() - root.winfo_rootx(), widget.winfo_rooty() - root.winfo_rooty(),
            widget.winfo_width(), widget.winfo_height()]


def workspace_geometry(root, view) -> dict:
    controls = {**{f"navigation-{name}": widget for name, widget in view.workspace_buttons.items()},
                **{name: getattr(view, name) for name in (
                    "workspace_context", "search_entry", "search_button", "select_all_button",
                    "cancel_select_all_button", "clear_all_button", "previous_button", "next_button",
                    "page_label", "contacts", "workspace_status_label", "review_button", "pair_button",
                    "details_button", "change_selection_button", "account_recovery_button")}}
    records = {name: bounds(root, widget) for name, widget in controls.items() if widget.winfo_ismapped()}
    for name, (x, y, width, height) in records.items():
        if min(x, y) < 0 or width <= 1 or height <= 1 or x + width > root.winfo_width() or y + height > root.winfo_height():
            raise RuntimeError(f"Contact workspace control clipped: {name}: {records[name]}")
        if isinstance(controls[name], ttk.Button) and width < controls[name].winfo_reqwidth():
            raise RuntimeError(f"Contact workspace button text clipped: {name}")
    line = font.Font(root=root, font=view.contacts.cget("font")).metrics("linespace")
    if records["contacts"][3] < 3 * line:
        raise RuntimeError("Contact workspace leaves fewer than three readable list rows")
    list_bottom = records["contacts"][1] + records["contacts"][3]
    if list_bottom > records["workspace_status_label"][1]:
        raise RuntimeError("List overlaps fixed status/actions")
    if view.details_pane.winfo_ismapped():
        raise RuntimeError("Scrollable details pane leaked into contact workspace")
    return records


def native_disclosure_states(style) -> dict:
    name = "Native.Disclosure.TCheckbutton"
    result = {"map": {}, "resolved": {}}
    for key, expected in (("background", app.BACKGROUND), ("foreground", app.MUTED)):
        mapping = [list(map(str, item)) for item in style.map(name, key)]
        if mapping != [["disabled", expected]]:
            raise RuntimeError(f"Native disclosure disabled map changed: {key}: {mapping}")
        result["map"][key] = mapping
        for state in (("disabled",), ("disabled", "selected"), ("disabled", "active"), ("disabled", "focus")):
            actual = str(style.lookup(name, key, state=state))
            if actual != expected:
                raise RuntimeError(f"Native disclosure disabled color changed: {state}.{key}: {actual}")
            result["resolved"].setdefault(" ".join(state), {})[key] = actual
    return result


def computed_styles(root, view) -> dict:
    style = ttk.Style(root)
    expected = {
        "TFrame": {"background": app.BACKGROUND},
        "TLabel": {"background": app.BACKGROUND, "foreground": app.BODY, "font": ("Arial", "12")},
        "Heading.TLabel": {"foreground": app.HEADING, "font": ("Arial", "22", "bold")},
        "Primary.TButton": {"background": app.LIME, "foreground": app.BACKGROUND},
        "TButton": {"background": app.PANEL, "foreground": app.HEADING, "bordercolor": app.BORDER,
                    "padding": ("16", "12"), "font": ("Arial", "11", "bold")},
        "TCheckbutton": {"background": app.BACKGROUND, "foreground": app.BODY, "font": ("Arial", "12")},
        "Disclosure.TCheckbutton": {"background": app.BACKGROUND, "foreground": app.BODY, "font": ("Arial", "12")},
        "Native.Disclosure.TCheckbutton": {"background": app.BACKGROUND, "foreground": app.BODY, "font": ("Arial", "12")},
        "TEntry": {"fieldbackground": app.PANEL, "foreground": app.HEADING, "padding": ("8",)},
    }
    if style.theme_use() != "clam":
        raise RuntimeError("Native theme changed")
    result = {}
    for name, properties in expected.items():
        result[name] = {}
        for key, value in properties.items():
            actual = style.lookup(name, key)
            result[name][key] = str(actual)
            actual = tuple(map(str, root.tk.splitlist(actual))) if isinstance(value, tuple) else str(actual)
            if actual != value:
                raise RuntimeError(f"Accepted native style changed: {name}.{key}: {actual}")
    list_style = {key: str(view.contacts.cget(key)) for key in (
        "background", "foreground", "selectbackground", "selectforeground", "highlightbackground",
        "highlightcolor", "highlightthickness", "font", "activestyle", "exportselection")}
    for key, expected_value in {"background": app.PANEL, "foreground": app.HEADING,
                                "selectbackground": app.BLUE, "selectforeground": app.BACKGROUND,
                                "highlightbackground": app.BORDER, "highlightcolor": app.LIME,
                                "activestyle": "underline", "exportselection": "0"}.items():
        if list_style[key] != expected_value:
            raise RuntimeError(f"Accepted list/focus style changed: {key}")
    if tuple(root.tk.splitlist(list_style["font"])) != ("Arial", "12"):
        raise RuntimeError("Accepted list font changed")
    result["Listbox"] = list_style
    result["buttonStateMap"] = {key: [list(map(str, item)) for item in style.map("TButton", key)]
                                for key in ("background", "foreground")}
    result["nativeDisclosureStates"] = native_disclosure_states(style)
    return result


def check_keyboard(root, view) -> list[str]:
    root.focus_force()
    view.workspace_buttons["contacts"].focus_set()
    root.update()
    seen = []
    for _ in range(40):
        focused = root.focus_get()
        if focused is None:
            raise RuntimeError("Native keyboard focus was lost")
        name = str(focused)
        if name in seen:
            break
        seen.append(name)
        focused.event_generate("<KeyPress-Tab>")
        focused.event_generate("<KeyRelease-Tab>")
        root.update()
    for widget in (view.search_entry, view.contacts, view.review_button, view.account_recovery_button):
        if str(widget) not in seen:
            raise RuntimeError(f"Keyboard cannot reach contact control: {widget}")
    return seen


def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("Actual Windows desktop required; do not launch Tk on another platform")
    parser = argparse.ArgumentParser()
    for name in ("source", "logo", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--normal-open-only", action="store_true")
    mode.add_argument("--native-setup", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    root = create_root()
    if args.native_setup:
        from native_setup_fixture import render
        render(args, root)
        return
    if args.normal_open_only:
        with patch.object(app, "HelperProcess"), patch.object(app, "CompanionStatusServer"), patch.object(app, "cleanup_owned_updates"), patch.object(app.PhoneWindow, "pump"), patch.object(root, "geometry", wraps=root.geometry) as geometry:
            view = app.PhoneWindow(root, Path("synthetic-helper-not-launched"), args.logo)
        root.update()
        receipt = {"status": "normal-opening-measurement", "guiSourceSha256": gui_digest(args.source),
                   "geometryRequests": [call.args[0] for call in geometry.call_args_list if call.args],
                   "desktop": root.capture_desktop.snapshot(root), "fixtureResizedWindow": False,
                   "fixtureChangedDesktop": False, "physicalDpiAcceptance": False}
        # Preserve negative evidence without presenting it as a visibility-qualified capture.
        diagnostic_path = args.output / "windows-normal-open-unqualified.png"
        pixels = ImageGrab.grab(bbox=tuple(receipt["desktop"]["clientBounds"]), all_screens=True)
        pixels.save(diagnostic_path)
        receipt["diagnosticScreenshot"] = {"path": diagnostic_path.name, "size": list(pixels.size),
                                            "sha256": hashlib.sha256(diagnostic_path.read_bytes()).hexdigest(),
                                            "visibilityQualified": False}
        try:
            visible_snapshot(root)
            view.state = captured()
            view.render_contacts(); view.refresh(); root.update()
            receipt["contactGeometry"] = workspace_geometry(root, view)
            receipt["screenshot"] = capture(root, args.output, "normal-open-contacts")
            receipt["visible"] = True
        except RuntimeError as error:
            receipt.update({"visible": False, "failure": str(error)})
        (args.output / "normal-open-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        root.destroy()
        if not receipt["visible"]:
            raise SystemExit(receipt["failure"])
        return
    records = []
    style_records = []
    for width, height, scaling in ((920, 749, 4 / 3), (680, 680, 4 / 3), (680, 680, 1.75)):
        root.tk.call("tk", "scaling", scaling)
        with patch.object(app, "HelperProcess"), patch.object(app, "CompanionStatusServer"), patch.object(app, "cleanup_owned_updates"), patch.object(app.PhoneWindow, "pump"):
            view = app.PhoneWindow(root, Path("synthetic-helper-not-launched"), args.logo)
        place_viewport(root, width, height)
        styles = computed_styles(root, view)
        style_records.append({"viewport": [width, height], "tkScaling": root.tk.call("tk", "scaling"), "styles": styles})
        for name, state in scenarios().items():
            view.state = state
            view.render_contacts(); view.refresh(); view.show_workspace("contacts"); root.update()
            before = workspace_geometry(root, view)
            selected = set(state.selected_ids)
            view.contacts.yview_moveto(1.0); root.update()
            after = workspace_geometry(root, view)
            if before != after or state.selected_ids != selected:
                raise RuntimeError("List scrolling moved fixed controls or changed selection")
            if len(state.contacts) > 20 and view.contacts.yview()[0] <= 0:
                raise RuntimeError("Contact list did not scroll independently")
            scrolled = view.contacts.yview()
            if state.saved_acknowledged or state.save_receipt_pending:
                for control in (view.pair_button, view.change_selection_button):
                    if str(control.cget("state")) != "disabled":
                        raise RuntimeError("Receipt state exposed a replacement action")
            focus = check_keyboard(root, view) if name == "populated-readable-zero" else []
            view.contacts.yview_moveto(0); root.update()
            record = capture(root, args.output, f"{width}x{height}-scale-{scaling:.2f}-{name}")
            record.update({"scenario": name, "tkScaling": root.tk.call("tk", "scaling"),
                           "geometry": before, "listViewAfterScroll": scrolled,
                           "fixedDuringListScroll": True, "keyboardTraversal": focus,
                           "coverage": view.workspace_coverage.get(), "status": view.workspace_status.get()})
            records.append(record)
            if name == "long-error":
                view.show_workspace("details"); root.update()
                if view.status.get() != state.status:
                    raise RuntimeError("Full error disappeared from Details")
                records.append(capture(root, args.output, f"{width}x{height}-scale-{scaling:.2f}-error-details"))
                view.stop_button.focus_set(); root.update()
                x, y, w, h = bounds(root, view.stop_button)
                if min(x, y) < 0 or y + h > height or x + w > width:
                    raise RuntimeError("Tab/focus cannot reveal the details recovery action")
        view.show_workspace("setup"); root.update()
        for control in (view.approve, view.connect, view.decline_button, view.privacy_button, view.agreement):
            x, _y, w, _h = bounds(root, control)
            if x < 0 or x + w > width or w < control.winfo_reqwidth():
                raise RuntimeError(f"Setup disclosure/action is horizontally clipped: {control}")
        records.append(capture(root, args.output, f"{width}x{height}-scale-{scaling:.2f}-setup"))
        for child in root.winfo_children():
            child.destroy()
    receipt = {"status": "actual-windows-tk-synthetic-contact-workspace", "guiSourceSha256": gui_digest(args.source),
               "screenshots": records, "computedStylesByViewport": style_records, "pythonVersion": platform.python_version(),
               "tclVersion": root.tk.call("info", "patchlevel"), "tkVersion": root.tk.call("package", "provide", "Tk"),
               "windowingSystem": root.tk.call("tk", "windowingsystem"),
               "physicalDpiScreenReaderPhoneAccountOrInstallerProved": False}
    (args.output / "render-contact-workspace-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    root.destroy()
    print(json.dumps({"status": receipt["status"], "screenshots": len(records), "guiSourceSha256": receipt["guiSourceSha256"]}))


if __name__ == "__main__":
    main()
