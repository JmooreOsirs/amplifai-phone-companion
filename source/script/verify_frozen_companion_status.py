#!/usr/bin/env python3
"""Qualify only the frozen helper's status mode; never read a collection workspace."""
from __future__ import annotations

import argparse
import http.client
import json
from pathlib import Path
import queue
import subprocess
import threading
import time

ORIGIN = "https://amplifai.satoris.ai"


def request(method="GET", body=None):
    connection = http.client.HTTPConnection("127.0.0.1", 48752, timeout=3)
    headers = {"Origin": ORIGIN, "Host": "127.0.0.1:48752"}
    if body is not None:
        body = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    try:
        connection.request(method, "/v1/status" if method == "GET" else "/v1/check-update", body=body, headers=headers)
        response = connection.getresponse()
        encoded = response.read(4097)
        if len(encoded) > 4096:
            raise ValueError("Status response exceeds its bound")
        return response.status, json.loads(encoded)
    finally:
        connection.close()


def events(process):
    received = queue.Queue()

    def read():
        while True:
            line = process.stdout.readline(4097)
            if not line:
                received.put(None)
                return
            if len(line) > 4096 or not line.endswith(b"\n"):
                received.put(ValueError("Unbounded helper event"))
                return
            try:
                received.put(json.loads(line))
            except (ValueError, UnicodeDecodeError) as error:
                received.put(error)
                return

    threading.Thread(target=read, daemon=True).start()
    return received


def expect(received, value):
    actual = received.get(timeout=5)
    if actual != value:
        raise ValueError(f"Unexpected bounded status event: {actual!r}")


def qualify(helper: Path, architecture: str, version: str, build: str):
    intel = architecture == "x86_64"
    channel = "macos-intel-test" if intel else "macos-arm64"
    bundle = "ai.satoris.amplifai.phone.intel-candidate" if intel else "ai.satoris.amplifai.phone.candidate"
    arguments = [str(helper), "companion-status", "--platform", "macos", "--architecture", architecture,
                 "--channel", channel, "--bundle-id", bundle, "--version", version, "--build", build]
    children = []
    environment = {"PATH": "/usr/bin:/bin", "LANG": "en_US.UTF-8", "PYTHONNOUSERSITE": "1"}
    try:
        owner = subprocess.Popen(arguments, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=environment)
        children.append(owner)
        received = events(owner)
        expect(received, {"event": "status_ready"})
        status, identity = request()
        expected = {"platform": "macos", "architecture": architecture, "channel": channel,
                    "bundleId": bundle, "version": version, "build": build}
        if status != 200 or any(identity.get(key) != value for key, value in expected.items()):
            raise ValueError("Frozen helper returned another channel or identity")
        conflict = subprocess.Popen(arguments, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=environment)
        children.append(conflict)
        expect(events(conflict), {"event": "status_unavailable", "code": "port_in_use"})
        conflict.stdin.close()
        conflict.wait(timeout=5)
        state = {"action": "set_status", "busy": True, "updateState": "deferred",
                 "availableVersion": None, "availableBuild": None}
        owner.stdin.write(json.dumps(state).encode() + b"\n")
        owner.stdin.flush()
        deadline = time.monotonic() + 3
        while True:
            status, identity = request()
            if identity.get("busy") is True and identity.get("updateState") == "deferred":
                break
            if time.monotonic() > deadline:
                raise ValueError("Status child did not observe its bounded command")
        status, body = request("POST", {"schema": 1, "token": identity["updateCheckToken"]})
        if status != 409 or body != {"deferred": True}:
            raise ValueError("Busy helper did not defer its update request")
        state.update(busy=False, updateState="current")
        owner.stdin.write(json.dumps(state).encode() + b"\n")
        owner.stdin.flush()
        deadline = time.monotonic() + 3
        while True:
            status, identity = request()
            if identity.get("busy") is False:
                break
            if time.monotonic() > deadline:
                raise ValueError("Status child did not become idle")
        status, body = request("POST", {"schema": 1, "token": identity["updateCheckToken"]})
        if status != 202 or body != {"checking": True}:
            raise ValueError("Idle native check request was not accepted")
        expect(received, {"event": "update_check_requested"})
        owner.stdin.close()
        if owner.wait(timeout=5) != 0:
            raise ValueError("Status helper did not stop cleanly on stdin EOF")
        expect(received, None)
        return {"status": "qualified", **expected, "boundedStatus": True, "portConflict": True,
                "busyDeferral": True, "nativeCheckEvent": True, "stdinEOFShutdown": True,
                "collectionStarted": False, "workspaceRead": False}
    finally:
        for child in children:
            if child.stdin and not child.stdin.closed:
                child.stdin.close()
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--helper", required=True, type=Path)
    parser.add_argument("--architecture", choices=("arm64", "x86_64"), required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--build", required=True)
    args = parser.parse_args()
    print(json.dumps(qualify(args.helper.resolve(strict=True), args.architecture, args.version, args.build), sort_keys=True))


if __name__ == "__main__":
    main()
