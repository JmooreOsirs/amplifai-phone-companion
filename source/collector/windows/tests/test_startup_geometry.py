"""Startup fit regressions using measured Windows bounds; no native Tk launch."""
import ctypes
import unittest
from unittest.mock import Mock, patch

import app


CLIENT = (0, 0, 920, 749)
FRAME = (85, 0, 1007, 781)
OUTER = (78, 0, 1014, 788)
WORK = (0, 0, 1024, 720)


class StartupGeometryTests(unittest.TestCase):
    def test_measured_normal_open_taskbar_overlap_is_removed(self):
        result = app.fitted_window_bounds(CLIENT, FRAME, OUTER, WORK, (680, 680))
        self.assertEqual(result, (78, 0, 1014, 727))
        self.assertEqual((result[2] - result[0] - 16, result[3] - result[1] - 39), (920, 688))
        self.assertEqual((result[0] + 7, result[1], result[2] - 7, result[3] - 7), (85, 0, 1007, 720))

    def test_sufficient_work_area_preserves_normal_size_and_position(self):
        self.assertEqual(app.fitted_window_bounds(CLIENT, FRAME, OUTER, (0, 0, 1280, 1024), (680, 680)), OUTER)

    def test_negative_monitor_origin_and_top_left_taskbar_are_respected(self):
        result = app.fitted_window_bounds(CLIENT, (-1290, -20, -368, 761), (-1297, -20, -361, 768),
                                         (-1240, 40, 0, 900), (680, 680))
        self.assertEqual(result, (-1247, 40, -311, 828))
        self.assertEqual((result[0] + 7, result[1]), (-1240, 40))

    def test_offscreen_opening_moves_without_shrinking_when_size_fits(self):
        result = app.fitted_window_bounds(CLIENT, (900, 600, 1822, 1381), (893, 600, 1829, 1388),
                                         (0, 0, 1280, 1024), (680, 680))
        self.assertEqual(result, (351, 243, 1287, 1031))
        self.assertEqual((result[2] - result[0], result[3] - result[1]), (936, 788))

    def test_too_small_work_area_does_not_reduce_minimum_usable_controls(self):
        result = app.fitted_window_bounds(CLIENT, FRAME, OUTER, (0, 0, 640, 640), (680, 680))
        self.assertEqual((result[2] - result[0] - 16, result[3] - result[1] - 39), (680, 680))
        self.assertEqual((result[0] + 7, result[1]), (0, 0))

    def native_fixture(self):
        user, dwm, api, gui, window = (Mock() for _ in range(5))
        con = Mock(MONITOR_DEFAULTTONEAREST=2, SWP_NOZORDER=4, SWP_NOACTIVATE=16)
        user.SetThreadDpiAwarenessContext.side_effect = [1234, 1234]
        user.GetAncestor.return_value = 5678
        api.GetMonitorInfo.return_value = {"Work": WORK}
        gui.GetClientRect.return_value = CLIENT
        gui.GetWindowRect.return_value = OUTER
        window.winfo_width.return_value = 920
        window.winfo_height.return_value = 749
        window.winfo_id.return_value = 8765
        window.minsize.return_value = (680, 680)

        def measure_frame(_hwnd, _attribute, rectangle, _size):
            ctypes.memmove(rectangle, (ctypes.c_int32 * 4)(*FRAME), 16)
            return 0

        dwm.DwmGetWindowAttribute.side_effect = measure_frame
        return user, dwm, api, gui, con, window

    def test_native_fit_preserves_dpi_context_focus_and_z_order(self):
        user, dwm, api, gui, con, window = self.native_fixture()
        with patch.object(app.sys, "platform", "win32"), patch.object(app.ctypes, "WinDLL", side_effect=[user, dwm], create=True), patch.dict("sys.modules", {"win32api": api, "win32gui": gui, "win32con": con}):
            app.fit_startup_window(window)
        gui.SetWindowPos.assert_called_once_with(5678, 0, 78, 0, 936, 727, 20)
        api.MonitorFromWindow.assert_called_once_with(5678, 2)
        user.SetThreadDpiAwarenessContext.assert_called_with(1234)
        self.assertEqual(window.update_idletasks.call_count, 2)
        window.geometry.assert_not_called()
        window.configure.assert_not_called()

    def test_native_failure_restores_original_dpi_context(self):
        user, dwm, api, gui, con, window = self.native_fixture()
        dwm.DwmGetWindowAttribute.side_effect = None
        dwm.DwmGetWindowAttribute.return_value = -1
        with patch.object(app.sys, "platform", "win32"), patch.object(app.ctypes, "WinDLL", side_effect=[user, dwm], create=True), patch.dict("sys.modules", {"win32api": api, "win32gui": gui, "win32con": con}):
            with self.assertRaisesRegex(OSError, "visible window frame"):
                app.fit_startup_window(window)
        user.SetThreadDpiAwarenessContext.assert_called_with(1234)
        gui.SetWindowPos.assert_not_called()

    def test_scaled_native_client_keeps_logical_minimum(self):
        user, dwm, api, gui, con, window = self.native_fixture()
        # Synthetic API/Tk scale mismatch exercises units; it is not physical DPI proof.
        gui.GetClientRect.return_value = (0, 0, 920, 750)
        window.winfo_width.return_value = 460
        window.winfo_height.return_value = 375
        with patch.object(app.sys, "platform", "win32"), patch.object(app.ctypes, "WinDLL", side_effect=[user, dwm], create=True), patch.dict("sys.modules", {"win32api": api, "win32gui": gui, "win32con": con}), patch.object(app, "fitted_window_bounds", return_value=OUTER) as fitted:
            app.fit_startup_window(window)
        self.assertEqual(fitted.call_args.args[-1], (1360, 1360))


if __name__ == "__main__":
    unittest.main()
