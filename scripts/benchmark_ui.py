"""Repeatable offscreen benchmark using generated queues and a separate profile."""
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from pathlib import Path
import argparse
import json
import sys
from statistics import median
from time import perf_counter
from uuid import uuid4
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QCoreApplication, QEvent
from studyqueues.ui import Window, apply_theme
from studyqueues.session import Session

parser = argparse.ArgumentParser()
parser.add_argument("--output", default="docs/performance-v026.json")
parser.add_argument("--repeats", type=int, default=3)
args = parser.parse_args()
root = Path(__file__).resolve().parent.parent
profile = root / "local_data" / ("benchmark-" + uuid4().hex)
profile.mkdir(parents=True)
app = QApplication([])
apply_theme(app)


def settle():
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()


def measure(action):
    start = perf_counter()
    action()
    settle()
    return round((perf_counter() - start) * 1000, 1)


results = []
for count, children in ((25, 3), (100, 3), (250, 3), (1, 50)):
    opens, samples = [], []
    for repeat in range(args.repeats):
        directory = profile / f"{count}-{children}-{repeat}"
        directory.mkdir()
        paths = {}
        for key in ("university", "self_development"):
            paths[key] = directory / (key + ".md")
            lines = ["# Демонстрация\n"]
            for i in range(count):
                done = 'x' if i % 2 else ' '
                lines.append(f"- [{done}] Демонстрационная задача {i}: проверить решение и записать объяснение <!-- task:{key}-{i} -->\n")
                for j in range(children):
                    child_done = 'x' if children > 3 and j % 2 else done
                    lines.append(f"    - [{child_done}] Подпункт {j}: записать ход решения и вывод <!-- task:{key}-{i}-{j} -->\n")
            paths[key].write_text(''.join(lines), encoding='utf-8')
        start = perf_counter()
        window = Window(directory / "demo", paths, force_paths=True)
        window.show()
        settle()
        opens.append(round((perf_counter() - start) * 1000, 1))
        sample = {}
        sample['show_completed_ms'] = measure(lambda: window.show_done.setChecked(True))
        sample['hide_completed_ms'] = measure(lambda: window.show_done.setChecked(False))
        first = window.stores['university'].active[0]
        sample['expand_children_ms'] = measure(lambda: window.toggle_children('university', first.id))
        sample['collapse_children_ms'] = measure(lambda: window.toggle_children('university', first.id))
        sample['to_empty_stats_ms'] = measure(window.show_stats)
        sample['back_to_overview_ms'] = measure(window.show_overview)
        sample['cached_stats_ms'] = measure(window.show_stats)
        window.show_overview()
        window.duration.setValue(30)
        sample['start_work_ms'] = measure(window.start_session)
        sample['render_work_ms'] = measure(window.render_work)
        sample['add_work_task_ms'] = measure(lambda: window.add_task('university', 'Новая демонстрационная задача'))
        samples.append(sample)
        window.close()
        window.deleteLater()
        settle()
    record = {"roots_per_queue": count, "children_per_root": children,
              "total_items_both_queues": count * (children + 1) * 2,
              "open_ms": median(opens), **{key: median(s[key] for s in samples) for key in samples[0]},
              "raw_open_ms": opens, "raw_samples": samples}
    results.append(record)
    print(json.dumps({k: v for k, v in record.items() if not k.startswith('raw')}), flush=True)

# Long history: first construction and repeated cached page switches separately.
paths = {key: directory / (key + '.md') for key in ('university', 'self_development')}
window = Window(profile / 'history-demo', paths, force_paths=True)
records = []
for i in range(500):
    session = Session('university', paths['university'], 30)
    session.finish()
    records.append(session.to_dict())
window.state.write('sessions.json', {'sessions': records})
window.show()
settle()
history = {'sessions': len(records), 'first_stats_ms': measure(window.show_stats)}
cached = []
for _ in range(args.repeats):
    window.show_overview()
    settle()
    cached.append(measure(window.show_stats))
history['cached_stats_ms'] = median(cached)
window.close()
settle()
report = {'backend': 'Qt offscreen', 'repeats': args.repeats, 'queues': results, 'history': history}
(root / args.output).write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps(history), flush=True)
