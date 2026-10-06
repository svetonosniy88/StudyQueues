"""Small widgets for wrapped completion rows and mouse-only task reordering."""
from math import ceil, exp
from time import perf_counter
from dataclasses import dataclass
from PySide6.QtCore import Qt, QMimeData, QPoint, QTimer, QSize, QRectF, Signal, QPropertyAnimation, QParallelAnimationGroup, QEasingCurve
from PySide6.QtGui import QDrag, QPainter, QColor, QPen, QPixmap, QTextDocument, QTextOption
from PySide6.QtWidgets import (QApplication, QWidget, QFrame, QCheckBox,
                              QHBoxLayout, QVBoxLayout, QToolButton, QSizePolicy,
                              QScrollBar, QStyle, QStyleOptionSlider, QPushButton, QPlainTextEdit, QStyleOptionButton, QGraphicsOpacityEffect)


class IconButton(QPushButton):
    """Consistent line icons, independent of the system font's symbol glyphs."""
    def __init__(self, kind, action, tip, name="ghost"):
        super().__init__()
        self.kind = kind
        self.setObjectName(name)
        self.setFixedSize(38, 36)
        self.setToolTip(tip)
        self.setAccessibleName(tip)
        self.setCursor(Qt.PointingHandCursor)
        self.clicked.connect(action)

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor("#dfe1e5" if self.isEnabled() else "#676b73"), 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.translate(self.width() / 2, self.height() / 2)
        if self.kind == "close":
            p.drawLine(-5, -5, 5, 5)
            p.drawLine(-5, 5, 5, -5)
        elif self.kind == "minimize":
            p.drawLine(-6, 3, 6, 3)
        elif self.kind == "maximize":
            if self.window().isMaximized():
                p.drawRect(QRectF(-3, -6, 9, 9))
                p.fillRect(QRectF(-6, -3, 9, 9), QColor("#2b2d30"))
                p.drawRect(QRectF(-6, -3, 9, 9))
            else:
                p.drawRect(QRectF(-5, -5, 10, 10))
        elif self.kind in ("back", "forward"):
            if self.kind == "forward":
                p.scale(-1, 1)
            p.drawLine(-6, 0, 6, 0)
            p.drawLine(-6, 0, -1, -5)
            p.drawLine(-6, 0, -1, 5)
        elif self.kind in ("expand", "collapse"):
            if self.kind == "collapse":
                p.rotate(90)
            p.drawLine(-2, -4, 2, 0)
            p.drawLine(2, 0, -2, 4)
        elif self.kind == "delete":
            p.drawLine(-6, -4, 6, -4)
            p.drawLine(-2, -6, 2, -6)
            p.drawLine(-4, -2, -3, 6)
            p.drawLine(-3, 6, 3, 6)
            p.drawLine(3, 6, 4, -2)
            p.drawLine(-1, -1, -1, 3)
            p.drawLine(1, -1, 1, 3)
        elif self.kind == "menu":
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#9da0a8" if self.isEnabled() else "#676b73"))
            for x in (-5, 0, 5):
                p.drawEllipse(QRectF(x - 1, -1, 2, 2))
        elif self.kind == "refresh":
            p.drawArc(QRectF(-6, -6, 12, 12), 35 * 16, 285 * 16)
            p.drawLine(6, -6, 6, -1)
            p.drawLine(6, -1, 1, -1)


class QueueCard(QFrame):
    """Selection stays visible and uses a neutral state for the chosen queue."""
    def __init__(self, selected):
        super().__init__()
        self.setObjectName("queueCard")
        self.setProperty("selected", selected)


class TaskCheckBox(QCheckBox):
    """Only the indicator toggles completion; the adjacent text is an editor."""
    def hitButton(self, pos):
        option = QStyleOptionButton()
        self.initStyleOption(option)
        return self.style().subElementRect(QStyle.SE_CheckBoxIndicator, option, self).contains(pos)


