"""Test-only controller for an actual bridge with synthetic metadata and authority.

Stdout is a private JSON-lines channel, including the synthetic pairing capability.
No device collection, production account transport or fixed-port listener is used.
"""
from __future__ import annotations

import argparse
import http.client
import json
from pathlib import Path
import sys
import tempfile
import time
from uuid import uuid4

from amplifai_phone.bridge import ACCOUNT_ORIGIN, BROWSER_ORIGINS, BridgeServer
from AuthorityRefreshHelper import build_capture

INTENT = "11111111-1111-4111-8111-111111111111"
CAPABILITY = "A" * 43
RELEASE = "B" * 43


class FixtureClock:
    def __init__(self) -> None:
        self.offset = 0.0

    def __call__(self) -> float:
        return time.monotonic() + self.offset

    def advance_to(self, deadline: float) -> None:
        self.offset += max(0.0, deadline - self())


def account_proof(action: str, command: dict[str, str]) -> dict[str, object] | None:
    if command.get("intentId") != INTENT or command.get("capability") != CAPABILITY:
        return None
    if action == "claim":
        return {"destinationEmail": "synthetic.owner@example.invalid",
                "destinationCourse": "SYNTHETIC course"}
    if action == "consume" and command.get("releaseCode") == RELEASE:
        return {"consumed": True}
    return None


def emit(value: dict[str, object]) -> None:
    print(json.dumps(value, separators=(",", ":")), flush=True)


def post(bridge: BridgeServer, origin: str, path: str, body: dict[str, str]) -> dict[str, object]:
    connection = http.client.HTTPConnection("127.0.0.1", bridge.port, timeout=5)
    try:
        connection.request("POST", path, json.dumps(body), {
            "Host": f"127.0.0.1:{bridge.port}", "Origin": origin,
            "Content-Type": "application/json",
        })
        response = connection.getresponse()
        raw = response.read()
        if response.status != 200:
            raise RuntimeError(f"Synthetic setup failed at {path}: HTTP {response.status}")
        return json.loads(raw)
    finally:
        connection.close()


def release(bridge: BridgeServer, origin: str) -> dict[str, object]:
    post(bridge, origin, "/v3/connect", {
        "intentId": INTENT, "capability": CAPABILITY,
        "handoffId": bridge.handoff_id, "payloadSha256": bridge.payload_sha256,
    })
    if not bridge.approve_destination(INTENT, True):
        raise RuntimeError("Synthetic destination approval failed")
    return post(bridge, origin, "/v3/release", {
        "intentId": INTENT, "capability": CAPABILITY, "releaseCode": RELEASE,
    })


def snapshot(bridge: BridgeServer) -> dict[str, object]:
    with bridge.lock:
        state = {
            "authorityState": bridge.authority_state,
            "sourceCursors": dict(bridge.source_cursors),
            "sourceDispositions": dict(bridge.source_dispositions),
        }
    state["handoffState"] = bridge.handoff_state()
    return state


def control(bridge: BridgeServer, clock: FixtureClock) -> int:
    while line := sys.stdin.readline(1025):
        try:
            if len(line) > 1024:
                raise ValueError("Command exceeds fixture bound")
            command = json.loads(line)
            if not isinstance(command, dict):
                raise ValueError("Command must be an object")
            action = command.get("action")
            if action == "authority" and set(command) == {"action", "state"}:
                if command["state"] not in ("current", "refreshing", "paused"):
                    raise ValueError("Unknown authority state")
                accepted = bridge.set_run_authority(bridge.handoff_id, command["state"])
                emit({"kind": "authority", "accepted": accepted, **snapshot(bridge)})
            elif action in ("expire-refresh", "expire-transfer") and set(command) == {"action"}:
                with bridge.lock:
                    deadline = (bridge.authority_refresh_expires_at if action == "expire-refresh"
                                else bridge.transfer_expires_at)
                    if deadline is None:
                        raise ValueError("No active deadline")
                    clock.advance_to(deadline)
                if action == "expire-refresh":
                    bridge.set_run_authority(bridge.handoff_id, "refreshing")
                emit({"kind": action, **snapshot(bridge)})
            elif action == "status" and set(command) == {"action"}:
                emit({"kind": "status", **snapshot(bridge)})
            elif action == "stop" and set(command) == {"action"}:
                bridge.close()
                emit({"kind": "stopped"})
                return 0
            else:
                raise ValueError("Unknown command")
        except (ValueError, TypeError):
            emit({"kind": "fixture-error", "code": "invalid_command"})
            return 2
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--small", action="store_true", help="One row per source for launcher smoke checks only")
    parser.add_argument("--origin", choices=sorted(BROWSER_ORIGINS), default=ACCOUNT_ORIGIN)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="amplifai-website-refresh-fixture-") as temporary:
        started = time.monotonic()
        capture = build_capture(Path(temporary), small=args.small)
        captured = time.monotonic()
        bridge = None
        try:
            store = capture.record_store
            review_id = str(uuid4())
            count = len(capture.contacts.records)
            selection = None
            for cursor in range(0, count, 1000):
                selection = store.append_selection(
                    review_id, cursor, list(range(cursor + 1, min(cursor + 1000, count) + 1)), count,
                )
            if selection is None:
                raise RuntimeError("Synthetic selection did not complete")
            clock = FixtureClock()
            bridge = BridgeServer(capture, selection, port=0, now=clock, account_proof=account_proof)
            if bridge.port == 48751:
                raise RuntimeError("Fixture must not use the customer application port")
            pages_built = time.monotonic()
            sources = bridge.transfer.manifest["sources"]
            counts = {category: source["rowsIncluded"] for category, source in sources.items()}
            pages = {category: source["pageCount"] for category, source in sources.items()}
            expected_counts = ({"contacts": 1, "calls": 1, "messages": 1} if args.small
                               else {"contacts": 5134, "calls": 3711, "messages": 302304})
            expected_pages = ({"contacts": 1, "calls": 1, "messages": 1} if args.small
                              else {"contacts": 52, "calls": 38, "messages": 3024})
            if counts != expected_counts or pages != expected_pages:
                raise RuntimeError("Synthetic transfer cardinality changed")
            bridge.start()
            pairing = release(bridge, args.origin)
            emit({
                "kind": "ready", "protocol": "phone-refresh-web-fixture-v1", "synthetic": True,
                "host": "127.0.0.1", "port": bridge.port, "origin": args.origin,
                "pairing": pairing, "counts": counts, "pages": pages,
                "timingMs": {"capture": round((captured - started) * 1000),
                             "pages": round((pages_built - captured) * 1000)},
            })
            return control(bridge, clock)
        finally:
            if bridge is not None:
                bridge.close()
            capture.close()


if __name__ == "__main__":
    raise SystemExit(main())
