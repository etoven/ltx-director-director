"""Compact queue cards and transient rendering overlays for project thumbnails."""
from math import cos, sin, pi
from pathlib import Path
from PySide6.QtCore import Qt, QRect, QSize, QTimer
from PySide6.QtGui import QColor, QFont, QIcon, QPen
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QPushButton, QSizePolicy, QStyledItemDelegate, QStyle, QVBoxLayout, QWidget)
from .render_queue import ACTIVE_STATES


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


class QueueJobDelegate(QStyledItemDelegate):
    """Paint only inside the list viewport; no oversized row widgets by the scrollbar."""
    def sizeHint(self,option,index):
        return QSize(160,option.fontMetrics.height()*3+26)

    def paint(self,painter,option,index):
        job=index.data(Qt.ItemDataRole.UserRole)
        if job is None:return
        painter.save();painter.setClipRect(option.rect);painter.setRenderHint(painter.RenderHint.Antialiasing)
        card=option.rect.adjusted(1,2,-1,-2)
        selected=bool(option.state & QStyle.StateFlag.State_Selected)
        painter.setPen(QColor('#67b5c7' if selected else '#354858'));painter.setBrush(QColor('#213f4b' if selected else '#1d2b36'))
        painter.drawRoundedRect(card,4,4)
        font=QFont(option.font);painter.setFont(font);fm=painter.fontMetrics();line=fm.height()+4
        text=card.adjusted(10,7,-10,-7)
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
        painter.restore()


class RenderQueuePanel(QWidget):
    def __init__(self,queue,parent=None):
        super().__init__(parent);self.queue=queue;self.setObjectName('renderQueuePanel')
        self.setMinimumWidth(0);self.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
        layout=QVBoxLayout(self);layout.setContentsMargins(10,10,10,10);layout.setSpacing(8)
        row=QHBoxLayout();row.addWidget(QLabel('RENDER QUEUE'));row.addStretch()
        self.pause=QPushButton('Pause queue');self.pause.setCheckable(True);self.pause.toggled.connect(queue.set_paused);row.addWidget(self.pause);layout.addLayout(row)
        self.summary=QLabel();layout.addWidget(self.summary)
        self.list=QListWidget();self.list.setObjectName('renderQueueJobs');self.list.setSpacing(4)
        self.list.setItemDelegate(QueueJobDelegate(self.list));self.list.setMinimumWidth(0)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.list.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Expanding)
        self.list.currentItemChanged.connect(lambda *_:self.update_details());layout.addWidget(self.list,1)
        details=QFrame();details.setObjectName('renderJobDetails');body=QVBoxLayout(details);body.setContentsMargins(9,8,9,8);body.setSpacing(5)
        body.addWidget(QLabel('SELECTED RENDER'))
        self.details=QLabel();self.details.setWordWrap(True);self.details.setMinimumWidth(0);body.addWidget(self.details)
        self.progress=QLabel();self.progress.setWordWrap(True);body.addWidget(self.progress);layout.addWidget(details)
        actions=QHBoxLayout();self.cancel=QPushButton('Cancel selected');self.cancel.clicked.connect(self.cancel_selected);actions.addWidget(self.cancel);actions.addStretch();actions.addWidget(QLabel('Local + Cloud'));layout.addLayout(actions)
        note=QLabel('Jobs render in order. Pausing holds waiting jobs; the active render continues.');note.setWordWrap(True);note.setMinimumWidth(0);layout.addWidget(note)
        self.setStyleSheet('''#renderQueuePanel{background:#18232d} #renderQueueJobs{background:#101c25;border:1px solid #354a5c;border-radius:4px;padding:5px} #renderJobDetails{background:#15212b;border:1px solid #354a5c;border-radius:4px} #renderJobDetails QLabel{background:transparent;border:0}''')
        queue.changed.connect(self.refresh);self.refresh()

    def selected_job(self):
        item=self.list.currentItem();return item.data(Qt.ItemDataRole.UserRole) if item else None

    def refresh(self):
        selected=self.selected_job();key=selected.id if selected else None
        self.list.blockSignals(True)
        existing={self.list.item(i).data(Qt.ItemDataRole.UserRole).id:self.list.item(i) for i in range(self.list.count())}
        ids={j.id for j in self.queue.jobs}
        for id,item in existing.items():
            if id not in ids:self.list.takeItem(self.list.row(item))
        for job in self.queue.jobs:
            item=existing.get(job.id)
            if item is None:item=QListWidgetItem();item.setData(Qt.ItemDataRole.UserRole,job);self.list.addItem(item)
            item.setData(Qt.ItemDataRole.UserRole+1,self.queue.position(job))
            item.setToolTip(job.title+'\n'+job.state+' · '+job.source.title()+'\n'+job.model+'\n'+job.error)
            if job.id==key:self.list.setCurrentItem(item)
        if key is None and self.list.count():self.list.setCurrentRow(0)
        self.list.blockSignals(False);self.list.viewport().update()
        self.summary.setText(f"{sum(j.state in ('Rendering','Cancelling') for j in self.queue.jobs)} rendering · {sum(j.state=='Waiting' for j in self.queue.jobs)} waiting")
        self.pause.blockSignals(True);self.pause.setChecked(self.queue.paused);self.pause.blockSignals(False)
        self.pause.setText('Resume queue' if self.queue.paused else 'Pause queue');self.update_details()

    def update_details(self):
        job=self.selected_job();self.cancel.setEnabled(bool(job and job.state in ACTIVE_STATES))
        if job is None:self.details.setText('No queued renders');self.progress.clear();return
        self.details.setText(job.title+'\n'+job.model+' · '+job.kind+('\n'+job.details if job.details else ''))
        text=job.state+(' · '+f'{job.percent:.0f}%' if job.percent is not None else '')
        self.progress.setText(text+(' · '+job.message if job.message else '')+('\n'+job.error if job.error else ''))

    def cancel_selected(self):
        job=self.selected_job()
        if job:self.queue.cancel_job(job.id)
