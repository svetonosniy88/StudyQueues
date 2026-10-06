from pathlib import Path
from datetime import date, datetime
from math import ceil

from PySide6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve, QParallelAnimationGroup, QPoint
from PySide6.QtGui import QFont, QShortcut, QKeySequence, QPalette, QColor, QFontDatabase, QIcon
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QFrame, QLabel, QPushButton,
    QCheckBox, QLineEdit, QSpinBox, QVBoxLayout, QHBoxLayout, QScrollArea, QStackedWidget,
    QFileDialog, QMenu, QGraphicsOpacityEffect, QSizeGrip, QSizePolicy, QLayout)

from .queue_store import QueueStore, QueueError
from . import __version__
from .state_store import StateStore, StateError
from .session import Session, daily_seconds
from .models import Completion
from .queue_window import QueueWindow, PAGE_SIZE
from .native_window import NativeFrame
from .widgets import TaskArea, TaskList, DragHandle, QueueCard, LaunchBar, CapsuleScrollBar, WorkContent, IconButton, InlineTaskEdit, TaskCheckBox, TaskRow

NAMES = {"university": "Университет", "self_development": "Саморазвитие"}


def label(text, name=None, wrap=False):
    widget = QLabel(text)
    if name:
        widget.setObjectName(name)
    widget.setWordWrap(wrap)
    widget.setTextFormat(Qt.PlainText)
    return widget


def button(text, action, name=None, tip=None):
    widget = QPushButton(text)
    if name:
        widget.setObjectName(name)
    if tip:
        widget.setToolTip(tip)
    widget.setCursor(Qt.PointingHandCursor)
    widget.clicked.connect(action)
    return widget


def scroll_area():
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    scroll.setVerticalScrollBar(CapsuleScrollBar(scroll))
    return scroll


def clear(layout):
    while layout.count():
        child = layout.takeAt(0)
        if child.widget():
            child.widget().hide()
            child.widget().deleteLater()
        elif child.layout():
            clear(child.layout())


