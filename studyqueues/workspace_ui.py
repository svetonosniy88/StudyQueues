"""Task workspace and compact focus mode in a single native window."""
from pathlib import Path
from datetime import date
from math import ceil
import json
import sqlite3

from PySide6.QtCore import Qt,QTimer,QByteArray,Signal,QSignalBlocker,QSize,QRect,QRectF,QEvent
from PySide6.QtGui import QIcon,QShortcut,QKeySequence,QPainter,QColor,QPen,QFontMetricsF
from PySide6.QtWidgets import (QApplication,QMainWindow,QWidget,QFrame,QLabel,QPushButton,QVBoxLayout,
    QHBoxLayout,QLineEdit,QPlainTextEdit,QCheckBox,QTreeWidget,QTreeWidgetItem,QAbstractItemView,
    QStackedWidget,QMenu,QInputDialog,QFileDialog,QDialog,QListWidget,QComboBox,QSizeGrip,QStyledItemDelegate,QHeaderView,QStyle,QSizePolicy,QStyleOptionViewItem,QDialogButtonBox)

from .ui import TitleBar,label,button,clock_text,minutes_text
from .widgets import IconButton,CapsuleScrollBar
from .native_window import NativeFrame
from .session import Session,daily_seconds
from .state_store import StateError
from .queue_store import QueueError
from .workspace_store import WorkspaceStore
from .workspace_dialogs import TaskDialog,CorrectionDialog
from .workspace_theme import apply_workspace_theme, BG


class TaskDelegate(QStyledItemDelegate):
    def __init__(self,parent,checks=True,strike_done=True,cards=True):
        super().__init__(parent);self.checks=checks;self.strike_done=strike_done;self.cards=cards

    def createEditor(self,parent,option,index):
        return super().createEditor(parent,option,index) if index.column()==0 else None

    def text_width(self,index):
        return max(100,self.parent().viewport().width()-(40 if index.parent().isValid() else 20)-(66 if self.checks else 34))

    @staticmethod
    def checkbox_rect(rect):
        return QRectF(rect.left()+18,rect.top()+17,20,20)

    def sizeHint(self,option,index):
        bounds=QFontMetricsF(option.font).boundingRect(QRectF(0,0,self.text_width(index),10000),Qt.TextWordWrap,str(index.data() or ''))
        meta=index.data(Qt.UserRole+1) or ''
        return QSize(0,max(56,ceil(bounds.height())+30+(22 if meta else 0)))

    def paint(self,painter,option,index):
        painter.save();painter.setRenderHint(QPainter.Antialiasing);painter.setRenderHint(QPainter.TextAntialiasing)
        rect=QRectF(option.rect).adjusted(4,3,-4,-3)
        selected=self.parent().selectionModel().isSelected(index);hover=bool(option.state&QStyle.State_MouseOver)
        painter.setPen(QPen(QColor('#776086' if selected else '#45384e'),1) if self.cards else Qt.NoPen)
        painter.setBrush(QColor('#392a48' if selected else '#302935' if hover else '#242128' if self.cards else BG))
        painter.drawRoundedRect(rect,8,8)
        if not self.cards:
            painter.setPen(QPen(QColor('#302b35'),1));painter.drawLine(rect.bottomLeft(),rect.bottomRight())
        checked=index.data(Qt.CheckStateRole)==Qt.Checked.value
        text_left=rect.left()+16
        if self.checks:
            check=self.checkbox_rect(option.rect)
            painter.setPen(QPen(QColor('#b78be8' if checked else '#9580a5'),1.2));painter.setBrush(QColor('#8a3fd1' if checked else '#242128'));painter.drawRoundedRect(check,5,5)
            if checked:
                painter.setPen(QPen(QColor('#ffffff'),2,Qt.SolidLine,Qt.RoundCap,Qt.RoundJoin))
                painter.drawLine(check.left()+5,check.top()+10,check.left()+9,check.top()+14);painter.drawLine(check.left()+9,check.top()+14,check.left()+15,check.top()+6)
            text_left=check.right()+12
        font=option.font;font.setStrikeOut(bool(checked and self.strike_done));painter.setFont(font)
        painter.setPen(QColor('#a397ad' if checked and self.strike_done else '#f0eaf4'))
        width=max(60,rect.right()-text_left-14)
        bounds=QFontMetricsF(font).boundingRect(QRectF(0,0,width,10000),Qt.TextWordWrap,str(index.data() or ''))
        painter.drawText(QRectF(text_left,rect.top()+12,width,bounds.height()+2),Qt.TextWordWrap|Qt.AlignLeft|Qt.AlignTop,str(index.data() or ''))
        meta=index.data(Qt.UserRole+1) or ''
        if meta:
            font.setStrikeOut(False);font.setPixelSize(12);painter.setFont(font);painter.setPen(QColor('#b6aaba'))
            painter.drawText(QRectF(text_left,rect.top()+bounds.height()+18,width,20),Qt.AlignLeft|Qt.AlignVCenter,painter.fontMetrics().elidedText(meta,Qt.ElideRight,int(width)))
        painter.restore()

    def editorEvent(self,event,model,option,index):
        if not self.checks:return False
        mouse=event.type() in (QEvent.MouseButtonPress,QEvent.MouseButtonRelease) and event.button()==Qt.LeftButton and self.checkbox_rect(option.rect).contains(event.position())
        keyboard=event.type()==QEvent.KeyPress and event.key()==Qt.Key_Space
        if mouse and event.type()==QEvent.MouseButtonPress:return True
        if mouse or keyboard:
            model.setData(index,Qt.Unchecked if index.data(Qt.CheckStateRole)==Qt.Checked.value else Qt.Checked,Qt.CheckStateRole);return True
        return False


