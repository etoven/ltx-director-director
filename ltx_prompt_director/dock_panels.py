"""Native tabbed panel groups with Adobe-inspired chrome and collapsible icon rails."""
import json
from PySide6.QtCore import Qt, QObject, QTimer, QSize
from PySide6.QtGui import QColor, QIcon, QPainter
from PySide6.QtWidgets import (QDockWidget, QFrame, QHBoxLayout, QLabel, QMenu,
    QMainWindow, QSizePolicy, QTabBar, QTabWidget, QToolBar, QToolButton, QVBoxLayout, QWidget)

LEFT=Qt.DockWidgetArea.LeftDockWidgetArea
RIGHT=Qt.DockWidgetArea.RightDockWidgetArea


def panel_style(scale):
    s=lambda n:max(1,round(n*scale))
    return f'''
QMainWindow::separator{{width:{s(4)}px;height:{s(5)}px;background:#1c1c1c;background-image:none}}
QMainWindow::separator:hover{{background:#568de5}}
QDockWidget[adobePanel="true"]{{background:#292929;color:#e3e3e3}}
QDockWidget[adobePanel="true"] QWidget{{background-color:#292929;color:#e3e3e3}}
QDockWidget[adobePanel="true"] QLabel{{background-color:transparent}}
QFrame#panelIconRail{{background:#202020;border:0}}
QToolButton#panelRailButton{{background:transparent;border:1px solid transparent;border-radius:{s(4)}px;padding:0;min-height:0}}
QToolButton#panelRailButton:hover{{background:#383838;border-color:#545454}}
QToolButton#panelRailButton:checked{{background:#393939;border-color:#525252;border-left:2px solid #6ea8ff}}
QToolButton#panelRailButton:focus{{border:2px solid #6ea8ff}}
QToolButton#railChevron{{background:transparent;border:0;border-bottom:1px solid #3a3a3a;border-radius:0;color:#c4c4c4;font-size:{s(17)}px;padding:0;min-height:0}}
QToolButton#railChevron:hover{{background:#383838;color:#fff}}
QWidget#panelGroupHeader{{background:#292929;border:0;border-bottom:1px solid #1b1b1b}}
QLabel#panelGroupTitle{{background:transparent;color:#e5e5e5;font-size:{s(12)}px;font-weight:600;border:0;letter-spacing:0}}
QLabel#panelGroupGrip{{background:transparent;color:#787878;font-size:{s(13)}px;border:0}}
QToolButton#panelHeaderControl{{background:transparent;border:0;border-radius:{s(3)}px;color:#bcbcbc;font-size:{s(15)}px;padding:0;min-height:0}}
QToolButton#panelHeaderControl:hover{{background:#414141;color:#fff}}
QToolButton#panelHeaderControl:focus{{border:1px solid #6ea8ff}}
QTabBar#panelDockTabs{{background:#222;border:0;padding:0;margin:0}}
QTabBar#panelDockTabs::tab{{background:#222;color:#bdbdbd;border:0;border-bottom:2px solid transparent;border-radius:0;padding:{s(6)}px {s(10)}px;margin:0;font-size:{s(13)}px;font-weight:400;min-width:0}}
QTabBar#panelDockTabs::tab:hover{{background:#303030;color:#eee}}
QTabBar#panelDockTabs::tab:selected{{background:#303030;color:#fff;border-bottom-color:#d6d6d6;font-weight:600}}
QTabBar#panelDockTabs:focus{{border-bottom:1px solid #6ea8ff}}
QTabBar#panelDockTabs QToolButton{{background:#292929;border:1px solid #444;color:#ddd;border-radius:0;padding:0;min-height:0}}
QDockWidget[adobePanel="true"] #projectLibrarySurface,
QDockWidget[adobePanel="true"] #effectsPanel,
QDockWidget[adobePanel="true"] #mediaCatalogPanel{{background:#292929;border-color:#424242}}
QDockWidget[adobePanel="true"] #projectBrowser,
QDockWidget[adobePanel="true"] #effectTiles{{background:#202020;border:1px solid #424242;border-radius:{s(4)}px}}
QDockWidget[adobePanel="true"] #projectSearch,
QDockWidget[adobePanel="true"] QLineEdit,
QDockWidget[adobePanel="true"] QTextEdit,
QDockWidget[adobePanel="true"] QPlainTextEdit{{background:#202020;color:#e3e3e3;border:1px solid #4b4b4b;border-radius:{s(4)}px;selection-background-color:#345d93}}
QDockWidget[adobePanel="true"] QLineEdit:focus,
QDockWidget[adobePanel="true"] QTextEdit:focus,
QDockWidget[adobePanel="true"] QPlainTextEdit:focus{{border-color:#6ea8ff}}
QDockWidget[adobePanel="true"] QPushButton,
QDockWidget[adobePanel="true"] QComboBox,
QDockWidget[adobePanel="true"] QToolButton{{background:#333;color:#e2e2e2;border-color:#555}}
QDockWidget[adobePanel="true"] QPushButton:hover,
QDockWidget[adobePanel="true"] QComboBox:hover,
QDockWidget[adobePanel="true"] QToolButton:hover{{background:#414141;border-color:#777}}
QDockWidget[adobePanel="true"] QPushButton:disabled,
QDockWidget[adobePanel="true"] QToolButton:disabled{{color:#898989}}
QDockWidget[adobePanel="true"] #projectLibrarySurface QToolButton,
QDockWidget[adobePanel="true"] #projectLibrarySurface QComboBox,
QDockWidget[adobePanel="true"] #effectsPanel QComboBox{{background:#323232;border-color:#535353;color:#ddd}}
QDockWidget[adobePanel="true"] #projectLibrarySurface QToolButton:hover{{background:#424242;border-color:#737373}}
QDockWidget[adobePanel="true"] #projectLibrarySurface QToolButton:checked{{background:#464646;border-color:#8b8b8b}}
QDockWidget[adobePanel="true"] #referenceImageSlot{{background:#303030;border-color:#484848}}
QDockWidget[adobePanel="true"] #referenceImagePreview{{background:#222;border-color:#505050}}
QDockWidget[adobePanel="true"] #referenceImageSlot[dropActive="true"],
QDockWidget[adobePanel="true"] #mediaCatalogPanel[dropActive="true"]{{background:#263749;border-color:#6ea8ff}}
QDockWidget[adobePanel="true"] #sectionLabel{{color:#c8c8c8;letter-spacing:0}}
'''


