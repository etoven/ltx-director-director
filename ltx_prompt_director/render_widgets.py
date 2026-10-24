"""Compact queue cards and transient rendering overlays for project thumbnails."""
from math import cos, sin, pi
from pathlib import Path
import sys
from PySide6.QtCore import Qt, QRect, QSize, QTimer, QProcess, QMimeData, QUrl, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPen, QDrag, QDesktopServices
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QPushButton, QSizePolicy, QStyledItemDelegate, QStyle, QVBoxLayout, QWidget, QFileDialog, QMessageBox)
from .render_queue import ACTIVE_STATES, RenderJob


def render_icon(name):
    return QIcon(str(Path(__file__).parent/'assets'/('render-'+name+'.svg')))


def paint_render_overlay(painter,image,job,position,phase,font,scale):
    s=lambda n:max(1,round(n*scale))
    painter.save();painter.setRenderHint(painter.RenderHint.Antialiasing);painter.setClipRect(image)
    painter.fillRect(image,QColor(5,14,22,135))
    color=QColor('#e5b866' if job.state=='Waiting' else '#77d4e5')
    center=image.center();center.setY(center.y()-s(8));radius=s(12)
    for i in range(12):
        fade=QColor(color);fade.setAlpha(45+int(210*((i-phase)%12)/11))
        pen=QPen(fade,s(2));pen.setCapStyle(Qt.PenCapStyle.RoundCap);painter.setPen(pen)
        angle=i*pi/6
        painter.drawLine(center.x()+int(cos(angle)*radius*.6),center.y()+int(sin(angle)*radius*.6),center.x()+int(cos(angle)*radius),center.y()+int(sin(angle)*radius))
    source=QRect(image.right()-s(25),image.y()+s(4),s(21),s(21))
    painter.setBrush(QColor('#152532'));painter.setPen(QColor('#405768'));painter.drawRoundedRect(source,s(4),s(4))
    render_icon(job.source).paint(painter,source.adjusted(s(2),s(2),-s(2),-s(2)))
    label=job.state+(' · '+str(position) if job.state=='Waiting' else '')
    font=QFont(font);font.setPixelSize(s(12));painter.setFont(font)
    width=min(image.width()-s(6),painter.fontMetrics().horizontalAdvance(label)+s(28))
    badge=QRect(image.center().x()-width//2,image.bottom()-s(25),width,s(22))
    painter.setPen(QPen(color.darker(190),1));painter.setBrush(QColor('#10212d'));painter.drawRoundedRect(badge,s(5),s(5))
    render_icon('waiting' if job.state=='Waiting' else 'rendering').paint(painter,QRect(badge.x()+s(4),badge.y()+s(3),s(16),s(16)))
    painter.setPen(color);area=badge.adjusted(s(24),0,-s(3),0)
    painter.drawText(area,Qt.AlignmentFlag.AlignVCenter,painter.fontMetrics().elidedText(label,Qt.TextElideMode.ElideRight,max(1,area.width())))
    painter.restore()


class JobFileList(QListWidget):
    file_action = Signal(object, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True);self.setMouseTracking(True)

    def mouseMoveEvent(self,event):
        item=self.itemAt(event.position().toPoint())
        if item:
            job=item.data(Qt.ItemDataRole.UserRole)
            tooltip=job.title+'\n'+job.state
            for area,label in QueueJobDelegate.file_buttons(self.visualItemRect(item)):
                if job.output_path and area.contains(event.position().toPoint()):tooltip='Open containing folder' if label=='Folder' else 'Export file…'
            item.setToolTip(tooltip)
        super().mouseMoveEvent(event)

    def startDrag(self, actions):
        item = self.currentItem()
        job = item.data(Qt.ItemDataRole.UserRole) if item else None
        if not job or not job.output_path or not Path(job.output_path).is_file(): return
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(job.output_path)])
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)