class CardTree(QTreeWidget):
    """Let the delegate paint selection once, without Fusion's branch rectangle."""
    def drawRow(self,painter,option,index):
        clean=QStyleOptionViewItem(option)
        clean.state &= ~(QStyle.State_Selected|QStyle.State_HasFocus)
        super().drawRow(painter,clean,index)

    def keyPressEvent(self,event):
        item=self.currentItem()
        if event.key()==Qt.Key_Space and item and item.flags()&Qt.ItemIsUserCheckable:
            item.setCheckState(0,Qt.Unchecked if item.checkState(0)==Qt.Checked else Qt.Checked)
            event.accept();return
        super().keyPressEvent(event)


class TaskTree(CardTree):
    def __init__(self,owner):
        super().__init__();self.owner=owner
        self.setObjectName('workspaceTasks');self.setColumnCount(1);self.setHeaderHidden(True)
        self.setVerticalScrollBar(CapsuleScrollBar(self))
        self.header().setStretchLastSection(False)
        self.header().setSectionResizeMode(0,QHeaderView.Stretch)
        self.setRootIsDecorated(True);self.setAlternatingRowColors(False)
        self.setWordWrap(True)
        self.setIndentation(22);self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setSelectionMode(QAbstractItemView.SingleSelection);self.setItemDelegate(TaskDelegate(self))
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(owner.task_menu)
        self.itemChanged.connect(owner.task_changed);self.itemSelectionChanged.connect(owner.selection_changed)
        self.itemDoubleClicked.connect(lambda item,_:owner.edit_task(item.data(0,Qt.UserRole)))

    def drawBranches(self,painter,rect,index):
        # QTreeView paints its selected-branch background outside the delegate,
        # even with an entirely custom card and a transparent branch stylesheet.
        painter.fillRect(rect,QColor(BG))
        if not index.model().hasChildren(index):return
        painter.save();painter.setRenderHint(QPainter.Antialiasing)
        center=rect.right()-10;y=rect.center().y()
        painter.setPen(QPen(QColor('#9c88a8'),1.4,Qt.SolidLine,Qt.RoundCap))
        painter.drawLine(center-4,y,center+4,y)
        if not self.isExpanded(index):painter.drawLine(center,y-4,center,y+4)
        painter.restore()

    def dropEvent(self,event):
        old={item.data(0,Qt.UserRole):item.parent().data(0,Qt.UserRole) if item.parent() else None for item in self.owner.items.values()}
        with QSignalBlocker(self):super().dropEvent(event)
        groups={};invalid=False
        def visit(item,parent=None):
            nonlocal invalid
            ident=item.data(0,Qt.UserRole)
            if old.get(ident)!=parent:invalid=True
            groups.setdefault(parent,[]).append(ident)
            for i in range(item.childCount()):visit(item.child(i),ident)
        for i in range(self.topLevelItemCount()):visit(self.topLevelItem(i))
        if invalid:self.owner.notice('Перемещайте задачи внутри одного уровня. Проект меняется в деталях задачи.');self.owner.refresh_tasks();return
        self.owner.safe(lambda:[self.owner.store.reorder(ids) for ids in groups.values() if len(ids)>1])
        self.owner.refresh_tasks()


