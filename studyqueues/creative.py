"""A session journal: every row stays visible, independently of study queues."""
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QWidget, QFrame, QVBoxLayout, QHBoxLayout, QLineEdit

from .session import Session
from .widgets import TaskRow, IconButton


def create_creative_session(data_dir):
    session = Session('creative', '', 0)
    started = datetime.fromisoformat(session.started_at)
    folder = Path(data_dir) / 'creative'
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f'{started:%Y-%m-%d_%H-%M-%S}_{session.id}.md'
    created = False
    try:
        with path.open('x', encoding='utf-8', newline='\n') as file:
            created = True
            file.write(f'# Творческое занятие — {started:%d.%m.%Y %H:%M}\n\n')
    except OSError:
        if created:
            path.unlink(missing_ok=True)
        raise
    session.path = str(path)
    return session


class CreativePanel(QWidget):
    def __init__(self, owner, label, button, scroll_area):
        super().__init__()
        self.owner = owner
        self.session_id = owner.session.id
        self.groups, self.rows, self.editors, self.checkboxes, self.menus = {}, {}, {}, {}, {}
        owner.current_task = None
        owner.work_editors, owner.work_checkboxes = self.editors, self.checkboxes
        owner.work_rows, owner.work_menu_buttons = self.rows, self.menus
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        heading = QHBoxLayout()
        heading.addWidget(label('Творческий режим', 'brand'))
        heading.addStretch()
        heading.addWidget(button('Открыть записи', lambda: owner.open_notes(owner.session.path), 'ghost'))
        heading.addWidget(IconButton('refresh', owner.reload, 'Обновить записи после внешних изменений'))
        layout.addLayout(heading)
        layout.addWidget(label('Записывайте сделанное. Снимите отметку, если задача ещё в работе.', 'subtitle', True))
        panel = QFrame()
        panel.setObjectName('creativeTasks')
        content = QVBoxLayout(panel)
        content.setContentsMargins(12, 12, 12, 12)
        content.setSpacing(10)
        self.scroll = scroll_area()
        self.body = QWidget()
        self.list_layout = QVBoxLayout(self.body)
        self.list_layout.setContentsMargins(0, 0, 4, 0)
        self.list_layout.setSpacing(8)
        self.message = label('Здесь появится история задач этого занятия.\nДобавьте первую запись ниже.', 'muted', True)
        self.message.setAlignment(Qt.AlignCenter)
        self.list_layout.addWidget(self.message)
        self.list_layout.addStretch()
        self.scroll.setWidget(self.body)
        content.addWidget(self.scroll, 1)
        self.parent_line = QWidget()
        parent_layout = QHBoxLayout(self.parent_line)
        parent_layout.setContentsMargins(0, 0, 0, 0)
        self.parent_label = label('', 'muted', True)
        parent_layout.addWidget(self.parent_label, 1)
        parent_layout.addWidget(button('Новая задача', self.reset_parent, 'ghost'))
        content.addWidget(self.parent_line)
        entry_line = QHBoxLayout()
        self.entry = QLineEdit(owner.session.creative_draft['text'])
        self.entry.setMinimumHeight(42)
        self.entry.setPlaceholderText('Записать сделанное…')
        self.entry.setAccessibleName('Новая запись творческого занятия')
        self.entry.textChanged.connect(self.remember_draft)
        self.entry.returnPressed.connect(self.submit)
        entry_line.addWidget(self.entry, 1)
        self.add_button = button('Записать', self.submit, 'primary')
        self.add_button.setFixedHeight(42)
        entry_line.addWidget(self.add_button)
        content.addLayout(entry_line)
        layout.addWidget(panel, 1)
        owner.timer_caption = label('ВРЕМЯ ЗАНЯТИЯ', 'eyebrow')
        owner.timer_caption.setAlignment(Qt.AlignCenter)
        owner.timer_label = label('', 'timer')
        owner.timer_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(owner.timer_caption)
        layout.addWidget(owner.timer_label)
        actions = QHBoxLayout()
        actions.addStretch()
        owner.pause_button = button('Продолжить' if not owner.session.running else 'Пауза', owner.toggle_pause)
        owner.pause_button.setFixedWidth(owner.pause_button.fontMetrics().horizontalAdvance('Продолжить') + 32)
        actions.addWidget(owner.pause_button)
        owner.finish_button = button('Завершить занятие', owner.finish_session, 'finishSession')
        actions.addWidget(owner.finish_button)
        actions.addStretch()
        layout.addLayout(actions)
        self.sync()

    def remember_draft(self, text):
        if self.owner.session and self.owner.session.id == self.session_id:
            draft = self.owner.session.creative_draft
            draft['text'] = text
            draft.setdefault('task_id', uuid4().hex)

    def add_child(self, ident):
        if not self.owner.flush_work_edits():
            return
        self.owner.session.creative_draft['parent_id'] = ident
        self.update_parent()
        self.entry.setFocus()

    def reset_parent(self):
        self.owner.session.creative_draft['parent_id'] = None
        self.update_parent()
        self.entry.setFocus()

    def update_parent(self):
        store = self.owner.stores.get('creative')
        draft = self.owner.session.creative_draft
        parent = store.by_id.get(draft['parent_id']) if store else None
        if parent is None:
            draft['parent_id'] = None
        self.parent_label.setText(f'Подпункт к: {parent.text}' if parent else '')
        self.parent_line.setVisible(parent is not None)
        self.entry.setPlaceholderText('Записать подпункт…' if parent else 'Записать сделанное…')

    def submit(self):
        session = self.owner.session
        if not session or session.ended_at:
            return False
        parent = session.creative_draft['parent_id']
        if self.owner.add_task('creative', self.entry.text(), parent):
            self.entry.clear()
            self.entry.setFocus()
            # Clear the input in the snapshot too, without touching the journal again.
            self.owner.safe(self.owner.checkpoint)
            store = self.owner.stores['creative']
            ident = parent if parent in store.by_id else store.tasks[-1].id
            QTimer.singleShot(0, lambda: self.scroll.ensureWidgetVisible(self.groups[ident], 0, 12)
                              if self.owner.session and self.owner.session.id == self.session_id and ident in self.groups else None)
            return True
        return False

    def make_row(self, task):
        owner = self.owner
        row = TaskRow(task.text, task.completed, bool(task.parent_id))
        row.checkbox.clicked.connect(lambda checked, ident=task.id: owner.set_task_checked('creative', ident, checked))
        row.editor.saveRequested.connect(lambda text, ident=task.id, edit=row.editor: owner.save_work_text(ident, text, edit))
        row.editor.cancelled.connect(lambda edit=row.editor: owner.mark_editor_error(edit, False))
        row.menu_button.clicked.connect(lambda _, ident=task.id, widget=row: owner.task_menu('creative', ident, widget))
        self.rows[task.id] = row
        self.editors[task.id] = row.editor
        self.checkboxes[task.id] = row.checkbox
        self.menus[task.id] = row.menu_button
        return row

    def sync(self):
        store = self.owner.stores.get('creative')
        tasks = store.tasks if store else []
        wanted = {task.id for task in tasks}
        for ident, group in list(self.groups.items()):
            task = store.by_id.get(ident) if store else None
            if ident not in wanted or group.child_ids != tuple(child.id for child in task.children):
                for row in group.findChildren(TaskRow):
                    row.editor.suspended = True
                for key in (ident, *group.child_ids):
                    for mapping in (self.rows, self.editors, self.checkboxes, self.menus):
                        mapping.pop(key, None)
                self.list_layout.removeWidget(group)
                group.hide()
                group.deleteLater()
                del self.groups[ident]
        for index, task in enumerate(tasks):
            if task.id not in self.groups:
                group = QFrame()
                group.setObjectName('taskGroup')
                rows = QVBoxLayout(group)
                rows.setContentsMargins(4, 4, 4, 4)
                rows.setSpacing(2)
                for item in [task, *task.children]:
                    rows.addWidget(self.make_row(item))
                group.child_ids = tuple(child.id for child in task.children)
                self.groups[task.id] = group
            group = self.groups[task.id]
            self.list_layout.removeWidget(group)
            self.list_layout.insertWidget(index, group)
            for item in [task, *task.children]:
                row = self.rows[item.id]
                if row.editor.saved_text != item.text:
                    row.editor.setPlainText(item.text)
                    row.editor.mark_saved(item.text)
                font = row.editor.font()
                font.setStrikeOut(item.completed)
                row.editor.setFont(font)
                row.editor.adjust_height()
                row.checkbox.setChecked(item.completed)
                row.checkbox.setToolTip('Вернуть в работу' if item.completed else 'Отметить выполненным')
        self.message.setVisible(not tasks)
        self.message.setText('Здесь появится история задач этого занятия.\nДобавьте первую запись ниже.' if store
                             else 'Не удалось прочитать записи. Исправьте файл и нажмите обновление.')
        self.entry.setEnabled(store is not None and not self.owner.session.ended_at)
        self.add_button.setEnabled(self.entry.isEnabled())
        self.update_parent()

    def fit(self):
        for editor in self.editors.values():
            editor.adjust_height()

    def stop_editing(self):
        self.body.setEnabled(False)
        self.entry.setEnabled(False)
        self.add_button.setEnabled(False)
        self.owner.pause_button.setEnabled(False)
