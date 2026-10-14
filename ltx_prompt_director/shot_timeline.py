"""Time-scaled NLA tracks for completed movies and their linked source audio."""
from pathlib import Path
from collections import OrderedDict
from PySide6.QtCore import Qt, Signal, QRectF
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QScrollArea, QWidget, QListWidget, QAbstractItemView
from .media import TIMELINE_VIDEO_SUFFIXES
from .shot_sequence import overlaps, FPS


class ShotTimeline(QScrollArea):
    movies_dropped = Signal(list)
    remove_requested = Signal()
    order_changed = Signal()
    itemDoubleClicked = Signal(object)
    seek_requested = Signal(int)
    LEFT = 94
    TOP = 50
    TRACK = 60
    AUDIO_TRACK = 37

    def __init__(self, parent=None):
        super().__init__(parent)
        self.items = QListWidget(self)
        self.items.hide()
        self.items.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.canvas = TimelineCanvas(self)
        self.setWidget(self.canvas)
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumHeight(274)
        self.scale = 38.0
        self.total = 0.0
        self.clips = []
        self.playhead = 0.0
        self.coverage=[]
        self._thumbnails=OrderedDict()
        self._fit = True
        self.items.itemSelectionChanged.connect(self.canvas.update)

    # Keep ordering/selection in a Qt model, without displaying a second shot list.
    def clear(self):self.items.clear()
    def addItem(self, item):self.items.addItem(item)
    def item(self, index):return self.items.item(index)
    def count(self):return self.items.count()
    def selectedItems(self):return self.items.selectedItems()
    def currentRow(self):return self.items.currentRow()
    def setCurrentRow(self, row):self.items.setCurrentRow(row);self.canvas.update()
    def takeItem(self, row):return self.items.takeItem(row)
    def insertItem(self, row, item):self.items.insertItem(row,item)

    def set_shots(self, shots, overlap):
        ids={shot.id for shot in shots}
        self._thumbnails=OrderedDict((key,value) for key,value in self._thumbnails.items() if key in ids)
        joins, count = overlaps([{'frames':max(1,round(s.duration*FPS))} for s in shots],overlap)
        self.total = count/FPS
        self.clips=[]
        end=0.0
        for index,shot in enumerate(shots):
            amount=joins[index-1]/FPS if index else 0.0
            start=end-amount
            self.clips.append(dict(start=start,end=start+shot.duration,overlap=amount,
                                   name=shot.name,preview=QPixmap(shot.preview_path),id=shot.id,thumbs=self._thumbnails.get(shot.id,[])))
            end=start+shot.duration
        self.resize_canvas()

    def set_thumbnails(self,shot_id,images):
        if len(images)>64:images=[images[round(i*(len(images)-1)/63)] for i in range(64)]
        thumbs=[]
        for data in images:
            image=QPixmap();image.loadFromData(data);thumbs.append(image)
        self._thumbnails[shot_id]=thumbs
        # Bound decoded filmstrips independently of the compressed preview cache.
        while sum(sum(p.width()*p.height()*4 for p in images) for images in self._thumbnails.values())>24*1024*1024:
            removed,_=self._thumbnails.popitem(last=False)
            for clip in self.clips:
                if clip['id']==removed:clip['thumbs']=[]
        for clip in self.clips:
            if clip['id']==shot_id:clip['thumbs']=thumbs
        self.canvas.update()

    def set_coverage(self,ranges):
        self.coverage=list(ranges);self.canvas.update()

    def scrub(self,x):
        position=round(max(0,min(self.total,(x-self.LEFT)/self.scale))*1000)
        self.set_playhead(position);self.seek_requested.emit(position)

    def lane_y(self,lane):
        return self.TOP+min(lane,2)*self.TRACK+max(0,lane-2)*self.AUDIO_TRACK

    def clip_rect(self, index, audio=False):
        clip=self.clips[index]
        lane=index%2+(2 if audio else 0)
        return QRectF(self.LEFT+clip['start']*self.scale,self.lane_y(lane)+5,
                      max(1,(clip['end']-clip['start'])*self.scale),(self.AUDIO_TRACK if audio else self.TRACK)-10)

    def resize_canvas(self):
        if self._fit:self.scale=max(.2,(self.viewport().width()-self.LEFT-28)/max(1,self.total))
        self.canvas.resize(max(self.viewport().width(),round(self.LEFT+self.total*self.scale+28)),
                           max(self.viewport().height(),self.TOP+2*self.TRACK+2*self.AUDIO_TRACK+24))
        self.canvas.update()

    def resizeEvent(self,event):
        super().resizeEvent(event);self.resize_canvas()

    def fit(self):self._fit=True;self.resize_canvas()
    def zoom(self, factor):
        self._fit=False;self.scale=max(.2,min(500,self.scale*factor));self.resize_canvas()

    def set_playhead(self, milliseconds):
        self.playhead=milliseconds/1000;self.canvas.update()

    def reorder(self, indices, target):
        indices=sorted(set(indices))
        if not indices:return
        target=max(0,min(self.count(),target))
        destination=target-sum(i<target for i in indices)
        moving=[self.takeItem(i) for i in reversed(indices)][::-1]
        for offset,item in enumerate(moving):self.insertItem(destination+offset,item)
        self.order_changed.emit()
        for index in range(destination,destination+len(moving)):
            if self.item(index):self.item(index).setSelected(True)
        self.canvas.update()

    def keyPressEvent(self,event):
        if event.key()==Qt.Key.Key_Delete:self.remove_requested.emit();event.accept()
        else:super().keyPressEvent(event)

    def movie_paths(self,event):
        return [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()
                and Path(url.toLocalFile()).suffix.lower() in TIMELINE_VIDEO_SUFFIXES]
    def dragEnterEvent(self,event):
        if self.movie_paths(event):event.acceptProposedAction()
        else:event.ignore()
    def dragMoveEvent(self,event):
        if self.movie_paths(event):event.acceptProposedAction()
        else:event.ignore()
    def dropEvent(self,event):
        paths=self.movie_paths(event)
        if paths:self.movies_dropped.emit(paths);event.acceptProposedAction()
        else:event.ignore()


