"""Actual Windows Tk update states; no phone, installer, or network launch."""
from __future__ import annotations

import argparse
import json
import platform
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import app
from build_windows_candidate import gui_digest
from render_candidate import capture, create_root, descendants, place_viewport
from session import Session
from updater import BUILD, VERSION


def idle() -> Session:
    state = Session(); state.begin("inspect")
    state.handle({"kind": "residue", "sessions": []}); state.exited(0)
    return state


def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("An actual Windows desktop runner is required")
    parser = argparse.ArgumentParser()
    for name in ("source", "logo", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    root = create_root()
    with patch.object(app, "HelperProcess"), patch.object(app, "CompanionStatusServer"), patch.object(app, "cleanup_owned_updates"), patch.object(app.PhoneWindow, "pump"):
        view = app.PhoneWindow(root, Path("synthetic-helper-not-launched"), args.logo)
    view._startup_update_pending = False
    records = []
    styles = app.ttk.Style(root)
    for width, height in ((920, 749), (680, 680)):
        place_viewport(root, width, height)
        for state in ("checking", "available", "deferred", "installing", "failed"):
            view.state = idle()
            view.update_state = state
            view.update_message.set({
                "checking": "Checking for a compatible Windows update…",
                "available": f"Verified {VERSION}-next unsigned Windows TEST is ready. Open its installer when collection and unsaved work are finished.",
                "deferred": "Update check deferred. Finish collection, local review, account Save and temporary data inspection first.",
                "installing": "Preparing the verified update. This app will close when Windows Installer is ready.",
                "failed": "Update signature could not be verified. The installed app remains usable.",
            }[state])
            view.prepared_update = SimpleNamespace(manifest=SimpleNamespace(version=VERSION + "-next", build=str(int(BUILD) + 1))) if state in {"available", "deferred"} else None
            if state == "deferred":
                view.state._capture_received = True
            view.refresh(); root.update()
            view.show_workspace("details")
            root.update()  # Settle the newly mapped pane's height before scrolling.
            enabled = str(view.install_update_button.cget("state")) == "normal"
            if enabled != (state == "available"):
                raise RuntimeError("Update installer control did not respect idle/unsaved gate")
            # Update actions live at the end of Details & recovery. Capture the
            # actual state instead of a viewport containing only its upper rows.
            canvas = view.details_canvas
            canvas.yview_moveto(1.0)
            root.update()
            message = next(widget for widget in descendants(root) if isinstance(widget, app.ttk.Label)
                           and str(widget.cget("textvariable")) == str(view.update_message))
            left, top = canvas.winfo_rootx(), canvas.winfo_rooty()
            right, bottom = left + canvas.winfo_width(), top + canvas.winfo_height()
            for control in (message, view.check_update_button, view.install_update_button):
                x, y = control.winfo_rootx(), control.winfo_rooty()
                if (not control.winfo_ismapped() or x < left or y < top
                        or x + control.winfo_width() > right or y + control.winfo_height() > bottom):
                    capture(root, args.output, f"{width}-update-{state}-clipped")
                    raise RuntimeError("Update message or action is clipped: " + json.dumps({
                        "state": state, "width": width, "control": str(control),
                        "mapped": bool(control.winfo_ismapped()), "canvas": [left, top, right, bottom],
                        "controlBounds": [x, y, x + control.winfo_width(), y + control.winfo_height()],
                        "scrollRegion": canvas.cget("scrollregion"), "yview": canvas.yview()}))
            records.append(dict(capture(root, args.output, f"{width}-update-{state}"), updateControlsVisible=True))
            for control in (view.check_update_button, view.install_update_button):
                if control.cget("style") not in {"", "TButton"} or str(control.cget("takefocus")) == "0":
                    raise RuntimeError("Update controls diverged from native keyboard/button pattern")
    computed = {key: str(styles.lookup("TButton", key)) for key in ("font", "foreground", "background", "padding", "bordercolor")}
    # The accepted rounded secondary control paints BORDER over the PANEL backdrop.
    if (computed["background"] != app.PANEL or computed["foreground"] != app.HEADING or computed["bordercolor"] != app.BORDER):
        raise RuntimeError("Update button styles diverged from existing source tokens")
    receipt = {"status": "actual-windows-tk-synthetic-update-states", "guiSourceSha256": gui_digest(args.source),
               "screenshots": records, "computedButtonStyle": computed, "tkScaling": root.tk.call("tk", "scaling"),
               "windowingSystem": root.tk.call("tk", "windowingsystem"), "idleAndUnsavedGate": True,
               "pythonVersion": platform.python_version(), "tclVersion": root.tk.call("info", "patchlevel"),
               "tkVersion": root.tk.call("package", "provide", "Tk"),
               "phoneAccountOrInstallerExecuted": False}
    (args.output / "render-update-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    view.prepared_update = None
    view.shutdown()
    print(json.dumps(receipt, sort_keys=True))


if __name__ == "__main__":
    main()
