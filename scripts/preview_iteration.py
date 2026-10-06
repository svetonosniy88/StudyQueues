"""Render demo widgets for visual review; never read the personal data directory."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import argparse
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QEnterEvent, QImage, QPainter, QColor
from studyqueues.ui import Window, apply_theme
from studyqueues.session import Session
from studyqueues.state_store import StateStore


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", default="v026")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    profile = root / "local_data" / ("review-" + uuid4().hex) / "demo"
    output = root / "docs" / "screenshots"
    output.mkdir(exist_ok=True)
    app = QApplication([])
    apply_theme(app)

    def create(name):
        directory = profile / name
        directory.mkdir(parents=True)
        paths = {}
        for key in ("university", "self_development"):
            paths[key] = directory / f"{key}.md"
            shutil.copyfile(root / "examples" / f"{key}.md", paths[key])
        return directory, paths

    def capture(window, screen, suffix="", prepare=None):
        for width, height, ending in ((1160, 820, ""), (820, 650, "-narrow"), (1600, 1000, "-large")):
            window.resize(width, height)
            window.show()
            QTest.qWait(120)
            app.processEvents()
            if prepare:
                prepare()
                app.processEvents()
            assert window.grab().save(str(output / f"{screen}-{args.tag}{suffix}{ending}.png"))

    directory, paths = create("overview-work")
    window = Window(directory, paths, force_paths=True)
    capture(window, "overview")
    card = window.queue_cards["self_development"]
    QApplication.sendEvent(card, QEnterEvent(QPointF(5, 5), QPointF(5, 5), QPointF(card.mapToGlobal(card.rect().center()))))
    capture(window, "overview", "-hover")
    QApplication.sendEvent(card, QEvent(QEvent.Leave))
    window.change_duration(135)
    capture(window, "overview", "-ready")
    window.toggle_children("university", window.stores["university"].tasks[0].id)
    capture(window, "overview", "-expanded")
    window.add_child("university", window.stores["university"].tasks[0].id)
    window.add_inputs["university"].setText("Дополнительный подпункт")
    capture(window, "overview", "-inline-child")
    window.cancel_overview_draft("university")
    def show_drop():
        body = window.task_lists["university"]
        body.clear_drop()
        body.dragged_id = body.groups[0][0]
        body.begin_preview()
        body.locate_drop(body.groups[-1][1].geometry().bottomRight())
        QTest.qWait(210)
    capture(window, "overview", "-drop", show_drop)
    window.task_lists["university"].clear_drop()
    first = next(iter(window.task_edits.values()))
    first.setFocus(Qt.OtherFocusReason)
    capture(window, "overview", "-focus")
    first_row = window.task_rows[window.stores["university"].tasks[0].id]
    def show_menu():
        if getattr(window, "active_task_menu", None):
            window.active_task_menu.close()
        window.task_menu("university", window.stores["university"].tasks[0].id, first_row)
    capture(window, "overview", "-menu", show_menu)
    app.processEvents()
    window.active_task_menu.grab().save(str(output / f"task-menu-{args.tag}.png"))
    window.active_task_menu.close()
    capture(window, "overview", "-error", lambda: (
        first.setPlainText(""), first.request_save()))
    QTest.keyClick(first, Qt.Key_Escape)
    window.duration.setValue(135)
    window.start_session()
    capture(window, "work")
    first_child = next(iter(window.work_checkboxes.values()))
    first_child.click()
    QTest.qWait(260)
    def hover_child():
        row = list(window.work_checkboxes.values())[1]
        window.task_scroll.ensureWidgetVisible(row.parentWidget(), 0, 0)
        row.parentWidget().setAttribute(Qt.WA_UnderMouse, True)
        row.parentWidget().update()
    capture(window, "work", "-checked-hover", hover_child)
    window.toggle_pause()
    capture(window, "work", "-paused")
    window.toggle_pause()
    window.complete_button.click()
    QTest.qWait(260)
    window.back_button.click()
    capture(window, "work", "-completed")
    window.work_root_checkbox.click()
    capture(window, "work", "-reopened")
    window.show_work_draft(window.current_task.id)
    window.draft_editor.setPlainText("Дополнительный подпункт: записать вывод и проверить объяснение")
    capture(window, "work", "-inline-add", window.focus_work_draft)
    window.cancel_work_draft()
    editor = window.work_editors[window.current_task.id]
    editor.setFocus(Qt.OtherFocusReason)
    capture(window, "work", "-inline-edit")
    window.close()

    directory, paths = create("long")
    text = "ДЗ по теории вероятностей: разобрать независимость событий, решить задачи и проверить объяснение каждого шага решения"
    paths["university"].write_text("# Примеры\n- [ ] " + text + "\n" + "".join(
        f"    - [ ] № {i + 1}: длинный подпункт — записать ход решения, сформулировать вывод и проверить результат по определению\n" for i in range(12)), encoding="utf-8")
    window = Window(directory, paths, force_paths=True)
    capture(window, "overview", "-long")
    window.toggle_children("university", window.stores["university"].tasks[0].id)
    capture(window, "overview", "-long-expanded")
    window.duration.setValue(135)
    window.start_session()
    capture(window, "work", "-long")
    capture(window, "work", "-long-bottom", lambda: window.task_scroll.verticalScrollBar().setValue(window.task_scroll.verticalScrollBar().maximum()))
    window.close()

    directory, paths = create("empty")
    paths["university"].write_text("# Примеры\n", encoding="utf-8")
    window = Window(directory, paths, force_paths=True)
    capture(window, "overview", "-empty")
    window.close()

    directory, paths = create("stats")
    state = StateStore(directory)
    start = datetime.now().astimezone().replace(hour=9, minute=0, second=0, microsecond=0)
    for i, key in enumerate(("university", "self_development", "university")):
        session = Session(key, paths[key], 60)
        record = session.to_dict()
        begin = start + timedelta(hours=i)
        end = begin + timedelta(minutes=35 + 10 * i)
        record.update(started_at=begin.isoformat(), ended_at=end.isoformat(),
                      active_seconds=(end - begin).total_seconds(),
                      intervals=[{"start": begin.isoformat(), "end": end.isoformat(), "seconds": (end - begin).total_seconds()}],
                      favorite=i == 1,
                      completed={"demo-task": {"text": "Демонстрационная задача", "parent_id": None}})
        state.save_session(record)
    window = Window(directory, paths, force_paths=True)
    window.show_stats()
    capture(window, "stats")
    window.filter_history(True)
    capture(window, "stats", "-favorites")
    window.close()

    # A contact sheet complements individual, full-resolution state snapshots.
    states = (("overview", "Обзор"), ("work", "Работа"),
              ("work-inline-edit", "Редактирование"), ("work-inline-add", "Черновик"),
              ("overview-error", "Ошибка сохранения"), ("stats", "Результаты"))
    sheet = QImage(1184, 1356, QImage.Format_ARGB32)
    sheet.fill(QColor("#1e1f22"))
    painter = QPainter(sheet)
    painter.setPen(QColor("#dfe1e5"))
    for i, (name, title) in enumerate(states):
        screen, _, state = name.partition("-")
        suffix = "-" + state if state else ""
        snapshot = QImage(str(output / f"{screen}-{args.tag}{suffix}.png"))
        assert not snapshot.isNull()
        x, y = 12 + (i % 2) * 592, 12 + (i // 2) * 448
        painter.drawText(x, y + 18, title)
        painter.drawImage(x, y + 26, snapshot.scaled(580, 410, Qt.KeepAspectRatio, Qt.SmoothTransformation))
    painter.end()
    assert sheet.save(str(output / f"contact-{args.tag}.png"))


if __name__ == "__main__":
    main()
