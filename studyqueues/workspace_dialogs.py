"""Focused editors for task metadata and reversible time corrections."""
from datetime import datetime
import json
import sqlite3
from PySide6.QtCore import Qt, QDate, QDateTime, Signal
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QFormLayout,QLineEdit,QPlainTextEdit,
    QComboBox,QSpinBox,QDateEdit,QCheckBox,QPushButton,QLabel,QDialogButtonBox,QTabWidget,QWidget,
    QDateTimeEdit,QListWidget,QMessageBox,QStackedWidget)
from . import accounting
from .ui import clock_text


class TaskDialog(QDialog):
    def __init__(self, store, ident, parent=None):
        super().__init__(parent);self.store,self.ident=store,ident
        self.setWindowTitle('Задача');self.resize(520,500)
        task=store.task(ident);layout=QVBoxLayout(self);form=QFormLayout()
        self.title=QLineEdit(task['title']);self.title.setAccessibleName('Название задачи')
        self.notes=QPlainTextEdit(task['notes']);self.notes.setPlaceholderText('Заметки, ссылки, контекст…')
        self.project=QComboBox();self.project.addItem('Входящие',None)
        for project in store.projects(True):self.project.addItem(project['name'],project['id'])
        self.project.setCurrentIndex(max(0,self.project.findData(task['project_id'])))
        self.project.setEnabled(not task['parent_id'])
        self.has_due=QCheckBox('Назначить срок');self.has_due.setChecked(bool(task['due']))
        self.due=QDateEdit(QDate.fromString(task['due'],Qt.ISODate) if task['due'] else QDate.currentDate())
        self.due.setCalendarPopup(True);self.due.setEnabled(self.has_due.isChecked());self.has_due.toggled.connect(self.due.setEnabled)
        self.due.setButtonSymbols(QDateEdit.NoButtons)
        due_row=QWidget();row=QHBoxLayout(due_row);row.setContentsMargins(0,0,0,0);row.addWidget(self.has_due);row.addWidget(self.due)
        self.priority=QComboBox();self.priority.addItems(['Обычный','Низкий','Важный','Срочный']);self.priority.setCurrentIndex(task['priority'])
        self.repeat=QSpinBox();self.repeat.setRange(0,3660);self.repeat.setSpecialValueText('Не повторять');self.repeat.setSuffix(' дн.');self.repeat.setValue(task['repeat_days']);self.repeat.setEnabled(not task['parent_id'])
        self.repeat.setButtonSymbols(QSpinBox.NoButtons)
        self.tags=QLineEdit(', '.join(json.loads(task['labels'])))
        for caption,widget in [('Название',self.title),('Проект',self.project),('Срок',due_row),('Приоритет',self.priority),('Повтор через',self.repeat),('Метки',self.tags)]:form.addRow(caption,widget)
        layout.addLayout(form);layout.addWidget(QLabel('Заметки'));layout.addWidget(self.notes,1)
        buttons=QDialogButtonBox(QDialogButtonBox.Save|QDialogButtonBox.Cancel);buttons.button(QDialogButtonBox.Save).setText('Сохранить');buttons.button(QDialogButtonBox.Cancel).setText('Отмена')
        buttons.accepted.connect(self.save);buttons.rejected.connect(self.reject);layout.addWidget(buttons)

    def save(self):
        try:
            self.store.update_task(self.ident,title=self.title.text(),notes=self.notes.toPlainText(),project_id=self.project.currentData(),
                due=self.due.date().toString(Qt.ISODate) if self.has_due.isChecked() else None,
                priority=self.priority.currentIndex(),repeat_days=self.repeat.value(),labels=[s.strip() for s in self.tags.text().split(',') if s.strip()])
        except Exception as error:QMessageBox.warning(self,'Не удалось сохранить',str(error));return
        self.accept()


class CountField(QLineEdit):
    valueChanged=Signal(int)
    def __init__(self,maximum,parent=None):
        super().__init__(parent);self.setObjectName('durationNumber');self.setValidator(QIntValidator(0,maximum,self));self.setText('0');self.setFixedWidth(100)
        self.textChanged.connect(lambda _:self.valueChanged.emit(self.value()))
    def value(self):return int(self.text() or '0')
    def setValue(self,value):self.setText(str(value))


