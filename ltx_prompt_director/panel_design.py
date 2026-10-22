"""Compact native panel surfaces based on the supplied panel references."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from PySide6.QtCore import Qt, QSize, QRect, QRectF, QTimer
from PySide6.QtGui import QAction, QColor, QIcon, QKeySequence, QPainter, QPen, QPixmap, QLinearGradient
from PySide6.QtWidgets import (QApplication, QAbstractItemView, QCheckBox, QComboBox, QFrame,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QMenu, QMessageBox, QPushButton,
    QSizePolicy, QStackedWidget, QStyle, QStyledItemDelegate, QToolButton,
    QVBoxLayout, QWidget, QWidgetAction)


COLORS = [('Green', '#2ed05d'), ('Blue', '#168cff'), ('Amber', '#ffbf00'),
          ('Purple', '#893dff'), ('Red', '#ff5964'), ('Gray', '#89949f')]


def dot_icon(color):
    pixmap = QPixmap(16, 16); pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap); painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(QColor(color))
    painter.drawEllipse(3, 3, 10, 10); painter.end()
    return QIcon(pixmap)


class CheckMenu(QMenu):
    """Keep multi-select filters open until the user clicks away."""
    def add_check(self, text, color=None, checked=False):
        action=QWidgetAction(self);action.setText(text);action.setCheckable(True)
        row=QWidget();layout=QHBoxLayout(row);layout.setContentsMargins(7,3,10,3);layout.setSpacing(6)
        check=QCheckBox();check.setAccessibleName(text);check.setChecked(checked)
        check.setStyleSheet('QCheckBox{background:transparent;border:0;padding:0;spacing:0} QCheckBox::indicator{width:14px;height:14px;border:1px solid #60758c;border-radius:3px;background:#16232e} QCheckBox::indicator:checked{background:#168cff;border-color:#62a9f5;image:url("'+str(Path(__file__).parent/'assets/panel-check.svg')+'")}')
        layout.addWidget(check)
        if color:
            dot=QLabel();dot.setPixmap(dot_icon(color).pixmap(16,16));layout.addWidget(dot)
        label=QLabel(text);label.setStyleSheet('background:transparent;border:0');layout.addWidget(label,1)
        action.setDefaultWidget(row);action.setChecked(checked)
        action.toggled.connect(check.setChecked);check.toggled.connect(action.setChecked)
        check.clicked.connect(action.triggered.emit)
        self.addAction(action)
        return action

    def mouseReleaseEvent(self, event):
        action = self.actionAt(event.position().toPoint())
        if action and action.isEnabled() and action.isCheckable():
            action.trigger(); event.accept()
        else:
            super().mouseReleaseEvent(event)


class ProjectCardDelegate(QStyledItemDelegate):
    def __init__(self, owner, parent):
        super().__init__(parent); self.owner = owner

    def createEditor(self, parent, option, index):
        editor=QLineEdit(parent);editor.setObjectName('projectNameEditor')
        editor.setAccessibleName('Project or collection name')
        return editor

    def setEditorData(self, editor, index):
        editor.setText(str((index.data(Qt.ItemDataRole.UserRole) or {}).get('name','')))
        editor.selectAll()

    def updateEditorGeometry(self, editor, option, index):
        scale=self.owner.settings.value('ui_text_scale',100,int)/100
        s=lambda n:max(1,round(n*scale))
        card=option.rect.adjusted(3,3,-3,-3)
        if self.parent().viewMode()==QListWidget.ViewMode.ListMode:
            area=card.adjusted(s(98),s(11),-s(8),-s(6))
        else:
            image=QRect(card.x()+s(8),card.y()+s(16),card.width()-s(16),max(s(28),card.height()-s(84)))
            area=card.adjusted(s(9),image.bottom()-card.top()+s(7),-s(7),-s(6))
        font=option.font;font.setPixelSize(s(14));editor.setFont(font)
        editor.setGeometry(area.x(),area.y()-s(2),area.width(),max(s(24),editor.sizeHint().height()))

    def setModelData(self, editor, model, index):
        meta=dict(index.data(Qt.ItemDataRole.UserRole) or {})
        name=editor.text().strip()
        if name and name!=str(meta.get('name','')):
            # Refreshing the cards destroys the editor; defer until Qt closes it.
            QTimer.singleShot(0,self.owner,lambda:self.owner.library_surface.rename_to(meta,name))

    def paint(self, painter, option, index):
        meta = index.data(Qt.ItemDataRole.UserRole) or {}
        scale = self.owner.settings.value('ui_text_scale', 100, int) / 100
        s = lambda n: max(1, round(n * scale))
        card = option.rect.adjusted(3, 3, -3, -3)
        explicit=QColor(str(meta.get('color') or ''))
        color = explicit if explicit.isValid() else QColor(self.owner.project_state_color(meta) or '#416679')
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        painter.save(); painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        gradient = QLinearGradient(card.topLeft(), card.bottomRight())
        gradient.setColorAt(0, QColor('#14232a'))
        gradient.setColorAt(1, color.darker(450))
        painter.setBrush(gradient); painter.setPen(QPen(QColor('#b9edf4') if selected else color, 2 if selected else 1))
        painter.drawRoundedRect(card, s(4), s(4))
        painter.save(); painter.setClipRect(card.adjusted(1, 1, -1, -1))
        painter.fillRect(QRect(card.x(), card.y(), card.width(), s(9)), color.lighter(115) if hovered else color)
        painter.restore()
        list_mode = self.parent().viewMode() == QListWidget.ViewMode.ListMode
        if list_mode:
            image = QRect(card.x()+s(8), card.y()+s(15), s(80), max(s(30), card.height()-s(24)))
            text = card.adjusted(s(98), s(11), -s(8), -s(6))
        else:
            image = QRect(card.x()+s(8), card.y()+s(16), card.width()-s(16), max(s(28), card.height()-s(84)))
            text = card.adjusted(s(9), image.bottom()-card.top()+s(7), -s(7), -s(6))
        icon = index.data(Qt.ItemDataRole.DecorationRole)
        if isinstance(icon, QIcon):
            pixmap = icon.pixmap(QSize(480, 280))
            if not pixmap.isNull():
                pixmap = pixmap.scaled(image.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
                painter.save(); painter.setClipRect(image)
                painter.drawPixmap(image.center()-pixmap.rect().center(), pixmap); painter.restore()
        font = option.font; font.setPixelSize(s(14)); painter.setFont(font)
        painter.setPen(QColor('#edf0ed'))
        fm = painter.fontMetrics(); line = max(s(17), fm.height())
        name = str(meta.get('name') or 'Untitled project')
        description = str(meta.get('description') or 'No description').replace('\n', ' ')
        if meta.get('kind') == 'collection': description = f"{len(meta.get('members', []))} projects"
        painter.drawText(QRect(text.x(), text.y(), text.width(), line), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, fm.elidedText(name, Qt.TextElideMode.ElideRight, text.width()))
        painter.setPen(QColor('#bdc8d0'))
        painter.drawText(QRect(text.x(), text.y()+line, text.width(), line), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, fm.elidedText(description, Qt.TextElideMode.ElideRight, text.width()))
        status = 'Archive' if meta.get('archived') else str(meta.get('status') or ('Collection' if meta.get('kind') == 'collection' else 'No status'))
        status_colors = {tag['name'].casefold(): tag['color'] for tag in self.owner.project_status_tags()}
        status_color = QColor(status_colors.get(status.casefold(), '#8c969e'))
        pill = QRect(text.x(), max(text.y()+line*2+s(2), card.bottom()-s(27)), min(text.width(), fm.horizontalAdvance(status)+s(30)), s(22))
        painter.setPen(QPen(QColor('#3a4a58'), 1)); painter.setBrush(QColor('#1e2d3a'))
        painter.drawRoundedRect(pill, s(10), s(10))
        painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(status_color)
        painter.drawEllipse(pill.x()+s(6), pill.y()+s(6), s(10), s(10))
        painter.setPen(QColor('#e1e7eb'))
        painter.drawText(pill.adjusted(s(21), 0, -s(3), 0), Qt.AlignmentFlag.AlignVCenter, fm.elidedText(status, Qt.TextElideMode.ElideRight, max(1,pill.width()-s(24))))
        painter.restore()


class ProjectLibrarySurface:
    def __init__(self, owner, panel, layout, old_controls):
        self.owner = owner; self.history = [owner.current_collection]; self.position = 0
        panel.setObjectName('projectLibrarySurface')
        owner.project_library_title.hide(); old_controls.hide()
        layout.removeWidget(owner.project_library_title); layout.removeWidget(old_controls)
        layout.removeWidget(owner.project_filter_box); layout.removeWidget(owner.project_search)
        owner.project_filter_box.hide()
        self.navigation = QWidget(); nav = QHBoxLayout(self.navigation)
        nav.setContentsMargins(0,0,0,0); nav.setSpacing(4)
        self.back = self.button('', 'Back', lambda: self.travel(-1), nav, 'back')
        self.forward = self.button('', 'Forward', lambda: self.travel(1), nav, 'forward')
        self.home = self.button('', 'All projects', lambda: self.navigate(None), nav, 'home')
        self.location = QComboBox(); self.location.setMinimumWidth(0)
        self.location.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.location.setAccessibleName('Project collection')
        self.location.currentIndexChanged.connect(self.location_changed); nav.addWidget(self.location,1)
        layout.insertWidget(0,self.navigation)
        self.actions = QWidget(); actions = QHBoxLayout(self.actions)
        actions.setContentsMargins(0,0,0,0); actions.setSpacing(3)
        self.new = self.button('New', 'Create or open a project', None, actions, 'new')
        menu = QMenu(self.new); menu.addAction('New project',owner.new_project); menu.addAction('Open project…',owner.open_project)
        self.new.setMenu(menu); self.new.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.rename = self.button('Rename', 'Rename selected project or collection', self.rename_selected, actions, 'rename')
        self.rename_action=QAction('Rename',owner.project_list)
        self.rename_action.setShortcut(QKeySequence('F2'))
        self.rename_action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.rename_action.triggered.connect(self.rename_selected)
        owner.project_list.addAction(self.rename_action)
        owner.project_list.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.delete = self.button('Delete', 'Delete selected project', owner.delete_library_project, actions, 'delete')
        self.catalog = self.button('Catalog', 'Open media catalog', lambda: (owner.catalog_dock.show(),owner.catalog_dock.raise_()), actions,'library-catalog')
        self.more = self.button('…', 'More project actions', None, actions)
        self.more.setObjectName('projectMore')
        more = QMenu(self.more)
        self.duplicate_action=more.addAction('Duplicate project',owner.duplicate_library_project)
        self.details_action=more.addAction('Project details…',owner.edit_library_project)
        self.archive_action=more.addAction('Archive / restore selection',owner.toggle_archive_selected)
        sort_action = QWidgetAction(more); sort_action.setDefaultWidget(owner.project_sort); more.addAction(sort_action)
        owner.project_icon_button.menu().setTitle('Thumbnail size')
        more.addMenu(owner.project_icon_button.menu())
        self.more.setMenu(more); self.more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        layout.insertWidget(1,self.actions)
        self.browser = QFrame(); self.browser.setObjectName('projectBrowser')
        browser = QVBoxLayout(self.browser); browser.setContentsMargins(6,6,6,6); browser.setSpacing(6)
        search = QHBoxLayout(); search.setSpacing(4); search.addWidget(owner.project_search,1)
        self.grid = self.button('', 'Grid view', lambda: self.set_view('grid'),search,'grid')
        self.list = self.button('', 'List view', lambda: self.set_view('list'),search,'list')
        for button in (self.grid,self.list):button.setCheckable(True)
        browser.addLayout(search)
        self.filters = QHBoxLayout(); self.filters.setSpacing(5)
        self.color = self.button('Color: All', 'Filter project card colors', None,self.filters,'color')
        self.status = self.button('Status: All', 'Filter project statuses and tags',None,self.filters)
        self.status.setIcon(dot_icon('#2ed05d'))
        self.status.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.color.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Fixed)
        self.status.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Fixed)
        self.filters.setStretch(0,1);self.filters.setStretch(1,1)
        self.reset = self.button('', 'Clear search and reset filters', self.clear_filters,self.filters,'reset-filter')
        self.color_menu = CheckMenu(self.color); self.color_actions={}
        all_colors=self.color_menu.add_check('All colors')
        self.all_colors=all_colors;all_colors.triggered.connect(self.select_all_colors)
        saved=owner.settings.value('project_color_filters',None)
        try:chosen=json.loads(saved) if isinstance(saved,str) else None
        except ValueError:chosen=None
        for name,color in [('No color',''),*COLORS]:
            action=self.color_menu.add_check(name,color or '#89949f',chosen is None or color in chosen)
            action.toggled.connect(self.color_changed);self.color_actions[color]=action
        self.color.setMenu(self.color_menu);self.color.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.status.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        browser.addLayout(self.filters)
        layout.removeWidget(owner.project_list);browser.addWidget(owner.project_list,1)
        owner.project_list.setItemDelegate(ProjectCardDelegate(owner,owner.project_list))
        owner.project_list.panel_owner=owner
        owner.project_list.currentItemChanged.connect(lambda *_:self.refresh_selection())
        layout.addWidget(self.browser,1)
        self.rebuild_filters();self.color_changed()
        self.set_view(str(owner.settings.value('project_view_mode','grid')))
        self.refresh_selection()

    def button(self,text,tip,callback,layout,icon=None):
        button=QToolButton();button.setText(text);button.setToolTip(tip);button.setAccessibleName(tip)
        if icon:
            from .ui import toolbar_icon
            button.setIcon(toolbar_icon(icon))
            button.setIconSize(QSize(16,16))
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon if text else Qt.ToolButtonStyle.ToolButtonIconOnly)
        if callback:button.clicked.connect(callback)
        layout.addWidget(button);return button

    def refresh(self,records):
        current=self.owner.current_collection
        self.location.blockSignals(True);self.location.clear();self.location.addItem('Projects',None)
        for name in sorted({str(r['collection']) for r in records if r.get('collection')}):
            self.location.addItem('Projects › '+name,name)
        if current and self.location.findData(current)<0:self.location.addItem('Projects › '+current,current)
        self.location.setCurrentIndex(max(0,self.location.findData(current)));self.location.blockSignals(False)
        self.owner.project_search.setPlaceholderText('Search '+(current or 'projects'))
        self.back.setEnabled(self.position>0);self.forward.setEnabled(self.position<len(self.history)-1)

    def navigate(self,collection,remember=True):
        self.owner._project_open_timer.stop();self.owner._pending_project_id=None
        if collection!=self.owner.current_collection and remember:
            self.history=self.history[:self.position+1]+[collection];self.position+=1
        self.owner.current_collection=collection;self.owner.project_search.clear()
        self.owner.refresh_project_library(preserve_scroll=False)

    def travel(self,delta):
        position=self.position+delta
        if 0<=position<len(self.history):
            self.position=position;self.navigate(self.history[position],False)

    def location_changed(self,index):self.navigate(self.location.itemData(index))

    def rename_selected(self):
        item=self.owner.project_list.currentItem()
        if item is None or item.isHidden():return
        self.owner._project_open_timer.stop();self.owner._pending_project_id=None
        item.setFlags(item.flags()|Qt.ItemFlag.ItemIsEditable)
        self.owner.project_list.setFocus()
        self.owner.project_list.editItem(item)

    def rename_to(self,meta,name):
        name=name.strip()
        if not name or name==str(meta.get('name','')):return
        if meta.get('kind')=='collection' and name.casefold()!=str(meta.get('name','')).casefold():
            existing={str(self.location.itemData(i) or '').casefold() for i in range(self.location.count())}
            if name.casefold() in existing:
                QMessageBox.warning(self.owner,'Collection name in use','Choose a different name to keep these collections separate.')
                return
        for member in meta.get('members',[]) if meta.get('kind')=='collection' else [meta]:
            member['collection' if meta.get('kind')=='collection' else 'name']=name
            self.owner.persist_library_metadata(member)
        if meta.get('kind')=='collection':
            self.history=[name if value==meta.get('name') else value for value in self.history]
        elif str(meta.get('id'))==self.owner.current_project_id:
            self.owner.current_project_name=name
            if self.owner.current_project_id in self.owner.project_sessions:
                self.owner.project_sessions[self.owner.current_project_id]['name']=name
            self.owner.update_window_title()
        self.owner.refresh_project_library(str(meta.get('id','')) or None)
        if meta.get('kind')=='collection':
            for row in range(self.owner.project_list.count()):
                item=self.owner.project_list.item(row)
                value=item.data(Qt.ItemDataRole.UserRole) or {}
                if value.get('kind')=='collection' and value.get('name')==name:
                    self.owner.project_list.setCurrentItem(item)
                    break

    def refresh_selection(self):
        item=self.owner.project_list.currentItem()
        visible=item is not None and not item.isHidden()
        meta=item.data(Qt.ItemDataRole.UserRole) if item else {}
        project=visible and meta.get('kind')=='project'
        self.rename.setEnabled(visible);self.rename_action.setEnabled(visible);self.delete.setEnabled(project)
        self.duplicate_action.setEnabled(project);self.details_action.setEnabled(project)
        self.archive_action.setEnabled(visible)

    def rebuild_filters(self):
        menu=CheckMenu(self.status);all_statuses=menu.add_check('All statuses')
        keys=[key for key in self.owner.project_filter_buttons if key!='__archive__' and not key.startswith('tag:')]
        def toggle_all(yes):
            for key in keys:self.owner.project_filter_buttons[key].setChecked(yes)
        all_statuses.triggered.connect(toggle_all)
        self.status_actions={}
        extras=CheckMenu('More filters',menu)
        for key,button in self.owner.project_filter_buttons.items():
            tag=next((t for t in self.owner.project_status_tags()+self.owner.project_other_tags() if t['name']==button.text()),None)
            target=extras if key in ('__untagged__','__archive__') or key.startswith('tag:') or button.text() in {'Done','Consider','Re-shoot'} else menu
            action=target.add_check(button.text(),tag['color'] if tag else '#89949f',button.isChecked())
            action.toggled.connect(button.setChecked);button.toggled.connect(action.setChecked)
            button.toggled.connect(lambda *_:self.update_status_label())
            self.status_actions[key]=action
        menu.addSeparator();menu.addMenu(extras)
        previous=self.status.menu();self.status.setMenu(menu)
        if previous:previous.deleteLater()
        self.all_statuses=all_statuses;self.status_keys=keys;self.update_status_label()

    def update_status_label(self):
        buttons=self.owner.project_filter_buttons
        count=sum(buttons[key].isChecked() for key in self.status_keys)
        self.all_statuses.setChecked(count==len(self.status_keys))
        self.status.setText('Status: All' if count==len(self.status_keys) else f'Status: {count}')

    def select_all_colors(self,yes):
        for action in self.color_actions.values():action.setChecked(yes)

    def color_changed(self):
        if not hasattr(self,'color_actions'):return
        chosen=[color for color,action in self.color_actions.items() if action.isChecked()]
        all_selected=len(chosen)==len(self.color_actions)
        self.all_colors.setChecked(all_selected);self.color.setText('Color: All' if all_selected else f'Color: {len(chosen)}')
        self.owner.settings.setValue('project_color_filters',json.dumps(chosen))
        if hasattr(self.owner,'project_list'):self.owner.filter_projects(self.owner.project_search.text())

    def accepts_color(self,meta):
        if all(action.isChecked() for action in self.color_actions.values()):return True
        color=str(meta.get('color') or '')
        if not QColor(color).isValid():color=''
        return bool(self.color_actions.get(color) and self.color_actions[color].isChecked())

    def clear_filters(self):
        self.owner.project_search.clear();self.select_all_colors(True)
        for key,button in self.owner.project_filter_buttons.items():button.setChecked(key!='__archive__' and not key.startswith('tag:'))

    def set_view(self,mode):
        owner=self.owner;grid=mode!='list'
        self.grid.setChecked(grid);self.list.setChecked(not grid)
        owner.settings.setValue('project_view_mode','grid' if grid else 'list')
        owner.project_list.setViewMode(QListWidget.ViewMode.IconMode if grid else QListWidget.ViewMode.ListMode)
        owner.project_list.setFlow(QListWidget.Flow.LeftToRight if grid else QListWidget.Flow.TopToBottom)
        owner.project_list.setWrapping(grid);owner.update_project_icon_controls()
        for row in range(owner.project_list.count()):owner.project_list.item(row).setSizeHint(self.card_size())

    def card_size(self):
        scale=self.owner.settings.value('ui_text_scale',100,int)/100
        if self.owner.project_list.viewMode()==QListWidget.ViewMode.ListMode:return QSize(0,round(102*scale))
        gap=self.owner.project_list.spacing()
        available=max(1,self.owner.project_list.viewport().width()-2*gap)
        goal=round((self.owner.project_icon_size+36)*scale)
        columns=max(1,available//max(1,goal+2*gap))
        width=max(round(96*scale),available//columns-2*gap)
        return QSize(width,round((self.owner.project_icon_size+66)*scale))

    def layout_cards(self):
        size=self.card_size();view=self.owner.project_list
        grid=size if view.viewMode()==QListWidget.ViewMode.IconMode else QSize()
        if view.gridSize()!=grid:view.setGridSize(grid)
        for row in range(view.count()):
            item=view.item(row)
            if item.sizeHint()!=size:item.setSizeHint(size)


class PromptSurface(QFrame):
    """One visible editor, retaining each original document and its signals."""
    def __init__(self, owner):
        super().__init__(); self.owner=owner; self.updating=False; self.workspace=None
        self.setObjectName('unifiedPromptSurface')
        layout=QVBoxLayout(self);layout.setContentsMargins(9,5,9,7);layout.setSpacing(5)
        header=QHBoxLayout();header.setContentsMargins(1,0,1,0)
        self.scope=QComboBox();self.scope.setObjectName('surfaceScope')
        self.scope.setAccessibleName('Prompt scope')
        for label,key in [('SEGMENT PROMPT','segment'),('GLOBAL PROMPT','global'),('UNIFIED PROMPT','unified')]:self.scope.addItem(label,key)
        self.scope.setToolTip('Segment edits one beat. Global directs the whole sequence. Unified edits the production draft.')
        self.scope.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Fixed)
        header.addWidget(self.scope,1)
        self.collapse=QToolButton();self.collapse.setText('⌄');self.collapse.setCheckable(True)
        self.collapse.setObjectName('surfaceCollapse')
        self.collapse.setAccessibleName('Collapse prompt editor');header.addWidget(self.collapse)
        layout.addLayout(header)
        self.body=QWidget();self.body.setObjectName('promptSurfaceBody')
        body=QVBoxLayout(self.body);body.setContentsMargins(0,0,0,0);body.setSpacing(3)
        for marquee in (owner.director_ai_marquee,owner.segment_ai_marquee,owner.minimax_panel.ai_marquee):body.addWidget(marquee)
        body.addWidget(owner.minimax_panel.message_panel)
        self.content=QFrame();self.content.setObjectName('promptSurfaceContent')
        content=QHBoxLayout(self.content);content.setContentsMargins(0,0,7,0);content.setSpacing(5)
        self.editors=QStackedWidget();self.editors.setMinimumWidth(0)
        self.editors.setObjectName('surfaceEditorStack')
        self.editors.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Ignored)
        for editor in (owner.intent,owner.intent_segment_prompt,owner.segment_prompt,owner.unified_prompt,owner.global_prompt):
            self.editors.addWidget(editor)
            editor.setObjectName('surfacePromptEditor');editor.setMinimumWidth(0)
            editor.textChanged.connect(self.refresh_counter)
        content.addWidget(self.editors,1)
        self.rail=QWidget();self.rail.setObjectName('promptActionRail')
        rail=QVBoxLayout(self.rail);rail.setContentsMargins(0,7,0,0);rail.setSpacing(3)
        self.quick=QPushButton('Quick Fix');self.quick.setObjectName('surfaceQuickFix')
        self.quick_menu=QMenu(self.quick);self.quick.setMenu(self.quick_menu)
        self.quick_menu.aboutToShow.connect(self.populate_quick_fixes)
        self.generate=QPushButton('+ Generate');self.generate.setObjectName('surfaceGenerate')
        self.refine=QPushButton('Refine');self.refine.setObjectName('surfaceRefine')
        for control in (self.quick,self.generate,self.refine):rail.addWidget(control)
        rail.addStretch(1);content.addWidget(self.rail)
        body.addWidget(self.content,1)
        footer=QHBoxLayout();footer.setContentsMargins(2,0,0,0);footer.setSpacing(5)
        self.counter=QLabel();self.counter.setObjectName('surfacePromptCount');footer.addWidget(self.counter)
        footer.addWidget(owner.minimax_panel.cache_state);footer.addStretch()
        footer.addWidget(owner.sync_combined_button)
        self.copy=QToolButton();self.copy.setText('Copy');self.copy.setToolTip('Copy the active prompt')
        self.copy.clicked.connect(lambda:QApplication.clipboard().setText(self.editors.currentWidget().toPlainText()))
        footer.addWidget(self.copy);body.addLayout(footer);layout.addWidget(self.body,1)
        self.collapse.toggled.connect(self.set_collapsed)
        self.generate.clicked.connect(lambda:self.run(False));self.refine.clicked.connect(lambda:self.run(True))
        self.scope.currentIndexChanged.connect(self.scope_changed)
        owner.prompt_scope.currentIndexChanged.connect(self.legacy_scope_changed)
        self.setMinimumHeight(90)
        self.collapse.setChecked(owner.settings.value('prompt_surface_collapsed',False,bool))

    def set_collapsed(self,collapsed):
        self.body.setVisible(not collapsed);self.collapse.setText('›' if collapsed else '⌄')
        self.owner.settings.setValue('prompt_surface_collapsed',bool(collapsed))
        self.owner.queue_settings_sync()

    def select(self,scope):
        index=self.scope.findData(scope)
        if index>=0 and self.scope.model().item(index).isEnabled():
            self.scope.setCurrentIndex(index);self.scope_changed()

    def legacy_scope_changed(self,*_):
        if not self.updating and not self.owner._loading:
            self.select(str(self.owner.prompt_scope.currentData()))

    def scope_changed(self,*_):
        if self.updating:return
        self.updating=True;owner=self.owner;scope=self.scope.currentData()
        owner.finish_prompt_reviews()
        if hasattr(owner,'inline_typing') and owner.inline_typing.active:owner.inline_typing.suspend()
        if scope in ('global','segment'):
            owner.prompt_scope.setCurrentIndex(owner.prompt_scope.findData(scope))
            owner.refresh_shared_editor()
        self.updating=False;self.refresh_editor()
        owner.settings.setValue('prompt_surface_scope/'+owner.project_type,scope)

    def sync_workspace(self):
        owner=self.owner
        changed=self.workspace!=owner.project_type;self.workspace=owner.project_type
        self.updating=True
        for index in range(self.scope.count()):
            self.scope.model().item(index).setEnabled(not owner.prompt_only_workspace or self.scope.itemData(index)=='unified')
        if changed:
            scope='unified' if owner.prompt_only_workspace else str(owner.settings.value('prompt_surface_scope/'+owner.project_type,'segment'))
            self.scope.setCurrentIndex(max(0,self.scope.findData(scope)))
        self.updating=False
        for panel in (owner.director_panel,owner.segment_panel,owner.unified_panel,owner.prompt_splitter):panel.hide()
        owner.prompt_scope.hide()
        # Legacy action widgets remain connected for menus, shortcuts and tests.
        for action in (owner.magic_button,owner.prompt_refine_button,owner.segment_quick_fix_button,
                       owner.combined_generate_button,owner.combined_refine_button,owner.combined_quick_fix_button):action.hide()
        self.setVisible(owner.workspace_engine!='shot_sequence')
        self.scope_changed()

    def refresh_editor(self):
        owner=self.owner;scope=self.scope.currentData()
        if scope=='unified':editor=owner.unified_prompt if owner.unified_workspace else owner.global_prompt
        elif scope=='global':editor=owner.intent
        else:editor=owner.intent_segment_prompt if owner.unified_workspace else owner.segment_prompt
        self.editors.setCurrentWidget(editor)
        if scope=='global':editor.setPlaceholderText("Describe the whole sequence: narrative, pacing, camera, sound and ending…")
        elif scope=='unified' and not owner.unified_workspace:editor.setPlaceholderText('Write or generate the shared LTX global production prompt…')
        owner.sync_combined_button.setVisible(scope=='unified' and owner.unified_workspace and not owner.prompt_only_workspace)
        owner.minimax_panel.cache_state.setVisible(scope=='unified' and owner.unified_workspace)
        self.refresh_counter();self.refresh_actions()

    def refresh_counter(self):
        if self.editors.currentWidget():self.counter.setText(f'{len(self.editors.currentWidget().toPlainText())} characters')
        if hasattr(self,'scope') and not self.updating:self.refresh_actions()

    def refresh_actions(self):
        owner=self.owner;scope=self.scope.currentData();available=not owner.ai_busy and owner.workspace_engine!='shot_sequence'
        self.generate.setVisible(not owner.prompt_only_workspace)
        if scope=='unified':
            self.generate.setEnabled(available and bool(owner.segments) and not owner.prompt_only_workspace)
            self.refine.setEnabled(available and bool(self.editors.currentWidget().toPlainText().strip()) and (bool(owner.segments) or owner.prompt_only_workspace))
        else:
            self.generate.setEnabled(available and owner.magic_button.isEnabled())
            self.refine.setEnabled(available and owner.prompt_refine_button.isEnabled())
        self.quick.setEnabled(self.refine.isEnabled());self.scope.setEnabled(available)
        self.collapse.setEnabled(available)

    def run(self,refine):
        owner=self.owner;scope=self.scope.currentData()
        if owner.ai_busy:return
        if scope in ('global','segment') and owner.prompt_scope.currentData()!=scope:
            owner.prompt_scope.setCurrentIndex(owner.prompt_scope.findData(scope))
        if scope=='unified':
            if owner.unified_workspace:
                (owner.refine_combined_prompt if refine else owner.generate_combined_prompt)()
            else:self.run_shared_production(refine)
        else:
            (owner.refine_upper_prompt if refine else owner.generate_upper_prompt)()

    def run_shared_production(self,refine):
        from .ai import generate_global_direction,refine_shared_production_prompt
        owner=self.owner
        if owner.ai_busy or not owner.segments:return
        owner.finish_prompt_reviews();owner.inline_typing.finish()
        credentials=owner.ai_credentials()
        if not credentials:return
        provider,model,key=credentials;timeout=owner.settings.value('api_timeout',400,int)
        before=owner.global_prompt.toPlainText();segments=copy.deepcopy(owner.combined_generation_source_key())
        operation=refine_shared_production_prompt if refine else generate_global_direction
        notes=owner.build_refinement_request()
        args=(provider,model,key,before,timeout) if refine else (copy.deepcopy(owner.segments),provider,model,key,notes,timeout)
        def finished(result):
            if before==owner.global_prompt.toPlainText() and segments==owner.combined_generation_source_key():
                owner.global_prompt.setPlainText(result if isinstance(result,str) else result['globalPrompt'])
                if not refine:owner.apply_global_prefixes()
            else:owner.statusBar().showMessage('The prompt or timeline changed; the old response was discarded.',7000)
            owner.set_ai_controls_enabled(True);self.refresh_editor()
        owner.start_ai_worker(operation,args,'Refining the shared LTX production prompt…' if refine else 'Generating the shared LTX production prompt…',finished,show_main_overlay=False)

    def populate_quick_fixes(self):
        owner=self.owner;scope=self.scope.currentData()
        if scope!='global':
            owner.populate_quick_fix_menu(self.quick_menu,'unified' if scope=='unified' else 'segment');return
        from .effects_library import load_effects,preset_groups
        self.quick_menu.clear()
        for category,presets in preset_groups([e for e in load_effects(owner.settings) if e.get('show_in_menu') and e.get('prompt')],'segment'):
            heading=self.quick_menu.addAction(category);heading.setEnabled(False)
            for preset in presets:
                action=self.quick_menu.addAction(preset['name']);action.setEnabled(not owner.ai_busy and bool(owner.segments))
                action.triggered.connect(lambda checked=False,p=preset:self.global_quick_fix(p))
        self.quick_menu.addSeparator();self.quick_menu.addAction('Effects Library…',lambda:owner.open_effects_library('segment'))

    def global_quick_fix(self,preset):
        from .prompt_tags import insert_note
        from PySide6.QtGui import QTextCursor
        cursor=QTextCursor(self.owner.intent.document());cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText('\n\n');insert_note(self.owner.intent,cursor,'/refine-global',preset['prompt'])
        self.owner.refine_upper_prompt()
