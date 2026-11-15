"""V3 studio presentation over Aurora's existing timeline and audio controllers."""
from __future__ import annotations
import math
import re
from importlib.resources import files
from PySide6.QtCore import Qt, QRectF, QSize, QObject, QTimer
from PySide6.QtGui import QColor, QPainter, QPen, QIcon
from PySide6.QtWidgets import (QWidget, QFrame, QLabel, QPushButton, QSizePolicy,
    QHBoxLayout, QVBoxLayout, QScrollArea, QDialog, QFormLayout, QLineEdit,
    QDialogButtonBox, QPlainTextEdit, QLayout)

def icon(name):
    return QIcon(str(files('ltx_prompt_director').joinpath(f'assets/studio-{name}.svg')))


def dialogue_span(prompt):
    start = re.search(r'(?:Spoken Dialog(?:ue)?|Dialogue|Speaker[^:]*):', prompt, re.I)
    if not start and not prompt.lstrip().startswith(('"', '“')):
        return None
    return re.search(r'["“]([^"”]+)["”]', prompt[start.end():] if start else prompt)


def dialogue(prompt):
    match = dialogue_span(prompt)
    return match.group(1) if match else ''


def snap(owner, value, scale=None, exclude_audio=None):
    value = max(0., value)
    if not hasattr(owner, 'studio_snap') or not owner.studio_snap.isChecked():
        return value
    cursor = 0.
    targets = [0., owner.audio_panel.transport.position / 1000]
    for segment in owner.segments:
        cursor += segment.duration
        targets.append(cursor)
    for clip in owner.audio_panel.clips:
        if clip.id != exclude_audio:
            targets.extend((clip.start_frame / 24, clip.end_frame / 24))
    targets.extend(float(n) for n in range(max(0, math.floor(value)-1), math.floor(value)+3))
    closest = min(targets, key=lambda target: abs(value-target))
    return closest if abs(closest-value)*max(.001, scale or owner.pixels_per_second) <= 8 else value


class ElidedLabel(QLabel):
    """Keep long clip text inside its assigned duration, with full text on hover."""
    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self.full_text = ""
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setText(text)

    def setText(self, text):
        self.full_text = text
        self.setToolTip(text)
        self._elide()

    def _elide(self):
        super().setText(self.fontMetrics().elidedText(self.full_text, Qt.TextElideMode.ElideRight, max(1, self.width() - 10)))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()


