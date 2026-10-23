"""Stage-aware Windows geometry and native style checks; no product test hooks."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from tkinter import font, ttk

import app
from brand_widgets import ContactCards, RoundPanel
from build_windows_candidate import gui_digest
from render_candidate import capture, create_root, visible_snapshot
from session import Session


def captured(*, empty=False, partial=False) -> Session:
    state = Session()
    state.begin("inspect")
    state.handle({"kind": "residue", "sessions": []})
    state.exited(0)
    state.approve(True)
    state.begin("connect")
    state.handle({"kind": "capture", "contacts": [] if empty else [
        {"id": index, "name": f"Synthetic contact {index}", "phoneCount": 1, "phoneEnds": ["0000"]}
        for index in range(1, 201)], "query": "", "cursor": 0,
        "nextCursor": None if empty else 200, "totalContacts": 0 if empty else 1_205,
        "availableCalls": 0, "availableMessages": 0, "missing": ["calls"] if partial else [],
        "unavailableReasons": {"calls": "selected_payload_length"} if partial else {}})
    if not empty:
        state.update_visible_selection([1, 200])
    return state


def bounds(root, widget) -> list[int]:
    return [widget.winfo_rootx() - root.winfo_rootx(), widget.winfo_rooty() - root.winfo_rooty(),
            widget.winfo_width(), widget.winfo_height()]


def require_bounds(root, widget, name):
    box = bounds(root, widget)
    x, y, width, height = box
    if (not widget.winfo_ismapped() or min(x, y) < 0 or min(width, height) <= 1
            or x + width > root.winfo_width() or y + height > root.winfo_height()):
        raise RuntimeError(f"Current-stage control clipped or hidden: {name}: {box}")
    if isinstance(widget, ttk.Button) and width < widget.winfo_reqwidth():
        raise RuntimeError(f"Current-stage button text clipped: {name}")
    return box


def workspace_geometry(root, view) -> dict:
    if not isinstance(view.contacts, ContactCards):
        raise RuntimeError("Contact workspace did not use the production contact-card component")
    names = ("contact_heading", "contact_selected", "contact_total", "workspace_context", "search_entry", "search_button", "select_all_button",
             "cancel_select_all_button", "clear_all_button", "previous_button", "next_button",
             "page_label", "contacts", "workspace_status_label", "contact_actions", "review_button",
             "pair_button", "details_button", "change_selection_button")
    records = {name: require_bounds(root, getattr(view, name), name) for name in names
               if getattr(view, name).winfo_ismapped()}
    if "contacts" not in records or "contact_actions" not in records:
        raise RuntimeError("Contact grid or action dock is missing")
    for name in ("contact_heading", "contact_selected", "contact_total"):
        if name not in records or records[name][2] < getattr(view, name).winfo_reqwidth():
            raise RuntimeError(f"Contact heading or selection count is clipped: {name}")
    if (str(view.contact_heading.cget("text")) != "Choose contacts"
            or str(view.contact_selected.cget("text")) != f"{len(view.state.selected_ids):,} selected"
            or str(view.contact_total.cget("text")) != f"of {view.state.total_contacts:,}"):
        raise RuntimeError("Contact heading or count does not match the current selection")
    line = font.Font(root=root, font=view.contacts.cget("font")).metrics("linespace")
    if records["contacts"][3] < 3 * line:
        raise RuntimeError("Contact workspace leaves no readable contact card viewport")
    if records["contacts"][1] + records["contacts"][3] > records["workspace_status_label"][1]:
        raise RuntimeError("Contact grid overlaps the fixed status/action dock")
    if view.details_pane.winfo_ismapped():
        raise RuntimeError("Setup or recovery pane leaked into the contact workspace")
    return records


def native_disclosure_states(style) -> dict:
    name = "Native.Disclosure.TCheckbutton"
    result = {"map": {}, "resolved": {}}
    for key, expected in (("background", app.PANEL), ("foreground", app.MUTED)):
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
    scale = float(root.tk.call("tk", "scaling")) / (96 / 72)
    spacing = lambda value: str(round(value * scale))
    if style.theme_use() != "clam":
        raise RuntimeError("Native theme changed")
    if view.body_font != "Open Sans":
        raise RuntimeError("The bundled Open Sans font was not registered on Windows")
    expected = {
        "TFrame": {"background": app.BACKGROUND},
        "Panel.TFrame": {"background": app.PANEL},
        "TLabel": {"background": app.BACKGROUND, "foreground": app.BODY, "font": ("Open Sans", "12")},
        "Heading.TLabel": {"foreground": app.HEADING, "font": ("Arial", "21", "bold")},
        "Panel.Heading.TLabel": {"background": app.PANEL, "foreground": app.HEADING,
                                 "font": ("Arial", "21", "bold")},
        "Selected.TLabel": {"background": app.PANEL, "foreground": app.LIME,
                             "font": ("Arial", "18", "bold")},
        "Panel.Muted.TLabel": {"background": app.PANEL, "foreground": app.MUTED},
        "Primary.TButton": {"background": app.LIME, "foreground": app.BACKGROUND},
        "TButton": {"background": app.BORDER, "foreground": app.HEADING, "bordercolor": app.BORDER,
                    "padding": (spacing(16), spacing(10)), "font": ("Arial", "11", "bold")},
        "TEntry": {"fieldbackground": app.BACKGROUND, "foreground": app.HEADING, "padding": (spacing(8),)},
    }
    result = {}
    for name, properties in expected.items():
        result[name] = {}
        for key, expected_value in properties.items():
            actual = style.lookup(name, key)
            result[name][key] = str(actual)
            value = tuple(map(str, root.tk.splitlist(actual))) if isinstance(expected_value, tuple) else str(actual)
            if value != expected_value:
                raise RuntimeError(f"Mac-derived native style drift: {name}.{key}: {value}")
    for name in ("TLabel", "Heading.TLabel"):
        resolved = font.Font(root=root, font=style.lookup(name, "font"))
        result[name]["resolvedFont"] = {**resolved.actual(), "linePixels": resolved.metrics("linespace")}
    result["primaryStates"] = {" ".join(state) or "normal": {
        key: str(style.lookup("Primary.TButton", key, state=state)) for key in ("background", "foreground")}
        for state in ((), ("disabled",), ("active",), ("focus",))}
    if result["primaryStates"]["disabled"] != {"background": app.BORDER, "foreground": app.MUTED}:
        raise RuntimeError("Disabled primary action lost its native semantic state")
    result["nativeDisclosureStates"] = native_disclosure_states(style)
    result["panels"] = {}
    for name, panel in view.stage_panels.items():
        if not isinstance(panel, RoundPanel):
            raise RuntimeError("Guided panel did not derive from the accepted rounded panel")
        result["panels"][name] = {"radiusPixels": panel.radius, "paddingPixels": panel.padding,
                                   "scale": panel.scale, "fill": panel._fill, "border": panel._border}
        if panel.radius != round(12 * panel.scale) or panel.padding != round(20 * panel.scale):
            raise RuntimeError("Guided panel radius or spacing drifted from the Mac reference")
    result["layoutSpacing"] = {"scale": scale, "shellPaddingPixels": [int(str(value)) for value in root.tk.splitlist(view.shell.cget("padding"))],
                              "navigationGapPixels": [int(str(value)) for value in root.tk.splitlist(view.navigation.grid_info()["pady"])],
                              "logicalRhythm": [12, 20, 28]}
    if (result["layoutSpacing"]["shellPaddingPixels"] != [round(28 * scale)]
            or result["layoutSpacing"]["navigationGapPixels"] != [0, round(20 * scale)]):
        raise RuntimeError("Outer spacing diverged from the DPI-scaled Mac rhythm")
    result["contactCards"] = {"type": type(view.contacts).__name__, "font": list(view.contacts.cget("font")),
                               "columns": view.contacts._columns, "scale": view.contacts.scale}
    return result


def keyboard_traversal(root, required, *, start):
    root.focus_force()
    start.focus_set()
    root.update()
    seen = []
    for _ in range(260):
        focused = root.focus_get()
        if focused is None:
            raise RuntimeError("Native keyboard focus was lost")
        name = str(focused)
        if name in seen:
            break
        seen.append(name)
        require_bounds(root, focused, "keyboard focus")
        focused.event_generate("<KeyPress-Tab>")
        focused.event_generate("<KeyRelease-Tab>")
        root.update()
    else:
        raise RuntimeError("Native keyboard traversal did not complete a bounded cycle")
    for widget in required:
        if str(widget) not in seen:
            raise RuntimeError(f"Keyboard cannot reach a current-stage action: {widget}")
    return seen


def check_keyboard(root, view):
    required = [view.search_entry, view.review_button if view.review_button.winfo_ismapped() else view.pair_button,
                view.recovery_toggle]
    if view.contacts._cards:
        required.extend((view.contacts._cards[0].button, view.contacts._cards[-1].button))
    return keyboard_traversal(root, required, start=view.search_entry)


def main():
    if sys.platform != "win32" or os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("RUNNER_OS") != "Windows":
        raise SystemExit("This fixture requires the disposable actual Windows CI desktop")
    parser = argparse.ArgumentParser()
    for name in ("source", "logo", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--normal-open-only", action="store_true")
    mode.add_argument("--native-setup", action="store_true")
    args = parser.parse_args()
    if not args.normal_open_only:
        from render_guided_journey import render
        render(args, contacts_only=not args.native_setup)
        return
    from render_guided_journey import synthetic_view
    args.output.mkdir(parents=True, exist_ok=False)
    root = create_root()
    try:
        with synthetic_view(root, args.logo) as view:
            from test_session import inspected
            view.state = inspected()
            view.refresh()
            root.update()
            desktop = visible_snapshot(root)
            receipt = {"status": "normal-opening-visible-account-entry", "guiSourceSha256": gui_digest(args.source),
                       "desktop": desktop, "fixtureResizedWindow": False, "fixtureChangedDesktop": False,
                       "physicalDpiAcceptance": False, "geometry": require_bounds(root, view.account_start_button, "account entry"),
                       "screenshot": capture(root, args.output, "normal-open-account")}
            (args.output / "normal-open-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    finally:
        root.destroy()


if __name__ == "__main__":
    main()
