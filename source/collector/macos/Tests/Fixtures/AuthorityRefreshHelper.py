"""Synthetic capture/account boundaries; production agent, bridge, pages and receipts.

Only launched from the temporary native integration executable. No device collector or
production account transport is invoked. Source data is generated, never read from a backup.
"""
from __future__ import annotations

from functools import partial
from pathlib import Path
import socket
import sys

from amplifai_phone import agent
from amplifai_phone.bridge import BRIDGE_PORT, BridgeServer
from amplifai_phone.call_accounting import CallAccounting, CallAccountingHistory
from amplifai_phone.ios_backup import IPhoneCapture
from amplifai_phone.metadata import Contact, Interaction, SourceResult
from amplifai_phone.record_store import RecordStore
from amplifai_phone.workspace import SessionWorkspace


def build_capture(root: Path, small: bool = False) -> IPhoneCapture:
    workspace = SessionWorkspace(root / "sessions")
    directory = workspace.__enter__()
    store = RecordStore(directory / "sanitized.sqlite3")
    counts = ({"contacts": 1, "calls": 1, "messages": 1} if small
              else {"contacts": 5134, "calls": 3711, "messages": 302304})
    for index in range(1, counts["contacts"] + 1):
        store.add("contacts", Contact(index, f"Synthetic {index}", (f"+1555{index:07d}",), ()))
    for category, kind, count in (("calls", "call", counts["calls"]),
                                  ("messages", "message", counts["messages"])):
        for index in range(1, count + 1):
            phone = f"+1555{1 + (index - 1) % counts['contacts']:07d}"
            store.add(category, Interaction(index, kind, "sms" if kind == "message" else "call", "2026-10-19T00:00:00Z",
                                           "incoming", (phone,), 30 if kind == "call" else None))
    sources = [SourceResult(counts[key], store.collection(key), 0,
                            CallAccounting(counts[key], 0, 0, 0, counts[key]) if key == "calls" else None)
               for key in ("contacts", "calls", "messages")]
    return IPhoneCapture(*sources, "2026-10-19T01:00:00Z", "2026-04-19T00:00:00Z",
                         (), workspace, store, backup_encrypted=True)


def main() -> int:
    root = Path(sys.argv[1]).resolve()
    mode = sys.argv[2]
    if root.parent != Path(sys.argv[0]).resolve().parent:
        raise ValueError("Fixture must live inside its isolated executable directory")
    if mode == "backup-encryption-status":
        print('{"kind":"backup-encryption-status","backupEncrypted":true}', flush=True)
        return 0
    if mode == "inspect":
        print('{"kind":"residue","sessions":[]}', flush=True)
        return 0
    if mode != "connect":
        raise ValueError("Unsupported synthetic helper mode")
    # Abort without contacting or stopping any existing listener. Production binding
    # also fails closed if another process acquires the port after this preflight.
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(("127.0.0.1", BRIDGE_PORT))

    async def capture(**_options: object) -> IPhoneCapture:
        return build_capture(root, small="--small-capture" in sys.argv[3:])

    def proof(action: str, command: dict[str, str]) -> dict[str, object] | None:
        if (command.get("intentId") != "11111111-1111-4111-8111-111111111111"
                or command.get("capability") != "A" * 43):
            return None
        if action == "claim":
            return {"destinationEmail": "synthetic.owner@example.invalid",
                    "destinationCourse": "SYNTHETIC course"}
        if action == "consume" and command.get("releaseCode") == "B" * 43:
            return {"consumed": True}
        return None

    # Constructor dependency injection changes only the external account-proof boundary.
    agent.BridgeServer = partial(BridgeServer, account_proof=proof)
    return agent.run_connect(sys.stdin, sys.stdout, collector=capture, require_encrypted_backup=True,
                             call_accounting_history=CallAccountingHistory(root / "sessions"))


if __name__ == "__main__":
    raise SystemExit(main())