class QueueJobDelegate(QStyledItemDelegate):
    """Paint only inside the list viewport; no oversized row widgets by the scrollbar."""
    def sizeHint(self,option,index):
        job=index.data(Qt.ItemDataRole.UserRole)
        return QSize(160, option.fontMetrics.height()+20 if job and job.kind=='Group' else option.fontMetrics.height()*3+26)

    def paint(self,painter,option,index):
        job=index.data(Qt.ItemDataRole.UserRole)
        if job is None:return
        painter.save();painter.setClipRect(option.rect);painter.setRenderHint(painter.RenderHint.Antialiasing)
        card=option.rect.adjusted(15 if job.group_id and job.kind!='Group' else 1,2,-1,-2)
        if job.kind=='Group':
            painter.setPen(QColor('#abd8dc'))
            painter.drawText(card.adjusted(5,0,-5,0), Qt.AlignmentFlag.AlignVCenter,
                             option.fontMetrics.elidedText(job.title, Qt.TextElideMode.ElideRight, max(0,card.width()-10)))
            painter.restore();return
        selected=bool(option.state & QStyle.StateFlag.State_Selected)
        painter.setPen(QColor('#67b5c7' if selected else '#354858'));painter.setBrush(QColor('#213f4b' if selected else '#1d2b36'))
        painter.drawRoundedRect(card,4,4)
        font=QFont(option.font);painter.setFont(font);fm=painter.fontMetrics();line=fm.height()+4
        text=card.adjusted(10,7,-42 if job.output_path else -10,-7)
        # Both source text and icon are laid out against viewport-bounded card,
        # with a separate gutter after them before the scrollbar.
        source='Cloud' if job.source=='cloud' else 'Local';source_width=fm.horizontalAdvance(source)+23
        source_rect=QRect(text.right()-source_width+1,text.y(),source_width,line)
        render_icon(job.source).paint(painter,QRect(source_rect.x(),source_rect.y()+2,16,16))
        painter.setPen(QColor('#b7cbd9'));painter.drawText(source_rect.adjusted(21,0,0,0),Qt.AlignmentFlag.AlignVCenter,source)
        status=job.state+(' · #'+str(index.data(Qt.ItemDataRole.UserRole+1)) if job.state=='Waiting' else '')
        state_rect=QRect(text.x(),text.y(),max(0,source_rect.x()-text.x()-12),line)
        render_icon('waiting' if job.state=='Waiting' else 'rendering').paint(painter,QRect(state_rect.x(),state_rect.y()+2,16,16))
        painter.setPen(QColor('#e5b866' if job.state=='Waiting' else '#77d4e5'))
        label=state_rect.adjusted(23,0,0,0);painter.drawText(label,Qt.AlignmentFlag.AlignVCenter,fm.elidedText(status,Qt.TextElideMode.ElideRight,max(0,label.width())))
        for offset,value,color in ((1,job.title,'#e2ebf1'),(2,job.model+' · '+job.kind,'#aabac7')):
            painter.setPen(QColor(color));area=QRect(text.x(),text.y()+offset*line,text.width(),line)
            painter.drawText(area,Qt.AlignmentFlag.AlignVCenter,fm.elidedText(value,Qt.TextElideMode.ElideRight,max(0,area.width())))
        if job.output_path and Path(job.output_path).is_file():
            for area, label in self.file_buttons(option.rect):
                standard=QStyle.StandardPixmap.SP_DirOpenIcon if label=='Folder' else QStyle.StandardPixmap.SP_DialogSaveButton
                QApplication.style().standardIcon(standard).paint(painter,area.adjusted(2,2,-2,-2))
        painter.restore()

    @staticmethod
    def file_buttons(rect):
        x=rect.right()-28;y=rect.top()+8
        return [(QRect(x,y,23,23),'Folder'),(QRect(x,y+27,23,23),'Export…')]

    def editorEvent(self,event,model,option,index):
        if event.type()==event.Type.MouseButtonRelease and event.button()==Qt.MouseButton.LeftButton:
            job=index.data(Qt.ItemDataRole.UserRole)
            if job and job.output_path and Path(job.output_path).is_file():
                for area,label in self.file_buttons(option.rect):
                    if area.contains(event.position().toPoint()):
                        self.parent().file_action.emit(job,label);return True
        return super().editorEvent(event,model,option,index)


