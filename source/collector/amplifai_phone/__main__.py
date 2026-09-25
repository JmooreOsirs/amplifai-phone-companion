"""Developer-only executable path for the local iPhone collector candidate."""

from __future__ import annotations

import asyncio
import getpass
import sys

from .ios_backup import IPhoneCapture, collect_iphone
from .metadata import interactions_for_contacts, select_people


def review_capture(capture: IPhoneCapture, selection: str) -> dict[str, object]:
    """Project owner-selected contacts and their retained interaction counts."""
    try:
        ids = {int(part.strip()) for part in selection.split(",") if part.strip()}
    except ValueError as exc:
        raise ValueError("Enter contact IDs separated by commas") from exc
    if not ids:
        raise ValueError("Select at least one contact")
    chosen = select_people(capture.contacts, ids)
    interactions = interactions_for_contacts(chosen, (capture.calls, capture.messages))
    call_dates = sorted(
        item.occurred_at for item in interactions if item.kind == "call"
    )
    message_dates = sorted(
        item.occurred_at for item in interactions if item.kind == "message"
    )
    return {
        "selected_contacts": len(chosen),
        "matched_calls": sum(item.kind == "call" for item in interactions),
        "matched_messages": sum(item.kind == "message" for item in interactions),
        "observed_call_earliest": call_dates[0] if call_dates else None,
        "observed_call_latest": call_dates[-1] if call_dates else None,
        "observed_message_earliest": message_dates[0] if message_dates else None,
        "observed_message_latest": message_dates[-1] if message_dates else None,
        "available_since": capture.since,
        "missing_sources": capture.missing_sources,
    }


def main() -> int:
    print(
        "AMPLIFai local iPhone collector — developer candidate; no data is saved or uploaded."
    )
    print(
        "Use one previously paired iPhone over Wi-Fi, or connect by USB, unlock it, and approve Apple's Trust prompt if shown."
    )
    input("Press Enter when ready, or Ctrl-C to cancel. ")
    last_progress = -1

    def progress(value: float) -> None:
        nonlocal last_progress
        bucket = int(value // 10)
        if bucket > last_progress:
            last_progress = bucket
            print(f"Backup transfer: {min(100, bucket * 10)}%", file=sys.stderr)

    try:
        capture = asyncio.run(
            collect_iphone(
                password_provider=lambda: getpass.getpass(
                    "Existing encrypted-backup password: "
                ),
                progress_callback=progress,
            )
        )
        print(f"Available contacts: {len(capture.contacts.records)}")
        print(
            f"Available calls: {len(capture.calls.records)}; messages: {len(capture.messages.records)}"
        )
        if capture.missing_sources:
            print("Missing backup sources: " + ", ".join(capture.missing_sources))
        for contact in capture.contacts.records:
            print(
                f"{contact.source_id}: {contact.name or '(unnamed)'} — {len(contact.phones)} phone(s)"
            )
        selection = input("Enter contact IDs, comma separated: ")
        print(review_capture(capture, selection))
        print(
            "Review complete. This candidate deliberately does not save or upload owner data."
        )
        return 0
    except (KeyboardInterrupt, EOFError):
        print(
            "Cancelled. Temporary backup data is removed by the collector.",
            file=sys.stderr,
        )
        return 130
    except Exception as exc:  # noqa: BLE001 - sanitize unpredictable device-library errors
        # Exceptions from third-party backup libraries may contain source paths
        # or device details; never echo them to a terminal or log by default.
        print(
            f"Collection stopped ({type(exc).__name__}). Check Wi-Fi pairing or USB trust, the existing backup password, and the supported schema.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
