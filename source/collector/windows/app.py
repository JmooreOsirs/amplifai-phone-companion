"""Windows source candidate. Launch only with the matching bundled helper."""
from __future__ import annotations

import math
import queue
import sys
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import messagebox, ttk

from helper_process import HelperProcess, bundled_resources
from session import DISCLOSURE_VERSION, WEBSITE_ORIGIN, ProtocolError, Session

# Exact values from collector/macos/Sources/Support/Brand.swift.
BACKGROUND, PANEL, BORDER = "#0f1525", "#141d30", "#263146"
HEADING, BODY, MUTED = "#edeff3", "#c8cfd9", "#a1a9b5"
LIME, BLUE = "#a6e63d", "#5c87df"
GUARANTEE = "Data & Privacy Guarantee: What We Collect, How It's Protected, and Why"
WINDOW_TITLE = "AMPLIFai Phone · Windows source candidate"


def window_title(state: Session) -> str:
    if not state.running and state.inspected and not state.needs_inspection and not state.residue:
        return WINDOW_TITLE + " · ready"
    if not state.running and state.inspected and state.residue:
        return WINDOW_TITLE + " · temporary data remains"
    if not state.running and state.phase == "error":
        return WINDOW_TITLE + " · inspection failed"
    return WINDOW_TITLE


