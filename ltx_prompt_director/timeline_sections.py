"""Adjacent section resizing inside one top-aligned Timeline frame."""
from PySide6.QtCore import QObject, QEvent, QSize, QPoint, Qt, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget, QSplitter, QSplitterHandle, QSizePolicy, QLayout


class SectionHandle(QSplitterHandle):
    def __init__(self, orientation, parent):
        super().__init__(orientation,parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.SizeVerCursor)
        self.setToolTip('Drag to trade height between adjacent sections')
    def paintEvent(self,event):
        painter=QPainter(self);painter.fillRect(self.rect(),QColor('#292929'))
        painter.setPen(Qt.PenStyle.NoPen);painter.setBrush(QColor('#849ca8'))
        center=self.width()//2
        for x in range(center-15,center+16,6):painter.drawEllipse(x-1,self.height()//2-1,3,3)
    def keyPressEvent(self,event):
        if event.key() in (Qt.Key.Key_Up,Qt.Key.Key_Down):
            splitter=self.splitter();index=next(i for i in range(1,splitter.count()) if splitter.handle(i) is self)
            splitter.moveSplitter(self.y()+(-8 if event.key()==Qt.Key.Key_Up else 8),index)
            event.accept();return
        super().keyPressEvent(event)


class SectionSplitter(QSplitter):
    def __init__(self,parent=None):
        super().__init__(Qt.Orientation.Vertical,parent)
        self.preferred_height=500
        self.setHandleWidth(8);self.setChildrenCollapsible(False)
    def createHandle(self):return SectionHandle(self.orientation(),self)
    def sizeHint(self):
        hint=super().sizeHint()
        return QSize(hint.width(),max(self.minimumSizeHint().height(),self.preferred_height))


class TimelineSections(QObject):
    SETTINGS='timeline_section_heights_v1'
    def __init__(self,owner,outer,workspace,stack,layout):
        super().__init__(owner)
        self.owner=owner;self.outer=outer;self.frame=workspace;self.layout=layout
        self.widgets={'visual':owner.timeline_shell,'dialogue':owner.studio.dialogue,'audio':owner.audio_panel,'conditioning':owner.conditioning_guide}
        stored=owner.settings.value(self.SETTINGS,{})
        self.heights=dict(stored) if isinstance(stored,dict) else {}
        chrome=max(50,owner.timeline_shell.minimumSizeHint().height()-owner.timeline_height)
        self.defaults={'visual':owner.timeline_height+chrome,'dialogue':104,'audio':max(124,owner.audio_panel.handle.last_height+32),'conditioning':owner.conditioning_height or 144}
        self.brackets_layout=next((item.layout() for index in range(owner.timeline_shell.layout().count()) if (item:=owner.timeline_shell.layout().itemAt(index)).layout() is not None and item.layout().indexOf(owner.extension_brackets)>=0),None)
        self.syncing=False;self.signature=None
        self.parking=QWidget(owner);self.parking.hide()
        # Keep old settings callbacks compatible without leaving orphan grips.
        owner.timeline_shell.layout().removeWidget(owner.timeline_height_handle)
        outer.removeWidget(owner.conditioning_height_handle)
        for handle in (owner.timeline_height_handle,owner.conditioning_height_handle):handle.setParent(self.parking)
        self.splitter=SectionSplitter(stack);self.splitter.setObjectName('timelineSectionSplitter')
        self.splitter.setStyleSheet('QSplitter#timelineSectionSplitter{border:0;background:#141d21;}')
        for name,widget in self.widgets.items():
            layout.removeWidget(widget);outer.removeWidget(widget)
            widget.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Expanding)
            self.splitter.addWidget(widget)
        layout.addWidget(self.splitter)
        # Footer lives in the left column of the same frame; rail spans it all.
        for widget in (owner.sequence_options_scroll,owner.timeline_cache_coverage,owner.timeline_bottom_scroll):
            outer.removeWidget(widget);layout.addWidget(widget)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        workspace.setObjectName('timelineSharedPanel')
        workspace.setStyleSheet('#timelineSharedPanel{background:#292929;border:0 solid #42525b;border-radius:0;padding:0;}')
        workspace.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Expanding)
        outer.setAlignment(Qt.AlignmentFlag.AlignTop)
        workspace.layout().setAlignment(Qt.AlignmentFlag.AlignTop)
        workspace.layout().setAlignment(owner.add_tile_wrap,Qt.AlignmentFlag.AlignTop)
        owner.add_tile_wrap.setMinimumHeight(0);owner.add_tile_wrap.setMaximumHeight(16777215)
        owner.timeline.setMinimumHeight(122);owner.timeline.setMaximumHeight(16777215)
        owner.timeline.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Expanding)
        dialogue=owner.studio.dialogue
        dialogue.scroll.setMinimumHeight(72);dialogue.scroll.setMaximumHeight(16777215)
        dialogue.scroll.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Expanding)
        owner.conditioning_guide.setMinimumHeight(96);owner.conditioning_guide.setMaximumHeight(16777215)
        for widget in self.widgets.values():widget.installEventFilter(self)
        owner.timeline.installEventFilter(self)
        dialogue.scroll.viewport().installEventFilter(self)
        self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.setInterval(0);self.timer.timeout.connect(self.adapt)
        self.splitter.splitterMoved.connect(self.moved)
        QTimer.singleShot(0,self.sync_workspace)

    def visible_names(self):
        return [name for name,widget in self.widgets.items() if self.splitter.indexOf(widget)>=0 and not widget.isHidden()]

    def sync_workspace(self):
        if self.syncing:return
        self.syncing=True
        try:
            o=self.owner;sequence=o.workspace_engine=='shot_sequence'
            audio=o.audio_panel
            if sequence:
                if self.splitter.indexOf(audio)>=0:self.remember()
                o.shot_sequence_panel.audio_layout.addWidget(audio)
                audio.handle.show();audio.set_body_height(audio.handle.current_height or audio.handle.last_height)
            else:
                if self.splitter.indexOf(audio)<0:self.splitter.insertWidget(2,audio)
                audio.handle.hide()
                audio.body.setMinimumHeight(90);audio.body.setMaximumHeight(16777215)
                audio.body.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Expanding)
            audio.setVisible(o.workspace_engine!='minimax_frames')
            self.frame.setVisible(not sequence and not o.prompt_only_workspace)
            # Shot Sequence keeps the same bottom scrollbar outside this frame.
            for widget in (o.timeline_cache_coverage,o.timeline_bottom_scroll):
                if sequence:self.layout.removeWidget(widget);self.outer.addWidget(widget)
                else:self.outer.removeWidget(widget);self.layout.addWidget(widget)
                widget.show()
            names=self.visible_names()
            signature=(sequence,tuple(names))
            if signature!=self.signature:
                self.signature=signature
                defaults=self.defaults
                sizes=[]
                for name,widget in self.widgets.items():
                    if self.splitter.indexOf(widget)<0:continue
                    sizes.append(max(widget.minimumSizeHint().height(),int(self.heights.get(name,defaults[name]))) if name in names else 0)
                self.splitter.preferred_height=sum(sizes)+max(0,len(names)-1)*8
                self.splitter.setSizes(sizes);self.splitter.updateGeometry()
            self.frame.updateGeometry();o.editor_root.updateGeometry()
            self.timer.start()
        finally:self.syncing=False

    def eventFilter(self,widget,event):
        if event.type()==QEvent.Type.Resize:self.timer.start()
        elif event.type() in (QEvent.Type.Show,QEvent.Type.Hide) and widget in self.widgets.values():
            if not self.syncing:QTimer.singleShot(0,self.sync_workspace)
        return False

    def adapt(self):
        o=self.owner
        if self.frame.isHidden():return
        origin=o.timeline.viewport().mapTo(o.editor_root,QPoint(0,0)).x()
        ruler_origin=o.ruler_spacer.mapTo(o.editor_root,QPoint(0,0)).x()
        o.ruler_spacer.setFixedWidth(max(0,origin-ruler_origin))
        if self.brackets_layout is not None:self.brackets_layout.setContentsMargins(o.main_track_label.width(),0,0,0)
        o.timeline_height=o.timeline.height()
        o.conditioning_height=0 if o.conditioning_guide.isHidden() else o.conditioning_guide.height()
        dialogue=o.studio.dialogue
        dialogue.content.resize(max(dialogue.scroll.viewport().width(),sum(o.timeline_item_widths())),max(70,dialogue.scroll.viewport().height()-2))
        if not o.audio_panel.isHidden():
            o.audio_panel.body.setMinimumHeight(90);o.audio_panel.body.setMaximumHeight(16777215)
        o.update_timeline_layout()

    def remember(self):
        for name in self.visible_names():self.heights[name]=self.widgets[name].height()

    def moved(self,*args):
        self.remember();self.owner.settings.setValue(self.SETTINGS,self.heights)
        self.adapt()

    def resize_section(self,name,height):
        widget=self.widgets[name]
        index=self.splitter.indexOf(widget)
        if index<0:return
        sizes=self.splitter.sizes();wanted=max(widget.minimumSizeHint().height(),int(height))
        neighbors=[i for i in range(self.splitter.count()) if i!=index and not self.splitter.widget(i).isHidden()]
        if not neighbors:return
        neighbor=min(neighbors,key=lambda i:abs(i-index))
        delta=wanted-sizes[index]
        delta=min(delta,max(0,sizes[neighbor]-self.splitter.widget(neighbor).minimumSizeHint().height())) if delta>0 else delta
        sizes[index]+=delta;sizes[neighbor]-=delta
        self.splitter.setSizes(sizes);self.moved()
