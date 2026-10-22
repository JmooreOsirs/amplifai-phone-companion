"""Capture a disposable Windows Tk review fixture on the standard runner."""

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
from session import Session
from visible_desktop import Desktop, validate_visible

TOTAL_CONTACTS = 1_205
HANDOFF_ID = "11111111-1111-4111-8111-111111111111"
INTENT_ID = "22222222-2222-4222-8222-222222222222"


def selected_review() -> Session:
    state = Session()
    state.begin("inspect")
    state.handle({"kind": "residue", "sessions": []})
    state.exited(0)
    state.approve(True)
    state.begin("connect")

    def page(cursor: int, scan_id: str | None = None) -> dict:
        end = min(cursor + 200, TOTAL_CONTACTS)
        result = {
            "contacts": [{"id": index + 1, "name": f"Synthetic contact {index + 1}",
                          "phoneCount": 1, "phoneEnds": ["0000"]}
                         for index in range(cursor, end)],
            "query": "", "cursor": cursor,
            "nextCursor": end if end < TOTAL_CONTACTS else None,
            "totalContacts": TOTAL_CONTACTS,
        }
        if scan_id is not None:
            result["scanId"] = scan_id
        return result

    state.handle({"kind": "transfer", "stage": "backup", "receivedBytes": 4096,
                  "retainedBytes": 2048, "discardedBytes": 2048, "filesReceived": 3,
                  "elapsedSeconds": 2.0, "bytesPerSecond": 2048.0})
    state.handle({"kind": "transfer", "stage": "processing", "processedBytes": 1024})
    state.handle({"kind": "capture", **page(0), "availableCalls": 0,
                  "availableMessages": 38_999, "missing": ["calls"],
                  "backupEncrypted": True,
                  "unavailableReasons": {"calls": "selected_payload_length"}})
    command = state.select_all_contacts()
    for cursor in range(0, TOTAL_CONTACTS, 200):
        command = state.handle({"kind": "contacts", **page(cursor, command["scanId"])})
    assert command is None and len(state.selected_ids) == TOTAL_CONTACTS
    command = state.review(sorted(state.selected_ids))
    while command is not None:
        command = state.handle({"kind": "review-page", "reviewId": command["reviewId"],
                                "nextCursor": command["cursor"] + len(command["ids"])})
    digest = hashlib.sha256("".join(f"{index}\n" for index in range(1, TOTAL_CONTACTS + 1)).encode()).hexdigest()
    state.handle({"kind": "review", "reviewId": state._pending_review_id,
                  "selection_sha256": digest, "selected_contacts": TOTAL_CONTACTS,
                  "matched_calls": 0, "matched_messages": 12_670,
                  "missing_sources": ["calls"]})
    state.pair()
    state.handle({"kind": "pairing", "pairCode": "1234567890", "port": 48751,
                  "expiresInSeconds": 300, "handoffId": HANDOFF_ID,
                  "payloadSha256": "a" * 64})
    state.handle({"kind": "destination", "state": "pending", "handoffId": HANDOFF_ID,
                  "intentId": INTENT_ID, "email": "synthetic@example.test", "course": "Synthetic course"})
    return state


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def create_root():
    desktop = Desktop()
    desktop.physical_coordinates()
    root = tk.Tk()
    root.capture_desktop = desktop
    return root


def place_viewport(root, width, height):
    return root._root().capture_desktop.place(root, width, height)


def visible_snapshot(root, expected_size=None):
    return validate_visible(root._root().capture_desktop.snapshot(root), expected_size)


def capture(root: tk.Tk, output: Path, name: str, *, scroll: float | None = None) -> dict:
    if scroll is not None:
        for canvas in descendants(root):
            if isinstance(canvas, tk.Canvas) and canvas.winfo_ismapped():
                canvas.yview_moveto(scroll)
    root.update()
    time.sleep(0.15)
    desktop = visible_snapshot(root)
    x, y, right, bottom = desktop["clientBounds"]
    width, height = right - x, bottom - y
    path = output / f"windows-{name}.png"
    image = ImageGrab.grab(bbox=(x, y, x + width, y + height), all_screens=True)
    if image.size != (width, height):
        raise RuntimeError("Windows screenshot viewport mismatch")
    if visible_snapshot(root) != desktop:
        raise RuntimeError("Visible desktop bounds changed during screenshot capture")
    image.save(path)
    return {"path": path.name, "width": width, "height": height,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "visibleDesktop": desktop}


