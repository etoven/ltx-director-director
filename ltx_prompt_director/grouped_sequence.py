"""Project-owned clip attachments and reversible authoring/sequence projections."""
import copy
from pathlib import Path
from uuid import uuid4
from PySide6.QtCore import QObject, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget
from .models import Segment
from .blend_timing import DEFAULT_SECONDS, blend_seconds


class CacheCoverage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent);self.ranges=[];self.setFixedHeight(5)
        self.setToolTip('Green: cached video · Red: missing or rebuilding')
    def set_coverage(self,ranges):self.ranges=list(ranges);self.update()
    def paintEvent(self,event):
        painter=QPainter(self);total=max((b for _,b,_ in self.ranges),default=1) or 1
        for start,end,ready in self.ranges:
            painter.fillRect(round(start/total*self.width()),0,max(1,round((end-start)/total*self.width())),self.height(),QColor('#45c894' if ready else '#db6569'))


class GroupedSequence(QObject):
    def __init__(self, owner):
        super().__init__(owner);self.owner=owner;self.shots=[];self.data={};self.timeline_active=False;self.busy=False
        self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.setInterval(180)
        self.timer.timeout.connect(self.refresh)
        panel=owner.project_preview_panel
        owner.shot_sequence_panel.shots.items.currentRowChanged.connect(self.select_sequence_group)
        panel.play.clicked.disconnect(panel.toggle_playback)
        panel.play.clicked.connect(self.play_group)
        panel.player.positionChanged.connect(self.position)
        panel.player.mediaStatusChanged.connect(self.media_status)
        panel.player.playbackStateChanged.connect(self.playback_state)
    @property
    def enabled(self):
        return self.owner.workspace_engine.startswith('minimax') or bool(self.data.get('authoring'))
    def select_sequence_group(self,row):
        if self.owner.workspace_engine=='shot_sequence' and 0<=row<len(self.owner.segments):
            self.owner._active_render_group=self.owner.segments[row].render_group

    def record(self):
        if self.owner.workspace_engine=='shot_sequence' and self.data.get('authoring'):self.from_sequence()
        return copy.deepcopy(self.data)
    def load(self,record):
        self.timer.stop();self.timeline_active=False;self.data=copy.deepcopy(record or {});self.shots=[]
    def changed(self):
        if not self.busy and not self.owner._loading and self.enabled:self.timer.start()
    def refresh(self):
        o=self.owner
        if self.busy or o._loading or not self.enabled or not o.segments:return
        self.busy=True
        try:
            if o.workspace_engine=='shot_sequence':
                self.from_sequence();return
            self.shots=self.project_shots()
            panel=o.shot_sequence_panel
            panel.refresh()
            self.warning()
            o.update_timeline_layout()
        finally:self.busy=False
    def project_shots(self):
        o=self.owner;shots=[];ids=[]
        for group,segments in o.render_groups().items():
            options=o.render_group_options.setdefault(str(group),{})
            ident=options.setdefault('sequenceId',str(uuid4()));ids.append(ident)
            clip=copy.deepcopy(options.get('clip') or o.video_render_cache.get(str(group),{}).get('result',{}))
            path=clip.get('path','')
            if clip and 'clip' not in options:options['clip']=clip
            duration=float(clip.get('duration') or o.render_group_duration(group))
            shot=Segment(f'Group {list(o.render_groups()).index(group)+1}',path,clip.get('preview_path',''),
                'video' if path else 'text','start','',duration,
                media_duration_frames=clip.get('media_duration_frames') or max(1,round((clip.get('source_duration') or duration)*24)),
                trim_start=int(clip.get('trim_start',0)),id=ident,render_group=group,shot_trimmed=True)
            shots.append(shot)
        transitions={}
        groups=list(o.render_groups())
        for index,shot in enumerate(shots[:-1]):
            transitions[shot.id]=o.extension_transition(groups[index+1])
        o.shot_sequence_settings={**o.shot_sequence_settings,'shot_transitions':transitions}
        return shots
    def switch(self, before, after):
        o=self.owner
        o.shot_sequence_panel.preview.fullscreen_window.reject()
        o.project_preview_panel.fullscreen_window.reject()
        o.project_preview_panel.player.pause()
        o.project_preview_panel.release_memory_video()
        o.shot_sequence_panel.deactivate()
        self.timeline_active=False
        if after=='shot_sequence' and before!='shot_sequence' and (before.startswith('minimax') or o.video_extensions):
            o.store_render_group()
            self.data['authoring']=[{**s.to_dict(),**({'archiveOriginal':list(s._archive_source)} if getattr(s,'_archive_source',None) else {})} for s in o.segments]
            self.data['workspaceType']=o.project_type
            o.segments=self.project_shots()
        elif before=='shot_sequence' and after!='shot_sequence':
            if not self.data.get('authoring') and o.segments:
                rows=[]
                for index,shot in enumerate(o.segments):
                    shot.render_group=index
                    o.render_group_options[str(index)]={'sequenceId':shot.id,'requestedLength':shot.duration,'clip':dict(path=shot.media_path,preview_path=shot.preview_path,duration=shot.duration,trim_start=int(shot.trim_start or 0),media_duration_frames=shot.media_duration_frames)}
                    row=shot.to_dict();row.update(kind='text',role='text',media_path='',preview_path='',render_group=index)
                    rows.append(row)
                self.data['authoring']=rows
            self.from_sequence()
            o.segments=[]
            for raw in self.data['authoring']:
                segment=Segment.from_dict(raw)
                if raw.get('archiveOriginal'):segment._archive_source=tuple(raw['archiveOriginal'])
                o.segments.append(segment)
            o.video_extensions=max(0,len(o.render_groups())-1)
            if o._active_render_group not in o.render_groups():o._active_render_group=next(iter(o.render_groups()))
    def from_sequence(self):
        o=self.owner
        if not self.data.get('authoring'):return
        original=self.data['authoring'];by_group={}
        for raw in original:by_group.setdefault(raw.get('render_group',0),[]).append(raw)
        seen=set();rebuilt=[];next_group=max([int(k) for k in o.render_group_options]+[0])+1
        previous=None
        for shot in o.segments:
            group=shot.render_group
            options=o.render_group_options.get(str(group),{})
            if group in seen or options.get('sequenceId')!=shot.id:
                split=shot.media_path==options.get('clip',{}).get('path')
                template=copy.deepcopy(by_group.get(group,[])) if split else []
                options=copy.deepcopy(options);group=next_group;next_group+=1;shot.render_group=group
                if not split:
                    options.pop('authoringOriginal',None)
                    context=options.get('context',{})
                    context['globalPrompt']=''
                    context.get('minimaxH3',{})['prompt']=''
                for raw in template:raw['id']=str(uuid4());raw['render_group']=group
                by_group[group]=template
                o.render_group_options[str(group)]=options
            seen.add(group);options['sequenceId']=shot.id
            options['clip']=dict(path=shot.media_path,duration=shot.duration,trim_start=int(shot.trim_start or 0),media_duration_frames=shot.media_duration_frames,preview_path=shot.preview_path)
            o.render_group_options[str(group)]=options
            if previous is not None:
                join=o.shot_sequence_settings.get('shot_transitions',{}).get(previous.id,{})
                options.update(blend=join.get('blend','perceptual_crossfade'),blendLength=join.get('overlap',DEFAULT_SECONDS),blendSide=join.get('side','source'))
            rows=by_group.get(group) or [Segment(shot.name,'','','text','text','',shot.duration,render_group=group).to_dict()]
            rows=copy.deepcopy(rows)
            total=sum(float(raw.get('duration',0)) for raw in rows)
            if total and abs(total-shot.duration)>1e-5:
                options.setdefault('authoringOriginal',copy.deepcopy(rows))
                for raw in rows:raw['duration']=float(raw['duration'])*shot.duration/total
                rows[-1]['duration']+=shot.duration-sum(raw['duration'] for raw in rows)
            options['requestedLength']=shot.duration
            options.get('context',{}).setdefault('directionOptions',{})['requestedLength']=shot.duration
            rebuilt.extend(rows);previous=shot
        self.data['authoring']=rebuilt
        if o._active_render_group not in seen and seen:o._active_render_group=o.segments[0].render_group
        o.video_extensions=max(0,len(seen)-1)
    def display_durations(self):
        o=self.owner
        if o.workspace_engine=='shot_sequence' or not self.enabled:return [s.duration for s in o.segments]
        groups=list(o.render_groups());values=[s.duration for s in o.segments]
        for index,group in enumerate(groups):
            positions=[i for i,s in enumerate(o.segments) if s.render_group==group]
            total=sum(values[i] for i in positions)
            length=float(o.render_group_options.get(str(group),{}).get('clip',{}).get('duration') or total)
            if total:
                for i in positions:values[i]*=length/total
            amount=o.extension_transition(groups[index+1])['overlap'] if index+1<len(groups) else 0
            for i in reversed(positions):
                take=min(amount,max(0,values[i]-1/24));values[i]-=take;amount-=take
        return values

    def selected_clip(self):
        return self.owner.render_group_options.get(str(self.owner._active_render_group),{}).get('clip',{})
    def show_group(self):
        o=self.owner
        # Editor selection must not replace a running sampler's live preview.
        if o.project_preview_panel._rendering:return
        clip=self.selected_clip()
        if self.timeline_active:o.audio_panel.transport.pause()
        self.timeline_active=False
        if clip.get('path'):
            o.project_preview_panel.set_project(f'Group {o._active_render_group+1}',clip['path'])
    def play_group(self):
        o=self.owner
        if o.project_preview_panel._rendering:return
        if self.enabled and o.workspace_engine!='shot_sequence' and self.timeline_active:self.show_group()
        o.project_preview_panel.toggle_playback()
    def activate_timeline(self,seconds,playing=False):
        o=self.owner
        if o.project_preview_panel._rendering:return
        panel=o.shot_sequence_panel
        self.timeline_active=True
        panel._active=True;panel._pending_position=round(seconds*1000);panel._desired_playing=playing
        if panel._plan is None:self.refresh()
        if panel._plan is None:return
        panel._waiting_position=panel._pending_position>=panel._available_frames/24*1000
        if panel._available_frames and o.project_preview_panel._memory_preview:
            o.project_preview_panel.seek_to(panel._pending_position)
            if playing:o.project_preview_panel.player.play()
        else:
            panel._pending=True;panel._build_timer.start(0)
        o.project_preview_dock.show()
    def position(self,value):
        if self.timeline_active:self.owner.shot_sequence_panel.sequence_position_changed(value)
    def media_status(self,status):
        if self.timeline_active:self.owner.shot_sequence_panel.sequence_media_status(status)
    def playback_state(self,state):
        if self.timeline_active:self.owner.shot_sequence_panel.sequence_playback_changed(state)
    def warning(self):
        o=self.owner
        if hasattr(o,'keyframes_audio_warning'):
            o.keyframes_audio_warning.setVisible(o.workspace_engine=='minimax_frames' and bool(o.video_extensions) and not self.data.get('audioWarningDismissed'))