class DockHeader(QWidget):
    def __init__(self,manager,dock):
        super().__init__(dock);self.manager=manager;self.dock=dock;self.setObjectName('panelGroupHeader')
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground,True)
        row=QHBoxLayout(self);row.setContentsMargins(9,0,4,0);row.setSpacing(3)
        self.grip=QLabel('⠿');self.grip.setObjectName('panelGroupGrip');self.grip.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        row.addWidget(self.grip)
        self.label=QLabel(dock.windowTitle());self.label.setObjectName('panelGroupTitle')
        self.label.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
        self.label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        row.addWidget(self.label,1)
        self.collapse=QToolButton();self.collapse.setObjectName('panelHeaderControl');self.collapse.setToolTip('Collapse to panel icons')
        self.collapse.clicked.connect(lambda:manager.collapse_dock(dock));row.addWidget(self.collapse)
        self.menu=QToolButton();self.menu.setObjectName('panelHeaderControl');self.menu.setText('≡');self.menu.setToolTip('Panel options and grouping')
        self.menu.clicked.connect(lambda:self.show_menu(self.menu.mapTo(self,self.menu.rect().bottomLeft())));row.addWidget(self.menu)
        self.close=QToolButton();self.close.setObjectName('panelHeaderControl');self.close.setText('×');self.close.setToolTip('Close panel');self.close.clicked.connect(lambda:manager.close_dock(dock));row.addWidget(self.close)
        dock.windowTitleChanged.connect(self.label.setText)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu);self.customContextMenuRequested.connect(self.show_menu)

    def mousePressEvent(self,event):event.ignore()
    def mouseMoveEvent(self,event):event.ignore()
    def mouseReleaseEvent(self,event):event.ignore()
    def mouseDoubleClickEvent(self,event):event.ignore()

    def show_menu(self,point):
        menu=QMenu(self)
        for label,area in [('Dock left',LEFT),('Dock right',RIGHT),('Dock top',Qt.DockWidgetArea.TopDockWidgetArea),('Dock bottom',Qt.DockWidgetArea.BottomDockWidgetArea)]:
            if self.dock.allowedAreas() & area:
                menu.addAction(label,lambda a=area:self.manager.dock_at(self.dock,a))
        if self.dock.features() & QDockWidget.DockWidgetFeature.DockWidgetFloatable:
            menu.addAction('Dock panel' if self.dock.isFloating() else 'Float panel',lambda:self.dock.setFloating(not self.dock.isFloating()))
        group=menu.addMenu('Group with')
        for name,_,other,_ in self.manager.entries:
            if (other is not self.dock and not other.isFloating() and other.toggleViewAction().isVisible()
                    and self.dock.allowedAreas() & self.manager.owner.dockWidgetArea(other)):
                group.addAction(name,lambda checked=False,d=other:self.manager.tabify(self.dock,d))
        group.setEnabled(bool(group.actions()))
        menu.addSeparator();menu.addAction('Collapse to panel icons',lambda:self.manager.collapse_dock(self.dock))
        if self.dock.features() & QDockWidget.DockWidgetFeature.DockWidgetClosable:menu.addAction('Close panel',lambda:self.manager.close_dock(self.dock))
        if self.dock.contextMenuPolicy()==Qt.ContextMenuPolicy.CustomContextMenu:
            menu.addAction('Panel sizing and options…',lambda:self.dock.customContextMenuRequested.emit(self.dock.mapFromGlobal(self.mapToGlobal(point))))
        menu.exec(self.mapToGlobal(point))


