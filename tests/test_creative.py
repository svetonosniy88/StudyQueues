import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QDesktopServices

from studyqueues.ui import Window, apply_theme
from studyqueues.state_store import StateError
from studyqueues.queue_store import QueueError
from studyqueues.creative import create_creative_session
from studyqueues.session import Session


@pytest.fixture(scope='module')
def app():
    instance = QApplication.instance() or QApplication([])
    apply_theme(instance)
    return instance


@pytest.fixture
def desktop(tmp_path, app, monkeypatch):
    paths = {}
    for key in ('university', 'self_development'):
        paths[key] = tmp_path / (key + '.md')
        paths[key].write_text(f'# Очередь\n- [ ] Не трогать <!-- task:{key} -->\n', encoding='utf-8')
    errors = []
    monkeypatch.setattr(sys, 'excepthook', lambda *error: errors.append(error))
    window = Window(tmp_path / 'profile', paths)
    window.resize(1160, 820)
    window.show()
    app.processEvents()
    yield window, paths
    window.close()
    app.processEvents()
    assert not errors, [str(error[1]) for error in errors]


def enter(window, text):
    panel = window.creative_panel
    panel.entry.setText(text)
    QTest.keyClick(panel.entry, Qt.Key_Return)
    QApplication.processEvents()


def test_creative_starts_without_queues_or_duration_and_creates_unique_files(tmp_path, app):
    window = Window(tmp_path / 'profile', {})
    window.show()
    window.duration.setValue(0)
    window.start_creative_session()
    assert window.session.creative and window.session.planned_seconds == 0
    assert not window.stores.get('university') and not window.stores.get('self_development')
    assert window.pages.currentWidget() == window.work and window.sidebar.isHidden()
    path = Path(window.session.path)
    assert path.parent == window.state.directory / 'creative' and path.is_file()
    assert '# Творческое занятие' in path.read_text(encoding='utf-8')
    assert window.timer_label.text() == '00:00'
    window.finish_session()
    window.show_overview()
    window.start_creative_session()
    assert Path(window.session.path) != path and path.is_file()
    window.close()


def test_records_default_done_children_reopen_edit_and_remain_visible(desktop, app):
    window, paths = desktop
    original = {key: path.read_bytes() for key, path in paths.items()}
    QTest.mouseClick(window.creative_button, Qt.LeftButton)
    enter(window, 'Разобрал модель')
    store = window.stores['creative']
    root = store.tasks[0].id
    assert store.by_id[root].done and window.session.counts == (1, 0)
    window.task_menu('creative', root, window.creative_panel.rows[root])
    menu = window.active_task_menu
    action = next(action for action in menu.actions() if action.text() == 'Добавить подпункт')
    QTest.mouseClick(menu, Qt.LeftButton, pos=menu.actionGeometry(action).center())
    app.processEvents()
    assert window.session.creative_draft['parent_id'] == root
    enter(window, 'Проверил метрики')
    child = store.by_id[root].children[0].id
    assert store.by_id[root].done and store.by_id[child].done and window.session.counts == (1, 1)
    panel = window.creative_panel
    QTest.mouseClick(panel.rows[child].checkbox, Qt.LeftButton)
    assert not store.by_id[child].done and not store.by_id[root].done
    assert panel.rows[root].isVisible() and panel.rows[child].isVisible()
    QTest.mouseClick(panel.rows[child].checkbox, Qt.LeftButton)
    assert store.by_id[root].done and store.by_id[child].done
    assert panel.rows[root].isVisible() and panel.rows[child].isVisible()
    editor = panel.rows[root].editor
    editor.setPlainText('Модель и выводы')
    QTest.keyClick(editor, Qt.Key_Return)
    assert store.by_id[root].text == 'Модель и выводы'
    assert 'Модель и выводы' in panel.parent_label.text()
    assert not window.busy and window.current_task is None
    assert {key: path.read_bytes() for key, path in paths.items()} == original


