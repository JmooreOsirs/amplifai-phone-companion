"""Windows composition of the accepted Mac Brand and four-stage journey."""
from __future__ import annotations

import ctypes
import sys
import tkinter as tk
from fractions import Fraction
from pathlib import Path
from tkinter import font, ttk

from brand_controls import CollectionIllustration, JourneySteps, install_control_styles, surface
from brand_tokens import BACKGROUND, PANEL, BORDER, HEADING, BODY, MUTED, LIME, BLUE, RAISED, PAGE_SPACE, SECTION_SPACE


def pixels(widget, value):
    return round(value * float(widget.tk.call("tk", "scaling")) / (96 / 72))


def apply_style(window, font_path=None):
    space = lambda value: pixels(window, value)
    family = "Arial"
    if font_path is not None and Path(font_path).is_file() and sys.platform == "win32":
        register = ctypes.WinDLL("gdi32", use_last_error=True).AddFontResourceExW
        register.argtypes = (ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_void_p)
        register.restype = ctypes.c_int
        if register(str(font_path), 0x10, None) and "Open Sans" in font.families(window):
            family = "Open Sans"
    style = ttk.Style(window)
    style.theme_use("clam")
    # Tk points resolve to the Mac logical scale at 96dpi (12pt -> 16px).
    style.configure("TFrame", background=BACKGROUND)
    style.configure("Panel.TFrame", background=PANEL)
    style.configure("Well.TFrame", background=BACKGROUND)
    style.configure("TLabel", background=BACKGROUND, foreground=BODY, font=(family, 12))
    style.configure("Panel.TLabel", background=PANEL)
    style.configure("Well.TLabel", background=BACKGROUND, foreground=BODY)
    style.configure("Well.Muted.TLabel", background=BACKGROUND, foreground=MUTED)
    style.configure("Muted.TLabel", foreground=MUTED)
    style.configure("Heading.TLabel", foreground=HEADING, font=("Arial", 21, "bold"))
    style.configure("Panel.Heading.TLabel", background=PANEL, foreground=HEADING, font=("Arial", 21, "bold"))
    style.configure("Selected.TLabel", background=PANEL, foreground=LIME, font=("Arial", 18, "bold"))
    style.configure("Panel.Muted.TLabel", background=PANEL, foreground=MUTED)
    style.configure("Journey.TLabel", font=("Arial", 10, "bold"), foreground=MUTED)
    style.configure("Current.Journey.TLabel", foreground=HEADING)
    style.configure("Panel.Eyebrow.TLabel", background=PANEL, foreground=MUTED, font=(family, 10, "bold"))
    style.configure("Panel.Progress.TLabel", background=PANEL, foreground=HEADING, font=(family, 12, "bold"))
    style.configure("Raised.Metric.TLabel", background=RAISED, foreground=HEADING, font=("Arial", 21, "bold"))
    style.configure("Raised.Meta.TLabel", background=RAISED, foreground=MUTED, font=(family, 10))
    style.configure("TCheckbutton", background=PANEL, foreground=BODY, font=(family, 12))
    style.configure("Contact.TCheckbutton", background=RAISED, foreground=HEADING, font=(family, 12, "bold"))
    style.configure("Well.TCheckbutton", background=BACKGROUND, foreground=BODY, font=(family, 12))
    style.map("Well.TCheckbutton", background=[("disabled", BACKGROUND), ("active", BACKGROUND)], foreground=[("disabled", MUTED)])
    style.map("Contact.TCheckbutton", background=[("disabled", RAISED), ("active", RAISED)], foreground=[("disabled", MUTED)])
    style.map("TCheckbutton", background=[("disabled", PANEL), ("active", PANEL)], foreground=[("disabled", MUTED)])
    style.map("Native.Disclosure.TCheckbutton", background=[("disabled", PANEL)], foreground=[("disabled", MUTED)])
    style.configure("TEntry", background=BACKGROUND, fieldbackground=BACKGROUND, foreground=HEADING,
                    bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER, insertcolor=HEADING,
                    font=(family, 12), padding=space(8))
    style.map("TEntry", bordercolor=[("focus", BLUE)], lightcolor=[("focus", BLUE)], darkcolor=[("focus", BLUE)])
    install_control_styles(window, style, scale=float(window.tk.call("tk", "scaling")) / (96 / 72))
    return family


