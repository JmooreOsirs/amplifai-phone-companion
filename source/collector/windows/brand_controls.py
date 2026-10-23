"""Native ttk derivations of the accepted Mac BrandControls primitives.

The rounded backgrounds are nine-slice images; Button and Menubutton still own
their native bindings, focus, state and menu behavior. The five-pixel focus
reserve contains Brand's three-pixel blue ring and two-pixel gap without a
layout change. Images are generated from Brand tokens with no bundled assets.
"""
from __future__ import annotations

import base64
import io
import struct
import tkinter as tk
import zlib
from tkinter import ttk
from PIL import Image, ImageDraw

from brand_tokens import BACKGROUND, PANEL, BORDER, HEADING, MUTED, LIME, BLUE, RAISED
from brand_widgets import RoundPanel


def surface(parent, *, kind="panel", padding=None, background=None, fill_content=False, **kwargs):
    """PanelStyle, BrandWell and the solid edge of BrandRaisedCard; use .content."""
    fill, radius, default_padding, backdrop = {
        "panel": (PANEL, 12, 20, BACKGROUND),
        "well": (BACKGROUND, 10, 12, PANEL),
        "raised": (RAISED, 10, 12, BACKGROUND),
    }[kind]
    return RoundPanel(parent, fill=fill, border=BORDER, radius=radius,
                      padding=default_padding if padding is None else padding,
                      background=backdrop if background is None else background,
                      fill_content=fill_content, **kwargs)


def _rgb(color):
    return tuple(int(color[index:index + 2], 16) for index in (1, 3, 5))


def _blend(color, backdrop, opacity):
    return "#" + "".join(f"{round(a * opacity + b * (1 - opacity)):02x}"
                          for a, b in zip(_rgb(color), _rgb(backdrop)))


def _rounded_png(fill, border, scale, *, radius=8, focus=False, reserve=True):
    """Small antialiased RGBA tile. The center and straight edges may stretch."""
    halo = round(5 * scale) if reserve else 0
    corner = max(1, round(radius * scale))
    edge = halo + corner
    size = 2 * edge + max(2, round(3 * scale))
    stroke = max(1, round(scale))
    focus_width = max(1, round(3 * scale))
    fill_rgb, border_rgb, focus_rgb = _rgb(fill), _rgb(border), _rgb(BLUE)

    def inside(x, y, inset, rounding):
        if not inset <= x < size - inset or not inset <= y < size - inset:
            return False
        rounding = max(0, rounding)
        dx = max(inset + rounding - x, x - (size - inset - rounding), 0)
        dy = max(inset + rounding - y, y - (size - inset - rounding), 0)
        return dx * dx + dy * dy <= rounding * rounding

    rows = bytearray()
    # Four subpixels per axis keep the curved corners smooth at Windows DPI.
    offsets = (0.125, 0.375, 0.625, 0.875)
    for y in range(size):
        rows.append(0)  # PNG's unfiltered scanline.
        for x in range(size):
            red = green = blue = covered = 0
            for dy in offsets:
                for dx in offsets:
                    px, py = x + dx, y + dy
                    color = None
                    if inside(px, py, halo, corner):
                        color = (fill_rgb if inside(px, py, halo + stroke, corner - stroke)
                                 else border_rgb)
                    elif focus and inside(px, py, 0, edge) and not inside(px, py, focus_width, edge - focus_width):
                        color = focus_rgb
                    if color is not None:
                        red += color[0]
                        green += color[1]
                        blue += color[2]
                        covered += 1
            rows.extend((round(red / covered), round(green / covered), round(blue / covered),
                         round(255 * covered / 16)) if covered else (0, 0, 0, 0))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))
    return png, edge, halo


