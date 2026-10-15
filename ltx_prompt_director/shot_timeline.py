"""Time-scaled NLA tracks for completed movies and their linked source audio."""
from pathlib import Path
from collections import OrderedDict
import copy
from PySide6.QtCore import Qt, Signal, QRectF, QItemSelectionModel
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap, QPainterPath, QIcon, QActionGroup
from PySide6.QtWidgets import QScrollArea, QWidget, QListWidget, QAbstractItemView, QMenu
from .media import TIMELINE_VIDEO_SUFFIXES
from .shot_sequence import shot_layout, settings, transitions, FPS


class ShotTimeline(QScrollArea):
    movies_dropped = Signal(list,int)
    movies_replaced = Signal(list,str)
    status_requested = Signal(list,str)
    remove_requested = Signal()
    order_changed = Signal()
    itemDoubleClicked = Signal(object)
    seek_requested = Signal(int)
    frame_seek_requested = Signal(int)
    scrub_started = Signal()
    scrub_finished = Signal()
    export_requested = Signal(str)
    trim_started = Signal()
    trim_requested = Signal(str,int,int)
    split_requested = Signal(str,int)
    LEFT = 94
    TOP = 50
    TRACK = 60
    AUDIO_TRACK = 37

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('shotTimeline');self.setProperty('dropActive',False)
        self.items = QListWidget(self)
        self.items.hide()
        self.items.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.canvas = TimelineCanvas(self)
        self.setWidget(self.canvas)
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumHeight(150)
        self.scale = 38.0
        self.total = 0.0
        self.clips = []
        self.playhead = 0.0
        self.coverage=[]
        self.replace_target=None
        self.drop_target=None
        self.status_tags=[];self.statuses={}
        self._thumbnails=OrderedDict();self._waveforms={}
        self._fit = True
        self.items.itemSelectionChanged.connect(self.canvas.update)

    # Keep ordering/selection in a Qt model, without displaying a second shot list.
    def clear(self):self.items.clear()
    def addItem(self, item):self.items.addItem(item)
    def item(self, index):return self.items.item(index)
    def count(self):return self.items.count()
    def selectedItems(self):return self.items.selectedItems()
    def currentRow(self):return self.items.currentRow()
    def setCurrentRow(self, row):
        self.items.setCurrentRow(row,QItemSelectionModel.SelectionFlag.ClearAndSelect);self.canvas.update()
    def takeItem(self, row):return self.items.takeItem(row)
    def insertItem(self, row, item):self.items.insertItem(row,item)

    def set_shots(self, shots, overlap):
        options=settings(overlap if isinstance(overlap,dict) else {'overlap':overlap})
        self._shots=[copy.copy(shot) for shot in shots];self._options=options
        ids={shot.id for shot in shots}
        self._waveforms={key:value for key,value in self._waveforms.items() if key in ids}
        self._thumbnails=OrderedDict((key,value) for key,value in self._thumbnails.items() if key in ids)
        joins,count=shot_layout(shots,options)
        outgoing=transitions([shot.id for shot in shots],options)
        self.total = count/FPS
        self.clips=[]
        end=0.0
        for index,shot in enumerate(shots):
            amount=joins[index-1]/FPS if index else 0.0
            start=end-amount
            self.clips.append(dict(start=start,end=start+shot.duration,overlap=amount,
                                   source_start=int(shot.trim_start or 0),source_end=int(shot.trim_start or 0)+max(1,round(shot.duration*FPS)),
                                   source_frames=shot.media_duration_frames or int(shot.trim_start or 0)+max(1,round(shot.duration*FPS)),
                                   name=shot.name,preview=QPixmap(shot.preview_path),id=shot.id,thumbs=self._thumbnails.get(shot.id,[]),peaks=self._waveforms.get(shot.id,[]),
                                   transition=outgoing[index] if index<len(outgoing) else None,
                                   custom_transition=shot.id in options.get('shot_transitions',{})))
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

    def set_statuses(self,statuses,tags):
        self.statuses=dict(statuses) if isinstance(statuses,dict) else {}
        self.status_tags=list(tags)
        colors={tag['name'].casefold():tag['color'] for tag in tags}
        for clip in self.clips:
            clip['status']=self.statuses.get(clip['id'],'')
            clip['status_color']=colors.get(clip['status'].casefold(),'#56616a' if clip['status'] else '')
        self.canvas.update()

    def status_menu(self,index):
        if not self.item(index).isSelected():self.setCurrentRow(index)
        ids=[item.data(Qt.ItemDataRole.UserRole) for item in self.selectedItems()]
        selected={self.statuses.get(ident,'').casefold() for ident in ids}
        menu=QMenu(self);menu.addSection('Shot status')
        group=QActionGroup(menu);group.setExclusive(True)
        for name,color in [('',None)]+[(t['name'],t['color']) for t in self.status_tags]:
            action=menu.addAction(name or 'No status');action.setCheckable(True)
            action.setChecked(selected=={name.casefold()});group.addAction(action)
            if color:
                swatch=QPixmap(16,16);swatch.fill(QColor(color));action.setIcon(QIcon(swatch))
            action.triggered.connect(lambda checked=False,name=name:self.status_requested.emit(ids,name))
        menu.addSeparator()
        ident=self.item(index).data(Qt.ItemDataRole.UserRole)
        clip=self.clips[index]
        frame=round((self.playhead-clip['start'])*FPS)
        split=menu.addAction('Split at playhead',lambda:self.split_requested.emit(ident,frame))
        split.setEnabled(0<frame<clip['source_end']-clip['source_start'])
        export=menu.addAction('Export segment video',lambda:self.export_requested.emit(ident))
        export.setToolTip('Export the trimmed source with original video/audio codecs. Stream-copy cuts depend on source keyframes.')
        menu.addSeparator()
        menu.addAction('Delete',self.remove_requested.emit)
        return menu

    def set_waveform(self,shot_id,peaks):
        self._waveforms[shot_id]=list(peaks)
        for clip in self.clips:
            if clip['id']==shot_id:clip['peaks']=self._waveforms[shot_id]
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

    def set_playhead(self, milliseconds, follow=False):
        self.playhead=milliseconds/1000;self.canvas.update()
        if follow and not self.canvas.scrubbing:
            bar=self.horizontalScrollBar();x=self.LEFT+self.playhead*self.scale
            margin=min(80,self.viewport().width()/4)
            if milliseconds<=0:bar.setValue(0)
            elif x>bar.value()+self.viewport().width()-margin:
                bar.setValue(round(x-self.viewport().width()+margin))
            elif x<bar.value()+margin:
                bar.setValue(round(max(0,x-margin)))

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
        if event.key() in (Qt.Key.Key_Left,Qt.Key.Key_Right) and event.modifiers()==Qt.KeyboardModifier.NoModifier:
            self.step_frame(-1 if event.key()==Qt.Key.Key_Left else 1);event.accept()
        elif event.key()==Qt.Key.Key_Escape and self.canvas.trimming is not None:
            self.canvas.cancel_trim();event.accept()
        elif event.key()==Qt.Key.Key_Delete:self.remove_requested.emit();event.accept()
        else:super().keyPressEvent(event)

    def step_frame(self,direction):
        if not self.clips or self.canvas.trimming is not None:return
        frame=max(0,min(max(0,round(self.total*FPS)-1),round(self.playhead*FPS)+direction))
        position=round(frame*1000/FPS)
        self.set_playhead(position,follow=True)
        self.frame_seek_requested.emit(position)

    def movie_paths(self,event):
        return [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()
                and Path(url.toLocalFile()).suffix.lower() in TIMELINE_VIDEO_SUFFIXES]
    def dragEnterEvent(self,event):
        if self.movie_paths(event):
            self.set_drop_active(True);self.update_drop_target(event);event.acceptProposedAction()
        else:event.ignore()

    def set_drop_active(self,active):
        if self.property('dropActive')==active:return
        self.setProperty('dropActive',active);self.style().unpolish(self);self.style().polish(self)

    def update_drop_target(self,event):
        self.replace_target=self.replacement_target(event)
        point=self.canvas.mapFrom(self.viewport(),event.position().toPoint())
        self.drop_target=None if self.replace_target is not None else self.canvas.insertion(point.x())
        self.canvas.update()

    def clear_drop_target(self):
        self.replace_target=None;self.drop_target=None;self.set_drop_active(False);self.canvas.update()
    def replacement_target(self,event):
        if not event.modifiers()&Qt.KeyboardModifier.ControlModifier:return None
        point=self.canvas.mapFrom(self.viewport(),event.position().toPoint())
        return self.canvas.hit(point)

    def dragMoveEvent(self,event):
        if self.movie_paths(event):
            self.set_drop_active(True);self.update_drop_target(event);event.acceptProposedAction()
        else:self.clear_drop_target();event.ignore()

    def dragLeaveEvent(self,event):
        self.clear_drop_target();event.accept()

    def dropEvent(self,event):
        paths=self.movie_paths(event);target=self.replacement_target(event)
        point=self.canvas.mapFrom(self.viewport(),event.position().toPoint())
        insertion=self.canvas.insertion(point.x())
        self.clear_drop_target()
        if paths:
            if target is not None:self.movies_replaced.emit(paths,self.item(target).data(Qt.ItemDataRole.UserRole))
            else:self.movies_dropped.emit(paths,insertion)
            event.acceptProposedAction()
        else:event.ignore()


