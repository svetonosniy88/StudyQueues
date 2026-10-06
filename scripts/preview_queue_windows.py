"""Demo-only snapshots of collapsed queues and the complete working task."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import sys
from uuid import uuid4
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from studyqueues.ui import Window, apply_theme

root = Path(__file__).resolve().parent.parent
profile = root / 'local_data' / ('window-preview-' + uuid4().hex)
profile.mkdir(parents=True)
output = root / 'docs' / 'screenshots'
output.mkdir(parents=True, exist_ok=True)
app = QApplication([])
apply_theme(app)
paths = {}
for key in ('university', 'self_development'):
    lines = ['# Примеры\n']
    for i in range(60):
        done = 'x' if i < 20 else ' '
        lines.append(f'- [{done}] Задача {i + 1}: проверить решение и записать объяснение <!-- task:{key}-{i} -->\n')
        for j in range(50 if i == 20 else 2):
            lines.append(f'    - [{done}] Подпункт {j + 1}: оформить вычисления и проверить результат <!-- task:{key}-{i}-{j} -->\n')
    paths[key] = profile / (key + '.md')
    paths[key].write_text(''.join(lines), encoding='utf-8')
window = Window(profile / 'demo', paths, force_paths=True)
window.show()
suffix = f"-scale{round(float(os.environ['QT_SCALE_FACTOR']) * 100)}" if 'QT_SCALE_FACTOR' in os.environ else ''


def capture(name):
    for width, height, ending in ((1160, 820, ''), (820, 650, '-narrow')):
        window.resize(width, height)
        QTest.qWait(120)
        if name == 'work-all-children-bottom':
            window.task_scroll.verticalScrollBar().setValue(window.task_scroll.verticalScrollBar().maximum())
            app.processEvents()
        window.grab().save(str(output / f'{name}-v026{suffix}{ending}.png'))


capture('overview-pages')
window.toggle_children('university', 'university-20')
capture('overview-expanded')
window.show_done.setChecked(True)
capture('overview-pages-completed')
window.move_queue_window('university', 1)
capture('overview-pages-next')
window.show_done.setChecked(False)
window.duration.setValue(30)
window.start_session()
capture('work-all-children')
window.task_scroll.verticalScrollBar().setValue(window.task_scroll.verticalScrollBar().maximum())
capture('work-all-children-bottom')
window.close()
