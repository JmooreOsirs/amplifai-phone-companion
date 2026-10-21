"""Render only the changed Windows handoff controls on an isolated runner desktop."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from unittest.mock import patch

import app
from build_windows_candidate import gui_digest
from render_candidate import HANDOFF_ID, capture, create_root, place_viewport, selected_review


def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("An actual Windows desktop runner is required")
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--logo", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)

    root = create_root()
    with patch.object(app, "HelperProcess"), patch.object(app, "CompanionStatusServer"), patch.object(app, "cleanup_owned_updates"), patch.object(app.PhoneWindow, "pump"):
        view = app.PhoneWindow(root, Path("synthetic-helper-not-launched"), args.logo)
    records = []
    for width, height in ((920, 749), (680, 680)):
        place_viewport(root, width, height)
        view.state = selected_review()
        view.helper.write.reset_mock()
        view.render_contacts()
        view.refresh()
        root.update()
        records.append(capture(root, args.output, f"{width}-review", scroll=0.55))
        if not view.state.can_change_selection:
            raise RuntimeError("Reviewed handoff did not offer explicit contact-change action")

        root.focus_force()
        view.change_selection_button.focus_set()
        root.update()
        with patch.object(app.messagebox, "askyesno", return_value=True):
            view.change_selection_button.event_generate("<KeyPress-space>")
            view.change_selection_button.event_generate("<KeyRelease-space>")
            root.update()
        if (not view.state._selection_change_pending or view.state.handoff_id != HANDOFF_ID
                or view.state.selected_ids != set(range(1, 1_206))):
            raise RuntimeError("Keyboard contact-change did not retain the reviewed handoff")
        view.helper.write.assert_called_with({"action": "revoke"})
        view.refresh()
        records.append(capture(root, args.output, f"{width}-change-pending", scroll=0.55))

        view.state.handle({"kind": "handoff", "state": "saved_pending_browser_receipt",
                           "handoffId": HANDOFF_ID})
        view.state.handle({"kind": "state", "state": "pairing_revoked"})
        view.refresh()
        if (not view.state.save_receipt_pending or view.state.can_change_selection
                or view.state.can_pair or view.state.handoff_id != HANDOFF_ID
                or view.state.selected_ids != set(range(1, 1_206))):
            raise RuntimeError("First receipt did not lock replacement and retain the reviewed handoff")
        records.append(capture(root, args.output, f"{width}-first-receipt", scroll=0.55))

    receipt = {
        "status": "actual-windows-tk-synthetic-ack-controls-only",
        "guiSourceSha256": gui_digest(args.source),
        "screenshots": records,
        "tkScaling": root.tk.call("tk", "scaling"),
        "windowingSystem": root.tk.call("tk", "windowingsystem"),
        "keyboardChangeRequested": True,
        "reviewedSelectionRetained": 1_205,
        "firstReceiptBlockedReplacement": True,
        "physicalPhoneOrAccountSaveProved": False,
    }
    (args.output / "render-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    root.destroy()
    print(json.dumps({"status": receipt["status"], "guiSourceSha256": receipt["guiSourceSha256"],
                      "screenshots": records}))


if __name__ == "__main__":
    main()
