import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
import json
import sys
import subprocess
from pathlib import Path
import pytest
from PySide6.QtWidgets import QApplication, QLabel, QCheckBox, QInputDialog, QStyle, QStyleOptionButton
from PySide6.QtCore import Qt, QLockFile, QPoint, QPointF, QMimeData, QEvent
from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent, QWheelEvent, QMouseEvent, QEnterEvent
from PySide6.QtTest import QTest
from studyqueues.ui import Window, apply_theme
from studyqueues.queue_store import QueueStore, QueueError
from studyqueues.session import Session
from studyqueues.state_store import StateStore, StateError
from studyqueues.widgets import DragHandle, CapsuleScrollBar


@pytest.fixture(scope="module")
def app():
    app = QApplication.instance() or QApplication([])
    apply_theme(app)
    yield app


@pytest.fixture
def desktop(tmp_path, app, monkeypatch):
    paths = {}
    for key in ("university", "self_development"):
        path = tmp_path / f"{key}.md"
        path.write_text("# Очередь\n- [ ] Первая\n- [ ] Вторая\n", encoding="utf-8")
        paths[key] = path
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda *error: errors.append(error))
    window = Window(tmp_path / "data", paths)
    window.show()
    app.processEvents()
    window.duration.setValue(25)
    yield window, paths
    window.close()
    app.processEvents()
    assert not errors, [str(e[1]) for e in errors]


def wait(window):
    QTest.qWait(260)
    # Cloud runners may deliver animation frames later than the nominal duration.
    for _ in range(200):
        if not window.busy:
            break
        QTest.qWait(10)
    assert not window.busy, "Completion animation did not finish"
    QApplication.processEvents()


def test_overview_add_from_last_page_shows_new_root_first_and_failure_keeps_input(desktop, app):
    window, paths = desktop
    paths['university'].write_text('# Очередь\n' + ''.join(
        f'- [ ] Задача {i} <!-- task:q{i} -->\n' for i in range(45)), encoding='utf-8')
    window.reload()
    window.move_queue_window('university', 1)
    window.move_queue_window('university', 1)
    app.processEvents()
    scroll = window.queue_scrolls['university'].verticalScrollBar()
    scroll.setValue(scroll.maximum())
    window.add_inputs['university'].setText('Новая наверху')
    QTest.keyClick(window.add_inputs['university'], Qt.Key_Return)
    QTest.qWait(30)
    store = window.stores['university']
    assert store.tasks[0].text == 'Новая наверху'
    assert len(store.tasks) == 46 and store.tasks[1].id == 'q0'
    assert window.task_lists['university'].groups[0][0] == store.tasks[0].id
    assert len(window.task_lists['university'].groups) == 15
    assert scroll.value() == 0 and not window.add_inputs['university'].text()
    external = paths['university'].read_bytes() + b'\nExternal\n'
    paths['university'].write_bytes(external)
    window.add_inputs['university'].setText('Сохранить мой ввод')
    QTest.keyClick(window.add_inputs['university'], Qt.Key_Return)
    app.processEvents()
    assert window.add_inputs['university'].text() == 'Сохранить мой ввод'
    assert window.toast.property('error') and paths['university'].read_bytes() == external
    assert store.tasks[0].text == 'Новая наверху'


def test_overview_folds_are_lazy_remembered_across_pages_and_reset_on_launch(desktop, app):
    window, paths = desktop
    paths['university'].write_text('# Очередь\n' + ''.join(
        f'- [ ] Родитель {i} <!-- task:q{i} -->\n'
        f'    - [ ] Подпункт <!-- task:c{i} -->\n' for i in range(45)), encoding='utf-8')
    window.reload()
    app.processEvents()
    before = paths['university'].read_bytes()
    assert not any('c' + str(i) in window.task_rows for i in range(45))
    QTest.mouseClick(window.task_rows['q0'].disclosure, Qt.LeftButton)
    app.processEvents()
    assert window.task_rows['c0'].isVisible()
    editor = window.task_rows['c0'].editor
    editor.setFocus()
    editor.setPlainText('Изменённый подпункт')
    QTest.mouseClick(window.task_rows['q0'].disclosure, Qt.LeftButton)
    app.processEvents()
    assert window.stores['university'].by_id['c0'].text == 'Изменённый подпункт'
    assert 'c0' not in window.task_rows
    QTest.mouseClick(window.task_rows['q0'].disclosure, Qt.LeftButton)
    window.show_done.setChecked(True)
    window.refresh_overview()
    window.move_queue_window('university', 1)
    app.processEvents()
    assert 'q0' not in window.task_rows
    window.move_queue_window('university', -1)
    app.processEvents()
    assert window.task_rows['c0'].isVisible()
    saved = paths['university'].read_bytes()
    window.toggle_children('university', 'q0')
    assert paths['university'].read_bytes() == saved and saved != before
    window.toggle_children('university', 'q0')
    reopened = Window(window.state.directory, paths)
    reopened.show()
    app.processEvents()
    assert 'c0' not in reopened.task_rows
    assert reopened.task_rows['q0'].disclosure.kind == 'expand'
    reopened.close()


def test_fold_with_invalid_child_edit_stays_open(desktop, app):
    window, paths = desktop
    store = window.stores['university']
    root = store.tasks[0].id
    child = store.add('Подпункт', root)
    window.refresh_overview()
    window.toggle_children('university', root)
    app.processEvents()
    original = paths['university'].read_bytes()
    editor = window.task_rows[child].editor
    editor.setFocus()
    editor.setPlainText('')
    QTest.mouseClick(window.task_rows[root].disclosure, Qt.LeftButton)
    app.processEvents()
    assert child in window.task_rows and window.task_rows[child].isVisible()
    assert editor.property('error') and paths['university'].read_bytes() == original
    QTest.keyClick(editor, Qt.Key_Escape)


