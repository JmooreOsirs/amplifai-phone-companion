"""Contact selection behavior; actual Tk checks run only on Windows, withdrawn."""
from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

LOCATION = Path(__file__).resolve().parents[1] / "brand_widgets.py"
try:
    import tkinter as tk
except ModuleNotFoundError:
    tk = None


def load_widgets():
    spec = importlib.util.spec_from_file_location("brand_widgets_test_source", LOCATION)
    module = importlib.util.module_from_spec(spec)
    if tk is not None:
        spec.loader.exec_module(module)
    else:
        toolkit = types.ModuleType("tkinter")
        toolkit.Frame = toolkit.Canvas = object
        toolkit.font = types.ModuleType("tkinter.font")
        toolkit.ttk = types.ModuleType("tkinter.ttk")
        with patch.dict(sys.modules, {"tkinter": toolkit}):
            spec.loader.exec_module(module)
    return module


widgets = load_widgets()


class ContactSelectionTests(unittest.TestCase):
    def populated(self, count=4):
        model = widgets._ContactSelection()
        for index in range(count):
            model.append(f"Synthetic contact {index} · phone ending 0123")
        return model

    def test_two_hundred_contact_page_is_bounded_without_partial_extra_row(self):
        model = self.populated(200)
        model.select(199, True)
        with self.assertRaises(ValueError):
            model.append("Unverified extra contact")
        self.assertEqual(len(model.rows), 200)
        self.assertEqual(model.selected, {199})

    def test_disabled_page_cannot_change_selection_rows_or_active_contact(self):
        model = self.populated()
        model.select(1, True)
        model.activate(1)
        model.disabled = True
        self.assertFalse(model.select(1, False))
        self.assertFalse(model.select(2, True))
        self.assertFalse(model.append("Replacement"))
        self.assertFalse(model.clear())
        model.activate(3)
        self.assertEqual((len(model.rows), model.selected, model.active), (4, {1}, 1))

    def test_new_page_clears_old_selection_and_active_index(self):
        model = self.populated()
        model.select(3, True)
        model.activate(3)
        model.clear()
        model.append("New page · phone ending 4321")
        self.assertEqual((model.selected, model.active), (set(), 0))
        self.assertFalse(model.select(3, True))
        self.assertFalse(model.select(-1, True))

    def test_navigation_does_not_select_and_stays_within_page(self):
        model = self.populated(7)
        model.select(2, True)
        for index, expected in ((4, 4), (99, 6), (-10, 0)):
            model.activate(index)
            self.assertEqual(model.active, expected)
            self.assertEqual(model.selected, {2})

    def test_grid_respects_minimum_card_width_at_normal_and_large_dpi(self):
        self.assertEqual([widgets.contact_columns(width) for width in (375, 768, 1440)], [1, 3, 6])
        self.assertEqual(widgets.contact_columns(1152, scale=1.5), 3)
        self.assertEqual(widgets.contact_columns(0), 1)

    def test_text_wrap_keeps_long_names_and_unbroken_words(self):
        text = "A very long synthetic contact name"
        wrapped = widgets.wrap_name(text, 12, len)
        self.assertEqual(" ".join(wrapped.split()), text)
        self.assertTrue(all(len(line) <= 12 for line in wrapped.splitlines()))
        self.assertEqual(widgets.wrap_name("ABCDEFGHI", 3, len), "ABC\nDEF\nGHI")

    def test_phone_suffix_does_not_parse_delimiters_inside_name(self):
        name, ending = widgets.contact_text("Name · phone ending alias · phone ending 0123, 4321")
        self.assertEqual(name, "Name · phone ending alias")
        self.assertEqual(ending, "•••• 0123 · •••• 4321")
        self.assertEqual(widgets.contact_text(" · phone ending "), ("Unnamed contact", "No supported phone number"))


