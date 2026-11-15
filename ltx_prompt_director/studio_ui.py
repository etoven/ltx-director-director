"""V3 studio presentation over Aurora's existing timeline and audio controllers."""
from __future__ import annotations
import math
import re
from importlib.resources import files
from PySide6.QtCore import Qt, QRectF, QSize, QObject, QTimer
from PySide6.QtGui import QColor, QPainter, QPen, QIcon
from PySide6.QtWidgets import (QWidget, QFrame, QLabel, QPushButton, QSizePolicy,
    QHBoxLayout, QVBoxLayout, QScrollArea, QDialog, QFormLayout, QLineEdit,
    QDialogButtonBox, QPlainTextEdit, QLayout, QScrollBar)

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
    durations=owner.grouped_sequence.display_durations() if hasattr(owner,'grouped_sequence') else [s.duration for s in owner.segments]
    for duration in durations:
        cursor += duration
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
    """Keep the original stacked card controls and all their editing signals."""
    card.setProperty('kind',card.segment.kind)
    card.outer_layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
    card.content.layout().setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
    card.content.layout().setContentsMargins(2,2,2,2)
    card.content.layout().setSpacing(1)
    card.setMinimumWidth(0);card.content.setMinimumWidth(0)


class DialoguePanel(QFrame):
    def __init__(self, owner):
        super().__init__(); self.owner=owner; self.signature=None
        self.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Fixed)
        self.setObjectName('studioDialoguePanel')
        layout=QVBoxLayout(self);layout.setContentsMargins(0,1,0,1)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
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
        owner.main_track_label.show();track_row.setContentsMargins(0,1,0,1)
        header=QFrame();header.setObjectName('studioTrackHeader');header_row=QHBoxLayout(header);header_row.setContentsMargins(12,7,12,7)
        title=QLabel('▦  TRACK V1: VISUAL & PROMPTS');title.setObjectName('studioTrackTitle');header_row.addWidget(title);header_row.addStretch();self.range=QLabel('0.00s – 0.00s');self.range.setObjectName('studioTrackRange');header_row.addWidget(self.range)
        timeline_layout.insertWidget(0,header);header.hide()
        self.dialogue=DialoguePanel(owner)
        stack=QWidget();stack.setObjectName('studioStack');stack_layout=QVBoxLayout(stack);stack_layout.setContentsMargins(0,0,0,0);stack_layout.setSpacing(0)
        stack_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        stack.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Minimum)
        owner.timeline_shell.layout().setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        for widget in (owner.timeline_shell,owner.audio_panel):outer.removeWidget(widget)
        stack_layout.addWidget(owner.timeline_shell);stack_layout.addWidget(self.dialogue);stack_layout.addWidget(owner.audio_panel)
        owner.audio_main_layout=stack_layout
        from .grouped_sequence import CacheCoverage
        warning=QFrame();warning.setObjectName('keyframesAudioWarning')
        warning.setStyleSheet('#keyframesAudioWarning{background:#4b401c;color:#ffe6a1;border:0;border-radius:0;}')
        warning_row=QHBoxLayout(warning);warning_row.setContentsMargins(7,1,7,1)
        warning_row.addWidget(QLabel('Audio will not extend properly between groups in keyframes mode.'))
        dismiss=QPushButton('×');dismiss.setFixedWidth(24)
        def dismiss_warning():
            owner.grouped_sequence.data['audioWarningDismissed']=True
            warning.hide();owner.mark_dirty()
        dismiss.clicked.connect(dismiss_warning);warning_row.addWidget(dismiss)
        owner.keyframes_audio_warning=warning;warning.hide();stack_layout.insertWidget(0,warning)
        # One bottom scrollbar owns horizontal navigation for all lanes.
        owner.timeline_cache_coverage=CacheCoverage(root);outer.addWidget(owner.timeline_cache_coverage)
        owner.timeline_bottom_scroll=QScrollBar(Qt.Orientation.Horizontal,root);outer.addWidget(owner.timeline_bottom_scroll)
        self.sync_scrollbar=lambda: self._sync_scrollbar()
        for bar in (owner.timeline.horizontalScrollBar(),owner.shot_sequence_panel.shots.horizontalScrollBar()):
            bar.rangeChanged.connect(lambda *args:self._sync_scrollbar())
            bar.valueChanged.connect(lambda value:self._sync_scrollbar())
        def scroll(value):
            bar=owner.shot_sequence_panel.shots.horizontalScrollBar() if owner.workspace_engine=='shot_sequence' else owner.timeline.horizontalScrollBar()
            bar.setValue(value)
            owner.audio_panel.scroll.horizontalScrollBar().setValue(value)
            owner.conditioning_guide.horizontalScrollBar().setValue(value)
            self.dialogue.scroll.horizontalScrollBar().setValue(value)
        owner.timeline_bottom_scroll.valueChanged.connect(scroll)
        owner.shot_sequence_panel.shots.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        owner.timeline.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        owner.timeline.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        owner.conditioning_guide.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        owner.audio_panel.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        for panel in (owner.timeline_shell,self.dialogue,owner.audio_panel,owner.conditioning_guide):
            panel.setStyleSheet(panel.styleSheet()+'\n'+ '#'+panel.objectName()+'{border-radius:0;padding:0;margin-top:1px;margin-bottom:1px;}')

        workspace=QWidget();workspace.setObjectName('studioWorkspace');row=QHBoxLayout(workspace);row.setContentsMargins(7,1,0,1);row.setSpacing(12);row.addWidget(stack,1)
        row.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        workspace.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Minimum)
        track_row.removeWidget(owner.add_tile_wrap);owner.add_tile_wrap.setObjectName('studioActionRail');row.addWidget(owner.add_tile_wrap,0,Qt.AlignmentFlag.AlignTop)
        for button in (owner.add_tile,owner.add_text_tile,owner.sync_timeline_button,owner.retime_all_button,owner.add_extension_tile):button.setObjectName('studioActionButton')
        outer.insertWidget(2,workspace);outer.setContentsMargins(0,0,0,0);outer.setSpacing(0)
        owner.audio_panel.transport.playing_changed.connect(lambda playing:self.play.setIcon(icon('pause' if playing else 'play')))
        owner.audio_panel.transport.position_changed.connect(self.position_changed)
        owner.timeline.horizontalScrollBar().valueChanged.connect(lambda _:self.dialogue.refresh())
        from .timeline_sections import TimelineSections
        owner.timeline_sections=TimelineSections(owner,outer,workspace,stack,stack_layout)
        self.refresh()

    def _sync_scrollbar(self):
        owner=self.owner
        bar=owner.shot_sequence_panel.shots.horizontalScrollBar() if owner.workspace_engine=='shot_sequence' else owner.timeline.horizontalScrollBar()
        bottom=owner.timeline_bottom_scroll
        bottom.blockSignals(True)
        bottom.setRange(bar.minimum(),bar.maximum());bottom.setPageStep(bar.pageStep());bottom.setValue(bar.value())
        bottom.blockSignals(False)

    def set_snap(self, enabled):
        self.owner.studio_snap.setText('Snap: On' if enabled else 'Snap: Off');self.owner.settings.setValue('timeline_snap',enabled)

    def jump(self, previous):
        owner=self.owner;position=owner.audio_panel.transport.position/1000;boundaries=[0.]
        for segment in owner.segments:boundaries.append(boundaries[-1]+segment.duration)
        candidates=[x for x in boundaries if x<position-.01] if previous else [x for x in boundaries if x>position+.01]
        value=(max(candidates) if previous else min(candidates)) if candidates else (0 if previous else boundaries[-1])
        owner.audio_panel.seek(round(value*1000))

    def position_changed(self, position):
        total=sum(self.owner.grouped_sequence.display_durations()) if hasattr(self.owner,'grouped_sequence') and self.owner.grouped_sequence.enabled else max(self.owner.total_duration(),self.owner.audio_panel.transport.duration/1000)
        if self.owner.workspace_engine=='shot_sequence':total=self.owner.shot_sequence_panel.shots.total
        self.time.setText(f'{int(position/60000):02d}:{position/1000%60:05.2f} / {int(total/60):02d}:{total%60:05.2f}')

    def refresh(self):
        self.dialogue.setVisible(self.owner.workspace_engine!='shot_sequence' and not self.owner.prompt_only_workspace)
        length=sum(self.owner.grouped_sequence.display_durations()) if hasattr(self.owner,'grouped_sequence') and self.owner.grouped_sequence.enabled else self.owner.total_duration()
        self.range.setText(f'0.00s – {length:.2f}s')
        self.dialogue.refresh();self.position_changed(self.owner.audio_panel.transport.position)
        if hasattr(self.owner,'timeline_bottom_scroll'):self._sync_scrollbar()
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
    p.fillRect(event.rect(),QColor('#141e23'))
    left,scale=panel.left,panel.scale;visible=panel.scroll.horizontalScrollBar().value();right=visible+panel.scroll.viewport().width()
    colors=('#2dd4bf','#c084fc','#fbbf24');lanes=max(3,max((clip.lane+1 for clip in panel.clips),default=0))
    # Reference-style ruler and alternating audio lanes; existing hit targets stay.
    p.fillRect(QRectF(visible,0,right-visible,canvas.TOP),QColor('#111b20'))
    for lane in range(lanes):
        p.fillRect(QRectF(visible,canvas.TOP+lane*canvas.ROW,right-visible,canvas.ROW),QColor('#19262d' if lane%2==0 else '#142027'))
    step=1
    while step*scale<48:step+=1
    for second in range(max(0,math.floor((visible-left)/scale/step)*step),max(1,math.ceil((right-left)/scale))+1,step):
        x=left+second*scale
        p.setPen(QColor('#2c3d46'));p.drawLine(QRectF(x,canvas.TOP,0,canvas.height()-canvas.TOP).topLeft(),QRectF(x,canvas.TOP,0,canvas.height()-canvas.TOP).bottomLeft())
        p.setPen(QColor('#85a6b6'));p.drawText(QRectF(x+3,0,45,canvas.TOP),Qt.AlignmentFlag.AlignVCenter,f'{second}s')
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
    position=left+panel.transport.position/1000*scale
    p.setPen(QPen(QColor('#f2c96b'),1));p.drawLine(QRectF(position,0,0,canvas.height()).topLeft(),QRectF(position,0,0,canvas.height()).bottomLeft())
    p.setBrush(QColor('#f2c96b'));p.drawEllipse(QRectF(position-4,2,8,8))
    if not panel.clips:
        p.setPen(QColor('#78859d'));p.drawText(QRectF(left+12,canvas.TOP,canvas.width()-left-25,80),Qt.AlignmentFlag.AlignVCenter,'Add dialogue, ambience / SFX, or music to display decoded waveforms.')


