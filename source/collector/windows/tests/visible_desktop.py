"""Visible desktop qualification helpers; never imported by the product."""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
from pathlib import Path


def contains(outer, inner):
    return outer[0] <= inner[0] < inner[2] <= outer[2] and outer[1] <= inner[1] < inner[3] <= outer[3]


def intersects(first, second):
    return max(first[0], second[0]) < min(first[2], second[2]) and max(first[1], second[1]) < min(first[3], second[3])


def validate_visible(snapshot, expected_size=None):
    client, work = snapshot["clientBounds"], snapshot["workArea"]
    size = (client[2] - client[0], client[3] - client[1])
    if snapshot["dpiAwareness"] != 2 or snapshot["tkClientBounds"] != client:
        raise RuntimeError("Tk and physical Win32 client coordinates differ")
    if expected_size is not None and size != tuple(expected_size):
        raise RuntimeError(f"Required client viewport changed: {size} != {expected_size}")
    if not contains(snapshot["monitorBounds"], work) or not contains(work, client) or not contains(work, snapshot["visibleFrameBounds"]):
        raise RuntimeError(f"Window is outside the visible work area: client={client}, frame={snapshot['visibleFrameBounds']}, work={work}")
    if snapshot["occludingWindows"]:
        raise RuntimeError("Another visible desktop window covers the captured client")
    return snapshot


class Desktop:
    def __init__(self):
        if sys.platform != "win32":
            raise RuntimeError("Visible desktop checks require the disposable Windows runner")
        import win32api
        import win32con
        import win32gui
        import pywintypes
        self.api, self.con, self.gui, self.error = win32api, win32con, win32gui, pywintypes.error
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.user.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        self.user.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        self.user.GetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        self.user.GetAwarenessFromDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        self.user.GetAwarenessFromDpiAwarenessContext.restype = ctypes.c_int
        self.user.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        self.user.GetAncestor.restype = ctypes.c_void_p
        self.user.GetDpiForWindow.argtypes = [ctypes.c_void_p]
        self.user.GetDpiForWindow.restype = ctypes.c_uint
        self.dwm = ctypes.WinDLL("dwmapi")
        self.dwm.DwmGetWindowAttribute.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
        self.dwm.DwmGetWindowAttribute.restype = ctypes.c_int32

    def physical_coordinates(self):
        # Set before creating Tk windows; the synthetic Tk text scale stays separate.
        if not self.user.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4)):
            raise ctypes.WinError(ctypes.get_last_error())
        if self.awareness() != 2:
            raise RuntimeError("Physical desktop coordinate context was not applied")

    def awareness(self):
        return self.user.GetAwarenessFromDpiAwarenessContext(self.user.GetThreadDpiAwarenessContext())

    def visible_frame(self, hwnd):
        rect = (ctypes.c_int32 * 4)()
        if self.dwm.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(rect), ctypes.sizeof(rect)) != 0:
            raise RuntimeError("DWM visible frame measurement failed")
        return list(rect)

    def snapshot(self, root):
        client_id = root.winfo_id()
        hwnd = self.user.GetAncestor(client_id, 2)
        if not hwnd or not self.gui.IsWindowVisible(hwnd) or self.gui.IsIconic(hwnd):
            raise RuntimeError("Capture window is not visible on the desktop")
        monitor = self.api.GetMonitorInfo(self.api.MonitorFromWindow(hwnd, self.con.MONITOR_DEFAULTTONEAREST))
        rect = self.gui.GetClientRect(client_id)
        left, top = self.gui.ClientToScreen(client_id, (rect[0], rect[1]))
        right, bottom = self.gui.ClientToScreen(client_id, (rect[2], rect[3]))
        client = [left, top, right, bottom]
        occluders = []
        above = self.gui.GetWindow(hwnd, self.con.GW_HWNDPREV)
        visited = set()
        while above:
            if above in visited or len(visited) >= 256:
                raise RuntimeError("Desktop Z-order enumeration did not terminate")
            visited.add(above)
            if self.gui.IsWindowVisible(above) and not self.gui.IsIconic(above):
                rectangle = self.gui.GetWindowRect(above)
                if intersects(rectangle, client):
                    occluders.append({"hwnd": above, "bounds": list(rectangle)})
            above = self.gui.GetWindow(above, self.con.GW_HWNDPREV)
        return {"hwnd": hwnd, "clientBounds": client,
                "tkClientBounds": [root.winfo_rootx(), root.winfo_rooty(),
                                   root.winfo_rootx() + root.winfo_width(), root.winfo_rooty() + root.winfo_height()],
                "windowBounds": list(self.gui.GetWindowRect(hwnd)), "visibleFrameBounds": self.visible_frame(hwnd),
                "monitorBounds": list(monitor["Monitor"]), "workArea": list(monitor["Work"]),
                "dpiAwareness": self.awareness(), "windowDpi": self.user.GetDpiForWindow(hwnd),
                "occludingWindows": occluders}

    def place(self, root, width, height):
        root.geometry(f"{width}x{height}+0+0")
        root.update()
        measured = self.snapshot(root)
        frame, work, window = (measured[key] for key in ("visibleFrameBounds", "workArea", "windowBounds"))
        frame_width, frame_height = frame[2] - frame[0], frame[3] - frame[1]
        if frame_width > work[2] - work[0] or frame_height > work[3] - work[1]:
            raise RuntimeError(f"Requested {width}x{height} client plus native frame cannot fit work area {work}; prepare the disposable desktop first")
        x = work[0] + (work[2] - work[0] - frame_width) // 2 + window[0] - frame[0]
        y = work[1] + (work[3] - work[1] - frame_height) // 2 + window[1] - frame[1]
        self.gui.SetWindowPos(measured["hwnd"], self.con.HWND_TOP, x, y, 0, 0,
                              self.con.SWP_NOSIZE | self.con.SWP_NOACTIVATE)
        root.update()
        return validate_visible(self.snapshot(root), (width, height))

    def mode(self):
        return self.api.EnumDisplaySettings(None, self.con.ENUM_CURRENT_SETTINGS)

    def modes(self):
        values = []
        for index in range(512):
            try:
                values.append(self.api.EnumDisplaySettings(None, index))
            except self.error:
                return values
        raise RuntimeError("Display mode enumeration exceeded the fixture bound")

    def apply_mode(self, mode):
        mode.Fields = (self.con.DM_PELSWIDTH | self.con.DM_PELSHEIGHT | self.con.DM_BITSPERPEL |
                       self.con.DM_DISPLAYFREQUENCY | self.con.DM_DISPLAYFLAGS | self.con.DM_DISPLAYORIENTATION)
        result = self.api.ChangeDisplaySettingsEx(None, mode, self.con.CDS_TEST)
        if result != self.con.DISP_CHANGE_SUCCESSFUL:
            raise RuntimeError(f"Advertised display mode failed CDS_TEST: {result}")
        result = self.api.ChangeDisplaySettingsEx(None, mode, 0)
        if result != self.con.DISP_CHANGE_SUCCESSFUL:
            raise RuntimeError(f"Session-only display mode change failed: {result}")

    def primary_work_area(self):
        return list(self.api.GetMonitorInfo(self.api.MonitorFromPoint((0, 0), self.con.MONITOR_DEFAULTTOPRIMARY))["Work"])