@unittest.skipUnless(sys.platform == "win32" and tk is not None, "actual Windows Tk required; no Mac GUI launched")
class ContactCardsTkTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.cards = widgets.ContactCards(self.root)
        self.cards.pack(fill="both", expand=True)
        self.notifications = []
        self.cards.bind("<<ListboxSelect>>", lambda _event: self.notifications.append(self.cards.curselection()))
        self.cards.insert(tk.END, "Synthetic one · phone ending 0123")
        self.cards.insert(tk.END, "Synthetic two · phone ending 4321")
        self.root.update_idletasks()

    def tearDown(self):
        self.root.destroy()

    def test_programmatic_restore_is_silent_but_native_checkbox_notifies(self):
        self.cards.selection_set(1)
        self.assertEqual(self.cards.curselection(), (1,))
        self.assertEqual(self.notifications, [])
        self.cards._cards[0].button.invoke()
        self.assertEqual(self.notifications, [(0, 1)])

    def test_disabled_native_checkbox_cannot_change_or_notify(self):
        self.cards.selection_set(1)
        self.cards.configure(state="disabled")
        self.cards._cards[0].button.invoke()
        self.cards.selection_set(0)
        self.cards.delete(0, tk.END)
        self.assertEqual(self.cards.curselection(), (1,))
        self.assertEqual(len(self.cards._cards), 2)
        self.assertEqual(self.notifications, [])
        self.assertEqual(self.cards.cget("state"), "disabled")

    def test_old_page_callback_cannot_toggle_replacement_card(self):
        old = self.cards._cards[0]
        self.cards.delete(0, tk.END)
        self.cards.insert(tk.END, "Replacement · phone ending 9999")
        self.cards._changed(old)
        self.assertEqual(self.cards.curselection(), ())
        self.assertEqual(self.notifications, [])

    def test_user_selection_callback_may_replace_the_current_page(self):
        def replace(_event):
            self.cards.delete(0, tk.END)
            self.cards.insert(tk.END, "Replacement · phone ending 9999")
        self.cards.bind("<<ListboxSelect>>", replace, add=True)
        self.cards._cards[1].button.invoke()
        self.root.update_idletasks()
        self.assertEqual(self.cards.curselection(), ())
        self.assertEqual(len(self.cards._cards), 1)

    def test_destroy_cancels_layout_and_removes_owned_callbacks(self):
        commands = set(self.root.tk.call("info", "commands"))
        extra = widgets.ContactCards(self.root)
        extra.insert(tk.END, "Synthetic pending layout · phone ending 0123")
        extra.destroy()
        self.root.update_idletasks()
        self.assertEqual(set(self.root.tk.call("info", "commands")), commands)

    def test_unchanged_width_and_page_do_not_measure_names_again(self):
        names = [card.button.cget("text") for card in self.cards._cards]
        with patch.object(self.cards._name_font, "measure", wraps=self.cards._name_font.measure) as measure:
            self.cards._viewport_width = self.cards._canvas.winfo_width()
            self.cards._viewport_changed(types.SimpleNamespace(width=self.cards._viewport_width, height=400))
            self.cards._queue_layout()
            self.root.update_idletasks()
            measure.assert_not_called()
        self.assertEqual([card.button.cget("text") for card in self.cards._cards], names)

    def test_card_menu_uses_text_width_instead_of_theme_minimum(self):
        from guided_layout import apply_style
        apply_style(self.root)
        self.cards.set_contact_tools(0, review=False, reason=None, marked=False, organizing=False,
                                     mark_command=lambda _index: None, move_command=lambda *_args: None)
        self.root.update_idletasks()
        self.assertLess(self.cards._cards[0].move_button.winfo_reqwidth(), round(48 * self.cards.scale))
        self.assertLessEqual(len(self.cards._cards[0].button.cget("text").splitlines()), 2)

    def test_tall_native_page_keeps_last_organize_control_inside_viewport(self):
        from guided_layout import apply_style
        apply_style(self.root)
        self.root.geometry("420x260")
        self.root.deiconify()
        self.cards.delete(0, tk.END)
        for index in range(200):
            self.cards.insert(tk.END, "Synthetic long contact name for a tall review card · phone ending 0123")
            self.cards.set_contact_tools(index, review=True, reason="Company or organization", marked=False,
                                         organizing=True, mark_command=lambda _index: None, move_command=lambda *_args: None)
        self.root.update()
        self.assertGreater(self.cards._scroll_bounds[3], 32768)
        last = self.cards._cards[-1]
        self.root.focus_force()
        last.mark_button.focus_set()
        self.root.update()
        for width in (420, 720, 420):
            self.root.geometry(f"{width}x260")
            self.root.update()
            self.assertIs(self.root.focus_get(), last.mark_button)
            canvas = self.cards._canvas
            top = last.mark_button.winfo_rooty() - canvas.winfo_rooty()
            self.assertGreaterEqual(top, 1)
            self.assertLessEqual(top + last.mark_button.winfo_height(), canvas.winfo_height() - 1)
        self.assertEqual(self.cards.curselection(), ())

    def test_panel_measures_wrapped_content_and_releases_pending_measurement(self):
        panel = widgets.RoundPanel(self.root, width=260)
        label = tk.Label(panel.content, text="Synthetic wrapped content " * 8, wraplength=200)
        label.pack(fill="x")
        self.root.update_idletasks()
        self.assertGreaterEqual(panel.winfo_reqheight(), label.winfo_reqheight() + 2 * panel.padding)
        panel.destroy()
        self.root.update_idletasks()
        commands = set(self.root.tk.call("info", "commands"))
        pending = widgets.RoundPanel(self.root)
        pending.destroy()
        self.root.update_idletasks()
        self.assertEqual(set(self.root.tk.call("info", "commands")), commands)


if __name__ == "__main__":
    unittest.main()