class CorrectionDialog(QDialog):
    def __init__(self,store,ident,parent=None):
        super().__init__(parent);self.store,self.ident=store,ident;self.ranges=[]
        self.setWindowTitle('Исправить время');self.resize(520,410)
        self.record=store.session(ident,False);self.total=self.record['active_seconds']
        layout=QVBoxLayout(self);layout.setContentsMargins(22,20,22,20);layout.setSpacing(12)
        heading=QLabel('Убрать нерабочее время');heading.setObjectName('brand');layout.addWidget(heading)
        explanation=QLabel('Поправку можно отменить в истории занятия.');explanation.setObjectName('muted');explanation.setWordWrap(True);layout.addWidget(explanation)
        selector=QHBoxLayout();self.mode_buttons=[]
        self.tabs=QStackedWidget()
        for index,text in enumerate(('Длительность','По часам')):
            control=QPushButton(text);control.setObjectName('segment');control.setCheckable(True);control.setChecked(index==0);control.setFixedHeight(38);control.clicked.connect(lambda _,i=index:self.set_mode(i));selector.addWidget(control);self.mode_buttons.append(control)
        selector.addStretch();layout.addLayout(selector)
        duration_page=QWidget();duration_layout=QVBoxLayout(duration_page);duration_layout.setContentsMargins(0,4,0,4);duration_layout.setSpacing(14)
        available=store.session(ident)['active_seconds']
        quick=QHBoxLayout()
        for value in (5,15,30):
            b=QPushButton(f'−{value} мин');b.setEnabled(available>=value*60);b.setFixedHeight(38);b.clicked.connect(lambda _,v=value:self.set_minutes(v));quick.addWidget(b)
        quick.addStretch();duration_layout.addLayout(quick)
        line=QHBoxLayout();line.setSpacing(10)
        self.minutes=CountField(max(1,int(available//60)));self.minutes.setAccessibleName('Лишние минуты')
        self.seconds=CountField(59);self.seconds.setAccessibleName('Лишние секунды')
        self.minutes.setValue(min(15,int(available//60)));self.seconds.setValue(0 if available>=60 else int(available))
        line.addWidget(self.minutes);line.addWidget(QLabel('мин'));line.addSpacing(10);line.addWidget(self.seconds);line.addWidget(QLabel('сек'));line.addStretch();duration_layout.addLayout(line)
        self.tabs.addWidget(duration_page)
        wall_page=QWidget();form=QFormLayout(wall_page)
        end=datetime.fromisoformat(self.record['ended_at'] or (self.record['intervals'][-1]['end'] if self.record['intervals'] else self.record['started_at']))
        self.start=QDateTimeEdit(QDateTime.fromMSecsSinceEpoch(int(end.timestamp()*1000)-900000).toLocalTime())
        self.end=QDateTimeEdit(QDateTime.fromMSecsSinceEpoch(int(end.timestamp()*1000)).toLocalTime())
        for widget in (self.start,self.end):widget.setDisplayFormat('dd.MM.yyyy HH:mm:ss');widget.setCalendarPopup(True);widget.setButtonSymbols(QDateTimeEdit.NoButtons)
        form.addRow('Отошёл с',self.start);form.addRow('Вернулся в',self.end);self.tabs.addWidget(wall_page);layout.addWidget(self.tabs)
        self.preview=QLabel();self.preview.setObjectName('correctionPreview');self.preview.setWordWrap(True);layout.addWidget(self.preview)
        self.reason=QLineEdit('Забыл поставить на паузу');self.reason.setPlaceholderText('Причина (необязательно)');layout.addWidget(self.reason)
        self.list=QListWidget();self.list.setAccessibleName('История поправок');self.list.setMaximumHeight(150);layout.addWidget(self.list)
        self.undo_button=QPushButton('Отменить выбранную поправку');self.undo_button.clicked.connect(self.undo);layout.addWidget(self.undo_button)
        self.buttons=QDialogButtonBox(QDialogButtonBox.Apply|QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Apply).setText('Применить');self.buttons.button(QDialogButtonBox.Cancel).setText('Закрыть')
        self.buttons.button(QDialogButtonBox.Apply).setObjectName('primary')
        for control in self.buttons.buttons():control.setFixedHeight(40)
        self.buttons.button(QDialogButtonBox.Apply).clicked.connect(self.apply);self.buttons.rejected.connect(self.reject);layout.addWidget(self.buttons)
        self.minutes.valueChanged.connect(self.update_preview);self.seconds.valueChanged.connect(self.update_preview)
        self.start.dateTimeChanged.connect(self.update_preview);self.end.dateTimeChanged.connect(self.update_preview);self.tabs.currentChanged.connect(self.update_preview)
        self.refresh_history();self.update_preview()

    def set_mode(self,index):
        self.tabs.setCurrentIndex(index)
        for i,button in enumerate(self.mode_buttons):button.setChecked(i==index)

    def set_minutes(self,value):
        self.minutes.setValue(value);self.seconds.setValue(0)

    def draft(self):
        excluded=self.store.excluded(self.ident)
        if self.tabs.currentIndex()==0:
            return accounting.remove_latest(self.total,self.minutes.value()*60+self.seconds.value(),excluded)
        start=datetime.fromtimestamp(self.start.dateTime().toSecsSinceEpoch()).astimezone()
        end=datetime.fromtimestamp(self.end.dateTime().toSecsSinceEpoch()).astimezone()
        return accounting.subtract(accounting.wall_range(self.record,start,end),excluded)

    def update_preview(self,*_):
        try:
            self.ranges=self.draft();amount=accounting.duration(self.ranges)
            if amount<0.001:raise ValueError('В этом диапазоне нет ещё учтённого рабочего времени.')
            current=self.store.session(self.ident)['active_seconds']
            self.preview.setText(f'{clock_text(current)} → {clock_text(max(0,current-amount))} · исключить {clock_text(amount)}')
            self.buttons.button(QDialogButtonBox.Apply).setEnabled(True)
        except ValueError as error:
            self.ranges=[];self.preview.setText(str(error));self.buttons.button(QDialogButtonBox.Apply).setEnabled(False)

    def refresh_history(self):
        self.list.clear();self.entries=self.store.corrections(self.ident)
        for entry in self.entries:
            amount=accounting.duration(json.loads(entry['ranges']))
            self.list.addItem(f"{'Отменена · ' if entry['undone'] else ''}−{clock_text(amount)} · {entry['reason']}")
        self.undo_button.setEnabled(any(not entry['undone'] for entry in self.entries))
        self.list.setVisible(bool(self.entries));self.undo_button.setVisible(bool(self.entries))

    def undo(self):
        index=self.list.currentRow()
        if index<0 or self.entries[index]['undone']:return
        try:self.store.undo_correction(self.entries[index]['id'])
        except sqlite3.Error as error:self.preview.setText('Не удалось сохранить: '+str(error));return
        self.refresh_history();self.update_preview()

    def apply(self):
        try:self.store.correct(self.ident,self.draft(),self.reason.text())
        except (ValueError,sqlite3.Error) as error:self.preview.setText(str(error));return
        self.accept()