class RenderQueuePanel(QWidget):
    def __init__(self,queue,parent=None):
        super().__init__(parent);self.queue=queue;self.setObjectName('renderQueuePanel')
        self.setMinimumWidth(0);self.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
        layout=QVBoxLayout(self);layout.setContentsMargins(10,10,10,10);layout.setSpacing(8)
        row=QHBoxLayout();row.addWidget(QLabel('RENDER QUEUE'));row.addStretch()
        self.pause=QPushButton('Pause queue');self.pause.setCheckable(True);self.pause.toggled.connect(queue.set_paused);row.addWidget(self.pause);layout.addLayout(row)
        self.summary=QLabel();layout.addWidget(self.summary)
        self.collapsed_groups=set()
        self.list=JobFileList();self.list.setObjectName('renderQueueJobs');self.list.setSpacing(4)
        self.list.setItemDelegate(QueueJobDelegate(self.list));self.list.setMinimumWidth(0)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.list.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Expanding)
        self.list.file_action.connect(self.file_action)
        self.list.itemClicked.connect(self.toggle_group)
        self.list.currentItemChanged.connect(lambda *_:self.update_details());layout.addWidget(self.list,1)
        details=QFrame();details.setObjectName('renderJobDetails');body=QVBoxLayout(details);body.setContentsMargins(9,8,9,8);body.setSpacing(5)
        body.addWidget(QLabel('SELECTED RENDER'))
        self.details=QLabel();self.details.setWordWrap(True);self.details.setMinimumWidth(0);body.addWidget(self.details)
        self.progress=QLabel();self.progress.setWordWrap(True);body.addWidget(self.progress);layout.addWidget(details)
        actions=QHBoxLayout();self.cancel=QPushButton('Cancel selected');self.cancel.clicked.connect(self.cancel_selected);actions.addWidget(self.cancel);actions.addStretch();actions.addWidget(QLabel('Local + Cloud'));layout.addLayout(actions)
        self.cleanup=QPushButton('Clean up block videos')
        self.cleanup.setToolTip('Delete intermediate blocks for the selected completed group; keep its blended video.')
        self.cleanup.clicked.connect(self.cleanup_selected)
        layout.addWidget(self.cleanup)
        self.file_status=QLabel();self.file_status.setWordWrap(True);layout.addWidget(self.file_status)
        note=QLabel('Jobs render in order. Pausing holds waiting jobs; the active render continues.');note.setWordWrap(True);note.setMinimumWidth(0);layout.addWidget(note)
        self.setStyleSheet('''#renderQueuePanel{background:#18232d} #renderQueueJobs{background:#101c25;border:1px solid #354a5c;border-radius:4px;padding:5px} #renderJobDetails{background:#15212b;border:1px solid #354a5c;border-radius:4px} #renderJobDetails QLabel{background:transparent;border:0}''')
        queue.changed.connect(self.refresh);self.refresh()

    def selected_job(self):
        item=self.list.currentItem();return item.data(Qt.ItemDataRole.UserRole) if item else None

    def refresh(self):
        selected=self.selected_job();key=selected.id if selected else None
        self.list.blockSignals(True)
        scroll=self.list.verticalScrollBar().value()
        self.list.clear()
        seen=set()
        for job in self.queue.jobs:
            if job.group_id and job.group_id not in seen:
                seen.add(job.group_id)
                group_jobs=[candidate for candidate in self.queue.jobs if candidate.group_id==job.group_id]
                state=next((candidate.state for candidate in group_jobs if candidate.state in ACTIVE_STATES), 'Complete' if all(candidate.state=='Complete' for candidate in group_jobs) else 'Failed' if any(candidate.state=='Failed' for candidate in group_jobs) else 'Cancelled')
                header=RenderJob(('▸ ' if job.group_id in self.collapsed_groups else '▾ ')+job.group_title,
                                 job.project_id,job.source,'','Group',id=job.group_id,group_id=job.group_id,state=state)
                item=QListWidgetItem();item.setData(Qt.ItemDataRole.UserRole,header);self.list.addItem(item)
                if header.id==key:self.list.setCurrentItem(item)
            item=QListWidgetItem();item.setData(Qt.ItemDataRole.UserRole,job);self.list.addItem(item)
            item.setHidden(job.group_id in self.collapsed_groups)
            item.setData(Qt.ItemDataRole.UserRole+1,self.queue.position(job))
            item.setToolTip(job.title+'\n'+job.state+' · '+job.source.title()+'\n'+job.model+'\n'+job.error+
                            ('\nDrag this tile to copy the output file.' if job.output_path else ''))
            if job.id==key:self.list.setCurrentItem(item)
        if key is None and self.list.count():self.list.setCurrentRow(0)
        self.list.verticalScrollBar().setValue(scroll)
        self.list.blockSignals(False);self.list.viewport().update()
        self.summary.setText(f"{sum(j.state in ('Rendering','Cancelling') for j in self.queue.jobs)} rendering · {sum(j.state=='Waiting' for j in self.queue.jobs)} waiting")
        self.pause.blockSignals(True);self.pause.setChecked(self.queue.paused);self.pause.blockSignals(False)
        self.pause.setText('Resume queue' if self.queue.paused else 'Pause queue');self.update_details()

    def update_details(self):
        job=self.selected_job();self.cancel.setEnabled(bool(job and (job.state in ACTIVE_STATES if job.kind!='Group' else any(j.group_id==job.group_id and j.state in ACTIVE_STATES for j in self.queue.jobs))))
        self.cleanup.setEnabled(self.can_cleanup(job))
        if job is None:self.details.setText('No queued renders');self.progress.clear();return
        self.details.setText(job.title+'\n'+job.model+' · '+job.kind+('\n'+job.details if job.details else ''))
        text=job.state+(' · '+f'{job.percent:.0f}%' if job.percent is not None else '')
        self.progress.setText(text+(' · '+job.message if job.message else '')+('\n'+job.error if job.error else ''))

    def cancel_selected(self):
        job=self.selected_job()
        if job:
            if job.group_id:
                for child in tuple(self.queue.jobs):
                    if child.group_id==job.group_id:self.queue.cancel_job(child.id)
            else:self.queue.cancel_job(job.id)

    def toggle_group(self,item):
        job=item.data(Qt.ItemDataRole.UserRole)
        if job.kind!='Group':return
        if job.group_id in self.collapsed_groups:self.collapsed_groups.remove(job.group_id)
        else:self.collapsed_groups.add(job.group_id)
        self.refresh()

    def can_cleanup(self,job):
        if not job or not job.group_id:return False
        jobs=[j for j in self.queue.jobs if j.group_id==job.group_id]
        return (not any(j.state in ACTIVE_STATES and j.kind=='Video' and j.project_id==job.project_id for j in self.queue.jobs)
                and any(not j.intermediate and j.state=='Complete' and j.output_path and Path(j.output_path).is_file() for j in jobs)
                and any(j.intermediate and j.output_path and Path(j.output_path).is_file() for j in jobs))

    def cleanup_selected(self):
        job=self.selected_job()
        if not self.can_cleanup(job):return
        blocks=[j for j in self.queue.jobs if j.group_id==job.group_id and j.intermediate and j.output_path]
        finals={Path(j.output_path).resolve() for j in self.queue.jobs if not j.intermediate and j.output_path}
        paths=[j.output_path for j in blocks if Path(j.output_path).resolve() not in finals]
        if QMessageBox.question(self,'Clean up block videos',
            f'Delete {len(paths)} intermediate block video(s)? The final blended video will be kept. Deleted blocks must be rendered again on the next build.')!=QMessageBox.StandardButton.Yes:return
        self.cleanup.setEnabled(False)
        def remove():
            removed=[]
            for path in paths:
                try:Path(path).unlink(missing_ok=True);removed.append(path)
                except OSError as error:return removed,str(error)
            return removed,''
        def ready(result):
            removed,error=result
            for block in blocks:
                if block.output_path in removed:block.output_path='';block.message='Intermediate video cleaned up'
            self.file_status.setText(error or 'Intermediate block videos removed. Blended video kept.')
            self.queue.changed.emit()
        self.queue.executor(remove,(),ready,lambda error:self.file_status.setText(str(error)))

    def file_action(self,job,action):
        path=Path(job.output_path)
        if not path.is_file():return
        if action=='Folder':
            if sys.platform in {'win32','darwin'}:
                program,args=('explorer.exe',['/select,',str(path)]) if sys.platform=='win32' else ('/usr/bin/open',['-R',str(path)])
                result=QProcess.startDetached(program,args)
                opened=result[0] if isinstance(result,tuple) else result
                if not opened:QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))
                return
            try:
                from PySide6.QtDBus import QDBusConnection,QDBusMessage
                message=QDBusMessage.createMethodCall('org.freedesktop.FileManager1','/org/freedesktop/FileManager1','org.freedesktop.FileManager1','ShowItems')
                message.setArguments([[QUrl.fromLocalFile(str(path)).toString()], ''])
                reply=QDBusConnection.sessionBus().call(message,timeout=1000)
                if reply.type()!=QDBusMessage.MessageType.ErrorMessage:return
            except ImportError:pass
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))
            return
        target,_=QFileDialog.getSaveFileName(self,'Export rendered video',path.name,'Video files (*'+path.suffix+');;All files (*)')
        if not target or Path(target).resolve()==path.resolve():return
        def export():
            import os,shutil,tempfile
            destination=Path(target)
            handle,temporary=tempfile.mkstemp(prefix='.director-export-',suffix=destination.suffix,dir=destination.parent)
            os.close(handle)
            try:shutil.copy2(path,temporary);os.replace(temporary,destination)
            finally:Path(temporary).unlink(missing_ok=True)
            return str(destination)
        self.queue.executor(export,(),lambda result:self.file_status.setText('Exported · '+result),lambda error:self.file_status.setText(str(error)))
