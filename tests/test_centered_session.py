"""User journeys for the accepted centered session design."""
from datetime import datetime, timedelta, timezone
import sqlite3
import pytest
from PySide6.QtCore import Qt, QPoint, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QPushButton
from tests.test_workspace_ui import app, window
from studyqueues.workspace_ui import WorkspaceWindow
from studyqueues.workspace_dialogs import CorrectionDialog


def seed_queue(window):
    a=window.store.add_task('Разобрать интегрирование по частям','university')
    child=window.store.add_task('Решить пример',parent=a)
    b=window.store.add_task('Проверить лабораторную по Python','university')
    window.store.reorder([a,b]);return a,child,b


def test_opening_and_selecting_25_minutes_is_only_preparation(window,app):
    a,_,b=seed_queue(window);panel=window.focus_panel
    initial=window.geometry();handle=int(window.winId())
    assert window.view=='session' and window.session is None
    QTest.mouseClick(panel.mode_buttons['queue'],Qt.LeftButton);app.processEvents()
    assert panel.pages.currentWidget() is panel.setup
    QTest.mouseClick(panel.presets[60],Qt.LeftButton)
    QTest.mouseClick(panel.presets[25],Qt.LeftButton)
    item=next(panel.start_tasks.topLevelItem(i) for i in range(panel.start_tasks.topLevelItemCount()) if panel.start_tasks.topLevelItem(i).data(0,Qt.UserRole)==b)
    panel.start_tasks.setCurrentItem(item);app.processEvents()
    assert window.session is None and window.store.active_session() is None
    assert not window.store.history() and not window.store.task(b)['done']
    assert window.geometry()==initial
    QTest.mouseClick(panel.start,Qt.LeftButton);app.processEvents()
    assert window.session.planned_seconds==1500 and window.current_id==b
    assert window.session.running and int(window.winId())==handle
    assert window.geometry()==initial and not window.focus_mode
    assert 'из 25 мин' in panel.caption.text()


def test_invalid_duration_and_empty_queue_cannot_start(window,app):
    panel=window.focus_panel;window.choose_mode('queue')
    assert not panel.start.isEnabled() and window.session is None
    seed_queue(window);panel.refresh_setup()
    for text in ('','0','481'):
        panel.duration.setText(text)
        assert not panel.start.isEnabled()
    panel.duration.setText('25');assert panel.start.isEnabled()


def test_free_start_does_not_resize_and_explicit_start_is_required(window,app):
    seed_queue(window);panel=window.focus_panel
    geometry=window.geometry();window.open_focus()
    assert window.session is None
    panel.entry.setText('Не должен запускать занятие');assert panel.add() is None
    assert window.session is None and not window.store.history()
    panel.entry.clear();window.leave_focus()
    QTest.mouseClick(panel.pause,Qt.LeftButton);app.processEvents()
    assert window.geometry()==geometry and window.session.creative
    assert panel.tasks.topLevelItemCount()==0
    assert 'Запустите' not in panel.empty.text()


def test_records_and_drafts_survive_view_switch_and_relaunch(window,app):
    panel=window.focus_panel;window.start_session()
    panel.entry.setText('Разобрал замену переменной');QTest.keyClick(panel.entry,Qt.Key_Return)
    ident=window.session.id;window.session.pause();window.checkpoint()
    seconds=window.session.active_seconds
    panel.entry.setText('Незаконченная запись')
    handle=int(window.winId())
    for _ in range(3):
        window.open_focus();app.processEvents();window.leave_focus();app.processEvents()
        assert int(window.winId())==handle and window.session.id==ident
        assert not window.session.running and window.session.active_seconds==seconds
        assert panel.entry.text()=='Незаконченная запись' and panel.tasks.topLevelItemCount()==1
    window.open_focus();directory=window.store.directory;window.close()
    reopened=WorkspaceWindow(directory);reopened.show();app.processEvents()
    try:
        assert reopened.focus_mode and reopened.view=='session'
        assert reopened.session.id==ident and not reopened.session.running
        assert reopened.session.active_seconds==seconds
        assert reopened.focus_panel.entry.text()=='Незаконченная запись'
        assert reopened.focus_panel.tasks.topLevelItemCount()==1
        assert reopened.focus_panel.restored.isVisible()
    finally:reopened.close()


def test_browsing_other_queue_keeps_current_task_and_time_assignment(window,app):
    a,_,b=seed_queue(window)
    other=window.store.add_task('Другой проект','self_development')
    window.choose_mode('queue','university',a);window.start_prepared()
    window.set_view('project','self_development')
    window.tree.setCurrentItem(window.items[other]);app.processEvents()
    assert window.current_id==a and window.session.view_task_id==a
    assert window.queue_project=='university' and window.session_card.isVisible()
    assert other not in [r['id'] for r in window.store.session_tasks(window.session.id)]
    QTest.mouseClick(window.focus_button,Qt.LeftButton)
    assert window.view=='session' and window.focus_panel.title.text()==window.store.task(a)['title']


def test_queue_complete_skip_undo_and_end_do_not_finish_session(window,app):
    a,child,b=seed_queue(window)
    window.choose_mode('queue','university',a);window.start_prepared();ident=window.session.id
    window.complete_queue_task();assert window.current_id==b
    assert window.store.task(a)['done'] and window.store.task(child)['done']
    window.undo_queue_action();assert window.current_id==a
    assert not window.store.task(a)['done'] and not window.store.task(child)['done']
    window.complete_queue_task(True);assert window.current_id==b and not window.store.task(a)['done']
    window.complete_queue_task();assert window.current_id is None and window.session.at_queue_end
    assert window.session.id==ident and window.session.running
    assert not window.store.history()


