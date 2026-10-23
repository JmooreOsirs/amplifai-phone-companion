"""Windows derivations of Mac BrandWell, BrandRaisedCard and ContactReviewCard.

Native checkbuttons retain keyboard/accessibility behavior. The raised fill uses the
Mac gradient's 10% heading/panel edge as a solid surface beneath native controls.
Selection here is presentation only; Session remains the authority for review locks.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont, ttk

from brand_tokens import (BACKGROUND, PANEL, BORDER, HEADING, MUTED, LIME, BLUE, RAISED,
                          CONTACT_MINIMUM, ITEM_SPACE, PANEL_RADIUS, CARD_RADIUS, SECTION_SPACE)

ITEM_GAP = ITEM_SPACE
PAGE_LIMIT = 200


def contact_columns(width: int, scale: float = 1.0) -> int:
    gap = round(ITEM_GAP * scale)
    return max(1, (width - gap) // (round(CONTACT_MINIMUM * scale) + gap))


def contact_text(value: str) -> tuple[str, str]:
    name, separator, suffix = value.rpartition(" · phone ending ")
    if not separator:
        return value.strip() or "Unnamed contact", "No supported phone number"
    endings = [part.strip() for part in suffix.split(",") if part.strip()]
    return (name.strip() or "Unnamed contact",
            " · ".join(f"•••• {ending}" for ending in endings) or "No supported phone number")


def wrap_name(value: str, width: int, measure) -> str:
    """ttk checkbutton has no wraplength; retain every character in long words."""
    lines, current = [], ""
    for word in value.split():
        candidate = f"{current} {word}" if current else word
        if measure(candidate) <= width:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = ""
        for character in word:
            if current and measure(current + character) > width:
                lines.append(current)
                current = ""
            current += character
    if current:
        lines.append(current)
    return "\n".join(lines)


class _ContactSelection:
    def __init__(self):
        self.rows: list[str] = []
        self.selected: set[int] = set()
        self.active = 0
        self.disabled = False

    def append(self, value: str) -> bool:
        if self.disabled:
            return False
        if len(self.rows) >= PAGE_LIMIT:
            raise ValueError("Contact cards require a page of at most 200 contacts")
        if not isinstance(value, str):
            raise TypeError("Contact card text must be a string")
        self.rows.append(value)
        return True

    def clear(self) -> bool:
        if self.disabled:
            return False
        self.rows.clear()
        self.selected.clear()
        self.active = 0
        return True

    def select(self, index: int, selected: bool) -> bool:
        if self.disabled or not 0 <= index < len(self.rows):
            return False
        changed = (index in self.selected) != selected
        if selected:
            self.selected.add(index)
        else:
            self.selected.discard(index)
        return changed

    def activate(self, index: int) -> None:
        if not self.disabled:
            self.active = max(0, min(index, len(self.rows) - 1))


def _rounded_points(width: int, height: int, radius: int, inset: int) -> tuple:
    left = top = inset
    right, bottom = max(left, width - inset), max(top, height - inset)
    radius = min(radius, (right - left) / 2, (bottom - top) / 2)
    return (left + radius, top, right - radius, top, right, top, right, top + radius,
            right, bottom - radius, right, bottom, right - radius, bottom,
            left + radius, bottom, left, bottom, left, bottom - radius,
            left, top + radius, left, top)


class RoundPanel(tk.Canvas):
    """Rounded native content host; requested height follows its content frame."""
    def __init__(self, master, *, fill=PANEL, border=BORDER, background=BACKGROUND,
                 radius=PANEL_RADIUS, padding=SECTION_SPACE, scale=None, fill_content=False, **kwargs):
        self.scale = scale if scale is not None else float(master.tk.call("tk", "scaling")) / (96 / 72)
        self.padding = round(padding * self.scale)
        self.radius = round(radius * self.scale)
        self._fill, self._border = fill, border
        self._focused = False
        self._measure_job = None
        self._fill_content = fill_content
        kwargs.setdefault("width", round(CONTACT_MINIMUM * self.scale))
        kwargs.setdefault("height", 2 * self.padding + 1)
        super().__init__(master, background=background, highlightthickness=0, borderwidth=0,
                         takefocus=False, **kwargs)
        self.content = tk.Frame(self, background=fill, borderwidth=0)
        self._content_window = self.create_window(self.padding, self.padding, anchor="nw", window=self.content)
        self.content.bind("<Configure>", self._queue_measure)
        self.bind("<Configure>", self._resize)
        self.bind("<Map>", self._queue_measure)
        self._queue_measure()

    def set_border(self, color: str) -> None:
        if self._border != color:
            self._border = color
            self._draw()

    def set_padding(self, padding: int) -> None:
        if self.padding != padding:
            self.padding = padding
            self.coords(self._content_window, padding, padding)
            self.itemconfigure(self._content_window, width=max(1, self.winfo_width() - 2 * padding))
            if self._fill_content:
                self.itemconfigure(self._content_window, height=max(1, self.winfo_height() - 2 * padding))
            self._queue_measure()

    def set_focus(self, focused: bool) -> None:
        if self._focused != focused:
            self._focused = focused
            self._draw()

    def _resize(self, event) -> None:
        self.itemconfigure(self._content_window, width=max(1, event.width - 2 * self.padding))
        if self._fill_content:
            self.itemconfigure(self._content_window, height=max(1, event.height - 2 * self.padding))
        self._draw()
        self._queue_measure()

    def _queue_measure(self, _event=None) -> None:
        if self._measure_job is None:
            self._measure_job = self.after_idle(self._defer_measure)

    def _defer_measure(self) -> None:
        # Child pack/grid requests queued during construction must settle first,
        # including when this stage is hidden and emits no Configure event.
        self._measure_job = self.after_idle(self._measure)

    def _measure(self) -> None:
        self._measure_job = None
        if self._fill_content:
            return
        height = self.content.winfo_reqheight() + 2 * self.padding
        if int(self.cget("height")) != height:
            self.configure(height=height)

    def _draw(self) -> None:
        self.delete("panel-shape")
        width, height = self.winfo_width(), self.winfo_height()
        inset = max(1, round(3 * self.scale))
        self.create_polygon(_rounded_points(width, height, self.radius, inset),
                            fill=self._fill, outline=self._border, width=max(1, round(self.scale)),
                            smooth=True, splinesteps=12, tags="panel-shape")
        if self._focused:
            self.create_polygon(_rounded_points(width, height, self.radius + inset, 1),
                                fill="", outline=BLUE, width=inset, smooth=True,
                                splinesteps=12, tags="panel-shape")
        self.tag_lower("panel-shape")

    def destroy(self) -> None:
        if self._measure_job is not None:
            self.after_cancel(self._measure_job)
            self._measure_job = None
        super().destroy()


class _ContactCard:
    def __init__(self, owner, index: int, value: str):
        self.index = index
        self.name, ending = contact_text(value)
        self.variable = tk.BooleanVar(master=owner, value=False)
        self.panel = RoundPanel(owner._content, fill=RAISED, radius=CARD_RADIUS, padding=ITEM_GAP,
                                scale=owner.scale)
        self.panel.content.grid_columnconfigure(0, weight=1)
        self.panel.content.grid_rowconfigure(0, minsize=round(32 * owner.scale))
        self.button = ttk.Checkbutton(self.panel.content, text=self.name, variable=self.variable,
                                      style=owner._checkbutton_style, command=lambda: owner._changed(self))
        self.button.grid(row=0, column=0, sticky="ew")
        self.ending = tk.Label(self.panel.content, text=ending, anchor="nw", justify="left",
                               background=RAISED, foreground=MUTED, font=owner._detail_font,
                               borderwidth=0, padx=0, pady=0)
        self.ending.grid(row=1, column=0, sticky="ew", padx=(round(34 * owner.scale), 0))
        self.tools_enabled = False
        self.layout_key = None
        self.mark_command = self.move_command = None
        self.marked = tk.BooleanVar(master=owner, value=False)
        self.move_menu = tk.Menu(self.panel, tearoff=False)
        self.move_button = ttk.Menubutton(self.panel.content, text="⋯", menu=self.move_menu,
                                          style="Card.TMenubutton", takefocus=True)
        self.reason = tk.Label(self.panel.content, background=RAISED, foreground=MUTED,
                               font=owner._detail_font, anchor="nw", justify="left", borderwidth=0, padx=0, pady=0)
        self.mark_button = ttk.Checkbutton(self.panel.content, text="Mark to move", variable=self.marked,
                                           style=owner._checkbutton_style, command=lambda: owner._mark_card(self))
        self.button.bind("<FocusIn>", lambda _event: owner._focus_card(self))
        self.button.bind("<FocusOut>", lambda _event: self.panel.set_focus(False))
        for item in (self.move_button, self.mark_button):
            item.bind("<FocusIn>", lambda _event: owner._focus_card(self))
            item.bind("<FocusOut>", lambda _event: self.panel.set_focus(False))
        for key in ("Left", "Right", "Up", "Down", "Home", "End"):
            self.button.bind(f"<Key-{key}>", lambda event: owner._navigate(self, event.keysym))
        for widget in (self.panel, self.panel.content, self.button, self.ending, self.move_button, self.reason, self.mark_button):
            owner._bind_wheel(widget)
            if widget in (self.panel, self.panel.content, self.ending, self.reason):
                widget.bind("<Button-1>", lambda _event: owner._click_card(self))


class ContactCards(tk.Frame):
    """A bounded contact grid with the application's existing Listbox interface.

    The caller restores verified selection using selection_set. Only a user's
    checkbox action emits <<ListboxSelect>>, synchronously against the current page.
    """
    def __init__(self, master, *, font=("Arial", 12), checkbutton_style="Contact.TCheckbutton", **kwargs):
        super().__init__(master, background=BACKGROUND, borderwidth=0, **kwargs)
        self.scale = float(self.tk.call("tk", "scaling")) / (96 / 72)
        self._model = _ContactSelection()
        self._cards: list[_ContactCard] = []
        self._font_spec = font
        self._name_font = tkfont.Font(root=self, font=font)
        self._name_font.configure(weight="bold")
        self._detail_font = tkfont.Font(root=self, font=font)
        self._detail_font.configure(size=round(abs(self._detail_font.cget("size")) * 13 / 16))
        self._checkbutton_style = checkbutton_style
        self._layout_job = None
        self._viewport_width = None
        self._layout_width = None
        self._scroll_bounds = None
        self._columns = 1
        self._wheel_remainder = 0
        self._aqua = self.tk.call("tk", "windowingsystem") == "aqua"
        self._canvas = tk.Canvas(self, background=BACKGROUND, highlightbackground=BORDER,
                                 highlightthickness=1, borderwidth=0, takefocus=False,
                                 width=round((CONTACT_MINIMUM + 2 * ITEM_GAP) * self.scale),
                                 height=round(240 * self.scale), yscrollincrement=1)
        self._canvas.pack(fill="both", expand=True)
        self._content = tk.Frame(self._canvas, background=BACKGROUND, borderwidth=0)
        self._content_window = self._canvas.create_window(round(ITEM_GAP * self.scale / 2), 0,
                                                          anchor="nw", window=self._content)
        self._canvas.bind("<Configure>", self._viewport_changed)
        self._content.bind("<Configure>", self._content_changed)
        self._bind_wheel(self._canvas)
        self._bind_wheel(self._content)

    def configure(self, cnf=None, **kwargs):
        if isinstance(cnf, str):
            return super().configure(cnf, **kwargs)
        options = dict(cnf or {}, **kwargs)
        if "state" in options:
            state = options.pop("state")
            if state not in ("normal", "disabled"):
                raise tk.TclError("Contact cards state must be normal or disabled")
            disabled = state == "disabled"
            if disabled != self._model.disabled:
                self._model.disabled = disabled
                for card in self._cards:
                    for control in (card.button, card.move_button, card.mark_button):
                        control.state(["disabled"] if disabled else ["!disabled"])
                    if disabled:
                        card.panel.set_focus(False)
        if "yscrollcommand" in options:
            self._canvas.configure(yscrollcommand=options.pop("yscrollcommand"))
        return super().configure(**options) if options or not (cnf or kwargs) else None

    config = configure

    def cget(self, key):
        if key == "state":
            return "disabled" if self._model.disabled else "normal"
        if key == "font":
            return self._font_spec
        return super().cget(key)

    def delete(self, first, last=None) -> None:
        if str(first) != "0" or last != "end":
            raise tk.TclError("Contact cards replace complete pages with delete(0, END)")
        if not self._model.clear():
            return
        for card in self._cards:
            card.panel.destroy()
        self._cards.clear()
        self._canvas.yview_moveto(0)
        self._queue_layout()

    def insert(self, index, value: str) -> None:
        if index != "end":
            raise tk.TclError("Contact cards append page entries with insert(END, text)")
        if self._model.append(value):
            self._cards.append(_ContactCard(self, len(self._cards), value))
            self._queue_layout()

    def selection_set(self, index: int) -> None:
        if self._model.select(index, True):
            self._refresh_card(self._cards[index])

    def set_contact_tools(self, index: int, *, review: bool, reason: str | None, marked: bool,
                          organizing: bool, mark_command, move_command) -> None:
        card = self._cards[index]
        card.tools_enabled = True
        card.mark_command, card.move_command = mark_command, move_command
        card.marked.set(marked)
        card.move_menu.delete(0, tk.END)
        destination = "Main" if review else "Review"
        card.move_menu.add_command(label=f"Move to {destination}", command=lambda: self._move_card(card, destination))
        card.move_button.grid(row=0, column=1, sticky="ne")
        if reason:
            card.reason.configure(text=reason)
            card.reason.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(round(6 * self.scale), 0))
        else:
            card.reason.grid_remove()
        if organizing:
            card.mark_button.grid(row=3, column=0, columnspan=2, sticky="w", pady=(round(6 * self.scale), 0))
        else:
            card.mark_button.grid_remove()
        self._queue_layout()

    def _mark_card(self, card) -> None:
        if self._current(card) and not self._model.disabled and card.mark_command is not None:
            card.mark_command(card.index)

    def _move_card(self, card, destination: str) -> None:
        if self._current(card) and not self._model.disabled and card.move_command is not None:
            card.move_command(card.index, destination)

    def curselection(self) -> tuple[int, ...]:
        return tuple(sorted(self._model.selected))

    def index(self, value) -> int:
        return self._model.active if value == "active" else int(value)

    def focus_set(self) -> None:
        if self._cards and not self._model.disabled:
            self._cards[self._model.active].button.focus_set()

    def yview(self, *args):
        return self._canvas.yview(*args)

    def yview_moveto(self, fraction):
        return self._canvas.yview_moveto(fraction)

    def _current(self, card) -> bool:
        return 0 <= card.index < len(self._cards) and self._cards[card.index] is card

    def _refresh_card(self, card) -> None:
        selected = card.index in self._model.selected
        card.variable.set(selected)
        card.panel.set_border(LIME if selected else BORDER)

    def _changed(self, card) -> None:
        if not self._current(card):
            return
        changed = self._model.select(card.index, bool(card.variable.get()))
        self._refresh_card(card)
        if changed:
            self._model.activate(card.index)
            # Synchronous delivery prevents a queued old-page event reading a new page.
            self.event_generate("<<ListboxSelect>>", when="now")

    def _click_card(self, card) -> None:
        if self._current(card) and not self._model.disabled:
            card.button.focus_set()
            card.button.invoke()

    def _focus_card(self, card) -> None:
        if self._current(card) and not self._model.disabled:
            self._model.activate(card.index)
            card.panel.set_focus(True)
            self._see(card)

    def _navigate(self, card, key: str) -> str:
        if self._current(card) and not self._model.disabled:
            movement = {"Left": -1, "Right": 1, "Up": -self._columns, "Down": self._columns}
            target = 0 if key == "Home" else len(self._cards) - 1 if key == "End" else card.index + movement[key]
            self._model.activate(target)
            next_card = self._cards[self._model.active]
            next_card.button.focus_set()
            self._see(next_card)
        return "break"

    def _see(self, card) -> None:
        total, viewport = self._content.winfo_height(), self._canvas.winfo_height()
        if total <= viewport:
            return
        top = self._canvas.canvasy(0)
        y, height = card.panel.winfo_y(), card.panel.winfo_height()
        # A tall card can exceed a compact viewport. Reveal the actual focused
        # control in that case; aligning the whole card hides its top control.
        focused = self.focus_get()
        if height > viewport - 2 and focused in (card.button, card.move_button, card.mark_button):
            y = focused.winfo_rooty() - self._content.winfo_rooty()
            height = focused.winfo_height()
        if y < top:
            self._canvas.yview_moveto(max(0, y - 1) / total)
        elif y + height > top + viewport - 2:
            self._canvas.yview_moveto((y + height + 2 - viewport) / total)

    def _bind_wheel(self, widget) -> None:
        widget.bind("<MouseWheel>", self._wheel)
        widget.bind("<Button-4>", lambda _event: self._scroll(-1))
        widget.bind("<Button-5>", lambda _event: self._scroll(1))

    def _wheel(self, event) -> str:
        if self._aqua:
            return self._scroll(-event.delta)
        self._wheel_remainder += event.delta
        units = int(self._wheel_remainder / 120)
        self._wheel_remainder -= units * 120
        return self._scroll(-units)

    def _scroll(self, units: int) -> str:
        self._canvas.yview_scroll(units * round(24 * self.scale), "units")
        return "break"

    def _viewport_changed(self, event) -> None:
        if event.width == self._viewport_width:
            return
        self._viewport_width = event.width
        gap = round(ITEM_GAP * self.scale)
        self._canvas.itemconfigure(self._content_window, width=max(1, event.width - 2 - gap))
        self._queue_layout()

    def _content_changed(self, _event=None) -> None:
        bounds = self._canvas.bbox(self._content_window)
        if bounds and bounds != self._scroll_bounds:
            self._scroll_bounds = bounds
            self._canvas.configure(scrollregion=(0, 0, bounds[2], bounds[3] + round(ITEM_GAP * self.scale)))

    def _queue_layout(self) -> None:
        if self._layout_job is None:
            self._layout_job = self.after_idle(self._layout)

    def _layout(self) -> None:
        self._layout_job = None
        actual_width = self._canvas.winfo_width()
        width = max(1, (actual_width if actual_width > 1 else self._canvas.winfo_reqwidth()) - 2)
        columns = contact_columns(width, self.scale)
        gap = round(ITEM_GAP * self.scale)
        card_width = max(1, (width - (columns + 1) * gap) // columns)
        if width != self._layout_width:
            for column in range(max(self._columns, columns)):
                active = column < columns
                self._content.grid_columnconfigure(column, weight=1 if active else 0,
                                                    uniform="contact-cards" if active else "",
                                                    minsize=card_width + gap if active else 0)
            self._layout_width = width
        self._columns = columns
        for index, card in enumerate(self._cards):
            menu_width = card.move_button.winfo_reqwidth() if card.tools_enabled else 0
            key = (columns, card_width, menu_width)
            if card.layout_key == key:
                continue
            card.layout_key = key
            card.panel.grid(row=index // columns, column=index % columns, sticky="nsew",
                            padx=(gap // 2, gap - gap // 2), pady=(gap, 0))
            card.panel.configure(width=card_width)
            text_width = max(1, card_width - round(62 * self.scale) - menu_width)
            card.button.configure(text=wrap_name(card.name, text_width, self._name_font.measure))
            card.ending.configure(wraplength=text_width)
            card.reason.configure(wraplength=max(1, card_width - 2 * card.panel.padding))
        self._content_changed()

    def destroy(self) -> None:
        if self._layout_job is not None:
            self.after_cancel(self._layout_job)
            self._layout_job = None
        super().destroy()
        self._cards.clear()
        self._name_font = self._detail_font = None
