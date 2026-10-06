"""Render the public README images using example queues and synthetic history."""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from datetime import datetime, timedelta
from pathlib import Path
import shutil
import sys
from uuid import uuid4

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from studyqueues.ui import Window, apply_theme
from studyqueues.session import Session
from studyqueues.state_store import StateStore

profile = root / 'local_data' / ('public-preview-' + uuid4().hex)
profile.mkdir(parents=True)
output = root / 'docs' / 'images'
output.mkdir(parents=True, exist_ok=True)
paths = {}
for key in ('university', 'self_development'):
    paths[key] = profile / (key + '.md')
    shutil.copyfile(root / 'examples' / (key + '.md'), paths[key])
app = QApplication([])
apply_theme(app)


def capture(window, name):
    window.resize(1160, 820)
    window.show()
    QTest.qWait(120)
    assert window.grab().save(str(output / (name + '.png')))


window = Window(profile / 'demo', paths, force_paths=True)
window.duration.setValue(75)
window.toggle_children('university', window.stores['university'].tasks[0].id)
capture(window, 'overview')
window.start_session()
capture(window, 'work')
window.close()

state = StateStore(profile / 'history-demo')
begin = datetime.now().astimezone().replace(hour=9, minute=0, second=0, microsecond=0)
for i, key in enumerate(('university', 'self_development', 'university')):
    session = Session(key, paths[key], 60)
    record = session.to_dict()
    start = begin + timedelta(hours=i)
    end = start + timedelta(minutes=35 + 10 * i)
    record.update(started_at=start.isoformat(), ended_at=end.isoformat(),
                  active_seconds=(end - start).total_seconds(),
                  intervals=[{'start': start.isoformat(), 'end': end.isoformat(), 'seconds': (end - start).total_seconds()}],
                  favorite=i == 1,
                  completed={'example-task': {'text': 'Демонстрационная задача', 'parent_id': None}})
    state.save_session(record)
window = Window(state.directory, paths, force_paths=True)
window.show_stats()
capture(window, 'results')
window.close()
print('Public screenshots created from examples and synthetic history.')