class InlineTaskEdit(QPlainTextEdit):
    saveRequested = Signal(str)
    cancelled = Signal()

    def __init__(self, text="", draft=False):
        super().__init__()
        self._height_cache = {}
        self.saved_text = text
        self.draft = draft
        self.suspended = False
        self.menu_open = False
        self.setObjectName("workText")
        self.setPlainText(text)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setTabChangesFocus(True)
        self.document().setDocumentMargin(0)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self.textChanged.connect(self.adjust_height)

    def heightForWidth(self, width):
        key = (width, self.document().revision(), self.font().key(), self.logicalDpiY())
        if key in self._height_cache:
            return self._height_cache[key]
        doc = QTextDocument()
        doc.setDefaultFont(self.font())
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        doc.setDefaultTextOption(option)
        doc.setDocumentMargin(0)
        doc.setPlainText(self.toPlainText() or " ")
        doc.setTextWidth(max(1, width - 4))
        height = max(self.fontMetrics().height() + 4, ceil(doc.size().height()) + 4)
        if len(self._height_cache) >= 12:
            self._height_cache.clear()
        self._height_cache[key] = height
        return height

    def hasHeightForWidth(self):
        return True

    def sizeHint(self):
        return QSize(360, self.heightForWidth(self.width()))

    def minimumSizeHint(self):
        return QSize(40, self.fontMetrics().height() + 4)

    def adjust_height(self):
        height = self.heightForWidth(self.width())
        if self.minimumHeight() != height or self.maximumHeight() != height:
            self.setFixedHeight(height)
            self.updateGeometry()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.adjust_height()

    def text(self):
        return self.toPlainText()

    def setText(self, text):
        self.setPlainText(text)

    def clear(self):
        self.setPlainText("")

    def mark_saved(self, text):
        self.saved_text = text
        self.setProperty("error", False)
        self.style().unpolish(self)
        self.style().polish(self)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.request_save()
            event.accept()
        elif event.key() == Qt.Key_Escape:
            if self.draft:
                self.cancelled.emit()
            else:
                self.setPlainText(self.saved_text)
                self.cancelled.emit()
                self.mark_saved(self.saved_text)
                self.clearFocus()
            event.accept()
        else:
            super().keyPressEvent(event)

    def request_save(self):
        if not self.suspended and not self.menu_open and (self.draft or self.toPlainText().strip() != self.saved_text):
            self.saveRequested.emit(self.toPlainText())

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        # Let a clicked checkbox/delete/navigation action run before saving.
        # A rebuilding page suspends old editors before transferring focus.
        if not self.draft:
            QTimer.singleShot(0, self.request_save)


@dataclass(frozen=True)
class RowMetrics:
    """Logical pixels, shared by overview and work instead of per-screen offsets."""
    padding: int = 8
    gap: int = 10
    indent: int = 32
    indicator: int = 22
    menu: int = 28
    minimum_height: int = 44


ROW = RowMetrics()


class BranchMark(QWidget):
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor("#858993"), 1.5, Qt.SolidLine, Qt.RoundCap))
        # Fixed gap from the end of the dash to the indicator in the adjacent line.
        y = self.height() / 2
        painter.drawLine(QPoint(self.width() - 18, round(y)), QPoint(self.width() - 8, round(y)))


class TaskRow(QFrame):
    """One wrapped row: first-line anchors, equal indicator padding, stable menu slot."""
    def __init__(self, text, completed=False, nested=False, work=False, handle=None, disclosure=None):
        super().__init__()
        self.setObjectName("taskRow")
        self.editor = InlineTaskEdit(text)
        self.editor.setProperty("mode", "work" if work else "overview")
        self.editor.setProperty("root", not nested)
        self.editor.setAccessibleName("Текст подпункта" if nested else "Текст задачи")
        self.editor.setToolTip("Редактировать · Enter — сохранить, Esc — отменить")
        # Explicit font metrics match the themed editor at both densities.
        font = self.editor.font()
        font.setPixelSize(21 if work else 16)
        font.setStrikeOut(completed)
        self.editor.setFont(font)
        anchor = self.editor.fontMetrics().height() + 4
        padding = max(ROW.padding, ceil((ROW.minimum_height - anchor) / 2))
        indicator_inset = padding + (anchor - ROW.indicator) // 2
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.prefix = None
        if nested:
            self.prefix = BranchMark()
            self.prefix.setFixedSize(ROW.indent * (1 if work else 2) + (0 if work else 24), anchor + padding * 2)
        elif handle:
            self.prefix = handle
            self.prefix.setFixedSize(ROW.indent, anchor + padding * 2)
        if self.prefix:
            outer.addWidget(self.prefix, 0, Qt.AlignTop)
        self.disclosure = disclosure
        if not work and not nested:
            slot = disclosure or QWidget()
            slot.setFixedSize(24, anchor + padding * 2)
            outer.addWidget(slot, 0, Qt.AlignTop)
        self.line = QFrame()
        self.line.setObjectName("taskLine")
        self.line.setMinimumHeight(ROW.minimum_height)
        layout = QHBoxLayout(self.line)
        layout.setContentsMargins(indicator_inset, padding, ROW.padding, padding)
        layout.setSpacing(ROW.gap)
        self.checkbox = TaskCheckBox()
        self.checkbox.setObjectName("taskCheck")
        self.checkbox.setFixedSize(ROW.indicator, anchor)
        self.checkbox.setChecked(completed)
        self.checkbox.setAccessibleName(text)
        self.checkbox.setToolTip("Вернуть в работу" if completed else "Отметить выполненным")
        self.checkbox.setCursor(Qt.PointingHandCursor)
        self.checkbox.caption = self.editor
        layout.addWidget(self.checkbox, 0, Qt.AlignTop)
        layout.addWidget(self.editor, 1)
        self.menu_button = IconButton("menu", lambda: None, "Действия с подпунктом" if nested else "Действия с задачей", "taskMenu")
        self.menu_button.setFixedSize(ROW.menu, anchor)
        self.menu_button.setFocusPolicy(Qt.NoFocus)
        layout.addWidget(self.menu_button, 0, Qt.AlignTop)
        outer.addWidget(self.line, 1)