def clock_text(seconds):
    total = max(0, int(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours:02}:{minutes:02}:{secs:02}" if hours else f"{minutes:02}:{secs:02}"


def minutes_text(seconds):
    minutes = int(seconds // 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours} ч {minutes:02} мин" if hours else f"{minutes} мин"


class TitleBar(QWidget):
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.window().windowHandle():
            self.window().windowHandle().startSystemMove()

    def mouseDoubleClickEvent(self, event):
        window = self.window()
        window.showNormal() if window.isMaximized() else window.showMaximized()


class Window(QMainWindow):
    def __init__(self, data_dir, default_paths=None, force_paths=False):
        super().__init__()
        self.setWindowTitle("Учебные очереди · StudyQueues")
        self.setWindowIcon(QIcon(str(Path(__file__).with_name("app.ico"))))
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        self.app_platform = QApplication.platformName()
        self.native_frame = NativeFrame(self)
        self.resize(1160, 820)
        self.setMinimumSize(820, 650)
        if self.app_platform == "windows":
            area = QApplication.primaryScreen().availableGeometry()
            self.resize(max(820, min(1160, area.width() - 24)),
                        max(650, min(820, area.height() - 24)))
        self.state = StateStore(data_dir)
        self.settings = self.state.read("settings.json", {"version": 1, "paths": {}, "minutes": 60})
        self.state.validate_settings(self.settings)
        # Retain the stored profile, but each launch starts a fresh time selection.
        self.initial_minutes = 0
        self.stats_favorites_only = False
        self.state.history()  # Fail visibly before overwriting corrupt state.
        self.stores = {}
        self.load_errors = {}
        self.selected = "university"
        self.session = None
        self.busy = False
        self.animations = []
        self.checkpoint_ticks = 0
        self.work_draft = None
        self.overview_add_parents = {}
        self.queue_windows = {key: QueueWindow() for key in NAMES}
        self.child_windows = {}
        self.overview_expanded = set()
        self.overview_dirty = True
        self.stats_dirty = True
        self.fit_timer = QTimer(self)
        self.fit_timer.setSingleShot(True)
        self.fit_timer.timeout.connect(self.fit_task_title)
        self.recovery = self.state.read("active_session.json", {"version": 1, "session": None}).get("session")
        if self.recovery is not None:
            self.state.validate_session(self.recovery)
        for key, path in (default_paths or {}).items():
            if force_paths:
                self.settings["paths"][key] = str(path)
            else:
                self.settings["paths"].setdefault(key, str(path))
        self._load_queues()
        self._build()
        self.refresh_overview()
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self.tick)
        self.timer.start()
        self.undo_shortcut = QShortcut(QKeySequence.Undo, self)
        self.undo_shortcut.activated.connect(self.handle_undo)
        if self.recovery:
            self.notify("Есть незавершённое занятие. Его можно восстановить на паузе.")

    def _load_queues(self):
        previous = self.stores
        self.stores = {}
        self.load_errors = {}
        for key in NAMES:
            path = self.settings.get("paths", {}).get(key)
            if path:
                try:
                    old = previous.get(key)
                    if old and old.path == Path(path):
                        old.load()
                        self.stores[key] = old
                    else:
                        self.stores[key] = QueueStore(path, self.state.backups)
                except QueueError as e:
                    self.load_errors[key] = str(e)

    def _build(self):
        shell = QWidget()
        shell.setObjectName("shell")
        self.setCentralWidget(shell)
        outer = QVBoxLayout(shell)
        outer.setContentsMargins(1, 1, 1, 1)
        outer.setSpacing(0)
        titlebar = TitleBar()
        self.titlebar = titlebar
        self.window_controls = []
        titlebar.setObjectName("titlebar")
        titlebar.setAttribute(Qt.WA_StyledBackground, True)
        titlebar.setFixedHeight(52)
        title = QHBoxLayout(titlebar)
        title.setContentsMargins(16, 5, 6, 5)
        mark = label("SQ", "appmark")
        mark.setFixedSize(28, 28)
        mark.setAlignment(Qt.AlignCenter)
        title.addWidget(mark)
        title.addSpacing(6)
        title.addWidget(label("Учебные очереди", "brand"))
        title.addWidget(label(" /  личное пространство", "muted"))
        title.addStretch()
        for kind, action, name, tip in (("minimize", self.showMinimized, "ghost", "Свернуть"),
                ("maximize", lambda: self.showNormal() if self.isMaximized() else self.showMaximized(), "ghost", "Развернуть / восстановить"),
                ("close", self.close, "windowClose", "Закрыть")):
            control = IconButton(kind, action, tip, name)
            control.setFocusPolicy(Qt.NoFocus)
            title.addWidget(control)
            self.window_controls.append(control)
        outer.addWidget(titlebar)
        body = QHBoxLayout()
        body.setSpacing(0)
        outer.addLayout(body, 1)
        self.sidebar = QFrame()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setFixedWidth(190)
        side = QVBoxLayout(self.sidebar)
        side.setContentsMargins(12, 26, 12, 12)
        side.setSpacing(7)
        side.addWidget(label("ПРОСТРАНСТВО", "eyebrow"))
        side.addSpacing(9)
        self.nav_overview = button("Обзор", self.show_overview, "nav")
        self.nav_stats = button("Результаты", self.show_stats, "nav")
        self.nav_overview.setCheckable(True)
        self.nav_stats.setCheckable(True)
        side.addWidget(self.nav_overview)
        side.addWidget(self.nav_stats)
        side.addStretch()
        side.addWidget(label(f"StudyQueues\nВерсия {__version__}", "muted"))
        body.addWidget(self.sidebar)
        content = QWidget()
        content.setObjectName("content")
        content_layout = QVBoxLayout(content)
        self.content_layout = content_layout
        content_layout.setContentsMargins(30, 27, 30, 12)
        content_layout.setSpacing(16)
        self.pages = QStackedWidget()
        self.overview, self.work, self.stats = QWidget(), QWidget(), QWidget()
        for page in (self.overview, self.work, self.stats):
            self.pages.addWidget(page)
        self.overview_layout = QVBoxLayout(self.overview)
        self.overview_layout.setContentsMargins(0, 0, 0, 0)
        self.work_layout = QVBoxLayout(self.work)
        self.work_layout.setContentsMargins(0, 0, 0, 0)
        self.stats_layout = QVBoxLayout(self.stats)
        self.stats_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.addWidget(self.pages, 1)
        self.toast = label("", "toast", True)
        self.toast.hide()
        content_layout.addWidget(self.toast)
        status = QHBoxLayout()
        status.addWidget(label("Локально · ваши очереди и ваше время", "muted"))
        status.addStretch()
        status.addWidget(QSizeGrip(self))
        content_layout.addLayout(status)
        body.addWidget(content, 1)
        self.show_overview()

    def notify(self, text, error=False):
        self.last_editor_notice = None
        self.toast.setText(text)
        self.toast.setProperty("error", error)
        self.toast.style().unpolish(self.toast)
        self.toast.style().polish(self.toast)
        self.toast.show()
        QTimer.singleShot(9000 if error else 5000, self.toast.hide)

    def safe(self, operation):
        try:
            operation()
            return True
        except (QueueError, StateError, OSError, ValueError, KeyError) as e:
            self.notify(str(e), True)
            return False

    def show_overview(self):
        if self.session and not self.session.ended_at:
            self.notify("Сначала завершите текущее занятие.")
            return
        if hasattr(self, "duration") and self.overview_dirty:
            self.refresh_overview()
        self.pages.setCurrentWidget(self.overview)
        self.sidebar.show()
        self.nav_overview.setChecked(True)
        self.nav_stats.setChecked(False)

    def refresh_overview(self):
        self.overview_dirty = True
        if self.session and self.pages.currentWidget() == self.work:
            return
        if not self.flush_overview_edits():
            return
        self.overview_dirty = False
        if hasattr(self, "queue_counts"):
            self.sync_overview()
            return
        for editor in getattr(self, "task_edits", {}).values():
            editor.suspended = True
        show_done = getattr(self, "show_done", None)
        checked = show_done.isChecked() if show_done else False
        old_minutes = self.duration.value() if hasattr(self, "duration") else self.initial_minutes
        drafts = {key: field.text() for key, field in getattr(self, "add_inputs", {}).items()}
        scroll_positions = {key: scroll.verticalScrollBar().value() for key, scroll in getattr(self, "queue_scrolls", {}).items()}
        self.overview_layout.setEnabled(False)
        self.overview.setUpdatesEnabled(False)
        clear(self.overview_layout)
        self.overview_layout.setSpacing(16)
        top = QHBoxLayout()
        titles = QVBoxLayout()
        titles.addWidget(label("Ваш следующий шаг", "heading"))
        titles.addWidget(label("Выберите очередь и время. Остальное — по одной задаче.", "subtitle"))
        top.addLayout(titles)
        top.addStretch()
        top.addWidget(IconButton("refresh", self.reload, "Обновить очереди после внешних изменений"))
        self.overview_layout.addLayout(top)
        controls = QHBoxLayout()
        self.show_done = QCheckBox("Показать выполненное")
        self.show_done.setChecked(checked)
        self.show_done.toggled.connect(self.apply_overview_filter)
        controls.addWidget(self.show_done)
        controls.addStretch()
        if self.recovery:
            controls.addWidget(button("Восстановить занятие", self.restore_session))
        self.overview_layout.addLayout(controls)
        self.overview_layout.addSpacing(4)
        cards = QHBoxLayout()
        cards.setSpacing(18)
        self.task_edits = {}
        self.task_rows = {}
        self.add_inputs = {}
        self.add_cancels = {}
        self.queue_scrolls = {}
        self.task_lists = {}
        self.queue_cards = {}
        self.queue_select_buttons = {}
        self.queue_titles = {}
        self.queue_counts = {}
        self.queue_page_labels = {}
        self.queue_pagers = {}
        self.history_pagers = {}
        for key, name in NAMES.items():
            card = QueueCard(key == self.selected)
            self.queue_cards[key] = card
            layout = QVBoxLayout(card)
            layout.setContentsMargins(12, 16, 12, 16)
            layout.setSpacing(12)
            header = QHBoxLayout()
            header.setSpacing(8)
            select = button(name, lambda _, k=key: self.select_queue(k), "queueTitle", "Выбрать эту очередь")
            self.queue_titles[key] = select
            header.addWidget(select)
            header.addStretch()
            choose = button("Выбрать", lambda _, k=key: self.select_queue(k), "queueSelect")
            choose.setFixedSize(78, 36)
            choose.setEnabled(key != self.selected)
            choose.setCursor(Qt.PointingHandCursor if key != self.selected else Qt.ArrowCursor)
            header.addWidget(choose)
            self.queue_select_buttons[key] = choose
            header.addWidget(self.menu_button(lambda _, k=key: self.choose_path(k), "Выбрать файл очереди"))
            layout.addLayout(header)
            store = self.stores.get(key)
            pending = len(store.active) if store else 0
            self.queue_counts[key] = label(f"{pending} задач в очереди" if store else "Очередь не подключена", "muted")
            layout.addWidget(self.queue_counts[key])
            scroll = scroll_area()
            self.queue_scrolls[key] = scroll
            body = TaskList(key, scroll, self)
            self.task_lists[key] = body
            body.all_groups = {}
            rows = body.rows
            rows.setEnabled(False)
            if store:
                window = self.queue_windows[key]
                visible, upcoming, past = window.resolve(store.tasks, True)
                for task in visible:
                    group = self.create_task_group(key, task, body)
                    rows.addWidget(group)
                    body.all_groups[task.id] = group
                    group.setVisible(checked or not task.completed)
                    if checked or not task.completed:
                        body.groups.append((task.id, group))
                if visible:
                    self.update_drop_tail(key)
                if not body.groups:
                    empty = label("Всё выполнено.\nМожно добавить следующую задачу.", "muted", True)
                    empty.setAlignment(Qt.AlignCenter)
                    rows.addWidget(empty)
            else:
                rows.addWidget(label(self.load_errors.get(key, "Подключите заметку с задачами."), "muted", True))
                rows.addWidget(button("Выбрать файл", lambda _, k=key: self.choose_path(k)))
            rows.addStretch()
            rows.setEnabled(True)
            scroll.setWidget(body)
            if key in scroll_positions:
                QTimer.singleShot(0, lambda s=scroll, v=scroll_positions[key]: s.verticalScrollBar().setValue(v))
            layout.addWidget(scroll, 1)
            if store:
                pager = QHBoxLayout()
                previous = IconButton("back", lambda _, k=key: self.move_queue_window(k, -1), "Предыдущие 15 задач")
                following = IconButton("forward", lambda _, k=key: self.move_queue_window(k, 1), "Следующие 15 задач")
                previous.setEnabled(window.start > 0)
                following.setEnabled(window.start + PAGE_SIZE < len(upcoming))
                pager.addWidget(previous)
                self.queue_page_labels[key] = label(f"{window.start + 1}–{min(window.start + PAGE_SIZE, len(upcoming))} из {len(upcoming)}", "muted")
                pager.addWidget(self.queue_page_labels[key], 1, Qt.AlignCenter)
                pager.addWidget(following)
                self.queue_pagers[key] = (previous, following)
                layout.addLayout(pager)
                for control in (previous, following, self.queue_page_labels[key]):
                    control.setVisible(len(upcoming) > PAGE_SIZE)
            if store:
                pager = QHBoxLayout()
                older = button("Ранее", lambda _, k=key: self.move_history_window(k, 1), "ghost", "Предыдущие 15 выполненных задач")
                newer = button("Позже", lambda _, k=key: self.move_history_window(k, -1), "ghost", "Более поздние выполненные задачи")
                older.setEnabled((window.history_page + 1) * PAGE_SIZE < len(past))
                newer.setEnabled(window.history_page > 0)
                pager.addWidget(older)
                history_label = label("Выполненные", "muted")
                pager.addWidget(history_label, 1, Qt.AlignCenter)
                pager.addWidget(newer)
                self.history_pagers[key] = (older, newer, history_label)
                layout.addLayout(pager)
                for control in (older, newer, history_label):
                    control.setVisible(checked and len(past) > PAGE_SIZE)
            add = QHBoxLayout()
            input_box = QLineEdit()
            input_box.setPlaceholderText("Добавить задачу…")
            input_box.setText(drafts.get(key, ""))
            input_box.setEnabled(store is not None)
            input_box.returnPressed.connect(lambda k=key: self.submit_overview_task(k))
            self.add_inputs[key] = input_box
            add.addWidget(input_box, 1)
            cancel = IconButton("close", lambda _, k=key: self.cancel_overview_draft(k), "Отменить добавление подпункта")
            cancel.setFixedSize(32, 40)
            self.add_cancels[key] = cancel
            add.addWidget(cancel)
            add.addWidget(button("+", lambda _, k=key: self.submit_overview_task(k), tip="Добавить пункт"))
            escape = QShortcut(QKeySequence(Qt.Key_Escape), input_box)
            escape.setContext(Qt.WidgetShortcut)
            escape.activated.connect(lambda k=key: self.cancel_overview_draft(k))
            self.update_overview_draft(key)
            layout.addLayout(add)
            cards.addWidget(card, 1)
        self.overview_layout.addLayout(cards, 1)
        self.overview_layout.addSpacing(8)
        self.launch_title = label(NAMES[self.selected], "launchQueue")
        self.start_button = button("Начать занятие", self.start_session, "primary")
        self.duration = QSpinBox()
        self.duration.setObjectName("duration")
        self.duration.setRange(0, 480)
        self.duration.setButtonSymbols(QSpinBox.NoButtons)
        self.duration.setValue(old_minutes)
        self.duration.setSuffix(" мин")
        self.duration.setAccessibleName("Длительность занятия в минутах")
        self.duration.setKeyboardTracking(True)
        self.reset_duration = button("Сброс", lambda: self.change_duration(0), "timeButton", "Сбросить длительность")
        self.duration_add_buttons = {}
        for minutes in (5, 15, 60):
            control = button(f"+{minutes} мин", lambda _, m=minutes: self.change_duration((self.duration.value() if self.duration.hasAcceptableInput() else 0) + m), "timeButton")
            self.duration_add_buttons[minutes] = control
        self.launch_bar = LaunchBar(self.launch_title, self.duration, self.reset_duration, self.duration_add_buttons, self.start_button)
        self.duration.valueChanged.connect(self.update_start_button)
        self.duration.lineEdit().textChanged.connect(self.update_start_button)
        self.duration.lineEdit().textChanged.connect(lambda: QTimer.singleShot(0, self.update_start_button))
        self.update_start_button()
        self.overview_layout.addWidget(self.launch_bar)
        self.overview_layout.setEnabled(True)
        self.overview.setUpdatesEnabled(True)

    def sync_overview(self):
        """Keep existing rows and controls; change only the bounded root window."""
        checked = self.show_done.isChecked()
        if set(self.queue_pagers) != set(self.stores):
            del self.queue_counts
            self.refresh_overview()
            return
        for key, store in self.stores.items():
            body = self.task_lists[key]
            if not hasattr(body, "all_groups") or key not in self.queue_pagers:
                # A previously disconnected queue needs its file controls rebuilt.
                del self.queue_counts
                self.refresh_overview()
                return
            scroll_value = self.queue_scrolls[key].verticalScrollBar().value()
            visible, pending, past = self.queue_windows[key].resolve(store.tasks, True)
            wanted = {task.id for task in visible}
            for ident, group in list(body.all_groups.items()):
                task = store.by_id.get(ident)
                children = self.visible_children(task) if task else []
                if ident not in wanted or not task or group.child_ids != tuple(child.id for child in children) or group.has_child_pager != (bool(children) and len(task.children) > PAGE_SIZE) or group.has_children != bool(task.children):
                    for row in group.findChildren(TaskRow):
                        row.editor.suspended = True
                    for item in [ident, *group.child_ids]:
                        self.task_edits.pop(item, None)
                        self.task_rows.pop(item, None)
                    body.rows.removeWidget(group)
                    group.hide()
                    group.deleteLater()
                    del body.all_groups[ident]
            body.groups = []
            for index, task in enumerate(visible):
                group = body.all_groups.get(task.id)
                if group is None:
                    group = self.create_task_group(key, task, body)
                    body.all_groups[task.id] = group
                if index >= body.rows.count() or body.rows.itemAt(index).widget() is not group:
                    body.rows.removeWidget(group)
                    body.rows.insertWidget(index, group)
                group.setVisible(checked or not task.completed)
                if checked or not task.completed:
                    body.groups.append((task.id, group))
                for item in [task, *self.visible_children(task)]:
                    row = self.task_rows[item.id]
                    editor = row.editor
                    if editor.saved_text != item.text:
                        editor.setPlainText(item.text)
                        editor.mark_saved(item.text)
                    font = editor.font()
                    if font.strikeOut() != item.completed:
                        font.setStrikeOut(item.completed)
                        editor.setFont(font)
                        editor.adjust_height()
                    row.checkbox.setChecked(item.completed)
                    row.checkbox.setAccessibleName(item.text)
                    row.checkbox.setToolTip("Вернуть в работу" if item.completed else "Отметить выполненным")
                    if item.parent_id:
                        row.setVisible(checked or not item.done)
                self.update_child_pagers(task, group)
            # Old empty/error placeholders must not coexist with real groups.
            for index in reversed(range(body.rows.count())):
                item = body.rows.itemAt(index)
                widget = item.widget()
                if widget and widget.objectName() != "taskGroup":
                    body.rows.takeAt(index)
                    widget.hide()
                    widget.deleteLater()
            if not body.groups:
                empty = label("Всё выполнено.\nМожно добавить следующую задачу.", "muted", True)
                empty.setAlignment(Qt.AlignCenter)
                body.rows.insertWidget(0, empty)
            self.update_queue_pagers(key, pending, past)
            self.update_overview_draft(key)
            self.queue_counts[key].setText(f"{len(pending)} задач в очереди")
            self.update_drop_tail(key)
            QTimer.singleShot(0, lambda s=self.queue_scrolls[key], v=scroll_value: s.verticalScrollBar().setValue(v))
        self.update_start_button()

    def visible_children(self, task):
        if task.id not in self.overview_expanded:
            return []
        if len(task.children) <= PAGE_SIZE:
            return task.children
        return self.child_windows.setdefault(task.id, QueueWindow()).resolve(task.children, True)[0]

    def create_task_group(self, key, task, body):
        group = QFrame()
        group.setObjectName("taskGroup")
        rows = QVBoxLayout(group)
        rows.setContentsMargins(4, 4, 4, 4)
        rows.setSpacing(2)
        rows.addWidget(self.task_row(key, task, body))
        children = self.visible_children(task)
        for child in children:
            row = self.task_row(key, child, body)
            rows.addWidget(row)
            row.setVisible(self.show_done.isChecked() or not child.done)
        group.child_ids = tuple(child.id for child in children)
        group.has_children = bool(task.children)
        group.has_child_pager = bool(children) and len(task.children) > PAGE_SIZE
        if group.has_child_pager:
            self.build_child_pagers(key, task, group, rows)
        return group

    def toggle_children(self, key, ident):
        if not self.flush_overview_edits():
            return
        if ident in self.overview_expanded:
            self.overview_expanded.remove(ident)
        else:
            self.overview_expanded.add(ident)
        self.refresh_overview()

    def build_child_pagers(self, key, task, group, rows):
        group.has_child_pager = True
        group.child_pagers = []
        for history in (False, True):
            panel = QWidget()
            line = QHBoxLayout(panel)
            line.setContentsMargins(8, 0, 8, 0)
            line.setSpacing(4)
            previous = IconButton("back", lambda _, h=history: self.move_child_window(key, task.id, -1, h), "Предыдущие подпункты")
            following = IconButton("forward", lambda _, h=history: self.move_child_window(key, task.id, 1, h), "Следующие подпункты")
            previous.setFixedSize(28, 30)
            following.setFixedSize(28, 30)
            caption = label("", "muted")
            caption.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            line.addWidget(previous)
            line.addWidget(caption, 1, Qt.AlignCenter)
            line.addWidget(following)
            rows.addWidget(panel)
            group.child_pagers.append((panel, previous, following, caption))
        self.update_child_pagers(task, group)

    def update_child_pagers(self, task, group):
        if not group.has_child_pager:
            return
        window = self.child_windows[task.id]
        _, pending, past = window.resolve(task.children, True)
        panel, previous, following, caption = group.child_pagers[0]
        panel.setVisible(len(pending) > PAGE_SIZE)
        previous.setEnabled(window.start > 0)
        following.setEnabled(window.start + PAGE_SIZE < len(pending))
        caption.setText(f"Подпункты {window.start + 1}–{min(window.start + PAGE_SIZE, len(pending))} из {len(pending)}")
        panel, previous, following, caption = group.child_pagers[1]
        panel.setVisible((group is getattr(self, "work_card", None) or self.show_done.isChecked()) and len(past) > PAGE_SIZE)
        previous.setEnabled((window.history_page + 1) * PAGE_SIZE < len(past))
        following.setEnabled(window.history_page > 0)
        caption.setText(f"Выполненные · {window.history_page + 1}/{ceil(len(past) / PAGE_SIZE)}")

    def move_child_window(self, key, ident, direction, history=False):
        if not self.flush_work_edits() or not self.flush_overview_edits():
            return
        window = self.child_windows[ident]
        if history:
            window.history_page = max(0, window.history_page - direction)
        else:
            window.move(self.stores[key].by_id[ident].children, direction)
        self.refresh_overview()
        if self.session and self.pages.currentWidget() == self.work:
            self.render_work()

    def update_drop_tail(self, key):
        body = self.task_lists[key]
        store = self.stores[key]
        last = next((i for i, task in enumerate(store.tasks) if body.groups and task.id == body.groups[-1][0]), None)
        body.tail_before = store.tasks[last + 1].id if last is not None and last + 1 < len(store.tasks) else None

    def update_queue_pagers(self, key, pending, past):
        window = self.queue_windows[key]
        previous, following = self.queue_pagers[key]
        previous.setEnabled(window.start > 0)
        following.setEnabled(window.start + PAGE_SIZE < len(pending))
        self.queue_page_labels[key].setText(f"{window.start + 1}–{min(window.start + PAGE_SIZE, len(pending))} из {len(pending)}")
        for control in (previous, following, self.queue_page_labels[key]):
            control.setVisible(len(pending) > PAGE_SIZE)
        older, newer, history_label = self.history_pagers[key]
        older.setEnabled((window.history_page + 1) * PAGE_SIZE < len(past))
        newer.setEnabled(window.history_page > 0)
        for control in (older, newer, history_label):
            control.setVisible(self.show_done.isChecked() and len(past) > PAGE_SIZE)

    def apply_overview_filter(self, *_):
        if not self.flush_overview_edits():
            self.show_done.blockSignals(True)
            self.show_done.setChecked(not self.show_done.isChecked())
            self.show_done.blockSignals(False)
            return
        self.sync_overview()

    def move_queue_window(self, key, direction):
        if not self.flush_overview_edits():
            return
        self.queue_windows[key].move(self.stores[key].tasks, direction)
        self.queue_scrolls[key].verticalScrollBar().setValue(0)
        self.refresh_overview()

    def move_history_window(self, key, direction):
        if not self.flush_overview_edits():
            return
        window = self.queue_windows[key]
        window.history_page = max(0, window.history_page + direction)
        self.queue_scrolls[key].verticalScrollBar().setValue(0)
        self.refresh_overview()

    def update_start_button(self):
        self.start_button.setEnabled(bool(self.duration.hasAcceptableInput() and self.duration.value() > 0 and self.stores.get(self.selected) and self.stores[self.selected].active))
        self.start_button.setToolTip("Начать занятие" if self.duration.value() else "Наберите длительность кнопками или введите минуты")

    def change_duration(self, minutes):
        self.duration.setValue(minutes)
        # setValue alone leaves invalid edited text untouched if the number is unchanged.
        self.duration.lineEdit().setText(self.duration.textFromValue(self.duration.value()) + self.duration.suffix())
        self.update_start_button()

    def menu_button(self, action, tip):
        control = IconButton("menu", action, tip, "taskMenu")
        control.setFixedSize(36, 36)
        return control

    def task_row(self, key, task, area=None):
        handle = DragHandle(area, task.id) if not task.parent_id and area is not None else None
        disclosure = None
        if task.children:
            expanded = task.id in self.overview_expanded
            disclosure = IconButton("collapse" if expanded else "expand", lambda: self.toggle_children(key, task.id), "Свернуть подпункты" if expanded else "Развернуть подпункты")
            disclosure.setFocusPolicy(Qt.NoFocus)
        row = TaskRow(task.text, task.completed, bool(task.parent_id), handle=handle, disclosure=disclosure)
        row.checkbox.clicked.connect(lambda checked, k=key, t=task.id: self.set_task_checked(k, t, checked))
        row.editor.saveRequested.connect(lambda text, k=key, t=task.id, e=row.editor: self.save_overview_text(k, t, text, e))
        row.editor.cancelled.connect(lambda e=row.editor: self.mark_editor_error(e, False))
        row.menu_button.clicked.connect(lambda _, k=key, t=task.id, r=row: self.task_menu(k, t, r))
        self.task_edits[task.id] = row.editor
        self.task_rows[task.id] = row
        return row

    def select_queue(self, key):
        self.selected = key
        self.launch_title.setText(NAMES[key])
        for queue, card in self.queue_cards.items():
            card.setProperty("selected", queue == key)
            card.style().unpolish(card)
            card.style().polish(card)
            control = self.queue_select_buttons[queue]
            control.setEnabled(queue != key)
            control.setCursor(Qt.ArrowCursor if queue == key else Qt.PointingHandCursor)
        self.update_start_button()

    def choose_path(self, key):
        file, _ = QFileDialog.getOpenFileName(self, "Выберите очередь", str(Path(self.settings.get("paths", {}).get(key, str(Path.home()))).parent), "Markdown (*.md)")
        if not file:
            return
        def action():
            if any(Path(s.path).resolve() == Path(file).resolve() for k, s in self.stores.items() if k != key):
                raise QueueError("Для двух очередей выберите разные файлы.")
            store = QueueStore(file, self.state.backups)
            self.settings["paths"][key] = file
            self.state.write("settings.json", self.settings)
            self.stores[key] = store
            self.queue_windows[key] = QueueWindow()
            self.refresh_overview()
        self.safe(action)

    def reload(self):
        if self.busy or not self.flush_work_edits() or not self.flush_overview_edits():
            return
        def action():
            if self.session:
                self.session.last_completion = None
            self._load_queues()
            if self.session:
                self.reconcile_results()
                self.checkpoint()
            self.refresh_overview()
            if self.session and self.pages.currentWidget() == self.work:
                if self.session.queue not in self.stores:
                    self.session.pause()
                self.render_work()
            if self.load_errors:
                self.notify("\n".join(self.load_errors.values()), True)
            else:
                self.notify("Очереди обновлены.")
        self.safe(action)

    def add_task(self, key, text, parent=None):
        if self.busy or (self.session and self.session.ended_at) or not self.flush_work_edits() or not self.flush_overview_edits():
            return False
        committed = False
        in_work = bool(self.session and self.pages.currentWidget() == self.work)
        def action():
            nonlocal committed
            store = self.stores.get(key)
            if not store:
                raise QueueError("Сначала подключите файл очереди.")
            current = getattr(self, "current_task", None)
            after = current.id if parent is None and self.session and self.session.queue == key and self.pages.currentWidget() == self.work and current else None
            store.add(text, parent, after_id=after, at_start=parent is None and not in_work)
            committed = True
            if not in_work:
                if parent is None:
                    self.queue_windows[key] = QueueWindow()
                    self.queue_scrolls[key].verticalScrollBar().setValue(0)
                else:
                    self.overview_expanded.add(parent)
            if self.session:
                self.session.last_completion = None
                self.reconcile_results()
                self.checkpoint()
        self.safe(action)
        if committed:
            self.refresh_overview()
            if self.session and self.pages.currentWidget() == self.work:
                self.render_work()
        return committed

    def edit_task(self, key, ident, text):
        editor = self.task_edits.get(ident)
        if editor:
            self.save_overview_text(key, ident, text, editor)

    def save_overview_text(self, key, ident, text, editor):
        if editor.suspended or self.busy or (self.session and self.session.ended_at):
            return False
        store = self.stores[key]
        task = store.by_id.get(ident)
        if not task:
            return True
        if task.text == text.strip():
            return True
        def action():
            store.edit(ident, text)
            editor.mark_saved(store.by_id[ident].text)
            editor.parentWidget().findChild(QCheckBox).setAccessibleName(store.by_id[ident].text)
            if self.session:
                self.session.last_completion = None
                self.reconcile_results()
                self.checkpoint()
        saved = self.safe(action)
        self.mark_editor_error(editor, not saved)
        return saved

    def mark_editor_error(self, editor, error):
        if error:
            self.last_editor_notice = editor
        elif getattr(self, "last_editor_notice", None) is editor:
            if self.toast.property("error"):
                self.toast.hide()
            self.last_editor_notice = None
        editor.setProperty("error", error)
        editor.style().unpolish(editor)
        editor.style().polish(editor)

    def flush_overview_edits(self, excluding=None):
        excluded = excluding or set()
        for key, store in self.stores.items():
            for ident, editor in getattr(self, "task_edits", {}).items():
                if ident in store.by_id and ident not in excluded and not editor.suspended and editor.toPlainText().strip() != editor.saved_text:
                    if not self.save_overview_text(key, ident, editor.toPlainText(), editor):
                        return False
        return True

    def save_work_text(self, ident, text, editor):
        if editor.suspended or self.busy or not self.session or self.session.ended_at:
            return False
        store = self.stores[self.session.queue]
        task = store.by_id.get(ident)
        if not task:
            return False
        if task.text == text.strip():
            return True
        def action():
            store.edit(ident, text)
            editor.mark_saved(store.by_id[ident].text)
            self.current_task = store.by_id.get(self.session.view_task_id)
            self.session.last_completion = None
            self.reconcile_results()
            self.checkpoint()
            self.refresh_overview()
        saved = self.safe(action)
        self.mark_editor_error(editor, not saved)
        return saved

    def flush_work_edits(self, excluding=None):
        if not self.session or self.pages.currentWidget() != self.work:
            return True
        excluded = {excluding} if isinstance(excluding, str) else excluding or set()
        for ident, editor in getattr(self, "work_editors", {}).items():
            if ident not in excluded and not editor.suspended and editor.toPlainText().strip() != editor.saved_text:
                if not self.save_work_text(ident, editor.toPlainText(), editor):
                    return False
        return True

    def show_work_draft(self, parent=None):
        if self.busy or not self.session or self.session.ended_at or not self.flush_work_edits():
            return
        if self.work_draft is None:
            self.work_draft = {"parent_id": parent, "text": ""}
            self.render_work()
        else:
            self.work_draft["parent_id"] = parent
            self.update_work_draft_type()
        QTimer.singleShot(0, self.focus_work_draft)

    def focus_work_draft(self):
        if self.work_draft is not None and hasattr(self, "draft_editor"):
            self.task_scroll.ensureWidgetVisible(self.draft_row)
            self.draft_editor.setFocus(Qt.OtherFocusReason)
            self.task_scroll.verticalScrollBar().setValue(self.task_scroll.verticalScrollBar().maximum())

    def cancel_work_draft(self):
        self.work_draft = None
        self.render_work()

    def save_work_draft(self):
        if self.busy or self.work_draft is None or not self.flush_work_edits():
            return
        draft = self.work_draft
        self.work_draft = None
        if not self.add_task(self.session.queue, draft["text"], draft["parent_id"]):
            self.work_draft = draft
            self.render_work()
            QTimer.singleShot(0, self.focus_work_draft)

    def delete_task(self, key, ident):
        store = self.stores.get(key)
        task = store.by_id.get(ident) if store else None
        if not task:
            return
        removed_ids = {t.id for t in [task, *task.children]}
        if self.busy or (self.session and self.session.ended_at) or not self.flush_work_edits(excluding=removed_ids) or not self.flush_overview_edits(excluding=removed_ids):
            return
        committed = False
        def action():
            nonlocal committed
            store = self.stores[key]
            task = store.by_id[ident]
            replacement = None
            if not task.parent_id:
                index = store.tasks.index(task)
                neighbors = store.tasks[index + 1:] + list(reversed(store.tasks[:index]))
                replacement = neighbors[0].id if neighbors else None
            store.delete(ident)
            committed = True
            if self.session and self.session.queue == key:
                if self.session.view_task_id == ident:
                    self.session.view_task_id = replacement
                if self.work_draft and self.work_draft["parent_id"] == ident:
                    self.work_draft = None
                self.session.last_completion = None
                self.reconcile_results()
                self.checkpoint()
        self.safe(action)
        if committed:
            for removed in removed_ids:
                editor = self.task_edits.get(removed)
                if editor:
                    editor.suspended = True
            self.refresh_overview()
            if self.session and self.pages.currentWidget() == self.work:
                self.render_work()

    def reconcile_results(self):
        """Keep this session's credit consistent with the current Markdown marks."""
        store = self.stores.get(self.session.queue)
        if not store:
            return
        for ident in list(self.session.completed):
            task = store.by_id.get(ident)
            if not task or not task.completed:
                self.session.completed.pop(ident)
            else:
                self.session.completed[ident] = {"text": task.text, "parent_id": task.parent_id}

    def task_menu(self, key, ident, row=None):
        task = self.stores[key].by_id.get(ident)
        if task is None or self.busy:
            return
        menu = QMenu(self)
        menu.setObjectName("taskActions")
        menu.setAttribute(Qt.WA_DeleteOnClose)
        if not task.parent_id:
            menu.addAction("Добавить подпункт", lambda: self.show_work_draft(ident) if self.pages.currentWidget() == self.work else self.add_child(key, ident))
            menu.addSeparator()
        action = menu.addAction("Удалить подпункт" if task.parent_id else "Удалить задачу и подпункты" if task.children else "Удалить задачу", lambda: self.delete_task(key, ident))
        action.setObjectName("deleteTaskAction")
        self.active_task_menu = menu
        menu.aboutToHide.connect(lambda: setattr(self, "active_task_menu", None) if self.active_task_menu is menu else None)
        if row:
            row.editor.menu_open = True
            def resume_editor():
                row.editor.menu_open = False
                row.editor.request_save()
            menu.aboutToHide.connect(lambda: QTimer.singleShot(0, resume_editor))
            menu.popup(row.menu_button.mapToGlobal(QPoint(0, row.menu_button.height() + 4)))
        else:
            menu.popup(self.cursor().pos())

    def add_child(self, key, ident):
        if self.busy or (self.session and self.session.ended_at):
            return
        self.overview_add_parents[key] = ident
        self.update_overview_draft(key)
        self.add_inputs[key].setFocus(Qt.OtherFocusReason)

    def update_overview_draft(self, key):
        store = self.stores.get(key)
        parent = store.by_id.get(self.overview_add_parents.get(key)) if store else None
        if parent is None:
            self.overview_add_parents.pop(key, None)
        field = self.add_inputs[key]
        field.setPlaceholderText("Добавить подпункт…" if parent else "Добавить задачу…")
        field.setToolTip(f"Подпункт к: {parent.text}" if parent else "Новая задача в начало очереди")
        self.add_cancels[key].setVisible(parent is not None)

    def cancel_overview_draft(self, key):
        self.overview_add_parents.pop(key, None)
        self.add_inputs[key].clear()
        self.update_overview_draft(key)

    def submit_overview_task(self, key):
        if self.add_task(key, self.add_inputs[key].text(), self.overview_add_parents.get(key)):
            self.cancel_overview_draft(key)

    def move_task(self, key, ident, direction):
        if self.safe(lambda: self.stores[key].move(ident, direction)):
            self.refresh_overview()

    def reorder_task(self, key, ident, before, fingerprint, *, refresh=True):
        def action():
            store = self.stores[key]
            if store.fingerprint != fingerprint:
                raise QueueError("Очередь изменилась во время перетаскивания. Обновите её.")
            store.move_before(ident, before)
            window = self.queue_windows[key]
            if window.start and store.active:
                window.anchor = store.active[min(window.start, len(store.active) - 1)].id
        committed = self.safe(action)
        if committed and refresh:
            self.refresh_overview()
        return committed

    def checkpoint(self):
        if self.session:
            store = self.stores.get(self.session.queue)
            index = next((i for i, t in enumerate(store.tasks) if t.id == self.session.view_task_id), None) if store else None
            # Before the first Markdown write IDs are temporary. An unchanged file
            # can safely restore the same position without writing just to browse.
            self.session.view_task_position = {"index": index, "fingerprint": store.fingerprint} if index is not None else None
            self.state.write("active_session.json", {"session": self.session.to_dict()})

    def start_session(self):
        if not self.flush_overview_edits():
            return
        def action():
            if self.session and not self.session.ended_at:
                raise StateError("Уже есть активное занятие.")
            store = self.stores[self.selected]
            store.load()
            if not store.active:
                raise QueueError("В очереди нет невыполненных задач.")
            if self.recovery:
                raise StateError("Сначала восстановите и завершите незавершённое занятие.")
            minutes = self.duration.value()
            if not self.duration.hasAcceptableInput() or minutes <= 0:
                raise StateError("Выберите длительность занятия: кнопками или вручную.")
            self.settings["minutes"] = minutes
            self.state.write("settings.json", self.settings)
            self.session = Session(self.selected, store.path, minutes)
            try:
                self.checkpoint()
            except StateError:
                self.session = None
                raise
            self.pages.setCurrentWidget(self.work)
            self.sidebar.hide()
            self.render_work()
        self.safe(action)

    def restore_session(self):
        def action():
            session = Session.restore(self.recovery)
            if session.ended_at:
                self.state.save_session(session.to_dict())
                self.state.write("active_session.json", {"session": None})
                self.recovery = None
                self.refresh_overview()
                self.show_stats()
                return
            store = QueueStore(session.path, self.state.backups)
            if session.pending:
                pending = session.pending
                changes = pending["changes"]
                expected = "before" if pending["kind"] == "undo" else "after"
                if all(c["id"] in store.by_id and store.by_id[c["id"]].text == c["text"] and store.by_id[c["id"]].done == c[expected] for c in changes):
                    if pending["kind"] == "complete":
                        session.register(Completion(changes))
                        if any(c["parent_id"] is None for c in changes):
                            session.view_task_id = None
                    else:
                        for c in changes:
                            session.completed.pop(c["id"], None)
                session.pending = None
            session.last_completion = None
            settings = {**self.settings, "paths": {**self.settings["paths"], session.queue: session.path}}
            self.state.write("settings.json", settings)
            self.state.write("active_session.json", {"session": session.to_dict()})
            self.settings = settings
            self.session = session
            self.selected = session.queue
            self.stores[session.queue] = store
            self.reconcile_results()
            self.recovery = None
            self.pages.setCurrentWidget(self.work)
            self.sidebar.hide()
            self.render_work()
        self.safe(action)

    def render_work(self):
        previous_id = getattr(getattr(self, "current_task", None), "id", None)
        scroll_value = self.task_scroll.verticalScrollBar().value() if hasattr(self, "task_scroll") else 0
        for editor in self.work.findChildren(InlineTaskEdit):
            editor.suspended = True
        focused = QApplication.focusWidget()
        if focused and self.work.isAncestorOf(focused):
            # Prevent Qt from transferring focus into a different row and scrolling
            # it into view while the previous completion widgets are replaced.
            self.work.setFocus(Qt.OtherFocusReason)
        clear(self.work_layout)
        self.work_layout.setSpacing(8)
        top = QHBoxLayout()
        top.addWidget(label(NAMES[self.session.queue], "brand"))
        top.addStretch()
        top.addWidget(IconButton("refresh", self.reload, "Обновить очередь; отмена будет сброшена"))
        self.work_layout.addLayout(top)
        self.work_layout.addSpacing(4)
        self.work_panel = QFrame()
        self.work_panel.setObjectName("workTask")
        panel = QVBoxLayout(self.work_panel)
        panel.setContentsMargins(16, 12, 16, 12)
        panel.setSpacing(8)
        self.work_card = WorkContent()
        card = QVBoxLayout(self.work_card)
        card.setSizeConstraint(QLayout.SetNoConstraint)
        card.setContentsMargins(0, 0, 0, 0)
        card.setSpacing(4)
        store = self.stores.get(self.session.queue)
        task = store.by_id.get(self.session.view_task_id) if store else None
        position = self.session.view_task_position
        if task is None and self.session.view_task_id and store and position and position["fingerprint"] == store.fingerprint and position["index"] < len(store.tasks):
            task = store.tasks[position["index"]]
        if task is None or task.parent_id:
            task = store.active[0] if store and store.active else None
        self.session.view_task_id = task.id if task else None
        heading = QHBoxLayout()
        heading.addWidget(label("СЕЙЧАС В РАБОТЕ" if task and not task.completed else "ВЫПОЛНЕННАЯ ЗАДАЧА" if task else "ОЧЕРЕДЬ ЗАВЕРШЕНА" if store else "ОЧЕРЕДЬ НЕДОСТУПНА", "eyebrow"))
        heading.addStretch()
        index = store.tasks.index(task) if task else len(store.tasks) if store else 0
        self.back_button = IconButton("back", lambda: self.browse_task(-1), "Предыдущая задача")
        self.forward_button = IconButton("forward", lambda: self.browse_task(1), "Следующая задача · пропустить без выполнения")
        self.back_button.setEnabled(bool(store and index > 0))
        self.forward_button.setEnabled(bool(store and index < len(store.tasks) - 1))
        heading.addWidget(self.back_button)
        heading.addWidget(self.forward_button)
        panel.addLayout(heading)
        self.current_task = task
        self.work_checkboxes = {}
        self.work_root_checkbox = None
        self.work_editors = {}
        self.work_menu_buttons = {}
        self.work_rows = {}
        if task:
            row, self.work_root_checkbox = self.work_task_row(task)
            self.task_title = self.work_root_checkbox.caption
            card.addWidget(row)
            for child in task.children:
                row, checkbox = self.work_task_row(child)
                card.addWidget(row)
                self.work_checkboxes[child.id] = checkbox
        else:
            self.task_title = label("Все задачи выполнены" if store else "Не удалось прочитать файл. Исправьте его и нажмите обновление.", "tasktitle", True)
            self.task_title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Minimum)
            card.addWidget(self.task_title)
        if store:
            additions = QHBoxLayout()
            self.add_work_button = button("+ Задача", lambda: self.show_work_draft(), "ghost")
            additions.addWidget(self.add_work_button)
            self.add_child_button = button("+ Подпункт", lambda: self.show_work_draft(task.id) if task else None, "ghost")
            self.add_child_button.setEnabled(task is not None)
            additions.addWidget(self.add_child_button)
            additions.addStretch()
            card.addSpacing(8)
            card.addLayout(additions)
            if self.work_draft is not None:
                self.build_work_draft(card)
        card.setAlignment(Qt.AlignTop)
        self.task_scroll = scroll_area()
        self.task_scroll.setMinimumHeight(0)
        self.task_scroll.setWidget(self.work_card)
        panel.addWidget(self.task_scroll)
        self.task_area = TaskArea(self.work_panel)
        self.work_layout.addWidget(self.task_area, 1)
        self.work_layout.addSpacing(4)
        self.timer_caption = label("ОСТАЛОСЬ", "eyebrow")
        self.timer_caption.setAlignment(Qt.AlignCenter)
        self.timer_label = label("", "timer")
        self.timer_label.setAlignment(Qt.AlignCenter)
        self.elapsed_label = label("", "muted")
        self.elapsed_label.setAlignment(Qt.AlignCenter)
        self.work_layout.addWidget(self.timer_caption)
        self.work_layout.addWidget(self.timer_label)
        self.work_layout.addWidget(self.elapsed_label)
        self.action_block = QWidget()
        block = QVBoxLayout(self.action_block)
        block.setContentsMargins(0, 0, 0, 0)
        block.setSpacing(12)
        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(10)
        self.pause_button = button("Пауза" if self.session.running else "Продолжить", self.toggle_pause)
        self.pause_button.setText("Продолжить")
        self.pause_button.setFixedWidth(self.pause_button.sizeHint().width())
        actions.addWidget(self.pause_button)
        for minutes in (10, 15, 30):
            actions.addWidget(button(f"+{minutes} мин", lambda _, m=minutes: self.extend(m), "ghost"))
        actions.addSpacing(14)
        self.finish_button = button("Завершить занятие", self.finish_session, "finishSession", "Сохранить занятие и фактически отработанное время")
        actions.addWidget(self.finish_button)
        block.addLayout(actions)
        complete_text = "Завершить задачу и подпункты" if task and task.children else "Выполнено  ✓"
        self.complete_button = button(complete_text, lambda: self.complete(self.session.queue, task.id) if task else None, "primary")
        self.complete_button.setMinimumWidth(245)
        self.complete_button.setEnabled(task is not None and not task.completed)
        self.complete_button.setFixedHeight(48)
        block.addWidget(self.complete_button)
        self.action_block.setFixedWidth(actions.sizeHint().width())
        self.work_layout.addWidget(self.action_block, 0, Qt.AlignHCenter)
        self.work_layout.addSpacing(4)
        self.update_clock()
        scroll = self.task_scroll
        def fit_and_restore():
            self.fit_task_title()
            if self.task_scroll is scroll:
                scroll.verticalScrollBar().setValue(scroll_value if task and task.id == previous_id else 0)
        QTimer.singleShot(0, fit_and_restore)

    def fit_task_title(self):
        if self.session and hasattr(self, "task_title"):
            margins = self.work_card.layout().contentsMargins()
            width = max(1, self.work_card.width() - margins.left() - margins.right())
            if not self.work_root_checkbox:
                self.task_title.setFixedHeight(max(20, self.task_title.heightForWidth(width)))
            for editor in self.work_editors.values():
                editor.adjust_height()
            self.work_card.layout().activate()

    def work_task_row(self, task):
        row = TaskRow(task.text, task.completed, bool(task.parent_id), work=True)
        checkbox, editor = row.checkbox, row.editor
        editor.saveRequested.connect(lambda text, t=task.id, e=editor: self.save_work_text(t, text, e))
        editor.cancelled.connect(lambda e=editor: self.mark_editor_error(e, False))
        self.work_editors[task.id] = editor
        checkbox.clicked.connect(lambda checked, t=task.id: self.set_task_checked(self.session.queue, t, checked))
        row.menu_button.clicked.connect(lambda: self.task_menu(self.session.queue, task.id, row))
        self.work_menu_buttons[task.id] = row.menu_button
        self.work_rows[task.id] = row
        return row, checkbox

    def build_work_draft(self, card):
        self.draft_row = QFrame()
        self.draft_row.setObjectName("workDraft")
        draft = QHBoxLayout(self.draft_row)
        draft.setContentsMargins(13, 8, 8, 8)
        draft.setSpacing(10)
        parent = self.work_draft["parent_id"]
        check = TaskCheckBox()
        check.setEnabled(False)
        check.setFixedSize(22, 32)
        draft.addWidget(check, 0, Qt.AlignTop)
        self.draft_editor = InlineTaskEdit(self.work_draft["text"], draft=True)
        self.draft_editor.setProperty("mode", "work")
        font = self.draft_editor.font()
        font.setPixelSize(21)
        self.draft_editor.setFont(font)
        self.update_work_draft_type()
        self.draft_editor.textChanged.connect(lambda: self.work_draft.update(text=self.draft_editor.toPlainText()) if self.work_draft else None)
        self.draft_editor.saveRequested.connect(lambda _: self.save_work_draft())
        self.draft_editor.cancelled.connect(self.cancel_work_draft)
        draft.addWidget(self.draft_editor, 1)
        add = button("Добавить", self.save_work_draft, "primary")
        add.setFixedHeight(34)
        draft.addWidget(add, 0, Qt.AlignTop)
        cancel = IconButton("close", self.cancel_work_draft, "Отменить добавление")
        cancel.setFixedSize(28, 34)
        draft.addWidget(cancel, 0, Qt.AlignTop)
        card.addWidget(self.draft_row)

    def update_work_draft_type(self):
        parent = self.work_draft["parent_id"]
        self.draft_editor.setPlaceholderText("Новый подпункт…" if parent else "Новая задача…")
        self.draft_editor.setAccessibleName("Текст нового подпункта" if parent else "Текст новой задачи")
        target = self.stores[self.session.queue].by_id.get(parent) if parent else None
        self.draft_editor.setToolTip(f"Новый подпункт к: {target.text}" if target else "Новая задача сразу после текущей")

    def browse_task(self, direction):
        if self.busy or not self.session or self.session.ended_at or not self.flush_work_edits():
            return
        store = self.stores.get(self.session.queue)
        if not store:
            return
        index = next((i for i, t in enumerate(store.tasks) if t.id == self.session.view_task_id), len(store.tasks))
        dest = index + direction
        if 0 <= dest < len(store.tasks):
            previous = self.session.view_task_id
            self.session.view_task_id = store.tasks[dest].id
            if not self.safe(self.checkpoint):
                self.session.view_task_id = previous
                return
            self.render_work()

    def advance_after_completion(self, ident):
        store = self.stores[self.session.queue]
        index = next((i for i, t in enumerate(store.tasks) if t.id == ident), -1)
        following = store.tasks[index + 1:] + store.tasks[:index + 1]
        self.session.view_task_id = next((t.id for t in following if not t.completed), None)

    def set_task_checked(self, key, ident, checked):
        if not self.flush_work_edits() or not self.flush_overview_edits():
            for t, control in [(getattr(self, "current_task", None), getattr(self, "work_root_checkbox", None)),
                               *((self.stores[key].by_id.get(i), c) for i, c in getattr(self, "work_checkboxes", {}).items())]:
                if t and control:
                    control.setChecked(t.completed)
            return
        if checked:
            self.complete(key, ident)
        else:
            self.reopen_task(key, ident)

    def reopen_task(self, key, ident):
        if self.busy or (self.session and self.session.ended_at):
            return
        committed = False
        def action():
            nonlocal committed
            change = self.stores[key].reopening_for(ident)
            active = self.session and self.session.queue == key
            if active:
                self.session.pending = {"kind": "reopen", "changes": change.changes}
                self.checkpoint()
            self.stores[key].restore(change, forward=True)
            committed = True
            if active:
                for c in change.changes:
                    self.session.completed.pop(c["id"], None)
                self.session.last_completion = None
                self.session.pending = None
                self.checkpoint()
        if not self.safe(action) and not committed and self.session:
            self.session.pending = None
            if not self.safe(self.checkpoint):
                self.session.pause()
        if self.pages.currentWidget() == self.work:
            self.render_work()
        else:
            self.refresh_overview()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "content_layout"):
            compact = event.size().width() < 1000
            if compact != getattr(self, "compact_layout", None):
                self.compact_layout = compact
                self.sidebar.setFixedWidth(156 if compact else 190)
                self.content_layout.setContentsMargins(24 if compact else 30, 27, 24 if compact else 30, 12)
        if getattr(self, "session", None):
            self.fit_timer.start(0)

    def showEvent(self, event):
        super().showEvent(event)
        self.native_frame.install()

    def complete(self, key, ident):
        if self.busy or (self.session and self.session.ended_at) or not self.flush_work_edits():
            return
        committed = False
        def action():
            nonlocal committed
            self.busy = True
            store = self.stores[key]
            completion = store.completion_for(ident)
            active_session = self.session and not self.session.ended_at and self.session.queue == key
            if active_session:
                self.session.pending = {"kind": "complete", "changes": completion.changes}
                self.checkpoint()
            store.restore(completion, forward=True)
            committed = True
            if active_session:
                self.session.register(completion)
                if self.current_task and self.current_task.id in completion.ids:
                    self.advance_after_completion(self.current_task.id)
                self.checkpoint()
            if self.pages.currentWidget() == self.work:
                font = self.task_title.font()
                if self.current_task.id in completion.ids:
                    font.setStrikeOut(True)
                    self.task_title.setFont(font)
                target = self.work_card if self.current_task.id in completion.ids else self.work_checkboxes.get(ident, self.work_card)
                if target is not self.work_card:
                    font = target.font()
                    font.setStrikeOut(True)
                    target.setFont(font)
                    target.setChecked(True)
                self.animate_completion(target)
            else:
                self.busy = False
                self.refresh_overview()
        if not self.safe(action):
            self.busy = False
            if not committed and self.session and self.session.pending:
                self.session.pending = None
                if not self.safe(self.checkpoint):
                    self.session.pause()
            if self.pages.currentWidget() == self.work:
                self.render_work()
            else:
                self.refresh_overview()

    def animate_completion(self, target):
        self.work.setFocus(Qt.OtherFocusReason)
        self.complete_button.setEnabled(False)
        for control in [self.work_root_checkbox, *self.work_checkboxes.values(), self.back_button, self.forward_button,
                        *self.work_editors.values(), *self.work_menu_buttons.values(), self.add_work_button, self.add_child_button]:
            if control:
                control.setEnabled(False)
        effect = QGraphicsOpacityEffect(target)
        target.setGraphicsEffect(effect)
        animation = QParallelAnimationGroup(self)
        opacity = QPropertyAnimation(effect, b"opacity", animation)
        opacity.setStartValue(1.0)
        opacity.setEndValue(0.0)
        motion = QPropertyAnimation(target, b"pos", animation)
        motion.setStartValue(target.pos())
        motion.setEndValue(target.pos() + QPoint(12, -7))
        for part in (opacity, motion):
            part.setDuration(210)
            part.setEasingCurve(QEasingCurve.InOutQuad)
            animation.addAnimation(part)
        self.animations.append(animation)
        def finished():
            self.busy = False
            self.render_work()
            self.animations.remove(animation)
            animation.deleteLater()
        animation.finished.connect(finished)
        animation.start()

    def undo(self):
        if self.busy or not self.session or self.session.ended_at or not self.session.last_completion:
            return
        committed = False
        def action():
            nonlocal committed
            completion = self.session.last_completion
            self.session.pending = {"kind": "undo", "changes": completion.changes}
            self.checkpoint()
            self.stores[self.session.queue].restore(completion)
            committed = True
            self.session.undo_registered()
            root = next(c["id"] if c["parent_id"] is None else c["parent_id"] for c in completion.changes)
            self.session.view_task_id = root
            self.session.pending = None
            self.checkpoint()
            self.render_work()
            self.notify("Последнее выполнение отменено.")
        if not self.safe(action) and not committed:
            self.session.pending = None
            if not self.safe(self.checkpoint):
                self.session.pause()

    def handle_undo(self):
        editor = QApplication.focusWidget()
        if isinstance(editor, (InlineTaskEdit, QLineEdit)):
            editor.undo()
        else:
            self.undo()

    def update_clock(self):
        if not self.session or not hasattr(self, "timer_label"):
            return
        remaining = self.session.remaining
        self.timer_caption.setText("ОСТАЛОСЬ" if remaining >= 0 else "СВЕРХ ПЛАНА")
        self.timer_label.setText(("+" if remaining < 0 else "") + clock_text(abs(remaining) if remaining < 0 else ceil(remaining)))
        self.elapsed_label.setText(f"В работе  {clock_text(self.session.active_seconds)}")
        self.pause_button.setText("Пауза" if self.session.running else "Продолжить")

    def tick(self):
        if not self.session or self.session.ended_at:
            return
        if self.session.tick():
            self.notify("Занятие приостановлено после сна или перерыва обновлений. Продолжите, когда будете готовы.")
        self.update_clock()
        self.checkpoint_ticks += 1
        if self.checkpoint_ticks >= 20:
            self.checkpoint_ticks = 0
            if not self.safe(self.checkpoint):
                self.session.pause()

    def toggle_pause(self):
        if self.busy or not self.session or self.session.ended_at:
            return
        if self.session.queue not in self.stores:
            self.notify("Сначала восстановите доступ к очереди и нажмите обновление.", True)
            return
        if self.session.running:
            self.session.pause()
        else:
            self.session.resume()
        self.update_clock()
        if not self.safe(self.checkpoint):
            self.session.pause()
            self.update_clock()

    def extend(self, minutes):
        if self.busy or not self.session or self.session.ended_at:
            return
        self.session.extend(minutes)
        self.update_clock()
        self.safe(self.checkpoint)

    def finish_session(self):
        if self.busy or not self.session or not self.flush_work_edits():
            return
        def action():
            self.session.finish()
            self.checkpoint()
            self.state.save_session(self.session.to_dict())
            self.state.write("active_session.json", {"session": None})
            roots, children = self.session.counts
            self.notify(f"Занятие сохранено: {minutes_text(self.session.active_seconds)} · задач {roots} · подпунктов {children}.")
            self.session = None
            self.recovery = None
            self.overview_dirty = True
            self.stats_dirty = True
            self.show_stats()
        if not self.safe(action) and self.session and self.session.ended_at:
            for control in (self.complete_button, self.pause_button, self.back_button, self.forward_button,
                            self.work_root_checkbox, *self.work_checkboxes.values(), *self.work_editors.values(), *self.work_menu_buttons.values(),
                            getattr(self, "add_work_button", None), getattr(self, "add_child_button", None)):
                if control:
                    control.setEnabled(False)

    def show_stats(self):
        if self.session and not self.session.ended_at:
            self.notify("Результаты будут доступны после завершения занятия.")
            return
        def action():
            path = self.state.directory / "sessions.json"
            stat = path.stat() if path.exists() else None
            signature = (stat.st_mtime_ns, stat.st_size, date.today()) if stat else (None, date.today())
            if not self.stats_dirty and signature == getattr(self, "stats_signature", None):
                self.pages.setCurrentWidget(self.stats)
                self.sidebar.show()
                self.nav_overview.setChecked(False)
                self.nav_stats.setChecked(True)
                return
            history = self.state.history()
            clear(self.stats_layout)
            self.stats_layout.setSpacing(12)
            self.stats_layout.addWidget(label("Время, которое вы вложили", "heading"))
            self.stats_layout.addWidget(label("Занятия и выполненные задачи — без пауз и двойного учёта.", "subtitle"))
            totals = daily_seconds(history)
            today = date.today().isoformat()
            cards = QHBoxLayout()
            for key, name in NAMES.items():
                card = QFrame()
                card.setObjectName("statCard")
                box = QVBoxLayout(card)
                box.setContentsMargins(20, 18, 20, 18)
                box.addWidget(label(name, "muted"))
                box.addWidget(label(minutes_text(totals.get((today, key), 0)), "heading"))
                box.addWidget(label("сегодня", "muted"))
                cards.addWidget(card)
            self.stats_layout.addLayout(cards)
            self.stats_layout.addSpacing(6)
            scroll = scroll_area()
            self.stats_scroll = scroll
            body = QWidget()
            body.setObjectName("scrollBody")
            box = QVBoxLayout(body)
            box.setContentsMargins(0, 4, 4, 0)
            box.setSpacing(10)
            for day in sorted({d for d, _ in totals}, reverse=True)[:60]:
                box.addWidget(label(datetime.fromisoformat(day).strftime("%d.%m.%Y"), "brand"))
                for key, name in NAMES.items():
                    if totals.get((day, key), 0):
                        box.addWidget(label(f"{name}   ·   {minutes_text(totals[day, key])}", "muted"))
            box.addSpacing(18)
            history_controls = QHBoxLayout()
            history_controls.addWidget(label(f"ЗАНЯТИЯ · {len(history)}", "eyebrow"))
            history_controls.addStretch()
            for only_favorites, title in ((False, "Все"), (True, "Избранные")):
                control = button(title, lambda _, value=only_favorites: self.filter_history(value), "historyFilter")
                control.setCheckable(True)
                control.setChecked(self.stats_favorites_only == only_favorites)
                history_controls.addWidget(control)
            box.addLayout(history_controls)
            visible = [s for s in history if not self.stats_favorites_only or s.get("favorite", False)]
            self.history_rows = {}
            self.favorite_buttons = {}
            self.delete_buttons = {}
            for record in reversed(visible):
                completed = record.get("completed", {})
                roots = sum(c["parent_id"] is None for c in completed.values())
                children = len(completed) - roots
                started = datetime.fromisoformat(record["started_at"]).strftime("%d.%m · %H:%M")
                row = QFrame()
                row.setObjectName("historyRow")
                line = QHBoxLayout(row)
                line.setContentsMargins(14, 12, 12, 12)
                line.setSpacing(12)
                info = label(f"{started}   {NAMES[record['queue']]}   ·   {minutes_text(record['active_seconds'])}\nЗадач: {roots}   Подпунктов: {children}", wrap=True)
                info.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Minimum)
                line.addWidget(info, 1)
                favorite = record.get("favorite", False)
                star = button("★" if favorite else "☆", lambda _, i=record["id"], v=not favorite: self.favorite_session(i, v), "favorite", "Убрать из избранного" if favorite else "Добавить в избранное")
                star.setProperty("selected", favorite)
                star.setFixedSize(42, 42)
                line.addWidget(star)
                delete = button("Удалить", lambda _, i=record["id"]: self.delete_session(i), "deleteSession", "Удалить занятие из истории и итогов")
                delete.setFixedHeight(42)
                line.addWidget(delete)
                box.addWidget(row)
                self.history_rows[record["id"]] = row
                self.favorite_buttons[record["id"]] = star
                self.delete_buttons[record["id"]] = delete
            if not visible:
                box.addWidget(label("Отметьте занятие звездой, чтобы добавить его в избранное." if self.stats_favorites_only else "Здесь появятся результаты первого занятия.", "muted", True))
            box.addStretch()
            scroll.setWidget(body)
            self.stats_layout.addWidget(scroll, 1)
            self.pages.setCurrentWidget(self.stats)
            self.sidebar.show()
            self.nav_overview.setChecked(False)
            self.nav_stats.setChecked(True)
            self.stats_dirty = False
            self.stats_signature = signature
        self.safe(action)

    def filter_history(self, favorites_only):
        self.stats_favorites_only = favorites_only
        self.stats_dirty = True
        self.show_stats()

    def favorite_session(self, ident, favorite):
        if self.session or self.recovery:
            self.notify("Сначала сохраните завершение или восстановите незавершённое занятие.", True)
            return
        if self.safe(lambda: self.state.set_favorite(ident, favorite)):
            self.stats_dirty = True
            self.show_stats()

    def delete_session(self, ident):
        if self.session or self.recovery:
            self.notify("Сначала сохраните завершение или восстановите незавершённое занятие.", True)
            return
        if self.safe(lambda: self.state.delete_session(ident)):
            self.stats_dirty = True
            self.show_stats()
            self.notify("Занятие удалено из истории и итогов.")

    def closeEvent(self, event):
        if self.busy or not self.flush_work_edits() or not self.flush_overview_edits():
            event.ignore()
            return
        if self.session and not self.session.ended_at:
            self.session.pause()
            if not self.safe(self.checkpoint):
                event.ignore()
                return
        self.native_frame.uninstall()
        event.accept()


def apply_theme(app):
    # Windows' offscreen Qt backend does not discover system fonts itself.
    if app.platformName() == "offscreen":
        for filename in ("segoeui.ttf", "segoeuib.ttf", "seguisb.ttf", "seguisym.ttf", "consola.ttf"):
            font = Path("C:/Windows/Fonts") / filename
            if font.exists():
                QFontDatabase.addApplicationFont(str(font))
    app.setStyle("Fusion")
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor("#1e1f22"))
    palette.setColor(QPalette.Base, QColor("#1e1f22"))
    palette.setColor(QPalette.Text, QColor("#dfe1e5"))
    palette.setColor(QPalette.WindowText, QColor("#dfe1e5"))
    palette.setColor(QPalette.Button, QColor("#333539"))
    palette.setColor(QPalette.ButtonText, QColor("#dfe1e5"))
    palette.setColor(QPalette.Highlight, QColor("#3574f0"))
    app.setPalette(palette)
    check = Path(__file__).with_name("check.svg").as_posix()
    app.setStyleSheet(Path(__file__).with_name("theme.qss").read_text(encoding="utf-8") + f'\nQCheckBox::indicator:checked {{ image: url("{check}"); }}')