MODE_FIELDS = ("PelsWidth", "PelsHeight", "BitsPerPel", "DisplayFrequency", "DisplayFlags", "DisplayOrientation")


def mode_record(mode):
    return {key: int(getattr(mode, key)) for key in MODE_FIELDS}


def choose_mode(modes, current):
    compatible = [mode for mode in modes if mode.PelsWidth >= 1280 and mode.PelsHeight >= 1024
                  and all(getattr(mode, key) == getattr(current, key) for key in MODE_FIELDS[2:])]
    if not compatible:
        raise RuntimeError("No advertised compatible desktop mode of at least 1280x1024; visible qualification cannot proceed")
    return min(compatible, key=lambda mode: (mode.PelsWidth * mode.PelsHeight, mode.PelsWidth))


def restore_mode(desktop, record):
    mode = desktop.mode()
    for key in MODE_FIELDS:
        setattr(mode, key, record[key])
    desktop.apply_mode(mode)
    if mode_record(desktop.mode()) != record:
        raise RuntimeError("Original desktop mode was not restored")


def main():
    parser = argparse.ArgumentParser()
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--prepare", type=Path)
    action.add_argument("--restore", type=Path)
    args = parser.parse_args()
    if os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("RUNNER_OS") != "Windows":
        raise SystemExit("Display changes are restricted to the disposable Windows CI runner")
    desktop = Desktop()
    desktop.physical_coordinates()
    if args.restore:
        receipt = json.loads(args.restore.read_text(encoding="utf-8"))
        restore_mode(desktop, receipt["originalMode"])
        receipt["restoredMode"] = mode_record(desktop.mode())
        args.restore.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        return
    original = desktop.mode()
    receipt = {"originalMode": mode_record(original), "originalWorkArea": desktop.primary_work_area(),
               "registryUpdated": False, "taskbarChanged": False}
    args.prepare.parent.mkdir(parents=True, exist_ok=True)
    with args.prepare.open("x", encoding="utf-8") as output:
        output.write(json.dumps(receipt, indent=2) + "\n")
    try:
        work = receipt["originalWorkArea"]
        if work[2] - work[0] < 1000 or work[3] - work[1] < 850:
            candidate = choose_mode(desktop.modes(), original)
            desktop.apply_mode(candidate)
        receipt.update({"preparedMode": mode_record(desktop.mode()), "preparedWorkArea": desktop.primary_work_area()})
        work = receipt["preparedWorkArea"]
        if work[2] - work[0] < 1000 or work[3] - work[1] < 850:
            raise RuntimeError("Prepared desktop still has insufficient visible work area")
        args.prepare.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    except Exception:
        restore_mode(desktop, receipt["originalMode"])
        raise


if __name__ == "__main__":
    main()
