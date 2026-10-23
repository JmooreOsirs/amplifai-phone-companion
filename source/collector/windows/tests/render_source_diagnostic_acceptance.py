"""Render synthetic current/previous diagnostic states on the actual Windows runner."""
from __future__ import annotations

import argparse
import json
import platform
import sys
import tkinter as tk
from pathlib import Path
from unittest.mock import patch

import app
from build_windows_candidate import gui_digest
from render_candidate import capture as capture_window, create_root, place_viewport
from session import Session
from test_windows_source_diagnostics import capture, connecting, diagnostic, history


def children(widget):
    for child in widget.winfo_children():
        yield child
        yield from children(child)


def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("Actual Windows Tk rendering requires the disposable Windows desktop runner")
    parser = argparse.ArgumentParser()
    for name in ("source", "logo", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    root = create_root()
    with patch.object(app, "HelperProcess"), patch.object(app, "CompanionStatusServer"), patch.object(app, "cleanup_owned_updates"), patch.object(app.PhoneWindow, "pump"):
        view = app.PhoneWindow(root, Path("synthetic-helper-not-launched"), args.logo)
    view._startup_update_pending = False
    styles = app.ttk.Style(root)
    records = []
    for width, height in ((920, 749), (680, 680)):
        place_viewport(root, width, height)
        for name in ("current-unavailable", "previous-run", "readable-zero-with-history"):
            if name == "current-unavailable":
                state = connecting()
                state.handle({"kind": "selected-payload-diagnostic", **diagnostic()})
                state.handle(capture(sourceDiagnostics=[diagnostic()]))
            else:
                state = Session(); state.begin("inspect")
                state.handle({"kind": "residue", "sessions": [], "sourceDiagnosticHistory": history()})
                state.exited(0)
                if name == "readable-zero-with-history":
                    state.approve(True); state.begin("connect")
                    state.handle(capture(missing=[], unavailableReasons={}))
            view.state = state
            view.render_contacts(); view.refresh(); root.update()
            view.show_workspace("details")
            root.update()
            expected = "Current collection source details" if name == "current-unavailable" else "Previous run diagnostics"
            if expected not in view.coverage_status.get() or str(view.diagnostic_details_button.cget("state")) != "normal":
                raise RuntimeError("Native diagnostic label or disclosure state differs from session facts")
            if name == "readable-zero-with-history" and "Messages: 0 readable phone entries" not in view.coverage_status.get():
                raise RuntimeError("Historical diagnostics changed a readable zero")
            label = next(widget for widget in children(root) if isinstance(widget, app.ttk.Label)
                         and str(widget.cget("textvariable")) == str(view.coverage_status))
            canvas = view.details_canvas
            left, right = canvas.winfo_rootx(), canvas.winfo_rootx() + canvas.winfo_width()
            button = view.diagnostic_details_button
            if button.winfo_rootx() < left or button.winfo_rootx() + button.winfo_width() > right:
                raise RuntimeError("Diagnostic disclosure is horizontally clipped")
            scroll = max(0.0, (label.winfo_y() - 24) / canvas.bbox("all")[3])
            records.append(capture_window(root, args.output, f"{width}-diagnostic-{name}", scroll=scroll))
            if name == "previous-run":
                view.show_source_diagnostics(); root.update()
                details = view.source_details_window
                text = next(widget for widget in children(details) if isinstance(widget, tk.Text))
                if str(text.cget("state")) != "disabled" or "Historical evidence only" not in text.get("1.0", "end"):
                    raise RuntimeError("Diagnostic disclosure is editable or loses the historical label")
                place_viewport(details, details.winfo_width(), details.winfo_height())
                records.append(capture_window(details, args.output, f"{width}-diagnostic-disclosure"))
                details.event_generate("<Escape>"); root.update()
                if view.source_details_window is not None:
                    raise RuntimeError("Escape did not close the source diagnostic disclosure")
    computed = {key: str(styles.lookup("TButton", key)) for key in ("font", "foreground", "background", "padding", "bordercolor")}
    label_style = {key: str(styles.lookup("TLabel", key)) for key in ("font", "foreground", "background")}
    if (computed["background"], computed["foreground"], computed["bordercolor"]) != (app.BORDER, app.HEADING, app.BORDER):
        raise RuntimeError("Diagnostic controls diverged from existing native source tokens")
    if (label_style["background"], label_style["foreground"]) != (app.BACKGROUND, app.BODY):
        raise RuntimeError("Diagnostic labels diverged from existing native source tokens")
    receipt = {"status": "actual-windows-tk-synthetic-source-diagnostic-states", "guiSourceSha256": gui_digest(args.source),
               "screenshots": records, "computedButtonStyle": computed, "computedLabelStyle": label_style,
               "pythonVersion": platform.python_version(),
               "tclVersion": root.tk.call("info", "patchlevel"), "tkVersion": root.tk.call("package", "provide", "Tk"),
               "currentAndPreviousSeparate": True, "readableZeroPreserved": True, "readOnlyDisclosureEscape": True,
               "phoneAccountNetworkOrInstallerExecuted": False}
    (args.output / "render-source-diagnostic-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    view.shutdown()
    print(json.dumps(receipt, sort_keys=True))


if __name__ == "__main__":
    main()
