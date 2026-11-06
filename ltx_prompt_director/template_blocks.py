"""Native code-kit template canvas: pill images, nested sockets and movable groups."""
from __future__ import annotations
import json
import re
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import Qt, QSize, QPoint, QRectF, QMimeData, QUrl, Signal, QTimer, QSignalBlocker
from PySide6.QtGui import (QColor, QPainter, QPainterPath, QPen, QLinearGradient, QFont,
    QFontMetrics, QPixmap, QDrag, QTextCursor, QTextDocument, QTextImageFormat, QTextCharFormat, QTextFormat, QKeySequence)
from PySide6.QtWidgets import (QWidget, QFrame, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QTextEdit, QScrollArea, QAbstractButton, QPushButton, QMenu, QInputDialog, QApplication, QSizePolicy)
from .output_templates import parse_template, serialize_template, names, META

MIME = 'application/x-director-template-block'
TOKEN_PROPERTY = int(QTextFormat.Property.UserProperty) + 319
COLORS = dict(section='#ad89e3', segments='#dba341', subjects='#6599e5', field='#28b6b1', timing='#ad89e3', text='#879bb3')
FIELDS = {
    'global_prompt': ('Global direction', 'global_prompt', 'field'), 'segment_prompt': ('Segment prompt', 'prompt', 'field'),
    'subject_prompt': ('Subject details', 'prompt', 'subjects'), 'sound': ('Sound', 'sound', 'field'),
    'music': ('Music', 'music', 'field'), 'custom': ('Named field', 'custom_part', 'field'),
    'index': ('Number', 'index', 'segments'), 'name': ('Name', 'name', 'subjects'),
    'start': ('Start time', 'start', 'timing'), 'end': ('End time', 'end', 'timing'),
    'duration': ('Duration', 'duration', 'timing'), 'role': ('Frame role', 'role', 'timing'),
}


def mix(base, color, strength):
    a, b = QColor(base), QColor(color)
    return QColor(*[round(a.getRgb()[i] * (1-strength) + b.getRgb()[i] * strength) for i in range(3)])