def test_remaining_clock_and_correction_preserve_queue_state(window,app):
    a,_,_=seed_queue(window);window.choose_mode('queue','university',a);window.start_prepared()
    session=window.session
    clock=[0.0];base=datetime(2026,11,1,12,tzinfo=timezone.utc)
    session.clock=lambda:clock[0];session.now=lambda:base+timedelta(seconds=clock[0])
    session.last_tick=0;session.last_wall=base;session.started_at=base.isoformat();session.intervals=[];session.active_seconds=0;session._merge=False
    for _ in range(120):clock[0]+=5;session.tick()
    session.pause();window.checkpoint();window.update_clock()
    assert window.focus_panel.timer_label.text()=='15:00'
    assert '10:00' in window.focus_panel.caption.text()
    dialog=CorrectionDialog(window.store,session.id,window)
    dialog.minutes.setValue(5);dialog.seconds.setValue(0);dialog.apply()
    window.update_clock()
    assert window.focus_panel.timer_label.text()=='20:00'
    assert not session.running and window.current_id==a and not window.store.task(a)['done']
    # Crossing the plan is display state, never implicit completion.
    session.planned_seconds=60;window.update_clock()
    assert window.focus_panel.timer_label.text().startswith('+')
    assert window.session is session and not window.store.task(a)['done']


def test_finish_saves_pending_note_and_never_offers_false_restore(window,app):
    window.start_session();panel=window.focus_panel
    panel.entry.setText('Последняя запись');window.entry.setText('Отдельный черновик задачи')
    QTest.mouseClick(panel.finish,Qt.LeftButton);app.processEvents()
    assert window.session is None and window.store.active_session() is None
    assert panel.pages.currentWidget() is panel.result
    assert panel.result_list.topLevelItem(0).text(0)=='Последняя запись'
    assert window.entry.text()=='Отдельный черновик задачи'
    directory=window.store.directory;window.close()
    reopened=WorkspaceWindow(directory);reopened.show();app.processEvents()
    try:
        assert reopened.session is None and not reopened.focus_panel.restored.isVisible()
        assert reopened.focus_panel.pause.text()=='Начать'
        assert len(reopened.store.history())==1
    finally:reopened.close()


def test_failed_finish_and_close_keep_the_session_available(window,monkeypatch):
    window.start_session();ident=window.session.id
    original=window.store.save_session
    def fail(*args,**kwargs):raise sqlite3.OperationalError('disk full')
    monkeypatch.setattr(window.store,'save_session',fail)
    window.finish_session()
    assert window.session.id==ident and window.session.ended_at is None
    assert window.store.active_session()['id']==ident and not window.result_id
    window.close();assert not window.closed and window.isVisible()
    monkeypatch.setattr(window.store,'save_session',original)


def test_mode_change_cancel_keeps_session_and_accept_opens_preparation(window,app):
    seed_queue(window);window.start_session();ident=window.session.id
    panel=window.focus_panel
    panel.entry.setText('Запись перед сменой режима')
    QTimer.singleShot(0,lambda: app.activeModalWidget().reject())
    QTest.mouseClick(panel.mode_buttons['queue'],Qt.LeftButton)
    assert window.session.id==ident and window.session.running
    assert panel.mode_buttons['free'].isChecked() and not panel.mode_buttons['queue'].isChecked()
    assert panel.entry.text()=='Запись перед сменой режима'
    QTimer.singleShot(0,lambda: app.activeModalWidget().accept())
    QTest.mouseClick(panel.mode_buttons['queue'],Qt.LeftButton)
    assert window.session is None and window.store.active_session() is None
    assert panel.pages.currentWidget() is panel.setup
    assert len(window.store.history())==1
    assert window.store.session_entries(ident)[0]['title']=='Запись перед сменой режима'


@pytest.mark.parametrize('size',[(440,640),(480,760),(700,900)])
@pytest.mark.parametrize('queued',[False,True])
def test_compact_controls_are_visible_without_overlap(window,app,size,queued):
    a,_,_=seed_queue(window)
    if queued:
        window.store.update_task(a,title='Длинная задача: проверить условия теоремы, разобрать доказательство и решить несколько подробных примеров с разными исходными данными')
        window.choose_mode('queue','university',a);window.start_prepared()
    else:window.start_session()
    window.open_focus();window.resize(*size)
    panel=window.focus_panel
    panel.entry.setText('Короткая запись');panel.add();QTest.qWait(30)
    controls=(panel.pause,panel.finish,panel.correct,panel.back,panel.done_button,panel.skip_button) if queued else (panel.pause,panel.finish,panel.correct,panel.entry,panel.record,panel.back)
    for control in controls:
        rect=control.rect();top_left=control.mapTo(window,rect.topLeft());bottom_right=control.mapTo(window,rect.bottomRight())
        assert control.isVisible() and window.rect().contains(top_left) and window.rect().contains(bottom_right)
    assert panel.pause.geometry().right()<panel.finish.geometry().left()
    if queued:
        QTest.mouseClick(panel.notes_toggle,Qt.LeftButton);QTest.qWait(30)
    assert panel.entry.isVisible() and panel.record.isVisible()
    assert panel.entry.geometry().right()<panel.record.geometry().left()
    assert abs(panel.timer_label.mapTo(window,panel.timer_label.rect().center()).x()-window.width()/2)<3
    if queued:
        assert panel.title.height()>=panel.title.heightForWidth(panel.title.width())
    for first,second in ((panel.status,panel.timer_label),(panel.timer_label,panel.caption),(panel.caption,panel.pause),(panel.pause,panel.correct)):
        assert first.mapTo(window,first.rect().bottomLeft()).y()<second.mapTo(window,second.rect().topLeft()).y()
