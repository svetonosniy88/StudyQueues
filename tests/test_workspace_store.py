from datetime import datetime, timedelta
from pathlib import Path
import json
import pytest

from studyqueues.workspace_store import WorkspaceStore
from studyqueues.state_store import StateStore
from studyqueues.session import Session, daily_seconds
from studyqueues import accounting


def record():
    s=Session('creative','sqlite:workspace',0)
    start=datetime.fromisoformat('2026-11-01T23:50:00+03:00')
    s.started_at=start.isoformat();s.ended_at=(start+timedelta(minutes=30)).isoformat()
    s.active_seconds=1800
    s.intervals=[{'start':s.started_at,'end':s.ended_at,'seconds':1800}]
    return s.to_dict()


def test_recover_written_notes_without_treating_queue_links_as_entries(tmp_path):
    from datetime import datetime,timedelta
    store=WorkspaceStore(tmp_path)
    queued=store.add_task('Очередь','university')
    s=Session('creative','sqlite:workspace',0)
    s.started_at=(datetime.now().astimezone()-timedelta(seconds=2)).isoformat()
    store.save_session(s.to_dict(),True);store.link(s.id,queued)
    store.db.execute('UPDATE tasks SET origin_key=? WHERE id=?',('queue-source|a',queued));store.db.commit()
    note=store.add_task('Записанное в старой версии',done=True,session=s.id)
    before=store.active_session()
    store.recover_session_entries();store.recover_session_entries()
    assert [row['id'] for row in store.session_entries(s.id)]==[note]
    assert store.active_session()==before and len(store.session_tasks(s.id))==2
    assert not store.task(queued)['done']
    store.close()


def test_corrections_keep_original_and_allocate_across_midnight(tmp_path):
    store=WorkspaceStore(tmp_path);s=record();store.save_session(s)
    key=store.correct(s['id'],[[300,900]],'Перерыв')
    adjusted=store.session(s['id'])
    assert store.session(s['id'],False)['intervals']==s['intervals']
    assert adjusted['active_seconds']==1200
    assert daily_seconds([adjusted])=={('2026-11-01','creative'):300,('2026-11-02','creative'):900}
    with pytest.raises(ValueError):store.correct(s['id'],[[300,900]])
    store.undo_correction(key)
    assert store.session(s['id'])['active_seconds']==1800
    store.close()


def test_latest_correction_skips_already_removed_time_and_wall_overlap():
    assert accounting.remove_latest(100,30,[[80,100]])==[[50.0,80.0]]
    with pytest.raises(ValueError):accounting.remove_latest(100,81,[[80,100]])
    s=record()
    assert accounting.wall_range(s,datetime.fromisoformat('2026-11-01T23:55:00+03:00'),datetime.fromisoformat('2026-11-02T00:05:00+03:00'))==[[300,900]]


def test_task_repeat_children_undo_redo_and_trash(tmp_path):
    store=WorkspaceStore(tmp_path)
    root=store.add_task('Родитель','university');child=store.add_task('Подпункт',parent=root)
    store.update_task(root,repeat_days=7,due='2026-11-01',priority=2)
    store.toggle(root,True)
    next_id=store.task(root)['next_id']
    assert next_id and store.task(child)['done']
    assert len(store.tasks(show_done=True))==4
    assert store.undo() and not store.task(root)['done']
    assert len(store.tasks(show_done=True))==2
    assert store.undo(redo=True) and store.task(root)['done']
    assert len(store.tasks(show_done=True))==4
    store.trash(root)
    assert len(store.tasks(show_done=True))==2
    store.undo()
    assert len(store.tasks(show_done=True))==4
    store.toggle(child,False)
    assert not store.task(root)['done']
    store.close()