def paint_symbol(painter, key, rect, color):
    painter.save(); painter.translate(rect.left(), rect.top()); painter.scale(rect.width()/18, rect.height()/18)
    painter.setPen(QPen(QColor(color), 1.3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    if key in ('start', 'end', 'duration', 'role'):
        painter.drawEllipse(QRectF(3, 4, 12, 12)); painter.drawLine(7, 1, 11, 1); painter.drawLine(9, 7, 9, 10); painter.drawLine(9, 10, 12, 10)
        if key == 'end': painter.drawLine(3, 3, 15, 15)
    elif key in ('subjects', 'subject_prompt', 'name'):
        painter.drawEllipse(QRectF(6, 1, 6, 6)); path=QPainterPath();path.moveTo(3,17);path.cubicTo(3,6,15,6,15,17);painter.drawPath(path)
    elif key == 'music':
        painter.drawLine(7, 3, 15, 1); painter.drawLine(7, 3, 7, 14); painter.drawLine(15, 1, 15, 12); painter.drawEllipse(QRectF(2, 12, 5, 4)); painter.drawEllipse(QRectF(10, 10, 5, 4))
    elif key == 'sound':
        for x, y in [(2,7),(5,3),(8,5),(11,1),(14,5),(17,7)]: painter.drawLine(x,y,x,18-y)
    elif key == 'section':
        painter.drawRoundedRect(QRectF(2, 2, 14, 14), 2, 2); painter.drawLine(2,6,16,6)
    elif key == 'segments':
        path=QPainterPath();path.moveTo(3,6);path.lineTo(3,3);path.lineTo(14,3);path.lineTo(14,8);path.moveTo(11,5);path.lineTo(14,8);path.lineTo(17,5);path.moveTo(15,12);path.lineTo(15,15);path.lineTo(4,15);path.lineTo(4,10);path.moveTo(1,13);path.lineTo(4,10);path.lineTo(7,13);painter.drawPath(path)
    elif key == 'global_prompt':
        painter.drawRoundedRect(QRectF(2, 5, 14, 11), 2, 2); painter.drawLine(2,9,16,9); painter.drawLine(4,2,2,5); painter.drawLine(9,1,7,5); painter.drawLine(15,1,12,5); painter.drawLine(3,3,16,1)
    elif key == 'index':
        painter.drawLine(7,2,5,16);painter.drawLine(13,2,11,16);painter.drawLine(2,7,16,7);painter.drawLine(1,12,15,12)
    else:
        painter.drawRoundedRect(QRectF(2,3,14,12), 2, 2)
        for y in (7,11): painter.drawLine(5,y,13,y)
    painter.restore()


def pill_image(label, key, color_key='field', width=None, height=30, grip=False):
    font=QFont('Roboto Condensed');font.setPixelSize(14)
    width=width or QFontMetrics(font).horizontalAdvance(label)+46+(13 if grip else 0)
    image=QPixmap(width*2,height*2);image.setDevicePixelRatio(2);image.fill(Qt.GlobalColor.transparent)
    painter=QPainter(image);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    color=COLORS.get(color_key, COLORS['field']);gradient=QLinearGradient(0,0,0,height)
    gradient.setColorAt(0,mix('#222b37',color,.24));gradient.setColorAt(1,mix('#222b37',color,.12))
    painter.setBrush(gradient);painter.setPen(QPen(mix('#3b4656',color,.65),1));painter.drawRoundedRect(QRectF(.5,.5,width-1,height-1),height/2,height/2)
    x=10
    if grip:
        painter.setPen(Qt.PenStyle.NoPen);painter.setBrush(QColor('#8e9cae'))
        for xx in (x,x+4):
            for yy in (height/2-4,height/2,height/2+4):painter.drawEllipse(QRectF(xx,yy,1.5,1.5))
        x+=14
    paint_symbol(painter,key,QRectF(x,(height-16)/2,16,16),color);painter.setPen(QColor('#e0e8ef'));painter.setFont(font)
    painter.drawText(QRectF(x+23,0,width-x-29,height),Qt.AlignmentFlag.AlignVCenter,label);painter.end();return image


def payload(mime):
    if not mime.hasFormat(MIME):return None
    try:return json.loads(bytes(mime.data(MIME)))
    except (ValueError,TypeError):return None


class PalettePill(QAbstractButton):
    def __init__(self, canvas, key, label, color_key):
        super().__init__();self.canvas=canvas;self.key=key;self.image=pill_image(label,key,color_key,178,32,True)
        self.setAccessibleName(label);self.setToolTip('Drag '+label+' into a matching socket');self.setFixedSize(180,36);self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.clicked.connect(lambda:canvas.insert_palette(key))
    def paintEvent(self,event):
        painter=QPainter(self);painter.drawPixmap(1,2,self.image)
    def mousePressEvent(self,event):self.press=event.position().toPoint();super().mousePressEvent(event)
    def mouseMoveEvent(self,event):
        if event.buttons() & Qt.MouseButton.LeftButton and (event.position().toPoint()-self.press).manhattanLength()>=QApplication.startDragDistance():
            drag=QDrag(self);mime=QMimeData();mime.setData(MIME,json.dumps({'palette':self.key}).encode());drag.setMimeData(mime);drag.setPixmap(self.image);drag.setHotSpot(QPoint(20,16));drag.exec(Qt.DropAction.CopyAction)
        else:super().mouseMoveEvent(event)


class DragHandle(QLabel):
    def __init__(self, block):
        super().__init__('⠿');self.block=block;self.setCursor(Qt.CursorShape.OpenHandCursor);self.setFixedWidth(15);self.setToolTip('Drag this block and its contents')
    def mousePressEvent(self,event):self.press=event.position().toPoint()
    def mouseMoveEvent(self,event):
        if event.buttons() & Qt.MouseButton.LeftButton and (event.position().toPoint()-self.press).manhattanLength()>=QApplication.startDragDistance():self.block.canvas.start_move(self.block)
    def contextMenuEvent(self,event):
        menu=QMenu(self);menu.addAction('Remove block',lambda:self.block.canvas.remove_block(self.block));menu.exec(event.globalPos())


class TemplateText(QTextEdit):
    def __init__(self, block, source):
        super().__init__();self.block=block;self.setObjectName('templateTextSlot');self.setAcceptRichText(False)
        self.setMinimumWidth(0);self.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Fixed)
        self.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth);self.setAcceptDrops(True);self.setPlaceholderText('Type a heading or instruction, or drop a pill here…')
        self.setStyleSheet('QTextEdit#templateTextSlot{font:14px "Roboto Condensed";background:#1b2430;color:#dfe8ef;border:0;border-radius:4px;padding:3px;}')
        self.set_source(source);self.textChanged.connect(self.edited)
        self.document().documentLayout().documentSizeChanged.connect(self.fit_height)
        self.fit_height()
    def add_token(self, name, cursor=None):
        cursor=cursor or self.textCursor();scope=self.block.parent_zone.scope()
        key='segment_prompt' if name=='prompt' and scope=='segments' else 'subject_prompt' if name=='prompt' else name
        label,_,color=FIELDS.get(key,(name.replace('_',' ').title(),name,'field'))
        image=pill_image(label,key,color);url=QUrl('director-chip:'+name)
        self.document().addResource(QTextDocument.ResourceType.ImageResource,url,image.toImage())
        fmt=QTextImageFormat();fmt.setName(url.toString());fmt.setWidth(image.width()/image.devicePixelRatio());fmt.setHeight(image.height()/image.devicePixelRatio())
        fmt.setProperty(TOKEN_PROPERTY,'${'+name+'}');fmt.setProperty(QTextFormat.Property.ImageAltText,label);cursor.insertImage(fmt)
        cursor.setCharFormat(QTextCharFormat())
    def set_source(self, source):
        blocker=QSignalBlocker(self);self.clear();cursor=QTextCursor(self.document());start=0
        for match in re.finditer(r'\$\{([a-zA-Z_][a-zA-Z0-9_]*)\}',source):
            cursor.insertText(source[start:match.start()]);self.add_token(match[1],cursor);start=match.end()
        cursor.insertText(source[start:]);del blocker
    def source(self):
        lines=[];block=self.document().begin()
        while block.isValid():
            pieces=[];it=block.begin()
            while not it.atEnd():
                fragment=it.fragment()
                if fragment.isValid():
                    fmt=fragment.charFormat();token=fmt.property(TOKEN_PROPERTY)
                    # Qt can carry the preceding image's format onto typed text.
                    # Only the image object represents a chip, never its neighbours.
                    pieces.append(fragment.text().replace('\ufffc',str(token)) if token else fragment.text())
                it+=1
            lines.append(''.join(pieces));block=block.next()
        return '\n'.join(lines)
    def edited(self):self.fit_height();self.block.canvas.changed.emit()
    def fit_height(self,*_):
        height=max(47,min(150,round(self.document().size().height())+12))
        if self.height()!=height:self.setFixedHeight(height)
    def resizeEvent(self,event):super().resizeEvent(event);self.fit_height()
    def token_at(self,position):
        probe=self.cursorForPosition(position)
        for direction in (QTextCursor.MoveOperation.NextCharacter,QTextCursor.MoveOperation.PreviousCharacter):
            candidate=QTextCursor(probe);candidate.movePosition(direction,QTextCursor.MoveMode.KeepAnchor)
            if candidate.selectedText()=='\ufffc' and candidate.charFormat().property(TOKEN_PROPERTY):
                return candidate
        return None
    def mousePressEvent(self,event):
        self.block.canvas.active_zone=self.block.parent_zone;self.press=event.position().toPoint();self.drag_token=None
        candidate=self.token_at(self.press)
        if candidate is not None:
            self.drag_token=(candidate.selectionStart(),str(candidate.charFormat().property(TOKEN_PROPERTY))[2:-1])
        super().mousePressEvent(event)
    def mouseMoveEvent(self,event):
        if self.drag_token and event.buttons() & Qt.MouseButton.LeftButton and (event.position().toPoint()-self.press).manhattanLength()>=QApplication.startDragDistance():
            position,name=self.drag_token;drag=QDrag(self);mime=QMimeData();mime.setData(MIME,json.dumps(dict(field=name,origin=self.block.id,position=position)).encode());drag.setMimeData(mime)
            key='segment_prompt' if name=='prompt' and self.block.parent_zone.scope()=='segments' else 'subject_prompt' if name=='prompt' else name
            label,_,color=FIELDS.get(key,(name.replace('_',' ').title(),name,'field'));drag.setPixmap(pill_image(label,key,color));drag.setHotSpot(QPoint(15,15));self.drag_token=None;drag.exec(Qt.DropAction.MoveAction)
        else:super().mouseMoveEvent(event)
    def dragEnterEvent(self,event):self.dragMoveEvent(event)
    def dragMoveEvent(self,event):
        data=payload(event.mimeData());zone=self.block.parent_zone
        if data and zone.can_accept(data):zone.highlight(True);event.acceptProposedAction()
        else:event.ignore()
    def dragLeaveEvent(self,event):self.block.parent_zone.highlight(False);super().dragLeaveEvent(event)
    def dropEvent(self,event):
        data=payload(event.mimeData());zone=self.block.parent_zone;zone.highlight(False)
        if not data or not zone.can_accept(data):event.ignore();return
        key=data.get('palette')
        if key in FIELDS or data.get('field'):
            name=data.get('field') or zone.canvas.field_name(key)
            if name:
                cursor=self.cursorForPosition(event.position().toPoint());position=cursor.position()
                origin=zone.canvas.registry.get(data.get('origin'))
                if origin is self.block and data.get('position',0)<position:position-=1
                zone.canvas.remove_origin_token(data);cursor=QTextCursor(self.document());cursor.setPosition(max(0,min(position,self.document().characterCount()-1)))
                self.add_token(name,cursor);zone.canvas.set_status('Pill snapped into '+zone.label()+'.')
        else:zone.canvas.apply_drop(data,zone,zone.nodes.index(self.block)+1)
        event.acceptProposedAction()
    def insertFromMimeData(self,mime):
        data=payload(mime)
        if data:
            if self.block.parent_zone.can_accept(data):self.block.canvas.apply_drop(data,self.block.parent_zone)
            return
        cursor=self.textCursor();cursor.insertText(mime.text())
    def contextMenuEvent(self,event):
        cursor=self.token_at(event.pos())
        token=cursor.charFormat().property(TOKEN_PROPERTY) if cursor is not None else None
        menu=self.createStandardContextMenu()
        if token and str(token)[2:-1] not in META|{'prompt','global_prompt'}:
            action=menu.addAction('Rename field…')
            def rename():
                old=str(token)[2:-1];name,ok=QInputDialog.getText(self,'Name this field','Field name:',text=old.replace('_',' '))
                if ok:
                    name=self.block.canvas.normalize_field_name(name, excluding=old)
                    if name:cursor.removeSelectedText();self.add_token(name,cursor)
            action.triggered.connect(rename)
        menu.exec(event.globalPos())


