"""Render the changed Windows guide and source details on the standard runner."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import tkinter as tk
from pathlib import Path
from unittest.mock import patch

from PIL import ImageGrab

import app
from build_windows_candidate import gui_digest
from render_candidate import capture, selected_review
from session import BACKUP_CHECK_MODE, Session


def checked_setting(value: bool) -> Session:
    state = Session()
    state.begin("inspect")
    state.handle({"kind": "residue", "sessions": []})
    state.exited(0)
    state.begin(BACKUP_CHECK_MODE)
    state.handle({"kind": BACKUP_CHECK_MODE, "backupEncrypted": value})
    state.exited(0)
    state.begin("inspect")
    state.handle({"kind": "residue", "sessions": []})
    state.exited(0)
    return state


def capture_window(window: tk.Toplevel, output: Path, name: str) -> dict:
    window.update()
    time.sleep(0.15)
    x, y = window.winfo_rootx(), window.winfo_rooty()
    width, height = window.winfo_width(), window.winfo_height()
    path = output / f"windows-{name}.png"
    image = ImageGrab.grab(bbox=(x, y, x + width, y + height), all_screens=True)
    if image.size != (width, height):
        raise RuntimeError("Source-details screenshot viewport mismatch")
    image.save(path)
    return {"path": path.name, "width": width, "height": height,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("An actual Windows desktop runner is required")
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--logo", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)

    root = tk.Tk()
    with patch.object(app, "HelperProcess"), patch.object(app.PhoneWindow, "pump"):
        view = app.PhoneWindow(root, Path("synthetic-helper-not-launched"), args.logo)
    records = []
    for width, height in ((920, 800), (680, 680)):
        root.geometry(f"{width}x{height}+30+30")
        view.state = checked_setting(False)
        view.refresh()
        root.update()
        records.append(capture(root, args.output, f"{width}-setting-off", scroll=0.0))
        view.guide_next.focus_set()
        view.guide_next.invoke()
        root.update()
        if view.guide_step != 1 or "Check the backup setting" not in view.guide_heading.get():
            raise RuntimeError("Current-step guide did not advance from its keyboard action")
        records.append(capture(root, args.output, f"{width}-guide-step2", scroll=0.0))
        view.guide_step = 0
        view.render_guide()

    view.state = selected_review()
    view.render_contacts()
    view.refresh()
    view.contacts.activate(0)
    view.show_source_details()
    details = view.source_details_window
    if details is None:
        raise RuntimeError("Focused contact source details did not open")
    if "Contact source details" != details.title():
        raise RuntimeError("Source details title changed")
    details.geometry("520x320+80+80")
    records.append(capture_window(details, args.output, "contact-source-details"))
    view.close_source_details()
    if view.source_details_window is not None:
        raise RuntimeError("Source details did not close")
    receipt = {
        "status": "actual-windows-tk-synthetic-guide-and-source-details",
        "guiSourceSha256": gui_digest(args.source),
        "screenshots": records,
        "tkScaling": root.tk.call("tk", "scaling"),
        "windowingSystem": root.tk.call("tk", "windowingsystem"),
        "keyboardGuideAdvance": True,
        "readOnlyOffSetting": True,
        "sourceDetailsDidNotChangeSelection": len(view.state.selected_ids) == 1205,
        "physicalPhoneOrAccountSaveProved": False,
    }
    if not receipt["sourceDetailsDidNotChangeSelection"]:
        raise RuntimeError("Opening source details changed contact selection")
    (args.output / "render-video-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    root.destroy()
    print(json.dumps({"status": receipt["status"], "guiSourceSha256": receipt["guiSourceSha256"],
                      "screenshots": records}))


if __name__ == "__main__":
    main()