class TransportPlayButton(QPushButton):
    """Draw a circular transport control independently of platform button themes."""
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#1c1d25"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#7465ff" if self.underMouse() else "#5546e8"))
        side = min(self.width(), self.height()) - 1
        painter.drawEllipse(QRectF((self.width() - side) / 2, (self.height() - side) / 2, side, side))
        self.icon().paint(painter, (self.width() - 14) // 2, (self.height() - 14) // 2, 14, 14)


STUDIO_STYLE = """
QWidget{background:#131517;font:12px Arial}
QMainWindow,#directorWorkspace{background:#131517;color:#d7dce8}
#mainToolbar{background:#1e1f27;border-bottom:1px solid #30313d;padding:3px}
#studioBrand{color:#818cf8;font-weight:bold;font-size:13px;background:transparent;padding:0 8px}
#studioProject{color:#9daec9;background:transparent;padding:0 10px}
#studioToolbarSpacer{background:transparent}
#studioWorkspace,#studioStack{background:#131517}
#studioScroll{background:#131517;border:0}
#studioTransport{background:#1c1d25;border-bottom:1px solid #30313d}
#studioTransport QLabel{background:transparent;color:#8190ad}
#studioTransport QPushButton{min-height:22px}
#studioTransport QSlider::groove:horizontal{height:3px;background:#d1d5db;border:0;border-radius:1px}
#studioTransport QSlider::sub-page:horizontal{background:#6366f1;border:0;border-radius:1px}
#studioTransport QSlider::handle:horizontal{width:12px;margin:-5px 0;background:#6366f1;border:0;border-radius:6px}
#studioPlay{background:#5546e8;color:white;border:0;border-radius:14px;min-width:26px;min-height:26px;padding:0}
#studioTransportButton{background:transparent;border:0;color:#96a8c4;min-width:20px;padding:0}
#studioSnap{background:#181a20;border:1px solid #343544;color:#a5b4fc;padding:2px 8px}
#studioSnap:checked{background:#24233e;color:#b8c2ff;border-color:#635bf2}
#studioActionRail{background:#1c1e26;border-left:1px solid #303444}
#studioActionButton{background:#102a3b;border:1px solid #0ea5e9;color:#7dd3fc;border-radius:5px;padding:0;font-weight:bold}
#studioActionButton:hover{background:#164363;border-color:#7dd3fc;color:white}
#studioActionButton:disabled{color:#4a5d6a;border-color:#334155;background:#1b222a}
#studioPrimary{background:#5142e8;border:1px solid #6e64ff;color:white;font-weight:bold}
#studioAudioButton{background:#113337;border:1px solid #176264;color:#67e8dd}
#studioVisualButton{background:#282b37;border:1px solid #3b3e4e;color:#cbd5ed}
#timelineShell,#studioVisualPanel{background:#1b1d23;border:1px solid #303343;border-radius:8px}
#studioTrackHeader{background:#202229;border-bottom:1px solid #303343}
#studioTrackTitle{color:#34d399;background:transparent;font-weight:bold}
#studioTrackRange{background:#0b302a;border:1px solid #12634e;color:#6ee7b7;padding:2px 7px;border-radius:3px}
#studioTrackToggle{background:transparent;border:0;padding:0;min-height:16px;min-width:16px;max-width:22px;color:#94a3b8}
#studioTrackToggle:checked{background:#263344}
#timeline{background:#1b1d23;border:0;padding:0;outline:0}
#timeline::item,#timeline::item:selected,#timeline::item:focus{background:transparent;border:0;outline:0}
#segmentCard{background:#22252d;border:2px solid #10b981;border-radius:6px}
#segmentCard[kind="text"]{border-color:#0ea5e9}
#segmentCard[selected="true"]{background:#252a34;border:2px solid #6ee7b7}
#segmentCard[kind="text"][selected="true"]{border-color:#7dd3fc}
#segmentCardBody{background:transparent;border:0}
#studioClipName{background:transparent;color:#d7e4ee;font-weight:bold}
#studioClipPrompt{background:#181b20;border:0;border-radius:3px;color:#c9d8ed;padding:7px}
#segmentCard[kind="text"] #studioClipPrompt{font-style:italic}
#studioClipQuote{background:transparent;color:#96a9c3;font-style:italic}
#segmentPreview{background:#15171c;border:0;border-radius:4px;color:#78bddb}
#studioClipTime,#roleBadge{background:#0c2b28;border:1px solid #0e5e49;color:#6ee7b7;border-radius:3px;padding:2px 5px;font-size:9px}
#segmentCard[kind="text"] #studioClipTime{background:#102d44;border-color:#075985;color:#7dd3fc}
#tileDuration,#tileStartTime{background:transparent;color:#7dd3fc;font-size:9px}
#tileDelete{background:transparent;color:#8190a3;min-height:0;padding:0;border:0}
#resizeHandle{background:transparent;border:0}
#studioDialoguePanel{background:#1b1d24;border:1px solid #3c3565;border-radius:8px}
#studioDialogueTitle{color:#818cf8;background:transparent;font-weight:bold}
#studioDialogueBadge,#studioSpeaker{background:#282344;color:#a5b4fc;border:1px solid #443879;border-radius:3px;padding:2px 6px;font-size:10px}
#studioDialogueCue{background:#22242d;border:1px solid #3f3b66;border-radius:5px}
#studioDialogueText{background:transparent;color:#c5d3ed;font-style:italic}
#studioTime{background:transparent;color:#94a3b8;font-size:9px}
#studioAudioPanel{background:#191d22;border:1px solid #185755;border-radius:8px}
#studioAudioTitle{background:transparent;color:#2dd4bf;font-weight:bold}
#studioAudioBadge{background:#153a38;border:1px solid #176360;color:#5eead4;border-radius:3px;padding:2px 6px;font-size:9px}
#studioAudioBody,#studioAudioRow,#studioWaveform,#studioDialoguePanel QScrollArea,#studioDialoguePanel QScrollArea>QWidget>QWidget{background:transparent}
#studioFilter{background:#282b37;border:1px solid #343846;border-radius:3px;color:#8f9cb7;padding:2px 7px;font-size:10px;min-height:17px}
#studioFilter:checked{background:#153a38;color:#5eead4;border-color:#187b73}
#studioMuteSolo{background:#2b303c;border:0;border-radius:3px;color:#94a3b8;padding:0;min-height:0;font-size:9px}
#studioMuteSolo:checked{background:#155e59;color:#99f6e4}
#studioSubtleButton{background:transparent;border:0;color:#94a3b8;padding:2px 4px;min-height:15px}
#studioEmpty{color:#78859d;background:transparent}
"""


def style_card(card):
    """Rearrange existing controls; retain artwork, diff, review and resize objects."""
    card.setProperty('kind', card.segment.kind)
    card.outer_layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
    layout = card.content.layout()
    layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
    parts = [layout.takeAt(0) for _ in range(layout.count())]
    badges, old_title, timing = parts[0].layout(), parts[2].widget(), parts[3].layout()
    old_title.hide()
    kind = badges.itemAt(0).widget()
    kind.setText('A  Text' if card.segment.kind == 'text' else card.segment.name)
    kind.setObjectName('studioClipName')
    kind.setMinimumWidth(0)
    kind.setMinimumWidth(70)
    kind.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    badges.removeWidget(card.role_badge)
    timing.removeWidget(card.start_time_label)
    badges.addWidget(card.start_time_label)
    card.start_time_label.setObjectName('studioClipTime')
    card.role_badge.setText((card.segment.media_path.rsplit('.',1)[-1].upper() if '.' in card.segment.media_path else 'IMAGE') if card.segment.kind != 'text' else 'TEXT')
    card.role_badge.setVisible(card.segment.kind != 'text')
    timing.addWidget(card.role_badge)
    quote = ElidedLabel(dialogue(card.segment.prompt))
    quote.setObjectName('studioClipQuote')
    quote.setVisible(card.segment.kind != 'text')
    timing.insertWidget(0, quote, 1)
    card.studio_quote = quote
    details = QVBoxLayout(); details.setContentsMargins(0,0,0,0); details.setSpacing(5)
    details.addLayout(badges)
    if card.segment.kind == 'text':
        card.preview.setWordWrap(False)
        card.preview.setMargin(7)
        card.preview.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        details.addWidget(card.preview, 1)
    else:
        card.studio_prompt = ElidedLabel(card.segment.prompt or 'Enter a prompt…')
        card.studio_prompt.setObjectName('studioClipPrompt')
        card.studio_prompt.setMaximumHeight(44)
        details.addWidget(card.studio_prompt, 1)
    details.addLayout(timing)
    row = QHBoxLayout(); row.setContentsMargins(3,3,3,3); row.setSpacing(12)
    if card.segment.kind != 'text':
        card.preview.setFixedWidth(174)
        row.addWidget(card.preview)
    row.addLayout(details,1)
    layout.setContentsMargins(5,5,5,5)
    layout.addLayout(row)
    card.studio_ready = True
    card.setMinimumWidth(0)
    card.content.setMinimumWidth(0)
    card.update_layout(80, card.resize_handle.pixels_per_second)


class DialoguePanel(QFrame):
    def __init__(self, owner):
        super().__init__(); self.owner=owner; self.signature=None
        self.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Fixed)
        self.setObjectName('studioDialoguePanel')
        layout=QVBoxLayout(self);layout.setContentsMargins(12,7,12,10)
        header=QHBoxLayout();title=QLabel('◉  DIALOGUE & SUBTITLES');title.setObjectName('studioDialogueTitle')
        header.addWidget(title);header.addStretch();self.count=QLabel('0 Lines Active');self.count.setObjectName('studioDialogueBadge');header.addWidget(self.count)
        add=QPushButton('+ Add Cue');add.setObjectName('studioSubtleButton');add.clicked.connect(self.add_cue);header.addWidget(add);layout.addLayout(header)
        self.scroll=QScrollArea();self.scroll.setFrameShape(QFrame.Shape.NoFrame);self.scroll.setFixedHeight(72)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff);self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.content=QWidget();self.row=QHBoxLayout(self.content);self.row.setContentsMargins(0,0,0,0);self.row.setSpacing(6);self.scroll.setWidget(self.content)
        layout.addWidget(self.scroll)

    def refresh(self):
        owner=self.owner;widths=owner.timeline_item_widths()
        signature=tuple((s.id,s.duration,s.prompt) for s in owner.segments),tuple(widths)
        if signature!=self.signature:
            self.signature=signature
            while self.row.count():
                item=self.row.takeAt(0)
                if item.widget():item.widget().deleteLater()
            cursor=0.;active=0
            for index,(segment,width) in enumerate(zip(owner.segments,widths)):
                words=dialogue(segment.prompt);active+=bool(words)
                cue=QFrame();cue.setObjectName('studioDialogueCue');cue.setFixedWidth(max(1,width-6));box=QVBoxLayout(cue);box.setContentsMargins(8,5,8,5)
                top=QHBoxLayout();speaker=ElidedLabel(f'SPEAKER {index+1}' if words else 'NO DIALOGUE');speaker.setObjectName('studioSpeaker');speaker.setFixedWidth(105)
                top.addWidget(speaker);top.addStretch();stamp=QLabel(f'{cursor:.2f}s – {cursor+segment.duration:.2f}s');stamp.setObjectName('studioTime');top.addWidget(stamp);box.addLayout(top)
                text=ElidedLabel(words or 'Double-click to add dialogue');text.setObjectName('studioDialogueText');box.addWidget(text)
                cue.mouseDoubleClickEvent=lambda event,ident=segment.id:self.edit(ident)
                self.row.addWidget(cue);cursor+=segment.duration
            if not owner.segments:self.row.addWidget(QLabel('Add a visual or text clip to place timed dialogue.'))
            self.content.resize(max(self.scroll.viewport().width(),sum(widths)),70)
            self.count.setText(f'{active} Lines Active')
        self.scroll.horizontalScrollBar().setValue(owner.timeline.horizontalScrollBar().value())

    def add_cue(self):
        if not self.owner.segments:self.owner.add_text_segment()
        if self.owner.segments:self.edit(self.owner.current_segment().id if self.owner.current_segment() else self.owner.segments[0].id)

    def edit(self, ident):
        owner=self.owner;segment=next((s for s in owner.segments if s.id==ident),None)
        if not segment or owner.ai_busy:return
        dialog=QDialog(self);dialog.setWindowTitle('Edit dialogue cue');dialog.resize(560,230)
        form=QFormLayout(dialog);text=QPlainTextEdit(dialogue(segment.prompt));form.addRow('Spoken words',text)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel);buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);form.addRow(buttons)
        if dialog.exec()==QDialog.DialogCode.Accepted:
            original=segment.prompt;start=re.search(r'(?:Spoken Dialog(?:ue)?|Dialogue|Speaker[^:]*):',original,re.I)
            offset=start.end() if start else 0;match=dialogue_span(original)
            words=text.toPlainText().strip()
            if match:segment.prompt=original[:offset+match.start(1)]+words+original[offset+match.end(1):]
            elif words:segment.prompt=original+'\nSpoken Dialog: "'+words+'"'
            owner.mark_dirty();owner.refresh_timeline(owner.segments.index(segment))
            owner.refresh_segment_prompt_box(owner.timeline.currentRow())