class TextBlock(QWidget):
    def __init__(self, canvas, parent_zone, source=''):
        super().__init__();self.canvas=canvas;self.parent_zone=parent_zone;self.id=uuid4().hex;self.kind='text'
        self.setMinimumWidth(0);layout=QHBoxLayout(self);layout.setContentsMargins(0,0,0,0);layout.setSpacing(3)
        self.handle=DragHandle(self);layout.addWidget(self.handle,0,Qt.AlignmentFlag.AlignTop)
        self.editor=TemplateText(self,source);layout.addWidget(self.editor,1);canvas.registry[self.id]=self
    def to_node(self):return dict(type='text',text=self.editor.source())


class ContainerBlock(QFrame):
    def __init__(self,canvas,parent_zone,node):
        super().__init__();self.canvas=canvas;self.parent_zone=parent_zone;self.id=uuid4().hex;self.kind=node['type'];self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Maximum)
        self.setObjectName('templateContainer');self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        layout=QVBoxLayout(self);layout.setContentsMargins(11,0,0,12);layout.setSpacing(0)
        head=QWidget();head.setFixedHeight(39);row=QHBoxLayout(head);row.setContentsMargins(0,3,9,0);row.setSpacing(7);row.addWidget(DragHandle(self))
        icon=QLabel();symbol=QPixmap(18,18);symbol.fill(Qt.GlobalColor.transparent);painter=QPainter(symbol);painter.setRenderHint(QPainter.RenderHint.Antialiasing);paint_symbol(painter,self.kind,QRectF(0,0,18,18),COLORS[self.kind]);painter.end();icon.setPixmap(symbol);row.addWidget(icon)
        caption=QLabel('Section' if self.kind=='section' else 'For each timeline segment' if self.kind=='segments' else 'For each subject');caption.setStyleSheet('font:14px "Roboto Condensed";color:#e0e8ef;');row.addWidget(caption)
        if self.kind=='section':
            self.heading=QLineEdit(node.get('heading',''));self.heading.setPlaceholderText('Section heading');self.heading.setMinimumWidth(40)
            self.heading.setStyleSheet('QLineEdit{font:14px "Roboto Condensed";background:transparent;border:0;border-bottom:1px solid #7f718e;border-radius:0;padding:1px;min-height:0;}')
            self.heading.textChanged.connect(canvas.changed);row.addWidget(self.heading,1)
        else:row.addStretch()
        menu_button=QPushButton('⋮');menu_button.setFixedSize(22,25);menu_button.setStyleSheet('QPushButton{background:transparent;border:0;padding:0;min-height:0;font-size:16px;}')
        menu=QMenu(menu_button);menu.addAction('Add text',lambda:self.zone.add_text());menu.addAction('Add nested section',lambda:canvas.apply_drop({'palette':'section'},self.zone))
        menu.addSeparator();menu.addAction('Remove block',lambda:canvas.remove_block(self));menu_button.setMenu(menu);row.addWidget(menu_button);layout.addWidget(head)
        self.zone=DropZone(canvas,self);layout.addWidget(self.zone);self.zone.load(node.get('children',[]));canvas.registry[self.id]=self
    def to_node(self):return dict(type=self.kind,heading=self.heading.text() if self.kind=='section' else '',children=self.zone.to_nodes())
    def paintEvent(self,event):
        painter=QPainter(self);painter.setRenderHint(QPainter.RenderHint.Antialiasing);accent=COLORS[self.kind];fill=mix('#212935',accent,.20);edge=mix('#3a4759',accent,.6)
        painter.setPen(QPen(edge,1));painter.setBrush(fill);painter.drawRoundedRect(QRectF(.5,5.5,self.width()-1,self.height()-7),8,8)
        painter.setPen(Qt.PenStyle.NoPen);painter.setBrush(QColor('#1b2430'));painter.drawRoundedRect(QRectF(11,39,self.width()+8,self.height()-52),4,4)
        painter.setBrush(fill);painter.setPen(QPen(edge,1));painter.drawRoundedRect(QRectF(22,self.height()-7,30,6),4,4)
        painter.setPen(Qt.PenStyle.NoPen);painter.setBrush(QColor('#151c26'));painter.drawRoundedRect(QRectF(22,0,30,9),4,4)
        painter.setPen(QPen(QColor(accent),2));painter.drawLine(1,14,1,self.height()-15)


