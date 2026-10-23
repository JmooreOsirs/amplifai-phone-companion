"""Mac contact workspace composition using accessible Windows native controls."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from brand_controls import surface
from brand_tokens import PANEL
from brand_widgets import ContactCards


def build(view):
    space = view.space
    workspace = view.contact_workspace.content
    workspace.columnconfigure(0, weight=1)
    workspace.rowconfigure(2, weight=1)
    view.organizing = False
    view.include_all = tk.BooleanVar(master=view.window, value=False)

    header = ttk.Frame(workspace, style="Panel.TFrame")
    header.grid(row=0, column=0, sticky="ew", pady=(0, space(12)))
    header.columnconfigure(0, weight=1)
    view.contact_heading = ttk.Label(header, text="Choose contacts", style="Panel.Heading.TLabel")
    view.contact_heading.grid(row=0, column=0, sticky="w")
    count = ttk.Frame(header, style="Panel.TFrame")
    count.grid(row=0, column=1, sticky="e")
    view.contact_selected = ttk.Label(count, style="Selected.TLabel")
    view.contact_selected.pack(side="left", padx=(0, space(8)))
    view.contact_total = ttk.Label(count, style="Panel.Muted.TLabel")
    view.contact_total.pack(side="left")
    view.workspace_context = ttk.Label(header, justify="left", style="Panel.TLabel")
    view.workspace_context.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(space(12), 0))
    def resize_header(_event):
        if header.winfo_width() <= 1:
            return
        stacked = header.winfo_width() < view.contact_heading.winfo_reqwidth() + count.winfo_reqwidth() + space(20)
        position = (1, 0) if stacked else (0, 1)
        info = count.grid_info()
        if (info["row"], info["column"]) != position:
            count.grid(row=position[0], column=position[1], sticky="w" if stacked else "e",
                       pady=(space(8) if stacked else 0, 0))
        view.workspace_context.configure(wraplength=header.winfo_width())
    header.bind("<Configure>", resize_header)
    count.bind("<Configure>", resize_header)

    well = view.contact_controls = surface(workspace, kind="well", padding=12, background=PANEL)
    well.grid(row=1, column=0, sticky="ew", pady=(0, space(12)))
    controls = well.content
    controls.columnconfigure(1, weight=1)
    view.select_all_button = ttk.Checkbutton(controls, variable=view.include_all,
        text="Include all contacts", style="Well.TCheckbutton", command=view.toggle_all_contacts)
    view.select_all_button.grid(row=0, column=0, sticky="w", padx=(0, space(12)))
    filters = ttk.Frame(controls, style="Well.TFrame")
    filters.grid(row=0, column=1, sticky="w")
    view.group_buttons = {}
    for group in ("All", "Main", "Review"):
        button = ttk.Button(filters, text=group, style="Group.TButton", command=lambda value=group: view.show_contact_group(value))
        button.pack(side="left", padx=(0, space(8)))
        view.group_buttons[group] = button
    view.organize_menu = tk.Menu(controls, tearoff=False, postcommand=view.refresh_organize_menu)
    view.organize_button = ttk.Menubutton(controls, text="Organize", style="Quiet.TMenubutton", menu=view.organize_menu)
    view.organize_button.grid(row=0, column=2, sticky="e")

    search = ttk.Frame(controls, style="Well.TFrame")
    search.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(space(12), 0))
    search.columnconfigure(0, weight=1)
    view.search_entry = ttk.Entry(search, textvariable=view.contact_query, width=20)
    view.search_entry.grid(row=0, column=0, sticky="ew", padx=(0, space(8)))
    view.search_entry.bind("<Return>", lambda _event: view.search_contacts())
    view.search_button = ttk.Button(search, text="Search names", command=view.search_contacts, style="Quiet.TButton")
    view.search_button.grid(row=0, column=1, sticky="e")
    scope = ttk.Frame(search, style="Well.TFrame")
    view.contact_scope = scope
    scope.grid(row=0, column=2, sticky="e", padx=(space(12), 0))
    view.scope_select_button = ttk.Button(scope, text="Select matches", style="Quiet.TButton", command=lambda: view.select_contact_scope(True))
    view.scope_clear_button = ttk.Button(scope, text="Deselect matches", style="Quiet.TButton", command=lambda: view.select_contact_scope(False))
    view.scope_menu = tk.Menu(scope, tearoff=False)
    view.scope_menu.add_command(label="Select all matches", command=lambda: view.select_contact_scope(True))
    view.scope_menu.add_command(label="Deselect all matches", command=lambda: view.select_contact_scope(False))
    view.scope_menu_button = ttk.Menubutton(scope, text="Selection actions", style="Quiet.TMenubutton", menu=view.scope_menu)
    view.scope_count_label = ttk.Label(scope, style="Well.Muted.TLabel")
    view.scope_count_label.pack(side="right")
    recovery = ttk.Frame(controls, style="Well.TFrame")
    recovery.grid(row=3, column=0, columnspan=3, sticky="w")
    view.cancel_select_all_button = ttk.Button(recovery, text="Stop check", command=view.cancel_select_all, style="Quiet.TButton")
    view.clear_all_button = ttk.Button(recovery, text="Clear all", command=view.clear_all_contacts, style="Quiet.TButton")

    def resize_controls(_event=None):
        width = controls.winfo_width()
        if width <= 1:
            return
        narrow = width < space(900)
        position = (1, 0) if narrow else (0, 1)
        info = filters.grid_info()
        if (info["row"], info["column"]) != position:
            filters.grid(row=position[0], column=position[1], columnspan=2 if narrow else 1,
                         sticky="w", pady=(space(8) if narrow else 0, 0))
            search.grid(row=2 if narrow else 1)
        if view.state.contact_workspace is not None and view.state.contact_workspace.is_scoped:
            scope.grid(row=1 if narrow else 0, column=0 if narrow else 2,
                       columnspan=2 if narrow else 1, sticky="w" if narrow else "e",
                       padx=(0 if narrow else space(12), 0), pady=(space(8) if narrow else 0, 0))
        else:
            scope.grid_remove()
        if getattr(view, "_contact_controls_narrow", None) != narrow:
            view._contact_controls_narrow = narrow
            for item in (view.scope_select_button, view.scope_clear_button, view.scope_menu_button):
                item.pack_forget()
            if narrow:
                view.scope_menu_button.pack(side="left", padx=(0, space(12)))
            else:
                view.scope_select_button.pack(side="left", padx=(0, space(8)))
                view.scope_clear_button.pack(side="left", padx=(0, space(8)))
    controls.bind("<Configure>", resize_controls)
    view.resize_contact_controls = resize_controls

    contacts_well = surface(workspace, kind="well", padding=12, background=PANEL, fill_content=True)
    contacts_well.grid(row=2, column=0, sticky="nsew")
    contacts_well.content.columnconfigure(0, weight=1)
    contacts_well.content.rowconfigure(0, weight=1)
    view.contacts = ContactCards(contacts_well.content, font=(view.body_font, 12))
    view.contacts.grid(row=0, column=0, sticky="nsew")
    scrollbar = ttk.Scrollbar(contacts_well.content, command=view.contacts.yview)
    scrollbar.grid(row=0, column=1, sticky="ns")
    view.contacts.configure(yscrollcommand=scrollbar.set)
    view.contacts.bind("<<ListboxSelect>>", view.selection_changed)
    view.contact_empty_label = ttk.Label(contacts_well.content, justify="center", style="Well.Muted.TLabel")
    view.contact_empty_label.grid(row=0, column=0, sticky="nsew", padx=space(12))
    view.contact_empty_label.bind("<Configure>", lambda event: view.contact_empty_label.configure(wraplength=max(1, event.width - space(24))))
    view.contact_empty_label.grid_remove()
    paging = ttk.Frame(contacts_well.content, style="Well.TFrame")
    paging.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(space(8), 0))
    view.page_label = ttk.Label(paging, textvariable=view.contact_page, justify="left", style="Well.Muted.TLabel")
    view.page_label.pack(side="left", fill="x", expand=True)
    view.page_label.bind("<Configure>", lambda event: view.page_label.configure(wraplength=event.width))
    view.previous_button = ttk.Button(paging, text="Previous", command=view.previous_contacts, style="Quiet.TButton")
    view.previous_button.pack(side="left", padx=(space(8), 0))
    view.next_button = ttk.Button(paging, text="Next page", command=view.next_contacts, style="Quiet.TButton")
    view.next_button.pack(side="left", padx=(space(8), 0))

    view.contact_actions = ttk.Frame(workspace, style="Panel.TFrame")
    view.contact_actions.grid(row=3, column=0, sticky="ew", pady=(space(12), 0))
    view.contact_actions.columnconfigure(0, weight=1)
    view.workspace_status_label = ttk.Label(view.contact_actions, textvariable=view.workspace_status, justify="left", style="Panel.Muted.TLabel")
    view.workspace_status_label.grid(row=0, column=0, sticky="ew", padx=(0, space(12)))
    view.workspace_status_label.bind("<Configure>", lambda event: view.workspace_status_label.configure(wraplength=event.width))
    view.review_button = ttk.Button(view.contact_actions, text="Continue", style="Primary.TButton", command=lambda: view.review(continue_to_account=view.native_active))
    view.review_button.grid(row=0, column=1, rowspan=2, sticky="e")
    view.pair_button = ttk.Button(view.contact_actions, text="Continue", style="Primary.TButton", command=view.pair)
    view.pair_button.grid(row=0, column=1, rowspan=2, sticky="e")
    actions = ttk.Frame(view.contact_actions, style="Panel.TFrame")
    actions.grid(row=1, column=0, sticky="w")
    view.details_button = ttk.Button(actions, text="Contact details", command=view.show_source_details, style="Quiet.TButton")
    view.details_button.pack(side="left")
    view.change_selection_button = ttk.Button(view.contact_actions, text="Change selected contacts", command=view.change_selection, style="Quiet.TButton")
    view.change_selection_button.grid(row=2, column=0, sticky="w")


def refresh(view):
    if not hasattr(view, "group_buttons"):
        return
    state, workspace = view.state, view.state.contact_workspace
    editing = state.can_edit_workspace and not view.native_selection_locked
    view.include_all.set(bool(workspace.known_ids and workspace.known_ids <= state.selected_ids) if workspace
                         else state.total_contacts > 0 and len(state.selected_ids) == state.total_contacts)
    view.select_all_button.state(["alternate"] if state.selected_ids and not view.include_all.get() else ["!alternate"])
    view.select_all_button.configure(text=f"Include all {state.total_contacts:,} contacts")
    for group, button in view.group_buttons.items():
        count = state.total_contacts if group == "All" else workspace.main_count if workspace and group == "Main" else workspace.review_count if workspace else 0
        button.configure(text=f"{group} {count:,}", state="normal" if editing else "disabled",
                         style="Selected.Group.TButton" if group == (workspace.group if workspace else "All") else "Group.TButton")
    intro = ("Review your selection in Main and Review. Only the contacts you choose continue to account review."
             if workspace else "Choose contacts to include. Your selection is kept while you search and change pages.")
    view.workspace_context.configure(text=intro)
    if state.contacts:
        view.contact_empty_label.grid_remove()
    else:
        empty = ("No contacts match this search." if state.contact_query else
                 "No contacts in Review. You can move contacts here from Main." if workspace and workspace.group == "Review" else
                 "No contacts in Main. Review contacts to choose who to include." if workspace and workspace.group == "Main" else
                 "No contacts are available in this collection.")
        view.contact_empty_label.configure(text=empty)
        view.contact_empty_label.grid()
    view.resize_contact_controls()
    scope = workspace.scope_label if workspace else "All contacts"
    selected = len(state.selected_ids & workspace.filtered_ids) if workspace else len(state.selected_ids)
    view.scope_count_label.configure(text=f"{selected:,} selected in {workspace.group if workspace else 'All'}")
    view.scope_select_button.configure(text=f"Select all {workspace.filtered_count:,} in {workspace.group}" if workspace else "Select matches",
                                       state="normal" if editing and workspace.filtered_ids - state.selected_ids else "disabled")
    view.scope_clear_button.configure(text=f"Deselect in {workspace.group}" if workspace else "Deselect matches",
                                      state="normal" if editing and selected else "disabled")
    view.scope_menu_button.configure(state="normal" if editing else "disabled")
    view.scope_menu.entryconfigure(0, label=f"Select all in {scope}", state="normal" if editing else "disabled")
    view.scope_menu.entryconfigure(1, label=f"Deselect all in {scope}", state="normal" if editing else "disabled")
    view.organize_button.configure(text=("Organizing" if view.organizing else "Organize") +
                                  (f" · {len(state.marked_group_ids):,} marked" if state.marked_group_ids else ""),
                                  state="normal" if editing or state.can_review and not view.native_selection_locked else "disabled")
    view.cancel_select_all_button.pack(side="left") if state.selecting_all else view.cancel_select_all_button.pack_forget()
    view.clear_all_button.pack(side="left") if workspace is None and not state.selecting_all else view.clear_all_button.pack_forget()
    if workspace:
        count = workspace.filtered_count
        start = workspace.page_start + 1 if count else 0
        view.contact_page.set(f"Showing {start:,}–{workspace.page_start + len(state.contacts):,} of {count:,} in {workspace.group}")