def style_audio(panel, header, row):
    """Keep AudioTimelinePanel, AudioTransport, AudioClip and archive/export routes."""
    panel.canvas.TOP=26
    panel.studio_filter=None
    panel.play.show();panel.time.show();panel.summary.show()
    title=next((w for w in header.findChildren(QLabel) if w.text()=='AUDIO'),None)
    if title:title.setText('AUDIO');title.setObjectName('studioAudioTitle')
    panel.enabled.setText('Conditioning')
    row.insertStretch(3,1)
    panel.add.setText('+ Audio');panel.add.setObjectName('studioAudioButton')
    # Existing source-range fields remain available on demand.
    properties=QPushButton('Properties');properties.setObjectName('studioSubtleButton');properties.setCheckable(True)
    properties.toggled.connect(panel.edit_controls.setVisible);row.addWidget(properties)
    properties.setChecked(True)
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
    row.setContentsMargins(4,2,4,2);row.setSpacing(4)
    # Three native 52px lanes; preserve saved heights when the user changes them.
    if not panel.owner.settings.contains('audio_panel_height'):panel.set_body_height(166)


# Timeline-only reference presentation. Transport/snap, caches and controllers
# retain their current styling and behavior.
STUDIO_STYLE += """
#timelineShell{background:#11191d;border:1px solid #354047;border-radius:0;padding:0}
#timeline{background:#11191d;border:0;padding:0}
#studioStack{background:#292929}
#studioActionRail{background:#192329;border-left:1px solid #354047}
#studioActionButton{background:#1b2e38;border:1px solid #385667;color:#b7dcef;border-radius:3px}
#segmentCard,#segmentCard[kind="text"]{background:#252728;border:1px solid #47545c;border-radius:0}
#segmentCard[selected="true"],#segmentCard[kind="text"][selected="true"]{background:#192a33;border:1px solid #82bacf}
#segmentCardBody{background:#252728;border:0}
#segmentPreview{background:#17191a;color:#e1e8ec;border:0;border-radius:0}
#mediaBadge{background:#e5e5e5;color:#262626;font-weight:bold;padding:1px 3px}
#roleBadge{background:#36393a;color:#eee;border:0;border-radius:0;padding:1px 4px;font-size:11px}
#tileDelete{background:#36393a;color:#eee;border:0;border-radius:0;padding:0}
#tileTitle{background:transparent;color:#b7d5e3;font-size:10px}
#tileDuration,#tileStartTime{background:transparent;color:#85acbd;font-size:9px}
#studioDialoguePanel{background:#192126;border:1px solid #354047;border-radius:0}
#studioDialogueTitle{color:#a6c9d9;background:transparent}
#studioDialogueCue{background:#1b242a;border:1px solid #3d4b54;border-radius:0}
#studioDialogueBadge,#studioDialoguePanel #studioSpeaker{background:#2b353c;color:#bfd4df;border:1px solid #47545d;border-radius:2px}
#studioDialogueText{color:#d3e0e7}
#audioTimelinePanel{background:#292929;border:0;border-radius:0}
#studioAudioTitle{background:transparent;color:#8edbce}
#studioFilter{background:#28333a;color:#b9ced9;border:1px solid #42515b;border-radius:2px;padding:1px 4px}
#studioFilter:checked{background:#24463f;color:#b6e5d6;border-color:#527d70}
#conditioningGuide{background:#141d21;border:1px solid #42525b;border-radius:0}
"""