class DropZone(QWidget):
    def __init__(self,canvas,owner=None):
        super().__init__();self.canvas=canvas;self.owner_block=owner;self.nodes=[];self.snapped=False;self.setMinimumWidth(0);self.setAcceptDrops(True)
        self.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Maximum)
        self.box=QVBoxLayout(self);self.box.setContentsMargins(10,9,10,9);self.box.setSpacing(9)
        self.empty=QPushButton('+  Drop a matching block here');self.empty.setStyleSheet('QPushButton{font:12px "Roboto Condensed";color:#a6b5c5;background:transparent;border:1px dashed #455266;border-radius:5px;padding:6px;min-height:22px;}')
        self.empty.clicked.connect(self.add_text);self.box.addWidget(self.empty)
    def scope(self):
        block=self.owner_block
        while block:
            if block.kind in ('segments','subjects'):return block.kind
            block=block.parent_zone.owner_block
        return None
    def label(self):
        if self.owner_block:
            return self.owner_block.heading.text() if self.owner_block.kind=='section' else 'each timeline segment' if self.owner_block.kind=='segments' else 'each subject'
        return 'the template'
    def load(self,nodes):
        for node in nodes:self.add_node(node,emit=False)
        self.update_empty()
    def to_nodes(self):return [node.to_node() for node in self.nodes]
    def update_empty(self):self.empty.setVisible(not self.nodes)
    def add_node(self,node,index=None,emit=True):
        block=TextBlock(self.canvas,self,node.get('text','')) if node['type']=='text' else ContainerBlock(self.canvas,self,node)
        index=len(self.nodes) if index is None else index;self.nodes.insert(index,block);self.box.insertWidget(index,block);self.update_empty()
        if emit:self.canvas.changed.emit()
        return block
    def add_text(self):
        block=self.add_node(dict(type='text',text=''));block.editor.setFocus();self.canvas.active_zone=self
    def can_accept(self,data):
        scope=self.scope();key=data.get('palette')
        if key in ('segments','subjects'):return scope is None and not any(block.kind==key for block in self.canvas.registry.values())
        if key in ('section','text','space'):return True
        if key in FIELDS or data.get('field'):
            if key=='segment_prompt':return scope=='segments' and not self.canvas.scope_has_field(scope,'prompt')
            if key=='subject_prompt':return scope=='subjects' and not self.canvas.scope_has_field(scope,'prompt')
            name=data.get('field') or FIELDS[key][1]
            origin=self.canvas.registry.get(data.get('origin'))
            if name=='prompt' and origin is not None:return scope==origin.parent_zone.scope()
            if name in ('start','end','duration','role'):return scope=='segments'
            if name in ('index','name'):return scope is not None
            return scope is None and (bool(data.get('field')) or not self.canvas.scope_has_field(None,name))
        moving=self.canvas.registry.get(data.get('move'))
        if moving is None or moving is self.owner_block or moving.isAncestorOf(self):return False
        try:
            self.canvas.check_node(moving.to_node(),scope)
            return True
        except ValueError:return False
    def highlight(self,on):
        if self.snapped!=on:self.snapped=on;self.update()
    def paintEvent(self,event):
        if self.snapped:
            painter=QPainter(self);painter.setRenderHint(QPainter.RenderHint.Antialiasing);painter.setPen(QPen(QColor('#28b6b1'),2));painter.setBrush(QColor(40,182,177,20));painter.drawRoundedRect(self.rect().adjusted(1,1,-1,-1),5,5)
    def dragEnterEvent(self,event):self.dragMoveEvent(event)
    def dragMoveEvent(self,event):
        data=payload(event.mimeData())
        if data and self.can_accept(data):self.highlight(True);event.acceptProposedAction()
        else:self.highlight(False);event.ignore()
    def dragLeaveEvent(self,event):self.highlight(False);event.accept()
    def dropEvent(self,event):
        data=payload(event.mimeData());self.highlight(False)
        if not data or not self.can_accept(data):event.ignore();return
        index=next((i for i,node in enumerate(self.nodes) if event.position().y()<node.geometry().center().y()),len(self.nodes))
        self.canvas.apply_drop(data,self,index);event.acceptProposedAction()


