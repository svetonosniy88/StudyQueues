"""Transactional local workspace. Legacy Markdown/JSON are import/export formats."""
from pathlib import Path
from datetime import datetime, date, timedelta
from uuid import uuid4, uuid5, NAMESPACE_URL
import hashlib
import json
import sqlite3
import shutil

from .state_store import StateStore, StateError
from .queue_store import QueueStore, IDENTITY
from . import accounting


def now():
    return datetime.now().astimezone().isoformat()


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def legacy_ids(store):
    result={}
    for task in store.by_id.values():
        match=IDENTITY.search(store.lines[task.line].rstrip('\r\n'))
        result[task.id]=match.group(1) if match else 'line-'+str(task.line)+'-'+hashlib.sha256(task.text.encode('utf-8')).hexdigest()[:20]
    return result


class WorkspaceStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / 'workspace.sqlite3'
        # A locked profile must produce a recoverable error, not block the UI
        # for sqlite's default five-second busy timeout.
        self.db = sqlite3.connect(self.path, timeout=0.25)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        version = self.db.execute('PRAGMA user_version').fetchone()[0]
        if version not in (0, 1):
            raise StateError('Неподдерживаемая версия базы; исходные данные сохранены.')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY,name TEXT NOT NULL,archived INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS tasks(
                id TEXT PRIMARY KEY, project_id TEXT REFERENCES projects(id), parent_id TEXT REFERENCES tasks(id),
                title TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '', due TEXT, priority INTEGER NOT NULL DEFAULT 0,
                repeat_days INTEGER NOT NULL DEFAULT 0, position REAL NOT NULL, done INTEGER NOT NULL DEFAULT 0,
                deleted INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, completed_at TEXT,
                origin_key TEXT UNIQUE, origin_id TEXT, next_id TEXT, labels TEXT NOT NULL DEFAULT '[]');
            CREATE INDEX IF NOT EXISTS tasks_project ON tasks(project_id,deleted,position);
            CREATE INDEX IF NOT EXISTS tasks_parent ON tasks(parent_id);
            CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,payload TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 0,favorite INTEGER NOT NULL DEFAULT 0);
            CREATE UNIQUE INDEX IF NOT EXISTS one_active_session ON sessions(active) WHERE active=1;
            CREATE TABLE IF NOT EXISTS session_tasks(session_id TEXT NOT NULL REFERENCES sessions(id),task_id TEXT NOT NULL REFERENCES tasks(id),
                title TEXT NOT NULL,PRIMARY KEY(session_id,task_id));
            CREATE TABLE IF NOT EXISTS session_entries(session_id TEXT NOT NULL REFERENCES sessions(id),task_id TEXT NOT NULL REFERENCES tasks(id),
                PRIMARY KEY(session_id,task_id));
            CREATE TABLE IF NOT EXISTS corrections(id TEXT PRIMARY KEY,session_id TEXT NOT NULL REFERENCES sessions(id),
                ranges TEXT NOT NULL,reason TEXT NOT NULL,created_at TEXT NOT NULL,undone INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS commands(id INTEGER PRIMARY KEY AUTOINCREMENT,label TEXT NOT NULL,before_state TEXT NOT NULL,
                after_state TEXT NOT NULL,undone INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS saved_views(id TEXT PRIMARY KEY,name TEXT NOT NULL,filters TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS attributions(id INTEGER PRIMARY KEY AUTOINCREMENT,session_id TEXT NOT NULL REFERENCES sessions(id),
                start_offset REAL NOT NULL,end_offset REAL NOT NULL,task_id TEXT REFERENCES tasks(id),project_id TEXT REFERENCES projects(id));
            PRAGMA user_version=1;
        ''')
        with self.db:
            self.db.executemany('INSERT OR IGNORE INTO projects(id,name) VALUES (?,?)',
                                [('university','Университет'),('self_development','Свои проекты')])

    def close(self):
        self.db.close()

    def meta(self, key, default=None):
        row = self.db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_meta(self, key, value):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',(key,encode(value)))

    def projects(self, include_archived=False):
        return [dict(r) for r in self.db.execute('SELECT * FROM projects WHERE archived=0 OR ? ORDER BY rowid',(include_archived,))]

    def add_project(self, name):
        name = name.strip()
        if not name:
            raise ValueError('Введите название проекта.')
        ident = uuid4().hex
        with self.db:
            self.db.execute('INSERT INTO projects(id,name) VALUES (?,?)',(ident,name))
        return ident

    def task(self, ident):
        row = self.db.execute('SELECT * FROM tasks WHERE id=?',(ident,)).fetchone()
        return dict(row) if row else None

    def tasks(self, project=None, inbox=False, search='', show_done=False, trash=False, due_today=False, priority=False):
        conditions, args = ['t.deleted=?'], [int(trash)]
        if project:
            conditions.append('t.project_id=?'); args.append(project)
        if inbox:
            conditions.append('t.project_id IS NULL')
        if not show_done and not trash:
            conditions.append('t.done=0')
        if search.strip():
            # Python casefold keeps Cyrillic search independent of SQLite's ASCII LIKE.
            pass
        if due_today:
            conditions.append('t.due IS NOT NULL AND t.due<=?'); args.append(date.today().isoformat())
        if priority:
            conditions.append('t.priority>=2')
        rows = [dict(r) for r in self.db.execute('SELECT t.*,p.name AS project_name FROM tasks t LEFT JOIN projects p ON p.id=t.project_id WHERE '+' AND '.join(conditions)+' ORDER BY t.position,t.created_at',args)]
        query = search.strip().casefold()
        if query:
            rows = [r for r in rows if query in ' '.join(str(r.get(k) or '') for k in ('title','notes','project_name','labels')).casefold()]
        return rows

    def _snapshot(self, ids):
        return {ident:self.task(ident) for ident in ids}

    def _record(self, label, before, after):
        self.db.execute('DELETE FROM commands WHERE undone=1')
        self.db.execute('INSERT INTO commands(label,before_state,after_state) VALUES (?,?,?)',(label,encode(before),encode(after)))
        self.db.execute('DELETE FROM commands WHERE id NOT IN (SELECT id FROM commands ORDER BY id DESC LIMIT 100)')

    def add_task(self, title, project=None, parent=None, done=False, session=None, recorded_entry=False):
        title = title.strip()
        if not title:
            raise ValueError('Введите текст задачи.')
        if parent:
            root = self.task(parent)
            if not root or root['parent_id'] or root['deleted']:
                raise ValueError('Подпункт можно добавить к существующей основной задаче.')
            project = root['project_id']
        ident = uuid4().hex
        if parent:
            position=self.db.execute('SELECT COALESCE(MAX(position),0)+1 FROM tasks WHERE parent_id=?',(parent,)).fetchone()[0]
        else:
            current=None
            if session:
                record=self.session(session,False)
                current=self.task(record.get('view_task_id')) if record.get('view_task_id') else None
                if current and current['parent_id']:current=self.task(current['parent_id'])
            if current:
                following=self.db.execute('SELECT MIN(position) FROM tasks WHERE parent_id IS NULL AND deleted=0 AND position>?',(current['position'],)).fetchone()[0]
                position=(current['position']+following)/2 if following is not None else current['position']+1
            else:position=self.db.execute('SELECT COALESCE(MIN(position),1)-1 FROM tasks WHERE parent_id IS NULL').fetchone()[0]
        before = {ident:None}
        if parent: before[parent] = self.task(parent)
        with self.db:
            self.db.execute('INSERT INTO tasks(id,project_id,parent_id,title,position,done,created_at,completed_at) VALUES (?,?,?,?,?,?,?,?)',
                            (ident,project,parent,title,position,int(done),now(),now() if done else None))
            if parent and not done:
                self.db.execute('UPDATE tasks SET done=0,completed_at=NULL WHERE id=?',(parent,))
            self._record('Добавление задачи',before,self._snapshot(before))
            if session: self._link(session,ident)
            if recorded_entry:
                self.db.execute('INSERT INTO session_entries VALUES (?,?)',(session,ident))
        return ident

    def add_session_entry(self, session, text):
        """Explicitly recorded work; looking at a task never creates an entry."""
        return self.add_task(text,done=True,session=session,recorded_entry=True)

    def session_entries(self, session):
        return [dict(row) for row in self.db.execute('''
            SELECT t.*,t.title AS snapshot_title FROM session_entries e
            JOIN tasks t ON t.id=e.task_id WHERE e.session_id=? ORDER BY t.created_at,e.rowid
        ''',(session,))]

    def recover_session_entries(self):
        """Classify pre-existing written notes without importing selected queue tasks.

        Old creative Markdown files have explicit provenance. Earlier workspace
        versions created written notes during the session with no import origin.
        Keep task links and historical payloads intact; only add note provenance.
        """
        if self.meta('session_entries_ready'):return
        with self.db:
            for row in self.db.execute('SELECT id,payload FROM sessions').fetchall():
                record=json.loads(row['payload'])
                if record['queue']!='creative':continue
                source=str(Path(record['path']).resolve())+'|' if record['path']!='sqlite:workspace' else None
                for task in self.session_tasks(row['id']):
                    original_note=source and task['origin_key'] and task['origin_key'].startswith(source)
                    written_note=not task['origin_key'] and task['created_at']>=record['started_at'] and (not record['ended_at'] or task['created_at']<=record['ended_at'])
                    if original_note or written_note:
                        self.db.execute('INSERT OR IGNORE INTO session_entries VALUES (?,?)',(row['id'],task['id']))
            self.db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',('session_entries_ready',encode(True)))

    def update_task(self, ident, **fields):
        allowed = {'title','notes','project_id','due','priority','repeat_days','labels','position'}
        if not fields or not fields.keys() <= allowed:
            raise ValueError('Некорректные поля задачи.')
        if 'title' in fields:
            fields['title'] = fields['title'].strip()
            if not fields['title']: raise ValueError('Текст задачи не может быть пустым.')
        if fields.get('due'): date.fromisoformat(fields['due'])
        if 'priority' in fields and fields['priority'] not in (0,1,2,3): raise ValueError('Некорректный приоритет.')
        if 'repeat_days' in fields and (type(fields['repeat_days']) is not int or not 0<=fields['repeat_days']<=3660): raise ValueError('Некорректный повтор.')
        if 'labels' in fields and not isinstance(fields['labels'],str): fields['labels']=encode(fields['labels'])
        before=self._snapshot([ident])
        if not before[ident] or before[ident]['deleted']: raise ValueError('Задача не найдена.')
        affected=[ident]
        if 'project_id' in fields and not before[ident]['parent_id']:
            affected += [r[0] for r in self.db.execute('SELECT id FROM tasks WHERE parent_id=?',(ident,))]
            before=self._snapshot(affected)
        with self.db:
            self.db.execute('UPDATE tasks SET '+','.join(k+'=?' for k in fields)+' WHERE id=?',(*fields.values(),ident))
            if len(affected)>1:
                self.db.execute('UPDATE tasks SET project_id=? WHERE parent_id=?',(fields['project_id'],ident))
            self._record('Изменение задачи',before,self._snapshot(affected))

    def _link(self, session, ident):
        task=self.task(ident)
        self.db.execute('INSERT OR IGNORE INTO session_tasks VALUES (?,?,?)',(session,ident,task['title']))

    def link(self, session, ident):
        with self.db: self._link(session,ident)

    def session_tasks(self, ident):
        return [dict(r) for r in self.db.execute('SELECT t.*,s.title AS snapshot_title FROM session_tasks s JOIN tasks t ON t.id=s.task_id WHERE s.session_id=? ORDER BY t.position',(ident,))]

    def toggle(self, ident, done, session=None):
        task=self.task(ident)
        if not task or task['deleted']: raise ValueError('Задача не найдена.')
        children=[r[0] for r in self.db.execute('SELECT id FROM tasks WHERE parent_id=? AND deleted=0',(ident,))]
        ids=[ident,*children]
        if task['parent_id']: ids.append(task['parent_id'])
        before=self._snapshot(ids)
        with self.db:
            for key in [ident,*children]:
                self.db.execute('UPDATE tasks SET done=?,completed_at=? WHERE id=?',(int(done),now() if done else None,key))
                if session: self._link(session,key)
            if task['parent_id']:
                pending=self.db.execute('SELECT COUNT(*) FROM tasks WHERE parent_id=? AND deleted=0 AND done=0',(task['parent_id'],)).fetchone()[0]
                self.db.execute('UPDATE tasks SET done=?,completed_at=? WHERE id=?',(int(pending==0),now() if pending==0 else None,task['parent_id']))
                if session: self._link(session,task['parent_id'])
            if done and not task['done'] and task['repeat_days'] and not task['parent_id'] and not task['next_id']:
                new=uuid4().hex; before[new]=None
                next_date=max(date.today(),date.fromisoformat(task['due']) if task['due'] else date.today())+timedelta(days=task['repeat_days'])
                copy={**task,'id':new,'done':0,'completed_at':None,'due':next_date.isoformat(),'created_at':now(),'next_id':None,'origin_key':None,'origin_id':None,'position':task['position']+0.01}
                self._put_task(copy)
                self.db.execute('UPDATE tasks SET next_id=? WHERE id=?',(new,ident))
                for child in children:
                    child_copy=self.task(child); new_child=uuid4().hex; before[new_child]=None
                    child_copy.update(id=new_child,parent_id=new,done=0,completed_at=None,created_at=now(),origin_key=None,origin_id=None,next_id=None)
                    self._put_task(child_copy)
            self._record('Выполнение задачи',before,self._snapshot(before))

    def trash(self, ident, restore=False):
        task=self.task(ident)
        if not task: raise ValueError('Задача не найдена.')
        ids=[ident,*[r[0] for r in self.db.execute('SELECT id FROM tasks WHERE parent_id=?',(ident,))]]
        if restore and task['parent_id']: ids.append(task['parent_id'])
        before=self._snapshot(ids)
        with self.db:
            for key in ids:self.db.execute('UPDATE tasks SET deleted=? WHERE id=?',(int(not restore),key))
            self._record('Восстановление задачи' if restore else 'Перемещение в корзину',before,self._snapshot(ids))

    def _put_task(self, row):
        keys=list(row)
        self.db.execute('INSERT INTO tasks('+','.join(keys)+') VALUES ('+','.join('?' for _ in keys)+') ON CONFLICT(id) DO UPDATE SET '+','.join(k+'=excluded.'+k for k in keys if k!='id'),list(row.values()))

    def undo(self, redo=False):
        row=self.db.execute('SELECT * FROM commands WHERE undone=? ORDER BY id '+('ASC' if redo else 'DESC')+' LIMIT 1',(int(redo),)).fetchone()
        if not row:return None
        state=json.loads(row['after_state'] if redo else row['before_state'])
        with self.db:
            for ident,task in sorted(state.items(),key=lambda item:bool(item[1] and item[1]['parent_id'])):
                if task:self._put_task(task)
                else:self.db.execute('UPDATE tasks SET deleted=1 WHERE id=?',(ident,))
            self.db.execute('UPDATE commands SET undone=? WHERE id=?',(int(not redo),row['id']))
        return row['label']

    def reorder(self, ids):
        if len(ids)!=len(set(ids)):raise ValueError('Повторяющиеся задачи в порядке.')
        before=self._snapshot(ids)
        if any(not t or t['deleted'] for t in before.values()):raise ValueError('Задача уже недоступна.')
        if len({t['parent_id'] for t in before.values()})>1:raise ValueError('Перемещайте задачи в пределах одного уровня.')
        slots=sorted(t['position'] for t in before.values())
        with self.db:
            for ident,position in zip(ids,slots):self.db.execute('UPDATE tasks SET position=? WHERE id=?',(position,ident))
            self._record('Порядок задач',before,self._snapshot(ids))

    def attribute_until(self, session, total, task=None):
        last=self.db.execute('SELECT * FROM attributions WHERE session_id=? ORDER BY id DESC LIMIT 1',(session,)).fetchone()
        offset=last['end_offset'] if last else 0.0
        if total<=offset+1e-7:return
        row=self.task(task) if task else None
        project=row['project_id'] if row else None
        with self.db:
            if last and last['task_id']==task and last['project_id']==project:
                self.db.execute('UPDATE attributions SET end_offset=? WHERE id=?',(total,last['id']))
            else:self.db.execute('INSERT INTO attributions(session_id,start_offset,end_offset,task_id,project_id) VALUES (?,?,?,?,?)',(session,offset,total,task,project))

    def project_totals(self, day=None):
        from .session import daily_seconds
        result={}
        for row in self.db.execute('SELECT id FROM sessions'):
            record=self.session(row['id'],False);total=record['active_seconds']
            if not total:continue
            groups={};covered=[]
            for attr in self.db.execute('SELECT * FROM attributions WHERE session_id=?',(row['id'],)):
                span=[attr['start_offset'],min(total,attr['end_offset'])]
                if span[1]>span[0]:groups.setdefault(attr['project_id'],[]).append(span);covered.append(span)
            missing=accounting.subtract([[0,total]],covered)
            groups.setdefault(None,[]).extend(missing)
            for project,ranges in groups.items():
                keep=accounting.subtract(ranges,self.excluded(row['id']))
                if not keep:continue
                if day:
                    adjusted=accounting.adjusted_record(record,accounting.subtract([[0,total]],keep))
                    seconds=sum(v for (d,_),v in daily_seconds([adjusted]).items() if d==day)
                else:seconds=accounting.duration(keep)
                result[project]=result.get(project,0)+seconds
        return result

    def save_session(self, record, active=False):
        StateStore.validate_session(record)
        with self.db:
            if active:
                other=self.db.execute('SELECT id FROM sessions WHERE active=1 AND id<>?',(record['id'],)).fetchone()
                if other:raise ValueError('Сначала завершите текущее занятие.')
            prior=self.db.execute('SELECT favorite FROM sessions WHERE id=?',(record['id'],)).fetchone()
            favorite=prior[0] if prior else int(record.get('favorite',False))
            self.db.execute('INSERT INTO sessions VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,active=excluded.active,favorite=excluded.favorite',
                            (record['id'],encode(record),int(active and not record['ended_at']),favorite))

    def active_session(self):
        row=self.db.execute('SELECT payload FROM sessions WHERE active=1').fetchone()
        return json.loads(row[0]) if row else None

    def session(self, ident, adjusted=True):
        row=self.db.execute('SELECT * FROM sessions WHERE id=?',(ident,)).fetchone()
        if not row:raise ValueError('Занятие не найдено.')
        record={**json.loads(row['payload']),'favorite':bool(row['favorite'])}
        return accounting.adjusted_record(record,self.excluded(ident)) if adjusted else record

    def history(self):
        ids=[r[0] for r in self.db.execute("SELECT id FROM sessions WHERE active=0 AND json_extract(payload,'$.ended_at') IS NOT NULL ORDER BY json_extract(payload,'$.started_at') DESC")]
        return [self.session(ident) for ident in ids]

    def excluded(self, ident):
        ranges=[]
        for row in self.db.execute('SELECT ranges FROM corrections WHERE session_id=? AND undone=0',(ident,)):
            ranges.extend(json.loads(row[0]))
        return accounting.union(ranges)

    def correct(self, ident, ranges, reason='Забыл поставить на паузу'):
        record=self.session(ident,False)
        ranges=accounting.union(ranges)
        if ranges and ranges[-1][1]>record['active_seconds']+1e-6:raise ValueError('Поправка выходит за время занятия.')
        fresh=accounting.subtract(ranges,self.excluded(ident))
        if accounting.duration(fresh)<0.001:raise ValueError('Этот перерыв уже исключён или не пересекается с рабочим временем.')
        key=uuid4().hex
        with self.db:
            self.db.execute('INSERT INTO corrections(id,session_id,ranges,reason,created_at) VALUES (?,?,?,?,?)',(key,ident,encode(fresh),reason.strip() or 'Ручная поправка',now()))
        return key

    def corrections(self, ident):
        return [dict(r) for r in self.db.execute('SELECT * FROM corrections WHERE session_id=? ORDER BY created_at DESC',(ident,))]

    def undo_correction(self, ident):
        with self.db:self.db.execute('UPDATE corrections SET undone=1 WHERE id=?',(ident,))

    def favorite(self, ident, value):
        with self.db:self.db.execute('UPDATE sessions SET favorite=? WHERE id=?',(int(value),ident))

    def save_view(self, name, filters):
        with self.db:self.db.execute('INSERT INTO saved_views VALUES (?,?,?)',(uuid4().hex,name,encode(filters)))

    def saved_views(self):
        return [dict(r) for r in self.db.execute('SELECT * FROM saved_views ORDER BY rowid')]

    def export_markdown(self, path, session=None):
        if Path(path).resolve()==self.path.resolve():raise ValueError('Выберите отдельный Markdown-файл для экспорта.')
        if session:
            record=self.session(session); tasks=self.session_entries(session) if record['queue']=='creative' else self.session_tasks(session)
            # Completed snapshots remain authoritative after a task is renamed or trashed.
            lines=[f"# Занятие — {record['started_at'][:10]}\n",f"\nВ работе: {int(record['active_seconds']//60)} мин\n\n"]
            for item in record['completed'].values():lines.append(f"- [x] {item['text']}\n")
            completed=set(record['completed'])
            for task in tasks:
                if task['id'] not in completed:lines.append(f"- [ ] {task['snapshot_title']}\n")
        else:
            lines=['# Задачи StudyQueues\n\n']
            tasks=self.tasks(show_done=True); by_parent={}
            for task in tasks:by_parent.setdefault(task['parent_id'],[]).append(task)
            for root in by_parent.get(None,[]):
                for task in [root,*by_parent.get(root['id'],[])]:
                    lines.append(f"{'    ' if task['parent_id'] else ''}- [{'x' if task['done'] else ' '}] {task['title']} <!-- task:{task['id']} -->\n")
        Path(path).write_text(''.join(lines),encoding='utf-8')

    def backup(self, destination):
        if Path(destination).resolve()==self.path.resolve():raise ValueError('Выберите другой файл для резервной копии.')
        with sqlite3.connect(destination) as target:self.db.backup(target)

    def import_markdown(self,path,project=None):
        path=str(Path(path).resolve());source=QueueStore(path,self.directory/'backups');count=0
        logical=legacy_ids(source)
        with self.db:
            for root in source.tasks:
                for task in [root,*root.children]:
                    origin=f'{path}|{logical[task.id]}'
                    if self.db.execute('SELECT 1 FROM tasks WHERE origin_key=?',(origin,)).fetchone():continue
                    parent=None
                    if task.parent_id:
                        row=self.db.execute('SELECT id FROM tasks WHERE origin_key=?',(f'{path}|{logical[task.parent_id]}',)).fetchone()
                        parent=row[0] if row else None
                    ident=uuid5(NAMESPACE_URL,origin).hex
                    position=self.db.execute('SELECT COALESCE(MAX(position),0)+1 FROM tasks').fetchone()[0]
                    self.db.execute('INSERT INTO tasks(id,project_id,parent_id,title,position,done,created_at,origin_key,origin_id) VALUES (?,?,?,?,?,?,?,?,?)',
                                    (ident,project,parent,task.text,position,int(task.completed),now(),origin,logical[task.id]));count+=1
        return count

    def migrate_legacy(self, paths=None):
        if self.meta('legacy_import_complete'):return self.meta('legacy_import_complete')
        legacy=StateStore(self.directory)
        settings=legacy.read('settings.json',{'version':1,'paths':{},'minutes':60})
        legacy.validate_settings(settings)
        history=legacy.history()
        active=legacy.read('active_session.json',{'version':1,'session':None}).get('session')
        if active:legacy.validate_session(active)
        sources={}
        for project,path in (paths or settings['paths']).items():
            if project in ('university','self_development') and Path(path).exists():sources[str(Path(path).resolve())]=project
        records=[*history,*([active] if active else [])]
        for record in records:
            if record['queue']=='creative' and Path(record['path']).is_file():sources.setdefault(str(Path(record['path']).resolve()),None)
        stores={path:QueueStore(path,self.directory/'backups') for path in sources}
        files=[self.directory/name for name in ('settings.json','sessions.json','active_session.json') if (self.directory/name).exists()]
        files += [Path(p) for p in sources]
        snapshot=self.directory/'backups'/('before-workspace-'+uuid4().hex)
        snapshot.mkdir(parents=True)
        manifest=[]
        for i,file in enumerate(files):
            raw=file.read_bytes(); name=f'{i:03}-{file.name}'
            (snapshot/name).write_bytes(raw)
            manifest.append({'source':str(file),'backup':name,'sha256':hashlib.sha256(raw).hexdigest()})
        (snapshot/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
        mapping={}
        with self.db:
            for path,store in stores.items():
                project=sources[path]
                logical=legacy_ids(store)
                for root in store.tasks:
                    for task in [root,*root.children]:
                        origin=f'{path}|{logical[task.id]}'
                        ident=uuid5(NAMESPACE_URL,origin).hex
                        mapping[(path,task.id)]=ident
                        parent=mapping[(path,task.parent_id)] if task.parent_id else None
                        self.db.execute('INSERT INTO tasks(id,project_id,parent_id,title,position,done,created_at,origin_key,origin_id) VALUES (?,?,?,?,?,?,?,?,?)',
                            (ident,project,parent,task.text,task.line,int(task.completed),now(),origin,logical[task.id]))
            history_by_id={r['id']:r for r in history}
            imported={r['id']:r for r in history}
            if active and not (active['id'] in history_by_id and history_by_id[active['id']]['ended_at']):imported[active['id']]=active
            for record in imported.values():
                is_active=bool(active and active['id']==record['id'] and not record['ended_at'])
                self.db.execute('INSERT INTO sessions VALUES (?,?,?,?)',(record['id'],encode(record),int(is_active),int(record.get('favorite',False))))
                if record['active_seconds']:
                    self.db.execute('INSERT INTO attributions(session_id,start_offset,end_offset,project_id) VALUES (?,?,?,?)',(record['id'],0,record['active_seconds'],record['queue'] if record['queue'] in ('university','self_development') else None))
                source=str(Path(record['path']).resolve())
                for old_id,item in record['completed'].items():
                    ident=mapping.get((source,old_id))
                    if ident:self.db.execute('INSERT OR IGNORE INTO session_tasks VALUES (?,?,?)',(record['id'],ident,item['text']))
            report={'tasks':len(mapping),'sessions':len(imported),'active':bool(self.db.execute('SELECT 1 FROM sessions WHERE active=1').fetchone()),'backup':str(snapshot)}
            if self.db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0]!=len(mapping):raise StateError('Проверка числа импортированных задач не прошла.')
            for entry in manifest:
                if hashlib.sha256(Path(entry['source']).read_bytes()).hexdigest()!=entry['sha256']:
                    raise StateError('Исходные данные изменились во время импорта. Закройте старую версию и повторите перенос в новую базу.')
            self.db.execute('INSERT INTO meta VALUES (?,?)',('legacy_import_complete',encode(report)))
        return report
