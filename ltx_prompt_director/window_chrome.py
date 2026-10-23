"""Integrated application header using desktop icons and native Qt window actions."""
from pathlib import Path
import os
import re

from PySide6.QtCore import QEvent, QObject, QSize, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QMenu,
    QSizePolicy, QStyle, QToolBar, QToolButton, QVBoxLayout, QWidget)


class DocumentDragArea(QLabel):
    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.document_title=title
        self.setToolTip(title+' · Drag to move · Double-click to maximize or restore')
        self.setObjectName('chromeDocumentTitle')
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Expanding)
        self.setMinimumWidth(80)

    def set_title(self,title):
        self.document_title=title
        self.setToolTip(title+" · Drag to move · Double-click to maximize or restore")
        self.setText(self.fontMetrics().elidedText(title,Qt.TextElideMode.ElideRight,max(0,self.width()-12)))

    def resizeEvent(self,event):
        super().resizeEvent(event)
        self.setText(self.fontMetrics().elidedText(self.document_title,Qt.TextElideMode.ElideRight,max(0,self.width()-12)))


def desktop_window_icons(style):
    """Use the desktop icon theme, with Qt platform-style standard fallbacks."""
    if os.name=='posix' and not QIcon.themeName():
        paths=QIcon.themeSearchPaths()
        for folder in ('/usr/local/share/icons','/usr/share/icons'):
            if Path(folder).is_dir() and folder not in paths:paths.append(folder)
        QIcon.setThemeSearchPaths(paths)
        if Path('/usr/share/icons/breeze-dark/index.theme').is_file():QIcon.setThemeName('breeze-dark')
    values={
        'minimize':('window-minimize',QStyle.StandardPixmap.SP_TitleBarMinButton),
        'maximize':('window-maximize',QStyle.StandardPixmap.SP_TitleBarMaxButton),
        'restore':('window-restore',QStyle.StandardPixmap.SP_TitleBarNormalButton),
        'close':('window-close',QStyle.StandardPixmap.SP_TitleBarCloseButton),
    }
    return {key:QIcon.fromTheme(name,style.standardIcon(standard)) for key,(name,standard) in values.items()}


