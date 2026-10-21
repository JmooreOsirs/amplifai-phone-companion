"""Windows source candidate. Launch only with the matching bundled helper."""
from __future__ import annotations

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
    def __init__(self, window: tk.Tk, helper: Path, logo: Path) -> None:
        self.window, self.state = window, Session()
        self.events: queue.Queue = queue.Queue(maxsize=8)
        self.helper = HelperProcess([str(helper)], self.events.put)
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
        )
        self.window.title(WINDOW_TITLE)
        self.window.geometry("920x800")
        self.window.minsize(680, 680)
        self.window.configure(background=BACKGROUND)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self._style()
        self._layout(logo)
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
        self.label(outer, GUARANTEE)
        self.label(outer, "Selected contact names and numbers, call counterpart/time/direction/duration, and message counterpart/time/direction/channel provide relationship context. Message text, attachments, passwords and backups are never handed to the website.")
        self.label(outer, "An iPhone backup can contain private content locally. Cleanup can leave files after interruption. Trust and device permissions are your decision. Agreement permits collection, not marketing or account saving.")
        self.agreement = ttk.Checkbutton(outer, text="I agree to this selected phone metadata collection disclosure.", variable=self.checked, command=self.toggle_agreement, style="Disclosure.TCheckbutton")
        self.agreement.pack(fill="x", pady=(0, 8))
        self.label(outer, "You must check the box and select Agree to continue. Agree alone does not collect. Version: " + DISCLOSURE_VERSION)
        actions = ttk.Frame(outer)
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
        self.open_account_button = ttk.Button(outer, text="Open approved account browser", command=lambda: webbrowser.open(WEBSITE_ORIGIN + "/phone/account"))
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
        if name == "contacts":
            self.details_pane.grid_remove()
            self.contact_workspace.grid()
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
        self.state.approve(self.checked.get())
        self.refresh()

    def toggle_agreement(self) -> None:
        self.state.approve(False)
        self.refresh()

    def decline(self) -> None:
        self.checked.set(False)
        self.state.approve(False)
        self.refresh()

    def start(self, mode: str) -> None:
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
        self.refresh()

    def clear_password(self) -> None:
        # Disabled ttk entries ignore delete; enable only for this atomic clear.
        self.password.configure(state="normal")
        self.password.delete(0, tk.END)
        self.password.configure(state="disabled")

    def cooperative_failure(self, *, clear_password: bool = True) -> None:
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
        if self._rendering_contacts or not self.state.can_select:
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
        try:
            self.send(self.state.select_all_contacts())
        except ProtocolError:
            self.state.status = "Wait for the current page or review before checking all contacts."
        self.refresh()

    def cancel_select_all(self) -> None:
        self.state.cancel_select_all()
        self.refresh()

    def clear_all_contacts(self) -> None:
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
        try:
            self.send(self.state.review(sorted(self.state.selected_ids)))
        except ProtocolError:
            self.state.status = "Select contacts and wait for any pending response before reviewing again."
        self.refresh()

    def change_selection(self) -> None:
        if not self.state.can_change_selection:
            return
        if messagebox.askyesno("Change selected contacts?", "Close the current browser pairing and return to your selected contacts? The helper will refuse if account saving has started; your reviewed import stays open."):
            try:
                self.send(self.state.request_selection_change())
            except ProtocolError:
                self.state.status = "Finish the current account save before changing contacts."
        self.refresh()

    def pair(self) -> None:
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
            busy = not state.can_update or not self.status_owned or self.update_state == "installing"
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
        if not self.status_owned or not self.state.can_update or self.update_state in {"checking", "installing"}:
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
                if event["prepared"] is not self.prepared_update or not self.status_owned or not self.state.can_update:
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
                    self.update_state = "available" if self.state.can_update else "deferred"
                    label = "unsigned Windows TEST" if manifest.trust == "unsigned-test" else "Windows update"
                    self.update_message.set(f"Verified {manifest.version} {label} is ready. Open its installer when collection and unsaved work are finished.")
                else:
                    self.update_state = "current"
                    self.update_message.set(f"{VERSION} is current for the signed Windows x64 Test feed. This app is an unsigned TEST.")
        if self._startup_update_pending and self.status_owned and self.state.can_update:
            self.request_update_check()
        return False

    def install_update(self) -> None:
        prepared = self.prepared_update
        if not prepared or not self.status_owned or not self.state.can_update or self.update_state not in {"available", "deferred"}:
            return
        trust = "This is an unsigned Windows TEST. Windows security may warn or refuse it." if prepared.manifest.trust == "unsigned-test" else "Windows Installer and Windows security control this installation."
        if not messagebox.askyesno("Open verified Windows update?", f"Install {prepared.manifest.version}?\n\n{trust}\nThis app will close, then Windows Installer will open. Reopen AMPLIFai Phone after installation finishes."):
            return
        # Tk dialogs process events; collection may have started during the decision.
        if prepared is not self.prepared_update or not self.status_owned or not self.state.can_update or self.update_state not in {"available", "deferred"}:
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
                    if mode != "inspect":
                        self.start("inspect")
                else:
                    command = self.state.handle(event)
                    self.send(command)
                    if event.get("kind") in {"capture", "contacts"} and self.state._capture_received and not self.state.selecting_all:
                        self.render_contacts()
                    if event.get("kind") == "destination" and self.state.can_decide_destination:
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
                        self.state.status += (f" {selected} selected contacts · {calls_summary} · "
                                              f"{messages_summary}. Check source coverage below before account Save.")
                    if event.get("kind") == "handoff" and self.state.saved_acknowledged:
                        self.send(self.state.finish())
                    if event.get("kind") == "error" and self.state.phase == "error":
                        self.cooperative_failure()
            self.send(self.state.poll())
        except (OSError, RuntimeError, ValueError):
            self.cooperative_failure()
        self.refresh()
        self.window.after(1000, self.pump)


def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("Windows source candidate; no Windows runtime proof on this platform.")
    try:
        helper, logo = bundled_resources(Path(sys.executable), frozen=bool(getattr(sys, "frozen", False)))
    except ValueError:
        raise SystemExit("A matching frozen onedir helper and original logo are required. No source/PATH fallback is launched.") from None
    window = tk.Tk()
    PhoneWindow(window, helper, logo)
    window.mainloop()


if __name__ == "__main__":
    main()