class TimelineCanvas(QWidget):
    def __init__(self,timeline):
        super().__init__();self.timeline=timeline;self.press=None;self.dragging=False;self.target=None
        self.setMouseTracking(True)
        self.setToolTip('Drag strip edges to trim linked video/audio · drag strips to reorder · right-click to split at the playhead · Left/Right steps one frame · Ctrl-click selects multiple · drag scrubhead/ruler to seek · green is cached, red is rebuilding · Ctrl-drop replaces a shot')
        self.scrubbing=False;self.trimming=None

    def trim_handle(self,point):
        index=self.hit(point)
        if index is None:return None
        audio=self.timeline.clip_rect(index,True).contains(point)
        rect=self.timeline.clip_rect(index,audio)
        band=min(8,max(2,rect.width()/3))
        if abs(point.x()-rect.left())<=band:return index,'start'
        if abs(point.x()-rect.right())<=band:return index,'end'
        return None

    def cancel_trim(self):
        if self.trimming is None:return
        edit=self.trimming;self.trimming=None;t=self.timeline;t._fit=edit['fit']
        t.set_shots(edit['shots'],edit['options']);t.set_statuses(t.statuses,t.status_tags)
        self.unsetCursor()

    def hit(self,point):
        for i in range(len(self.timeline.clips)-1,-1,-1):
            if any(self.timeline.clip_rect(i,audio).contains(point) for audio in (False,True)):return i
        return None

    def insertion(self,x):
        t=self.timeline
        boundaries=[clip['start'] for clip in t.clips]+[t.total]
        return min(range(len(boundaries)),key=lambda index:abs(x-(t.LEFT+boundaries[index]*t.scale)))

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
                if not rect.intersects(QRectF(event.rect())):continue
                status_color=QColor(clip.get('status_color',''))
                color=status_color.darker(160) if status_color.isValid() else QColor('#29645f' if audio else '#36587b')
                p.setBrush(color);p.setPen(QPen(QColor('#68b9ee' if t.replace_target==index else '#efbc65' if selected else '#62778b'),2 if selected or t.replace_target==index else 1))
                if status_color.isValid() and not selected and t.replace_target!=index:p.setPen(QPen(status_color.lighter(145),1.4))
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
                    if status_color.isValid():
                        tint=QColor(status_color);tint.setAlpha(85);p.fillRect(rect,tint)
                    p.fillRect(QRectF(rect.x(),rect.y(),rect.width(),18),QColor(15,25,38,210))
                if audio:
                    area=rect.adjusted(3,1,-3,-1);center=area.center().y()
                    p.setPen(QPen(QColor('#79c4b7'),.7));p.drawLine(area.left(),center,area.right(),center)
                    peaks=clip['peaks']
                    if peaks:
                        wave=QPainterPath()
                        gain=1/max(.02,max(peaks))
                        def amplitude(value):return min(.5*area.height(),value*gain*.48*area.height())
                        wave.moveTo(area.left(),center)
                        for i,peak in enumerate(peaks):wave.lineTo(area.left()+i*area.width()/max(1,len(peaks)-1),center-amplitude(peak))
                        for i in range(len(peaks)-1,-1,-1):wave.lineTo(area.left()+i*area.width()/max(1,len(peaks)-1),center+amplitude(peaks[i]))
                        wave.closeSubpath();p.setBrush(QColor('#a3e0cf'));p.setPen(QPen(QColor('#86cbb9'),.6));p.drawPath(wave)
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.setPen(QColor('#edf3fa'));p.drawText(QRectF(rect.x()+7,rect.y()+1,max(1,rect.width()-12),18),label)
                else:
                    p.setPen(QColor('#edf3fa'));p.drawText(QRectF(rect.x()+7,rect.y()+1,max(1,rect.width()-12),18),label)
                    join=clip['transition']
                    if join and clip['custom_transition'] and rect.width()>120:
                        text=f'→ {join["blend"]} · {join["overlap"]:.2f}s'
                        badge=QRectF(rect.x()+3,rect.bottom()-17,rect.width()-6,15)
                        p.fillRect(badge,QColor(15,25,38,220));p.drawText(badge,Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter,text)
                if clip['overlap']:
                    fade=QRectF(rect.x(),rect.y(),clip['overlap']*t.scale,rect.height())
                    p.fillRect(fade,QColor(234,185,101,40));p.setPen(QPen(QColor('#e7b46c'),1))
                    p.drawLine(fade.bottomLeft(),fade.topRight())
                p.restore()
                # Video and audio edges edit one linked source interval.
                grip=min(6,max(2,rect.width()/4))
                for edge in (rect.left(),rect.right()-grip):
                    p.fillRect(QRectF(edge,rect.top()+3,grip,rect.height()-6),QColor('#c1d6e8' if selected else '#8299ae'))
        if not t.clips:
            p.setPen(QColor('#acbacb'));p.drawText(QRectF(t.LEFT+20,t.TOP+25,self.width()-t.LEFT-40,100),Qt.AlignmentFlag.AlignCenter,'Drop completed movies here to build your sequence')
        x=t.LEFT+min(t.total,t.playhead)*t.scale
        p.setPen(QPen(QColor('#f3c476'),2));p.drawLine(int(x),0,int(x),self.height())
        p.setBrush(QColor('#f3c476'));p.drawRect(QRectF(x-5,0,10,8))
        target=t.drop_target if t.drop_target is not None else self.target
        if target is not None:
            x=t.LEFT+(t.clips[target]['start'] if target<len(t.clips) else t.total)*t.scale
            p.setPen(QPen(QColor('#68b9ee'),4));p.drawLine(int(x),4,int(x),self.height()-4)
        p.end()

    def contextMenuEvent(self,event):
        index=self.hit(event.pos())
        if index is None:return
        menu=self.timeline.status_menu(index)
        menu.exec(event.globalPos());menu.deleteLater()

    def mousePressEvent(self,event):
        t=self.timeline;t.setFocus()
        if event.button()!=Qt.MouseButton.LeftButton:return
        point=event.position()
        handle=self.trim_handle(point) if not event.modifiers()&Qt.KeyboardModifier.ControlModifier else None
        if handle is not None:
            index,edge=handle;t.setCurrentRow(index)
            clip=t.clips[index]
            self.trimming=dict(index=index,edge=edge,x=point.x(),scale=t.scale,fit=t._fit,
                               start=clip['source_start'],end=clip['source_end'],limit=clip['source_frames'],
                               shots=[copy.copy(s) for s in t._shots],options=t._options,values=None)
            t._fit=False;t.trim_started.emit();self.setCursor(Qt.CursorShape.SizeHorCursor);return
        head=t.LEFT+t.playhead*t.scale
        if point.y()<t.TOP or abs(point.x()-head)<5:
            self.scrubbing=True;t.scrub_started.emit();t.scrub(point.x());return
        index=self.hit(point)
        if index is None:
            t.items.clearSelection();return
        item=t.item(index)
        if event.modifiers()&Qt.KeyboardModifier.ControlModifier:
            selected=item.isSelected()
            t.items.setCurrentItem(item,QItemSelectionModel.SelectionFlag.NoUpdate)
            item.setSelected(not selected)
        elif not item.isSelected():t.setCurrentRow(index)
        else:t.items.setCurrentItem(item,QItemSelectionModel.SelectionFlag.NoUpdate)
        self.press=point;self.dragging=False;self.update()

    def mouseMoveEvent(self,event):
        if self.trimming is not None:
            edit=self.trimming;t=self.timeline
            delta=round((event.position().x()-edit['x'])/edit['scale']*FPS)
            start,end=edit['start'],edit['end']
            if edit['edge']=='start':start=max(0,min(end-1,start+delta))
            else:end=max(start+1,min(edit['limit'],end+delta))
            shots=[copy.copy(s) for s in edit['shots']];shot=shots[edit['index']]
            shot.trim_start=start;shot.duration=(end-start)/FPS
            edit['values']=(shot.id,start,end)
            t.set_shots(shots,edit['options']);t.set_statuses(t.statuses,t.status_tags)
            self.setToolTip(f'Source in {start/FPS:.3f}s · out {end/FPS:.3f}s · {(end-start)/FPS:.3f}s')
            return
        if self.scrubbing:self.timeline.scrub(event.position().x());return
        if self.press is not None and (event.position()-self.press).manhattanLength()>6:
            self.dragging=True;self.timeline.set_drop_active(True);self.target=self.insertion(event.position().x());self.update()
        elif self.press is None:
            self.setCursor(Qt.CursorShape.SizeHorCursor if self.trim_handle(event.position()) else Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self,event):
        t=self.timeline
        if self.trimming is not None:
            values=self.trimming['values'];self.cancel_trim()
            if values:t.trim_requested.emit(*values)
            self.unsetCursor();return
        if self.scrubbing:t.scrub_finished.emit()
        if self.dragging and self.target is not None:
            indices=[i for i in range(t.count()) if t.item(i).isSelected()]
            t.reorder(indices,self.target)
        t.set_drop_active(False)
        self.press=None;self.target=None;self.dragging=False;self.scrubbing=False;self.update()

    def mouseDoubleClickEvent(self,event):
        index=self.hit(event.position())
        if index is not None:
            self.timeline.setCurrentRow(index)
            self.timeline.scrub(event.position().x())
        self.press=None
