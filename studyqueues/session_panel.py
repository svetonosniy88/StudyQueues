"""The same session surface in the full and compact native window."""
from PySide6.QtCore import Qt, QSignalBlocker, QTimer
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (QWidget, QFrame, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QComboBox, QTreeWidgetItem, QAbstractItemView, QStackedWidget,
    QSizePolicy, QSizeGrip, QScrollArea)
from .ui import label, button, clock_text
from .widgets import CapsuleScrollBar


class SessionPanel(QWidget):
    def __init__(self, owner):
        from .workspace_ui import CardTree, TaskDelegate
        super().__init__(owner)
        self.owner = owner
        self.setObjectName('focusContent')
        self.list_signature = None
        self.setup_signature = None
        self.notes_open = False
        outer = QHBoxLayout(self)
        outer.setContentsMargins(20, 14, 20, 12)
        self.center = QWidget()
        self.center.setMaximumWidth(580)
        outer.addStretch(1)
        outer.addWidget(self.center, 100)
        outer.addStretch(1)
        layout = QVBoxLayout(self.center)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        heading = QHBoxLayout()
        self.heading = label('Занятие', 'brand')
        heading.addWidget(self.heading)
        heading.addStretch()
        self.back = button('Компактный вид', owner.toggle_focus, 'ghost')
        heading.addWidget(self.back)
        layout.addLayout(heading)
        self.restored = label('Занятие сохранено на паузе. Время вне приложения не учтено.', 'muted', True)
        layout.addWidget(self.restored)

        self.mode_picker = QFrame()
        self.mode_picker.setObjectName('modePicker')
        modes = QHBoxLayout(self.mode_picker)
        modes.setContentsMargins(4, 4, 4, 4)
        modes.setSpacing(3)
        self.mode_buttons = {}
        for key, text in [('free', 'Свободная работа'), ('queue', 'По очереди')]:
            control = button(text, lambda _, mode=key: owner.choose_mode(mode), 'segment')
            control.setCheckable(True)
            control.setMinimumHeight(38)
            modes.addWidget(control, 1)
            self.mode_buttons[key] = control
        layout.addWidget(self.mode_picker)

        self.pages = QStackedWidget()
        layout.addWidget(self.pages, 1)
        self.setup = QWidget()
        setup = QVBoxLayout(self.setup)
        setup.setContentsMargins(0, 6, 0, 0)
        setup.setSpacing(10)
        setup.addWidget(label('Очередь', 'muted'))
        self.queue_choice = QComboBox()
        self.queue_choice.setAccessibleName('Очередь для занятия')
        self.queue_choice.currentIndexChanged.connect(self.choose_queue)
        setup.addWidget(self.queue_choice)
        setup.addWidget(label('Начать с задачи', 'muted'))
        self.start_tasks = CardTree()
        self.start_tasks.setObjectName('workspaceTasks')
        self.start_tasks.setHeaderHidden(True)
        self.start_tasks.setRootIsDecorated(False)
        self.start_tasks.setItemDelegate(TaskDelegate(self.start_tasks, checks=False))
        self.start_tasks.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.start_tasks.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.start_tasks.setVerticalScrollBar(CapsuleScrollBar(self.start_tasks))
        self.start_tasks.itemSelectionChanged.connect(self.choose_start_task)
        setup.addWidget(self.start_tasks, 1)
        self.no_tasks = label('В очереди нет невыполненных задач. Добавьте их в разделе «Очереди».', 'muted', True)
        setup.addWidget(self.no_tasks)
        setup.addWidget(label('План времени', 'muted'))
        duration = QHBoxLayout()
        duration.setSpacing(7)
        self.presets = {}
        for minutes in (25, 45, 60):
            control = button(f'{minutes} мин', lambda _, n=minutes: self.duration.setText(str(n)), 'durationPreset')
            control.setCheckable(True)
            self.presets[minutes] = control
            duration.addWidget(control)
        self.duration = QLineEdit(str(owner.prepared_minutes))
        self.duration.setValidator(QIntValidator(1, 480, self))
        self.duration.setAccessibleName('Длительность занятия в минутах')
        self.duration.setAlignment(Qt.AlignCenter)
        self.duration.setFixedWidth(66)
        self.duration.textChanged.connect(self.duration_changed)
        duration.addWidget(self.duration)
        duration.addWidget(label('мин', 'muted'))
        duration.addStretch()
        setup.addLayout(duration)
        setup.addWidget(self.separator())
        launch = QHBoxLayout()
        self.preparation_status = label('', 'muted', True)
        launch.addWidget(self.preparation_status, 1)
        self.start = button('Начать', owner.start_prepared, 'primary')
        self.start.setMinimumWidth(115)
        launch.addWidget(self.start)
        setup.addLayout(launch)
        self.pages.addWidget(self.setup)

        self.work = QWidget()
        work = QVBoxLayout(self.work)
        work.setContentsMargins(0, 0, 0, 0)
        work.setSpacing(6)
        self.clock_area = QWidget()
        self.clock_area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        clock = QVBoxLayout(self.clock_area)
        clock.setContentsMargins(0, 3, 0, 0)
        clock.setSpacing(2)
        self.status = label('', 'clockStatus')
        self.status.setAlignment(Qt.AlignCenter)
        self.timer_label = label('00:00', 'largeClock')
        self.timer_label.setAlignment(Qt.AlignCenter)
        self.timer_label.setMinimumHeight(108)
        self.timer_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.caption = label('', 'muted', True)
        self.caption.setAlignment(Qt.AlignCenter)
        clock.addWidget(self.status)
        clock.addWidget(self.timer_label)
        clock.addWidget(self.caption)
        actions = QHBoxLayout()
        actions.setSpacing(10)
        actions.setContentsMargins(0, 12, 0, 0)
        actions.addStretch()
        self.pause = button('Начать', owner.pause_or_start, 'primary')
        self.pause.setMinimumWidth(130)
        self.finish = button('Завершить', owner.finish_session, 'finishSession')
        self.finish.setMinimumWidth(130)
        actions.addWidget(self.pause)
        actions.addWidget(self.finish)
        actions.addStretch()
        clock.addLayout(actions)
        correction = QHBoxLayout()
        correction.addStretch()
        self.correct = button('Исправить время', owner.correct_time, 'ghost')
        correction.addWidget(self.correct)
        correction.addStretch()
        clock.addLayout(correction)
        work.addWidget(self.clock_area)
        work.addWidget(self.separator())
        self.details_scroll = QScrollArea()
        self.details_scroll.setWidgetResizable(True)
        self.details_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.details_scroll.setVerticalScrollBar(CapsuleScrollBar(self.details_scroll))
        self.details_scroll.setMinimumHeight(40)
        details_body = QWidget()
        details_body.setObjectName('scrollBody')
        self.details_scroll.setWidget(details_body)
        details = QVBoxLayout(details_body)
        details.setContentsMargins(0, 0, 2, 0)
        details.setSpacing(8)
        work.addWidget(self.details_scroll, 1)
        row = QHBoxLayout()
        self.log_heading = label('Сделано за занятие', 'brand')
        row.addWidget(self.log_heading, 1)
        self.count = label('', 'muted')
        row.addWidget(self.count)
        details.addLayout(row)
        self.title = label('', 'focusTitle', True)
        self.title.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self.project_label = label('', 'muted', True)
        details.addWidget(self.title)
        details.addWidget(self.project_label)
        self.tasks = CardTree()
        self.tasks.setObjectName('sessionEntries')
        self.tasks.setHeaderHidden(True)
        self.tasks.setRootIsDecorated(False)
        self.tasks.setItemDelegate(TaskDelegate(self.tasks, strike_done=False, cards=False))
        self.tasks.setWordWrap(True)
        self.tasks.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.tasks.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.tasks.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tasks.itemChanged.connect(self.changed)
        self.tasks.itemDoubleClicked.connect(lambda item, _: owner.edit_task(item.data(0, Qt.UserRole)))
        details.addWidget(self.tasks)
        self.empty = label('', 'focusEmpty', True)
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setMinimumHeight(85)
        details.addWidget(self.empty)
        self.queue_actions = QWidget()
        queue_actions = QHBoxLayout(self.queue_actions)
        queue_actions.setContentsMargins(0, 0, 0, 0)
        self.done_button = button('Выполнено', self.complete_current, 'primary')
        self.skip_button = button('Пропустить', self.skip_current, 'ghost')
        self.undo_button = button('Отменить', owner.undo_queue_action, 'ghost')
        for control in (self.done_button, self.skip_button, self.undo_button):
            queue_actions.addWidget(control)
        queue_actions.addStretch()
        work.addWidget(self.queue_actions)
        self.next_task = label('', 'muted', True)
        details.addWidget(self.next_task)
        self.notes_toggle = button('Заметки занятия', self.toggle_notes, 'ghost')
        work.addWidget(self.notes_toggle)
        self.queue_notes = CardTree()
        self.queue_notes.setObjectName('sessionEntries')
        self.queue_notes.setHeaderHidden(True)
        self.queue_notes.setRootIsDecorated(False)
        self.queue_notes.setItemDelegate(TaskDelegate(self.queue_notes, checks=False, cards=False))
        self.queue_notes.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        details.addWidget(self.queue_notes)
        details.addStretch(1)
        self.entry_row = QWidget()
        entry_row = QHBoxLayout(self.entry_row)
        entry_row.setContentsMargins(0, 0, 0, 0)
        self.entry = QLineEdit()
        self.entry.setPlaceholderText('Что сделал?')
        self.entry.setAccessibleName('Запись занятия')
        self.entry.setText(owner.store.meta('focus_draft', ''))
        self.entry.returnPressed.connect(self.add)
        self.entry.textChanged.connect(self.draft_changed)
        entry_row.addWidget(self.entry, 1)
        self.record = button('Записать', self.add)
        entry_row.addWidget(self.record)
        work.addWidget(self.entry_row)
        self.saved = label('', 'savedStatus')
        work.addWidget(self.saved)
        self.pages.addWidget(self.work)

        self.result = QWidget()
        result = QVBoxLayout(self.result)
        result.setContentsMargins(0, 8, 0, 0)
        result.setSpacing(12)
        self.result_heading = label('Занятие завершено', 'heading')
        self.result_heading.setAlignment(Qt.AlignCenter)
        result.addWidget(self.result_heading)
        self.result_mode = label('', 'muted')
        self.result_mode.setAlignment(Qt.AlignCenter)
        result.addWidget(self.result_mode)
        self.result_clock = label('', 'resultClock')
        self.result_clock.setAlignment(Qt.AlignCenter)
        result.addWidget(self.result_clock)
        self.result_caption = label('Рабочее время · сохранено в истории', 'muted')
        self.result_caption.setAlignment(Qt.AlignCenter)
        result.addWidget(self.result_caption)
        result.addWidget(self.separator())
        result.addWidget(label('Сделано за занятие', 'brand'))
        self.result_list = CardTree()
        self.result_list.setObjectName('sessionEntries')
        self.result_list.setHeaderHidden(True)
        self.result_list.setRootIsDecorated(False)
        self.result_list.setItemDelegate(TaskDelegate(self.result_list, checks=False, cards=False))
        self.result_list.setVerticalScrollBar(CapsuleScrollBar(self.result_list))
        result.addWidget(self.result_list, 1)
        self.result_empty = label('Занятие сохранено без записей.', 'muted')
        result.addWidget(self.result_empty)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(button('Новое занятие', owner.new_session, 'primary'))
        row.addWidget(button('История', lambda: owner.set_view('history')))
        row.addStretch()
        result.addLayout(row)
        result.addWidget(button('Исправить итоговое время', lambda: owner.correct_time(owner.result_id), 'ghost'))
        self.pages.addWidget(self.result)
        grip = QHBoxLayout()
        grip.addStretch()
        grip.addWidget(QSizeGrip(owner))
        layout.addLayout(grip)

    @staticmethod
    def separator():
        frame = QFrame()
        frame.setObjectName('separator')
        frame.setFixedHeight(1)
        return frame

    def draft_changed(self):
        self.owner.draft_timer.start()
        self.saved.setText('Сохраняется черновик…')

    def duration_changed(self):
        valid = self.duration.hasAcceptableInput()
        if valid:
            self.owner.prepared_minutes = int(self.duration.text())
        for minutes, control in self.presets.items():
            control.setChecked(valid and minutes == self.owner.prepared_minutes)
        self.start.setEnabled(valid and self.start_tasks.currentItem() is not None)
        self.preparation_status.setText(
            f'{self.queue_choice.currentText()} · {self.duration.text()} мин\nЗанятие ещё не началось'
            if valid else 'Укажите от 1 до 480 минут. Занятие ещё не началось.')
        if hasattr(self.owner, 'draft_timer'):
            self.owner.draft_timer.start()

    def choose_queue(self):
        self.owner.prepared_queue = self.queue_choice.currentData()
        self.owner.prepared_task = None
        self.setup_signature = None
        self.refresh_setup()

    def choose_start_task(self):
        item = self.start_tasks.currentItem()
        self.owner.prepared_task = item.data(0, Qt.UserRole) if item else None
        self.duration_changed()

    def refresh_setup(self):
        projects = self.owner.store.projects()
        signature = tuple((p['id'], p['name']) for p in projects)
        if signature != getattr(self, 'project_signature', None):
            self.project_signature = signature
            with QSignalBlocker(self.queue_choice):
                self.queue_choice.clear()
                for project in projects:
                    self.queue_choice.addItem(project['name'], project['id'])
        index = self.queue_choice.findData(self.owner.prepared_queue)
        if index < 0:
            index = 0
            self.owner.prepared_queue = self.queue_choice.itemData(index)
        with QSignalBlocker(self.queue_choice):
            self.queue_choice.setCurrentIndex(index)
        rows = [t for t in self.owner.store.tasks(project=self.owner.prepared_queue)
                if not t['parent_id']] if self.owner.prepared_queue else []
        signature = tuple((r['id'], r['title'], r['position']) for r in rows)
        selected = self.owner.prepared_task
        if signature != self.setup_signature:
            self.setup_signature = signature
            with QSignalBlocker(self.start_tasks):
                self.start_tasks.clear()
                for row in rows:
                    item = QTreeWidgetItem(self.start_tasks, [row['title']])
                    item.setData(0, Qt.UserRole, row['id'])
        item = next((self.start_tasks.topLevelItem(i) for i in range(self.start_tasks.topLevelItemCount())
                     if self.start_tasks.topLevelItem(i).data(0, Qt.UserRole) == selected), None)
        item = item or self.start_tasks.topLevelItem(0)
        with QSignalBlocker(self.start_tasks):
            self.start_tasks.setCurrentItem(item)
        self.owner.prepared_task = item.data(0, Qt.UserRole) if item else None
        self.no_tasks.setVisible(not rows)
        self.start_tasks.setVisible(bool(rows))
        self.duration_changed()

    def toggle_notes(self):
        self.notes_open = not self.notes_open
        self.refresh()
        if self.notes_open:
            self.entry.setFocus()

    def refresh(self):
        owner = self.owner
        session = owner.session
        queued = bool(session and owner.queue_project)
        mode = ('queue' if queued else 'free') if session else owner.prepared_mode
        result = not session and bool(owner.result_id)
        self.heading.setText('Итог занятия' if result else 'Занятие')
        self.back.setText('Полный вид' if owner.focus_mode else 'Компактный вид')
        self.restored.setVisible(bool(owner.restored_session and session))
        self.mode_picker.setVisible(not result)
        for key, control in self.mode_buttons.items():
            control.setChecked(key == mode)
        if result:
            self.pages.setCurrentWidget(self.result)
            record = owner.store.session(owner.result_id)
            self.result_clock.setText(clock_text(record['active_seconds']))
            project = next((p['name'] for p in owner.store.projects(True) if p['id'] == owner.result_project), '')
            self.result_mode.setText('Свободная работа' if record['queue'] == 'creative' else project)
            self.result_list.clear()
            for item in record['completed'].values():
                QTreeWidgetItem(self.result_list, [item['text']])
            self.result_empty.setVisible(not record['completed'])
            return
        if not session and mode == 'queue':
            self.pages.setCurrentWidget(self.setup)
            self.refresh_setup()
            return
        self.pages.setCurrentWidget(self.work)
        task = owner.store.task(owner.current_id) if queued and owner.current_id else None
        if task and task['deleted']:
            task = None
        self.title.setVisible(queued)
        self.project_label.hide()
        self.title.setText(task['title'] if task else 'Очередь пройдена')
        project = next((p['name'] for p in owner.store.projects(True) if p['id'] == owner.queue_project), '')
        self.log_heading.setText(project if queued else 'Сделано за занятие')
        rows = ([t for t in owner.store.tasks(project=owner.queue_project, show_done=True)
                 if task and t['parent_id'] == task['id']] if queued
                else owner.store.session_entries(session.id) if session else [])
        rows = [r for r in rows if not r['deleted']]
        signature = tuple((r['id'], r['title'], r['done']) for r in rows)
        if self.list_signature != signature:
            self.list_signature = signature
            scroll = self.tasks.verticalScrollBar().value()
            with QSignalBlocker(self.tasks):
                self.tasks.clear()
                for row in rows:
                    item = QTreeWidgetItem(self.tasks, [row['title']])
                    item.setData(0, Qt.UserRole, row['id'])
                    item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                    item.setCheckState(0, Qt.Checked if row['done'] else Qt.Unchecked)
            self.tasks.verticalScrollBar().setValue(scroll)
        self.tasks.setVisible(bool(rows))
        self.empty.setVisible(not rows)
        self.empty.setText(('Подпунктов нет. Работайте над текущей задачей.' if task else
                            'Дальше нет невыполненных задач. Можно завершить занятие.') if queued else
                           ('Пока без записей.\nЗдесь будет то, что вы запишете сами.' if session else
                            'Начните с чистого листа.\nЗапустите секундомер и занимайтесь своим делом.'))
        self.count.setText(f'Записей: {len(rows)}' if rows and not queued else '')
        self.queue_actions.setVisible(queued)
        self.done_button.setEnabled(bool(task))
        self.skip_button.setEnabled(bool(task))
        self.undo_button.setVisible(bool(owner.queue_undo))
        following = next((t for t in owner.store.tasks(project=owner.queue_project)
                          if task and not t['parent_id'] and t['position'] > task['position']), None) if queued else None
        self.next_task.setVisible(queued)
        self.next_task.setText('Далее: ' + following['title'] if following else 'Без возврата к началу очереди')
        self.notes_toggle.setVisible(queued)
        notes = owner.store.session_entries(session.id) if queued else []
        self.notes_toggle.setText(('Скрыть заметки' if self.notes_open else 'Заметки занятия') + (f' · {len(notes)}' if notes else ''))
        self.queue_notes.setVisible(queued and self.notes_open and bool(notes))
        if queued:
            self.queue_notes.clear()
            for note in notes:
                QTreeWidgetItem(self.queue_notes, [note['title']])
        self.entry_row.setVisible(bool(session) and (not queued or self.notes_open))
        self.saved.setVisible(bool(session) and (not queued or self.notes_open))
        self.saved.setText('Черновик сохранён' if self.entry.text() else 'Все записи сохранены · Enter — записать')
        self.update_clock()
        QTimer.singleShot(0, self.fit_rows)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'tasks'):
            QTimer.singleShot(0, self.fit_rows)

    def fit_rows(self):
        # QLabel's unwrapped size hint is not enough inside a scroll area.
        # Give a long task its full wrapped height before laying out the rows.
        if self.title.isVisible():
            self.title.setFixedHeight(self.title.heightForWidth(self.title.width()))
        for tree in (self.tasks, self.queue_notes):
            tree.doItemsLayout()
            height = sum(tree.sizeHintForRow(i) for i in range(tree.topLevelItemCount())) + 4
            tree.setFixedHeight(max(4, height))

    def update_clock(self):
        session = self.owner.session
        active = bool(session)
        elapsed = self.owner.effective_seconds()
        queued = bool(active and self.owner.queue_project)
        if queued and session.planned_seconds:
            remaining = session.planned_seconds - elapsed
            self.timer_label.setText(('+' if remaining < 0 else '') + clock_text(abs(remaining)))
            self.caption.setText(('Сверх плана' if remaining < 0 else 'Осталось') +
                f' · отработано {clock_text(elapsed)} из {int(session.planned_seconds / 60)} мин')
        else:
            self.timer_label.setText(clock_text(elapsed))
            self.caption.setText('Секундомер · без ограничения времени' if not queued else 'Отработанное время · без плана')
        self.pause.setText(('Пауза' if session.running else 'Продолжить') if active else 'Начать')
        self.status.setText(('В работе' if session.running else 'На паузе · время не идёт') if active else 'Готов к началу')
        paused = bool(active and not session.running)
        if self.status.property('paused') != paused:
            self.status.setProperty('paused', paused)
            self.status.style().unpolish(self.status)
            self.status.style().polish(self.status)
        self.correct.setVisible(active)
        self.correct.setEnabled(active and elapsed >= 1)
        self.finish.setVisible(active)
        self.clock_area.layout().activate()
        self.clock_area.setFixedHeight(self.clock_area.minimumSizeHint().height())

    def complete_current(self):
        return self.owner.complete_queue_task(False)

    def skip_current(self):
        return self.owner.complete_queue_task(True)

    def changed(self, item, _):
        ident = item.data(0, Qt.UserRole)
        checked = item.checkState(0) == Qt.Checked
        def action():
            owner = self.owner
            owner.store.toggle(ident, checked, owner.session.id if owner.session else None)
            if checked and owner.session and owner.queue_project:
                current = owner.store.task(owner.current_id)
                if current and current['done']:
                    owner.advance_queue(current['position'])
            owner.checkpoint()
            owner.schedule_task_refresh()
        self.owner.safe(action)

    def add(self):
        text = self.entry.text().strip()
        if not text or not self.owner.session:
            return None
        def action():
            ident = self.owner.store.add_session_entry(self.owner.session.id, text)
            self.entry.clear()
            self.owner.checkpoint()
            self.owner.refresh_tasks()
            def reveal_entry():
                self.fit_rows()
                self.details_scroll.widget().layout().activate()
                bar = self.details_scroll.verticalScrollBar()
                bar.setValue(bar.maximum())
            QTimer.singleShot(0, reveal_entry)
            self.entry.setFocus()
            return ident
        return self.owner.safe(action)
