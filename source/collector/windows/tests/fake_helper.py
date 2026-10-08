"""Synthetic subprocess boundary fixture; no device imports or filesystem writes."""
import json
import sys

def emit(value):
    print(json.dumps(value), flush=True)

if sys.argv[1] == "inspect":
    emit({"kind": "residue", "sessions": []})
elif sys.argv[1] == "clear-residue":
    print("not-json", flush=True)
    raise SystemExit(130 if sys.stdin.read() == "" else 1)
else:
    emit({"kind": "state", "state": "connecting"})
    packet = json.loads(sys.stdin.readline())
    if packet == {"action": "disconnect"}:
        emit({"kind": "state", "state": "disconnected"})
    else:
        emit({"kind": "error", "code": "selection"})
        raise SystemExit(1)