class WorkspaceWindow(QMainWindow):
    def __init__(self,data_dir,paths=None,force_paths=False):
        super().__init__();self.setWindowTitle('StudyQueues');self.setObjectName('StudyQueuesWorkspace')
        apply_workspace_theme(QApplication.instance())
        self.setWindowIcon(QIcon(str(Path(__file__).with_name('app.ico'))))
        self.setWindowFlags(Qt.Window|Qt.FramelessWindowHint)
        self.app_platform=QApplication.platformName();self.native_frame=NativeFrame(self)
        self.resize(1000,780);self.setMinimumSize(820,650)
        self.store=WorkspaceStore(data_dir);self.import_report=self.store.migrate_legacy(paths if force_paths else None)
        self.store.recover_session_entries()
        self.session=None;self.current_id=None;self.selected_id=None;self.queue_project=None;self.focus_panel=None;self.focus_mode=False;self.items={};self.view='session';self.project_id=None
        setup=self.store.meta('queue_setup',{})
        self.prepared_mode='free';self.prepared_queue=setup.get('queue','university');self.prepared_task=setup.get('task');self.prepared_minutes=setup.get('minutes',25)
        self.result_id=None;self.result_project=None;self.queue_undo=None;self.restored_session=False
        self.closed=False;self.checkpoint_ticks=0
        active=self.store.active_session()
        if active:
            self.restored_session=True
            self.session=Session.restore(active);self.current_id=self.session.view_task_id
            self.queue_project=self.store.meta('queue-mode:'+self.session.id,self.session.queue if self.session.queue in ('university','self_development') else None)
            if self.current_id and not self.store.task(self.current_id):
                origin=str(Path(self.session.path).resolve())+'|'+self.current_id
                row=self.store.db.execute('SELECT id FROM tasks WHERE origin_key=?',(origin,)).fetchone()
                self.current_id=row[0] if row else None
                self.session.view_task_id=self.current_id
            if self.session.creative:
                self.current_id=None;self.session.view_task_id=None;self.queue_project=None
        self._build()
        self.task_refresh_timer=QTimer(self);self.task_refresh_timer.setSingleShot(True)
        self.task_refresh_timer.timeout.connect(self.apply_scheduled_refresh)
        self.refresh_all()
        geometry=self.store.meta('window_geometry')
        if geometry:self.restoreGeometry(QByteArray.fromBase64(geometry.encode('ascii')))
        self.timer=QTimer(self);self.timer.setInterval(250);self.timer.timeout.connect(self.tick);self.timer.start()
        self.search_timer=QTimer(self);self.search_timer.setSingleShot(True);self.search_timer.setInterval(120);self.search_timer.timeout.connect(self.refresh_tasks)
        self.search.textChanged.connect(lambda:self.search_timer.start())
        self.draft_timer=QTimer(self);self.draft_timer.setSingleShot(True);self.draft_timer.setInterval(350);self.draft_timer.timeout.connect(lambda:self.safe(self.save_drafts))
        self.entry.setText(self.store.meta('entry_draft',''))
        self.entry.textChanged.connect(lambda:self.draft_timer.start())
        self.shortcuts=[]
        for keys,action in [('Ctrl+K',self.focus_search),('Ctrl+N',self.focus_entry),('Ctrl+Z',self.undo),('Ctrl+Shift+Z',lambda:self.undo(True)),('F2',self.edit_selected),('F5',self.refresh_all),('Ctrl+Shift+F',self.toggle_focus)]:
            shortcut=QShortcut(QKeySequence(keys),self);shortcut.activated.connect(action);self.shortcuts.append(shortcut)
        if self.store.meta('focus_mode',False):self.open_focus()
        self.set_view('session')

    def focus_entry(self):
        if self.view=='session':
            if self.session and self.queue_project:self.focus_panel.notes_open=True;self.focus_panel.refresh()
            if self.session:self.focus_panel.entry.setFocus()
            else:self.focus_panel.pause.setFocus()
        else:self.entry.setFocus()

    def focus_search(self):
        self.set_view('today')
        self.search.setFocus()

    def edit_selected(self):
        item=self.focus_panel.tasks.currentItem() if self.view=='session' else self.tree.currentItem()
        if item:self.edit_task(item.data(0,Qt.UserRole))

    def _build(self):
        from .session_panel import SessionPanel
        shell=QWidget();shell.setObjectName('shell');self.setCentralWidget(shell)
        outer=QVBoxLayout(shell);outer.setContentsMargins(1,1,1,1);outer.setSpacing(0)
        self.titlebar=TitleBar();self.titlebar.setObjectName('titlebar');self.titlebar.setAttribute(Qt.WA_StyledBackground,True);self.titlebar.setFixedHeight(44)
        title=QHBoxLayout(self.titlebar);title.setContentsMargins(16,2,3,2);title.setSpacing(8)
        mark=label('SQ','appmark');mark.setFixedSize(24,24);mark.setAlignment(Qt.AlignCenter);title.addWidget(mark)
        title.addWidget(label('StudyQueues'));self.mode_label=label('','muted');self.mode_label.hide();title.addWidget(self.mode_label);title.addStretch()
        self.window_controls=[]
        for kind,action,name,tip in [('minimize',self.showMinimized,'ghost','Свернуть'),('maximize',self.toggle_maximize,'ghost','Развернуть / восстановить'),('close',self.close,'windowClose','Закрыть')]:
            control=IconButton(kind,action,tip,name);control.setFocusPolicy(Qt.NoFocus);title.addWidget(control);self.window_controls.append(control)
        outer.addWidget(self.titlebar)
        self.top_navigation=QFrame();self.top_navigation.setObjectName('topNavigation')
        top=QHBoxLayout(self.top_navigation);top.setContentsMargins(18,8,18,8);top.setSpacing(6)
        self.top_nav={}
        for key,text in [('session','Занятие'),('today','Очереди'),('history','История'),('settings','Настройки')]:
            if key=='settings':top.addStretch()
            control=button(text,lambda _,v=key:self.set_view(v),'nav');control.setCheckable(True);self.top_nav[key]=control;top.addWidget(control)
        outer.addWidget(self.top_navigation)
        self.mode_stack=QStackedWidget();outer.addWidget(self.mode_stack,1)
        self.focus_panel=SessionPanel(self);self.mode_stack.addWidget(self.focus_panel)
        self.workspace_page=QWidget();self.mode_stack.addWidget(self.workspace_page)
        body=QHBoxLayout(self.workspace_page);body.setContentsMargins(0,0,0,0);body.setSpacing(0)
        self.sidebar=QFrame();self.sidebar.setObjectName('sidebar');self.sidebar.setFixedWidth(180)
        side=QVBoxLayout(self.sidebar);side.setContentsMargins(12,20,12,14);side.setSpacing(5)
        side.addWidget(label('ЗАДАЧИ','eyebrow'));self.nav={}
        for key,text in [('today','Все задачи'),('inbox','Входящие'),('due','По срокам'),('priority','Важные')]:
            control=button(text,lambda _,v=key:self.set_view(v),'nav');control.setCheckable(True);self.nav[key]=control;side.addWidget(control)
        self.nav['history']=self.top_nav['history']
        side.addSpacing(14);side.addWidget(label('ОЧЕРЕДИ','eyebrow'))
        self.project_nav=QVBoxLayout();self.project_nav.setSpacing(3);side.addLayout(self.project_nav)
        side.addWidget(button('+ Новая очередь',self.new_project,'ghost'))
        self.saved_nav=QVBoxLayout();self.saved_nav.setSpacing(3);side.addLayout(self.saved_nav);side.addStretch()
        side.addWidget(button('Корзина',lambda:self.set_view('trash'),'ghost'));body.addWidget(self.sidebar)
        content=QWidget();content.setObjectName('content');content_layout=QVBoxLayout(content);content_layout.setContentsMargins(22,20,22,12);content_layout.setSpacing(12);body.addWidget(content,1)
        top=QHBoxLayout();self.heading=label('Очереди и задачи','heading');top.addWidget(self.heading);top.addStretch()
        self.search=QLineEdit();self.search.setPlaceholderText('Поиск · Ctrl+K');self.search.setMaximumWidth(280);self.search.setAccessibleName('Поиск задач');top.addWidget(self.search);content_layout.addLayout(top)
        self.pages=QStackedWidget();content_layout.addWidget(self.pages,1)
        self.board=QWidget();board=QVBoxLayout(self.board);board.setContentsMargins(0,0,0,0);board.setSpacing(12);self.pages.addWidget(self.board)
        filters=QHBoxLayout();self.show_done=QCheckBox('Показать выполненные');self.show_done.toggled.connect(self.refresh_tasks);filters.addWidget(self.show_done)
        filters.addStretch();self.queue_button=button('Подготовить занятие',self.queue_menu,'primary');filters.addWidget(self.queue_button)
        filters.addWidget(button('Сохранить вид',self.save_view,'ghost'));board.addLayout(filters)
        self.tree=TaskTree(self);board.addWidget(self.tree,1)
        self.empty=label('В этой очереди пока нет задач. Добавьте первую ниже.','muted',True);self.empty.setAlignment(Qt.AlignCenter);board.addWidget(self.empty)
        self.entry=QLineEdit();self.entry.setPlaceholderText('Новая задача');self.entry.setAccessibleName('Быстрый ввод задачи');self.entry.returnPressed.connect(self.add_entry)
        entry_line=QHBoxLayout();entry_line.addWidget(self.entry,1);entry_line.addWidget(button('Добавить',self.add_entry));board.addLayout(entry_line)
        self.history_page=QWidget();hist=QVBoxLayout(self.history_page);hist.setContentsMargins(0,0,0,0);hist.setSpacing(12)
        self.summary=label('','brand',True);hist.addWidget(self.summary);self.project_summary=label('','muted',True);hist.addWidget(self.project_summary)
        self.favorites=QCheckBox('Только избранные');self.favorites.toggled.connect(self.refresh_history);hist.addWidget(self.favorites)
        self.history_tree=CardTree();self.history_tree.setObjectName('workspaceTasks');self.history_tree.setColumnCount(1);self.history_tree.setHeaderHidden(True);self.history_tree.setRootIsDecorated(False);self.history_tree.setItemDelegate(TaskDelegate(self.history_tree,False))
        self.history_tree.itemDoubleClicked.connect(lambda item,_:self.show_session(item.data(0,Qt.UserRole)));hist.addWidget(self.history_tree,1)
        self.history_tree.setVerticalScrollBar(CapsuleScrollBar(self.history_tree));hist.addWidget(button('Открыть занятие',self.open_history_item));self.pages.addWidget(self.history_page)
        self.settings_page=QWidget();settings=QVBoxLayout(self.settings_page);settings.setContentsMargins(42,28,42,24);settings.setSpacing(14)
        settings.addWidget(label('Настройки и данные','heading'));settings.addWidget(label('Все данные хранятся на этом компьютере.','muted'))
        for text,action in [('Резервная копия задач и истории',self.backup_data),('Добавить задачи из Markdown',self.import_data),('Экспорт всех задач в Markdown',lambda:self.export()),('Корзина',lambda:self.set_view('trash'))]:
            control=button(text,action);control.setMaximumWidth(420);settings.addWidget(control)
        settings.addSpacing(16);settings.addWidget(label('При закрытии занятие сохраняется на паузе.\nПоложение, размер окна и черновики запоминаются.','muted',True));settings.addStretch();self.mode_stack.addWidget(self.settings_page)
        self.session_card=QFrame();self.session_card.setObjectName('sessionStrip');strip=QHBoxLayout(self.session_card);strip.setContentsMargins(18,10,18,10);strip.setSpacing(10)
        info=QVBoxLayout();self.session_name=label('','brand');self.session_status=label('','muted');info.addWidget(self.session_name);info.addWidget(self.session_status);strip.addLayout(info,1)
        self.timer_label=label('00:00','sessionTimer');strip.addWidget(self.timer_label)
        self.pause_button=button('Начать',self.pause_or_start,'primary');strip.addWidget(self.pause_button)
        self.correct_button=button('Исправить время',self.correct_time,'ghost');self.correct_button.hide()
        self.focus_button=button('К занятию',lambda:self.set_view('session'));strip.addWidget(self.focus_button)
        self.finish_button=button('Завершить',self.finish_session,'finishSession');self.finish_button.hide()
        outer.addWidget(self.session_card)
        self.toast=label('','toast',True);self.toast.hide();outer.addWidget(self.toast)

    def toggle_maximize(self):
        self.showNormal() if self.isMaximized() else self.showMaximized()

    def showEvent(self,event):
        super().showEvent(event);self.native_frame.install()

    def notice(self,text,error=False):
        self.toast.setText(text);self.toast.setProperty('error',error);self.toast.style().unpolish(self.toast);self.toast.style().polish(self.toast);self.toast.show()
        QTimer.singleShot(9000 if error else 5000,self.toast.hide)

    def safe(self,action):
        try:return action()
        except (ValueError,OSError,sqlite3.Error,StateError,QueueError) as error:self.notice(str(error),True);return None

    def refresh_nav(self):
        for key,control in self.nav.items():control.setChecked(key==self.view)
        projects=self.store.projects();views=self.store.saved_views()
        signature=(tuple((p['id'],p['name']) for p in projects),tuple((v['id'],v['name'],v['filters']) for v in views))
        if getattr(self,'nav_signature',None)==signature:
            for key,control in self.project_buttons.items():control.setChecked(self.view=='project' and self.project_id==key)
            return
        self.nav_signature=signature;self.project_buttons={}
        for layout in (self.project_nav,self.saved_nav):
            while layout.count():
                item=layout.takeAt(0)
                if item.widget():item.widget().deleteLater()
        for project in projects:
            control=button(project['name'],lambda _,p=project['id']:self.set_view('project',p),'nav');control.setCheckable(True);control.setChecked(self.project_id==project['id'] and self.view=='project');self.project_nav.addWidget(control)
            self.project_buttons[project['id']]=control
        for view in views:
            self.saved_nav.addWidget(button(view['name'],lambda _,v=view:self.load_view(v),'ghost'))

    def set_view(self,view,project=None):
        if view!='session' and self.focus_mode:self.leave_focus()
        self.view,self.project_id=view,project
        self.mode_stack.setCurrentWidget(self.focus_panel if view=='session' else self.settings_page if view=='settings' else self.workspace_page)
        self.sidebar.setVisible(view not in ('history','session','settings'))
        for key,control in self.top_nav.items():
            control.setChecked(key==view or key=='today' and view in ('project','inbox','due','priority','trash'))
        for key,control in self.nav.items():control.setChecked(key==view)
        names={'today':'Очереди и задачи','inbox':'Входящие','due':'По срокам','priority':'Важные','history':'История занятий','trash':'Корзина'}
        self.heading.setText(next((p['name'] for p in self.store.projects(True) if p['id']==project),'Проект') if view=='project' else names.get(view,'Задачи'))
        self.pages.setCurrentWidget(self.history_page if view=='history' else self.board)
        self.refresh_nav()
        self.refresh_history() if view=='history' else self.refresh_tasks()
        self.update_clock()

    def refresh_all(self):
        self.refresh_nav();self.refresh_tasks();self.update_clock();self.refresh_history()

    def refresh_tasks(self,*_):
        if not hasattr(self,'tree'):return
        self.task_refresh_timer.stop()
        if self.view=='history':
            self.refresh_history()
            if self.focus_panel:self.focus_panel.refresh()
            return
        scroll=self.tree.verticalScrollBar().value();expanded={key for key,item in self.items.items() if item.isExpanded()}
        rows=self.store.tasks(project=self.project_id if self.view=='project' else None,inbox=self.view=='inbox',
            search=self.search.text(),show_done=self.show_done.isChecked() or bool(self.session and self.view=='today'),
            trash=self.view=='trash',due_today=self.view=='due',priority=self.view=='priority')
        linked={t['id'] for t in self.store.session_tasks(self.session.id)} if self.session else set()
        if not self.show_done.isChecked() and self.view=='today' and self.session:rows=[t for t in rows if not t['done'] or t['id'] in linked]
        wanted={t['id']:t for t in rows}
        for task in list(rows):
            if task['parent_id'] and task['parent_id'] not in wanted:
                parent=self.store.task(task['parent_id'])
                if parent and not parent['deleted']:wanted[parent['id']]=parent
        projects={p['id']:p['name'] for p in self.store.projects(True)}
        with QSignalBlocker(self.tree):
            self.tree.clear();self.items={}
            sort_key=lambda t:(bool(t['parent_id']),t['due'] or '9999-12-31',-t['priority'],t['position']) if self.view=='due' else (bool(t['parent_id']),-t['priority'],t['position']) if self.view=='priority' else (bool(t['parent_id']),t['position'])
            for task in sorted(wanted.values(),key=sort_key):
                parent=self.items.get(task['parent_id']);item=QTreeWidgetItem(parent if parent else self.tree)
                item.setText(0,task['title'])
                meta=[]
                if self.view!='project':meta.append(projects.get(task['project_id'],'Входящие'))
                if task['due']:meta.append('Срок: '+task['due'])
                if task['priority']>=2:meta.append('Важная' if task['priority']==2 else 'Срочная')
                if task['repeat_days']:meta.append('Повтор: '+str(task['repeat_days'])+' дн.')
                item.setData(0,Qt.UserRole+1,' · '.join(meta))
                item.setData(0,Qt.UserRole,task['id']);item.setFlags(item.flags()|Qt.ItemIsUserCheckable|Qt.ItemIsEditable|Qt.ItemIsDragEnabled|Qt.ItemIsDropEnabled)
                item.setCheckState(0,Qt.Checked if task['done'] else Qt.Unchecked)
                font=item.font(0);font.setStrikeOut(bool(task['done']));item.setFont(0,font)
                item.setToolTip(0,task['title']+('\n'+task['notes'] if task['notes'] else '')+('\nВажная задача' if task['priority']>=2 else ''))
                item.setExpanded(task['id'] in expanded);self.items[task['id']]=item
            if self.selected_id in self.items:self.tree.setCurrentItem(self.items[self.selected_id])
        self.tree.verticalScrollBar().setValue(scroll);self.empty.setVisible(not wanted)
        self.tree.setDragDropMode(QAbstractItemView.InternalMove if self.view not in ('due','priority','trash') else QAbstractItemView.NoDragDrop)
        self.queue_button.setEnabled(bool(wanted) and self.view!='trash')
        self.entry.setEnabled(self.view!='trash')
        if self.focus_panel:self.focus_panel.refresh()

    def schedule_task_refresh(self):
        # Model notifications occur inside Qt's editor/selection call stack.
        # Never destroy the emitting item until that stack has returned.
        self.task_refresh_timer.start(0)

    def apply_scheduled_refresh(self):
        if QApplication.mouseButtons()!=Qt.NoButton:
            self.task_refresh_timer.start(16)
            return
        self.refresh_tasks()

    def task_changed(self,item,column):
        ident=item.data(0,Qt.UserRole);task=self.store.task(ident)
        if not task:return
        def action():
            if column==0 and item.text(0)!=task['title']:self.store.update_task(ident,title=item.text(0))
            elif column==0:
                checked=item.checkState(0)==Qt.Checked
                self.store.toggle(ident,checked,self.session.id if self.session and not self.session.creative else None)
                if checked and self.session and self.queue_project:
                    current=self.store.task(self.current_id) if self.current_id else None
                    if current and current['done']:self.advance_queue(current['position'])
            self.checkpoint();self.schedule_task_refresh();self.update_clock()
        self.safe(action)

    def selection_changed(self):
        item=self.tree.currentItem()
        if item:self.select_task(item.data(0,Qt.UserRole))

    def select_task(self,ident):
        # Browsing a catalogue is independent of the task currently being timed.
        self.selected_id=ident
        if not self.session:self.current_id=ident

    def add_entry(self,done=False):
        text=self.entry.text().strip()
        if not text:return
        def action():
            project=self.project_id if self.view=='project' else None
            ident=self.store.add_task(text,project,done=done)
            self.entry.clear();self.selected_id=ident
            if not self.session:self.current_id=ident
            if done and not self.session:self.show_done.setChecked(True)
            self.checkpoint();self.refresh_tasks();self.notice('Сделанное записано.' if done else 'Задача добавлена.');self.entry.setFocus()
            return ident
        return self.safe(action)

    def edit_task(self,ident):
        if not ident:return
        if TaskDialog(self.store,ident,self).exec():self.checkpoint();self.refresh_tasks()

    def task_menu(self,point):
        item=self.tree.itemAt(point)
        if not item:return
        ident=item.data(0,Qt.UserRole);task=self.store.task(ident);menu=QMenu(self)
        edit=menu.addAction('Детали задачи');child=menu.addAction('Добавить подпункт');child.setEnabled(not task['parent_id'] and not task['deleted'])
        start=menu.addAction('Работать над этой задачей');start.setEnabled(not task['deleted'])
        menu.addSeparator();up=menu.addAction('Выше');down=menu.addAction('Ниже');menu.addSeparator()
        trash=menu.addAction('Восстановить из корзины' if task['deleted'] else 'В корзину')
        chosen=menu.exec(self.tree.viewport().mapToGlobal(point))
        if chosen==edit:self.edit_task(ident)
        elif chosen==child:
            text,ok=QInputDialog.getText(self,'Подпункт','Что сделать?')
            if ok and text.strip():self.safe(lambda:self.store.add_task(text,parent=ident));self.checkpoint();self.refresh_tasks();self.items.get(ident,item).setExpanded(True)
        elif chosen==start:
            self.select_task(ident)
            self.queue_menu()
        elif chosen==trash:self.safe(lambda:self.store.trash(ident,restore=bool(task['deleted'])));self.checkpoint();self.refresh_tasks()
        elif chosen in (up,down):
            parent=item.parent();siblings=[parent.child(i) for i in range(parent.childCount())] if parent else [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
            ids=[i.data(0,Qt.UserRole) for i in siblings];index=ids.index(ident);other=index+(-1 if chosen==up else 1)
            if 0<=other<len(ids):ids[index],ids[other]=ids[other],ids[index];self.safe(lambda:self.store.reorder(ids));self.refresh_tasks()

    def new_project(self):
        name,ok=QInputDialog.getText(self,'Новый проект','Название')
        if ok and name.strip():
            ident=self.safe(lambda:self.store.add_project(name))
            if ident:self.set_view('project',ident)

    def save_view(self):
        name,ok=QInputDialog.getText(self,'Сохранить представление','Название')
        if ok and name.strip():self.store.save_view(name.strip(),{'view':self.view,'project':self.project_id,'search':self.search.text(),'done':self.show_done.isChecked()});self.refresh_nav()

    def load_view(self,view):
        filters=json.loads(view['filters']);self.search.setText(filters.get('search',''));self.show_done.setChecked(filters.get('done',False));self.set_view(filters.get('view','today'),filters.get('project'))

    def start_session(self,queue=None,minutes=0):
        if self.session:return
        def action():
            mode=queue if queue in ('university','self_development') else 'self_development' if queue else 'creative'
            candidate=Session(mode,'sqlite:workspace',minutes if queue else 0)
            self.store.save_session(candidate.to_dict(),True)
            self.session=candidate;self.queue_project=queue;self.result_id=None;self.queue_undo=None;self.restored_session=False
            self.store.set_meta('queue-mode:'+self.session.id,queue)
            if not queue:self.current_id=None
            elif self.current_id:self.session.view_task_id=self.current_id;self.store.link(self.session.id,self.current_id)
            self.checkpoint();self.update_clock();self.set_view('session')
        self.safe(action)

    def queue_menu(self):
        task=self.store.task(self.selected_id) if self.selected_id in self.items else None
        if task and task['parent_id']:task=self.store.task(task['parent_id'])
        project=task['project_id'] if task else self.project_id
        if task and not project:self.notice('Сначала назначьте задаче очередь в её деталях.');return
        self.choose_mode('queue',project,task['id'] if task and not task['done'] else None,True)

    def choose_mode(self,mode,project=None,task=None,prepare=False):
        active_mode=('queue' if self.queue_project else 'free') if self.session else None
        if self.session and (mode!=active_mode or prepare):
            dialog=QDialog(self);dialog.setWindowTitle('Сменить занятие');dialog.setMinimumWidth(420)
            layout=QVBoxLayout(dialog);layout.setContentsMargins(24,22,24,20);layout.setSpacing(16)
            layout.addWidget(label('Завершить текущее занятие?','brand'))
            layout.addWidget(label('Оно сохранится в истории. Затем откроется подготовка нового занятия, без запуска.','muted',True))
            actions=QHBoxLayout();actions.addWidget(button('Остаться',dialog.reject));actions.addWidget(button('Завершить и перейти',dialog.accept,'primary'));layout.addLayout(actions)
            if dialog.exec()!=QDialog.Accepted:
                self.focus_panel.refresh();return
            self.finish_session()
            if self.session:return
        elif self.session:
            self.set_view('session');return
        self.result_id=None;self.prepared_mode=mode
        if project:self.prepared_queue=project
        if task:self.prepared_task=task
        self.set_view('session');self.focus_panel.refresh()

    def start_prepared(self):
        if self.session:return
        panel=self.focus_panel
        if not panel.duration.hasAcceptableInput():self.notice('Укажите от 1 до 480 минут.',True);return
        task=self.store.task(self.prepared_task) if self.prepared_task else None
        if not task or task['done'] or task['deleted'] or task['project_id']!=self.prepared_queue:
            panel.refresh_setup();self.notice('Выберите невыполненную задачу для начала.');return
        self.current_id=task['id']
        self.start_session(self.prepared_queue,int(panel.duration.text()))

    def new_session(self):
        if self.session:return
        self.result_id=None;self.prepared_mode='free';self.restored_session=False
        self.focus_panel.notes_open=False;self.set_view('session')

    def start_queue(self,minutes=0):
        if self.session:return
        selected=self.selected_id or self.current_id
        task=self.store.task(selected) if selected else None
        if not task:
            task=next((t for t in self.store.tasks(project=self.project_id) if not t['parent_id']),None)
        if not task:return
        root=self.store.task(task['parent_id']) if task['parent_id'] else task
        if root['done']:self.notice('Выберите невыполненную задачу для начала очереди.');return
        if not root['project_id']:self.notice('Назначьте задаче проект, чтобы работать по его очереди.');return
        self.current_id=root['id'];self.start_session(root['project_id'],minutes)

    def complete_queue_task(self,skip=False):
        if not self.session or not self.queue_project:return
        task=self.store.task(self.current_id) if self.current_id else None
        if not task:return
        def action():
            if not skip:self.store.toggle(task['id'],True,self.session.id)
            command=self.store.db.execute('SELECT MAX(id) FROM commands WHERE undone=0').fetchone()[0]
            self.queue_undo={'task':task['id'],'skip':skip,'command':command}
            self.advance_queue(task['position']);self.checkpoint();self.refresh_tasks()
        return self.safe(action)

    def undo_queue_action(self):
        if not self.session or not self.queue_undo:return
        previous=self.queue_undo
        def action():
            if not previous['skip']:
                command=self.store.db.execute('SELECT MAX(id) FROM commands WHERE undone=0').fetchone()[0]
                if command!=previous['command']:self.notice('После этого действия задачи уже менялись. Используйте отмену в списке задач.');return
                self.store.undo()
            self.current_id=previous['task'];self.session.view_task_id=self.current_id;self.session.at_queue_end=False
            self.queue_undo=None;self.checkpoint();self.refresh_tasks()
        self.safe(action)

    def advance_queue(self,position):
        self.session.tick();self.checkpoint()
        following=next((t for t in self.store.tasks(project=self.queue_project) if not t['parent_id'] and t['position']>position),None)
        self.current_id=following['id'] if following else None
        self.session.view_task_id=self.current_id;self.session.at_queue_end=not bool(following)
        if following:self.store.link(self.session.id,following['id'])
        else:self.notice('Конец очереди. Таймер продолжает учитывать работу.')

    def pause_or_start(self):
        if not self.session:
            self.start_prepared() if self.prepared_mode=='queue' else self.start_session()
            return
        def action():
            self.restored_session=False
            self.session.pause() if self.session.running else self.session.resume()
            self.checkpoint();self.update_clock()
            self.focus_panel.restored.hide()
        self.safe(action)

    def sync_completed(self):
        if not self.session:return
        rows=self.store.session_entries(self.session.id) if self.session.creative else self.store.session_tasks(self.session.id)
        if self.session.creative:self.session.completed={}
        for task in rows:
            if task['origin_id']:self.session.completed.pop(task['origin_id'],None)
            if task['done'] and not task['deleted']:
                self.session.completed[task['id']]={'text':task['title'],'parent_id':task['parent_id']}
            else:self.session.completed.pop(task['id'],None)

    def checkpoint(self):
        if not self.session:return
        self.sync_completed();self.store.save_session(self.session.to_dict(),not bool(self.session.ended_at))
        self.store.attribute_until(self.session.id,self.session.active_seconds,None if self.session.creative else self.current_id)

    def tick(self):
        if self.closed or not self.session or self.session.ended_at:return
        if self.session.tick():self.notice('Учёт приостановлен после сна или перерыва обновлений. Продолжите, когда будете готовы.')
        self.update_clock();self.checkpoint_ticks+=1
        if self.checkpoint_ticks>=20:
            self.checkpoint_ticks=0
            try:self.checkpoint()
            except (sqlite3.Error,OSError,StateError,ValueError) as error:
                self.session.pause();self.update_clock();self.notice('Учёт приостановлен: '+str(error),True)

    def effective_seconds(self):
        if not self.session:return 0
        from .accounting import duration
        return max(0,self.session.active_seconds-duration(self.store.excluded(self.session.id)))

    def update_clock(self):
        active=bool(self.session)
        self.pause_button.setText(('Пауза' if self.session.running else 'Продолжить') if active else 'Начать')
        self.correct_button.setEnabled(active and self.effective_seconds()>=1)
        self.finish_button.setEnabled(active)
        self.timer_label.setText(clock_text(self.effective_seconds()))
        project=next((p['name'] for p in self.store.projects(True) if p['id']==self.queue_project),None)
        self.session_name.setText(('Очередь · '+project) if active and project else 'Свободная работа')
        self.session_status.setText(('В работе' if self.session.running else 'На паузе · время не учитывается') if active else 'Секундомер без плана времени · записи сделанного')
        if active and self.session.planned_seconds:
            remaining=self.session.planned_seconds-self.effective_seconds()
            self.timer_label.setText(('+' if remaining<0 else '')+clock_text(abs(remaining)))
            self.session_status.setText(('Осталось '+clock_text(remaining) if remaining>=0 else 'Сверх плана '+clock_text(-remaining))+(' · пауза' if not self.session.running else ''))
        self.session_card.setVisible(active and self.view!='session')
        if self.focus_panel:self.focus_panel.update_clock()

    def finish_session(self):
        if not self.session:return
        def action():
            if self.focus_panel and self.focus_panel.entry.text().strip() and not self.focus_panel.add():return
            self.session.pause();before=self.session.to_dict()
            try:
                self.session.finish();self.checkpoint()
            except (sqlite3.Error,OSError,StateError,ValueError):
                self.session=Session.restore(before);self.update_clock();raise
            self.result_id=self.session.id;self.result_project=self.queue_project
            self.session=None;self.queue_project=None;self.queue_undo=None;self.restored_session=False
            self.set_view('session');self.refresh_history()
        self.safe(action)

    def correct_time(self,ident=None):
        if isinstance(ident,bool):ident=None
        if ident is None:
            if not self.session:return
            self.session.pause();self.update_clock()
            try:self.checkpoint()
            except (sqlite3.Error,OSError,StateError,ValueError) as error:self.notice('Не удалось сохранить паузу: '+str(error),True);return
            ident=self.session.id
        CorrectionDialog(self.store,ident,self).exec();self.update_clock();self.refresh_history();self.focus_panel.refresh()

    def open_focus(self):
        if self.focus_mode:return
        self.workspace_geometry=self.saveGeometry()
        self.workspace_maximized=self.isMaximized()
        self.workspace_rect=self.normalGeometry() if self.workspace_maximized else self.geometry()
        self.focus_mode=True
        self.setMinimumSize(440,640)
        if self.isMaximized():self.showNormal()
        self.view='session';self.top_navigation.hide()
        self.mode_stack.setCurrentWidget(self.focus_panel)
        geometry=self.store.meta('focus_mode_geometry')
        if geometry:self.restoreGeometry(QByteArray.fromBase64(geometry.encode('ascii')))
        else:self.resize(480,760)
        self.focus_panel.refresh();self.update_clock()

    def toggle_focus(self):
        self.leave_focus() if self.focus_mode else self.open_focus()

    def leave_focus(self):
        if not self.focus_mode:return
        self.store.set_meta('focus_mode_geometry',bytes(self.saveGeometry().toBase64()).decode('ascii'))
        self.focus_mode=False;self.top_navigation.show()
        self.setMinimumSize(820,650);self.restoreGeometry(self.workspace_geometry)
        if not self.workspace_maximized:self.setGeometry(self.workspace_rect)
        if self.workspace_maximized:self.showMaximized()
        self.refresh_all()

    def refresh_history(self,*_):
        if not hasattr(self,'history_tree'):return
        records=self.store.history()
        active=self.store.active_session()
        totals=daily_seconds(records+([self.store.session(active['id'])] if active else []));today=date.today().isoformat()
        self.summary.setText('Сегодня: '+minutes_text(sum(v for (day,_),v in totals.items() if day==today)))
        projects={p['id']:p['name'] for p in self.store.projects(True)}
        parts=[f'{projects.get(key,"Без проекта")}: {minutes_text(value)}' for key,value in self.store.project_totals(today).items() if value>=1]
        self.project_summary.setText(' · '.join(parts))
        self.history_tree.clear()
        for record in records:
            if self.favorites.isChecked() and not record['favorite']:continue
            queue=self.store.meta('queue-mode:'+record['id'],record['queue'])
            title='Свободная работа' if record['queue']=='creative' else projects.get(queue,'Занятие')
            if self.search.text().strip() and self.search.text().casefold() not in (title+' '+str(record['completed'])).casefold():continue
            item=QTreeWidgetItem(self.history_tree,[('★ ' if record['favorite'] else '')+title])
            item.setData(0,Qt.UserRole+1,record['started_at'][:16].replace('T',' ')+' · '+clock_text(record['active_seconds'])+' · записей: '+str(len(record['completed'])))
            item.setData(0,Qt.UserRole,record['id'])

    def open_history_item(self):
        item=self.history_tree.currentItem()
        if item:self.show_session(item.data(0,Qt.UserRole))

    def show_session(self,ident):
        record=self.store.session(ident);dialog=QDialog(self);dialog.setWindowTitle('Занятие');dialog.resize(620,520);layout=QVBoxLayout(dialog)
        layout.addWidget(label(record['started_at'][:16].replace('T',' ')+' · '+clock_text(record['active_seconds']),'brand'))
        tasks=QListWidget();tasks.addItems([('    ' if item['parent_id'] else '')+'✓ '+item['text'] for item in record['completed'].values()]);layout.addWidget(tasks,1)
        favorite=QCheckBox('Избранное');favorite.setChecked(record['favorite']);favorite.toggled.connect(lambda checked:self.store.favorite(ident,checked));layout.addWidget(favorite)
        actions=QHBoxLayout();actions.addWidget(button('Исправить время',lambda:self.correct_time(ident)));actions.addWidget(button('Экспорт в Markdown',lambda:self.export(ident)));actions.addStretch();actions.addWidget(button('Закрыть',dialog.accept));layout.addLayout(actions)
        dialog.exec();self.refresh_history()

    def undo(self,redo=False):
        editor=QApplication.focusWidget()
        if isinstance(editor,(QLineEdit,QPlainTextEdit)):
            editor.redo() if redo else editor.undo();return
        result=self.safe(lambda:self.store.undo(redo))
        if result:self.safe(self.checkpoint);self.refresh_tasks();self.notice(('Повторено: ' if redo else 'Отменено: ')+result)

    def export(self,ident=None):
        path,_=QFileDialog.getSaveFileName(self,'Экспорт в Markdown','StudyQueues.md','Markdown (*.md)')
        if path:
            def action():
                self.store.export_markdown(path,ident)
                self.notice('Экспорт сохранён.')
            self.safe(action)

    def app_menu(self):
        self.set_view('settings')

    def import_data(self):
        path,_=QFileDialog.getOpenFileName(self,'Добавить задачи из Markdown','','Markdown (*.md)')
        if path:
            count=self.safe(lambda:self.store.import_markdown(path));self.refresh_all()
            if count is not None:self.notice(f'Добавлено задач: {count}. Существующие записи сохранены.')

    def backup_data(self):
        path,_=QFileDialog.getSaveFileName(self,'Резервная копия','StudyQueues-backup.sqlite3','SQLite (*.sqlite3)')
        if path:
            def action():self.store.backup(path);self.notice('Резервная копия сохранена.')
            self.safe(action)

    def closeEvent(self,event):
        try:
            if self.session:self.session.pause();self.checkpoint()
            self.save_drafts()
            if self.focus_mode:
                self.store.set_meta('focus_mode_geometry',bytes(self.saveGeometry().toBase64()).decode('ascii'))
            self.store.set_meta('focus_mode',self.focus_mode)
            geometry=self.workspace_geometry if self.focus_mode else self.saveGeometry()
            self.store.set_meta('window_geometry',bytes(geometry.toBase64()).decode('ascii'))
        except (sqlite3.Error,OSError,ValueError,StateError) as error:self.notice('Не удалось сохранить: '+str(error),True);event.ignore();return
        self.closed=True;self.timer.stop();self.draft_timer.stop();self.search_timer.stop();self.native_frame.uninstall()
        self.task_refresh_timer.stop()
        self.store.close();event.accept()

    def save_drafts(self):
        try:
            self.store.set_meta('entry_draft',self.entry.text())
            self.store.set_meta('focus_draft',self.focus_panel.entry.text())
            self.store.set_meta('queue_setup',{'queue':self.prepared_queue,'task':self.prepared_task,'minutes':self.prepared_minutes})
            self.focus_panel.saved.setText('Черновик сохранён' if self.focus_panel.entry.text() else 'Все записи сохранены · Enter — записать')
        except (sqlite3.Error,OSError,StateError,ValueError) as error:
            self.focus_panel.saved.setText('Не удалось сохранить черновик')
            raise