def test_drag_velocity_neutral_zone_progression_and_fractional_scroll(desktop, app):
    window, paths = desktop
    for i in range(20):
        window.stores['university'].add(f'Длинная задача {i}: ' + 'Текст ' * 18)
    window.refresh_overview()
    QTest.qWait(30)
    area = window.task_lists['university']
    height = area.scroll.viewport().height()
    area.origin_y = height / 2
    area.set_scroll_target(height / 2 + height / 9)
    assert area.scroll_target == 0
    area.set_scroll_target(height * 0.65)
    slow = area.scroll_target
    area.set_scroll_target(height - 1)
    assert 0 < slow < area.scroll_target <= 1500
    bar = area.scroll.verticalScrollBar()
    bar.setValue(bar.maximum() // 2)
    initial = bar.value()
    area.set_scroll_target(height * 0.35)
    assert area.scroll_target < 0
    for _ in range(30):
        area.last_tick -= 0.016
        area.scroll_edge()
    assert bar.value() < initial
    # A long delayed frame is capped, so resuming cannot jump the whole viewport.
    initial = bar.value()
    area.set_scroll_target(height + 100)
    area.last_tick -= 10
    area.scroll_edge()
    assert abs(bar.value() - initial) <= 75
    area.set_scroll_target(height / 2)
    area.stop_scroll()
    initial = bar.value()
    area.scroll_edge()
    assert bar.value() == initial and not area.edge_timer.isActive()
    assert window.queue_windows['university'].start == 0


def test_continuous_drag_moves_do_not_starve_autoscroll_timer(desktop, app):
    window, paths = desktop
    store = window.stores['university']
    for i in range(15):
        store.add('Длинная задача: ' + 'Подробности ' * 10)
    window.refresh_overview()
    QTest.qWait(30)
    area = window.task_lists['university']
    root = store.tasks[0].id
    area.dragged_id = root
    area.origin_y = area.scroll.viewport().height() / 2
    mime = QMimeData()
    mime.setData(area.MIME, root.encode())
    class Move(QDragMoveEvent):
        def source(self):
            return window
    for _ in range(15):
        position = area.mapFrom(area.scroll.viewport(), QPoint(20, area.scroll.viewport().height() - 1))
        area.dragMoveEvent(Move(position, Qt.MoveAction, mime, Qt.LeftButton, Qt.NoModifier))
        QTest.qWait(5)
    assert area.scroll.verticalScrollBar().value() > 0
    area.clear_drop()


@pytest.mark.parametrize('conflict', [False, True])
def test_drag_placeholder_moves_neighbours_and_restores_on_cancel_or_conflict(desktop, app, conflict):
    window, paths = desktop
    store = window.stores['university']
    for i in range(43):
        store.add(f'Задача {i}')
    window.refresh_overview()
    QTest.qWait(30)
    area = window.task_lists['university']
    ids = [ident for ident, _ in area.groups]
    original = {ident: group.geometry() for ident, group in area.groups}
    raw = paths['university'].read_bytes()
    area.dragged_id = ids[0]
    area.fingerprint = store.fingerprint
    area.begin_preview()
    area.locate_drop(QPoint(10, original[ids[2]].bottom() + 2))
    assert area.slot.isVisible() and area.slot.height() == original[ids[0]].height()
    assert area.rows.isEnabled() is False
    assert paths['university'].read_bytes() == raw
    neighbour = dict(area.groups)[ids[1]]
    QTest.qWait(65)
    assert original[ids[0]].top() < neighbour.y() < original[ids[1]].top()
    assert len(area.groups) == 15 and window.queue_windows['university'].start == 0
    if conflict:
        external = raw + b'\nExternal\n'
        paths['university'].write_bytes(external)
        area.commit_drop(ids[0], area.drop_before, area.fingerprint)
        assert window.toast.property('error') and paths['university'].read_bytes() == external
    else:
        area.clear_drop()
        assert paths['university'].read_bytes() == raw
    assert area.rows.isEnabled() and not area.slot.isVisible() and not area.edge_timer.isActive()
    assert {ident: group.geometry() for ident, group in area.groups} == original
    assert dict(area.groups)[ids[0]].graphicsEffect() is None


def test_drop_acknowledges_file_before_settling_animation_and_keeps_page(desktop, app):
    window, paths = desktop
    store = window.stores['university']
    for i in range(43):
        store.add(f'Задача {i}')
    window.refresh_overview()
    window.move_queue_window('university', 1)
    QTest.qWait(30)
    area = window.task_lists['university']
    ids = [ident for ident, _ in area.groups]
    source = dict(area.groups)[ids[0]]
    area.dragged_id = ids[0]
    area.fingerprint = store.fingerprint
    area.begin_preview()
    target = QPoint(20, dict(area.groups)[ids[-1]].geometry().bottom() + 10)
    area.locate_drop(target)
    area.hover_pos = area.mapTo(area.scroll.viewport(), target)
    QTest.qWait(220)
    area.commit_drop(ids[0], area.drop_before, area.fingerprint)
    persisted = QueueStore(paths['university'], window.state.backups)
    assert [t.id for t in persisted.tasks][15:30] == ids[1:] + ids[:1]
    assert [ident for ident, _ in area.groups] == ids[1:] + ids[:1]
    assert window.queue_windows['university'].start == 15
    assert area.motion.animationCount() > 0 and not area.rows.isEnabled()
    initial = source.y()
    QTest.qWait(65)
    assert source.y() < initial
    QTest.qWait(160)
    assert area.rows.isEnabled() and source.y() == max(g.y() for _, g in area.groups)


def delete_from_row_menu(window, row):
    QTest.mouseClick(row.menu_button, Qt.LeftButton)
    QApplication.processEvents()
    menu = window.active_task_menu
    action = next(a for a in menu.actions() if a.objectName() == 'deleteTaskAction')
    QTest.mouseClick(menu, Qt.LeftButton, pos=menu.actionGeometry(action).center())
    QApplication.processEvents()


def test_keyboard_edit_add_nested_move_work_undo_finish_reopen(desktop, app):
    window, paths = desktop
    first = window.stores["university"].tasks[0]
    editor = window.task_edits[first.id]
    editor.setFocus()
    editor.selectAll()
    QTest.keyClicks(editor, "Edited")
    QTest.keyClick(editor, Qt.Key_Return)
    app.processEvents()
    assert window.stores["university"].tasks[0].text == "Edited"
    input = window.add_inputs["university"]
    input.setText("Новая задача")
    QTest.keyClick(input, Qt.Key_Return)
    app.processEvents()
    store = window.stores["university"]
    parent_id = store.tasks[0].id
    window.add_task("university", "26", parent_id)
    window.add_task("university", "28", parent_id)
    window.move_task("university", parent_id, -1)
    window.move_task("university", parent_id, -1)
    assert store.active[0].id == parent_id
    window.duration.setValue(25)
    window.start_session()
    QTest.qWait(10)
    assert window.pages.currentWidget() == window.work and window.sidebar.isHidden()
    children = store.by_id[parent_id].children
    check = window.work_checkboxes[children[0].id]
    QTest.mouseClick(check, Qt.LeftButton, pos=QPoint(8, check.height() // 2))
    assert window.busy
    window.complete("university", children[1].id)  # Rapid second click is ignored.
    wait(window)
    assert len(window.work_checkboxes) == 2 and window.work_checkboxes[children[0].id].isChecked()
    assert window.session.counts == (0, 1)
    check = window.work_checkboxes[children[1].id]
    QTest.mouseClick(check, Qt.LeftButton, pos=QPoint(8, check.height() // 2))
    wait(window)
    assert window.current_task.text == "Edited"
    assert window.session.counts == (1, 2)
    QTest.keyClick(window, Qt.Key_Z, Qt.ControlModifier)
    assert window.current_task.id == parent_id and len(window.work_checkboxes) == 2
    assert not window.work_checkboxes[children[1].id].isChecked()
    assert window.session.counts == (0, 1)
    QTest.mouseClick(window.pause_button, Qt.LeftButton)
    assert not window.session.running
    window.extend(15)
    assert window.session.planned_seconds == 2400
    QTest.mouseClick(window.pause_button, Qt.LeftButton)
    assert window.session.running
    window.complete("university", parent_id)
    wait(window)
    window.finish_session()
    assert window.session is None and window.pages.currentWidget() == window.stats
    history = window.state.history()
    assert len(history) == 1 and len(history[0]["completed"]) == 3
    window.close()
    reopened = Window(window.state.directory, paths)
    assert reopened.recovery is None
    assert reopened.stores["university"].by_id[parent_id].done
    reopened.select_queue("self_development")
    reopened.duration.setValue(25)
    reopened.start_session()
    reopened.complete("self_development", reopened.current_task.id)
    wait(reopened)
    reopened.finish_session()
    assert len(reopened.state.history()) == 2
    reopened.close()


def test_external_edit_shows_error_then_reload_resets_undo(desktop):
    window, paths = desktop
    window.start_session()
    first = window.current_task.id
    window.complete("university", first)
    wait(window)
    with paths["university"].open("a", encoding="utf-8") as stream:
        stream.write("- [ ] Внешняя новая\n")
    window.undo()
    assert window.toast.property("error") and "Обнов" in window.toast.text()
    assert "Внешняя новая" in paths["university"].read_text(encoding="utf-8")
    window.reload()
    assert window.session.last_completion is None
    assert len(window.stores["university"].active) == 2


def test_write_failure_does_not_animate_or_advance(desktop, monkeypatch):
    window, paths = desktop
    window.start_session()
    first = window.current_task.id
    original = paths["university"].read_bytes()
    def fail(*args):
        raise QueueError("Нет доступа к очереди")
    monkeypatch.setattr(window.stores["university"], "_commit", fail)
    window.complete("university", first)
    assert window.current_task.id == first and not window.busy
    assert window.toast.property("error")
    assert paths["university"].read_bytes() == original
    assert window.session.counts == (0, 0)
    assert window.session.pending is None
    monkeypatch.undo()


def test_missing_queue_pauses_session_and_is_not_shown_as_finished(desktop):
    window, paths = desktop
    window.start_session()
    paths["university"].unlink()
    window.reload()
    assert not window.session.running
    assert not window.complete_button.isEnabled()
    assert "Не удалось прочитать" in window.task_title.text()
    window.toggle_pause()
    assert not window.session.running


def test_close_reopen_restore_paused(desktop):
    window, paths = desktop
    window.start_session()
    window.complete("university", window.current_task.id)
    wait(window)
    ident = window.session.id
    window.close()
    reopened = Window(window.state.directory, paths)
    assert reopened.recovery["id"] == ident
    reopened.restore_session()
    assert not reopened.session.running and reopened.session.last_completion is None
    assert reopened.session.counts == (1, 0)
    reopened.finish_session()
    assert len(reopened.state.history()) == 1
    reopened.close()


@pytest.mark.parametrize("applied", [False, True])
def test_recover_completion_between_markdown_and_checkpoint(desktop, applied):
    window, paths = desktop
    store = window.stores["university"]
    record = Session("university", store.path, 25).to_dict()
    action = store.completion_for(store.tasks[0].id)
    record["pending"] = {"kind": "complete", "changes": action.changes}
    window.state.write("active_session.json", {"session": record})
    if applied:
        store.restore(action, forward=True)
    reopened = Window(window.state.directory, paths)
    reopened.restore_session()
    assert reopened.session.counts == ((1, 0) if applied else (0, 0))
    assert reopened.session.pending is None and not reopened.session.running
    reopened.close()


def test_restore_finished_session_upserts_after_partial_final_save(desktop):
    window, paths = desktop
    session = Session("university", paths["university"], 25)
    session.finish()
    window.state.save_session(session.to_dict())
    window.state.write("active_session.json", {"session": session.to_dict()})
    reopened = Window(window.state.directory, paths)
    reopened.restore_session()
    assert len(reopened.state.history()) == 1 and reopened.recovery is None
    reopened.close()


def test_second_instance_cannot_lock_same_data(tmp_path):
    first = QLockFile(str(tmp_path / "application.lock"))
    second = QLockFile(str(tmp_path / "application.lock"))
    first.setStaleLockTime(0)
    second.setStaleLockTime(0)
    assert first.tryLock(0)
    assert not second.tryLock(0)
    first.unlock()
    assert second.tryLock(0)
    second.unlock()


def test_corrupt_snapshot_prevents_start_without_overwrite(tmp_path, app):
    path = tmp_path / "active_session.json"
    payload = '{"version":1,"session":{"queue":"unknown"}}'
    path.write_text(payload, encoding="utf-8")
    with pytest.raises(StateError):
        Window(tmp_path)
    assert path.read_text(encoding="utf-8") == payload


def test_long_title_is_not_clipped_and_refresh_hides_old_controls(desktop, app):
    window, paths = desktop
    store = window.stores["university"]
    text = "ДЗ по теории вероятностей: разобрать независимость событий, решить задачи № 26, № 28 и № 30 и проверить объяснение каждого шага решения"
    store.edit(store.tasks[0].id, text)
    window.resize(820, 650)
    window.start_session()
    wait(window)
    title = window.task_title
    assert title.height() >= title.heightForWidth(title.width())
    old_timer = window.timer_label
    window.render_work()
    assert old_timer.isHidden()


def test_duration_constructor_reset_manual_input_and_zero_start(desktop, app):
    window, paths = desktop
    # Stored minutes do not force the next launch to start at the old duration.
    fresh = Window(window.state.directory, paths)
    fresh.show()
    app.processEvents()
    assert fresh.duration.value() == 0 and not fresh.start_button.isEnabled()
    fresh.start_session()
    assert fresh.session is None
    QTest.mouseClick(fresh.duration_add_buttons[60], Qt.LeftButton)
    QTest.mouseClick(fresh.duration_add_buttons[60], Qt.LeftButton)
    QTest.mouseClick(fresh.duration_add_buttons[15], Qt.LeftButton)
    assert fresh.duration.value() == 135 and fresh.start_button.isEnabled()
    QTest.mouseClick(fresh.reset_duration, Qt.LeftButton)
    assert fresh.duration.value() == 0 and not fresh.start_button.isEnabled()
    fresh.duration.lineEdit().setFocus()
    fresh.duration.lineEdit().selectAll()
    QTest.keyClicks(fresh.duration.lineEdit(), "12")
    QTest.keyClick(fresh.duration.lineEdit(), Qt.Key_Return)
    QTest.mouseClick(fresh.duration_add_buttons[5], Qt.LeftButton)
    assert fresh.duration.value() == 17
    fresh.duration.lineEdit().clear()
    assert not fresh.start_button.isEnabled()
    fresh.start_session()
    assert fresh.session is None
    fresh.change_duration(17)
    assert fresh.start_button.isEnabled(), (fresh.duration.text(), fresh.duration.hasAcceptableInput(), fresh.duration.value())
    QTest.mouseClick(fresh.start_button, Qt.LeftButton)
    assert fresh.session.planned_seconds == 17 * 60
    fresh.close()


def test_wrapped_child_indicator_click_scroll_preserved_and_next_task_reset(desktop, app):
    window, paths = desktop
    store = window.stores["university"]
    root = store.tasks[0].id
    for i in range(16):
        store.add(f"Подпункт {i}: длинная русская формулировка, которая должна переноситься и оставаться доступной для нажатия по всей строке", root)
    window.start_session()
    wait(window)
    assert window.work_card.width() < window.work.width() * 0.65
    first = next(iter(window.work_checkboxes.values()))
    assert first.caption.height() >= first.caption.heightForWidth(first.caption.width())
    assert first.caption.height() > first.caption.fontMetrics().height()
    bar = window.task_scroll.verticalScrollBar()
    assert bar.maximum() > 0
    # Send an actual wheel event into the scroll viewport.
    viewport = window.task_scroll.viewport()
    wheel = QWheelEvent(QPointF(50, 50), QPointF(viewport.mapToGlobal(QPoint(50, 50))),
                        QPoint(), QPoint(0, -120), Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
    QApplication.sendEvent(viewport, wheel)
    assert bar.value() > 0
    bar.setValue(220)
    position = bar.value()
    timer_y = window.timer_label.mapTo(window.work, QPoint()).y()
    # Only the indicator performs completion; the text is now an editor.
    check = next(c for c in window.work_checkboxes.values()
                 if 0 <= c.mapTo(viewport, QPoint(0, c.height() // 2)).y() <= viewport.height())
    QTest.mouseClick(check, Qt.LeftButton, pos=QPoint(check.width() // 2, check.height() // 2))
    wait(window)
    assert window.session.counts == (0, 1)
    assert window.task_scroll.verticalScrollBar().value() == position
    assert window.timer_label.mapTo(window.work, QPoint()).y() == timer_y
    window.undo()
    wait(window)
    assert window.task_scroll.verticalScrollBar().value() == position
    window.complete("university", root)
    wait(window)
    assert window.current_task.id != root
    assert window.task_scroll.verticalScrollBar().value() == 0


def test_history_star_filter_remove_and_failure_do_not_change_queue(desktop, app, monkeypatch):
    window, paths = desktop
    window.start_session()
    window.complete("university", window.current_task.id)
    wait(window)
    window.finish_session()
    ident = window.state.history()[0]["id"]
    source = paths["university"].read_bytes()
    QTest.mouseClick(window.favorite_buttons[ident], Qt.LeftButton)
    assert window.state.history()[0]["favorite"] is True
    window.filter_history(True)
    assert list(window.history_rows) == [ident]
    window.favorite_session(ident, False)
    assert window.history_rows == {}
    window.filter_history(False)
    assert ident in window.history_rows
    original = window.state.write
    def fail(*args):
        raise StateError("Запись недоступна")
    monkeypatch.setattr(window.state, "write", fail)
    QTest.mouseClick(window.delete_buttons[ident], Qt.LeftButton)
    assert ident in window.history_rows and len(window.state.history()) == 1
    QTest.mouseClick(window.favorite_buttons[ident], Qt.LeftButton)
    assert not window.state.history()[0].get("favorite", False)
    assert window.favorite_buttons[ident].text() == "☆"
    monkeypatch.setattr(window.state, "write", original)
    QTest.mouseClick(window.delete_buttons[ident], Qt.LeftButton)
    assert window.state.history() == [] and window.history_rows == {}
    assert paths["university"].read_bytes() == source
    window.show_overview()
    assert len(window.task_lists["university"].groups) == 1


def test_history_contains_older_records_beyond_thirty_and_favorites_persist(desktop):
    window, paths = desktop
    records = []
    for i in range(35):
        session = Session("university", paths["university"], 25)
        session.finish()
        records.append(session.to_dict())
    records[0]["favorite"] = True
    window.state.write("sessions.json", {"sessions": records})
    window.show_stats()
    assert len(window.history_rows) == 35
    window.filter_history(True)
    assert list(window.history_rows) == [records[0]["id"]]
    reopened = Window(window.state.directory, paths)
    reopened.filter_history(True)
    assert list(reopened.history_rows) == [records[0]["id"]]
    reopened.close()


def test_drop_events_move_subtree_and_reject_cross_queue(desktop, app):
    window, paths = desktop
    store = window.stores["university"]
    root = store.tasks[0].id
    store.add("Подпункт", root)
    window.refresh_overview()
    app.processEvents()
    area = window.task_lists["university"]
    area.dragged_id = root
    area.fingerprint = store.fingerprint
    mime = QMimeData()
    mime.setData(area.MIME, root.encode("utf-8"))
    class Enter(QDragEnterEvent):
        def source(self):
            return window
    class Drop(QDropEvent):
        def source(self):
            return window
    # A drag from this queue cannot be inserted in the other queue.
    wrong = Enter(QPoint(10, 10), Qt.MoveAction, mime, Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(window.task_lists["self_development"], wrong)
    assert not wrong.isAccepted()
    target = QPoint(15, area.groups[-1][1].geometry().bottom() + 12)
    enter = Enter(target, Qt.MoveAction, mime, Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(area, enter)
    assert enter.isAccepted()
    drop = Drop(QPointF(target), Qt.MoveAction, mime, Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(area, drop)
    assert drop.isAccepted()
    app.processEvents()
    assert store.tasks[-1].id == root and store.tasks[-1].children[0].text == "Подпункт"
    assert QueueStore(paths["university"], window.state.backups).tasks[-1].id == root


def test_reorder_conflict_keeps_external_file_and_screen(desktop):
    window, paths = desktop
    store = window.stores["university"]
    ids = [t.id for t in store.tasks]
    external = paths["university"].read_text(encoding="utf-8") + "- [ ] Чужая задача\n"
    paths["university"].write_text(external, encoding="utf-8")
    window.reorder_task("university", ids[0], None, store.fingerprint)
    assert window.toast.property("error")
    assert [i for i, _ in window.task_lists["university"].groups] == ids
    assert paths["university"].read_text(encoding="utf-8") == external


def test_handle_starts_drag_with_preview_and_drop_scrolls_at_edge(desktop, app, monkeypatch):
    import studyqueues.widgets as widgets
    window, paths = desktop
    store = window.stores["university"]
    for i in range(12):
        store.add(f"Демонстрационная задача {i}")
    window.refresh_overview()
    QTest.qWait(30)
    app.processEvents()
    area = window.task_lists["university"]
    root = store.tasks[0].id
    handle = next(h for h in area.findChildren(DragHandle) if h.ident == root)
    called = []
    def native_drag(drag, action):
        called.append(drag.pixmap().hasAlphaChannel())
        assert drag.pixmap().deviceIndependentSize().width() == area.groups[0][1].width()
        assert drag.source() is window
        class Enter(QDragEnterEvent):
            def source(self):
                return drag.source()
        class Move(QDragMoveEvent):
            def source(self):
                return drag.source()
        class Drop(QDropEvent):
            def source(self):
                return drag.source()
        enter = Enter(QPoint(10, 10), action, drag.mimeData(), Qt.LeftButton, Qt.NoModifier)
        QApplication.sendEvent(area, enter)
        assert enter.isAccepted()
        edge = area.mapFrom(area.scroll.viewport(), QPoint(20, area.scroll.viewport().height() - 5))
        move = Move(edge, action, drag.mimeData(), Qt.LeftButton, Qt.NoModifier)
        QApplication.sendEvent(area, move)
        assert move.isAccepted() and area.edge_timer.isActive() and area.drop_y is not None
        assert area.scroll_target > 0, (edge.y(), move.position().y(), area.pos().y(), area.hover_pos.y(), area.scroll.viewport().height())
        QTest.qWait(40)
        area.scroll_edge()
        assert area.scroll.verticalScrollBar().value() > 0
        last = QPoint(10, area.groups[-1][1].geometry().bottom() + 10)
        drop = Drop(QPointF(last), action, drag.mimeData(), Qt.LeftButton, Qt.NoModifier)
        QApplication.sendEvent(area, drop)
        assert drop.isAccepted() and not area.edge_timer.isActive()
        return action
    # Only replace the OS drag loop, exercising the real handle, MIME, hover and drop code.
    monkeypatch.setattr(widgets.QDrag, "exec", native_drag)
    QTest.mousePress(handle, Qt.LeftButton)
    event = QMouseEvent(QEvent.MouseMove, QPointF(40, 20), QPointF(handle.mapToGlobal(QPoint(40, 20))),
                        Qt.NoButton, Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(handle, event)
    app.processEvents()
    assert called == [True]
    assert store.tasks[-1].id == root


def test_history_delete_blocked_until_partial_finish_is_saved(desktop, monkeypatch):
    window, paths = desktop
    window.start_session()
    original = window.state.write
    def fail_clear(name, data):
        if name == "active_session.json" and data.get("session") is None:
            raise StateError("Не удалось завершить сохранение")
        original(name, data)
    monkeypatch.setattr(window.state, "write", fail_clear)
    window.finish_session()
    ident = window.session.id
    assert len(window.state.history()) == 1
    window.delete_session(ident)
    assert len(window.state.history()) == 1
    monkeypatch.setattr(window.state, "write", original)
    window.finish_session()
    window.delete_session(ident)
    assert window.state.history() == []


def test_app_icon_has_small_and_large_windows_sizes(desktop):
    window, paths = desktop
    sizes = {size.width() for size in window.windowIcon().availableSizes()}
    assert {16, 32, 48, 256} <= sizes
    for size in (16, 32, 256):
        assert not window.windowIcon().pixmap(size, size).isNull()


@pytest.mark.parametrize("size", [(1160, 820), (820, 650)])
def test_launch_row_stays_aligned_without_overlap_when_resized(desktop, app, size):
    window, paths = desktop
    window.resize(*size)
    QTest.qWait(30)
    window.change_duration(480)
    app.processEvents()
    controls = [window.reset_duration, *window.duration_add_buttons.values(), window.duration, window.start_button]
    row = window.launch_bar
    rects = [control.geometry() for control in controls]
    assert {r.height() for r in rects} == {44}
    assert len({r.top() for r in rects}) == 1
    assert all(a.right() < b.left() for a, b in zip(rects, rects[1:]))
    assert rects[0].left() > window.launch_title.geometry().right()
    assert rects[-1].right() < row.width()
    assert window.launch_title.width() >= window.launch_title.fontMetrics().horizontalAdvance(window.launch_title.text())
    assert window.start_button.width() >= window.start_button.fontMetrics().horizontalAdvance(window.start_button.text()) + 16
    QTest.mouseClick(window.start_button, Qt.LeftButton)
    assert window.session.planned_seconds == 480 * 60


def test_select_stays_visible_without_hover_and_reserves_header_space(desktop, app):
    window, paths = desktop
    window.resize(820, 650)
    QTest.qWait(30)
    window.nav_overview.setFocus()
    app.processEvents()
    for key in ("university", "self_development"):
        card = window.queue_cards[key]
        control = window.queue_select_buttons[key]
        title_rect = window.queue_titles[key].geometry()
        control_rect = control.geometry()
        QApplication.sendEvent(card, QEnterEvent(QPointF(5, 5), QPointF(5, 5), QPointF(card.mapToGlobal(QPoint(5, 5)))))
        app.processEvents()
        assert control.isVisible() and control.graphicsEffect() is None
        assert control.isEnabled() == (key != window.selected)
        assert window.queue_titles[key].geometry() == title_rect and control.geometry() == control_rect
        QApplication.sendEvent(card, QEvent(QEvent.Leave))
        app.processEvents()
        assert control.isVisible() and control.graphicsEffect() is None
    other = window.queue_select_buttons["self_development"]
    other.setFocus(Qt.TabFocusReason)
    app.processEvents()
    assert other.isVisible()
    card = window.queue_cards["self_development"]
    QApplication.sendEvent(card, QEnterEvent(QPointF(5, 5), QPointF(5, 5), QPointF(card.mapToGlobal(QPoint(5, 5)))))
    QTest.mouseClick(other, Qt.LeftButton)
    app.processEvents()
    assert window.selected == "self_development"
    assert window.launch_title.text() == "Саморазвитие"
    assert not window.queue_select_buttons["self_development"].isEnabled()
    assert window.queue_select_buttons["university"].isEnabled()
    assert window.queue_select_buttons["self_development"].isVisible()


def test_work_frame_stays_fixed_and_last_wrapped_child_visible_at_scroll_end(desktop, app):
    window, paths = desktop
    store = window.stores["university"]
    root = store.tasks[0].id
    for i in range(12):
        store.add(f"№ {i}: длинная русская формулировка с переносами — записать ход решения, сформулировать вывод и проверить результат", root)
    window.start_session()
    for size in ((1160, 820), (820, 650), (1160, 820)):
        window.resize(*size)
        QTest.qWait(40)
        panel = window.work_panel
        fixed_rect = panel.geometry()
        timer_y = window.timer_label.mapTo(window.work, QPoint()).y()
        bar = window.task_scroll.verticalScrollBar()
        assert bar.maximum() > 0
        bar.setValue(bar.maximum())
        app.processEvents()
        last = list(window.work_editors.values())[-1]
        bottom = last.mapTo(window.task_scroll.viewport(), QPoint(0, last.height() - 1)).y()
        assert 0 < bottom <= window.task_scroll.viewport().height()
        assert panel.geometry() == fixed_rect
        assert window.timer_label.mapTo(window.work, QPoint()).y() == timer_y
        assert window.work_panel is not window.work_card


def test_capsule_scrollbar_drags_using_qt_and_is_used_on_all_pages(desktop, app):
    window, paths = desktop
    store = window.stores["university"]
    root = store.tasks[0].id
    for i in range(15):
        store.add(f"Подпункт {i}", root)
    window.refresh_overview()
    QTest.qWait(30)
    assert all(isinstance(s.verticalScrollBar(), CapsuleScrollBar) for s in window.queue_scrolls.values())
    window.start_session()
    QTest.qWait(40)
    bar = window.task_scroll.verticalScrollBar()
    assert isinstance(bar, CapsuleScrollBar) and bar.maximum() > 0
    point = bar.thumb_rect().center().toPoint()
    QTest.mousePress(bar, Qt.LeftButton, pos=point)
    assert bar.isSliderDown()
    moved = point + QPoint(0, 50)
    move = QMouseEvent(QEvent.MouseMove, QPointF(moved), QPointF(bar.mapToGlobal(moved)), Qt.NoButton, Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(bar, move)
    QTest.mouseRelease(bar, Qt.LeftButton, pos=moved)
    assert bar.value() > 0 and not bar.isSliderDown()
    window.finish_session()
    assert isinstance(window.stats_scroll.verticalScrollBar(), CapsuleScrollBar)


def test_edit_focus_keeps_group_geometry_and_saves_text_on_return(desktop, app):
    window, paths = desktop
    window.resize(820, 650)
    QTest.qWait(30)
    ident, group = window.task_lists["university"].groups[0]
    edit = window.task_edits[ident]
    before = group.geometry()
    edit.setFocus(Qt.OtherFocusReason)
    app.processEvents()
    assert group.geometry() == before and edit.hasFocus()
    edit.selectAll()
    QTest.keyClicks(edit, "Edited task")
    QTest.keyClick(edit, Qt.Key_Return)
    app.processEvents()
    assert window.stores["university"].by_id[ident].text == "Edited task"
    assert window.task_edits[ident] is edit and edit.hasFocus()
    assert window.task_rows[ident].parentWidget().objectName() == "taskGroup"


def test_start_snapshot_failure_keeps_overview_and_no_session(desktop, monkeypatch):
    window, paths = desktop
    original = window.state.write
    def fail(name, data):
        if name == "active_session.json":
            raise StateError("Не удалось сохранить снимок")
        original(name, data)
    monkeypatch.setattr(window.state, "write", fail)
    window.start_session()
    assert window.session is None and window.pages.currentWidget() == window.overview


def test_snapshot_failure_after_markdown_write_recovers_counts(desktop, monkeypatch):
    window, paths = desktop
    window.start_session()
    original = window.state.write
    calls = 0
    def fail_second(name, data):
        nonlocal calls
        if name == "active_session.json":
            calls += 1
            if calls == 2:
                raise StateError("Сбой после записи Markdown")
        original(name, data)
    monkeypatch.setattr(window.state, "write", fail_second)
    window.complete("university", window.current_task.id)
    assert window.session.counts == (1, 0) and window.current_task.text == "Вторая"
    # Simulate abrupt exit: construct another window from the durable pending record.
    reopened = Window(window.state.directory, paths)
    reopened.restore_session()
    assert reopened.session.counts == (1, 0) and reopened.current_task.text == "Вторая"
    reopened.close()


def test_demo_with_real_data_dir_cannot_change_personal_queue(tmp_path):
    personal = tmp_path / "personal.md"
    source = "- [ ] Настоящая задача пользователя\n"
    personal.write_text(source, encoding="utf-8")
    profile = tmp_path / "profile"
    state = StateStore(profile)
    state.write("settings.json", {"paths": {"university": str(personal)}, "minutes": 25})
    settings = (profile / "settings.json").read_bytes()
    result = subprocess.run([sys.executable, "-m", "studyqueues", "--demo", "--data-dir", str(profile), "--screen", "work", "--screenshot", str(tmp_path / "demo.png")], capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert personal.read_text(encoding="utf-8") == source
    assert (profile / "settings.json").read_bytes() == settings
    snapshot = StateStore(profile / "demo").read("active_session.json", {})
    assert Path(snapshot["session"]["path"]).parent == profile / "demo"


def test_overview_completed_checkbox_reopens_without_rewriting_history(desktop, app):
    window, paths = desktop
    window.start_session()
    ident = window.current_task.id
    window.complete('university', ident)
    wait(window)
    window.finish_session()
    history = (window.state.directory / 'sessions.json').read_bytes()
    window.show_overview()
    QTest.mouseClick(window.show_done, Qt.LeftButton)
    app.processEvents()
    check = window.task_edits[ident].parentWidget().findChild(QCheckBox)
    assert check.isEnabled() and check.isChecked()
    QTest.mouseClick(check, Qt.LeftButton)
    assert not window.stores['university'].by_id[ident].completed
    assert (window.state.directory / 'sessions.json').read_bytes() == history


def test_work_navigation_skip_back_reopen_and_recomplete_counts_once(desktop):
    window, paths = desktop
    store = window.stores['university']
    root = store.tasks[0].id
    window.add_task('university', 'Первый подпункт', root)
    window.add_task('university', 'Второй подпункт', root)
    window.start_session()
    original = paths['university'].read_bytes()
    QTest.mouseClick(window.forward_button, Qt.LeftButton)
    assert window.current_task.id != root and window.session.counts == (0, 0)
    assert paths['university'].read_bytes() == original
    QTest.mouseClick(window.back_button, Qt.LeftButton)
    assert window.current_task.id == root
    QTest.mouseClick(window.work_root_checkbox, Qt.LeftButton)
    wait(window)
    assert window.session.counts == (1, 2)
    QTest.mouseClick(window.back_button, Qt.LeftButton)
    assert window.work_root_checkbox.isChecked() and not window.complete_button.isEnabled()
    child = store.by_id[root].children[0].id
    QTest.mouseClick(window.work_checkboxes[child], Qt.LeftButton)
    assert not store.by_id[root].done and not store.by_id[child].done
    assert store.by_id[root].children[1].done and window.session.counts == (0, 1)
    QTest.mouseClick(window.complete_button, Qt.LeftButton)
    wait(window)
    QTest.mouseClick(window.back_button, Qt.LeftButton)
    QTest.mouseClick(window.work_root_checkbox, Qt.LeftButton)
    assert all(not t.done for t in [store.by_id[root], *store.by_id[root].children])
    assert window.session.counts == (0, 0)
    QTest.mouseClick(window.complete_button, Qt.LeftButton)
    wait(window)
    window.finish_session()
    assert len(window.state.history()[0]['completed']) == 3


@pytest.mark.parametrize('fail_phase', ['before', 'markdown', 'after'])
def test_reopen_snapshot_failures_restore_marks_and_counts(desktop, monkeypatch, fail_phase):
    window, paths = desktop
    window.start_session()
    root = window.current_task.id
    window.complete('university', root)
    wait(window)
    QTest.mouseClick(window.back_button, Qt.LeftButton)
    write = window.state.write
    commit = window.stores['university']._commit
    calls = 0
    def fail_write(name, data):
        nonlocal calls
        if name == 'active_session.json':
            calls += 1
            if (fail_phase == 'before' and calls == 1) or (fail_phase == 'after' and calls == 2):
                raise StateError('Сбой снимка')
        write(name, data)
    def fail_commit(lines):
        raise QueueError('Сбой Markdown')
    monkeypatch.setattr(window.state, 'write', fail_write)
    if fail_phase == 'markdown':
        monkeypatch.setattr(window.stores['university'], '_commit', fail_commit)
    window.reopen_task('university', root)
    assert window.toast.property('error')
    reopened = Window(window.state.directory, paths)
    reopened.restore_session()
    expected = fail_phase != 'after'
    assert reopened.stores['university'].by_id[root].done == expected
    assert reopened.session.counts == (int(expected), 0)
    assert reopened.session.pending is None
    reopened.close()
    monkeypatch.setattr(window.state, 'write', write)
    monkeypatch.setattr(window.stores['university'], '_commit', commit)


def test_work_edit_add_and_restore_browsed_task(desktop, monkeypatch):
    window, paths = desktop
    store = window.stores['university']
    root = store.tasks[0].id
    window.start_session()
    assert window.current_task.id == root
    def no_dialog(*args, **kwargs):
        raise AssertionError('Рабочий экран не должен открывать диалоги')
    monkeypatch.setattr(QInputDialog, 'getText', no_dialog)
    editor = window.work_editors[root]
    editor.setFocus()
    editor.setPlainText('Изменённая задача')
    QTest.keyClick(editor, Qt.Key_Return)
    assert store.by_id[root].text == 'Изменённая задача'
    QTest.mouseClick(window.add_child_button, Qt.LeftButton)
    QTest.qWait(20)
    window.draft_editor.setPlainText('Добавленный подпункт')
    QTest.keyClick(window.draft_editor, Qt.Key_Return)
    assert len(window.work_checkboxes) == 1
    QTest.mouseClick(window.add_work_button, Qt.LeftButton)
    QTest.qWait(20)
    window.draft_editor.setPlainText('Добавленная задача')
    QTest.keyClick(window.draft_editor, Qt.Key_Return)
    assert store.tasks[1].text == 'Добавленная задача'
    assert store.tasks[2].text == 'Вторая'
    QTest.mouseClick(window.forward_button, Qt.LeftButton)
    selected = window.current_task.id
    window.close()
    reopened = Window(window.state.directory, paths)
    reopened.restore_session()
    assert reopened.current_task.id == selected and not reopened.session.running
    assert len(reopened.stores['university'].tasks) == 3
    reopened.close()


def test_large_queue_window_fills_slots_pages_and_preserves_file(desktop, app):
    window, paths = desktop
    text = '# Очередь\n' + ''.join(f'- [ ] Задача {i} <!-- task:q{i} -->\n' for i in range(65))
    paths['university'].write_text(text, encoding='utf-8')
    window.reload()
    app.processEvents()
    body = window.task_lists['university']
    assert [ident for ident, _ in body.groups] == [f'q{i}' for i in range(15)]
    assert len(body.all_groups) == 15
    old_row = window.task_rows['q4']
    for ident in ('q0', 'q1', 'q2'):
        window.complete('university', ident)
    app.processEvents()
    assert [ident for ident, _ in body.groups] == [f'q{i}' for i in range(3, 18)]
    assert window.task_rows['q4'] is old_row
    window.move_queue_window('university', 1)
    app.processEvents()
    assert body.groups[0][0] == 'q18'
    assert len(body.all_groups) <= 30
    window.move_queue_window('university', -1)
    app.processEvents()
    assert body.groups[0][0] == 'q3'
    window.show_done.setChecked(True)
    app.processEvents()
    assert body.groups[0][0] == 'q0'
    QTest.mouseClick(window.task_rows['q0'].checkbox, Qt.LeftButton, pos=QPoint(10, 12))
    app.processEvents()
    assert not window.stores['university'].by_id['q0'].done
    assert len(window.stores['university'].tasks) == 65


def test_filter_and_cached_tab_switch_reuse_rows_and_controls(desktop, app, monkeypatch):
    window, _ = desktop
    store = window.stores['university']
    first = store.tasks[0].id
    window.complete('university', first)
    app.processEvents()
    row = window.task_rows[first]
    duration = window.duration
    window.show_done.setChecked(True)
    window.show_done.setChecked(False)
    assert window.task_rows[first] is row and window.duration is duration
    window.show_stats()
    stats_scroll = window.stats_scroll
    window.show_overview()
    monkeypatch.setattr(window.state, 'history', lambda: pytest.fail('Неизменённая история не перечитывается'))
    window.show_stats()
    assert window.stats_scroll is stats_scroll
    window.show_overview()
    assert window.duration is duration


def test_work_draft_switch_keeps_text_and_inserts_after_current_block(desktop, app):
    window, _ = desktop
    store = window.stores['university']
    first, second = store.tasks
    child = store.add('Старый подпункт', first.id)
    window.refresh_overview()
    window.start_session()
    original_rows = window.task_rows.copy()
    window.show_work_draft(first.id)
    app.processEvents()
    editor = window.draft_editor
    editor.setPlainText('Новая задача')
    window.show_work_draft()
    app.processEvents()
    assert window.draft_editor is editor and editor.toPlainText() == 'Новая задача'
    assert window.work_draft['parent_id'] is None and editor.placeholderText() == 'Новая задача…'
    window.show_work_draft(first.id)
    app.processEvents()
    assert editor.placeholderText() == 'Новый подпункт…' and window.work_draft['text'] == 'Новая задача'
    window.show_work_draft()
    window.save_work_draft()
    app.processEvents()
    assert [task.text for task in store.tasks] == ['Первая', 'Новая задача', 'Вторая']
    assert store.tasks[0].children[0].id == child and store.tasks[-1].id == second.id
    assert window.current_task.id == first.id
    assert window.task_rows == original_rows and window.overview_dirty
    window.finish_session()
    window.show_overview()
    app.processEvents()
    assert store.tasks[1].id in window.task_rows


def test_long_children_are_lazy_and_bounded_in_overview_but_all_visible_in_work(desktop, app):
    window, paths = desktop
    text = ('# Очередь\n- [ ] Родитель <!-- task:root -->\n' +
            ''.join(f'    - [ ] Подпункт {i} <!-- task:c{i} -->\n' for i in range(85)))
    paths['university'].write_text(text, encoding='utf-8')
    window.reload()
    app.processEvents()
    assert len(window.task_rows) == 3  # collapsed: only roots, no hidden child editors
    QTest.mouseClick(window.task_rows['root'].disclosure, Qt.LeftButton)
    app.processEvents()
    assert len(window.task_rows) == 18  # expanded: parent + 15 children + second queue's two roots
    window.complete('university', 'c0')
    app.processEvents()
    assert 'c15' in window.task_rows
    group = window.task_lists['university'].all_groups['root']
    next_children = group.child_pagers[0][2]
    window.queue_scrolls['university'].ensureWidgetVisible(next_children)
    app.processEvents()
    QTest.mouseClick(next_children, Qt.LeftButton)
    app.processEvents()
    assert 'c16' in window.task_rows and 'c31' not in window.task_rows
    window.move_child_window('university', 'root', -1)
    window.start_session()
    app.processEvents()
    assert len(window.work_checkboxes) == 85
    assert not hasattr(window.work_card, 'child_pagers')
    assert all(row.disclosure is None for row in window.work_rows.values())
    window.complete('university', 'root')
    wait(window)
    store = window.stores['university']
    assert all(child.done for child in store.by_id['root'].children)
    assert window.session.counts == (1, 84)  # c0 was completed before this session
    window.browse_task(-1)
    app.processEvents()
    assert len(window.work_checkboxes) == 85
    assert all(check.isChecked() for check in window.work_checkboxes.values())


def test_drop_after_last_rendered_block_does_not_move_to_end_of_file(desktop, app):
    window, paths = desktop
    text = '# Очередь\n' + ''.join(f'- [ ] Задача {i} <!-- task:q{i} -->\n' for i in range(45))
    paths['university'].write_text(text, encoding='utf-8')
    window.reload()
    app.processEvents()
    body = window.task_lists['university']
    body.locate_drop(QPoint(10, body.groups[-1][1].geometry().bottom() + 10))
    assert body.drop_before == 'q15'
    window.reorder_task('university', 'q0', body.drop_before, window.stores['university'].fingerprint)
    assert [task.id for task in window.stores['university'].tasks][:16] == [*[f'q{i}' for i in range(1, 15)], 'q0', 'q15']


def test_browse_before_first_markdown_write_restores_position_without_mutation(desktop):
    window, paths = desktop
    original = paths['university'].read_bytes()
    window.start_session()
    window.browse_task(1)
    assert window.current_task.text == 'Вторая'
    window.reload()
    assert window.current_task.text == 'Вторая'
    window.close()
    reopened = Window(window.state.directory, paths)
    reopened.restore_session()
    assert reopened.current_task.text == 'Вторая'
    assert paths['university'].read_bytes() == original
    assert reopened.session.counts == (0, 0)
    reopened.close()


def test_adding_child_to_completed_task_opens_parent_and_removes_its_credit(desktop):
    window, paths = desktop
    window.start_session()
    root = window.current_task.id
    window.complete('university', root)
    wait(window)
    window.browse_task(-1)
    window.add_task('university', 'Дополнительный пункт', root)
    assert not window.current_task.completed and window.session.counts == (0, 0)
    assert len(window.work_checkboxes) == 1 and window.complete_button.isEnabled()


def test_work_checkbox_padding_and_footer_alignment(desktop, app):
    window, paths = desktop
    window.add_task('university', 'Подпункт', window.stores['university'].tasks[0].id)
    window.start_session()
    QTest.qWait(40)
    checkbox = next(iter(window.work_checkboxes.values()))
    option = QStyleOptionButton()
    checkbox.initStyleOption(option)
    indicator = checkbox.style().subElementRect(QStyle.SE_CheckBoxIndicator, option, checkbox)
    frame = checkbox.parentWidget()
    point = checkbox.mapTo(frame, indicator.topLeft())
    assert point.x() >= 8 and point.y() >= 8
    assert checkbox.caption.geometry().left() >= checkbox.geometry().right() + 8
    root = window.work_root_checkbox
    assert checkbox.mapTo(window.work_card, QPoint()).x() > root.mapTo(window.work_card, QPoint()).x() + 24
    complete_rect = window.complete_button.geometry()
    assert complete_rect.left() == window.pause_button.geometry().left()
    assert complete_rect.right() == window.finish_button.geometry().right()
    geometry = window.action_block.geometry()
    pause_geometry = window.pause_button.geometry()
    QTest.mouseClick(window.pause_button, Qt.LeftButton)
    app.processEvents()
    assert window.action_block.geometry() == geometry and window.pause_button.geometry() == pause_geometry
    assert window.pause_button.text() == 'Продолжить'
    assert not window.work.findChildren(QLabel, 'chip')
    assert window.finish_button.mapTo(window.work, QPoint()).y() > window.timer_label.mapTo(window.work, QPoint()).y()
    assert not any(b.text() == 'Отменить' for b in window.work.findChildren(type(window.complete_button)))


def test_work_text_click_edits_without_completion_and_focus_loss_saves(desktop, app):
    window, paths = desktop
    window.start_session()
    ident = window.current_task.id
    editor = window.work_editors[ident]
    QTest.mouseClick(editor.viewport(), Qt.LeftButton)
    assert editor.hasFocus() and window.session.counts == (0, 0)
    editor.selectAll()
    QTest.keyClicks(editor, 'New text')
    QTest.mouseClick(window.pause_button, Qt.LeftButton)
    app.processEvents()
    assert window.stores['university'].by_id[ident].text == 'New text'
    assert window.session.counts == (0, 0) and not window.work_root_checkbox.isChecked()
    editor.setFocus()
    editor.selectAll()
    QTest.keyClicks(editor, 'Saved before navigation')
    QTest.mouseClick(window.forward_button, Qt.LeftButton)
    assert window.stores['university'].by_id[ident].text == 'Saved before navigation'
    assert window.session.counts == (0, 0)


def test_work_draft_cancel_and_invalid_input_do_not_write_markdown(desktop, app):
    window, paths = desktop
    window.start_session()
    original = paths['university'].read_bytes()
    QTest.mouseClick(window.add_child_button, Qt.LeftButton)
    QTest.qWait(20)
    assert window.draft_editor.hasFocus()
    window.draft_editor.setPlainText('Черновик')
    QTest.keyClick(window.draft_editor, Qt.Key_Escape)
    assert window.work_draft is None and paths['university'].read_bytes() == original
    QTest.mouseClick(window.add_work_button, Qt.LeftButton)
    QTest.qWait(20)
    QTest.keyClick(window.draft_editor, Qt.Key_Return)
    assert window.work_draft is not None and window.toast.property('error')
    assert paths['university'].read_bytes() == original
    QTest.keyClick(window.draft_editor, Qt.Key_Escape)


def test_work_delete_empty_child_and_parent_updates_current_credit(desktop):
    window, paths = desktop
    store = window.stores['university']
    root = store.tasks[0].id
    window.add_task('university', 'Первый подпункт', root)
    window.add_task('university', 'Второй подпункт', root)
    child = store.by_id[root].children[0].id
    window.start_session()
    window.complete('university', root)
    wait(window)
    window.browse_task(-1)
    assert window.session.counts == (1, 2)
    window.work_editors[child].setFocus()
    window.work_editors[child].setPlainText('')
    delete_from_row_menu(window, window.work_rows[child])
    assert child not in store.by_id and window.session.counts == (1, 1)
    remaining_child = store.by_id[root].children[0].id
    window.work_editors[remaining_child].setFocus()
    window.work_editors[remaining_child].setPlainText('')
    delete_from_row_menu(window, window.work_rows[root])
    assert root not in store.by_id and remaining_child not in store.by_id
    assert window.session.counts == (0, 0) and window.current_task.text == 'Вторая'


@pytest.mark.parametrize('failure', ['markdown', 'snapshot'])
def test_delete_write_failure_and_post_write_recovery(desktop, monkeypatch, failure):
    window, paths = desktop
    window.start_session()
    root = window.current_task.id
    window.complete('university', root)
    wait(window)
    window.browse_task(-1)
    write = window.state.write
    commit = window.stores['university']._commit
    def fail(*args, **kwargs):
        raise StateError('Сбой записи')
    if failure == 'markdown':
        monkeypatch.setattr(window.stores['university'], '_commit', fail)
    else:
        monkeypatch.setattr(window.state, 'write', fail)
    window.delete_task('university', root)
    assert window.toast.property('error')
    reopened = Window(window.state.directory, paths)
    reopened.restore_session()
    remains = failure == 'markdown'
    assert (root in reopened.stores['university'].by_id) == remains
    assert reopened.session.counts == (int(remains), 0)
    reopened.close()
    monkeypatch.setattr(window.state, 'write', write)
    monkeypatch.setattr(window.stores['university'], '_commit', commit)


def test_overview_delete_after_clearing_text_keeps_old_history(desktop, app):
    window, paths = desktop
    window.start_session()
    root = window.current_task.id
    window.complete('university', root)
    wait(window)
    window.finish_session()
    history = (window.state.directory / 'sessions.json').read_bytes()
    window.show_overview()
    QTest.mouseClick(window.show_done, Qt.LeftButton)
    app.processEvents()
    edit = window.task_edits[root]
    edit.setFocus()
    edit.clear()
    delete_from_row_menu(window, window.task_rows[root])
    app.processEvents()
    assert root not in window.stores['university'].by_id
    assert (window.state.directory / 'sessions.json').read_bytes() == history


def test_inline_write_failure_retains_text_and_native_undo_stays_in_editor(desktop, monkeypatch):
    window, paths = desktop
    window.start_session()
    root = window.current_task.id
    editor = window.work_editors[root]
    editor.setFocus()
    editor.selectAll()
    QTest.keyClicks(editor, 'Typing')
    QTest.keyClick(editor, Qt.Key_Z, Qt.ControlModifier)
    assert editor.toPlainText() != 'Typing' and window.session.counts == (0, 0)
    editor.setPlainText('Keep this draft')
    original = window.stores['university']._commit
    def fail(*args):
        raise QueueError('Нет доступа')
    monkeypatch.setattr(window.stores['university'], '_commit', fail)
    QTest.keyClick(editor, Qt.Key_Return)
    assert editor.toPlainText() == 'Keep this draft'
    assert window.stores['university'].by_id[root].text == 'Первая'
    window.browse_task(1)
    assert window.current_task.id == root
    monkeypatch.setattr(window.stores['university'], '_commit', original)
    QTest.keyClick(editor, Qt.Key_Escape)


@pytest.mark.parametrize('work', [False, True])
@pytest.mark.parametrize('width', [820, 1160])
def test_shared_row_first_line_alignment_padding_and_wrapping(desktop, app, work, width):
    window, paths = desktop
    store = window.stores['university']
    root = store.tasks[0].id
    window.add_task('university', 'Длинный подпункт: ' + 'проверить объяснение решения ' * 8, root)
    child = store.by_id[root].children[0].id
    window.resize(width, 650)
    if work:
        window.start_session()
    QTest.qWait(50)
    rows = window.work_rows if work else window.task_rows
    root_row, child_row = rows[root], rows[child]
    assert child_row.editor.height() > root_row.editor.height()
    for row in (root_row, child_row):
        option = QStyleOptionButton()
        row.checkbox.initStyleOption(option)
        indicator = row.checkbox.style().subElementRect(QStyle.SE_CheckBoxIndicator, option, row.checkbox)
        indicator_center = row.checkbox.mapTo(row, indicator.center()).y()
        menu_center = row.menu_button.mapTo(row, row.menu_button.rect().center()).y()
        assert abs(indicator_center - menu_center) <= 1
        if row.prefix:
            prefix_center = row.prefix.mapTo(row, row.prefix.rect().center()).y()
            assert abs(indicator_center - prefix_center) <= 1
        if row.disclosure:
            disclosure_center = row.disclosure.mapTo(row, row.disclosure.rect().center()).y()
            assert abs(indicator_center - disclosure_center) <= 1
        # Uniform space around the painted indicator, not just around its QWidget.
        point = row.checkbox.mapTo(row.line, indicator.topLeft())
        assert abs(point.x() - point.y()) <= 1
        position = row.editor.pos()
        row.editor.setFocus(Qt.OtherFocusReason)
        app.processEvents()
        assert row.editor.pos() == position
    root_x = root_row.checkbox.mapTo(root_row, QPoint(0, 0)).x()
    child_x = child_row.checkbox.mapTo(child_row, QPoint(0, 0)).x()
    assert child_x - root_x == 32


def test_selection_preserves_editors_cursor_and_scroll(desktop, app):
    window, paths = desktop
    for i in range(8):
        window.add_task('university', 'Задача ' + str(i))
    scroll = window.queue_scrolls['university'].verticalScrollBar()
    scroll.setValue(scroll.maximum())
    row = window.task_rows[window.stores['university'].tasks[-1].id]
    row.editor.setFocus()
    QTest.keyClick(row.editor, Qt.Key_End)
    app.processEvents()
    before = scroll.value()
    cursor = row.editor.textCursor().position()
    window.select_queue('self_development')
    app.processEvents()
    assert row.editor in window.task_edits.values()
    assert row.editor.textCursor().position() == cursor
    assert scroll.value() == before
    assert window.launch_title.text() == 'Саморазвитие'


def test_overview_save_failure_keeps_draft_until_retry(desktop, monkeypatch, app):
    window, paths = desktop
    store = window.stores['university']
    root = store.tasks[0].id
    editor = window.task_edits[root]
    original = store._commit
    def fail(*args):
        raise QueueError('Нет доступа к файлу')
    monkeypatch.setattr(store, '_commit', fail)
    editor.setFocus()
    editor.setPlainText('Сохранить этот ввод')
    QTest.keyClick(editor, Qt.Key_Return)
    assert editor.toPlainText() == 'Сохранить этот ввод' and editor.property('error')
    assert store.by_id[root].text == 'Первая'
    window.start_session()
    assert window.session is None and window.task_edits[root] is editor
    monkeypatch.setattr(store, '_commit', original)
    QTest.keyClick(editor, Qt.Key_Return)
    app.processEvents()
    assert store.by_id[root].text == 'Сохранить этот ввод'
    assert window.task_edits[root] is editor and editor.hasFocus() and not editor.property('error')


def test_task_menus_are_explicit_and_dismiss_without_moving_text(desktop, app):
    window, paths = desktop
    store = window.stores['university']
    root = store.tasks[0].id
    window.add_task('university', 'Подпункт', root)
    app.processEvents()
    child = store.by_id[root].children[0].id
    for ident, labels in ((root, ['Выбрать для занятия', 'Добавить подпункт', 'Удалить задачу и подпункты']), (child, ['Удалить подпункт'])):
        row = window.task_rows[ident]
        before = row.editor.geometry()
        QTest.mouseClick(row.menu_button, Qt.LeftButton)
        app.processEvents()
        menu = window.active_task_menu
        assert [a.text() for a in menu.actions() if not a.isSeparator()] == labels
        QTest.keyClick(menu, Qt.Key_Escape)
        app.processEvents()
        assert row.editor.geometry() == before and ident in store.by_id


def test_overview_child_is_added_inline_and_draft_survives_refresh(desktop, app):
    window, paths = desktop
    store = window.stores['university']
    root = store.tasks[0].id
    QTest.mouseClick(window.task_rows[root].menu_button, Qt.LeftButton)
    app.processEvents()
    menu = window.active_task_menu
    action = next(a for a in menu.actions() if a.text() == 'Добавить подпункт')
    QTest.mouseClick(menu, Qt.LeftButton, pos=menu.actionGeometry(action).center())
    app.processEvents()
    assert window.add_inputs['university'].hasFocus()
    assert window.overview_add_parents['university'] == root
    window.add_inputs['university'].setText('Новый подпункт')
    QTest.mouseClick(window.show_done, Qt.LeftButton)
    app.processEvents()
    assert window.add_inputs['university'].text() == 'Новый подпункт'
    QTest.keyClick(window.add_inputs['university'], Qt.Key_Return)
    app.processEvents()
    assert store.by_id[root].children[0].text == 'Новый подпункт'
    assert not window.add_inputs['university'].text()
    assert 'university' not in window.overview_add_parents


def test_overview_inline_child_cancel_does_not_write(desktop, app):
    window, paths = desktop
    root = window.stores['university'].tasks[0].id
    original = paths['university'].read_bytes()
    window.add_child('university', root)
    window.add_inputs['university'].setText('Не сохранять')
    QTest.keyClick(window.add_inputs['university'], Qt.Key_Escape)
    app.processEvents()
    assert paths['university'].read_bytes() == original
    assert not window.add_inputs['university'].text()
    assert window.add_cancels['university'].isHidden()


def test_start_selection_from_menu_keeps_bounded_page_and_time_setup(desktop, app):
    window, paths = desktop
    key = 'self_development'
    paths[key].write_text('# Очередь\n' + ''.join(
        f'- [ ] Задача {i} <!-- task:q{i} -->\n' for i in range(45)), encoding='utf-8')
    window.reload()
    window.move_queue_window(key, 1)
    window.duration.setValue(0)
    original = paths[key].read_bytes()
    row = window.task_rows['q17']
    QTest.mouseClick(row.menu_button, Qt.LeftButton)
    app.processEvents()
    menu = window.active_task_menu
    action = next(a for a in menu.actions() if a.objectName() == 'startTaskAction')
    assert action.text() == 'Выбрать для занятия' and action.isEnabled()
    QTest.mouseClick(menu, Qt.LeftButton, pos=menu.actionGeometry(action).center())
    app.processEvents()
    assert window.selected == key and window.start_task['id'] == 'q17'
    assert window.session is None and not window.start_button.isEnabled()
    assert paths[key].read_bytes() == original
    window.start_session()
    assert window.session is None and window.start_task['id'] == 'q17'
    group = window.task_lists[key].all_groups['q17']
    assert group.property('startSelected') and group.start_hint.isVisible()
    assert len(window.task_lists[key].groups) == 15
    window.move_queue_window(key, 1)
    assert 'q17' not in window.task_lists[key].all_groups
    window.move_queue_window(key, -1)
    window.refresh_overview()
    assert window.task_lists[key].all_groups['q17'].property('startSelected')
    assert len(window.task_lists[key].groups) == 15
    window.duration.setValue(20)
    window.start_session()
    assert window.current_task.id == 'q17' and window.session.queue == key
    assert window.start_task is None
    snapshot = window.state.read('active_session.json', {})['session']
    assert snapshot['view_task_id'] == 'q17' and not snapshot['at_queue_end']
    assert paths[key].read_bytes() == original
    window.complete(key, 'q17')
    wait(window)
    assert window.current_task.id == 'q18' and window.session.counts == (1, 0)
    assert not window.stores[key].by_id['q0'].done


def test_start_selection_clear_switch_and_completed_child_menu(desktop, app):
    window, paths = desktop
    key = 'university'
    root = window.stores[key].tasks[1].id
    window.choose_start_task(key, root)
    row = window.task_rows[root]
    window.task_menu(key, root, row)
    menu = window.active_task_menu
    action = next(a for a in menu.actions() if a.objectName() == 'startTaskAction')
    assert action.text() == 'Сбросить выбор'
    action.trigger()
    menu.close()
    app.processEvents()
    assert window.start_task is None
    assert not window.task_lists[key].all_groups[root].property('startSelected')
    window.choose_start_task(key, root)
    window.select_queue('self_development')
    assert window.start_task is None
    window.add_task(key, 'Подпункт', root)
    child = window.stores[key].by_id[root].children[0].id
    window.choose_start_task(key, child)
    assert window.start_task is None
    window.task_menu(key, child, window.task_rows[child])
    assert not any(a.objectName() == 'startTaskAction' for a in window.active_task_menu.actions())
    window.active_task_menu.close()
    window.set_task_checked(key, root, True)
    window.show_done.setChecked(True)
    window.task_menu(key, root, window.task_rows[root])
    action = next(a for a in window.active_task_menu.actions() if a.objectName() == 'startTaskAction')
    assert not action.isEnabled()
    window.active_task_menu.close()


@pytest.mark.parametrize('change', ['completed', 'deleted', 'identity_changed'])
def test_selected_start_invalidated_by_external_changes_stops_launch(desktop, change):
    window, paths = desktop
    key = 'university'
    paths[key].write_text('- [ ] Первая <!-- task:first -->\n- [ ] Выбранная <!-- task:chosen -->\n', encoding='utf-8')
    window.reload()
    window.choose_start_task(key, 'chosen')
    text = paths[key].read_text(encoding='utf-8')
    if change == 'completed':
        text = text.replace('- [ ] Выбранная', '- [x] Выбранная')
    elif change == 'deleted':
        text = text.splitlines(keepends=True)[0]
    else:
        text = text.replace('task:chosen', 'task:replacement')
    paths[key].write_text(text, encoding='utf-8')
    original = paths[key].read_bytes()
    window.start_session()
    assert window.session is None and window.start_task is None
    assert window.pages.currentWidget() == window.overview
    assert window.toast.property('error') and 'Выберите задачу' in window.toast.text()
    assert paths[key].read_bytes() == original


def test_selected_start_snapshot_failure_retains_choice_and_restore_position(desktop, monkeypatch):
    window, paths = desktop
    key = 'university'
    root = window.stores[key].tasks[1].id
    window.choose_start_task(key, root)
    original = paths[key].read_bytes()
    write = window.state.write
    def fail(name, data):
        if name == 'active_session.json':
            raise StateError('Нет доступа к снимку')
        write(name, data)
    monkeypatch.setattr(window.state, 'write', fail)
    window.start_session()
    assert window.session is None and window.start_task['id'] == root
    assert window.toast.property('error') and paths[key].read_bytes() == original
    monkeypatch.setattr(window.state, 'write', write)
    window.start_session()
    window.close()
    reopened = Window(window.state.directory, paths)
    reopened.restore_session()
    assert reopened.current_task.text == 'Вторая' and not reopened.session.running
    assert reopened.start_task is None
    reopened.close()


@pytest.mark.parametrize('selected_start', [False, True])
def test_completion_moves_only_forward_after_start_or_arrow_skip_and_restores_end(desktop, selected_start):
    window, paths = desktop
    key = 'university'
    store = window.stores[key]
    first, last = (task.id for task in store.tasks)
    if selected_start:
        window.choose_start_task(key, last)
    window.start_session()
    if not selected_start:
        window.browse_task(1)
    assert window.current_task.id == last
    window.complete(key, last)
    wait(window)
    assert window.current_task is None and window.session.at_queue_end
    assert not store.by_id[first].done and window.session.counts == (1, 0)
    assert window.task_title.text() == 'Дальше задач нет'
    assert window.session.running and not window.complete_button.isEnabled()
    assert window.back_button.isEnabled() and not window.forward_button.isEnabled()
    window.close()
    reopened = Window(window.state.directory, paths)
    reopened.restore_session()
    assert reopened.current_task is None and reopened.session.at_queue_end
    assert not reopened.session.running
    reopened.browse_task(-1)
    assert reopened.current_task.id == last and not reopened.session.at_queue_end
    reopened.browse_task(-1)
    assert reopened.current_task.id == first
    reopened.close()


def test_end_undo_and_new_task_do_not_return_to_skipped_root(desktop):
    window, paths = desktop
    key = 'university'
    store = window.stores[key]
    first, last = (task.id for task in store.tasks)
    window.start_session()
    window.browse_task(1)
    window.complete(key, last)
    wait(window)
    window.undo()
    assert window.current_task.id == last and not window.session.at_queue_end
    assert window.session.counts == (0, 0)
    window.complete(key, last)
    wait(window)
    window.add_task(key, 'Следующая после конца')
    assert window.current_task.text == 'Следующая после конца'
    assert not window.session.at_queue_end
    assert store.tasks[-1].id == window.current_task.id and not store.by_id[first].done


def test_forward_completion_skips_done_following_roots_without_wrapping(desktop):
    window, paths = desktop
    key = 'university'
    paths[key].write_text('- [ ] Пропущенная <!-- task:skip -->\n'
                          '- [ ] Текущая <!-- task:current -->\n'
                          '- [x] Готовая <!-- task:done -->\n'
                          '- [ ] Следующая <!-- task:next -->\n', encoding='utf-8')
    window.reload()
    window.start_session()
    window.browse_task(1)
    window.complete(key, 'current')
    wait(window)
    assert window.current_task.id == 'next'
    window.complete(key, 'next')
    wait(window)
    assert window.current_task is None and window.session.at_queue_end
    assert not window.stores[key].by_id['skip'].done


def test_pending_completion_recovery_keeps_end_after_markdown_commit(desktop):
    window, paths = desktop
    key = 'university'
    store = window.stores[key]
    last = store.tasks[-1].id
    session = Session(key, store.path, 25)
    session.view_task_id = last
    record = session.to_dict()
    completion = store.completion_for(last)
    record['pending'] = {'kind': 'complete', 'changes': completion.changes}
    window.state.write('active_session.json', {'session': record})
    store.restore(completion, forward=True)
    reopened = Window(window.state.directory, paths)
    reopened.restore_session()
    assert reopened.current_task is None and reopened.session.at_queue_end
    assert reopened.session.counts == (1, 0) and not reopened.session.running
    reopened.close()


def test_delete_last_work_root_keeps_end_and_browse_failure_keeps_cursor(desktop, monkeypatch):
    window, paths = desktop
    key = 'university'
    window.start_session()
    window.browse_task(1)
    last = window.current_task.id
    window.delete_task(key, last)
    assert window.current_task is None and window.session.at_queue_end
    def fail():
        raise StateError('Сбой снимка')
    monkeypatch.setattr(window, 'checkpoint', fail)
    window.browse_task(-1)
    assert window.current_task is None and window.session.at_queue_end
    assert window.session.view_task_id is None
    monkeypatch.undo()


def test_start_selection_recovery_blocks_menu_and_file_replacement_clears_choice(desktop, app, monkeypatch):
    window, paths = desktop
    key = 'university'
    root = window.stores[key].tasks[1].id
    window.choose_start_task(key, root)
    replacement = paths[key].parent / 'replacement.md'
    replacement.write_text(f'- [ ] Другая задача <!-- task:{root} -->\n', encoding='utf-8')
    from PySide6.QtWidgets import QFileDialog
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *args: (str(replacement), ''))
    window.choose_path(key)
    assert window.start_task is None and window.toast.property('error')
    root = window.stores[key].tasks[0].id
    window.recovery = Session(key, replacement, 10).to_dict()
    window.task_menu(key, root, window.task_rows[root])
    action = next(a for a in window.active_task_menu.actions() if a.objectName() == 'startTaskAction')
    assert not action.isEnabled()
    window.active_task_menu.close()
    window.choose_start_task(key, root)
    assert window.start_task is None


def test_start_selected_stops_on_unsaved_editor_write_failure(desktop, monkeypatch):
    window, paths = desktop
    key = 'university'
    root = window.stores[key].tasks[1].id
    window.choose_start_task(key, root)
    original = paths[key].read_bytes()
    window.task_edits[root].setPlainText('Не потерять ввод')
    def fail(lines):
        raise QueueError('Нет доступа к Markdown')
    monkeypatch.setattr(window.stores[key], '_commit', fail)
    window.start_session()
    assert window.session is None and window.start_task['id'] == root
    assert window.task_edits[root].toPlainText() == 'Не потерять ввод'
    assert paths[key].read_bytes() == original and window.toast.property('error')
    monkeypatch.undo()


def test_pending_undo_recovery_at_end_restores_last_root_without_wrapping(desktop):
    window, paths = desktop
    key = 'university'
    store = window.stores[key]
    last = store.tasks[-1].id
    completion = store.completion_for(last)
    store.restore(completion, forward=True)
    session = Session(key, store.path, 25)
    session.register(completion)
    session.at_queue_end = True
    record = session.to_dict()
    record['pending'] = {'kind': 'undo', 'changes': completion.changes}
    window.state.write('active_session.json', {'session': record})
    store.restore(completion)
    reopened = Window(window.state.directory, paths)
    reopened.restore_session()
    assert reopened.current_task.id == last and not reopened.session.at_queue_end
    assert reopened.session.counts == (0, 0) and not reopened.session.running
    reopened.close()