def test_import_is_idempotent_preserves_history_and_sources_and_resume(tmp_path):
    path=tmp_path/'university.md'
    path.write_text('# Пример\n- [ ] Учиться <!-- task:a -->\n    - [x] Пример <!-- task:b -->\n',encoding='utf-8')
    notes=tmp_path/'creative.md';notes.write_text('# Записи\n- [x] Сделано <!-- task:c -->\n',encoding='utf-8')
    legacy=StateStore(tmp_path)
    legacy.write('settings.json',{'paths':{'university':str(path)},'minutes':60})
    completed=record();completed['path']=str(notes);completed['favorite']=True
    completed['completed']={'c':{'text':'Сделано','parent_id':None}}
    legacy.save_session(completed)
    active=Session('university',path,60).to_dict()
    legacy.write('active_session.json',{'session':active})
    before={p:p.read_bytes() for p in [path,notes,tmp_path/'settings.json',tmp_path/'sessions.json',tmp_path/'active_session.json']}
    store=WorkspaceStore(tmp_path)
    report=store.migrate_legacy()
    assert report['tasks']==3 and report['sessions']==2
    assert store.active_session()==active
    assert store.session(completed['id'],False)==completed
    assert all(p.read_bytes()==raw for p,raw in before.items())
    assert store.migrate_legacy()==report and len(store.tasks(show_done=True))==3
    assert Path(report['backup']).is_dir()
    assert store.tasks(search='УЧИТЬСЯ',show_done=True)[0]['origin_id']=='a'
    store.close()


def test_import_of_failed_final_clear_does_not_resurrect_completed_session(tmp_path):
    legacy=StateStore(tmp_path);s=record()
    legacy.save_session(s);legacy.write('active_session.json',{'session':s})
    store=WorkspaceStore(tmp_path);store.migrate_legacy()
    assert store.active_session() is None and len(store.history())==1
    store.close()


def test_import_failure_rolls_back_and_can_retry(tmp_path,monkeypatch):
    path=tmp_path/'queue.md';path.write_text('- [ ] Пример <!-- task:x -->\n',encoding='utf-8')
    store=WorkspaceStore(tmp_path)
    original=store.db
    # Invalid second source is rejected before committing any imported tasks.
    bad=tmp_path/'bad.md';bad.write_text('- [ ] A <!-- task:x -->\n- [ ] B <!-- task:x -->\n',encoding='utf-8')
    with pytest.raises(Exception):store.migrate_legacy({'university':path,'self_development':bad})
    assert not store.meta('legacy_import_complete') and not store.tasks(show_done=True)
    assert store.migrate_legacy({'university':path})['tasks']==1
    store.close()


def test_search_inbox_saved_view_and_database_backup(tmp_path):
    store=WorkspaceStore(tmp_path/'profile')
    key=store.add_task('Изучить SQL');store.update_task(key,notes='Транзакции',labels=['база'])
    assert store.tasks(inbox=True,search='транзакции')[0]['id']==key
    store.save_view('SQL',{'search':'SQL'})
    assert json.loads(store.saved_views()[0]['filters'])=={'search':'SQL'}
    backup=tmp_path/'backup.sqlite3';store.backup(backup)
    assert backup.stat().st_size>0
    store.close()


def test_project_attribution_counts_each_second_once_after_correction(tmp_path):
    store=WorkspaceStore(tmp_path)
    a=store.add_task('A','university');b=store.add_task('B','self_development')
    s=record();store.save_session(s)
    store.attribute_until(s['id'],600,a);store.attribute_until(s['id'],1200,b);store.attribute_until(s['id'],1800,None)
    assert store.project_totals()=={'university':600,'self_development':600,None:600}
    store.correct(s['id'],[[300,900]])
    assert store.project_totals()=={'university':300,'self_development':300,None:600}
    assert store.project_totals('2026-11-01')=={'university':300,'self_development':0,None:0}
    store.close()


def test_markdown_without_ids_imports_once_and_keeps_source_unchanged(tmp_path):
    path=tmp_path/'plain.md';raw='# Задачи\n- [ ] A\n    - [ ] B\n';path.write_text(raw,encoding='utf-8')
    store=WorkspaceStore(tmp_path/'profile')
    assert store.import_markdown(path)==2
    assert store.import_markdown(path)==0
    assert len(store.tasks(show_done=True))==2 and path.read_text(encoding='utf-8')==raw
    store.close()


def test_export_and_backup_cannot_overwrite_the_live_database(tmp_path):
    store=WorkspaceStore(tmp_path);store.add_task('Сохранить задачу')
    with pytest.raises(ValueError):store.backup(store.path)
    with pytest.raises(ValueError):store.export_markdown(store.path)
    assert len(store.tasks())==1 and store.db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    store.close()