def install_control_styles(window, style, *, scale=1.0):
    """Install image borders once per Tk interpreter, preserving ttk classes."""
    space = lambda value: max(1, round(value * scale))
    images = getattr(window, "_amplifai_control_images", {})
    window._amplifai_control_images = images

    def image(fill, border, *, focus=False, radius=8, reserve=True):
        key = (fill, border, focus, radius, reserve, scale)
        if key not in images:
            png, edge, halo = _rounded_png(fill, border, scale, radius=radius, focus=focus, reserve=reserve)
            images[key] = (tk.PhotoImage(master=window, data=base64.b64encode(png).decode("ascii")), edge, halo)
        return images[key]

    def glyph(key, size, draw):
        cache_key = ("glyph", key, scale)
        if cache_key not in images:
            width, height = (space(value) for value in size)
            canvas = Image.new("RGBA", (width * 4, height * 4))
            draw(ImageDraw.Draw(canvas), lambda value: round(value * scale * 4))
            output = io.BytesIO()
            canvas.resize((width, height), Image.Resampling.LANCZOS).save(output, format="PNG")
            images[cache_key] = tk.PhotoImage(master=window, data=base64.b64encode(output.getvalue()).decode("ascii"))
        return images[cache_key]

    def chevron(color):
        return glyph(("chevron", color), (16, 12), lambda draw, s:
                     draw.line([(s(4), s(4)), (s(8), s(8)), (s(12), s(4))], fill=color, width=s(2), joint="curve"))

    menu_indicator = "Amplifai.Menubutton.indicator"
    if menu_indicator not in style.element_names():
        style.element_create(menu_indicator, "image", chevron(LIME), ("disabled", chevron(MUTED)),
                             width=space(16), height=space(12), sticky="")

    def check_image(selected=False, alternate=False, disabled=False, focus=False):
        def draw_check(draw, s):
            accent = _blend(LIME, BACKGROUND, .55) if disabled else LIME
            outline = BLUE if alternate and not disabled else MUTED
            if focus:
                draw.rounded_rectangle((s(0), s(0), s(32) - 1, s(32) - 1), radius=s(8), outline=BLUE, width=s(3))
            draw.rounded_rectangle((s(5), s(5), s(27), s(27)), radius=s(5),
                                   fill=accent if selected else BACKGROUND,
                                   outline=accent if selected else outline, width=s(2))
            if selected:
                draw.line([(s(10), s(16)), (s(14), s(20)), (s(22), s(11))], fill=BACKGROUND, width=s(2), joint="curve")
            elif alternate:
                draw.line([(s(11), s(16)), (s(21), s(16))], fill=outline, width=s(2))
        return glyph(("check", selected, alternate, disabled, focus), (32, 32), draw_check)

    check_indicator = "Amplifai.Checkbutton.indicator"
    if check_indicator not in style.element_names():
        style.element_create(check_indicator, "image", check_image(),
            ("disabled", "selected", check_image(selected=True, disabled=True)),
            ("disabled", "alternate", check_image(alternate=True, disabled=True)),
            ("disabled", check_image(disabled=True)),
            ("focus", "selected", check_image(selected=True, focus=True)),
            ("focus", "alternate", check_image(alternate=True, focus=True)),
            ("focus", check_image(focus=True)), ("selected", check_image(selected=True)),
            ("alternate", check_image(alternate=True)), width=space(32), height=space(32), sticky="")
    style.layout("TCheckbutton", [("Checkbutton.padding", {"sticky": "nsew", "children": [
        (check_indicator, {"side": "left", "sticky": ""}),
        ("Checkbutton.label", {"side": "left", "sticky": ""})]})])

    def control(name, key, fill, foreground, *, disabled_fill=None, radius=8, quiet=False,
                menu=False, padding=(18, 10), font_size=11, active_foreground=None,
                disabled_foreground=None, disabled_border=None, backdrop=PANEL):
        outline = fill if quiet or fill == LIME else BORDER
        disabled_fill = disabled_fill or fill
        disabled_foreground = disabled_foreground or _blend(foreground, disabled_fill, 0.55)
        active_foreground = active_foreground or foreground
        pressed = _blend(fill, BACKGROUND, 0.8)
        normal, edge, halo = image(fill, outline, radius=radius)
        active = image(fill, LIME if key in {"Group", "CardMenu"} else outline, radius=radius)[0]
        disabled = image(disabled_fill, disabled_border or (disabled_fill if quiet else BORDER), radius=radius)[0]
        down = image(pressed, outline, radius=radius)[0]
        focused = image(fill, outline, focus=True, radius=radius)[0]
        focused_down = image(pressed, outline, focus=True, radius=radius)[0]
        element = f"Amplifai.{key}.border"
        if element not in style.element_names():
            style.element_create(element, "image", normal, ("disabled", disabled),
                                 ("pressed", "focus", focused_down), ("focus", focused),
                                 ("pressed", down), ("active", active),
                                 border=edge, padding=halo, width=0, height=0, sticky="nsew")
        kind = "Menubutton" if menu else "Button"
        children = [(f"{kind}.label", {"side": "left", "sticky": ""})]
        if menu and key != "CardMenu":
            children.insert(0, (menu_indicator, {"side": "right", "sticky": ""}))
        style.layout(name, [(element, {"sticky": "nsew", "children": [
            (f"{kind}.padding", {"sticky": "nsew", "children": children})]})])
        style.configure(name, background=backdrop, foreground=foreground, bordercolor=outline,
                        lightcolor=fill, darkcolor=fill, relief="flat", borderwidth=0,
                        width=0,
                        padding=tuple(space(value) for value in padding),
                        font=("Arial", font_size, "bold", "underline") if quiet and not menu else ("Arial", font_size, "bold"),
                        arrowcolor=foreground, arrowsize=space(12), focuscolor=BLUE)
        pressed_foreground = _blend(foreground, BACKGROUND, 0.8) if quiet or key in {"Group", "SelectedGroup"} else foreground
        style.map(name, background=[("disabled", backdrop), ("pressed", backdrop), ("active", backdrop)],
                  foreground=[("disabled", disabled_foreground), ("pressed", pressed_foreground), ("active", active_foreground)],
                  arrowcolor=[("disabled", disabled_foreground), ("active", active_foreground)])

    control("TButton", "Secondary", BORDER, HEADING, disabled_fill=PANEL, disabled_foreground=MUTED)
    control("Primary.TButton", "Primary", LIME, BACKGROUND, disabled_fill=BORDER,
            disabled_foreground=MUTED, padding=(18, 12))
    control("Quiet.TButton", "Quiet", BACKGROUND, MUTED, quiet=True, padding=(8, 4),
            font_size=10, active_foreground=HEADING, backdrop=BACKGROUND)
    control("Group.TButton", "Group", BACKGROUND, HEADING, radius=16, padding=(12, 4), font_size=10,
            disabled_border=_blend(BORDER, BACKGROUND, 0.55), backdrop=BACKGROUND)
    selected_disabled = _blend(LIME, BACKGROUND, 0.55)
    control("Selected.Group.TButton", "SelectedGroup", LIME, BACKGROUND, disabled_fill=selected_disabled,
            disabled_foreground=BACKGROUND, disabled_border=selected_disabled, radius=16, padding=(12, 4), font_size=10,
            backdrop=BACKGROUND)
    control("Quiet.TMenubutton", "QuietMenu", BACKGROUND, LIME, quiet=True, menu=True, padding=(8, 4), font_size=10,
            backdrop=BACKGROUND)
    control("Card.TMenubutton", "CardMenu", RAISED, MUTED, quiet=True, menu=True, padding=(4, 0), font_size=12,
            backdrop=RAISED)
    style.map("Card.TMenubutton", foreground=[("disabled", _blend(MUTED, RAISED, 0.55)), ("active", LIME)],
              arrowcolor=[("disabled", _blend(MUTED, RAISED, 0.55)), ("active", LIME)])

    for name, fill, foreground in (("Stage.TLabel", BACKGROUND, MUTED),
                                   ("Completed.Stage.TLabel", BACKGROUND, HEADING),
                                   ("Active.Stage.TLabel", LIME, BACKGROUND)):
        tile, edge, _ = image(fill, LIME if fill == LIME else BORDER, radius=16, reserve=False)
        element = f"Amplifai.{name}.border"
        if element not in style.element_names():
            style.element_create(element, "image", tile, border=edge, padding=0, width=0, height=0, sticky="nsew")
        style.layout(name, [(element, {"sticky": "nsew", "children": [
            ("Label.padding", {"sticky": "nsew", "children": [("Label.label", {"sticky": ""})]})]})])
        style.configure(name, background=PANEL, foreground=foreground, font=("Arial", 10, "bold"),
                        padding=(space(12), space(4)), anchor="center")

    trough, edge, _ = image(BORDER, BORDER, radius=5, reserve=False)
    bar = image(LIME, LIME, radius=5, reserve=False)[0]
    for suffix, tile in (("trough", trough), ("pbar", bar)):
        element = f"Amplifai.Horizontal.Progressbar.{suffix}"
        if element not in style.element_names():
            style.element_create(element, "image", tile, border=edge, padding=0, width=0, height=0, sticky="nsew")
    style.layout("Horizontal.TProgressbar", [("Amplifai.Horizontal.Progressbar.trough", {
        "sticky": "nsew", "children": [("Amplifai.Horizontal.Progressbar.pbar", {"side": "left", "sticky": "ns"})]})])
    style.configure("Horizontal.TProgressbar", background=PANEL, troughcolor=BORDER,
                    lightcolor=LIME, darkcolor=LIME, borderwidth=0, thickness=space(14))

    field, edge, halo = image(PANEL, BORDER, radius=16)
    field_focus = image(PANEL, BLUE, focus=True, radius=16)[0]
    field_element = "Amplifai.Entry.field"
    if field_element not in style.element_names():
        style.element_create(field_element, "image", field, ("focus", field_focus), border=edge,
                             padding=halo, width=0, height=0, sticky="nsew")
    style.layout("TEntry", [(field_element, {"sticky": "nsew", "children": [
        ("Entry.padding", {"sticky": "nsew", "children": [("Entry.textarea", {"sticky": "nsew"})]})]})])
    style.configure("TEntry", background=BACKGROUND, fieldbackground=PANEL)
    style.configure("Vertical.TScrollbar", background=BORDER, troughcolor=BACKGROUND, bordercolor=BACKGROUND,
                    lightcolor=BORDER, darkcolor=BORDER, arrowcolor=MUTED, gripcount=0)
    style.map("Vertical.TScrollbar", background=[("active", MUTED), ("pressed", MUTED)])