class StudioController(QObject):
    def __init__(self, owner, root, outer, timeline_layout, ruler_row, track_row):
        super().__init__(owner);self.owner=owner
        owner.studio=self
        self.transport=QFrame();self.transport.setObjectName('studioTransport');self.transport.setFixedHeight(38);bar=QHBoxLayout(self.transport);bar.setContentsMargins(4,4,8,4);bar.setSpacing(6)
        for name,previous in [('previous',True),('play',None),('next',False)]:
            button=TransportPlayButton() if previous is None else QPushButton()
            button.setObjectName('studioPlay' if previous is None else 'studioTransportButton');button.setIcon(icon(name));button.setIconSize(QSize(14,14));button.setFixedSize(28,28)
            button.setAccessibleName(name.title());button.setToolTip('Play / pause sequence' if previous is None else name.title()+' clip boundary')
            button.clicked.connect(owner.audio_panel.toggle_play if previous is None else lambda checked,p=previous:self.jump(p))
            bar.addWidget(button)
            if previous is None:self.play=button
        self.time=QLabel('00:00.00 / 00:00.00');bar.addWidget(self.time);bar.addStretch()
        owner.studio_snap=QPushButton('Snap: On');owner.studio_snap.setObjectName('studioSnap');owner.studio_snap.setIcon(icon('snap'));owner.studio_snap.setCheckable(True)
        owner.studio_snap.setChecked(owner.settings.value('timeline_snap',True,bool));owner.studio_snap.toggled.connect(self.set_snap);self.set_snap(owner.studio_snap.isChecked());bar.addWidget(owner.studio_snap)
        owner.ruler.snapper=lambda value:snap(owner,value)
        # Move the existing, tested zoom and fit controls into the transport row.
        ruler_row.removeWidget(owner.timeline_navigation);bar.addWidget(owner.timeline_navigation);owner.timeline_navigation.show()
        owner.timeline_navigation.setFixedWidth(220)
        timeline_layout.removeItem(ruler_row);outer.insertWidget(0,self.transport);outer.insertLayout(1,ruler_row)
        owner.ruler_spacer.setFixedWidth(20);ruler_row.setSpacing(0);ruler_row.addSpacing(56)
        track_row.removeWidget(owner.main_track_label);owner.main_track_label.hide();track_row.setContentsMargins(12,10,12,0)
        header=QFrame();header.setObjectName('studioTrackHeader');header_row=QHBoxLayout(header);header_row.setContentsMargins(12,7,12,7)
        title=QLabel('▦  TRACK V1: VISUAL & PROMPTS');title.setObjectName('studioTrackTitle');header_row.addWidget(title);header_row.addStretch();self.range=QLabel('0.00s – 0.00s');self.range.setObjectName('studioTrackRange');header_row.addWidget(self.range)
        timeline_layout.insertWidget(0,header)
        self.dialogue=DialoguePanel(owner)
        stack=QWidget();stack.setObjectName('studioStack');stack_layout=QVBoxLayout(stack);stack_layout.setContentsMargins(0,0,0,0);stack_layout.setSpacing(12)
        for widget in (owner.timeline_shell,owner.audio_panel):outer.removeWidget(widget)
        stack_layout.addWidget(owner.timeline_shell);stack_layout.addWidget(self.dialogue);stack_layout.addWidget(owner.audio_panel)
        owner.audio_main_layout=stack_layout
        workspace=QWidget();workspace.setObjectName('studioWorkspace');row=QHBoxLayout(workspace);row.setContentsMargins(7,10,0,0);row.setSpacing(12);row.addWidget(stack,1)
        track_row.removeWidget(owner.add_tile_wrap);owner.add_tile_wrap.setObjectName('studioActionRail');row.addWidget(owner.add_tile_wrap,0,Qt.AlignmentFlag.AlignTop)
        for button in (owner.add_tile,owner.add_text_tile,owner.sync_timeline_button,owner.retime_all_button,owner.add_extension_tile):button.setObjectName('studioActionButton')
        outer.insertWidget(2,workspace);outer.setContentsMargins(0,0,0,0);outer.setSpacing(0)
        owner.audio_panel.transport.playing_changed.connect(lambda playing:self.play.setIcon(icon('pause' if playing else 'play')))
        owner.audio_panel.transport.position_changed.connect(self.position_changed)
        owner.timeline.horizontalScrollBar().valueChanged.connect(lambda _:self.dialogue.refresh())
        self.refresh()

    def set_snap(self, enabled):
        self.owner.studio_snap.setText('Snap: On' if enabled else 'Snap: Off');self.owner.settings.setValue('timeline_snap',enabled)

    def jump(self, previous):
        owner=self.owner;position=owner.audio_panel.transport.position/1000;boundaries=[0.]
        for segment in owner.segments:boundaries.append(boundaries[-1]+segment.duration)
        candidates=[x for x in boundaries if x<position-.01] if previous else [x for x in boundaries if x>position+.01]
        value=(max(candidates) if previous else min(candidates)) if candidates else (0 if previous else boundaries[-1])
        owner.audio_panel.seek(round(value*1000))

    def position_changed(self, position):
        total=max(self.owner.total_duration(),self.owner.audio_panel.transport.duration/1000)
        self.time.setText(f'{int(position/60000):02d}:{position/1000%60:05.2f} / {int(total/60):02d}:{total%60:05.2f}')

    def refresh(self):
        self.dialogue.setVisible(self.owner.workspace_engine!='shot_sequence' and not self.owner.prompt_only_workspace)
        self.range.setText(f'0.00s – {self.owner.total_duration():.2f}s')
        self.dialogue.refresh();self.position_changed(self.owner.audio_panel.transport.position)
        for index in range(self.owner.timeline.count()):
            card=self.owner.timeline.itemWidget(self.owner.timeline.item(index))
            if hasattr(card,'studio_prompt'):card.studio_prompt.setText(card.segment.prompt)
            if hasattr(card,'studio_quote'):card.studio_quote.setText(dialogue(card.segment.prompt))