class IconRail(QToolBar):
    def __init__(self,manager,side):
        super().__init__(manager.owner)
        self.setObjectName('fixedPanelIconRail'+side)
        self.setMovable(False)
        self.setFloatable(False)
        self.setAllowedAreas(Qt.ToolBarArea.LeftToolBarArea if side=='left' else Qt.ToolBarArea.RightToolBarArea)
        self.toggleViewAction().setVisible(False)
        self.rail=QFrame();self.rail.setObjectName('panelIconRail')
        self.rail.setSizePolicy(QSizePolicy.Policy.Preferred,QSizePolicy.Policy.Expanding)
        self.icons=QVBoxLayout(self.rail);self.icons.setContentsMargins(3,0,3,0);self.icons.setSpacing(3)
        self.chevron=QToolButton();self.chevron.setObjectName('railChevron');self.chevron.setText('»' if side=='left' else '«');self.chevron.setToolTip('Expand panels')
        self.chevron.clicked.connect(lambda:manager.toggle_column(side));self.icons.addWidget(self.chevron);self.icons.addStretch();self.addWidget(self.rail)


class PanelRails(QObject):
    def __init__(self,owner,entries):
        super().__init__(owner);self.owner=owner;self.entries=entries;self.headers={};self.buttons={};self.expanded={"left":False,"right":False};self.scale=1.0
        options=QMainWindow.DockOption
        owner.setDockOptions((owner.dockOptions() | options.AllowNestedDocks | options.AllowTabbedDocks | options.GroupedDragging)
                             & ~options.ForceTabbedDocks & ~options.VerticalTabs)
        owner.setTabPosition(Qt.DockWidgetArea.AllDockWidgetAreas,QTabWidget.TabPosition.North)
        self._refresh=QTimer(self);self._refresh.setSingleShot(True);self._refresh.timeout.connect(self.refresh)
        self.last={side:str(owner.settings.value('panels/'+side+'/active','')) for side in ('left','right')}
        self.widths={side:owner.settings.value('panels/'+side+'/width',330 if side=='left' else 390,int) for side in ('left','right')}
        self.members={side:self.saved_names(side,'members',None) for side in ('left','right')}
        self.selected={side:self.saved_names(side,'selected') for side in ('left','right')}
        self.rails={side:IconRail(self,side) for side in ('left','right')}
        migrated=owner.settings.value('panels/icon_rails_v1',False,bool)
        opened={d: not d.isHidden() for _,_,d,_ in entries}
        for name,icon,dock,side in entries:
            dock.setProperty('adobePanel',True)
            dock.setWindowTitle(name)
            if dock.widget():dock.widget().setAttribute(Qt.WidgetAttribute.WA_StyledBackground,True)
            header=DockHeader(self,dock);header.label.setText(name);dock.setTitleBarWidget(header);self.headers[dock]=header
            button=QToolButton();button.setObjectName('panelRailButton');button.setIcon(icon);button.setCheckable(True);button.setToolTip(name);button.setAccessibleName(name)
            button.panel_icon=icon
            button.clicked.connect(lambda checked=False,d=dock:self.activate(d));self.buttons[dock]=button
            dock.visibilityChanged.connect(self.schedule);dock.dockLocationChanged.connect(self.schedule);dock.topLevelChanged.connect(self.schedule);dock.toggleViewAction().changed.connect(self.schedule)
        for side,rail in self.rails.items():
            # Rails are window chrome. Never restore the old dock placeholders.
            owner.addToolBar(Qt.ToolBarArea.LeftToolBarArea if side=='left' else Qt.ToolBarArea.RightToolBarArea,rail)
        if not migrated:
            saved=owner.settings.value('window/state')
            if saved is not None:owner.settings.setValue('window/state_before_icon_rails',saved)
            # Retain existing floating and top/bottom docks during migration.
            for side in ('left','right'):
                area=LEFT if side=='left' else RIGHT
                group=[d for _,_,d,default in entries if default==side and not d.isFloating() and owner.dockWidgetArea(d) in (LEFT,RIGHT)]
                if not group:continue
                for dock in group:owner.removeDockWidget(dock)
                anchor=group[0];owner.addDockWidget(area,anchor)
                for dock in group[1:]:owner.tabifyDockWidget(anchor,dock)
                for dock in group:dock.setVisible(opened[dock])
                selected=next((d for d in group if opened[d]),None)
                if selected:selected.raise_();self.last[side]=selected.objectName()
            owner.settings.setValue('panels/icon_rails_v1',True)
        owner.tabifiedDockWidgetActivated.connect(self.activated)
        self.update_metrics(max(75,min(200,owner.settings.value('ui_text_scale',100,int)))/100)

    def saved_names(self,side,key,default=()):
        try:
            value=json.loads(str(self.owner.settings.value('panels/'+side+'/'+key,'null')))
            return [item for item in value if isinstance(item,str)] if isinstance(value,list) else default
        except (ValueError,TypeError):return default

    def side_for(self,dock):
        area=self.owner.dockWidgetArea(dock)
        if area in (LEFT,RIGHT):return 'left' if area==LEFT else 'right'
        return next(default for _,_,d,default in self.entries if d is dock)

    def group(self,side):
        return [dock for _,_,dock,_ in self.entries if self.side_for(dock)==side and not dock.isFloating() and self.owner.dockWidgetArea(dock) in (LEFT,RIGHT)]

    def schedule(self,*_):self._refresh.start(0)

    def activated(self,dock):
        if dock in self.buttons:self.last[self.side_for(dock)]=dock.objectName()
        self.schedule()

    def dock_at(self,dock,area):
        dock.setFloating(False);self.owner.addDockWidget(area,dock);dock.show();dock.raise_();self.schedule()

    def tabify(self,dock,target):
        area=self.owner.dockWidgetArea(target)
        if not dock.allowedAreas() & area:return
        dock.setFloating(False);self.owner.addDockWidget(area,dock)
        self.owner.tabifyDockWidget(target,dock)
        self.show_dock(target);self.show_dock(dock);dock.raise_();self.save();self.schedule()

    def close_dock(self,dock):
        side=self.side_for(dock)
        if dock in self.group(side) and self.members[side] is None:
            self.members[side]=[d.objectName() for d in self.group(side) if not d.isHidden()]
        for side in ('left','right'):
            if self.members[side] is not None:
                self.members[side]=[name for name in self.members[side] if name!=dock.objectName()]
            self.selected[side]=[name for name in self.selected[side] if name!=dock.objectName()]
        if dock is self.owner.minimax_panel.reference_dock:self.owner.set_reference_panel_open(False)
        else:dock.close()
        self.save();self.schedule()

    def show_dock(self,dock):
        if dock is self.owner.minimax_panel.reference_dock:self.owner.set_reference_panel_open(True)
        else:dock.show()

    def restore_group(self,side,target):
        """Reopen the prior tab groups, retaining native splits and selected tabs."""
        available=[d for d in self.group(side) if d.toggleViewAction().isVisible()]
        if self.members[side] is not None:
            restore=[d for d in available if d.objectName() in self.members[side]]
        else:
            peers=self.owner.tabifiedDockWidgets(target)
            restore=[d for d in available if d is target or d in peers]
            reference=self.owner.minimax_panel.reference_dock
            if target is not reference and not self.owner.settings.value('reference_panel_open',False,bool):
                restore=[d for d in restore if d is not reference]
        if target not in restore:restore.append(target)
        for dock in restore:self.show_dock(dock)
        for dock in restore:
            if dock.objectName() in self.selected[side]:dock.raise_()
        target.raise_()

    def activate(self,dock):
        if not dock.toggleViewAction().isVisible():return
        side=self.side_for(dock)
        if dock.isFloating():dock.show();dock.raise_();dock.activateWindow();return
        if self.owner.dockWidgetArea(dock) not in (LEFT,RIGHT):
            dock.setVisible(dock.isHidden())
            if not dock.isHidden():dock.raise_()
            self.schedule();return
        if not dock.isHidden() and not dock.visibleRegion().isEmpty():self.collapse(side);return
        expanded=any(not d.isHidden() for d in self.group(side))
        self.last[side]=dock.objectName()
        if not expanded:self.restore_group(side,dock)
        else:self.show_dock(dock)
        dock.raise_()
        if not expanded:self.owner.resizeDocks([dock],[self.widths[side]],Qt.Orientation.Horizontal)
        self.schedule()

    def expand(self,side):
        available=[d for d in self.group(side) if d.toggleViewAction().isVisible()]
        preferred=next((d for d in available if d.objectName() in self.selected[side]),available[0] if available else None)
        target=next((d for d in available if d.objectName()==self.last[side]),preferred)
        if target:self.activate(target)

    def toggle_column(self,side):
        if any(not dock.isHidden() for dock in self.group(side)):self.collapse(side)
        else:self.expand(side)

    def collapse_dock(self,dock):
        if dock.isFloating() or self.owner.dockWidgetArea(dock) not in (LEFT,RIGHT):
            dock.hide();self.save();self.schedule()
        else:self.collapse(self.side_for(dock))

    def collapse(self,side):
        group=self.group(side)
        visible=[d for d in group if not d.isHidden()]
        if visible:
            self.members[side]=[d.objectName() for d in visible]
            self.selected[side]=[d.objectName() for d in visible if not d.visibleRegion().isEmpty()]
        active=self.active_dock(side,visible)
        if active:self.widths[side]=active.width();self.last[side]=active.objectName()
        for dock in group:
            if dock is self.owner.minimax_panel.reference_dock and not dock.isHidden():self.owner.set_reference_panel_open(False)
            else:dock.hide()
        self.save();self.schedule()

    def save(self):
        for side in ('left','right'):
            visible=[d for d in self.group(side) if not d.isHidden()]
            active=self.active_dock(side,visible)
            if active:
                self.widths[side]=active.width();self.last[side]=active.objectName()
                self.members[side]=[d.objectName() for d in visible]
                self.selected[side]=[d.objectName() for d in visible if not d.visibleRegion().isEmpty()]
            self.owner.settings.setValue('panels/'+side+'/width',self.widths[side]);self.owner.settings.setValue('panels/'+side+'/active',self.last[side])
            for key,values in (('members',self.members),('selected',self.selected)):
                if values[side] is not None:
                    self.owner.settings.setValue('panels/'+side+'/'+key,json.dumps(values[side]))
        self.owner.save_window_panel_state()

    def active_dock(self,side,visible):
        shown=[d for d in visible if not d.visibleRegion().isEmpty()]
        return next((d for d in shown if d.objectName()==self.last[side]),shown[0] if shown else None)

    def refresh(self):
        for side,rail in self.rails.items():
            group=self.group(side)
            visible=[d for d in group if not d.isHidden()]
            actual=self.active_dock(side,visible)
            if actual:
                self.last[side]=actual.objectName()
                if not self.expanded[side]:
                    self.owner.resizeDocks([actual],[self.widths[side]],Qt.Orientation.Horizontal)
            self.expanded[side]=bool(visible)
            rail.chevron.setText(('«' if side=='left' else '»') if visible else ('»' if side=='left' else '«'))
            rail.chevron.setToolTip('Collapse panels to icons' if visible else 'Expand panels')
            rail.chevron.setEnabled(any(d.toggleViewAction().isVisible() for d in group))
            ordered=[d for _,_,d,_ in self.entries if self.side_for(d)==side]
            for index,dock in enumerate(ordered):
                button=self.buttons[dock]
                if button.parentWidget() is not rail.rail:rail.icons.insertWidget(rail.icons.count()-1,button)
                button.setVisible(dock.toggleViewAction().isVisible())
                button.setChecked(not dock.isFloating() and not dock.isHidden() and not dock.visibleRegion().isEmpty())
                header=self.headers[dock];header.collapse.setText('«' if side=='left' else '»')
                header.collapse.setVisible(not dock.isFloating())
                header.close.setVisible(bool(dock.features() & QDockWidget.DockWidgetFeature.DockWidgetClosable))
            rail.show()
        self.refresh_tabs()

    def refresh_tabs(self):
        names={d.windowTitle().replace('&',''):d for _,_,d,_ in self.entries}
        for dock,header in self.headers.items():
            grouped=not dock.isFloating() and any(not peer.isHidden() for peer in self.owner.tabifiedDockWidgets(dock))
            header.label.setVisible(not grouped)
            header.setFixedHeight(round((20 if grouped else 28)*self.scale))
        for bar in self.owner.findChildren(QTabBar):
            if bar.parentWidget() is not self.owner:continue
            labels=[bar.tabText(i).replace('&','') for i in range(bar.count())]
            if not labels or not set(labels)<=names.keys():continue
            if bar.objectName()!='panelDockTabs':
                bar.setObjectName('panelDockTabs')
                bar.setDocumentMode(True);bar.setDrawBase(False);bar.setExpanding(False)
                bar.setUsesScrollButtons(True);bar.setElideMode(Qt.TextElideMode.ElideNone);bar.setMovable(True)
                bar.setAccessibleName('Panel group tabs')
                bar.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
                bar.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Fixed)
                bar.style().unpolish(bar);bar.style().polish(bar)
            height=round(32*self.scale)
            if bar.minimumHeight()!=height or bar.maximumHeight()!=height:bar.setFixedHeight(height)
            for index,label in enumerate(labels):
                if bar.tabToolTip(index)!=label:bar.setTabToolTip(index,label)

    def update_metrics(self,scale=1):
        self.scale=scale
        s=lambda n:max(1,round(n*scale))
        for rail in self.rails.values():rail.setFixedWidth(s(38));rail.chevron.setFixedSize(s(32),s(26))
        for button in self.buttons.values():
            button.setFixedSize(s(32),s(36));button.setIconSize(QSize(s(20),s(20)))
            icon=QIcon()
            for mode,color in ((QIcon.Mode.Normal,'#c6c6c6'),(QIcon.Mode.Active,'#fff'),(QIcon.Mode.Selected,'#8bbcff'),(QIcon.Mode.Disabled,'#707070')):
                pixmap=button.panel_icon.pixmap(s(20),s(20))
                painter=QPainter(pixmap);painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
                painter.fillRect(pixmap.rect(),QColor(color));painter.end();icon.addPixmap(pixmap,mode)
            button.setIcon(icon)
        for header in self.headers.values():
            header.setFixedHeight(s(28))
            for button in (header.collapse,header.menu,header.close):button.setFixedSize(s(22),s(18))
        self.schedule()