class WindowChrome(QObject):
    """Integrated application header with commands on both rows and native window actions."""
    def __init__(self, owner, toolbar, document_title):
        super().__init__(owner)
        self.owner=owner
        self.toolbar=toolbar
        self.scale=1.0
        self.drag_targets=set()
        self.icons=desktop_window_icons(QApplication.style())
        owner.setWindowFlag(Qt.WindowType.FramelessWindowHint,True)
        owner.setWindowFlag(Qt.WindowType.WindowSystemMenuHint,True)
        owner.setWindowFlag(Qt.WindowType.WindowMinimizeButtonHint,True)
        owner.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint,True)
        owner.setWindowFlag(Qt.WindowType.WindowCloseButtonHint,True)
        self.header=QFrame(owner);self.header.setObjectName('customChromeHeader')
        column=QVBoxLayout(self.header);column.setContentsMargins(0,0,0,0);column.setSpacing(0)
        self.command_row=QFrame(self.header);self.command_row.setObjectName('chromeCommandRow')
        self.command_row.setFixedHeight(44)
        row=QHBoxLayout(self.command_row);row.setContentsMargins(0,0,0,0);row.setSpacing(0)
        self.title_balance=QWidget(self.command_row);self.title_balance.setObjectName('chromeCommandGroup')
        left_layout=QHBoxLayout(self.title_balance);left_layout.setContentsMargins(0,0,0,0);left_layout.setSpacing(0)
        self.project_commands=QToolBar(self.title_balance);self.project_commands.setObjectName('chromeProjectCommands')
        self.project_commands.setMovable(False);self.project_commands.setIconSize(toolbar.iconSize())
        left_layout.addWidget(self.project_commands);left_layout.addStretch()
        self.original_actions=tuple(toolbar.actions())
        # Keep app identity and file commands in the upper application row.
        # Panel/workflow/generation controls retain their order on the lower row.
        for action in self.original_actions:
            if (action is getattr(owner,'toolbar_brand_action',None)
                    or action.text().replace('&','') in ('New Project','Open','Save to Library','Export Project')):
                toolbar.removeAction(action);self.project_commands.addAction(action)
        row.addWidget(self.title_balance)
        self.document=DocumentDragArea(document_title,self.command_row)
        row.addWidget(self.document,1)
        self.drag_targets.update((self.document,self.command_row,self.title_balance,self.project_commands))
        column.addWidget(self.command_row)
        owner.removeToolBar(toolbar)
        toolbar.setParent(self.header);toolbar.setMinimumWidth(0)
        toolbar.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
        column.addWidget(toolbar)
        self.drag_targets.add(toolbar)
        for action in toolbar.actions():
            widget=toolbar.widgetForAction(action)
            if (type(widget) is QWidget and not widget.findChildren(QWidget)
                    and widget.sizePolicy().horizontalPolicy()==QSizePolicy.Policy.Expanding):
                widget.setObjectName('chromeTitleDragArea');self.drag_targets.add(widget)
        brand=getattr(owner,'toolbar_brand',None)
        if brand:
            self.drag_targets.add(brand)
            self.drag_targets.update(brand.findChildren(QLabel))
        self.trailing=QWidget(self.command_row);self.trailing.setObjectName('chromeCommandGroup')
        trailing_layout=QHBoxLayout(self.trailing);trailing_layout.setContentsMargins(0,0,0,0);trailing_layout.setSpacing(0)
        trailing_layout.addStretch()
        self.utility_commands=QToolBar(self.trailing);self.utility_commands.setObjectName('chromeUtilityCommands')
        self.utility_commands.setMovable(False);self.utility_commands.setIconSize(toolbar.iconSize())
        for action in self.original_actions:
            widget=toolbar.widgetForAction(action)
            if action.text().replace('&','')=='Settings' or (widget and widget.toolTip()=='Help and setup'):
                toolbar.removeAction(action);self.utility_commands.addAction(action)
        trailing_layout.addWidget(self.utility_commands)
        self.drag_targets.update((self.trailing,self.utility_commands))
        self.controls=QWidget(self.trailing);self.controls.setObjectName('chromeWindowControls')
        buttons=QHBoxLayout(self.controls);buttons.setContentsMargins(0,0,3,0);buttons.setSpacing(0)
        self.minimize=self.make_button('Minimize',self.icons['minimize'],owner.showMinimized)
        self.maximize=self.make_button('Maximize',self.icons['maximize'],self.toggle_maximized)
        self.close=self.make_button('Close',self.icons['close'],owner.close)
        self.close.setObjectName('chromeCloseButton')
        for button in (self.minimize,self.maximize,self.close):buttons.addWidget(button)
        self.controls.setFixedWidth(111);trailing_layout.addWidget(self.controls)
        gutter=max(240,self.project_commands.sizeHint().width(),self.utility_commands.sizeHint().width()+111)
        self.title_balance.setFixedWidth(gutter);self.trailing.setFixedWidth(gutter)
        row.addWidget(self.trailing)
        owner.setMenuWidget(self.header)
        toolbar.show()
        self.header.setMinimumHeight(44+max(42,toolbar.sizeHint().height()))
        owner.installEventFilter(self)
        QApplication.instance().installEventFilter(self)
        for widget in [owner,*owner.findChildren(QWidget)]:widget.setMouseTracking(True)
        owner.windowTitleChanged.connect(lambda *_:self.document.set_title(getattr(owner,'current_project_name','') or 'Untitled project'))
        self.sync_state()
        self.update_metrics()

    def update_metrics(self,scale=None):
        if scale is not None:self.scale=scale
        size=lambda n:max(1,round(n*self.scale))
        self.command_row.setFixedHeight(size(44))
        for button in (self.minimize,self.maximize,self.close):
            button.setFixedSize(size(36),size(38));button.setIconSize(QSize(size(16),size(16)))
        self.controls.setFixedWidth(size(111))
        for bar in (self.project_commands,self.utility_commands):bar.setIconSize(self.toolbar.iconSize())
        self.header.setMinimumHeight(size(44)+max(size(42),self.toolbar.sizeHint().height()))
        desired=max(size(240),self.project_commands.sizeHint().width(),self.utility_commands.sizeHint().width()+size(111))
        gutter=min(desired,max(size(150),(self.owner.width()-size(80))//2))
        for group in (self.title_balance,self.trailing):
            if group.width()!=gutter:group.setFixedWidth(gutter)
        self.document.set_title(getattr(self.owner,'current_project_name',self.document.document_title) or 'Untitled project')

    def make_button(self,label,icon,callback):
        button=QToolButton(self.controls);button.setObjectName('chromeWindowButton')
        button.setAccessibleName(label);button.setToolTip(label);button.setIcon(icon)
        button.setIconSize(QSize(16,16));button.setFixedSize(36,38)
        button.clicked.connect(callback)
        return button

    def toggle_maximized(self):
        if self.owner.isMaximized():self.owner.showNormal()
        else:self.owner.showMaximized()
        self.sync_state()

    def sync_state(self):
        maximized=self.owner.isMaximized()
        self.maximize.setIcon(self.icons['restore' if maximized else 'maximize'])
        label='Restore' if maximized else 'Maximize'
        self.maximize.setToolTip(label);self.maximize.setAccessibleName(label)

    def resize_edges(self,point):
        if self.owner.isMaximized() or self.owner.isFullScreen():return Qt.Edge(0)
        edges=Qt.Edge(0);grip=5
        if point.x()<grip:edges|=Qt.Edge.LeftEdge
        elif point.x()>=self.owner.width()-grip:edges|=Qt.Edge.RightEdge
        if point.y()<grip:edges|=Qt.Edge.TopEdge
        elif point.y()>=self.owner.height()-grip:edges|=Qt.Edge.BottomEdge
        return edges

    def window_menu(self,point):
        menu=QMenu(self.owner)
        restore=menu.addAction(self.icons['restore'],'Restore',self.owner.showNormal)
        restore.setEnabled(self.owner.isMaximized() or self.owner.isMinimized())
        menu.addAction(self.icons['minimize'],'Minimize',self.owner.showMinimized)
        maximize=menu.addAction(self.icons['maximize'],'Maximize',self.owner.showMaximized)
        maximize.setEnabled(not self.owner.isMaximized())
        menu.addSeparator();menu.addAction(self.icons['close'],'Close',self.owner.close)
        menu.exec(point)

    def eventFilter(self,watched,event):
        if watched is self.owner:
            if event.type()==QEvent.Type.WindowStateChange:self.sync_state()
            if event.type()==QEvent.Type.Resize:self.update_metrics()
        if not isinstance(watched,QWidget) or watched.window() is not self.owner:return False
        kind=event.type()
        if kind not in (QEvent.Type.MouseButtonPress,QEvent.Type.MouseButtonDblClick,QEvent.Type.MouseMove):return False
        point=watched.mapTo(self.owner,event.position().toPoint())
        edges=self.resize_edges(point)
        if kind==QEvent.Type.MouseMove:
            diagonal1=edges in (Qt.Edge.TopEdge|Qt.Edge.LeftEdge,Qt.Edge.BottomEdge|Qt.Edge.RightEdge)
            diagonal2=edges in (Qt.Edge.TopEdge|Qt.Edge.RightEdge,Qt.Edge.BottomEdge|Qt.Edge.LeftEdge)
            cursor=(Qt.CursorShape.SizeFDiagCursor if diagonal1 else Qt.CursorShape.SizeBDiagCursor if diagonal2 else
                    Qt.CursorShape.SizeHorCursor if edges & (Qt.Edge.LeftEdge|Qt.Edge.RightEdge) else
                    Qt.CursorShape.SizeVerCursor if edges else Qt.CursorShape.ArrowCursor)
            if edges:self.owner.setCursor(cursor)
            else:self.owner.unsetCursor()
            return False
        if event.button()==Qt.MouseButton.LeftButton:
            handle=self.owner.windowHandle()
            if edges and kind==QEvent.Type.MouseButtonPress and handle:
                return bool(handle.startSystemResize(edges))
            if watched in self.drag_targets:
                if kind==QEvent.Type.MouseButtonDblClick:self.toggle_maximized();return True
                if handle:return bool(handle.startSystemMove())
        elif event.button()==Qt.MouseButton.RightButton and watched in self.drag_targets:
            self.window_menu(event.globalPosition().toPoint());return True
        return False


def chrome_style(scale):
    style='QFrame#customChromeHeader{background:#17222c;border:0;border-bottom:1px solid #293b48}\nQFrame#chromeCommandRow,QWidget#chromeCommandGroup{background:transparent;border:0}\n#customChromeHeader QToolBar{border:0;background:transparent;spacing:2px}\n#customChromeHeader QToolBar QToolButton{background:transparent;border:1px solid transparent;border-radius:4px;padding:5px 9px;color:#c5cdd1}\n#customChromeHeader QToolBar QToolButton:hover{background:#2b3b49;border-color:#3a4f60}\n#customChromeHeader #workspaceBrand{background:transparent;border:0}\nQLabel#chromeDocumentTitle{background:transparent;border:0;color:#d6e7f2;padding:0 12px;font:600 16px "Roboto Condensed"}\nQWidget#chromeTitleDragArea{background:transparent;border:0}\nQWidget#chromeWindowControls{background:transparent;border:0}\nQToolButton#chromeWindowButton,QToolButton#chromeCloseButton{background:transparent;border:0;border-radius:0;padding:0;min-height:0}\nQToolButton#chromeWindowButton:hover{background:#324352}\nQToolButton#chromeWindowButton:pressed{background:#22313f}\nQToolButton#chromeCloseButton:hover{background:#ad3641}\nQToolButton#chromeCloseButton:pressed{background:#842a33}\n'
    return re.sub(r"(\d+)px",lambda match:str(max(1,round(int(match[1])*scale)))+"px",style)