STUDIO_STYLE += '''
#timelineShell{background:#1b1d23;border:1px solid #303343;border-radius:8px}
#studioActionRail{background:#1c1e26;border-left:1px solid #303444}
#audioTimelinePanel{background:#191d22;border:1px solid #185755;border-radius:8px}
#mainTrackLabel{background:transparent}
#studioDialoguePanel QLabel{background:transparent}
#studioDialoguePanel #studioSpeaker{background:#282344}
#studioClipTime{font-size:9px}
'''


def paint_audio(canvas, event):
    panel=canvas.panel;p=QPainter(canvas);p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.fillRect(event.rect(),QColor('#191d22'))
    left,scale=panel.left,panel.scale;visible=panel.scroll.horizontalScrollBar().value();right=visible+panel.scroll.viewport().width()
    colors=('#2dd4bf','#c084fc','#fbbf24');lanes=max(3,max((clip.lane+1 for clip in panel.clips),default=0))
    solo=any(getattr(c,'solo',False) for c in panel.clips)
    for lane in range(lanes):
        y=canvas.TOP+lane*canvas.ROW;color=QColor(colors[lane%3]);p.setPen(color);p.drawText(5,y+28,f'A{lane+1}')
        for x,label,key in ((30,'M','muted'),(51,'S','solo')):
            active=any(getattr(c,key,False) for c in panel.clips if c.lane==lane)
            p.setPen(Qt.PenStyle.NoPen);p.setBrush(QColor('#155e59' if active else '#2b303c'));p.drawRoundedRect(QRectF(x,y+15,17,18),3,3)
            p.setPen(QColor('#a5dcd6' if active else '#94a3b8'));p.drawText(QRectF(x,y+15,17,18),Qt.AlignmentFlag.AlignCenter,label)
    for clip in panel.clips:
        if getattr(panel,'studio_filter',None) is not None and clip.lane%3!=panel.studio_filter:continue
        rect=canvas.clip_rect(clip)
        if rect.right()<visible or rect.left()>right:continue
        color=QColor(colors[clip.lane%3]);muted=clip.muted or (solo and not clip.solo) or not panel.enabled.isChecked()
        if muted:color=QColor('#64748b')
        fill=QColor(color);fill.setAlpha(24)
        p.setPen(QPen(color,2 if clip.id==panel.selected else 1.5));p.setBrush(fill);p.drawRoundedRect(rect.adjusted(1,1,-1,-1),5,5)
        p.save();p.setClipRect(rect.adjusted(8,2,-8,-2));middle=round(rect.center().y())
        p.setPen(QPen(QColor(color.red(),color.green(),color.blue(),110),1));p.drawLine(round(rect.left()),middle,round(rect.right()),middle)
        p.setPen(QPen(color,1.5));normalizer=max(.001,max(clip.peaks,default=1))
        for x in range(max(int(rect.left())+12,visible),min(int(rect.right())-8,right),38):
            local=(x-rect.left())/scale;index=int((clip.trim_start/24+local)/clip.source_duration*len(clip.peaks))
            peak=clip.peaks[min(len(clip.peaks)-1,max(0,index))] if clip.peaks else 0
            height=max(1,min(1,peak/normalizer)*(rect.height()/2-7));p.drawLine(QRectF(x,middle-height,0,height*2).topLeft(),QRectF(x,middle-height,0,height*2).bottomLeft())
        p.restore();p.setPen(Qt.PenStyle.NoPen);p.setBrush(color)
        p.drawRoundedRect(QRectF(rect.left(),rect.top(),6,rect.height()),3,3);p.drawRoundedRect(QRectF(rect.right()-6,rect.top(),6,rect.height()),3,3)
        p.setPen(color);p.drawText(rect.adjusted(12,0,-12,0),Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter,f'{clip.duration_frames/24:.2f}s')
    if not panel.clips:
        p.setPen(QColor('#78859d'));p.drawText(QRectF(left+12,canvas.TOP,canvas.width()-left-25,80),Qt.AlignmentFlag.AlignVCenter,'Add dialogue, ambience / SFX, or music to display decoded waveforms.')


