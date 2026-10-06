"""Check native frame on an isolated demo. Does not automate the user's window."""
import os
os.environ["QT_QPA_PLATFORM"] = "windows"
from pathlib import Path
import ctypes
from ctypes import wintypes
import json
import sys
from uuid import uuid4
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from studyqueues.ui import Window, apply_theme
from studyqueues.native_window import FRAME_STYLE, HTCLIENT, HTCAPTION, HTTOPLEFT

root = Path(__file__).resolve().parent.parent
profile = root / "local_data" / ("native-check-" + uuid4().hex)
profile.mkdir(parents=True)
paths = {}
for key in ("university", "self_development"):
    paths[key] = profile / (key + ".md")
    paths[key].write_text("# Демо\n- [ ] Проверить окно\n", encoding="utf-8")
app = QApplication([])
apply_theme(app)
window = Window(profile / "demo", paths, force_paths=True)
window.setWindowTitle("StudyQueues — DEMO — оконная проверка")
window.show()
QTest.qWait(150)
native = window.native_frame
hwnd = int(window.winId())
style = native.user32.GetWindowLongPtrW(hwnd, -16)
assert style & FRAME_STYLE == FRAME_STYLE
user32 = native.user32
user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.SendMessageW.restype = ctypes.c_ssize_t


def hit_at(widget, pos):
    point = widget.mapToGlobal(pos)
    ratio = window.devicePixelRatioF()
    packed = ((round(point.y() * ratio) & 0xffff) << 16) | (round(point.x() * ratio) & 0xffff)
    return user32.SendMessageW(hwnd, 0x84, 0, packed)


assert hit_at(window.titlebar, QPoint(450, 25)) == HTCAPTION
assert hit_at(window.window_controls[1], QPoint(15, 15)) == HTCLIENT
assert hit_at(window, QPoint(1, 1)) == HTTOPLEFT
before = window.geometry()
QTest.mouseClick(window.window_controls[1], Qt.LeftButton)
QTest.qWait(200)
assert window.isMaximized()
maximum = window.geometry()
work_area = window.screen().availableGeometry()
assert maximum == work_area, (maximum, work_area)
assert hit_at(window.titlebar, QPoint(450, 2)) == HTCAPTION
QTest.mouseClick(window.window_controls[1], Qt.LeftButton)
QTest.qWait(200)
assert not window.isMaximized() and window.geometry() == before, (window.geometry(), before)
report = {"platform": app.platformName(), "native_frame_style": hex(style),
          "caption_hit_test": "HTCAPTION", "controls_hit_test": "HTCLIENT",
          "maximum_matches_work_area": True, "restore_matches_original": True,
          "dpi_ratio": window.devicePixelRatioF(), "screen": [work_area.width(), work_area.height()],
          "real_top_edge_drag_tested": False}
suffix = f"-scale{round(float(os.environ['QT_SCALE_FACTOR']) * 100)}" if 'QT_SCALE_FACTOR' in os.environ else ''
(root / "docs" / f"native-window-v025{suffix}.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report), flush=True)
window.close()