class LaunchBar(QFrame):
    """One stable row; narrow windows use shorter labels rather than a smaller font."""
    def __init__(self, title, duration, reset, additions, start):
        super().__init__()
        self.setObjectName("launchBar")
        self.duration, self.additions, self.start = duration, additions, start
        self.compact = None
        self.line = QHBoxLayout(self)
        self.line.addWidget(title)
        self.line.addStretch(1)
        duration.setFixedWidth(92)
        for control in (reset, *additions.values(), duration, start):
            control.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
            control.setMinimumWidth(36 if control in additions.values() else 58)
            self.line.addWidget(control)
        for control in (duration, reset, *additions.values(), start):
            control.setFixedHeight(44)
        self.adapt(0)

    def adapt(self, width):
        compact = width < 790
        if self.compact == compact:
            return
        self.compact = compact
        self.setProperty("compact", compact)
        self.line.setContentsMargins(12 if compact else 16, 16, 12 if compact else 16, 16)
        self.line.setSpacing(5 if compact else 8)
        self.duration.setFixedWidth(82 if compact else 92)
        for minutes, control in self.additions.items():
            control.setText(f"+{minutes}" if compact else f"+{minutes} мин")
            control.setToolTip(f"Добавить {minutes} минут")
        self.start.setText("Начать" if compact else "Начать занятие")
        for control in self.findChildren(QWidget):
            control.style().unpolish(control)
            control.style().polish(control)

    def resizeEvent(self, event):
        self.adapt(event.size().width())
        super().resizeEvent(event)


class CapsuleScrollBar(QScrollBar):
    """Qt handles interaction; the visible thumb has reliably rounded ends."""
    def __init__(self, parent=None):
        super().__init__(Qt.Vertical, parent)
        self.setAttribute(Qt.WA_OpaquePaintEvent, False)
        self.setMouseTracking(True)

    def thumb_rect(self):
        option = QStyleOptionSlider()
        self.initStyleOption(option)
        rect = QRectF(self.style().subControlRect(QStyle.CC_ScrollBar, option, QStyle.SC_ScrollBarSlider, self))
        rect.setLeft((self.width() - 8) / 2)
        rect.setWidth(8)
        return rect

    def paintEvent(self, event):
        if self.maximum() <= self.minimum():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#a2b9de" if self.isSliderDown() else "#8190a6" if self.underMouse() else "#59616d"))
        painter.drawRoundedRect(self.thumb_rect(), 4, 4)

    def enterEvent(self, event):
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.update()


class WorkContent(QWidget):
    """Use wrapped height at the actual width, not Qt's unconstrained size hint."""
    def __init__(self):
        super().__init__()
        self.setObjectName("scrollBody")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

    def heightForWidth(self, width):
        return self.layout().totalHeightForWidth(width) if self.layout() else 0

    def hasHeightForWidth(self):
        return True

    def sizeHint(self):
        return QSize(600, self.heightForWidth(max(1, self.width())))

    def minimumSizeHint(self):
        return QSize(0, self.heightForWidth(max(1, self.width())))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if event.size().width() != event.oldSize().width():
            self.updateGeometry()