class JourneySteps(ttk.Frame):
    """Mac's connected, informational journey; the circles are not Tab stops."""
    names = ("Approve", "Collect", "Choose", "Save & report")

    def __init__(self, parent, *, scale=1.0):
        super().__init__(parent, takefocus=False)
        self.space = lambda value: max(1, round(value * scale))
        self.current = None
        self.labels, self.circles, self.connectors = [], [], []
        self._compact = False
        for index, name in enumerate(self.names):
            circle = tk.Canvas(self, width=self.space(22), height=self.space(22),
                               background=BACKGROUND, highlightthickness=0, borderwidth=0, takefocus=False)
            circle.grid(row=0, column=index * 3, padx=(0, self.space(6)))
            item = ttk.Label(self, text=name, style="Journey.TLabel", takefocus=False)
            item.grid(row=0, column=index * 3 + 1, sticky="w")
            self.circles.append(circle)
            self.labels.append(item)
            if index < 3:
                line = tk.Frame(self, height=self.space(1), width=self.space(24), background=BORDER, takefocus=False)
                line.grid(row=0, column=index * 3 + 2, padx=self.space(8))
                self.connectors.append(line)
        self.bind("<Configure>", self._resize)
        self.set_current(0)

    def _resize(self, event):
        compact = event.width < self.space(540)
        if compact != self._compact:
            self._compact = compact
            self._arrange()

    def _arrange(self):
        for index, item in enumerate(self.labels):
            item.grid() if not self._compact or index == min(3, self.current) else item.grid_remove()
        for line in self.connectors:
            line.configure(width=self.space(10 if self._compact else 24))

    def set_current(self, current):
        if current == self.current:
            return
        self.current = current
        for index, (circle, item) in enumerate(zip(self.circles, self.labels)):
            complete, active = index < current, index == current
            circle.delete("all")
            circle.create_oval(self.space(1), self.space(1), self.space(21), self.space(21),
                               fill=LIME if active or complete else BACKGROUND,
                               outline=LIME if active or complete else BORDER, width=self.space(1))
            if complete:
                circle.create_line(self.space(6), self.space(11), self.space(10), self.space(15),
                                   self.space(16), self.space(7), fill=BACKGROUND, width=self.space(2),
                                   capstyle="round", joinstyle="round")
            else:
                circle.create_text(self.space(11), self.space(11), text=str(index + 1),
                                   fill=BACKGROUND if active else MUTED, font=("Arial", 9, "bold"))
            item.configure(style="Current.Journey.TLabel" if index <= current else "Journey.TLabel")
        self._arrange()


