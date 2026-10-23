"""Collapsible icon rails around native Qt docks, retaining drag/float/layout support."""
from PySide6.QtCore import Qt, QObject, QTimer, QSize
from PySide6.QtWidgets import (QDockWidget, QFrame, QHBoxLayout, QLabel, QMenu,
    QSizePolicy, QTabBar, QToolButton, QVBoxLayout, QWidget)

LEFT=Qt.DockWidgetArea.LeftDockWidgetArea
RIGHT=Qt.DockWidgetArea.RightDockWidgetArea


def panel_style(scale):
    s=lambda n:max(1,round(n*scale))
    return f'''
QMainWindow::separator{{width:{s(4)}px;height:{s(6)}px;background-image:none}}
QFrame#panelIconRail{{background:#18202a;border:0}}
QToolButton#panelRailButton{{background:transparent;border:1px solid transparent;border-radius:3px;padding:0;min-height:0}}
QToolButton#panelRailButton:hover{{background:#293b4b;border-color:#496479}}
QToolButton#panelRailButton:checked{{background:#183b4b;border:1px solid #4595ac;border-left:3px solid #65d0df}}
QToolButton#railChevron{{background:transparent;border:0;border-bottom:1px solid #35404b;border-radius:0;color:#bccbd7;font-size:{s(18)}px;padding:0;min-height:0}}
QWidget#panelGroupHeader{{background:#1d2530;border:0;border-bottom:1px solid #3c4756}}
QLabel#panelGroupTitle{{background:transparent;color:#cfdeea;font-size:{s(12)}px;font-weight:600;border:0}}
QToolButton#panelHeaderControl{{background:transparent;border:0;color:#aebdca;font-size:{s(17)}px;padding:0;min-height:0}}
QToolButton#panelHeaderControl:hover{{background:#344858}}
QTabBar#iconRailDockTabs{{max-height:0;min-height:0;border:0;padding:0;margin:0}}
QTabBar#iconRailDockTabs::tab{{height:0;min-height:0;max-height:0;padding:0;margin:0;border:0}}
'''


class DockHeader(QWidget):
    def __init__(self,manager,dock):
        super().__init__(dock);self.manager=manager;self.dock=dock;self.setObjectName('panelGroupHeader')
        row=QHBoxLayout(self);row.setContentsMargins(9,0,4,0);row.setSpacing(3)
        self.label=QLabel(dock.windowTitle().upper());self.label.setObjectName('panelGroupTitle')
        self.label.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
        self.label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        row.addWidget(self.label,1)
        self.collapse=QToolButton();self.collapse.setObjectName('panelHeaderControl');self.collapse.setToolTip('Collapse to panel icons')
        self.collapse.clicked.connect(lambda:manager.collapse_dock(dock));row.addWidget(self.collapse)
        self.close=QToolButton();self.close.setObjectName('panelHeaderControl');self.close.setText('×');self.close.setToolTip('Close panel');self.close.clicked.connect(dock.close);row.addWidget(self.close)
        dock.windowTitleChanged.connect(lambda title:self.label.setText(title.upper()))
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
        menu.addSeparator();menu.addAction('Collapse to panel icons',lambda:self.manager.collapse_dock(self.dock))
        if self.dock.features() & QDockWidget.DockWidgetFeature.DockWidgetClosable:menu.addAction('Close panel',self.dock.close)
        if self.dock.contextMenuPolicy()==Qt.ContextMenuPolicy.CustomContextMenu:
            menu.addAction('Panel sizing and options…',lambda:self.dock.customContextMenuRequested.emit(self.dock.mapFromGlobal(self.mapToGlobal(point))))
        menu.exec(self.mapToGlobal(point))