class PhoneWindow:
    def __init__(self, window: tk.Tk, helper: Path, logo: Path) -> None:
        self.window, self.state = window, Session()
        self.events: queue.Queue = queue.Queue(maxsize=8)
        self.helper = HelperProcess([str(helper)], self.events.put)
        self.checked = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value=self.state.status)
        self.pair_code = tk.StringVar(value="No browser pairing has been created.")
        self.contact_query = tk.StringVar(value="")
        self.contact_page = tk.StringVar(value="No contacts loaded.")
        self.destination_status = tk.StringVar(value="Connect from your signed-in account after reviewing contacts.")
        self._rendering_contacts = False
        self.window.title(WINDOW_TITLE)
        self.window.geometry("920x800")
        self.window.minsize(680, 680)
        self.window.configure(background=BACKGROUND)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self._style()
        self._layout(logo)
        self.window.after(100, self.pump)
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
        canvas = tk.Canvas(self.window, background=BACKGROUND, highlightthickness=0)
        scroll = ttk.Scrollbar(self.window, command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        outer = ttk.Frame(canvas, padding=24)
        content = canvas.create_window((0, 0), window=outer, anchor="nw")
        outer.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        def resize(event):
            canvas.itemconfigure(content, width=event.width)
            pending = [outer]
            while pending:
                widget = pending.pop()
                pending.extend(widget.winfo_children())
                if isinstance(widget, ttk.Label):
                    widget.configure(wraplength=max(240, event.width - 48))
        canvas.bind("<Configure>", resize)
        header = ttk.Frame(outer)
        header.pack(fill="x")
        self.logo = tk.PhotoImage(file=str(logo))
        self.logo = self.logo.subsample(max(1, math.ceil(self.logo.width() / 210)))
        ttk.Label(header, image=self.logo).pack(anchor="w", pady=(0, 12))
        self.label(outer, "Connect. Review. Decide.", heading=True)
        self.label(outer, GUARANTEE)
        self.label(outer, "Selected contact names and numbers, call counterpart/time/direction/duration, and message counterpart/time/direction/channel provide relationship context. Message text, attachments, passwords and backups are never handed to the website.")
        self.label(outer, "An iPhone backup can contain private content locally. Cleanup can leave files after interruption. Trust and device permissions are your decision. Agreement permits collection, not marketing or account saving.")
        self.agreement = ttk.Checkbutton(outer, text="I agree to this selected phone metadata collection disclosure.", variable=self.checked, command=self.toggle_agreement)
        self.agreement.pack(anchor="w", pady=(0, 8))
        self.label(outer, "You must check the box and select Agree to continue. Agree alone does not collect. Version: " + DISCLOSURE_VERSION)
        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=(0, 16))
        self.approve = ttk.Button(actions, text="Agree", command=self.agree, style="Primary.TButton")
        self.approve.pack(side="left", padx=(0, 8))
        self.connect = ttk.Button(actions, text="Connect iPhone", command=lambda: self.start("connect"), style="Primary.TButton")
        self.connect.pack(side="left", padx=(0, 8))
        self.decline_button = ttk.Button(actions, text="Not now", command=self.decline)
        self.decline_button.pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="Full privacy guarantee", command=lambda: webbrowser.open(WEBSITE_ORIGIN + "/experience/data-privacy-guarantee")).pack(side="left")
        ttk.Label(outer, textvariable=self.status, wraplength=820, justify="left").pack(fill="x", pady=(0, 8))
        self.progress = ttk.Progressbar(outer, maximum=100)
        self.progress.pack(fill="x", pady=(0, 12))
        password_row = ttk.Frame(outer)
        password_row.pack(fill="x", pady=(0, 12))
        self.password = ttk.Entry(password_row, show="•", width=30)
        self.password.pack(side="left", padx=(0, 8))
        self.password_button = ttk.Button(password_row, text="Send backup password privately", command=self.send_password)
        self.password_button.pack(side="left")
        self.label(outer, "Select contacts below. Nothing is selected by default; unavailable history is not zero activity.")
        paging = ttk.Frame(outer)
        paging.pack(fill="x", pady=(0, 8))
        self.search_entry = ttk.Entry(paging, textvariable=self.contact_query, width=28)
        self.search_entry.pack(side="left", padx=(0, 8))
        self.search_button = ttk.Button(paging, text="Search names", command=self.search_contacts)
        self.search_button.pack(side="left", padx=(0, 8))
        self.previous_button = ttk.Button(paging, text="Previous", command=self.previous_contacts)
        self.previous_button.pack(side="left", padx=(0, 8))
        self.next_button = ttk.Button(paging, text="Next", command=self.next_contacts)
        self.next_button.pack(side="left")
        selection_actions = ttk.Frame(outer)
        selection_actions.pack(fill="x", pady=(0, 8))
        self.select_all_button = ttk.Button(selection_actions, text="Select all contacts", command=self.select_all_contacts)
        self.select_all_button.pack(side="left", padx=(0, 8))
        self.cancel_select_all_button = ttk.Button(selection_actions, text="Stop check", command=self.cancel_select_all)
        self.cancel_select_all_button.pack(side="left", padx=(0, 8))
        self.clear_all_button = ttk.Button(selection_actions, text="Clear all", command=self.clear_all_contacts)
        self.clear_all_button.pack(side="left")
        ttk.Label(outer, textvariable=self.contact_page).pack(anchor="w", pady=(0, 8))
        contact_frame = ttk.Frame(outer)
        contact_frame.pack(fill="both", expand=True)
        self.contacts = tk.Listbox(contact_frame, selectmode=tk.EXTENDED, exportselection=False, height=6, background=PANEL, foreground=HEADING, selectbackground=BLUE, selectforeground=BACKGROUND, highlightbackground=BORDER, highlightcolor=LIME, font=("Arial", 12), activestyle="underline")
        scrollbar = ttk.Scrollbar(contact_frame, command=self.contacts.yview)
        self.contacts.configure(yscrollcommand=scrollbar.set)
        self.contacts.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.contacts.bind("<<ListboxSelect>>", self.selection_changed)
        self.review_button = ttk.Button(outer, text="Review selected metadata", command=self.review)
        self.review_button.pack(anchor="w", pady=(8, 8))
        self.label(outer, "After reviewing the exact selection, connect from your signed-in website account. Confirm the account and course here before preview transfer. Connect does not save; each source needs separate Save approval in the account.")
        ttk.Label(outer, textvariable=self.destination_status, wraplength=820, justify="left").pack(fill="x", pady=(0, 8))
        ttk.Label(outer, textvariable=self.pair_code, font=("Arial", 18, "bold"), foreground=LIME).pack(anchor="w")
        self.pair_button = ttk.Button(outer, text="Connect signed-in account", command=self.pair)
        self.pair_button.pack(anchor="w", pady=(8, 8))
        self.copy_code_button = ttk.Button(outer, text="Copy temporary code for manual recovery", command=self.copy_pair_code)
        self.copy_code_button.pack(anchor="w", pady=(0, 8))
        ttk.Button(outer, text="Open approved account browser", command=lambda: webbrowser.open(WEBSITE_ORIGIN + "/phone/account")).pack(anchor="w", pady=(0, 8))
        recovery = ttk.Frame(outer)
        recovery.pack(fill="x")
        self.inspect = ttk.Button(recovery, text="Check temporary data", command=lambda: self.start("inspect"))
        self.inspect.pack(side="left", padx=(0, 8))
        self.clear = ttk.Button(recovery, text="Remove marked abandoned data", command=self.clear_residue)
        self.clear.pack(side="left", padx=(0, 8))
        self.stop_button = ttk.Button(recovery, text="Request stop", command=self.stop)
        self.stop_button.pack(side="left", padx=(0, 8))
        self.force_button = ttk.Button(recovery, text="Force stop", command=self.force)
        self.force_button.pack(side="left")
        self.refresh()

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
        try:
            self.state.begin(mode)
        except ProtocolError:
            self.state.status = "Finish the current operation and inspect temporary data before starting another."
            self.refresh()
            return
        self.checked.set(False)
        if mode == "connect":
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

    def pair(self) -> None:
        replacing = " This replaces the current browser pairing." if self.state.handoff_id else ""
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
                self.window.destroy()
        else:
            self.window.destroy()

    def refresh(self) -> None:
        state = self.state
        self.window.title(window_title(state))
        self.status.set(state.status)
        self.approve.configure(state="normal" if self.checked.get() and state.inspected and not state.residue and not state.running else "disabled")
        self.connect.configure(state="normal" if state.can_connect else "disabled")
        self.agreement.configure(state="disabled" if state.running else "normal")
        self.decline_button.configure(state="disabled" if state.running else "normal")
        self.contacts.configure(state="normal" if state.can_select and not state.selecting_all else "disabled")
        self.review_button.configure(state="normal" if state.can_review and state.selected_ids else "disabled")
        self.search_entry.configure(state="normal" if state.can_select and not state.selecting_all and state.pending_contact is None else "disabled")
        self.search_button.configure(state="normal" if state.can_select and not state.selecting_all and state.pending_contact is None else "disabled")
        self.previous_button.configure(state="normal" if state.can_select and not state.selecting_all and state.pending_contact is None and state.previous_contact_cursors else "disabled")
        self.next_button.configure(state="normal" if state.can_select and not state.selecting_all and state.pending_contact is None and state.next_contact_cursor is not None else "disabled")
        self.select_all_button.configure(state="normal" if state.can_select_all else "disabled")
        self.cancel_select_all_button.configure(state="normal" if state.selecting_all and not state._select_all_cancel else "disabled")
        self.clear_all_button.configure(state="normal" if state.can_select and not state.selecting_all and state.pending_contact is None and state.selected_ids else "disabled")
        if state._capture_received:
            self.contact_page.set((f"Checking {state.select_all_visited} of {state.total_contacts} contacts. "
                                   f"Selection remains {len(state.selected_ids)} until complete.") if state.selecting_all else
                f"{len(state.contacts)} on this page · {state.total_contacts} total contacts · "
                f"{len(state.selected_ids)} selected across pages")
        self.pair_button.configure(state="normal" if state.can_pair else "disabled")
        self.password_button.configure(state="normal" if state.phase == "password_required" else "disabled")
        self.password.configure(state="normal" if state.phase == "password_required" else "disabled")
        self.inspect.configure(state="disabled" if state.running else "normal")
        self.clear.configure(state="normal" if state.residue and not state.running else "disabled")
        self.stop_button.configure(state="normal" if state.running and state.phase != "cancelling" else "disabled")
        self.force_button.configure(state="normal" if state.running and state.phase == "cancelling" else "disabled")
        self.progress.configure(value=state.progress or 0)
        self.pair_code.set("Temporary recovery code: " + state.pair_code if state.pair_code else "No browser pairing has been created.")
        self.copy_code_button.configure(state="normal" if state.pair_code else "disabled")
        self.destination_status.set(("Server-confirmed destination: " + state.destination_email + " · " + state.destination_course)
                                    if state.destination_intent_id else "Connect from your signed-in account after reviewing contacts.")

    def pump(self) -> None:
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
                        approved = messagebox.askyesno(
                            "Confirm signed-in account destination",
                            f"Account: {self.state.destination_email}\nCourse: {self.state.destination_course}\n\n"
                            "These details came from AMPLIFai's authenticated server. Connect sends the reviewed preview only. Each source still needs separate Save approval in the account. Connect to this account?",
                        )
                        self.send(self.state.decide_destination(approved))
                    if event.get("kind") == "review" and self.state.reviewed:
                        selected, calls, messages = self.state.review_counts
                        self.state.status += f" {selected} contacts · {calls} matching calls · {messages} matching messages. Unavailable: {', '.join(self.state.missing) or 'none reported; coverage remains partial'}."
                    if event.get("kind") == "handoff" and self.state.saved_acknowledged:
                        self.send(self.state.finish())
                    if event.get("kind") == "error":
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
