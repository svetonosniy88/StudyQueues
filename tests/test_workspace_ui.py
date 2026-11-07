import os
os.environ['QT_QPA_PLATFORM']='offscreen'
from pathlib import Path
import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt,QPoint
from studyqueues.ui import apply_theme
from studyqueues.workspace_ui import WorkspaceWindow
from studyqueues.workspace_dialogs import CorrectionDialog


@pytest.fixture(scope='module')
def app():
    app=QApplication.instance() or QApplication([]);apply_theme(app);return app


@pytest.fixture
def window(tmp_path,app):
    w=WorkspaceWindow(tmp_path);w.show();app.processEvents()
    yield w
    if not w.closed:w.close()
    app.processEvents()


def test_free_session_task_capture_finish_and_no_recovery(window,app):
    window.entry.setText('Сделать пример');ident=window.add_entry()
    window.select_task(ident);window.start_session()
    window.store.toggle(ident,True,window.session.id);window.checkpoint()
    assert window.session.counts==(0,0)
    window.focus_panel.entry.setText('Разобрал новую тему');window.focus_panel.add()
    assert window.session.counts==(1,0)
    window.finish_session()
    assert window.session is None and window.store.active_session() is None
    assert len(window.store.history())==1 and window.focus_panel.pages.currentWidget() is window.focus_panel.result
    assert not window.findChildren(type(window.pause_button),'recovery_button')


def test_close_resume_paused_drafts_and_focus_geometry(window,app):
    directory=window.store.directory
    window.entry.setText('Не потерять');window.start_session();ident=window.session.id
    window.open_focus();window.focus_panel.entry.setText('Черновик фокуса')
    assert window.width()<=500
    window.close()
    reopened=WorkspaceWindow(directory);reopened.show();app.processEvents()
    assert reopened.session.id==ident and not reopened.session.running
    assert reopened.entry.text()=='Не потерять'
    assert reopened.focus_mode and reopened.focus_panel.entry.text()=='Черновик фокуса'
    reopened.finish_session()
    assert len(reopened.store.history()[0]['completed'])==1
    assert reopened.entry.text()=='Не потерять'
    assert reopened.store.active_session() is None
    reopened.close()


def test_queue_moves_forward_without_wrapping(window):
    a=window.store.add_task('A','university');b=window.store.add_task('B','university')
    window.store.reorder([a,b])
    window.set_view('project','university');window.select_task(b);window.start_queue()
    window.store.toggle(b,True,window.session.id);window.advance_queue(window.store.task(b)['position']);window.checkpoint()
    assert window.session.at_queue_end and window.current_id is None
    assert not window.store.task(a)['done']


def test_time_dialog_preview_and_undo_finished_session(window):
    from tests.test_workspace_store import record
    s=record();window.store.save_session(s)
    dialog=CorrectionDialog(window.store,s['id'],window)
    dialog.minutes.setValue(5);dialog.seconds.setValue(0)
    assert '30:00' in dialog.preview.text() and '25:00' in dialog.preview.text()
    dialog.apply()
    assert window.store.session(s['id'])['active_seconds']==1500
    key=window.store.corrections(s['id'])[0]['id'];window.store.undo_correction(key)
    assert window.store.session(s['id'])['active_seconds']==1800


def test_card_checkbox_click_and_text_selection_are_separate(window,app):
    window.set_view('today')
    window.show_done.setChecked(True)
    key=window.store.add_task('Короткая задача','university');window.refresh_tasks();app.processEvents()
    item=window.items[key];rect=window.tree.visualItemRect(item)
    QTest.mouseClick(window.tree.viewport(),Qt.LeftButton,pos=QPoint(rect.left()+100,rect.top()+25))
    assert not window.store.task(key)['done']
    QTest.mouseClick(window.tree.viewport(),Qt.LeftButton,pos=QPoint(rect.left()+26,rect.top()+25))
    assert window.store.task(key)['done']
    app.processEvents()
    rect=window.tree.visualItemRect(window.items[key])
    QTest.mouseClick(window.tree.viewport(),Qt.LeftButton,pos=QPoint(rect.left()+26,rect.top()+25))
    assert not window.store.task(key)['done']
    app.processEvents()
    window.tree.setFocus();QTest.keyClick(window.tree,Qt.Key_Space)
    assert window.store.task(key)['done']
    QTest.keyClick(window.tree,Qt.Key_Space)
    assert not window.store.task(key)['done']


def test_correction_uses_plain_number_fields_without_spin_arrows(window):
    from tests.test_workspace_store import record
    from PySide6.QtWidgets import QAbstractSpinBox,QLineEdit
    s=record();window.store.save_session(s);dialog=CorrectionDialog(window.store,s['id'],window)
    assert isinstance(dialog.minutes,QLineEdit) and isinstance(dialog.seconds,QLineEdit)
    assert all(control.buttonSymbols()==QAbstractSpinBox.NoButtons for control in dialog.findChildren(QAbstractSpinBox))
    assert not dialog.list.isVisible()
    dialog.close()


