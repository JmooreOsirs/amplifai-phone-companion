"""Windows source candidate. Launch only with the matching bundled helper."""
from __future__ import annotations

import ctypes
import math
import queue
import sys
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import messagebox, ttk

from amplifai_phone.companion_status import CompanionIdentity, CompanionStatusServer
from updater import BUNDLE_ID, BUILD, CHANNEL, VERSION, PreparedUpdate, UpdateError, check_update, cleanup_owned_updates

from helper_process import HelperProcess, bundled_resources
from native_run import AuthorityWorker, NativeRun, Operation, Outcome
from native_setup import ClientIdentity, DISCLOSURE, SetupLink, SetupProtocolError
from setup_delivery import DeliveryError, SetupDelivery, launch_uri
from session import BACKUP_CHECK_MODE, DISCLOSURE_VERSION, WEBSITE_ORIGIN, ProtocolError, Session, contact_source_details

# Exact values from collector/macos/Sources/Support/Brand.swift.
BACKGROUND, PANEL, BORDER = "#0f1525", "#141d30", "#263146"
HEADING, BODY, MUTED = "#edeff3", "#c8cfd9", "#a1a9b5"
LIME, BLUE = "#a6e63d", "#5c87df"
GUARANTEE = "Data & Privacy Guarantee: What We Collect, How It's Protected, and Why"
WINDOW_TITLE = "AMPLIFai Phone · Windows unsigned TEST · " + VERSION
SETUP_STEPS = (
    ("Connect your iPhone", "Use a data-capable USB cable and unlock the iPhone. Open Apple Devices on this PC and decide any normal Trust prompt yourself. The read-only check uses an existing trusted connection."),
    ("Check the backup setting", "Choose Check iPhone setting below. It reads the current encryption setting without starting a backup or changing the phone. On, Off and Unknown describe this check only."),
    ("Prepare a backup if you choose", "Apple Devices → your iPhone → General → Backups. If you choose Encrypt local backup, keep the password. Confirming can start a separate backup and overwrite older local backups; allow PC storage. AMPLIFai cannot change encryption or reset the password."),
    ("Finish, then return", "Let any Apple Devices backup finish before collecting here; it is a separate backup stored on this PC. Recheck if needed, then review the collection agreement below. Even encrypted backups may lack older Calls. Captured coverage is shown after collection."),
)


def fitted_window_bounds(client, frame, outer, work, minimum):
    """Fit the visible frame, retaining the minimum usable client on small displays."""
    client_size = (client[2] - client[0], client[3] - client[1])
    frame_extra = (frame[2] - frame[0] - client_size[0], frame[3] - frame[1] - client_size[1])
    size = tuple(max(minimum[axis], min(client_size[axis], work[axis + 2] - work[axis] - frame_extra[axis]))
                 for axis in (0, 1))
    # A display smaller than the minimum cannot fit the controls; keep its title accessible.
    origin = tuple(max(work[axis], min(frame[axis], work[axis + 2] - size[axis] - frame_extra[axis]))
                   + outer[axis] - frame[axis] for axis in (0, 1))
    return (*origin, *(origin[axis] + size[axis] + outer[axis + 2] - outer[axis] - client_size[axis]
                       for axis in (0, 1)))


def fit_startup_window(window):
    if sys.platform != "win32":
        return
    import win32api
    import win32con
    import win32gui

    window.update_idletasks()
    logical_size = (window.winfo_width(), window.winfo_height())
    logical_minimum = window.minsize()
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    user.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    user.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    user.GetAncestor.restype = ctypes.c_void_p
    dwm = ctypes.WinDLL("dwmapi")
    dwm.DwmGetWindowAttribute.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
    dwm.DwmGetWindowAttribute.restype = ctypes.c_int32
    # The window keeps its existing DPI mode and fonts; only these API calls use physical pixels.
    previous = user.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
    if not previous:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        client_id = window.winfo_id()
        hwnd = user.GetAncestor(client_id, 2)  # GA_ROOT: Tk's decorated native window.
        if not hwnd:
            raise ctypes.WinError(ctypes.get_last_error())
        work = win32api.GetMonitorInfo(win32api.MonitorFromWindow(hwnd, win32con.MONITOR_DEFAULTTONEAREST))["Work"]
        client = win32gui.GetClientRect(client_id)
        outer = win32gui.GetWindowRect(hwnd)
        frame = (ctypes.c_int32 * 4)()
        if dwm.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(frame), ctypes.sizeof(frame)) != 0:  # EXTENDED_FRAME_BOUNDS
            raise OSError("Could not measure the visible window frame")
        minimum = tuple(math.ceil(logical_minimum[axis] * (client[axis + 2] - client[axis]) / logical_size[axis])
                        for axis in (0, 1))
        bounds = fitted_window_bounds(client, frame, outer, work, minimum)
        if bounds != outer:
            win32gui.SetWindowPos(hwnd, 0, bounds[0], bounds[1], bounds[2] - bounds[0], bounds[3] - bounds[1],
                                  win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE)
    finally:
        if not user.SetThreadDpiAwarenessContext(previous):
            raise ctypes.WinError(ctypes.get_last_error())
    window.update_idletasks()


def window_title(state: Session) -> str:
    if not state.running and state.inspected and not state.needs_inspection and not state.residue:
        return WINDOW_TITLE + " · ready"
    if not state.running and state.inspected and state.residue:
        return WINDOW_TITLE + " · temporary data remains"
    if not state.running and state.phase == "error":
        return WINDOW_TITLE + " · inspection failed"
    return WINDOW_TITLE


def workspace_summary(state: Session) -> tuple[str, str]:
    """Bounded navigation copy; full source/status evidence stays in Details."""
    coverage = "Call and message coverage has not been read."
    if state._capture_received:
        coverage = " · ".join(
            f"{label} unavailable" if source in state.missing else f"{label}: {count:,} readable"
            for source, label, count in (("calls", "Calls", state.source_counts[0]),
                                         ("messages", "Messages", state.source_counts[1])))
    if state.saved_acknowledged:
        status = "Account Save acknowledged. See recovery for cleanup."
    elif state.save_receipt_pending:
        status = "Save receipt pending. Keep this app open."
    elif state.phase in {"error", "cancelling"}:
        status = "Collection needs attention. Open Details & recovery."
    elif state._selection_change_pending:
        status = "Changing contacts. Waiting for the current handoff."
    elif state._browser_received:
        status = "Preview received. Account Save is still required."
    elif state.destination_approved:
        status = "Destination confirmed. Account Save is still required."
    elif state.destination_intent_id:
        status = "Confirm the signed-in account before preview transfer."
    elif state.reviewed:
        status = "Reviewed locally. Connect does not save."
    elif state._pending_review is not None:
        status = "Preparing the selected metadata for local review."
    elif not state._capture_received:
        status = "Local operation in progress. Open Details & recovery." if state.running else "Open Setup to review the collection agreement."
    elif not state.contacts:
        status = "No contacts on this page. Review source coverage in Details."
    else:
        status = "Select contacts, then review the selected metadata."
    return coverage, status