class TaskArea(QWidget):
    """Responsive side margins, approaching one fifth on each side of a wide window."""
    def __init__(self, scroll):
        super().__init__()
        self.line = QHBoxLayout(self)
        self.line.setContentsMargins(12, 0, 12, 0)
        self.line.addWidget(scroll)

    def resizeEvent(self, event):
        margin = max(12, min(self.width() // 5, (self.width() - 640) // 2))
        self.line.setContentsMargins(margin, 0, margin, 0)
        super().resizeEvent(event)


class DragHandle(QToolButton):
    def __init__(self, area, ident):
        super().__init__()
        self.area = area
        self.ident = ident
        self.anchor = None
        self.setObjectName("dragHandle")
        self.setToolTip("Перетащить задачу вместе с подпунктами")
        self.setAccessibleName("Перетащить задачу")
        self.setCursor(Qt.OpenHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setFixedSize(30, 36)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor("#a5c7ff" if self.underMouse() else "#858993"), 1.5, Qt.SolidLine, Qt.RoundCap))
        x, y = self.width() / 2, self.height() / 2
        for offset in (-4, 0, 4):
            painter.drawLine(QPoint(round(x - 6), round(y + offset)), QPoint(round(x + 6), round(y + offset)))

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.anchor = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.anchor is None or not event.buttons() & Qt.LeftButton:
            return
        if (event.position().toPoint() - self.anchor).manhattanLength() < QApplication.startDragDistance():
            return
        if not self.area.owner.flush_overview_edits():
            self.anchor = None
            return
        origin = self.mapTo(self.area.scroll.viewport(), self.anchor)
        self.anchor = None
        # The window owns the drag because a successful drop rebuilds the list.
        drag = QDrag(self.area.window())
        mime = QMimeData()
        mime.setData(TaskList.MIME, self.ident.encode("utf-8"))
        drag.setMimeData(mime)
        group = dict(self.area.groups)[self.ident]
        source = group.grab()
        preview = QPixmap(source.size())
        preview.setDevicePixelRatio(source.devicePixelRatio())
        preview.fill(Qt.transparent)
        painter = QPainter(preview)
        painter.setOpacity(0.68)
        painter.drawPixmap(0, 0, source)
        painter.end()
        drag.setPixmap(preview)
        drag.setHotSpot(QPoint(20, 20))
        self.area.dragged_id = self.ident
        self.area.fingerprint = self.area.window().stores[self.area.key].fingerprint
        self.setDown(False)
        area = self.area
        area.origin_y = origin.y()
        area.active_drag = True
        area.pending_drop = None
        result = Qt.IgnoreAction
        try:
            result = drag.exec(Qt.MoveAction)
        finally:
            area.active_drag = False
            area.stop_scroll()
            if not area.pending_drop or result != Qt.MoveAction:
                area.clear_drop()
                area.pending_drop = None
            area.dragged_id = None
        if area.pending_drop:
            area.schedule_drop(*area.pending_drop)
            area.pending_drop = None

    def mouseReleaseEvent(self, event):
        self.anchor = None
        super().mouseReleaseEvent(event)


class DropSlot(QWidget):
    """The insertion gap occupies a full task block, including its children."""
    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setObjectName("dropSlot")
        self.hide()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor(121, 168, 255, 150), 1.5, Qt.DashLine))
        painter.setBrush(QColor(121, 168, 255, 32))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 8, 8)