def label(parent, text=None, *, variable=None, heading=False, style=None):
    widget = ttk.Label(parent, text=text, textvariable=variable, justify="left", wraplength=780,
                       style=style or ("Panel.Heading.TLabel" if heading else "Panel.TLabel"), takefocus=False)
    widget.pack(fill="x", pady=(0, pixels(parent, 12)))
    return widget


def card(parent):
    panel = surface(parent)
    panel.grid(row=0, column=0, sticky="new")
    panel.grid_remove()
    return panel


def build(view, logo):
    view.layout_scale = float(view.window.tk.call("tk", "scaling")) / (96 / 72)
    space = view.space
    view.body_font = apply_style(view.window, Path(logo).parent / "fonts/OpenSans.ttf")
    shell = view.shell = ttk.Frame(view.window, padding=space(PAGE_SPACE))
    shell.pack(fill="both", expand=True)
    shell.columnconfigure(0, weight=1)
    shell.rowconfigure(2, weight=1)
    header = ttk.Frame(shell)
    header.grid(row=0, column=0, sticky="ew", pady=(0, space(12)))
    view.logo = tk.PhotoImage(file=str(logo))
    ratio = Fraction(space(180), view.logo.width()).limit_denominator(16)
    view.logo = view.logo.zoom(ratio.numerator).subsample(ratio.denominator)
    ttk.Label(header, image=view.logo).pack(side="left")
    view.recovery_toggle = ttk.Button(header, text="Details & recovery", style="Quiet.TButton",
        command=lambda: view.show_workspace("setup" if view._visible_workspace == "details" else "details"))
    view.recovery_toggle.pack(side="right")
    view.navigation = view.journey = JourneySteps(shell, scale=view.layout_scale)
    view.navigation.grid(row=1, column=0, sticky="ew", pady=(0, space(SECTION_SPACE)))
    view.journey_steps = view.journey.labels
    host = ttk.Frame(shell)
    host.grid(row=2, column=0, sticky="nsew")
    host.columnconfigure(0, weight=1)
    host.rowconfigure(0, weight=1)
    view._visible_workspace, view._workspace_capture = "setup", False
    view._journey_stage = None
    view.details_pane = ttk.Frame(host)
    view.details_pane.grid(row=0, column=0, sticky="nsew")
    view.details_canvas = canvas = tk.Canvas(view.details_pane, background=BACKGROUND, highlightthickness=0, takefocus=False)
    scroll = ttk.Scrollbar(view.details_pane, command=canvas.yview)
    canvas.configure(yscrollcommand=scroll.set)
    scroll.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)
    view.details_content = outer = ttk.Frame(canvas)
    outer.columnconfigure(0, weight=1)
    content = canvas.create_window((0, 0), window=outer, anchor="nw")
    outer.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
    def resize(event):
        maximum = space(1180 if view._journey_stage == "collection" else 880)
        width = min(event.width, maximum)
        canvas.itemconfigure(content, width=width)
        canvas.coords(content, max(0, (event.width - width) // 2), 0)
        for widget in descendants(outer):
            if isinstance(widget, ttk.Label) and not getattr(widget, "_brand_fixed_text", False):
                widget.configure(wraplength=max(space(160), width - space(56)))
        ttk.Style(view.window).configure("Native.Disclosure.TCheckbutton", wraplength=max(space(160), width - space(76)))
    canvas.bind("<Configure>", resize)
    view._resize_stage = lambda: resize(type("Size", (), {"width": canvas.winfo_width()})())
    view.stage_panels = {}
    home = view.stage_panels["account"] = card(outer)
    label(home.content, "Start from your AMPLIFai account", heading=True)
    label(home.content, "Open Phone in your signed-in account and choose Connect phone. Your account and course will appear here for approval.")
    view.account_start_button = ttk.Button(home.content, text="Open my account", style="Primary.TButton", command=view.open_account)
    view.account_start_button.pack(anchor="w", pady=(space(12), space(12)))
    label(home.content, "Keep the account tab open. This app will guide you through approval, collection and contact selection.")
    label(home.content, variable=view.status)
    approval = view.stage_panels["approval"] = card(outer)
    approval.content.columnconfigure(0, weight=1)
    view._layout_native_setup(approval.content)
    view.native_pane.grid()
    collection = view.stage_panels["collection"] = card(outer)
    build_collection(view, collection.content)
    save = view.stage_panels["save"] = card(outer)
    view.save_heading = label(save.content, "Saving your selected contacts", heading=True)
    view.save_selection = label(save.content)
    label(save.content, variable=view.workspace_coverage)
    label(save.content, variable=view.workspace_status)
    view.save_status_label = label(save.content, variable=view.native_status)
    view.save_account_button = ttk.Button(save.content, text="Reopen account", style="Primary.TButton", command=view.reopen_account)
    view.save_account_button.pack(anchor="w", pady=(space(12), space(12)))
    view.save_retry_button = ttk.Button(save.content, text="Retry confirmation", command=lambda: view.native_action(view.native.retry))
    view.save_retry_button.pack(anchor="w")
    view.save_detail = label(save.content)
    recovery = view.stage_panels["details"] = card(outer)
    build_recovery(view, recovery.content)
    view.contact_workspace = surface(host, kind="panel", fill_content=True, padding=20)
    view.contact_workspace.grid(row=0, column=0, sticky="nsew")
    view.contact_workspace.grid_remove()
    view._layout_contacts()
    for widget in descendants(outer):
        if isinstance(widget, (ttk.Button, ttk.Menubutton, ttk.Checkbutton, ttk.Radiobutton,
                               ttk.Entry, ttk.Treeview, tk.Text, tk.Listbox)):
            widget.bind("<FocusIn>", lambda event: view.reveal_detail_focus(event.widget), add="+")
        elif isinstance(widget, (tk.Frame, ttk.Frame, ttk.Label, tk.Canvas)):
            widget.configure(takefocus=False)
        widget.bind("<MouseWheel>", lambda event: canvas.yview_scroll(-int(event.delta / 120), "units"), add="+")
    view.refresh()


def descendants(parent):
    for child in parent.winfo_children():
        yield child
        yield from descendants(child)


def build_collection(view, parent):
    """CollectionStatusPanel's hierarchy, using only measured Session values."""
    space = view.space
    view.collection_eyebrow = label(parent, style="Panel.Eyebrow.TLabel")
    view.collection_eyebrow.pack_configure(pady=(0, space(8)))
    view.collection_heading = label(parent, heading=True)
    view.collection_heading.pack_configure(pady=(0, space(8)))
    view.collection_detail = label(parent)
    view.stop_button = ttk.Button(parent, text="Stop on this PC", command=view.stop)
    view.stop_button.pack(anchor="w", pady=(0, space(12)))
    view.password_row = ttk.Frame(parent, style="Panel.TFrame")
    label(view.password_row, "Enter your encrypted backup password. It is sent privately to the helper and is never stored.")
    view.password = ttk.Entry(view.password_row, show="•", width=30)
    view.password.pack(fill="x", pady=(0, space(12)))
    view.password_button = ttk.Button(view.password_row, text="Unlock local backup", style="Primary.TButton", command=view.send_password)
    view.password_button.pack(anchor="w", pady=(0, space(12)))

    view.collection_illustration_well = well = surface(parent, kind="well")
    well.pack(fill="x", pady=(0, space(12)))
    view.collection_illustration = CollectionIllustration(well.content, scale=view.layout_scale)
    view.collection_illustration.pack(pady=(space(4), space(12)))
    view.collection_illustration_caption = ttk.Label(well.content, style="Well.TLabel", anchor="center", takefocus=False)
    view.collection_illustration_caption.pack(fill="x", pady=(0, space(6)))

    view.collection_stages = stages = ttk.Frame(parent, style="Panel.TFrame", takefocus=False)
    stages.pack(fill="x", pady=(0, space(12)))
    view.collection_stage_labels = []
    for text in ("Connect iPhone", "Receive backup", "Read locally", "Clean up"):
        item = ttk.Label(stages, text=text, style="Stage.TLabel", takefocus=False)
        item._brand_fixed_text = True
        view.collection_stage_labels.append(item)
    stages._columns = None

    def arrange_stages(event):
        columns = 4 if event.width >= space(540) else 2
        if columns == stages._columns:
            return
        stages._columns = columns
        for index in range(4):
            stages.columnconfigure(index, minsize=space(120) if index < columns else 0)
        for index, item in enumerate(view.collection_stage_labels):
            item.grid(row=index // columns, column=index % columns, sticky="ew",
                      padx=(0, space(6) if index % columns < columns - 1 else 0),
                      pady=(0, space(6) if index // columns < 4 // columns - 1 else 0))

    stages.bind("<Configure>", arrange_stages)
    arrange_stages(type("Size", (), {"width": space(540)})())

    view.collection_metric_well = surface(parent, kind="well")
    view.collection_metric_card = metric = surface(view.collection_metric_well.content, kind="raised", width=space(248))
    metric.pack(anchor="w")
    view.collection_metric_value = ttk.Label(metric.content, style="Raised.Metric.TLabel", takefocus=False)
    view.collection_metric_value.pack(anchor="w", pady=(0, space(6)))
    view.collection_metric_label = ttk.Label(metric.content, style="Raised.Meta.TLabel", takefocus=False)
    view.collection_metric_label.pack(anchor="w")
    view.collection_metric_value._brand_fixed_text = view.collection_metric_label._brand_fixed_text = True
    view.activity = ttk.Frame(parent, style="Panel.TFrame", takefocus=False)
    view.activity.pack(fill="x", pady=(0, space(12)))
    view.progress = ttk.Progressbar(view.activity, maximum=100)
    view.progress_label = label(view.activity, variable=view.progress_caption, style="Panel.Progress.TLabel")
    view.progress_label.pack_configure(pady=0)
    view.collection_details_button = ttk.Button(parent, text="Transfer details", style="Quiet.TButton",
                                               command=lambda: view.show_workspace("details"))
    view.collection_details_button.pack(anchor="w", pady=(0, space(12)))
    view.collection_next = label(parent, style="Panel.Muted.TLabel")
    view.collection_next.pack_configure(pady=0)


def byte_description(value):
    if value < 1024:
        return f"{value:,} B"
    amount = float(value)
    for unit in ("KiB", "MiB", "GiB", "TiB", "PiB", "EiB"):
        amount /= 1024
        if amount < 1024 or unit == "EiB":
            return f"{amount:.1f} {unit}"


def refresh_collection(view):
    state, space = view.state, view.space
    presentation = (state.phase, state.transfer_stage, state.transfer_bytes)
    if getattr(view, "_collection_presentation", None) == presentation:
        return
    view._collection_presentation = presentation
    processing = state.transfer_stage == "processing" or state.phase in {"processing", "password_required"}
    active = 2 if processing else 1 if state.transfer_stage == "backup" else 0
    view.collection_eyebrow.configure(text=f"ON THIS PC · STAGE {active + 1} OF 4")
    view.collection_heading.configure(text=("Unlock your local backup" if state.phase == "password_required" else
        "Reading available sources" if processing else "Receiving the local backup" if active == 1 else "Connect your iPhone"))
    view.collection_detail.configure(text=("Enter the password used to encrypt this iPhone backup." if state.phase == "password_required" else
        "Reading available contacts and activity, then cleaning up temporary backup files." if processing else
        "Keep the cable connected. The temporary backup stays on this PC." if active == 1 else
        "Connect by USB, unlock your iPhone and tap Trust if asked."))
    view.collection_illustration.set_processing(processing)
    view.collection_illustration_caption.configure(
        text="Backup received · waiting for your password" if state.phase == "password_required" else
             "Backup received · reading on this PC" if processing else "iPhone → this PC",
        foreground=LIME if processing else BODY)
    for index, item in enumerate(view.collection_stage_labels):
        item.configure(style="Active.Stage.TLabel" if index == active else "Completed.Stage.TLabel" if index < active else "Stage.TLabel")
    if state.transfer_stage in {"backup", "processing"}:
        view.collection_metric_value.configure(text=byte_description(state.transfer_bytes))
        view.collection_metric_label.configure(text="PROCESSED" if state.transfer_stage == "processing" else "RECEIVED")
        view.collection_metric_well.pack(fill="x", pady=(0, space(12)), before=view.activity)
    else:
        view.collection_metric_well.pack_forget()
    view.collection_next.configure(text=(
        "Next: unlock the backup to read available contacts and activity locally." if state.phase == "password_required" else
        "Next: choose contacts, then review and save in your approved account." if processing else
        "Next: read available sources locally, then choose which contacts to share." if active == 1 else
        "Next: receive the iPhone backup on this PC. Keep the phone unlocked for Trust prompts."))


def build_recovery(view, parent):
    space = view.space
    view.recovery_heading = label(parent, "Details & recovery", heading=True)
    view.recovery_detail = label(parent)
    view.status_details_label = label(parent, variable=view.status)
    label(parent, variable=view.activity_measurement)
    label(parent, variable=view.transfer_status)
    view.recovery_retry_button = ttk.Button(parent, text="Retry confirmation", command=lambda: view.native_action(view.native.retry))
    label(parent, variable=view.coverage_status)
    view.diagnostic_details_button = ttk.Button(parent, text="Source diagnostic details", command=view.show_source_diagnostics)
    view.diagnostic_details_button.pack(anchor="w", pady=(0, space(12)))
    view.account_details_label = label(parent, variable=view.destination_status)
    view.code_label = label(parent, variable=view.pair_code)
    view.copy_code_button = ttk.Button(parent, text="Copy temporary recovery code", command=view.copy_pair_code)
    view.copy_code_button.pack(anchor="w", pady=(0, space(12)))
    view.open_account_button = ttk.Button(parent, text="Open account", command=view.open_account)
    view.open_account_button.pack(anchor="w", pady=(0, space(20)))
    view.inspect = ttk.Button(parent, text="Check temporary data", command=lambda: view.start("inspect"))
    view.inspect.pack(anchor="w", pady=(0, space(12)))
    view.clear = ttk.Button(parent, text="Remove marked abandoned data", command=view.clear_residue)
    view.clear.pack(anchor="w", pady=(0, space(12)))
    view.force_button = ttk.Button(parent, text="Force stop", command=view.force)
    view.force_button.pack(anchor="w", pady=(0, space(20)))
    label(parent, variable=view.update_message)
    view.check_update_button = ttk.Button(parent, text="Check for updates", command=view.request_update_check)
    view.check_update_button.pack(anchor="w", pady=(0, space(12)))
    view.install_update_button = ttk.Button(parent, text="Open verified update installer", command=view.install_update)
    view.install_update_button.pack(anchor="w")


def refresh(view, current):
    space = view.space
    view.journey.set_current(4 if current.stage == "complete" else current.step)
    stage = current.stage
    if view._journey_stage != stage:
        view._visible_workspace = "setup"
        view._journey_stage = stage
        view.details_canvas.yview_moveto(0)
    details = view._visible_workspace == "details" or stage == "attention"
    selected = "details" if details else "save" if stage == "complete" else "approval" if stage == "prepare" else stage
    for name, panel in view.stage_panels.items():
        panel.grid() if name == selected else panel.grid_remove()
    if selected == "contacts":
        view.details_pane.grid_remove()
        view.contact_workspace.grid()
    else:
        view.contact_workspace.grid_remove()
        view.details_pane.grid()
    view.recovery_toggle.configure(text="Back to current step" if details and stage != "attention" else "Details & recovery")
    if stage == "collection":
        refresh_collection(view)
    if view.state.phase == "password_required":
        view.password_row.pack(fill="x", pady=(0, space(12)), before=view.stop_button)
    else:
        view.password_row.pack_forget()
    view.code_label.pack_forget()
    view.copy_code_button.pack_forget()
    if view.state.pair_code and not view.native_active:
        view.code_label.pack(fill="x", before=view.open_account_button)
        view.copy_code_button.pack(anchor="w", pady=(0, space(12)), before=view.open_account_button)
    view.save_heading.configure(text=current.heading)
    view.save_detail.configure(text=current.detail)
    view.recovery_heading.configure(text=current.heading if stage == "attention" else "Details & recovery")
    view.recovery_detail.configure(text=current.detail if stage == "attention" else "Local status, account recovery and temporary data controls.")
    view.save_account_button.configure(text="Open report" if stage == "complete" else "Reopen account")
    retry = view.native_active and view.native.pending is None and view.native_worker.idle and (view.native.phase.endswith("_uncertain") or view.native.authority == "paused" and view.native.current)
    view.save_retry_button.pack(anchor="w") if retry else view.save_retry_button.pack_forget()
    view.recovery_retry_button.pack(anchor="w", pady=(0, space(12)), before=view.diagnostic_details_button) if retry else view.recovery_retry_button.pack_forget()
    view.account_start_button.configure(state="normal" if view.state.can_update and view.status_owned else "disabled")
    view._resize_stage()