def style_audio(panel, header, row):
    """Keep AudioTimelinePanel, AudioTransport, AudioClip and archive/export routes."""
    panel.canvas.TOP=2
    panel.studio_filter=None
    panel.play.hide();panel.time.hide();panel.summary.hide()
    title=next((w for w in header.findChildren(QLabel) if w.text()=='AUDIO'),None)
    if title:title.setText('☷  AUDIO TRACKS');title.setObjectName('studioAudioTitle')
    panel.enabled.setText('')
    row.insertStretch(3,1)
    panel.add.setText('+ Track');panel.add.setObjectName('studioAudioButton')
    # Existing source-range fields remain available on demand.
    properties=QPushButton('Properties');properties.setObjectName('studioSubtleButton');properties.setCheckable(True)
    properties.toggled.connect(panel.edit_controls.setVisible);row.addWidget(properties)
    panel.edit_controls.hide()
    for label,lane in (('All',None),('SFX',1),('Dialog',0),('Music',2)):
        button=QPushButton(label);button.setObjectName('studioFilter');button.setCheckable(True);button.setChecked(lane is None)
        def changed(checked,key=lane):
            panel.studio_filter=key
            for child in header.findChildren(QPushButton):
                if child.objectName()=='studioFilter':child.setChecked(child.property('lane')==key)
            panel.canvas.update()
        button.setProperty('lane',lane);button.clicked.connect(changed);row.addWidget(button)
    from PySide6.QtWidgets import QSlider
    volume=QSlider(Qt.Orientation.Horizontal);volume.setRange(0,100);volume.setValue(80);volume.setFixedWidth(80);volume.setToolTip('Audio monitoring volume')
    volume.valueChanged.connect(lambda value:panel.transport.set_volume(value/100));row.addWidget(volume)
    row.removeWidget(panel.add);row.addWidget(panel.add)
    row.setContentsMargins(12,7,12,7)
    # Three native 52px lanes; preserve saved heights when the user changes them.
    if not panel.owner.settings.contains('audio_panel_height'):panel.set_body_height(166)