class TimelineCanvas(QWidget):
    def __init__(self,timeline):
        super().__init__();self.timeline=timeline;self.press=None;self.dragging=False;self.target=None
        self.setMouseTracking(True)
        self.setToolTip('Drag strips to reorder · Ctrl-click selects multiple · drag scrubhead/ruler to seek the sequence · green is cached, red is rebuilding')
        self.scrubbing=False

    def hit(self,point):
        for i in range(len(self.timeline.clips)-1,-1,-1):
            if any(self.timeline.clip_rect(i,audio).contains(point) for audio in (False,True)):return i
        return None

    def insertion(self,x):
        t=self.timeline
        for i,clip in enumerate(t.clips):
            if x<t.LEFT+(clip['start']+clip['end'])/2*t.scale:return i
        return len(t.clips)

    def paintEvent(self,event):
        t=self.timeline;p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(),QColor('#161c23'))
        for lane,label in enumerate(('Video A','Video B','Audio A','Audio B')):
            y=t.lane_y(lane);height=t.TRACK if lane<2 else t.AUDIO_TRACK
            p.fillRect(0,y,self.width(),height,QColor('#202933' if lane%2==0 else '#1a232d'))
            p.setPen(QColor('#8293a7'));p.drawText(QRectF(9,y,t.LEFT-16,height),Qt.AlignmentFlag.AlignVCenter,label)
        p.setPen(QColor('#8492a6'))
        # Ruler and vertical grid keep useful spacing at any zoom.
        step=next((v for v in (.25,.5,1,2,5,10,15,30,60,120,300,600) if v*t.scale>=65),1200)
        at=0.0
        while at<=t.total+step:
            x=t.LEFT+at*t.scale
            p.setPen(QColor('#354251'));p.drawLine(int(x),t.TOP,int(x),self.height())
            p.setPen(QColor('#b1c1d3'));p.drawLine(int(x),t.TOP-7,int(x),t.TOP)
            label=f'{int(at)//60:02d}:{at%60:05.2f}'
            p.drawText(QRectF(x+4,8,100,24),label);at+=step
        # Coverage follows the actual cached timeline regions, above the strips.
        for start,end,ready in t.coverage:
            p.fillRect(QRectF(t.LEFT+start*t.scale,t.TOP-8,max(1,(end-start)*t.scale),5),QColor("#54d88d" if ready else "#e65c64"))
        for index,clip in enumerate(t.clips):
            selected=t.item(index) is not None and t.item(index).isSelected()
            for audio in (False,True):
                rect=t.clip_rect(index,audio)
                color=QColor('#29645f' if audio else '#36587b')
                p.setBrush(color);p.setPen(QPen(QColor('#efbc65' if selected else '#62778b'),2 if selected else 1))
                p.drawRoundedRect(rect,4,4)
                p.save();p.setClipRect(rect.adjusted(3,2,-3,-2))
                label=(f'{index+1:02d} · Audio · {clip["end"]-clip["start"]:.2f}s' if audio else f'{index+1:02d} · {clip["name"]}')
                if not audio:
                    # Filmstrip tiles represent different moments from the source.
                    images=clip['thumbs'] or [clip['preview']]
                    tile_width=76
                    for offset in range(0,max(1,int(rect.width())),tile_width):
                        time_fraction=min(1,(offset+tile_width/2)/max(1,rect.width()))
                        thumb=images[min(len(images)-1,int(time_fraction*len(images)))]
                        if not thumb.isNull():
                            image=thumb.scaled(tile_width,rect.height()-18,Qt.AspectRatioMode.KeepAspectRatioByExpanding,Qt.TransformationMode.SmoothTransformation)
                            p.drawPixmap(int(rect.x()+offset),int(rect.y()+18),image)
                    p.fillRect(QRectF(rect.x(),rect.y(),rect.width(),18),QColor(15,25,38,210))
                p.setPen(QColor('#edf3fa'));p.drawText(QRectF(rect.x()+7,rect.y()+1,max(1,rect.width()-12),18),label)
                if clip['overlap']:
                    fade=QRectF(rect.x(),rect.y(),clip['overlap']*t.scale,rect.height())
                    p.fillRect(fade,QColor(234,185,101,40));p.setPen(QPen(QColor('#e7b46c'),1))
                    p.drawLine(fade.bottomLeft(),fade.topRight())
                p.restore()
        if not t.clips:
            p.setPen(QColor('#acbacb'));p.drawText(QRectF(t.LEFT+20,t.TOP+25,self.width()-t.LEFT-40,100),Qt.AlignmentFlag.AlignCenter,'Drop completed movies here to build your sequence')
        x=t.LEFT+min(t.total,t.playhead)*t.scale
        p.setPen(QPen(QColor('#f3c476'),2));p.drawLine(int(x),0,int(x),self.height())
        p.setBrush(QColor('#f3c476'));p.drawRect(QRectF(x-5,0,10,8))
        if self.target is not None:
            x=t.LEFT+(t.clips[self.target]['start'] if self.target<len(t.clips) else t.total)*t.scale
            p.setPen(QPen(QColor('#74e5cc'),3));p.drawLine(int(x),t.TOP,int(x),self.height())
        p.end()

    def mousePressEvent(self,event):
        t=self.timeline;t.setFocus()
        if event.button()!=Qt.MouseButton.LeftButton:return
        point=event.position()
        head=t.LEFT+t.playhead*t.scale
        if point.y()<t.TOP or abs(point.x()-head)<5:
            self.scrubbing=True;t.scrub(point.x());return
        index=self.hit(point)
        if index is None:
            t.items.clearSelection();return
        item=t.item(index)
        if event.modifiers()&Qt.KeyboardModifier.ControlModifier:item.setSelected(not item.isSelected())
        elif not item.isSelected():t.setCurrentRow(index)
        self.press=point;self.dragging=False;self.update()

    def mouseMoveEvent(self,event):
        if self.scrubbing:self.timeline.scrub(event.position().x());return
        if self.press is not None and (event.position()-self.press).manhattanLength()>6:
            self.dragging=True;self.target=self.insertion(event.position().x());self.update()

    def mouseReleaseEvent(self,event):
        t=self.timeline
        if self.dragging and self.target is not None:
            indices=[i for i in range(t.count()) if t.item(i).isSelected()]
            t.reorder(indices,self.target)
        self.press=None;self.target=None;self.dragging=False;self.scrubbing=False;self.update()

    def mouseDoubleClickEvent(self,event):
        index=self.hit(event.position())
        if index is not None:
            self.timeline.setCurrentRow(index)
            self.timeline.scrub(event.position().x())
        self.press=None