class PhoneWindow:
    def __init__(self, window: tk.Tk, helper: Path, logo: Path, *, delivery=None) -> None:
        self.window, self.state = window, Session()
        self.events: queue.Queue = queue.Queue(maxsize=8)
        self.helper = HelperProcess([str(helper)], self.events.put)
        self.native = NativeRun(ClientIdentity(VERSION, BUILD))
        self.native_worker = AuthorityWorker()
        self.delivery = delivery
        self._native_authority_sent = None
        self.native_checked = tk.BooleanVar(value=False)
        self.native_scope = tk.StringVar(value="Waiting for the authenticated setup destination.")
        self.native_status = tk.StringVar()
        self.checked = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value=self.state.status)
        self.transfer_status = tk.StringVar(value=self.state.transfer_status)
        self.coverage_status = tk.StringVar(value=self.state.coverage_status)
        self.pair_code = tk.StringVar(value="No browser pairing has been created.")
        self.contact_query = tk.StringVar(value="")
        self.contact_page = tk.StringVar(value="No contacts loaded.")
        self.workspace_coverage = tk.StringVar()
        self.workspace_status = tk.StringVar()
        self.destination_status = tk.StringVar(value="Connect from your signed-in account after reviewing contacts.")
        self.guide_step = 0
        self.guide_heading = tk.StringVar()
        self.backup_setting = tk.StringVar(value=self.state.backup_check_status)
        self.activity_stage = tk.StringVar(value=self.state.activity_stage)
        self.progress_caption = tk.StringVar(value=self.state.progress_caption)
        self.activity_measurement = tk.StringVar(value="")
        self.source_details_window = None
        self._rendering_contacts = False
        self.update_events: queue.Queue = queue.Queue(maxsize=8)
        self.update_cancelled = threading.Event()
        self.update_lock = threading.Lock()
        self.update_state = "deferred"
        self.update_message = tk.StringVar(value="Updates are checked after temporary data inspection. This is an unsigned Windows TEST.")
        self.prepared_update: PreparedUpdate | None = None
        self.handoff_preparing = False
        self.status_owned = True
        self._startup_update_pending = True
        self._queued_update_check = False
        self._status_snapshot = {"busy": True, "updateState": "deferred", "availableVersion": None, "availableBuild": None}
        self.status_server = CompanionStatusServer(
            CompanionIdentity("windows", "x86_64", CHANNEL, BUNDLE_ID, VERSION, BUILD),
            status=lambda: dict(self._status_snapshot), request_update_check=self.enqueue_update_check,
            native_setup_v2=delivery is not None,
        )
        self.window.title(WINDOW_TITLE)
        self.window.geometry("920x800")
        self.window.minsize(680, 680)
        self.window.configure(background=BACKGROUND)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self._style()
        self._layout(logo)
        fit_startup_window(self.window)
        self.window.after(100, self.pump)
        try:
            self.status_server.start()
        except OSError:
            self.status_owned = False
            self.state.status = "Another companion owns the local status port. Close it, then relaunch this app before collecting."
            self.update_message.set("Another companion is running. Collection and updates are paused in this window.")
            self.update_state = "deferred"
            self.refresh()
        else:
            cleanup_owned_updates()
            self.start("inspect")

    @property
    def native_active(self):
        return getattr(self, "native", None) is not None and self.native.active

    @property
    def native_busy(self):
        return self.native_active and self.native.phase not in ("cancelled", "refused", "complete")

    @property
    def native_selection_locked(self):
        return self.native_active and self.native.continued_selection is not None

    @property
    def can_return_to_browser(self):
        return (self.native_active and self.native.continue_url is not None
                and self.state.handoff_id == self.native.handoff_id
                and not self.state._browser_received and not self.state.save_receipt_pending
                and not self.state.saved_acknowledged and not self.state._stopping)

    def open_account(self):
        url = self.native.account_url if self.native_active else WEBSITE_ORIGIN + "/phone/account"
        try:
            opened = webbrowser.open(url)
        except OSError:
            opened = False
        if self.native_active and not opened:
            self.native.message = "The browser did not open. Return to your original account tab."
            self.refresh()

    def continue_in_browser(self):
        if not self.can_return_to_browser:
            return
        try:
            opened = webbrowser.open(self.native.continue_url)
        except OSError:
            opened = False
        self.native.message = ("Return to your account tab to finish saving. Browser recovery may open another tab or profile. Keep this review open until the save receipt arrives."
            if opened else "The browser did not open. Return to your original account tab to finish saving. Keep this review open until the save receipt arrives.")
        self.refresh()

    def receive_setup(self, uri):
        if uri is None:
            self.window.deiconify()
            return "accepted"
        try:
            link = SetupLink.parse(uri)
            if (self.native.claim_request is not None and link == self.native.claim_request.link
                    and self.native.phase not in ("cancelled", "refused")):
                self.show_workspace("setup")
                return "duplicate"
            inspecting = self.state.running and self.state.mode == "inspect" and not self.state._capture_received
            if (self.native_busy or not self.native_worker.idle or not self.status_owned
                    or self.update_state == "installing"
                    or not ((self.state.can_update and not self.native_busy) or inspecting)):
                return "busy"
            self.native = NativeRun(ClientIdentity(VERSION, BUILD))
            self.state.approve(False)
            self.checked.set(False)
            self.native_checked.set(False)
            self._native_authority_sent = None
            self.dispatch_native(self.native.claim_link(uri))
            self.show_workspace("setup")
            self.refresh()
            return "accepted"
        except SetupProtocolError:
            return "unavailable"

    def dispatch_native(self, operation):
        if operation is not None and not self.native_worker.submit(operation):
            self.native.complete(operation, Outcome("uncertain"))
        self.sync_native_authority()

    def native_action(self, method):
        if self.native.pending is not None or not self.native_worker.idle:
            return
        try:
            self.dispatch_native(method())
        except SetupProtocolError as error:
            self.native.message = str(error)
        self.refresh()

    def approve_native(self):
        self.native.agree(self.native_checked.get())
        self.native_action(self.native.approve)

    def toggle_native_agreement(self):
        self.native.agree(self.native_checked.get())
        self.refresh()

    def start_native(self):
        if (not self.state.inspected or self.state.needs_inspection or self.state.residue or self.state.running
                or not self.status_owned or self.update_state == "installing"):
            return
        self.native_action(self.native.start)

    def cancel_native(self):
        if self.state.running and self.state.mode == "connect":
            self.stop()
            return
        self.native.cancel()
        self.native_checked.set(False)
        self.sync_native_authority()
        self.refresh()

    def sync_native_authority(self):
        if not self.native_active or self.native.phase == "complete":
            return
        if self.state.handoff_id and self.state.handoff_id == self.native.handoff_id:
            value = (self.state.handoff_id, self.native.authority)
            if value != self._native_authority_sent:
                self.send({"action": "run-authority", "handoffId": value[0], "state": value[1]})
                self._native_authority_sent = value

    def native_destination(self):
        if not self.native_active or not self.state.can_decide_destination:
            return
        if self.native.intent_id and self.native.intent_id != self.state.destination_intent_id:
            self.native.supersede_intent()
            self.sync_native_authority()
        if (self.native.phase == "bound" and self.native.pending is None and self.native_worker.idle
                and self.native.intent_id != self.state.destination_intent_id):
            self.native_action(lambda: self.native.verify(self.state.destination_intent_id,
                self.state.destination_email, self.state.destination_course))

    def handle_native_result(self, result):
        if isinstance(result, Operation):
            self.dispatch_native(result)
        elif result == "start":
            if (not self.state.running and self.state.inspected and not self.state.needs_inspection
                    and not self.state.residue and self.native.current):
                self.state.approve(True)
                self.start("connect", native_admitted=True)
            else:
                self.native.cancel()
        elif result == "pair":
            if (self.state.can_pair and self.native.continued_selection == tuple(sorted(self.state.selected_ids))
                    and set(self.state.selection) == self.state.selected_ids):
                self.send(self.state.pair())
            else:
                self.native.cancel()
        elif result == "bound":
            self.native_destination()
            self.native.message = "Return to your original account tab to finish saving. If it is missing, choose Continue in browser. Keep this review open until the save receipt arrives."
        elif result == "destination_verified":
            if (self.native.current and self.state.can_decide_destination
                    and self.native.handoff_id == self.state.handoff_id
                    and self.native.intent_id == self.state.destination_intent_id
                    and self.native.continued_selection == tuple(sorted(self.state.selected_ids))):
                self.send(self.state.decide_destination(True))
        self.sync_native_authority()

    def pump_native(self):
        if not hasattr(self, "native"):
            return
        if self.delivery is not None:
            self.delivery.drain(self.receive_setup)
            if self.delivery.failed:
                self.native_status.set("Setup delivery is unavailable. Finish local work, reopen the companion and retry from your account.")
        while not self.native_worker.results.empty():
            operation, outcome = self.native_worker.results.get_nowait()
            self.handle_native_result(self.native.complete(operation, outcome))
        self.native.timeout()
        if (self.native.current and self.native.phase in ("collecting", "bound")
                and self.native.authority == "current" and self.native.pending is None
                and self.native_worker.idle and self.native.monotonic() >= self.native.next_refresh):
            self.dispatch_native(self.native.refresh())
        self.native_destination()
        self.sync_native_authority()
        if self.native.stop_requested:
            self.native.stop_requested = False
            if self.state.running and self.state.mode == "connect" and not self.state._stopping:
                self.state.review_discarded = self.state._capture_received and not self.state.saved_acknowledged
                self.send(self.state.request_stop())
                self.clear_password()
                self.helper.close_input()

    def _layout_native_setup(self, host):
        pane = self.native_pane = ttk.Frame(host)
        pane.grid(row=0, column=0, sticky="nsew")
        pane.columnconfigure(0, weight=1)
        pane.rowconfigure(2, weight=1)
        ttk.Label(pane, text="Phone collection", style="Heading.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 8))
        scope = ttk.Label(pane, textvariable=self.native_scope, justify="left")
        scope.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        text_host = ttk.Frame(pane)
        text_host.grid(row=2, column=0, sticky="nsew", pady=(0, 8))
        text_host.columnconfigure(0, weight=1)
        text_host.rowconfigure(0, weight=1)
        self.native_disclosure = self.readable_text(text_host, height=4)
        self.native_disclosure.pack_forget()
        self.native_disclosure.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(text_host, command=self.native_disclosure.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.native_disclosure.configure(yscrollcommand=scrollbar.set, state="normal")
        self.native_disclosure.insert("1.0", DISCLOSURE["sentences"][0])
        self.native_disclosure.configure(state="disabled")
        def focus_text(event, backwards=False):
            target = event.widget.tk_focusPrev() if backwards else event.widget.tk_focusNext()
            target.focus_set()
            return "break"
        self.native_disclosure.bind("<Tab>", focus_text)
        self.native_disclosure.bind("<Shift-Tab>", lambda event: focus_text(event, True))
        self.native_agreement = ttk.Checkbutton(pane, text=DISCLOSURE["agreement"],
            variable=self.native_checked, command=self.toggle_native_agreement,
            style="Disclosure.TCheckbutton")
        self.native_agreement.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        status = ttk.Label(pane, textvariable=self.native_status, justify="left")
        status.grid(row=4, column=0, sticky="ew", pady=(0, 8))
        primary = ttk.Frame(pane)
        primary.grid(row=5, column=0, sticky="ew", pady=(0, 8))
        self.native_approve = ttk.Button(primary, text="Agree", style="Primary.TButton", command=self.approve_native)
        self.native_start = ttk.Button(primary, text="Start", style="Primary.TButton", command=self.start_native)
        self.native_retry = ttk.Button(primary, text="Retry confirmation", command=lambda: self.native_action(self.native.retry))
        for button in (self.native_approve, self.native_start, self.native_retry):
            button.pack(side="left", padx=(0, 8))
        secondary = ttk.Frame(pane)
        secondary.grid(row=6, column=0, sticky="ew", pady=(0, 8))
        self.native_cancel = ttk.Button(secondary, text="Cancel setup", command=self.cancel_native)
        self.native_cancel.pack(side="left", padx=(0, 8))
        self.native_account_button = ttk.Button(secondary, text="Open account", command=self.open_account)
        self.native_account_button.pack(side="left")
        links = ttk.Frame(pane)
        links.grid(row=7, column=0, sticky="ew")
        for title, href in (("Account privacy", DISCLOSURE["privacyHref"]), ("Windows privacy", DISCLOSURE["supplementHref"])):
            ttk.Button(links, text=title, command=lambda path=href: webbrowser.open(WEBSITE_ORIGIN + path)).pack(side="left", padx=(0, 8))
        def resize(event):
            for label in (scope, status):
                label.configure(wraplength=event.width)
            ttk.Style(self.window).configure("Disclosure.TCheckbutton", wraplength=max(240, event.width - 32))
        pane.bind("<Configure>", resize)
        pane.grid_remove()

    def refresh_native(self):
        if not self.native_active:
            return
        run = self.native
        scope = run.run.scope if run.run else run.claim.scope if run.claim else None
        self.native_scope.set("Account save confirmed. Open results returns to this report." if run.completion
            else "Confirm the account, course and sources below." if scope else "Waiting for the authenticated setup destination.")
        labels = {"claiming": "Confirming your account setup…", "pending_approval": "Check the agreement, then choose Agree. Agree does not collect.",
            "approving": "Confirming your agreement…", "prepared": "Approved. Choose Start when your iPhone is ready.",
            "starting": "Confirming permission to start this collection…", "collecting": "Collection started. Review your contacts before Continue.",
            "awaiting_pair": "Preparing your selected metadata…", "binding": "Confirming the reviewed selection…",
            "bound": "Finish in your account tab. Keep this review open until the save receipt arrives.",
            "complete": "Account save confirmed. Check temporary data cleanup in Details & recovery."}
        status = run.message or labels.get(run.phase, "Return to your account to continue.")
        self.native_status.set(status if len(status) <= 180 else "Read the confirmation details above, then return to your account or retry confirmation.")
        disclosure_text = ((f"Account: {scope.email}\nCourse: {scope.course}\nAccount save sources: {', '.join(scope.categories)}\n\n")
                           if scope else "") + DISCLOSURE["sentences"][0]
        if len(status) > 180:
            disclosure_text += "\n\n" + status
        if disclosure_text != getattr(self, "_native_disclosure_text", None):
            self.native_disclosure.configure(state="normal")
            self.native_disclosure.delete("1.0", tk.END)
            self.native_disclosure.insert("1.0", disclosure_text)
            self.native_disclosure.configure(state="disabled")
            self._native_disclosure_text = disclosure_text
        idle = run.pending is None and self.native_worker.idle
        clean = self.state.inspected and not self.state.needs_inspection and not self.state.residue and not self.state.running
        self.native_agreement.configure(state="normal" if run.phase == "pending_approval" and run.claim_current and idle else "disabled")
        self.native_approve.configure(state="normal" if run.phase == "pending_approval" and run.claim_current and self.native_checked.get() and idle and clean else "disabled")
        self.native_start.configure(state="normal" if run.phase == "prepared" and run.current and idle and clean else "disabled")
        recoverable = run.phase in ("claim_uncertain", "approval_uncertain", "start_uncertain", "continue_uncertain", "bind_uncertain") or (run.current and run.authority == "paused" and run.phase in ("collecting", "bound"))
        self.native_retry.configure(state="normal" if idle and recoverable else "disabled")
        self.native_cancel.configure(state="disabled" if run.phase in ("cancelled", "complete") else "normal")
        account_label = ("Open results" if run.completion else "Check saved report" if run.phase == "handoff_expired"
                         else "Account controls" if run.run else "Open account")
        self.native_account_button.configure(text=account_label)
        self.open_account_button.configure(text=account_label)
        self.account_details_label.configure(text="Continue saves only the reviewed selection to the account and course approved in Setup. Keep this window open for the matching account-save receipt. Return to the original account tab to stop the server setup or recover a completed report.")

    def _style(self) -> None:
        style = ttk.Style(self.window)
        style.theme_use("clam")
        style.configure("TFrame", background=BACKGROUND)
        style.configure("TLabel", background=BACKGROUND, foreground=BODY, font=("Arial", 12))
        style.configure("Heading.TLabel", foreground=HEADING, font=("Arial", 22, "bold"))
        style.configure("TButton", background=PANEL, foreground=HEADING, bordercolor=BORDER, padding=(16, 12), font=("Arial", 11, "bold"))
        style.map("TButton", background=[("active", BORDER)], foreground=[("disabled", MUTED)])
        style.configure("Primary.TButton", background=LIME, foreground=BACKGROUND)
        style.map("Primary.TButton", background=[("disabled", BORDER), ("active", LIME)], foreground=[("disabled", MUTED)])
        style.configure("TCheckbutton", background=BACKGROUND, foreground=BODY, font=("Arial", 12))
        style.configure("TEntry", fieldbackground=PANEL, foreground=HEADING, padding=8)
        style.configure("Horizontal.TProgressbar", background=LIME, troughcolor=BORDER)

    def label(self, parent, text: str, heading=False):
        item = ttk.Label(parent, text=text, wraplength=820, justify="left", style="Heading.TLabel" if heading else "TLabel")
        item.pack(fill="x", pady=(0, 12))
        return item

    def _layout(self, logo: Path) -> None:
        shell = ttk.Frame(self.window, padding=24)
        shell.pack(fill="both", expand=True)
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(1, weight=1)
        self.navigation = ttk.Frame(shell)
        self.navigation.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self.workspace_buttons = {}
        for name, text in (("contacts", "Contacts"), ("setup", "Setup"), ("details", "Details & recovery")):
            button = ttk.Button(self.navigation, text=text, command=lambda target=name: self.show_workspace(target))
            button.pack(side="left", padx=(0, 8))
            self.workspace_buttons[name] = button
        host = ttk.Frame(shell)
        host.grid(row=1, column=0, sticky="nsew")
        host.columnconfigure(0, weight=1)
        host.rowconfigure(0, weight=1)
        self.details_pane = ttk.Frame(host)
        self.details_pane.grid(row=0, column=0, sticky="nsew")
        self.contact_workspace = ttk.Frame(host)
        self.contact_workspace.grid(row=0, column=0, sticky="nsew")
        self.contact_workspace.grid_remove()
        self._layout_native_setup(host)
        self._visible_workspace = "setup"
        self._workspace_capture = False
        canvas = self.details_canvas = tk.Canvas(self.details_pane, background=BACKGROUND, highlightthickness=0)
        scroll = ttk.Scrollbar(self.details_pane, command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        outer = self.details_content = ttk.Frame(canvas)
        content = canvas.create_window((0, 0), window=outer, anchor="nw")
        outer.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        def resize(event):
            canvas.itemconfigure(content, width=event.width)
            pending = [outer]
            while pending:
                widget = pending.pop()
                pending.extend(widget.winfo_children())
                if isinstance(widget, ttk.Label):
                    widget.configure(wraplength=max(240, event.width))
            ttk.Style(self.window).configure("Disclosure.TCheckbutton", wraplength=max(240, event.width - 32))
        canvas.bind("<Configure>", resize)
        header = ttk.Frame(outer)
        header.pack(fill="x")
        self.logo = tk.PhotoImage(file=str(logo))
        self.logo = self.logo.subsample(max(1, math.ceil(self.logo.width() / 210)))
        ttk.Label(header, image=self.logo).pack(anchor="w", pady=(0, 12))
        self.label(outer, "Connect. Review. Decide.", heading=True)
        ttk.Label(outer, textvariable=self.update_message, wraplength=820, justify="left").pack(fill="x", pady=(0, 8))
        update_actions = ttk.Frame(outer)
        update_actions.pack(fill="x", pady=(0, 16))
        self.check_update_button = ttk.Button(update_actions, text="Check for updates", command=self.request_update_check)
        self.check_update_button.pack(anchor="w")
        self.install_update_button = ttk.Button(update_actions, text="Open verified update installer", command=self.install_update)
        self.install_update_button.pack(anchor="w", pady=(8, 0))
        self.setup_toggle = ttk.Button(outer, text="Hide iPhone setup guide", command=self.toggle_setup_guide)
        self.setup_toggle.pack(anchor="w", pady=(0, 8))
        self.setup_guide = ttk.Frame(outer)
        self.setup_guide.pack(fill="x", pady=(0, 16))
        ttk.Label(self.setup_guide, textvariable=self.guide_heading, style="Heading.TLabel").pack(anchor="w", pady=(0, 8))
        self.guide_text = self.readable_text(self.setup_guide, height=5)
        guide_actions = ttk.Frame(self.setup_guide)
        guide_actions.pack(fill="x", pady=(8, 8))
        self.guide_back = ttk.Button(guide_actions, text="Back", command=lambda: self.move_guide(-1))
        self.guide_back.pack(side="left", padx=(0, 8))
        self.guide_next = ttk.Button(guide_actions, text="Next", command=lambda: self.move_guide(1))
        self.guide_next.pack(side="left", padx=(0, 8))
        self.backup_check = ttk.Button(guide_actions, text="Check iPhone setting", command=lambda: self.start(BACKUP_CHECK_MODE))
        self.backup_check.pack(side="left")
        ttk.Label(self.setup_guide, textvariable=self.backup_setting, wraplength=820, justify="left").pack(fill="x")
        self.render_guide()
        consent = self.legacy_consent = ttk.Frame(outer)
        consent.pack(fill="x")
        self.label(consent, GUARANTEE)
        self.label(consent, "Selected contact names and numbers, call counterpart/time/direction/duration, and message counterpart/time/direction/channel provide relationship context. Message text, attachments, passwords and backups are never handed to the website.")
        self.label(consent, "An iPhone backup can contain private content locally. Cleanup can leave files after interruption. Trust and device permissions are your decision. Agreement permits collection, not marketing or account saving.")
        self.agreement = ttk.Checkbutton(consent, text="I agree to this selected phone metadata collection disclosure.", variable=self.checked, command=self.toggle_agreement, style="Disclosure.TCheckbutton")
        self.agreement.pack(fill="x", pady=(0, 8))
        self.label(consent, "You must check the box and select Agree to continue. Agree alone does not collect. Version: " + DISCLOSURE_VERSION)
        actions = ttk.Frame(consent)
        actions.pack(fill="x", pady=(0, 16))
        primary_actions = ttk.Frame(actions)
        primary_actions.pack(fill="x", pady=(0, 8))
        secondary_actions = ttk.Frame(actions)
        secondary_actions.pack(fill="x")
        self.approve = ttk.Button(primary_actions, text="Agree", command=self.agree, style="Primary.TButton")
        self.approve.pack(side="left", padx=(0, 8))
        self.connect = ttk.Button(primary_actions, text="Connect iPhone", command=lambda: self.start("connect"), style="Primary.TButton")
        self.connect.pack(side="left", padx=(0, 8))
        self.decline_button = ttk.Button(secondary_actions, text="Not now", command=self.decline)
        self.decline_button.pack(side="left", padx=(0, 8))
        self.privacy_button = ttk.Button(secondary_actions, text="Full privacy guarantee", command=lambda: webbrowser.open(WEBSITE_ORIGIN + "/experience/data-privacy-guarantee"))
        self.privacy_button.pack(side="left")
        self.status_details_label = ttk.Label(outer, textvariable=self.status, wraplength=820, justify="left")
        self.status_details_label.pack(fill="x", pady=(0, 8))
        self.activity = ttk.Frame(outer)
        self.activity.pack(fill="x", pady=(0, 12))
        ttk.Label(self.activity, textvariable=self.activity_stage, wraplength=820, style="Heading.TLabel").pack(fill="x", pady=(0, 8))
        self.progress = ttk.Progressbar(self.activity, maximum=100)
        self.progress.pack(fill="x", pady=(0, 12))
        self.progress_label = ttk.Label(self.activity, textvariable=self.progress_caption, wraplength=820, justify="left")
        self.progress_label.pack(fill="x", pady=(0, 8))
        ttk.Label(self.activity, textvariable=self.transfer_status, wraplength=820, justify="left").pack(fill="x", pady=(0, 8))
        ttk.Label(self.activity, textvariable=self.activity_measurement, wraplength=820, justify="left").pack(fill="x")
        password_row = ttk.Frame(outer)
        password_row.pack(fill="x", pady=(0, 12))
        self.password = ttk.Entry(password_row, show="•", width=30)
        self.password.pack(fill="x", pady=(0, 8))
        self.password_button = ttk.Button(password_row, text="Send backup password privately", command=self.send_password)
        self.password_button.pack(anchor="w")
        self.label(outer, "Select contacts in Contacts. Nothing is selected by default; unavailable history is not zero activity.")
        ttk.Label(outer, textvariable=self.coverage_status, wraplength=820, justify="left").pack(fill="x", pady=(0, 8))
        self.diagnostic_details_button = ttk.Button(outer, text="Source diagnostic details", command=self.show_source_diagnostics)
        self.diagnostic_details_button.pack(anchor="w", pady=(0, 8))
        self.account_details_label = self.label(outer, "After reviewing the exact selection, connect from your signed-in website account. Confirm the account and course here before preview transfer. Connect does not save; each source needs separate Save approval in the account.")
        ttk.Label(outer, textvariable=self.destination_status, wraplength=820, justify="left").pack(fill="x", pady=(0, 8))
        ttk.Label(outer, textvariable=self.pair_code, font=("Arial", 18, "bold"), foreground=LIME).pack(anchor="w")
        self.copy_code_button = ttk.Button(outer, text="Copy temporary code for manual recovery", command=self.copy_pair_code)
        self.copy_code_button.pack(anchor="w", pady=(8, 8))
        self.open_account_button = ttk.Button(outer, text="Open approved account browser", command=self.open_account)
        self.open_account_button.pack(anchor="w", pady=(0, 8))
        recovery = ttk.Frame(outer)
        recovery.pack(fill="x")
        self.inspect = ttk.Button(recovery, text="Check temporary data", command=lambda: self.start("inspect"))
        self.inspect.pack(anchor="w", pady=(0, 8))
        self.clear = ttk.Button(recovery, text="Remove marked abandoned data", command=self.clear_residue)
        self.clear.pack(anchor="w", pady=(0, 8))
        self.stop_button = ttk.Button(recovery, text="Request stop", command=self.stop)
        self.stop_button.pack(anchor="w", pady=(0, 8))
        self.force_button = ttk.Button(recovery, text="Force stop", command=self.force)
        self.force_button.pack(anchor="w")
        pending = [outer]
        while pending:
            widget = pending.pop()
            pending.extend(widget.winfo_children())
            widget.bind("<FocusIn>", lambda event: self.reveal_detail_focus(event.widget), add="+")
        self._layout_contacts()
        self.show_workspace("setup")
        self.refresh()

    def _layout_contacts(self) -> None:
        workspace = self.contact_workspace
        workspace.columnconfigure(0, weight=1)
        workspace.rowconfigure(4, weight=1)
        self.workspace_context = ttk.Label(workspace, textvariable=self.workspace_coverage, justify="left")
        self.workspace_context.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        search = ttk.Frame(workspace)
        search.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        self.search_entry = ttk.Entry(search, textvariable=self.contact_query, width=20)
        self.search_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.search_entry.bind("<Return>", lambda _event: self.search_contacts())
        self.search_button = ttk.Button(search, text="Search names", command=self.search_contacts)
        self.search_button.pack(side="left")
        selection = ttk.Frame(workspace)
        selection.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        self.select_all_button = ttk.Button(selection, text="Select all contacts", command=self.select_all_contacts)
        self.select_all_button.pack(side="left", padx=(0, 8))
        self.cancel_select_all_button = ttk.Button(selection, text="Stop check", command=self.cancel_select_all)
        self.cancel_select_all_button.pack(side="left", padx=(0, 8))
        self.clear_all_button = ttk.Button(selection, text="Clear all", command=self.clear_all_contacts)
        self.clear_all_button.pack(side="left")
        paging = ttk.Frame(workspace)
        paging.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        self.previous_button = ttk.Button(paging, text="Previous", command=self.previous_contacts)
        self.previous_button.pack(side="left", padx=(0, 8))
        self.next_button = ttk.Button(paging, text="Next", command=self.next_contacts)
        self.next_button.pack(side="left", padx=(0, 8))
        self.page_label = ttk.Label(paging, textvariable=self.contact_page, justify="left")
        self.page_label.pack(side="left", fill="x", expand=True)
        self.page_label.bind("<Configure>", lambda event: self.page_label.configure(wraplength=event.width))
        contact_frame = ttk.Frame(workspace)
        contact_frame.grid(row=4, column=0, sticky="nsew")
        self.contacts = tk.Listbox(contact_frame, selectmode=tk.EXTENDED, exportselection=False, height=6, background=PANEL, foreground=HEADING, selectbackground=BLUE, selectforeground=BACKGROUND, highlightbackground=BORDER, highlightcolor=LIME, font=("Arial", 12), activestyle="underline")
        scrollbar = ttk.Scrollbar(contact_frame, command=self.contacts.yview)
        self.contacts.configure(yscrollcommand=scrollbar.set)
        self.contacts.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.contacts.bind("<<ListboxSelect>>", self.selection_changed)
        self.workspace_status_label = ttk.Label(workspace, textvariable=self.workspace_status, justify="left")
        self.workspace_status_label.grid(row=5, column=0, sticky="ew", pady=(8, 8))
        self.contact_actions = ttk.Frame(workspace)
        self.contact_actions.grid(row=6, column=0, sticky="ew")
        self.review_button = ttk.Button(self.contact_actions, text="Review selected metadata", command=self.review)
        self.review_button.grid(row=0, column=0, sticky="w", padx=(0, 8), pady=(0, 8))
        self.pair_button = ttk.Button(self.contact_actions, text="Connect signed-in account", command=self.pair)
        self.pair_button.grid(row=0, column=0, sticky="w", padx=(0, 8), pady=(0, 8))
        self.details_button = ttk.Button(self.contact_actions, text="Contact details", command=self.show_source_details)
        self.details_button.grid(row=0, column=1, sticky="w", pady=(0, 8))
        self.change_selection_button = ttk.Button(self.contact_actions, text="Change selected contacts", command=self.change_selection)
        self.change_selection_button.grid(row=1, column=0, sticky="w", padx=(0, 8))
        self.account_recovery_button = ttk.Button(self.contact_actions, text="Account & recovery", command=lambda: self.show_workspace("details", account=True))
        self.account_recovery_button.grid(row=1, column=1, sticky="w")
        def resize(event):
            for label in (self.workspace_context, self.workspace_status_label):
                label.configure(wraplength=event.width)
        workspace.bind("<Configure>", resize)

    def show_workspace(self, name: str, *, account: bool = False) -> None:
        if name not in self.workspace_buttons:
            raise ValueError("Unknown native workspace")
        self._visible_workspace = name
        for target, button in self.workspace_buttons.items():
            button.configure(style="Primary.TButton" if target == name else "TButton")
        self.workspace_buttons[name].focus_set()
        self.native_pane.grid_remove()
        if name == "contacts":
            self.details_pane.grid_remove()
            self.contact_workspace.grid()
        elif name == "setup" and self.native_active:
            self.contact_workspace.grid_remove()
            self.details_pane.grid_remove()
            self.native_pane.grid()
        else:
            self.contact_workspace.grid_remove()
            self.details_pane.grid()
            self.window.update_idletasks()
            anchor = self.account_details_label if account else self.status_details_label
            offset = 0 if name == "setup" else anchor.winfo_y()
            self.details_canvas.yview_moveto(offset / max(1, self.details_content.winfo_height()))

    def reveal_detail_focus(self, widget) -> None:
        """Tab traversal reveals controls inside the bounded details pane."""
        if not self.details_pane.winfo_ismapped() or not widget.winfo_ismapped():
            return
        canvas = self.details_canvas
        top = widget.winfo_rooty() - self.details_content.winfo_rooty()
        bottom = top + widget.winfo_height()
        visible = canvas.canvasy(0)
        height = canvas.winfo_height()
        if top < visible or bottom > visible + height:
            offset = top if top < visible else bottom - height
            canvas.yview_moveto(offset / max(1, self.details_content.winfo_height()))

    def refresh_workspace(self) -> None:
        coverage, status = workspace_summary(self.state)
        if self.native_active and self.state.reviewed and not self.state.handoff_id:
            status = "Review available metadata. Continue saves this selection to the account approved in Setup."
            if self.native.phase == "handoff_expired":
                status = "Handoff expired. Open Setup for account recovery or cancellation."
        elif self.native_active and self.state._browser_received and not self.state.save_receipt_pending and not self.state.saved_acknowledged:
            status = "Preview received. Waiting for the account-save receipt."
        if self.native_active and not self.state.save_receipt_pending and not self.state.saved_acknowledged:
            if self.native.phase.endswith("_uncertain"):
                status = "Confirmation unavailable. Open Setup to retry; the continued selection stays fixed."
            elif self.native.phase == "binding":
                status = "Confirming the reviewed selection. Keep this app open."
            elif self.native.phase == "bound" and self.native.authority == "paused":
                status = "Confirmation paused. Return to the approved account tab or open Setup for recovery."
            elif self.native.phase == "bound" and not self.state._browser_received:
                status = "Return to your original account tab. If it is missing, choose Continue in browser. Keep this review open."
        self.workspace_coverage.set(coverage)
        self.workspace_status.set(status)
        if self.state.reviewed:
            self.review_button.grid_remove()
            self.pair_button.grid()
            if self.window.focus_get() == self.review_button:
                self.pair_button.focus_set()
        else:
            self.pair_button.grid_remove()
            self.review_button.grid()
            if self.window.focus_get() == self.pair_button:
                self.review_button.focus_set()
        if self.state._capture_received and not self._workspace_capture:
            self.show_workspace("contacts")
            self.contacts.focus_set()
        self._workspace_capture = self.state._capture_received

    def readable_text(self, parent, *, height: int):
        value = tk.Text(parent, height=height, wrap="word", background=PANEL, foreground=HEADING,
                        font=("Arial", 12), relief="flat", padx=10, pady=10, takefocus=True)
        value.pack(fill="x")
        value.configure(state="disabled")
        return value

    def render_guide(self) -> None:
        title, detail = SETUP_STEPS[self.guide_step]
        self.guide_heading.set(f"{self.guide_step + 1} of {len(SETUP_STEPS)} · {title}")
        self.guide_text.configure(state="normal")
        self.guide_text.delete("1.0", tk.END)
        self.guide_text.insert("1.0", detail)
        self.guide_text.configure(state="disabled")
        self.guide_back.configure(state="normal" if self.guide_step else "disabled")
        self.guide_next.configure(state="normal" if self.guide_step < len(SETUP_STEPS) - 1 else "disabled")

    def move_guide(self, direction: int) -> None:
        self.guide_step = max(0, min(len(SETUP_STEPS) - 1, self.guide_step + direction))
        self.render_guide()
        self.guide_text.focus_set()

    def toggle_setup_guide(self) -> None:
        if self.setup_guide.winfo_manager():
            self.setup_guide.pack_forget()
            self.setup_toggle.configure(text="Show iPhone setup guide")
        else:
            self.setup_guide.pack(fill="x", pady=(0, 16), after=self.setup_toggle)
            self.setup_toggle.configure(text="Hide iPhone setup guide")

    def show_source_details(self) -> None:
        if not self.state.contacts or self.state.selecting_all or self.state.pending_contact is not None:
            return
        index = self.contacts.index(tk.ACTIVE)
        if not 0 <= index < len(self.state.contacts):
            return
        item = self.state.contacts[index]
        self.show_readonly_details("Contact source details", item["name"] or "Unnamed contact", contact_source_details(item), height=7)

    def show_source_diagnostics(self) -> None:
        if self.state.diagnostic_details:
            self.show_readonly_details("Source diagnostic details", "Current collection and previous runs",
                                       self.state.diagnostic_details, height=18, scrollable=True)

    def show_readonly_details(self, title: str, heading: str, text: str, *, height: int, scrollable: bool = False) -> None:
        self.close_source_details()
        details = tk.Toplevel(self.window)
        self.source_details_window = details
        details.title(title)
        details.configure(background=BACKGROUND)
        details.minsize(400, 240)
        body = ttk.Frame(details, padding=16)
        body.pack(fill="both", expand=True)
        self.label(body, heading, heading=True)
        if scrollable:
            details.maxsize(self.window.winfo_width(), self.window.winfo_height())
            text_frame = ttk.Frame(body)
            text_frame.pack(fill="both", expand=True)
            content = self.readable_text(text_frame, height=height)
            content.pack_configure(side="left", fill="both", expand=True)
            scroll = ttk.Scrollbar(text_frame, command=content.yview)
            content.configure(yscrollcommand=scroll.set)
            scroll.pack(side="right", fill="y")
        else:
            content = self.readable_text(body, height=height)
        content.configure(state="normal")
        content.insert("1.0", text)
        content.configure(state="disabled")
        ttk.Button(body, text="Close details", command=self.close_source_details).pack(anchor="w", pady=(12, 0))
        details.protocol("WM_DELETE_WINDOW", self.close_source_details)
        details.bind("<Escape>", lambda _event: self.close_source_details())
        content.focus_set()

    def close_source_details(self) -> None:
        if self.source_details_window is not None:
            self.source_details_window.destroy()
            self.source_details_window = None

    def agree(self) -> None:
        if self.native_active:
            return
        self.state.approve(self.checked.get())
        self.refresh()

    def toggle_agreement(self) -> None:
        self.state.approve(False)
        self.refresh()

    def decline(self) -> None:
        self.checked.set(False)
        self.state.approve(False)
        self.refresh()

    def start(self, mode: str, *, native_admitted=False) -> None:
        if mode == "connect" and self.native_active and not native_admitted:
            self.start_native()
            return
        if native_admitted and (not self.native.current or not self.native.started or self.native.phase != "collecting"):
            return
        if not getattr(self, "status_owned", True) or getattr(self, "update_state", "") == "installing":
            self.state.status = "Collection is paused while another companion or Windows Installer owns this operation."
            self.refresh()
            return
        try:
            self.state.begin(mode)
        except ProtocolError:
            self.state.status = "Finish the current operation and inspect temporary data before starting another."
            self.refresh()
            return
        self.checked.set(False)
        if mode == "connect":
            self.close_source_details()
            self.contact_query.set("")
            self.contact_page.set("No contacts loaded.")
            self._rendering_contacts = True
            self.contacts.configure(state="normal")
            self.contacts.delete(0, tk.END)
            self.contacts.configure(state="disabled")
            self._rendering_contacts = False
            self.clear_password()
        try:
            self.helper.start(mode)
        except (OSError, RuntimeError):
            self.state.exited(1)
            self.state.status = "The packaged helper could not start. No collection is assumed. Check the package/runtime; temporary data still needs inspection."
            if self.native_active:
                self.native.cancel()
        self.refresh()

    def clear_password(self) -> None:
        # Disabled ttk entries ignore delete; enable only for this atomic clear.
        self.password.configure(state="normal")
        self.password.delete(0, tk.END)
        self.password.configure(state="disabled")

    def cooperative_failure(self, *, clear_password: bool = True) -> None:
        if self.native_active:
            self.native.cancel()
        self.state.cooperative_failure()
        if clear_password:
            self.clear_password()
        self.helper.close_input()

    def send(self, command: dict | None) -> None:
        if command is not None:
            try:
                self.helper.write(command)
            except (OSError, RuntimeError, ValueError):
                self.cooperative_failure()

    def send_password(self) -> None:
        try:
            self.state.write_password(self.password.get(), self.helper.stdin)
        except ProtocolError:
            self.state.status = "No password was sent. Enter the requested backup password."
        except (OSError, RuntimeError, ValueError):
            self.cooperative_failure(clear_password=False)
        finally:
            self.clear_password()
        self.refresh()

    def selection_changed(self, _event=None) -> None:
        if self._rendering_contacts or not self.state.can_select or self.native_selection_locked:
            return
        try:
            self.send(self.state.update_visible_selection(
                [self.state.contacts[index]["id"] for index in self.contacts.curselection()]
            ))
        except (ProtocolError, IndexError):
            self.state.status = "The displayed contact selection changed unexpectedly. Review again."
        self.refresh()

    def render_contacts(self) -> None:
        self.close_source_details()
        self._rendering_contacts = True
        try:
            self.contacts.configure(state="normal")
            self.contacts.delete(0, tk.END)
            for index, item in enumerate(self.state.contacts):
                self.contacts.insert(tk.END, item["name"] + " · phone ending " + ", ".join(item["phoneEnds"]))
                if item["id"] in self.state.selected_ids:
                    self.contacts.selection_set(index)
            self.contact_query.set(self.state.contact_query)
            shown = len(self.state.contacts)
            self.contact_page.set(
                f"{shown} on this page · {self.state.total_contacts} total contacts · "
                f"{len(self.state.selected_ids)} selected across pages"
            )
        finally:
            self._rendering_contacts = False

    def _page(self, method) -> None:
        if self.native_selection_locked:
            return
        try:
            self.send(method())
        except ProtocolError:
            self.state.status = "Wait for the current contact page before navigating."
        self.refresh()

    def search_contacts(self) -> None:
        self._page(lambda: self.state.search_contacts(self.contact_query.get()))

    def next_contacts(self) -> None:
        self._page(self.state.next_contacts)

    def previous_contacts(self) -> None:
        self._page(self.state.previous_contacts)

    def select_all_contacts(self) -> None:
        if self.native_selection_locked:
            return
        try:
            self.send(self.state.select_all_contacts())
        except ProtocolError:
            self.state.status = "Wait for the current page or review before checking all contacts."
        self.refresh()

    def cancel_select_all(self) -> None:
        self.state.cancel_select_all()
        self.refresh()

    def clear_all_contacts(self) -> None:
        if self.native_selection_locked:
            return
        try:
            self.send(self.state.clear_all_contacts())
        except ProtocolError:
            self.state.status = "Wait for the current contact operation before clearing selection."
        self.render_contacts()
        self.refresh()

    def copy_pair_code(self) -> None:
        if self.state.pair_code:
            self.window.clipboard_clear()
            self.window.clipboard_append(self.state.pair_code)

    def review(self) -> None:
        if self.native_selection_locked:
            return
        try:
            self.send(self.state.review(sorted(self.state.selected_ids)))
        except ProtocolError:
            self.state.status = "Select contacts and wait for any pending response before reviewing again."
        self.refresh()

    def change_selection(self) -> None:
        if not self.state.can_change_selection or self.native_selection_locked:
            return
        if messagebox.askyesno("Change selected contacts?", "Close the current browser pairing and return to your selected contacts? The helper will refuse if account saving has started; your reviewed import stays open."):
            try:
                self.send(self.state.request_selection_change())
            except ProtocolError:
                self.state.status = "Finish the current account save before changing contacts."
        self.refresh()

    def pair(self) -> None:
        if self.native_active:
            if self.native.completion:
                self.open_account()
            elif self.can_return_to_browser:
                self.continue_in_browser()
            elif self.state.can_pair:
                self.native_action(lambda: self.native.continue_review(tuple(sorted(self.state.selected_ids))))
            return
        replacing = " A successful new pairing replaces the current browser pairing." if self.state.handoff_id else ""
        if messagebox.askyesno("Connect the signed-in account?", "Create an exact-origin local pairing for " + WEBSITE_ORIGIN + "? Choose Connect on the signed-in account page, then confirm its server-verified destination here. Only the reviewed preview transfers. Each source requires separate account Save approval." + replacing):
            try:
                self.send(self.state.pair())
            except ProtocolError:
                self.state.status = "Review the current selection and wait for any pending response before pairing."
        self.refresh()

    def clear_residue(self) -> None:
        if messagebox.askyesno("Remove marked abandoned data?", "Delete only app-marked abandoned temporary sessions? Other backups are untouched. This cannot be undone."):
            self.start("clear-residue")

    def stop(self) -> None:
        if self.state._capture_received and not self.state.saved_acknowledged:
            if not messagebox.askyesno("Discard local phone review?", "Request stop discards the local reviewed import after the helper exits. Account parts may already be saved, while their final browser receipt may still be pending. Keep this window open and finish in the browser if you want to complete that save. Stop anyway?"):
                return
        if self.native_active:
            self.native.cancel()
            self.sync_native_authority()
        self.state.review_discarded = self.state._capture_received and not self.state.saved_acknowledged
        self.send(self.state.request_stop())
        self.clear_password()
        self.helper.close_input()
        self.refresh()

    def force(self) -> None:
        if not self.state.running or self.state.phase != "cancelling":
            return
        target = self.helper.process
        if messagebox.askyesno("Force stop; cleanup unconfirmed", "The helper may still be collecting. Windows force-stop bypasses graceful cleanup. Private temporary backup data can remain. Stop this owned helper anyway?"):
            if self.helper.process is target and self.state.running and self.state.phase == "cancelling":
                self.state.force_stopped()
                try:
                    self.helper.force_stop()
                except (OSError, RuntimeError):
                    self.state.status = "Force stop could not be confirmed. Cleanup is unconfirmed; wait for the helper and inspect afterwards."
        self.refresh()

    def close(self) -> None:
        if self.state.running:
            messagebox.showinfo("Keep recovery visible", "Request stop first. Keep this window open until the owned helper exits, then inspect temporary data. Force stop does not confirm cleanup.")
        elif self.state._capture_received and not self.state.saved_acknowledged:
            if messagebox.askyesno("Discard unsaved local review?", "Browser received is not saved. Closing discards the private local review, not account records or temporary backup files. Close anyway?"):
                self.shutdown()
        else:
            self.shutdown()

    def refresh(self) -> None:
        state = self.state
        if hasattr(self, "update_message"):
            busy = not state.can_update or self.native_busy or not self.status_owned or self.update_state == "installing"
            manifest = self.prepared_update.manifest if self.prepared_update else None
            self._status_snapshot = {"busy": busy, "updateState": self.update_state,
                                     "availableVersion": manifest.version if manifest else None,
                                     "availableBuild": manifest.build if manifest else None}
            self.check_update_button.configure(state="normal" if self.status_owned and not busy and self.update_state != "checking" else "disabled")
            self.install_update_button.configure(state="normal" if manifest and not busy and self.update_state in {"available", "deferred"} else "disabled")
        self.window.title(window_title(state))
        self.status.set(state.status)
        self.transfer_status.set(state.transfer_status)
        self.coverage_status.set(state.coverage_status)
        self.diagnostic_details_button.configure(state="normal" if state.diagnostic_details else "disabled")
        self.backup_setting.set(state.backup_check_status)
        self.backup_check.configure(state="normal" if state.can_check_backup_setting and getattr(self, "status_owned", True) and getattr(self, "update_state", "") != "installing" else "disabled")
        self.activity_stage.set(state.activity_stage)
        self.progress_caption.set(state.progress_caption)
        self.activity_measurement.set(state.activity_measurement)
        self.details_button.configure(state="normal" if state.contacts and not state.selecting_all and state.pending_contact is None else "disabled")
        self.approve.configure(state="normal" if self.checked.get() and state.inspected and not state.residue and not state.running else "disabled")
        self.connect.configure(state="normal" if state.can_connect and getattr(self, "status_owned", True) and getattr(self, "update_state", "") != "installing" else "disabled")
        self.agreement.configure(state="disabled" if state.running else "normal")
        self.decline_button.configure(state="disabled" if state.running else "normal")
        self.contacts.configure(state="normal" if state.can_select and not state.selecting_all else "disabled")
        self.review_button.configure(state="normal" if state.can_review and state.selected_ids else "disabled")
        self.change_selection_button.configure(state="normal" if state.can_change_selection else "disabled")
        self.search_entry.configure(state="normal" if state.can_select and not state.selecting_all and state.pending_contact is None else "disabled")
        self.search_button.configure(state="normal" if state.can_select and not state.selecting_all and state.pending_contact is None else "disabled")
        self.previous_button.configure(state="normal" if state.can_select and not state.selecting_all and state.pending_contact is None and state.previous_contact_cursors else "disabled")
        self.next_button.configure(state="normal" if state.can_select and not state.selecting_all and state.pending_contact is None and state.next_contact_cursor is not None else "disabled")
        self.select_all_button.configure(state="normal" if state.can_select_all else "disabled")
        self.cancel_select_all_button.configure(state="normal" if state.selecting_all and not state._select_all_cancel else "disabled")
        self.clear_all_button.configure(state="normal" if state.can_select and not state.selecting_all and state.pending_contact is None and state.selected_ids else "disabled")
        if state._capture_received:
            self.contact_page.set((f"Checking {state.select_all_visited:,} / {state.total_contacts:,}\n"
                                   f"{len(state.selected_ids):,} selected; check pending") if state.selecting_all else
                f"{len(state.contacts):,} on page / {state.total_contacts:,} total\n"
                f"{len(state.selected_ids):,} selected across pages")
        else:
            self.contact_page.set("No contacts loaded.")
        self.pair_button.configure(state="normal" if state.can_pair else "disabled")
        self.password_button.configure(state="normal" if state.phase == "password_required" else "disabled")
        self.password.configure(state="normal" if state.phase == "password_required" else "disabled")
        self.inspect.configure(state="disabled" if state.running else "normal")
        self.clear.configure(state="normal" if state.residue and not state.running else "disabled")
        self.stop_button.configure(state="normal" if state.running and state.phase != "cancelling" else "disabled")
        self.force_button.configure(state="normal" if state.running and state.phase == "cancelling" else "disabled")
        known_backup_progress = (state.mode == "connect" and state.running and not state._capture_received and
                                 state.transfer_stage != "processing" and state.phase != "processing" and state.progress is not None)
        if known_backup_progress:
            self.progress.configure(value=state.progress)
            self.progress.pack(fill="x", pady=(0, 12), before=self.progress_label)
        else:
            self.progress.pack_forget()
        self.pair_code.set("Temporary recovery code: " + state.pair_code if state.pair_code else "No browser pairing has been created.")
        self.copy_code_button.configure(state="normal" if state.pair_code else "disabled")
        self.destination_status.set(("Server-confirmed destination: " + state.destination_email + " · " + state.destination_course)
                                    if state.destination_intent_id else "Connect from your signed-in account after reviewing contacts.")
        if self.native_active:
            self.legacy_consent.pack_forget()
            for button in (self.approve, self.connect, self.agreement, self.decline_button):
                button.configure(state="disabled")
            can_continue = (state.can_pair and self.native.current and self.native.phase in ("collecting", "continue_uncertain"))
            return_to_browser = self.can_return_to_browser
            ready = self.native.pending is None and self.native_worker.idle
            self.pair_button.configure(text="Open results" if self.native.completion else "Continue in browser" if return_to_browser else "Continue",
                state="normal" if self.native.completion or ready and (can_continue or return_to_browser) else "disabled")
            if self.native_selection_locked:
                for widget in (self.contacts, self.review_button, self.change_selection_button, self.search_entry,
                               self.search_button, self.previous_button, self.next_button, self.select_all_button,
                               self.cancel_select_all_button, self.clear_all_button):
                    widget.configure(state="disabled")
            self.refresh_native()
        self.refresh_workspace()

    def enqueue_update_check(self) -> bool:
        with self.update_lock:
            if self.update_cancelled.is_set() or self._queued_update_check:
                return False
            try:
                self.update_events.put_nowait({"kind": "check"})
            except queue.Full:
                return False
            self._queued_update_check = True
            return True

    def request_update_check(self) -> None:
        if not self.status_owned or not (self.state.can_update and not self.native_busy) or self.update_state in {"checking", "installing"}:
            if self.update_state not in {"checking", "installing"}:
                self.update_state = "deferred"
                self.update_message.set("Update check deferred. Finish collection, local review, account Save and temporary data inspection first.")
            self.refresh()
            return
        self._startup_update_pending = False
        self.update_state = "checking"
        self.update_message.set("Checking for a compatible Windows update…")
        self.refresh()
        threading.Thread(target=self.prepare_update, daemon=True, name="signed-update-check").start()

    def prepare_update(self) -> None:
        try:
            manifest = check_update(cancelled=self.update_cancelled)
            prepared = PreparedUpdate(manifest, cancelled=self.update_cancelled) if int(manifest.build) > int(BUILD) else None
            with self.update_lock:
                if self.update_cancelled.is_set():
                    if prepared:
                        prepared.cleanup()
                    return
                self.update_events.put_nowait({"kind": "prepared", "update": prepared})
        except (UpdateError, OSError) as error:
            message = str(error) if isinstance(error, UpdateError) else "Verified update could not be prepared locally. Check again later."
            with self.update_lock:
                if not self.update_cancelled.is_set():
                    self.update_events.put_nowait({"kind": "failed", "message": message})

    def pump_updates(self) -> bool:
        while not self.update_events.empty():
            event = self.update_events.get_nowait()
            if event["kind"] == "check":
                with self.update_lock:
                    self._queued_update_check = False
                self.request_update_check()
            elif event["kind"] == "failed":
                self.update_state = "failed"
                self.update_message.set(event["message"] + " The installed app remains usable.")
            elif event["kind"] == "handoff_failed":
                self.handoff_preparing = False
                self.update_state = "failed"
                self.update_message.set("The verified copied updater could not prepare. This companion remains open; check for updates again.")
            elif event["kind"] == "handoff_ready":
                self.handoff_preparing = False
                handoff = event["handoff"]
                if event["prepared"] is not self.prepared_update or not self.status_owned or not (self.state.can_update and not self.native_busy):
                    handoff.abort()
                    self.update_state = "deferred"
                    self.update_message.set("Installation deferred because local work changed. Finish it, then check for updates again.")
                else:
                    try:
                        handoff.commit()
                    except OSError:
                        handoff.abort()
                        self.update_state = "failed"
                        self.update_message.set("Updater commit failed. The companion remains open; check again.")
                    else:
                        self.prepared_update = None
                        self.shutdown()
                        return True
            elif event["kind"] == "prepared":
                if self.prepared_update:
                    self.prepared_update.cleanup()
                self.prepared_update = event["update"]
                if self.prepared_update:
                    manifest = self.prepared_update.manifest
                    self.update_state = "available" if (self.state.can_update and not self.native_busy) else "deferred"
                    label = "unsigned Windows TEST" if manifest.trust == "unsigned-test" else "Windows update"
                    self.update_message.set(f"Verified {manifest.version} {label} is ready. Open its installer when collection and unsaved work are finished.")
                else:
                    self.update_state = "current"
                    self.update_message.set(f"{VERSION} is current for the signed Windows x64 Test feed. This app is an unsigned TEST.")
        if self._startup_update_pending and self.status_owned and (self.state.can_update and not self.native_busy):
            self.request_update_check()
        return False

    def install_update(self) -> None:
        prepared = self.prepared_update
        if not prepared or not self.status_owned or not (self.state.can_update and not self.native_busy) or self.update_state not in {"available", "deferred"}:
            return
        trust = "This is an unsigned Windows TEST. Windows security may warn or refuse it." if prepared.manifest.trust == "unsigned-test" else "Windows Installer and Windows security control this installation."
        if not messagebox.askyesno("Open verified Windows update?", f"Install {prepared.manifest.version}?\n\n{trust}\nThis app will close, then Windows Installer will open. Reopen AMPLIFai Phone after installation finishes."):
            return
        # Tk dialogs process events; collection may have started during the decision.
        if prepared is not self.prepared_update or not self.status_owned or not (self.state.can_update and not self.native_busy) or self.update_state not in {"available", "deferred"}:
            self.update_message.set("Installation deferred because local work changed. Finish it, then select the installer again.")
            self.refresh()
            return
        self.handoff_preparing = True
        self.update_state = "installing"
        self.update_message.set("Preparing the verified update. This app will close when Windows Installer is ready.")
        self.refresh()
        helper = Path(self.helper.command[0])
        threading.Thread(target=self.prepare_handoff, args=(prepared, helper), daemon=True, name="verified-update-handoff").start()

    def prepare_handoff(self, prepared: PreparedUpdate, helper: Path) -> None:
        try:
            handoff = prepared.launch(helper)
            with self.update_lock:
                if self.update_cancelled.is_set():
                    handoff.abort()
                    return
                self.update_events.put_nowait({"kind": "handoff_ready", "handoff": handoff, "prepared": prepared})
        except (OSError, ValueError):
            with self.update_lock:
                if not self.update_cancelled.is_set():
                    self.update_events.put_nowait({"kind": "handoff_failed"})

    def shutdown(self) -> None:
        if hasattr(self, "native_worker"):
            self.native.cancel()
            self.native_worker.close()
        if getattr(self, "delivery", None) is not None:
            self.delivery.stop()
        if hasattr(self, "status_server"):
            with self.update_lock:
                self.update_cancelled.set()
                while not self.update_events.empty():
                    pending = self.update_events.get_nowait()
                    if pending["kind"] == "prepared" and pending["update"]:
                        pending["update"].cleanup()
                    elif pending["kind"] == "handoff_ready":
                        pending["handoff"].abort()
            self.status_server.stop()
            if self.prepared_update and not self.handoff_preparing:
                self.prepared_update.cleanup()
        self.window.destroy()

    def pump(self) -> None:
        if hasattr(self, "update_events"):
            try:
                if self.pump_updates():
                    return
            except (OSError, UpdateError):
                self.update_state = "failed"
                self.update_message.set("Update preparation or verified download cleanup failed. Current collection and review remain open; check again later.")
        try:
            while not self.events.empty():
                event = self.events.get_nowait()
                if not isinstance(event, dict):
                    raise ProtocolError("Invalid helper event")
                if event.get("kind") == "process-exit":
                    mode = self.state.mode
                    self.state.exited(event.get("code"))
                    if mode == "connect" and self.native_active and not self.state.saved_acknowledged:
                        self.native.cancel()
                    if mode != "inspect":
                        self.start("inspect")
                else:
                    command = self.state.handle(event)
                    self.send(command)
                    if event.get("kind") == "pairing" and self.native_active and self.state.handoff_id == event.get("handoffId"):
                        self.dispatch_native(self.native.pair_received(self.state.handoff_id, event["payloadSha256"]))
                    if event.get("kind") in {"capture", "contacts"} and self.state._capture_received and not self.state.selecting_all:
                        self.render_contacts()
                    if event.get("kind") == "destination" and self.native_active:
                        self.native_destination()
                        self.state.status = ("Approved account and course confirmed for this reviewed selection. Waiting for the account-save receipt."
                            if self.state.destination_approved else "Confirming the approved account and course for this reviewed selection.")
                    elif event.get("kind") == "destination" and self.state.can_decide_destination:
                        destination = (self.state.handoff_id, self.state.destination_intent_id)
                        approved = messagebox.askyesno(
                            "Confirm signed-in account destination",
                            f"Account: {self.state.destination_email}\nCourse: {self.state.destination_course}\n\n"
                            "These details came from AMPLIFai's authenticated server. Connect sends the reviewed preview only. Each source still needs separate Save approval in the account. Connect to this account?",
                        )
                        # Native dialogs can process other events while an answer is pending.
                        if (destination == (self.state.handoff_id, self.state.destination_intent_id)
                                and self.state.can_decide_destination):
                            self.send(self.state.decide_destination(approved))
                    if event.get("kind") == "review" and self.state.reviewed:
                        selected, calls, messages = self.state.review_counts
                        calls_summary = "Calls unavailable" if "calls" in self.state.missing else f"{calls} matching calls"
                        messages_summary = "Messages unavailable" if "messages" in self.state.missing else f"{messages} matching messages"
                        self.state.status = (f"{selected} selected contacts · {calls_summary} · {messages_summary}. "
                            + ("Check source coverage before Continue saves to your approved account and course." if self.native_active
                               else "Check source coverage below before account Save."))
                    if event.get("kind") == "handoff" and event.get("state") == "expired" and self.native_active:
                        self.native.handoff_expired()
                        self.state.status = self.native.message
                    if event.get("kind") == "handoff" and self.state.saved_acknowledged:
                        if self.native_active:
                            self.native.record_saved(self.state.handoff_id)
                            self.native_checked.set(False)
                        if not self.state._finishing:
                            self.send(self.state.finish())
                    if event.get("kind") == "error" and self.state.phase == "error":
                        self.cooperative_failure()
            self.send(self.state.poll())
            self.pump_native()
        except (OSError, RuntimeError, ValueError):
            self.cooperative_failure()
        self.refresh()
        self.window.after(1000, self.pump)


def main() -> int:
    if sys.platform != "win32":
        raise SystemExit("Windows source candidate; no Windows runtime proof on this platform.")
    try:
        uri = launch_uri(sys.argv[1:])
        helper, logo = bundled_resources(Path(sys.executable), frozen=bool(getattr(sys, "frozen", False)))
    except ValueError:
        ctypes.windll.user32.MessageBoxW(None, "Open the installed companion with one setup link from your account. A matching packaged helper is required.", "AMPLIFai Phone setup", 0x10)
        return 2
    delivery = None
    try:
        delivery = SetupDelivery()
        if not delivery.start():
            delivery.forward(uri)
            return 0
        window = tk.Tk()
        view = PhoneWindow(window, helper, logo, delivery=delivery)
        if uri is not None and view.receive_setup(uri) not in ("accepted", "duplicate"):
            messagebox.showerror("Setup not opened", "Finish local work and temporary data inspection, then retry from the original account tab.")
        window.mainloop()
        return 0
    except (DeliveryError, OSError):
        ctypes.windll.user32.MessageBoxW(None, str(DeliveryError()), "AMPLIFai Phone setup", 0x10)
        return 2
    finally:
        if delivery is not None:
            delivery.stop()


if __name__ == "__main__":
    raise SystemExit(main())