class IconRail(QDockWidget):
    def __init__(self,manager,side):
        super().__init__(manager.owner);self.setObjectName('panelIconRailDock'+side)
        self.side=side;self.setAllowedAreas(LEFT|RIGHT);self.setFeatures(QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)
        header=QWidget();header.setFixedHeight(0);self.setTitleBarWidget(header)
        self.toggleViewAction().setVisible(False)
        self.rail=QFrame();self.rail.setObjectName('panelIconRail')
        self.icons=QVBoxLayout(self.rail);self.icons.setContentsMargins(3,0,3,0);self.icons.setSpacing(3)
        self.chevron=QToolButton();self.chevron.setObjectName('railChevron');self.chevron.setText('»' if side=='left' else '«');self.chevron.setToolTip('Expand panels')
        self.chevron.clicked.connect(lambda:manager.expand(side));self.icons.addWidget(self.chevron);self.icons.addStretch();self.setWidget(self.rail)


class PanelRails(QObject):
    def __init__(self,owner,entries):
        super().__init__(owner);self.owner=owner;self.entries=entries;self.headers={};self.buttons={};self.expanded={"left":False,"right":False}
        self._refresh=QTimer(self);self._refresh.setSingleShot(True);self._refresh.timeout.connect(self.refresh)
        self.last={side:str(owner.settings.value('panels/'+side+'/active','')) for side in ('left','right')}
        self.widths={side:owner.settings.value('panels/'+side+'/width',330 if side=='left' else 390,int) for side in ('left','right')}
        self.rails={side:IconRail(self,side) for side in ('left','right')}
        migrated=owner.settings.value('panels/icon_rails_v1',False,bool)
        opened={d: not d.isHidden() for _,_,d,_ in entries}
        for name,icon,dock,side in entries:
            header=DockHeader(self,dock);header.label.setText(name.upper());dock.setTitleBarWidget(header);self.headers[dock]=header
            button=QToolButton();button.setObjectName('panelRailButton');button.setIcon(icon);button.setCheckable(True);button.setToolTip(name);button.setAccessibleName(name)
            button.clicked.connect(lambda checked=False,d=dock:self.activate(d));self.buttons[dock]=button
            dock.visibilityChanged.connect(self.schedule);dock.dockLocationChanged.connect(self.schedule);dock.topLevelChanged.connect(self.schedule);dock.toggleViewAction().changed.connect(self.schedule)
        for side,rail in self.rails.items():
            if not (migrated and owner.restoreDockWidget(rail)):
                owner.addDockWidget(LEFT if side=='left' else RIGHT,rail)
        if not migrated:
            saved=owner.settings.value('window/state')
            if saved is not None:owner.settings.setValue('window/state_before_icon_rails',saved)
            # Retain existing floating and top/bottom docks during migration.
            for side in ('left','right'):
                area=LEFT if side=='left' else RIGHT
                group=[d for _,_,d,default in entries if default==side and not d.isFloating() and owner.dockWidgetArea(d) in (LEFT,RIGHT)]
                if not group:continue
                # Split rails before tabifying content: Qt otherwise adds the
                # rail as another tab instead of placing it outside the group.
                for dock in group:owner.removeDockWidget(dock)
                anchor=group[0];owner.addDockWidget(area,anchor)
                if side=='left':owner.splitDockWidget(self.rails[side],anchor,Qt.Orientation.Horizontal)
                else:owner.splitDockWidget(anchor,self.rails[side],Qt.Orientation.Horizontal)
                for dock in group[1:]:owner.tabifyDockWidget(anchor,dock)
                for dock in group:dock.setVisible(opened[dock])
                selected=next((d for d in group if opened[d]),None)
                if selected:selected.raise_();self.last[side]=selected.objectName()
            owner.settings.setValue('panels/icon_rails_v1',True)
        owner.tabifiedDockWidgetActivated.connect(self.activated)
        self.update_metrics();self.schedule()

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

    def activate(self,dock):
        if not dock.toggleViewAction().isVisible():return
        side=self.side_for(dock)
        if dock.isFloating():dock.show();dock.raise_();dock.activateWindow();return
        if self.owner.dockWidgetArea(dock) not in (LEFT,RIGHT):
            dock.setVisible(dock.isHidden())
            if not dock.isHidden():dock.raise_()
            self.schedule();return
        if not dock.isHidden() and self.last[side]==dock.objectName() and not dock.visibleRegion().isEmpty():self.collapse(side);return
        self.last[side]=dock.objectName()
        if dock is self.owner.minimax_panel.reference_dock:self.owner.set_reference_panel_open(True)
        else:dock.show()
        dock.raise_()
        if self.owner.dockWidgetArea(dock) in (LEFT,RIGHT):self.owner.resizeDocks([dock],[self.widths[side]],Qt.Orientation.Horizontal)
        self.schedule()

    def expand(self,side):
        available=[d for _,_,d,_ in self.entries if self.side_for(d)==side and d.toggleViewAction().isVisible()]
        target=next((d for d in available if d.objectName()==self.last[side]),available[0] if available else None)
        if target:self.activate(target)

    def collapse_dock(self,dock):
        if dock.isFloating() or self.owner.dockWidgetArea(dock) not in (LEFT,RIGHT):
            dock.hide();self.save();self.schedule()
        else:self.collapse(self.side_for(dock))

    def collapse(self,side):
        group=self.group(side)
        active=next((d for d in group if not d.isHidden() and not d.visibleRegion().isEmpty()),None)
        if active:self.widths[side]=active.width();self.last[side]=active.objectName()
        for dock in group:
            if dock is self.owner.minimax_panel.reference_dock and not dock.isHidden():self.owner.set_reference_panel_open(False)
            else:dock.hide()
        self.save();self.schedule()

    def save(self):
        for side in ('left','right'):
            active=next((d for d in self.group(side) if not d.isHidden() and not d.visibleRegion().isEmpty()),None)
            if active and active.width()>=200:self.widths[side]=active.width();self.last[side]=active.objectName()
            self.owner.settings.setValue('panels/'+side+'/width',self.widths[side]);self.owner.settings.setValue('panels/'+side+'/active',self.last[side])
        self.owner.save_window_panel_state()

    def refresh(self):
        for side,rail in self.rails.items():
            group=self.group(side)
            visible=[d for d in group if not d.isHidden()]
            actual=next((d for d in visible if not d.visibleRegion().isEmpty()),None)
            if actual:
                self.last[side]=actual.objectName()
                if not self.expanded[side] or actual.width()<200:
                    self.owner.resizeDocks([actual],[self.widths[side]],Qt.Orientation.Horizontal)
            self.expanded[side]=bool(visible)
            rail.chevron.setVisible(not visible)
            ordered=[d for _,_,d,_ in self.entries if self.side_for(d)==side]
            for index,dock in enumerate(ordered):
                button=self.buttons[dock]
                if button.parentWidget() is not rail.rail:rail.icons.insertWidget(rail.icons.count()-1,button)
                button.setVisible(dock.toggleViewAction().isVisible())
                button.setChecked(bool(actual is dock or (not dock.isFloating() and self.owner.dockWidgetArea(dock) not in (LEFT,RIGHT) and not dock.isHidden())))
                header=self.headers[dock];header.collapse.setText('«' if side=='left' else '»')
                header.collapse.setVisible(not dock.isFloating())
                header.close.setVisible(bool(dock.features() & QDockWidget.DockWidgetFeature.DockWidgetClosable))
            rail.show()
        # Qt tab membership remains native for layout/drag support. Its tab row is
        # replaced visually by the rails, including newly tabified panels.
        names={d.windowTitle().replace('&','') for _,_,d,_ in self.entries}
        for bar in self.owner.findChildren(QTabBar):
            labels={bar.tabText(i).replace('&','') for i in range(bar.count())}
            if labels and labels<=names:
                bar.setObjectName('iconRailDockTabs');bar.setFixedHeight(0);bar.hide()

    def update_metrics(self,scale=1):
        s=lambda n:max(1,round(n*scale))
        for rail in self.rails.values():rail.setFixedWidth(s(38));rail.chevron.setFixedSize(s(32),s(26))
        for button in self.buttons.values():button.setFixedSize(s(32),s(36));button.setIconSize(QSize(s(20),s(20)))
        for header in self.headers.values():
            header.setFixedHeight(s(28))
            for button in (header.collapse,header.close):button.setFixedSize(s(24),s(22))
