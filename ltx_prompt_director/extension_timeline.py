"""Timeline brackets for independently rendered video blocks."""
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget


class ExtensionBrackets(QWidget):
    def __init__(self, owner):
        super().__init__()
        self.owner=owner
        self.setFixedHeight(42)
        self.setToolTip('Each bracket is one render block. Select a segment to edit that group using the timeline settings below. Joins blend without adding time.')

    def spans(self):
        result=[]
        for row, segment in enumerate(self.owner.segments):
            item=self.owner.timeline.item(row)
            if item is None:continue
            rect=self.owner.timeline.visualItemRect(item)
            if result and result[-1][0]==segment.render_group:
                result[-1][2]=rect.right()
            else:
                gap=self.owner.extension_chrome()[row]
                result.append([segment.render_group,rect.left()+gap,rect.right()])
        return result

    def paintEvent(self,event):
        painter=QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(),QColor('#171f24'))
        selected=self.owner.current_segment()
        for group,left,right in self.spans():
            if right<0 or left>self.width():continue
            active=selected and selected.render_group==group
            color=QColor('#9bdacb' if active else '#638f9e')
            painter.setPen(QPen(color,2 if active else 1))
            left+=3;right-=3
            painter.drawLine(left,25,left,9);painter.drawLine(left,9,right,9);painter.drawLine(right,9,right,25)
            length=self.owner.render_group_duration(group)
            title=('Block 1' if group==0 else f'Extension {group}')+f' · {length:g} s'
            visible_left=max(0,left);visible_right=min(self.width(),right)
            label=painter.fontMetrics().elidedText(title,Qt.TextElideMode.ElideRight,max(0,visible_right-visible_left-12))
            width=painter.fontMetrics().horizontalAdvance(label)+10
            rect=QRectF((visible_left+visible_right-width)/2,0,width,19)
            painter.fillRect(rect,QColor('#171f24'));painter.drawText(rect,Qt.AlignmentFlag.AlignCenter,label)
            if group:
                painter.setPen(QColor('#a8bfc7'))
                setting=self.owner.extension_transition(group)
                label='Hard cut' if setting['blend']=='cut' else f'↔ {setting["overlap"]:g} s blend included'
                painter.drawText(QRectF(visible_left+5,22,max(0,visible_right-visible_left-10),18),Qt.AlignmentFlag.AlignLeft,label)


class BlendEdgeHandle(QWidget):
    def __init__(self,control,side):
        super().__init__();self.control=control;self.side=side;self.anchor=None
        self.setFixedWidth(8);self.setCursor(Qt.CursorShape.SizeHorCursor)
        self.setToolTip('Drag to adjust blend length')

    def paintEvent(self,event):
        painter=QPainter(self);painter.setPen(QPen(QColor('#79a4a9'),1))
        middle=self.height()//2
        for offset in (-9,-3,3,9):painter.drawLine(2,middle+offset,5,middle+offset)

    def mousePressEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton:
            self.anchor=event.globalPosition().x();self.original=self.control.length.value();self.grabMouse();event.accept()

    def mouseMoveEvent(self,event):
        if self.anchor is None:return
        delta=2*(event.globalPosition().x()-self.anchor)/max(.001,self.control.owner.pixels_per_second)
        self.control.length.setValue(max(0,min(15,self.original+delta*self.side)))
        event.accept()

    def mouseReleaseEvent(self,event):
        if self.anchor is not None:self.releaseMouse();self.anchor=None;event.accept()