class TemplateCanvas(QScrollArea):
    changed=Signal()
    status_changed=Signal(str)
    def __init__(self):
        super().__init__();self.registry={};self.active_zone=None;self.setWidgetResizable(True);self.setMinimumWidth(0)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet('QScrollArea{background:#151c26;border:0;} QScrollArea > QWidget > QWidget{background:#151c26;}')
        self.root=DropZone(self);self.setWidget(self.root);self.setAccessibleName('Complete output template canvas')
    def set_status(self,message):self.status_changed.emit(message)
    def setPlainText(self,source):
        self.registry={};old=self.takeWidget();self.root=DropZone(self);self.root.load(parse_template(source));self.setWidget(self.root)
        self.active_zone=self.root
        if old:old.deleteLater()
    def toPlainText(self):return serialize_template(self.root.to_nodes())
    def check_node(self,node,scope):
        kind=node['type']
        if kind=='text':
            variables=names(node['text'])
            if scope is None and any(name in META|{'prompt'} for name in variables):raise ValueError('Timeline and subject fields need a repeat block.')
            if scope is not None and any(name not in META|{'prompt'} for name in variables):raise ValueError('Global fields belong outside a repeat block.')
        elif kind=='section':
            for child in node.get('children',[]):self.check_node(child,scope)
        else:
            if scope is not None:raise ValueError('Repeat groups need a section socket outside another repeat group.')
            for child in node.get('children',[]):self.check_node(child,kind)
    def field_name(self,key):
        if key!='custom':return FIELDS[key][1]
        label,ok=QInputDialog.getText(self,'Name this field','Field name:')
        if not ok:return None
        return self.normalize_field_name(label)
    def normalize_field_name(self,label,excluding=None):
        name=re.sub(r'[^a-z0-9_]+','_',label.strip().lower()).strip('_')
        if not name or name[0].isdigit() or name in META|{'prompt','global_prompt'}:
            self.set_status('Choose a descriptive field name, starting with a letter.');return None
        if name!=excluding and self.scope_has_field(None,name):
            self.set_status('That field already exists. Drag its chip to move it.');return None
        return name
    def scope_has_field(self,scope,name):
        return any(isinstance(block,TextBlock) and block.parent_zone.scope()==scope
                   and '${'+name+'}' in block.editor.source() for block in self.registry.values())
    def insert_palette(self,key):
        data={'palette':key};zone=self.active_zone or self.root
        if not zone.can_accept(data):
            zone=next((candidate for candidate in [self.root,*self.root.findChildren(DropZone)] if candidate.can_accept(data)),None)
        if zone:self.apply_drop(data,zone)
        else:self.set_status('Use a matching socket, or drag the existing pill to move this field.')
    def apply_drop(self,data,zone,index=None):
        if not zone.can_accept(data):self.set_status('That block needs a matching socket.');return
        moving=self.registry.get(data.get('move'))
        if moving:
            old=moving.parent_zone;old_index=old.nodes.index(moving);old.nodes.remove(moving);old.box.removeWidget(moving);old.update_empty()
            if old is zone and index is not None and old_index<index:index-=1
            index=len(zone.nodes) if index is None else index;moving.parent_zone=zone;zone.nodes.insert(index,moving);zone.box.insertWidget(index,moving);moving.show();zone.update_empty();self.changed.emit()
            self.set_status('Block and its contents snapped into '+zone.label()+'.');return
        if data.get('field'):
            self.remove_origin_token(data);zone.add_node(dict(type='text',text='${'+data['field']+'}\n'),index)
            self.active_zone=zone;self.set_status('Pill snapped into '+zone.label()+'.');return
        key=data['palette']
        if key in FIELDS:
            name=self.field_name(key)
            if not name:return
            zone.add_node(dict(type='text',text='${'+name+'}\n'),index)
        elif key=='section':zone.add_node(dict(type='section',heading='New section',children=[]),index)
        elif key in ('segments','subjects'):
            body='[Shot ${index}] At ${start} – ${end}\n${prompt}\n\n' if key=='segments' else '[Subject ${index}: ${name}]\n${prompt}\n\n'
            zone.add_node(dict(type=key,children=[dict(type='text',text=body)]),index)
        else:zone.add_node(dict(type='text',text='\n' if key=='space' else ''),index)
        self.active_zone=zone;self.set_status('Block snapped into '+zone.label()+'.')
    def remove_origin_token(self,data):
        origin=self.registry.get(data.get('origin'))
        if origin and data.get('field'):
            cursor=QTextCursor(origin.editor.document());cursor.setPosition(int(data['position']));cursor.movePosition(QTextCursor.MoveOperation.NextCharacter,QTextCursor.MoveMode.KeepAnchor)
            if cursor.charFormat().property(TOKEN_PROPERTY)=='${'+data['field']+'}':cursor.removeSelectedText()
    def remove_block(self,block):
        zone=block.parent_zone;zone.nodes.remove(block);zone.box.removeWidget(block)
        for key,candidate in list(self.registry.items()):
            if candidate is block or block.isAncestorOf(candidate):self.registry.pop(key,None)
        block.deleteLater();zone.update_empty();self.changed.emit()
    def start_move(self,block):
        drag=QDrag(block);mime=QMimeData();mime.setData(MIME,json.dumps({'move':block.id}).encode());drag.setMimeData(mime)
        image=block.grab();image=image.scaledToWidth(min(380,image.width()),Qt.TransformationMode.SmoothTransformation)
        drag.setPixmap(image);drag.setHotSpot(QPoint(20,15));drag.exec(Qt.DropAction.MoveAction)