def test_failed_start_does_not_leave_an_unpersisted_session(window,monkeypatch):
    import sqlite3
    def fail(*_):raise sqlite3.OperationalError('database is locked')
    monkeypatch.setattr(window.store,'save_session',fail)
    window.start_session()
    assert window.session is None and window.store.active_session() is None


def test_queue_completing_last_child_advances_and_preserves_plan(window,app):
    root=window.store.add_task('A','university');child=window.store.add_task('A.1',parent=root)
    following=window.store.add_task('B','university');window.store.reorder([root,following])
    window.set_view('project','university');window.select_task(root);window.start_queue(25)
    assert window.session.planned_seconds==1500
    window.refresh_tasks();window.items[child].setCheckState(0,Qt.Checked);app.processEvents()
    assert window.store.task(root)['done'] and window.current_id==following


@pytest.mark.parametrize('size',[(820,650),(1160,820),(1600,1000)])
def test_centered_workspace_and_no_action_overlap(window,app,size):
    window.start_session()
    window.leave_focus()
    window.resize(*size);QTest.qWait(30)
    buttons=[window.focus_panel.pause,window.focus_panel.finish]
    rects=[b.geometry() for b in buttons]
    assert all(a.right()<b.left() for a,b in zip(rects,rects[1:]))
    assert window.focus_panel.center.width()<=580
    clock=window.focus_panel.timer_label
    center=clock.mapTo(window,clock.rect().center()).x()
    assert abs(center-window.width()/2)<3


def test_focus_reuses_native_window_and_restores_workspace(window,app):
    from PySide6.QtWidgets import QMainWindow
    handle=int(window.winId());window.resize(1040,740);app.processEvents()
    geometry=window.geometry()
    for _ in range(5):
        window.open_focus();app.processEvents()
        assert window.focus_mode and window.mode_stack.currentWidget() is window.focus_panel
        assert int(window.winId())==handle
        assert window.focus_panel.window() is window and not window.focus_panel.isWindow()
        assert [w for w in app.topLevelWidgets() if isinstance(w,QMainWindow) and w.isVisible()]==[window]
        assert window.width()<=500
        QTest.mouseClick(window.focus_panel.back,Qt.LeftButton);app.processEvents()
        assert not window.focus_mode and window.geometry()==geometry


def test_free_focus_is_a_blank_session_canvas_with_saved_entries(window,app):
    old=window.store.add_task('Задача из списка','university');window.select_task(old)
    window.open_focus();panel=window.focus_panel
    assert panel.empty.isVisible() and not panel.title.isVisible()
    assert window.session is None
    QTest.mouseClick(panel.pause,Qt.LeftButton)
    panel.entry.setText('Разобрал определение');first=panel.add();app.processEvents()
    panel.entry.setText('Проверил пример');second=panel.add();app.processEvents()
    assert window.session and window.session.creative
    assert {panel.tasks.topLevelItem(i).data(0,Qt.UserRole) for i in range(panel.tasks.topLevelItemCount())}=={first,second}
    assert window.store.task(first)['done'] and window.store.task(second)['done']
    window.leave_focus();window.open_focus()
    assert panel.tasks.topLevelItemCount()==2
    panel.tasks.topLevelItem(0).setCheckState(0,Qt.Unchecked);app.processEvents()
    assert panel.tasks.topLevelItemCount()==2
    assert not panel.empty.isVisible()


def test_checkbox_does_not_destroy_item_inside_model_notification(window,app):
    key=window.store.add_task('Проверить отметку');window.refresh_tasks()
    item=window.items[key];resets=[]
    window.tree.model().modelReset.connect(lambda:resets.append(True))
    item.setCheckState(0,Qt.Checked)
    assert window.store.task(key)['done'] and not resets
    assert window.items[key] is item
    app.processEvents()
    assert resets and key not in window.items


def test_navigation_stays_responsive_with_large_tree_and_active_session(window,app):
    import time
    from PySide6.QtCore import QTimer
    import sys
    errors=[];previous=sys.excepthook
    sys.excepthook=lambda *args:errors.append(args)
    try:
        for i in range(50):
            root=window.store.add_task(f'Тема {i}: длинный текст для проверки переноса','university')
            for j in range(7):window.store.add_task(f'Подпункт {j}',parent=root)
        window.store.add_task('Входящее');window.refresh_tasks();window.start_session()
        beats=[];timer=QTimer();timer.setInterval(5);timer.timeout.connect(lambda:beats.append(True));timer.start()
        project_button=window.project_buttons['university'];times=[]
        for _ in range(12):
            start=time.perf_counter()
            QTest.mouseClick(window.nav['today'],Qt.LeftButton);app.processEvents()
            item=window.tree.topLevelItem(0);window.tree.setCurrentItem(item)
            QTest.mouseClick(window.nav['inbox'],Qt.LeftButton);app.processEvents()
            QTest.mouseClick(project_button,Qt.LeftButton);app.processEvents()
            QTest.mouseClick(window.nav['history'],Qt.LeftButton);QTest.qWait(8)
            times.append(time.perf_counter()-start)
        timer.stop()
        assert window.project_buttons['university'] is project_button
        assert beats and not errors
        assert max(times)<2, times
    finally:sys.excepthook=previous