def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("This fixture requires the actual Windows desktop runner")
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--logo", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    root = create_root()
    with patch.object(app, "HelperProcess"), patch.object(app, "CompanionStatusServer"), patch.object(app, "cleanup_owned_updates"):
        view = app.PhoneWindow(root, Path("synthetic-helper-not-launched"), args.logo)
    view.state = selected_review()
    view.render_contacts()
    view.refresh()
    root.update()
    records = []
    for width, height in ((920, 749), (680, 680)):
        place_viewport(root, width, height)
        view.show_workspace("setup")
        root.update()
        canvas = view.details_canvas
        left, right = canvas.winfo_rootx(), canvas.winfo_rootx() + canvas.winfo_width()
        for button in (view.approve, view.connect, view.decline_button, view.privacy_button):
            if button.winfo_rootx() < left or button.winfo_rootx() + button.winfo_width() > right:
                raise RuntimeError(f"Consent action clipped at {width}px: {button.cget('text')}")
        records.append(capture(root, args.output, f"{width}x{height}-top", scroll=0.0))
        view.show_workspace("contacts")
        records.append(capture(root, args.output, f"{width}x{height}-review"))
    root.focus_force()
    root.clipboard_clear()
    view.show_workspace("details", account=True)
    view.copy_code_button.focus_set()
    root.update()
    view.copy_code_button.event_generate("<KeyPress-space>")
    view.copy_code_button.event_generate("<KeyRelease-space>")
    root.update()
    if root.clipboard_get() != "1234567890":
        raise RuntimeError("Keyboard Copy did not preserve the exact one-use code")
    view.events.put({"kind": "destination", "state": "pending", "handoffId": HANDOFF_ID,
                     "intentId": INTENT_ID, "email": "synthetic@example.test", "course": "Synthetic course"})
    with patch.object(app.messagebox, "askyesno", return_value=True):
        view.pump()
    if view.state.destination_requested_approval is not True or view.state.saved_acknowledged:
        raise RuntimeError("Destination confirmation did not preserve separate account Save")
    scan = Session()
    scan.begin("inspect")
    scan.handle({"kind": "residue", "sessions": []})
    scan.exited(0)
    scan.approve(True)
    scan.begin("connect")
    scan.handle({"kind": "capture", "contacts": [
                 {"id": index, "name": f"Synthetic contact {index}",
                  "phoneCount": 1, "phoneEnds": ["0000"]} for index in (1, 2)],
                 "query": "", "cursor": 0, "nextCursor": None,
                 "totalContacts": 2, "availableCalls": 0,
                 "availableMessages": 0, "missing": []})
    scan.update_visible_selection([1])
    pending_page = scan.select_all_contacts()
    view.state = scan
    view.refresh()
    view.cancel_select_all_button.invoke()
    if not scan._select_all_cancel or scan.selected_ids != {1}:
        raise RuntimeError("Stop check did not keep the previous selection while a page was in flight")
    scan.handle({"kind": "contacts", "contacts": [
                 {"id": index, "name": f"Synthetic contact {index}",
                  "phoneCount": 1, "phoneEnds": ["0000"]} for index in (1, 2)],
                 "query": "", "cursor": pending_page["cursor"], "nextCursor": None,
                 "totalContacts": 2, "scanId": pending_page["scanId"]})
    if scan.selecting_all or scan.selected_ids != {1}:
        raise RuntimeError("Stop check changed an uncommitted all-page selection")
    if scan.saved_acknowledged:
        raise RuntimeError("A reviewed preview was mistaken for an account save")
    receipt = {"status": "actual-windows-tk-synthetic-view-only", "screenshots": records,
               "tkScaling": root.tk.call("tk", "scaling"), "windowingSystem": root.tk.call("tk", "windowingsystem"),
               "selectedContacts": TOTAL_CONTACTS, "missingCalls": "selected_payload_length",
               "keyboardCodeCopied": True, "destinationConfirmedWithoutSave": True,
               "scanCancelPreservedSelection": True, "accountSaveAcknowledged": False}
    (args.output / "render-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    root.destroy()
    print(json.dumps({"status": receipt["status"], "screenshots": records,
                      "selectedContacts": TOTAL_CONTACTS}))


if __name__ == "__main__":
    main()
