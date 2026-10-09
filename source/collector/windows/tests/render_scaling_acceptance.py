"""Exercise the existing Tk controls at a larger synthetic text scale on Windows."""

from __future__ import annotations

import hashlib
import json
import sys
import tkinter as tk
from pathlib import Path
from unittest.mock import patch

from PIL import ImageGrab

import app
from build_windows_candidate import gui_digest
from render_candidate import selected_review

EXPECTED_RC14_WINDOWS_GUI_SHA256 = "5a85246880d336c672719685dcc697ab4a76aac610d1f916392bc92e28c08ad6"


def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("This check requires a Windows desktop")
    output = Path(sys.argv[1])
    logo = Path(sys.argv[2])
    source_gui_sha256 = gui_digest(Path("source"))
    if source_gui_sha256 != EXPECTED_RC14_WINDOWS_GUI_SHA256:
        raise RuntimeError("Synthetic Windows GUI source differs from retained rc14 MSI receipt")
    output.mkdir(parents=True, exist_ok=False)
    root = tk.Tk()
    root.tk.call("tk", "scaling", 1.75)
    root.geometry("680x680+30+30")
    with patch.object(app, "HelperProcess"):
        view = app.PhoneWindow(root, Path("synthetic-helper-not-launched"), logo)
    view.state = selected_review()
    view.render_contacts()
    view.refresh()
    root.update()
    canvas = next(widget for widget in root.winfo_children() if isinstance(widget, tk.Canvas))
    left, right = canvas.winfo_rootx(), canvas.winfo_rootx() + canvas.winfo_width()
    for button in (view.approve, view.connect, view.decline_button, view.privacy_button):
        if button.winfo_rootx() < left or button.winfo_rootx() + button.winfo_width() > right:
            raise RuntimeError(f"Consent action clipped at synthetic scaling 1.75: {button.cget('text')}")
    x, y = root.winfo_rootx(), root.winfo_rooty()
    width, height = root.winfo_width(), root.winfo_height()
    path = output / "windows-680x680-synthetic-scale-1.75.png"
    ImageGrab.grab(bbox=(x, y, x + width, y + height), all_screens=True).save(path)
    receipt = {
        "status": "synthetic-tk-scaling-only-not-physical-dpi-or-screen-reader-proof",
        "actualViewport": [width, height],
        "tkScaling": root.tk.call("tk", "scaling"),
        "retainedRc14GuiSourceSha256": source_gui_sha256,
        "consentActionsWithinCanvas": True,
        "screenshotSha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "screenshotBytes": path.stat().st_size,
    }
    (output / "scaling-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    root.destroy()
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