def test_selection_has_no_colored_rectangle_in_branch_gutter(window,app):
    from PySide6.QtGui import QColor
    root=window.store.add_task('Основная задача');window.store.add_task('Подпункт',parent=root)
    window.set_view('today');window.tree.setCurrentItem(window.items[root]);app.processEvents()
    rect=window.tree.visualItemRect(window.items[root]);image=window.tree.viewport().grab().toImage()
    ratio=image.devicePixelRatio()
    assert image.pixelColor(round(5*ratio),round((rect.top()+10)*ratio))==QColor('#1b191e')
    assert window.tree.selectionModel().isSelected(window.tree.indexFromItem(window.items[root]))


def test_locked_database_returns_error_promptly_without_starting_session(window):
    import sqlite3,time
    other=sqlite3.connect(window.store.path)
    try:
        other.execute('BEGIN IMMEDIATE')
        start=time.perf_counter();window.start_session();elapsed=time.perf_counter()-start
        assert elapsed<1.5 and window.session is None
        assert 'locked' in window.toast.text()
    finally:other.rollback();other.close()


def test_focus_refreshes_when_entered_from_history_and_shortcuts_target_visible_screen(window,app):
    window.set_view('history');window.open_focus()
    QTest.mouseClick(window.focus_panel.pause,Qt.LeftButton)
    window.focus_panel.entry.setText('Запись из журнала');window.focus_panel.add();app.processEvents()
    assert window.focus_panel.tasks.topLevelItemCount()==1
    window.focus_entry();assert app.focusWidget() is window.focus_panel.entry
    window.focus_search();assert not window.focus_mode and app.focusWidget() is window.search


def test_start_free_work_is_an_upward_stopwatch_with_no_queue(window,app):
    root=window.store.add_task('Очередь: основная','university')
    window.store.add_task('Очередь: подпункт',parent=root)
    window.set_view('project','university');window.select_task(root)
    window.start_session();app.processEvents()
    assert not window.focus_mode and window.session.creative
    assert window.view=='session'
    assert window.session.planned_seconds==0 and window.session.view_task_id is None
    assert window.current_id is None and window.queue_project is None
    assert window.focus_panel.tasks.topLevelItemCount()==0 and window.focus_panel.empty.isVisible()
    assert not window.store.session_tasks(window.session.id)
    clock=[0.0];window.session.clock=lambda:clock[0];window.session.last_tick=0
    clock[0]=2;window.tick();first=window.effective_seconds()
    clock[0]=5;window.tick()
    assert first>=2 and window.effective_seconds()>first
    assert not window.store.task(root)['done']


def test_browsing_and_completing_queue_tasks_does_not_add_free_entries(window,app):
    root=window.store.add_task('Очередь: невыполненная','university')
    window.store.add_task('Очередь: подпункт',parent=root)
    window.start_session();window.leave_focus();window.set_view('project','university')
    window.tree.setCurrentItem(window.items[root])
    window.items[root].setCheckState(0,Qt.Checked);app.processEvents();window.open_focus()
    assert not window.store.session_tasks(window.session.id)
    assert not window.store.session_entries(window.session.id)
    assert window.focus_panel.tasks.topLevelItemCount()==0
    assert window.session.completed=={} and window.session.view_task_id is None
    window.focus_panel.entry.setText('Сделал то, чем сам решил заняться');entry=window.focus_panel.add()
    assert [row['id'] for row in window.store.session_entries(window.session.id)]==[entry]
    assert window.store.task(entry)['project_id'] is None
    assert window.session.counts==(1,0)


def test_restored_free_session_hides_old_queue_links_but_keeps_written_entries(window,app):
    root=window.store.add_task('Старая задача очереди','university');window.start_session()
    ident=window.session.id;seconds=window.session.active_seconds
    window.store.link(ident,root)  # Links left by the previous implementation.
    window.focus_panel.entry.setText('Моя запись');entry=window.focus_panel.add()
    directory=window.store.directory;window.close()
    reopened=WorkspaceWindow(directory);reopened.show();app.processEvents()
    try:
        assert not reopened.focus_mode and not reopened.session.running
        assert reopened.session.id==ident and reopened.session.active_seconds>=seconds
        assert [row['id'] for row in reopened.store.session_entries(ident)]==[entry]
        assert reopened.focus_panel.tasks.topLevelItemCount()==1
        assert not reopened.store.task(root)['done']
        reopened.finish_session()
        assert set(reopened.store.history()[0]['completed'])=={entry}
        target=directory/'session.md';reopened.store.export_markdown(target,ident)
        assert 'Моя запись' in target.read_text(encoding='utf-8')
        assert 'Старая задача очереди' not in target.read_text(encoding='utf-8')
    finally:reopened.close()
