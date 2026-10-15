"""Actual Windows Tk update states; no phone, installer, or network launch."""
from __future__ import annotations

import argparse
import json
import platform
import sys
import tkinter as tk
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import app
from build_windows_candidate import gui_digest
from render_candidate import capture
from session import Session


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
    root = tk.Tk()
    with patch.object(app, "HelperProcess"), patch.object(app.PhoneWindow, "pump"):
        view = app.PhoneWindow(root, Path("synthetic-helper-not-launched"), args.logo)
    view._startup_update_pending = False
    records = []
    styles = app.ttk.Style(root)
    for width, height in ((920, 800), (680, 680)):
        root.geometry(f"{width}x{height}+30+30")
        for state in ("checking", "available", "deferred", "installing", "failed"):
            view.state = idle()
            view.update_state = state
            view.update_message.set({
                "checking": "Checking for a compatible Windows update…",
                "available": "Verified 2026.10.16-rc20 unsigned Windows TEST is ready. Open its installer when collection and unsaved work are finished.",
                "deferred": "Update check deferred. Finish collection, local review, account Save and temporary data inspection first.",
                "installing": "Preparing the verified update. This app will close when Windows Installer is ready.",
                "failed": "Update signature could not be verified. The installed app remains usable.",
            }[state])
            view.prepared_update = SimpleNamespace(manifest=SimpleNamespace(version="2026.10.16-rc20", build="26101620")) if state in {"available", "deferred"} else None
            if state == "deferred":
                view.state._capture_received = True
            view.refresh(); root.update()
            enabled = str(view.install_update_button.cget("state")) == "normal"
            if enabled != (state == "available"):
                raise RuntimeError("Update installer control did not respect idle/unsaved gate")
            records.append(capture(root, args.output, f"{width}-update-{state}", scroll=0.0))
            for control in (view.check_update_button, view.install_update_button):
                if control.cget("style") not in {"", "TButton"} or str(control.cget("takefocus")) == "0":
                    raise RuntimeError("Update controls diverged from native keyboard/button pattern")
    computed = {key: str(styles.lookup("TButton", key)) for key in ("font", "foreground", "background", "padding", "bordercolor")}
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