class BlendControl(QWidget):
    """A single mix region, resized directly by its two timeline edges."""
    def __init__(self,owner,group):
        super().__init__()
        from PySide6.QtWidgets import QVBoxLayout,QHBoxLayout,QLabel,QDoubleSpinBox,QComboBox
        from .shot_sequence import MODES
        self.owner=owner;self.group=group
        self.setObjectName('extensionBlendBox');self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground,True)
        self.setStyleSheet('#extensionBlendBox{background:#14262d;border-top:1px solid #63868e;border-bottom:1px solid #63868e;border-left:0;border-right:0;} QLabel{background:transparent;border:0;color:#b9dfd7}')
        row=QHBoxLayout(self);row.setContentsMargins(1,2,1,2);row.setSpacing(3)
        self.left_handle=BlendEdgeHandle(self,-1);row.addWidget(self.left_handle)
        setting=owner.extension_transition(group);self._last_blend_length=setting['overlap'] or 1
        # Internal bounded value; the only visible length controls are the edges.
        self.length=QDoubleSpinBox(self);groups=list(owner.render_groups());index=groups.index(group)
        maximum=min(owner.render_group_duration(group),owner.render_group_duration(groups[index-1]))
        self.length.setRange(0,maximum);self.length.setDecimals(4)
        self.length.setValue(0 if setting['blend']=='cut' else setting['overlap']);self.length.hide()
        middle=QVBoxLayout();middle.setContentsMargins(0,0,0,0);middle.setSpacing(2)
        self.title=QLabel(self);self.title.hide()
        self.diagram=BlendDiagram(owner,group);middle.addWidget(self.diagram,1)
        self.mode=QComboBox();self.mode.setMinimumWidth(0);self.mode.setMinimumContentsLength(1)
        self.mode.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon);self.mode.setAccessibleName('Blend mode')
        for mode in MODES:self.mode.addItem(blend_type_icon(mode),'Hard cut' if mode=='cut' else mode.replace('_',' ').title(),mode)
        self.mode.setCurrentIndex(self.mode.findData(setting['blend']));self.mode.setFixedHeight(22);self.mode.setStyleSheet('QComboBox{padding:1px 20px 1px 3px;min-height:0;}');middle.addWidget(self.mode)
        self.side=QComboBox();self.side.addItem('Source side','source');self.side.addItem('Destination side','destination')
        self.side.setCurrentIndex(self.side.findData(setting['side']));self.side.setAccessibleName('Blend overlap side');self.side.setFixedHeight(22);self.side.setStyleSheet('QComboBox{padding:1px 20px 1px 3px;min-height:0;}');middle.addWidget(self.side)
        row.addLayout(middle,1)
        self.right_handle=BlendEdgeHandle(self,1);row.addWidget(self.right_handle)
        def title():
            index=list(owner.render_groups()).index(group)
            self.title.setText(f'{index} → {index+1} · {self.length.value():g} s')
        def update():
            cut=self.mode.currentData()=='cut'
            self.mode.setToolTip(self.mode.currentText())
            if cut:
                if self.length.value():self._last_blend_length=self.length.value()
                self.length.blockSignals(True);self.length.setValue(0);self.length.blockSignals(False)
            else:
                if self.length.value()==0:
                    self.length.blockSignals(True);self.length.setValue(self._last_blend_length);self.length.blockSignals(False)
                self._last_blend_length=self.length.value()
            self.length.setEnabled(not cut);self.left_handle.setEnabled(not cut);self.right_handle.setEnabled(not cut)
            owner.set_extension_transition(group,self._last_blend_length if cut else self.length.value(),self.mode.currentData())
            title();self.diagram.update()
        self.length.valueChanged.connect(update);self.mode.currentIndexChanged.connect(update)
        def side_changed():
            owner.set_extension_side(group,self.side.currentData());self.diagram.update()
        self.side.currentIndexChanged.connect(side_changed)
        title()
        for control in (self.length,self.left_handle,self.right_handle):control.setEnabled(setting['blend']!='cut')


def blend_type_icon(mode):
    from PySide6.QtGui import QIcon,QPixmap
    image=QPixmap(28,22);image.fill(Qt.GlobalColor.transparent)
    painter=QPainter(image)
    draw_blend_curves(painter,QRectF(2,2,24,18),mode)
    painter.end()
    return QIcon(image)


def draw_blend_curves(painter,rect,mode):
    from PySide6.QtGui import QPainterPath
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    for incoming,color in [(False,'#f0b276'),(True,'#85ddd1')]:
        path=QPainterPath()
        for step in range(41):
            alpha=step/40
            if mode=='cut':value=0 if alpha<.5 else 1
            elif mode=='filmic_crossfade':value=alpha**(1/2.2)
            elif mode=='perceptual_crossfade':value=alpha*alpha*(3-2*alpha)
            else:value=alpha
            if not incoming:value=1-value
            x=rect.left()+alpha*rect.width();y=rect.bottom()-value*rect.height()
            if step:path.lineTo(x,y)
            else:path.moveTo(x,y)
        painter.setPen(QPen(QColor(color),2));painter.drawPath(path)


class BlendDiagram(QWidget):
    def __init__(self,owner,group):
        super().__init__();self.owner=owner;self.group=group
        self.setObjectName("extensionBlendDiagram")
        self.setMinimumHeight(38);self.setMaximumHeight(90)
        self.setToolTip('Outgoing (gold) and incoming (mint) blend. Image segments show edge frames when available; text sequences show the curve. Curves illustrate the selected blend type.')

    def edge_paths(self):
        groups=list(self.owner.render_groups().items())
        index=next((i for i,(group,_) in enumerate(groups) if group==self.group),0)
        if not index:return []
        before=groups[index-1][1][-1];after=groups[index][1][0]
        if before.kind not in {'image','video'} or after.kind not in {'image','video'}:return []
        cache=self.owner._video_block_frames.get(self.owner.video_preview_key(),{})
        left=cache.get(str(before.render_group),{}).get('last_frame') or before.preview_path
        right=cache.get(str(after.render_group),{}).get('first_frame') or after.preview_path
        return [left,right]

    def paintEvent(self,event):
        from PySide6.QtGui import QPixmap
        painter=QPainter(self);painter.fillRect(self.rect(),QColor('#0c1a20'))
        for index,path in enumerate(self.edge_paths()):
            image=QPixmap(path)
            if image.isNull():continue
            area=QRectF(index*self.width()/2,0,self.width()/2,self.height())
            painter.drawPixmap(area.toRect(),image.scaled(area.size().toSize(),Qt.AspectRatioMode.KeepAspectRatioByExpanding,Qt.TransformationMode.SmoothTransformation))
            painter.fillRect(area,QColor(0,0,0,90))
        mode=self.owner.extension_transition(self.group)['blend']
        draw_blend_curves(painter,QRectF(5,5,max(1,self.width()-10),max(1,self.height()-10)),mode)
        length=0 if mode=='cut' else self.owner.extension_transition(self.group)['overlap']
        text=f'{length:g} s'
        width=painter.fontMetrics().horizontalAdvance(text)+8
        label=QRectF(self.width()-width-2,1,width,painter.fontMetrics().height()+2)
        painter.fillRect(label,QColor(0,0,0,175));painter.setPen(QColor('#d3e6e8'));painter.drawText(label,Qt.AlignmentFlag.AlignCenter,text)
