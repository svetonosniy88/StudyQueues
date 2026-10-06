"""Keep a custom Qt title bar while letting Windows move, resize and snap it."""
import ctypes
import sys
from ctypes import wintypes
from PySide6.QtCore import QPoint, QAbstractNativeEventFilter
from PySide6.QtWidgets import QApplication

WM_NCCALCSIZE = 0x83
WM_NCHITTEST = 0x84
WM_GETMINMAXINFO = 0x24
HTCLIENT, HTCAPTION = 1, 2
HTLEFT, HTRIGHT, HTTOP, HTTOPLEFT, HTTOPRIGHT = 10, 11, 12, 13, 14
HTBOTTOM, HTBOTTOMLEFT, HTBOTTOMRIGHT = 15, 16, 17
FRAME_STYLE = 0x00C00000 | 0x00040000 | 0x00080000 | 0x00020000 | 0x00010000


class MINMAXINFO(ctypes.Structure):
    _fields_ = [(name, wintypes.POINT) for name in
                ("reserved", "max_size", "max_position", "min_track", "max_track")]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("monitor", wintypes.RECT),
                ("work", wintypes.RECT), ("flags", wintypes.DWORD)]


def hit_test(x, y, width, height, border, maximized, title, interactive=False):
    """Coordinates in logical Qt pixels, including negative monitor origins."""
    if not maximized:
        left, right = x < border, x >= width - border
        top, bottom = y < border, y >= height - border
        if top:
            return HTTOPLEFT if left else HTTOPRIGHT if right else HTTOP
        if bottom:
            return HTBOTTOMLEFT if left else HTBOTTOMRIGHT if right else HTBOTTOM
        if left or right:
            return HTLEFT if left else HTRIGHT
    return HTCAPTION if title and not interactive else HTCLIENT


class NativeFrame(QAbstractNativeEventFilter):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.enabled = sys.platform == "win32" and window.app_platform == "windows"
        self.installed = False
        if not self.enabled:
            return
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        self.user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        self.user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
        self.user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
        self.user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        self.user32.MonitorFromWindow.restype = wintypes.HMONITOR
        self.user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFO)]
        self.user32.ScreenToClient.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
        self.user32.IsZoomed.argtypes = [wintypes.HWND]
        self.user32.GetDpiForWindow.argtypes = [wintypes.HWND]
        self.user32.GetSystemMetricsForDpi.argtypes = [ctypes.c_int, wintypes.UINT]

    def install(self):
        if not self.enabled or self.installed:
            return
        hwnd = int(self.window.winId())
        self.hwnd = hwnd
        self.installed = True
        QApplication.instance().installNativeEventFilter(self)
        style = self.user32.GetWindowLongPtrW(hwnd, -16)
        self.user32.SetWindowLongPtrW(hwnd, -16, style | FRAME_STYLE)
        # Inform DWM of the native frame while WM_NCCALCSIZE keeps our title bar.
        self.user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0020 | 0x0001 | 0x0002 | 0x0004 | 0x0010)

    def uninstall(self):
        if self.installed:
            QApplication.instance().removeNativeEventFilter(self)
            self.installed = False

    def nativeEventFilter(self, event_type, address):
        result = self.event(address)
        return (False, 0) if result is None else (True, result)

    def event(self, address):
        if not self.enabled or not self.installed:
            return None
        msg = wintypes.MSG.from_address(int(address))
        if msg.hWnd != self.hwnd:
            return None
        if msg.message == WM_NCCALCSIZE and msg.wParam:
            if self.user32.IsZoomed(msg.hWnd):
                # Windows extends a maximized resize frame beyond the work area.
                # Keep that native frame, but draw our client inside its border.
                rect = wintypes.RECT.from_address(msg.lParam)
                dpi = self.user32.GetDpiForWindow(msg.hWnd)
                padding = self.user32.GetSystemMetricsForDpi(92, dpi)
                horizontal = self.user32.GetSystemMetricsForDpi(32, dpi) + padding
                vertical = self.user32.GetSystemMetricsForDpi(33, dpi) + padding
                rect.left += horizontal
                rect.right -= horizontal
                rect.top += vertical
                rect.bottom -= vertical
            return 0
        if msg.message == WM_GETMINMAXINFO:
            monitor = self.user32.MonitorFromWindow(msg.hWnd, 2)
            info = MONITORINFO()
            info.size = ctypes.sizeof(info)
            if self.user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                limits = MINMAXINFO.from_address(msg.lParam)
                limits.max_position.x = info.work.left - info.monitor.left
                limits.max_position.y = info.work.top - info.monitor.top
                limits.max_size.x = info.work.right - info.work.left
                limits.max_size.y = info.work.bottom - info.work.top
                ratio = self.window.devicePixelRatioF()
                limits.min_track.x = round(self.window.minimumWidth() * ratio)
                limits.min_track.y = round(self.window.minimumHeight() * ratio)
                return 0
        if msg.message == WM_NCHITTEST:
            # Signed 16-bit screen positions, not LOWORD/HIWORD unsigned values.
            point = wintypes.POINT(ctypes.c_short(msg.lParam & 0xffff).value,
                                   ctypes.c_short((msg.lParam >> 16) & 0xffff).value)
            self.user32.ScreenToClient(msg.hWnd, ctypes.byref(point))
            ratio = self.window.devicePixelRatioF()
            pos = QPoint(round(point.x / ratio), round(point.y / ratio))
            titlebar = self.window.titlebar
            title = titlebar.geometry().contains(pos)
            interactive = any(control.geometry().contains(control.parentWidget().mapFrom(self.window, pos))
                              for control in self.window.window_controls)
            return hit_test(pos.x(), pos.y(), self.window.width(), self.window.height(), 6,
                            self.window.isMaximized(), title, interactive)
        return None