class CollectionIllustration(tk.Canvas):
    """Decorative vector equivalents of the Mac phone/desktop and disk/read symbols."""
    def __init__(self, parent, *, scale=1.0):
        self.space = lambda value: round(value * scale)
        self._processing = None
        super().__init__(parent, width=self.space(248), height=self.space(68),
                         background=BACKGROUND, highlightthickness=0, borderwidth=0, takefocus=False)
        self.bind("<Configure>", lambda _event: self._draw())
        self.set_processing(False)

    def set_processing(self, processing):
        if processing == self._processing:
            return
        self._processing = processing
        self._draw()

    def _draw(self):
        self.delete("all")
        s = self.space
        stroke = max(1, s(2))

        def line(*points, **kwargs):
            self.create_line(*(s(value) for value in points), fill=BLUE, width=stroke, **kwargs)

        def rect(*points):
            self.create_rectangle(*(s(value) for value in points), outline=BLUE, width=stroke)

        if self._processing:
            rect(22, 18, 78, 53)
            line(22, 42, 78, 42)
            line(31, 48, 45, 48)
            self.create_oval(s(67), s(47), s(70), s(50), fill=BLUE, outline=BLUE)
            line(179, 5, 206, 5, 218, 17, 218, 39)
            line(179, 5, 179, 60, 200, 60)
            line(205, 5, 205, 18, 218, 18)
            for y in (26, 33, 40):
                line(187, y, 203, y)
            self.create_oval(s(202), s(42), s(221), s(61), outline=BLUE, width=stroke)
            line(218, 58, 227, 67)
        else:
            # Rounded line joins avoid importing icon artwork or a new icon family.
            line(36, 4, 64, 4, 67, 7, 67, 60, 64, 63, 36, 63, 33, 60, 33, 7, 36, 4,
                 joinstyle="round")
            line(44, 9, 56, 9)
            line(45, 57, 55, 57)
            rect(166, 9, 236, 51)
            line(193, 52, 193, 60, 184, 64, 218, 64, 209, 60, 209, 52)
        line(101, 34, 139, 34, arrow="last", arrowshape=(s(9), s(11), s(5)))
        width = self.winfo_width() if self.winfo_width() > 1 else self.winfo_reqwidth()
        for item in self.find_all():
            xs = self.coords(item)[::2]
            anchor, fraction = (50, .25) if max(xs) < s(100) else (200, .75) if min(xs) > s(150) else (120, .5)
            self.move(item, width * fraction - s(anchor), 0)