class TaskList(QWidget):
    MIME = "application/x-studyqueues-task"

    def __init__(self, key, scroll, window):
        super().__init__()
        self.key, self.scroll, self.owner = key, scroll, window
        self.groups = []
        self.dragged_id = None
        self.active_drag = False
        self.pending_drop = None
        self.fingerprint = None
        self.drop_before = None
        self.tail_before = None
        self.drop_y = None
        self.origin_y = None
        self.scroll_target = 0.0
        self.scroll_velocity = 0.0
        self.scroll_fraction = 0.0
        self.last_tick = perf_counter()
        self.hover_pos = QPoint()
        self.preview_id = None
        self.preview_geometries = None
        self.targets = {}
        self.motion = QParallelAnimationGroup(self)
        self.motion_callback = None
        self.motion.finished.connect(self.motion_finished)
        self.slot = DropSlot(self)
        self.setObjectName("scrollBody")
        self.setAcceptDrops(True)
        self.rows = QVBoxLayout(self)
        self.rows.setContentsMargins(0, 4, 3, 4)
        self.rows.setSpacing(8)
        self.edge_timer = QTimer(self)
        self.edge_timer.setInterval(16)
        self.edge_timer.timeout.connect(self.scroll_edge)

    def payload(self, event):
        if event.source() is not self.owner or not event.mimeData().hasFormat(self.MIME):
            return None
        ident = bytes(event.mimeData().data(self.MIME)).decode("utf-8", errors="replace")
        return ident if ident == self.dragged_id and ident in dict(self.groups) else None

    def dragEnterEvent(self, event):
        if self.payload(event):
            if self.origin_y is None:
                self.origin_y = self.mapTo(self.scroll.viewport(), event.position().toPoint()).y()
            self.begin_preview()
            event.setDropAction(Qt.MoveAction)
            event.accept()
        else:
            event.ignore()

    def locate_drop(self, pos):
        if self.preview_geometries is not None:
            # Staying inside the open gap retains its position rather than
            # oscillating across neighbours as their animations run.
            if not self.slot.geometry().top() <= pos.y() <= self.slot.geometry().bottom():
                before = self.tail_before
                for ident, group in self.groups:
                    if ident != self.preview_id and pos.y() < self.targets[ident].center().y():
                        before = ident
                        break
                if before != self.drop_before:
                    self.arrange_preview(before)
            self.drop_y = self.slot.y()
            return
        self.drop_before = self.tail_before
        self.drop_y = self.groups[-1][1].geometry().bottom() + 4 if self.groups else 4
        for ident, group in self.groups:
            if pos.y() < group.geometry().center().y():
                self.drop_before = ident
                self.drop_y = group.geometry().top() - 4
                break
        self.update()

    def dragMoveEvent(self, event):
        if not self.payload(event):
            event.ignore()
            return
        self.hover_pos = self.mapTo(self.scroll.viewport(), event.position().toPoint())
        self.locate_drop(event.position().toPoint())
        self.set_scroll_target(self.hover_pos.y())
        if self.scroll_target:
            if not self.edge_timer.isActive():
                self.last_tick = perf_counter()
                self.edge_timer.start()
        else:
            self.stop_scroll()
        event.setDropAction(Qt.MoveAction)
        event.accept()

    def scroll_edge(self):
        now = perf_counter()
        elapsed = min(0.05, max(0.0, now - self.last_tick))
        self.last_tick = now
        self.scroll_velocity += (self.scroll_target - self.scroll_velocity) * (1 - exp(-elapsed / 0.075))
        self.scroll_fraction += self.scroll_velocity * elapsed
        step = int(self.scroll_fraction)
        self.scroll_fraction -= step
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.value() + step)
        if bar.value() in (bar.minimum(), bar.maximum()):
            self.scroll_fraction = 0.0
        self.locate_drop(self.mapFrom(self.scroll.viewport(), self.hover_pos))

    def set_scroll_target(self, y):
        height = max(1, self.scroll.viewport().height())
        origin = height / 2 if self.origin_y is None else self.origin_y
        half = height / 8
        edge = min(12, height / 16)
        center = max(half + edge, min(height - half - edge, origin))
        low, high = center - half, center + half
        if y < low:
            self.scroll_target = -1500 * min(1, (low - y) / low) ** 1.4
        elif y > high:
            self.scroll_target = 1500 * min(1, (y - high) / (height - high)) ** 1.4
        else:
            self.scroll_target = 0.0

    def stop_scroll(self):
        self.edge_timer.stop()
        self.scroll_target = self.scroll_velocity = self.scroll_fraction = 0.0

    def begin_preview(self):
        if self.preview_geometries is not None:
            return
        self.finish_motion()
        self.rows.activate()
        self.preview_geometries = {ident: group.geometry() for ident, group in self.groups}
        self.preview_id = self.dragged_id
        self.original_minimum = self.minimumHeight()
        self.setMinimumHeight(self.height())
        self.rows.setEnabled(False)
        source = dict(self.groups)[self.preview_id]
        opacity = QGraphicsOpacityEffect(source)
        opacity.setOpacity(0)
        source.setGraphicsEffect(opacity)
        rect = self.preview_geometries[self.preview_id]
        self.slot.setGeometry(rect)
        self.slot.show()
        self.slot.lower()
        ids = [ident for ident, _ in self.groups]
        index = ids.index(self.preview_id)
        self.drop_before = ids[index + 1] if index + 1 < len(ids) else self.tail_before
        self.targets = dict(self.preview_geometries)
        self.drop_y = rect.y()

    def move_widgets(self, destinations, callback=None):
        self.motion.stop()
        self.motion.clear()
        self.motion_callback = callback
        for widget, destination in destinations:
            if widget.pos() == destination:
                continue
            animation = QPropertyAnimation(widget, b"pos", self.motion)
            animation.setDuration(190)
            animation.setStartValue(widget.pos())
            animation.setEndValue(destination)
            animation.setEasingCurve(QEasingCurve.OutCubic)
            self.motion.addAnimation(animation)
        if self.motion.animationCount():
            self.motion.start()
        elif callback:
            self.motion_callback = None
            callback()

    def motion_finished(self):
        callback, self.motion_callback = self.motion_callback, None
        if callback:
            callback()

    def arrange_preview(self, before):
        self.drop_before = before
        ids = [ident for ident, _ in self.groups if ident != self.preview_id]
        index = ids.index(before) if before in ids else len(ids)
        ids.insert(index, self.preview_id)
        y = min(rect.y() for rect in self.preview_geometries.values())
        destinations = []
        for ident in ids:
            rect = self.preview_geometries[ident]
            rect = type(rect)(rect)
            rect.moveTop(y)
            self.targets[ident] = rect
            if ident == self.preview_id:
                # Keep the gap immediately legible while neighbours slide.
                self.slot.setGeometry(rect)
            else:
                destinations.append((dict(self.groups)[ident], rect.topLeft()))
            y += rect.height() + self.rows.spacing()
        self.move_widgets(destinations)

    def finish_motion(self):
        self.motion.stop()
        self.motion_callback = None
        self.motion.clear()
        self.rows.setEnabled(True)
        self.rows.activate()

    def restore_preview(self):
        self.motion.stop()
        self.slot.hide()
        if self.preview_geometries is None:
            return
        for ident, group in self.groups:
            group.move(self.preview_geometries[ident].topLeft())
        source = dict(self.groups).get(self.preview_id)
        if source:
            source.setGraphicsEffect(None)
        self.setMinimumHeight(self.original_minimum)
        self.preview_geometries = None
        self.preview_id = None
        self.finish_motion()

    def clear_drop(self):
        self.stop_scroll()
        self.restore_preview()
        self.drop_y = None
        self.origin_y = None
        self.update()

    def dragLeaveEvent(self, event):
        self.clear_drop()
        event.accept()

    def dropEvent(self, event):
        ident = self.payload(event)
        if ident is None:
            event.ignore()
            return
        self.locate_drop(event.position().toPoint())
        before, fingerprint = self.drop_before, self.fingerprint
        self.hover_pos = self.mapTo(self.scroll.viewport(), event.position().toPoint())
        self.stop_scroll()
        event.setDropAction(Qt.MoveAction)
        event.accept()
        # An OS drag has a nested event loop. Rebuild only after its handle returns.
        if self.active_drag:
            self.pending_drop = (ident, before, fingerprint)
        else:
            self.schedule_drop(ident, before, fingerprint)

    def schedule_drop(self, ident, before, fingerprint):
        QTimer.singleShot(0, lambda: self.commit_drop(ident, before, fingerprint))

    def commit_drop(self, ident, before, fingerprint):
        # A drag owns no data mutations until it has exited Qt's nested loop.
        if not self.owner.reorder_task(self.key, ident, before, fingerprint, refresh=False):
            self.clear_drop()
            return
        positions = {i: group.pos() for i, group in self.groups}
        if self.preview_geometries is not None:
            positions[ident] = self.mapFrom(self.scroll.viewport(), self.hover_pos) - QPoint(20, 20)
        self.setUpdatesEnabled(False)
        self.clear_drop()
        self.owner.refresh_overview()
        self.rows.activate()
        targets = [(group, group.pos()) for i, group in self.groups]
        self.rows.setEnabled(False)
        for i, group in self.groups:
            if i in positions:
                group.move(positions[i])
        self.setUpdatesEnabled(True)
        self.move_widgets(targets, self.finish_motion)