def test_all_creative_roots_and_children_remain_on_screen_without_paging(desktop, app):
    window, paths = desktop
    window.start_creative_session()
    store = window.stores['creative']
    for i in range(21):
        assert window.add_task('creative', f'Выполненная задача {i}')
    root = store.tasks[0].id
    for i in range(18):
        assert window.add_task('creative', f'Подпункт {i}', root)
    app.processEvents()
    panel = window.creative_panel
    assert len(panel.groups) == 21 and len(panel.rows) == 39
    assert window.session.counts == (21, 18)
    assert all(row.isVisible() for row in panel.rows.values())
    assert panel.scroll.verticalScrollBar().maximum() > 0
    scroll = panel.scroll.verticalScrollBar()
    scroll.setValue(scroll.maximum() // 2)
    position = scroll.value()
    window.set_task_checked('creative', store.tasks[-1].id, False)
    app.processEvents()
    assert scroll.value() == position and len(panel.rows) == 39
    assert not hasattr(panel, 'pagers')


def test_creative_close_restores_notes_and_unsaved_draft_on_pause(desktop):
    window, paths = desktop
    window.start_creative_session()
    enter(window, 'Уже сделано')
    root = window.stores['creative'].tasks[0].id
    window.creative_panel.add_child(root)
    window.creative_panel.entry.setText('Ещё пишу')
    notes = Path(window.session.path)
    before = notes.read_bytes()
    window.close()
    reopened = Window(window.state.directory, paths)
    reopened.restore_session()
    assert reopened.session.creative and not reopened.session.running
    assert reopened.creative_panel.entry.text() == 'Ещё пишу'
    assert reopened.session.creative_draft['parent_id'] == root
    assert len(reopened.creative_panel.rows) == 1 and notes.read_bytes() == before
    assert 'creative' not in reopened.settings['paths']
    reopened.finish_session()
    reopened.show_overview()
    assert reopened.creative_button.isEnabled()
    reopened.close()


def test_creative_finish_records_history_opens_file_and_delete_keeps_file(desktop, monkeypatch):
    window, paths = desktop
    window.start_creative_session()
    enter(window, 'Сделал прототип')
    window.creative_panel.entry.setText('Запись перед завершением')
    notes = Path(window.session.path)
    ident = window.session.id
    window.finish_session()
    assert window.session is None and window.pages.currentWidget() == window.stats
    record = window.state.history()[0]
    assert record['queue'] == 'creative' and len(record['completed']) == 2
    assert 'Запись перед завершением' in notes.read_text(encoding='utf-8')
    opened = []
    monkeypatch.setattr(QDesktopServices, 'openUrl', lambda url: opened.append(url.toLocalFile()) or True)
    QTest.mouseClick(window.open_notes_buttons[ident], Qt.LeftButton)
    assert Path(opened[0]) == notes
    window.favorite_session(ident, True)
    window.filter_history(True)
    assert ident in window.history_rows
    window.delete_session(ident)
    assert notes.is_file() and window.state.history() == []
    window.show_overview()
    assert 'creative' not in window.stores
    window.duration.setValue(5)
    window.start_session()
    assert not window.session.creative and window.current_task is not None


def test_creative_start_snapshot_failure_does_not_leave_file_or_replace_state(desktop, monkeypatch):
    window, paths = desktop
    def fail():
        raise StateError('Сбой снимка')
    monkeypatch.setattr(window, 'checkpoint', fail)
    window.start_creative_session()
    assert window.session is None and 'creative' not in window.stores
    assert list((window.state.directory / 'creative').glob('*.md')) == []
    assert window.toast.property('error')
    monkeypatch.undo()


def test_creative_write_conflict_retains_input_and_external_content(desktop):
    window, paths = desktop
    window.start_creative_session()
    store = window.stores['creative']
    external = store.path.read_bytes() + 'Внешняя заметка\n'.encode('utf-8')
    store.path.write_bytes(external)
    enter(window, 'Не терять эту запись')
    assert window.creative_panel.entry.text() == 'Не терять эту запись'
    assert store.path.read_bytes() == external and window.toast.property('error')
    window.reload()
    assert window.creative_panel.entry.text() == 'Не терять эту запись'
    QTest.keyClick(window.creative_panel.entry, Qt.Key_Return)
    assert 'Внешняя заметка' in store.path.read_text(encoding='utf-8')
    assert store.tasks[0].done and window.session.counts == (1, 0)


def test_missing_creative_file_pauses_and_is_not_reported_finished(desktop):
    window, paths = desktop
    window.start_creative_session()
    path = Path(window.session.path)
    original = path.read_bytes()
    path.unlink()
    window.reload()
    assert not window.session.running and not window.creative_panel.entry.isEnabled()
    assert 'Не удалось прочитать' in window.creative_panel.message.text()
    path.write_bytes(original)
    window.reload()
    assert window.creative_panel.entry.isEnabled()
    assert 'Не удалось прочитать' not in window.creative_panel.message.text()
    window.toggle_pause()
    assert window.session.running


def test_creative_delete_task_reconciles_credit_and_parent_draft(desktop):
    window, paths = desktop
    window.start_creative_session()
    enter(window, 'Родитель')
    root = window.stores['creative'].tasks[0].id
    window.creative_panel.add_child(root)
    enter(window, 'Подпункт')
    window.creative_panel.entry.setText('Черновик не потерять')
    window.delete_task('creative', root)
    assert not window.creative_panel.rows and window.session.counts == (0, 0)
    assert window.creative_panel.entry.text() == 'Черновик не потерять'
    assert window.session.creative_draft['parent_id'] is None


def test_creative_finish_failure_is_retryable_and_does_not_lose_notes(desktop, monkeypatch):
    window, paths = desktop
    window.start_creative_session()
    enter(window, 'Сохранено в файле')
    notes = Path(window.session.path)
    save = window.state.save_session
    def fail(record):
        raise StateError('Сбой истории')
    monkeypatch.setattr(window.state, 'save_session', fail)
    window.finish_session()
    assert window.session.ended_at and not window.session.running
    assert not window.creative_panel.entry.isEnabled() and window.finish_button.isEnabled()
    assert 'Сохранено в файле' in notes.read_text(encoding='utf-8')
    monkeypatch.setattr(window.state, 'save_session', save)
    window.finish_session()
    assert window.session is None and len(window.state.history()) == 1


def test_creative_and_queue_start_block_second_session(desktop):
    window, paths = desktop
    window.start_creative_session()
    ident = window.session.id
    window.start_creative_session()
    window.duration.setValue(5)
    window.start_session()
    assert window.session.id == ident
    assert len(list((window.state.directory / 'creative').glob('*.md'))) == 1


def test_missing_history_file_reports_error_without_opening(desktop, monkeypatch):
    window, paths = desktop
    opened = []
    monkeypatch.setattr(QDesktopServices, 'openUrl', lambda url: opened.append(url) or True)
    window.open_notes(window.state.directory / 'missing.md')
    assert not opened and window.toast.property('error')


def test_creative_results_keep_legacy_history_and_queue_recovery_blocks_launch(desktop):
    window, paths = desktop
    normal = Session('university', paths['university'], 5)
    normal.finish()
    record = normal.to_dict()
    record.pop('creative_draft')
    window.state.save_session(record)
    window.start_creative_session()
    enter(window, 'Творческая запись')
    creative_id = window.session.id
    window.finish_session()
    assert set(window.history_rows) == {normal.id, creative_id}
    assert set(window.open_notes_buttons) == {creative_id}
    assert next(s for s in window.state.history() if s['id'] == normal.id) == record
    window.show_overview()
    window.duration.setValue(5)
    window.start_session()
    window.close()
    original = (window.state.directory / 'active_session.json').read_bytes()
    reopened = Window(window.state.directory, paths)
    assert not reopened.creative_button.isEnabled()
    reopened.start_creative_session()
    assert reopened.session is None
    assert (window.state.directory / 'active_session.json').read_bytes() == original
    assert len(list((window.state.directory / 'creative').glob('*.md'))) == 1
    reopened.close()


def test_creative_completed_undo_keeps_rows_and_pending_recovery_counts(desktop):
    window, paths = desktop
    window.start_creative_session()
    enter(window, 'Сделанная запись')
    store = window.stores['creative']
    root = store.tasks[0].id
    window.set_task_checked('creative', root, False)
    window.set_task_checked('creative', root, True)
    window.undo()
    assert not store.by_id[root].done and root in window.creative_panel.rows
    assert window.session.counts == (0, 0)
    completion = store.completion_for(root)
    record = window.session.to_dict()
    record['pending'] = {'kind': 'complete', 'changes': completion.changes}
    window.state.write('active_session.json', {'session': record})
    store.restore(completion, forward=True)
    reopened = Window(window.state.directory, paths)
    reopened.restore_session()
    assert reopened.session.creative and reopened.session.counts == (1, 0)
    assert root in reopened.creative_panel.rows and reopened.creative_panel.rows[root].checkbox.isChecked()
    assert not reopened.session.running
    reopened.close()


@pytest.mark.parametrize('phase', ['before', 'after'])
def test_creative_snapshot_failure_recovers_one_entry_without_duplicate_draft(desktop, monkeypatch, phase):
    window, paths = desktop
    window.start_creative_session()
    window.timer.stop()
    panel = window.creative_panel
    panel.entry.setText('Сохранить ровно один раз')
    window.checkpoint()  # The draft may already have been autosaved while typing.
    write = window.state.write
    calls = 0
    def fail(name, data):
        nonlocal calls
        if name == 'active_session.json':
            calls += 1
            if calls >= (1 if phase == 'before' else 2):
                raise StateError('Сбой снимка')
        write(name, data)
    monkeypatch.setattr(window.state, 'write', fail)
    panel.submit()
    assert window.toast.property('error')
    applied = phase == 'after'
    assert len(window.stores['creative'].tasks) == int(applied)
    assert panel.entry.text() == ('' if applied else 'Сохранить ровно один раз')
    reopened = Window(window.state.directory, paths)
    reopened.restore_session()
    assert len(reopened.creative_panel.rows) == int(applied)
    assert reopened.session.counts == (int(applied), 0)
    assert reopened.creative_panel.entry.text() == ('' if applied else 'Сохранить ровно один раз')
    reopened.close()
    monkeypatch.setattr(window.state, 'write', write)
