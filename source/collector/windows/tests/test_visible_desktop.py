"""Headless rejection/placement checks; no Windows API or Tk execution."""
import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from visible_desktop import Desktop, choose_mode, intersects, mode_record, restore_mode, validate_visible


def snapshot():
    return {"hwnd": 123, "clientBounds": [8, 31, 928, 780], "tkClientBounds": [8, 31, 928, 780],
            "windowBounds": [-8, 0, 944, 796], "visibleFrameBounds": [0, 0, 936, 788],
            "monitorBounds": [0, 0, 1280, 1024], "workArea": [0, 0, 1280, 976],
            "dpiAwareness": 2, "windowDpi": 96, "occludingWindows": []}


def mode(width, height, **changes):
    values = dict(PelsWidth=width, PelsHeight=height, BitsPerPel=32, DisplayFrequency=60,
                  DisplayFlags=0, DisplayOrientation=0)
    values.update(changes)
    return SimpleNamespace(**values)


class VisibleDesktopTests(unittest.TestCase):
    def test_taskbar_repro_fails_even_when_exact_logical_client_size_matches(self):
        measured = snapshot()
        measured.update(monitorBounds=[0, 0, 1024, 768], workArea=[0, 0, 1024, 720])
        with self.assertRaisesRegex(RuntimeError, "outside the visible work area"):
            validate_visible(measured, (920, 749))

    def test_narrow_client_and_frame_fit_with_taskbar_present(self):
        measured = snapshot()
        measured.update(clientBounds=[8, 31, 688, 711], tkClientBounds=[8, 31, 688, 711],
                        visibleFrameBounds=[0, 0, 696, 719], monitorBounds=[0, 0, 1024, 768],
                        workArea=[0, 0, 1024, 720])
        self.assertIs(validate_visible(measured, (680, 680)), measured)

    def test_frame_clipping_fails_when_client_fits(self):
        measured = snapshot()
        measured["workArea"][3] = 784
        with self.assertRaisesRegex(RuntimeError, "outside the visible work area"):
            validate_visible(measured)

    def test_dpi_mismatch_and_smaller_client_cannot_substitute_for_requested_size(self):
        for change in ({"dpiAwareness": 0}, {"tkClientBounds": [8, 31, 698, 593]}):
            measured = snapshot(); measured.update(change)
            with self.subTest(change=change), self.assertRaisesRegex(RuntimeError, "coordinates differ"):
                validate_visible(measured)
        with self.assertRaisesRegex(RuntimeError, "viewport changed"):
            validate_visible(snapshot(), (920, 800))

    def test_overlapping_window_fails_even_inside_work_area(self):
        measured = snapshot()
        measured["occludingWindows"] = [{"hwnd": 456, "bounds": [100, 100, 200, 200]}]
        with self.assertRaisesRegex(RuntimeError, "covers the captured client"):
            validate_visible(measured)

    def test_exclusive_edges_and_negative_monitor_coordinates(self):
        self.assertFalse(intersects([0, 0, 100, 100], [0, 100, 100, 200]))
        self.assertTrue(intersects([0, 0, 100, 100], [0, 99, 100, 200]))
        measured = snapshot()
        for key in ("clientBounds", "tkClientBounds", "windowBounds", "visibleFrameBounds", "monitorBounds", "workArea"):
            measured[key][0] -= 1280; measured[key][2] -= 1280
        validate_visible(measured, (920, 749))

    def test_owned_window_placement_uses_visible_frame_and_keeps_exact_size(self):
        desktop = Desktop.__new__(Desktop)
        desktop.gui = Mock()
        desktop.con = SimpleNamespace(HWND_TOP=0, SWP_NOSIZE=1, SWP_NOACTIVATE=16)
        before = snapshot(); after = copy.deepcopy(before)
        for key in ("clientBounds", "tkClientBounds", "windowBounds", "visibleFrameBounds"):
            after[key][0] += 172; after[key][2] += 172
            after[key][1] += 94; after[key][3] += 94
        desktop.snapshot = Mock(side_effect=[before, after])
        root = Mock()
        self.assertEqual(desktop.place(root, 920, 749), after)
        root.geometry.assert_called_once_with("920x749+0+0")
        desktop.gui.SetWindowPos.assert_called_once_with(123, 0, 164, 94, 0, 0, 17)

    def test_placement_refuses_to_shrink_or_move_an_oversized_frame_offscreen(self):
        desktop = Desktop.__new__(Desktop)
        measured = snapshot(); measured["workArea"][3] = 720
        desktop.snapshot, desktop.gui = Mock(return_value=measured), Mock()
        with self.assertRaisesRegex(RuntimeError, "cannot fit work area"):
            desktop.place(Mock(), 920, 749)
        desktop.gui.SetWindowPos.assert_not_called()

    def test_resolution_selection_requires_advertised_compatible_mode(self):
        current = mode(1024, 768)
        valid = mode(1280, 1024)
        self.assertIs(choose_mode([mode(1920, 1080), mode(1280, 1024, DisplayFrequency=75), valid], current), valid)
        with self.assertRaisesRegex(RuntimeError, "No advertised compatible"):
            choose_mode([mode(1920, 1080, DisplayOrientation=1), mode(1280, 720)], current)

    def test_display_change_tests_before_applying_without_registry_flags(self):
        desktop = Desktop.__new__(Desktop)
        desktop.api = Mock()
        desktop.api.ChangeDisplaySettingsEx.return_value = 0
        desktop.con = SimpleNamespace(DM_PELSWIDTH=1, DM_PELSHEIGHT=2, DM_BITSPERPEL=4,
                                      DM_DISPLAYFREQUENCY=8, DM_DISPLAYFLAGS=16, DM_DISPLAYORIENTATION=32,
                                      CDS_TEST=2, DISP_CHANGE_SUCCESSFUL=0)
        target = mode(1280, 1024)
        desktop.apply_mode(target)
        self.assertEqual([call.args[2] for call in desktop.api.ChangeDisplaySettingsEx.call_args_list], [2, 0])
        desktop.api.reset_mock()
        desktop.api.ChangeDisplaySettingsEx.return_value = -2
        with self.assertRaisesRegex(RuntimeError, "failed CDS_TEST"):
            desktop.apply_mode(target)
        self.assertEqual(desktop.api.ChangeDisplaySettingsEx.call_count, 1)

    def test_restoration_requires_exact_original_mode(self):
        original = mode_record(mode(1024, 768))
        desktop = Mock()
        desktop.mode.side_effect = [mode(1280, 1024), mode(1024, 768)]
        restore_mode(desktop, original)
        self.assertEqual(mode_record(desktop.apply_mode.call_args.args[0]), original)
        desktop.mode.side_effect = [mode(1280, 1024), mode(1280, 1024)]
        with self.assertRaisesRegex(RuntimeError, "not restored"):
            restore_mode(desktop, original)

    def test_capture_rejects_obscured_window_before_grabbing_pixels(self):
        import render_candidate
        with tempfile.TemporaryDirectory() as folder, patch.object(render_candidate, "visible_snapshot", side_effect=RuntimeError("obscured")), patch.object(render_candidate.ImageGrab, "grab") as grab, patch.object(render_candidate.time, "sleep"):
            with self.assertRaisesRegex(RuntimeError, "obscured"):
                render_candidate.capture(Mock(), Path(folder), "unqualified")
            grab.assert_not_called()


if __name__ == "__main__":
    unittest.main()
