"""Completed-shot ordering and rendering controls, using Director's existing player."""
from __future__ import annotations
import copy
import hashlib
import shutil
import threading
from pathlib import Path
from importlib.resources import files
from uuid import uuid4

from PySide6.QtCore import Qt, Signal, QObject, QTimer, QSize
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QComboBox,
                              QDoubleSpinBox,QSpinBox,QListWidget,QListWidgetItem,QAbstractItemView,
                              QSplitter,QFileDialog,QMessageBox,QProgressBar)
from .models import Segment
from .media import prepare_media, safe_media_filename, TIMELINE_VIDEO_SUFFIXES
from .project_archive import materialize_source
from .shot_sequence import MODES, settings, probe, render_sequence, shot_layout, independent_transitions, inherit_transitions
from .shot_timeline import ShotTimeline
from .shot_preview_cache import PreviewCache, make_plan, source_identity
from .audio_scrub import AudioScrubber
from .responsive_layout import ControlRow


def render_shots(shots,options,output=None,preview=False,cancel=None,progress=None):
    # Resolve portable originals off the GUI thread, including archived source movies.
    paths=[]
    for shot in shots:
        if cancel is not None and cancel.is_set():raise RuntimeError('Shot sequence operation cancelled.')
        paths.append(materialize_source(shot))
    options={**options,'_frame_limits':[max(1,round(s.duration*24)) for s in shots],
             '_shot_ids':[s.id for s in shots]}
    return render_sequence(paths,options,output,preview,cancel,progress)


def render_preview(shots,options,cancel,progress=None):
    try:return render_shots(shots,options,None,True,cancel,progress)
    except RuntimeError:
        if cancel.is_set():return None
        raise


def import_shots(paths, directory):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    result=[]
    for path in paths:
        source=Path(path)
        if not source.is_file() or source.suffix.lower() not in TIMELINE_VIDEO_SUFFIXES:
            raise ValueError(f'Choose a completed movie: {source.name}')
        dest=directory/(uuid4().hex+'-'+safe_media_filename(source.name,'shot'))
        try:
            shutil.copy2(source,dest)
            info=probe(dest)
            kind,preview,_,_=prepare_media(str(dest))
            shot=Segment(source.name,str(dest),preview,kind,'start','',info['frames']/24,
                         media_duration_frames=info['frames'],trim_start=0)
            digest=hashlib.sha256()
            with dest.open('rb') as stream:
                for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
            shot._media_digest=digest.hexdigest()
            result.append(shot)
        except Exception:
            dest.unlink(missing_ok=True)
            # Remove earlier copies when a batch import fails before it can be committed.
            for shot in result:Path(shot.media_path).unlink(missing_ok=True)
            raise
    return result


def shot_icon(name):
    return QIcon(str(files('ltx_prompt_director').joinpath(f'assets/shot-{name}.svg')))


class RenderProgress(QObject):
    updated=Signal(int,str)
    region=Signal(int)
    thumbnail=Signal(str,object,object)
    partial=Signal(object,int)


class ShotSequencePanel(QWidget):
    def __init__(self,owner,player_class,parent=None):
        super().__init__(parent)
        self.owner=owner;self._revision=0;self._cancel=None;self._populating=False
        self._signature=None;self._baking=False;self.cache_percent=0
        self.cache=PreviewCache();self._plan=None;self._worker_running=False
        self._active=False;self._pending=False;self._pending_position=0
        self._desired_playing=False;self._installing=False;self._available_frames=0;self._waiting_position=False
        self._build_timer=QTimer(self);self._build_timer.setSingleShot(True);self._build_timer.setInterval(180)
        self._build_timer.timeout.connect(self._start_cached_preview)
        layout=QVBoxLayout(self)
        heading=QLabel('SHOT SEQUENCE · completed movies');heading.setObjectName('previewTitle')
        layout.addWidget(heading)
        row=ControlRow()
        self.blend=QComboBox();self.blend.addItems(MODES)
        self.side=QComboBox();self.side.addItem('Source side','source');self.side.addItem('Destination side','destination')
        self.side.setToolTip('Source: fade source tail toward destination head. Destination: reverse overlap weighting. For cut, trim the selected side.')
        self.overlap=QDoubleSpinBox();self.overlap.setRange(0,60);self.overlap.setDecimals(2);self.overlap.setSuffix(' s');self.overlap.setValue(1)
        self.crf=QSpinBox();self.crf.setRange(0,63);self.crf.setValue(15);self.crf.setToolTip('WebM CRF: lower values retain more detail.')
        self.width=QSpinBox();self.height=QSpinBox()
        for field,value in [(self.width,1280),(self.height,704)]:field.setRange(32,8192);field.setSingleStep(2);field.setValue(value)
        row2=ControlRow()
        for text,widget in [('Blend',self.blend),('Overlap',self.overlap),('Side',self.side),('Width',self.width),('Height',self.height),('CRF',self.crf)]:
            target=row2 if text in {"Width","Height","CRF"} else row
            target.addWidget(QLabel(text));target.addWidget(widget)
        layout.addLayout(row)
        row2.addStretch();layout.addLayout(row2)
        split=QSplitter(Qt.Orientation.Vertical)
        left=QWidget();column=QVBoxLayout(left)
        self.shots=ShotTimeline()
        self.shots.movies_dropped.connect(self.add_paths)
        self.shots.movies_replaced.connect(self.replace_paths)
        self.shots.status_requested.connect(self.set_shot_status)
        self.shots.export_requested.connect(self.export_segment)
        self.shots.remove_requested.connect(self.remove)
        self.shots.order_changed.connect(self.order_changed)
        self.shots.items.currentRowChanged.connect(self.sync_transition_controls)
        self.shots.items.itemSelectionChanged.connect(self.sync_transition_controls)
        for signal in (self.blend.currentIndexChanged,self.overlap.valueChanged,self.side.currentIndexChanged):
            signal.connect(self.transition_changed)
        column.addWidget(QLabel('NLA timeline · thumbnail strips · drag scrubhead to seek · green cached / red rebuilding'))
        column.addWidget(self.shots,1)
        buttons=ControlRow()
        self.timeline_buttons=[]
        actions=[('Add shots…','add',self.choose,'Import completed movie files'),
                 ('Collection previews…','collection',self.add_collection_movies,'Add saved rendered previews from other projects in this collection'),
                 ('Restore full clips','restore',self.restore_full_clips,'Restore each movie to its full source duration'),
                 ('Remove','remove',self.remove,'Remove selected shots from the sequence'),
                 ('','earlier',lambda:self.move(-1),'Move selected shot earlier'),
                 ('','later',lambda:self.move(1),'Move selected shot later'),
                 ('','zoom-out',lambda:self.shots.zoom(.8),'Zoom out'),
                 ('','zoom-in',lambda:self.shots.zoom(1.25),'Zoom in'),
                 ('Fit','fit',self.shots.fit,'Fit the entire sequence in the timeline')]
        for title,icon,action,hint in actions:
            button=QPushButton(title);button.clicked.connect(action)
            button.setIcon(QIcon(str(files('ltx_prompt_director').joinpath('assets/toolbar-delete.svg'))) if icon=='remove' else shot_icon(icon))
            button.setIconSize(QSize(24,24));button.setMinimumHeight(34)
            button.setToolTip(hint);button.setAccessibleName(hint)
            if not title:button.setFixedWidth(40)
            self.timeline_buttons.append(button);buttons.addWidget(button)
        buttons.addStretch()
        column.addLayout(buttons)
        self.preview=player_class(self)
        self.preview.play.clicked.disconnect(self.preview.toggle_playback)
        self.preview.play.clicked.connect(self.toggle_sequence_playback)
        self.preview.choose.setText('Add Shots…');self.preview.choose.setIcon(shot_icon('add'))
        self.preview.export.setText('Bake WebM…');self.preview.export.setIcon(shot_icon('bake'))
        self.preview.choose_requested.connect(self.choose)
        self.preview.export_requested.connect(self.bake)
        self.preview.video_dropped.connect(lambda path:self.add_paths([path]))
        split.addWidget(self.preview);split.addWidget(left)
        split.setStretchFactor(0,3);split.setStretchFactor(1,1)
        self._audio_scrub=AudioScrubber(self.preview.player,self)
        self.shots.seek_requested.connect(self.scrub_sequence)
        self.shots.scrub_started.connect(self._audio_scrub.begin)
        self.shots.scrub_finished.connect(self._audio_scrub.end)
        for slider,callback in ((self.preview.seek,self.preview.seek_to),
                                (self.preview.fullscreen_window.seek,self.preview.fullscreen_window.seek_to)):
            slider.seek_requested.disconnect(callback)
            slider.seek_requested.connect(self.scrub_sequence)
            slider.scrub_started.connect(self._audio_scrub.begin)
            slider.scrub_finished.connect(self._audio_scrub.end)
        self.preview.player.positionChanged.connect(self.sequence_position_changed)
        self.preview.player.playbackStateChanged.connect(self.sequence_playback_changed)
        self.preview.player.mediaStatusChanged.connect(self.sequence_media_status)
        layout.addWidget(split,1)
        self.summary=QLabel('No shots yet')
        layout.addWidget(self.summary)
        self.bake_progress=QProgressBar()
        self.bake_progress.setRange(0,100);self.bake_progress.setValue(0);self.bake_progress.hide();layout.addWidget(self.bake_progress)
        for control,signal in [(self.crf,self.crf.valueChanged),
                               (self.width,self.width.valueChanged),(self.height,self.height.valueChanged)]:signal.connect(self.options_changed)
        self.refresh()

    def options(self):
        return settings({**getattr(self.owner,'shot_sequence_settings',{}),
                         'crf':self.crf.value(),'width':self.width.value(),'height':self.height.value()})

    def invalidate(self):
        self._audio_scrub.cancel()
        self._revision+=1
        self._build_timer.stop()
        if self._cancel:self._cancel.set()
        self._cancel=None
        self._installing=True;self.preview.clear_project();self._installing=False
        self._available_frames=0
        self.preview.choose.setEnabled(True)
        self.preview.export.setEnabled(bool(self.owner.segments) and not self._baking)
        self.cache_percent=0;self.preview.play.setToolTip('Play the assembled sequence; the timeline line shows cache coverage')
        self.shots.set_playhead(self._pending_position)
        self.preview.play.setEnabled(bool(self.owner.segments))
        self.preview.title.setText('Shot Sequence · RAM preview')
        self.preview.empty.setText('Caching the sequence in the background…' if self.owner.segments else 'Drop completed shots to begin')

    def deactivate(self):
        self._active=False;self._pending=False;self._desired_playing=False;self._pending_position=0
        self.invalidate();self._signature=None;self._plan=None;self.shots.set_coverage([])

    def refresh(self):
        opts=independent_transitions([shot.id for shot in self.owner.segments],getattr(self.owner,'shot_sequence_settings',None))
        self.owner.shot_sequence_settings=opts
        tags=self.owner.project_status_tags()
        signature=(self.owner.workspace_revision,tuple((s.id,s.media_path,s.duration,s.name,s.preview_path,source_identity(s)) for s in self.owner.segments),tuple(opts.items()),tuple((t["name"],t["color"]) for t in tags))
        if signature==self._signature:return
        self._active=self.owner.workspace_engine=="shot_sequence"
        self._signature=signature;self._populating=True
        selected={item.data(Qt.ItemDataRole.UserRole) for item in self.shots.selectedItems()}
        current_row=self.shots.currentRow()
        current=self.shots.item(current_row)
        current_id=current.data(Qt.ItemDataRole.UserRole) if current else None
        self.blend.setCurrentText(opts['blend']);self.side.setCurrentIndex(self.side.findData(opts['side']))
        self.overlap.setValue(opts['overlap']);self.crf.setValue(opts['crf']);self.width.setValue(opts['width']);self.height.setValue(opts['height'])
        self.shots.clear()
        for index,shot in enumerate(self.owner.segments):
            item=QListWidgetItem(f'{index+1:02d} · {shot.name}\n{shot.duration:.2f}s')
            item.setData(Qt.ItemDataRole.UserRole,shot.id);item.setToolTip(shot.media_path)
            from PySide6.QtGui import QIcon
            item.setIcon(QIcon(shot.preview_path))
            self.shots.addItem(item)
        self.shots.set_shots(self.owner.segments,opts)
        self.shots.set_statuses(opts.get('shot_statuses',{}),tags)
        for index,shot in enumerate(self.owner.segments):
            if shot.id==current_id:self.shots.setCurrentRow(index)
        for index,shot in enumerate(self.owner.segments):self.shots.item(index).setSelected(shot.id in selected)
        if self.owner.segments and not any(shot.id==current_id for shot in self.owner.segments):
            self.shots.setCurrentRow(min(max(0,current_row),len(self.owner.segments)-1))
        self._populating=False
        self.sync_transition_controls()
        joins,total=shot_layout(self.owner.segments,opts)
        self.summary.setText(f'{len(self.owner.segments)} shots · {total/24:.2f}s · per-shot transitions · audio included')
        plan=make_plan(copy.deepcopy(self.owner.segments),opts)
        if self._plan is None or plan.signature!=self._plan.signature:
            self._plan=plan
            self._pending_position=min(self._pending_position,round(plan.count/24*1000))
            self.invalidate()
            self.shots.set_coverage(self.cache.coverage(plan))
            for key,(images,peaks) in self.cache.thumbnails(plan).items():self.install_thumbnails(plan,key,images,peaks)
            self._pending=bool(plan.parts)
            if self._active and self._pending:self._build_timer.start()

    def options_changed(self,*_):
        if self._populating:return
        self.owner.shot_sequence_settings=self.options()
        self.refresh();self.owner.mark_dirty()

    def sync_transition_controls(self,*_):
        if self._populating:return
        row=self.shots.currentRow()
        selected=0<=row<len(self.owner.segments) and self.shots.item(row).isSelected()
        self._populating=True
        opts=self.options()
        join={key:opts[key] for key in ('blend','overlap','side')}
        if selected:
            shot=self.owner.segments[row]
            hint=f'{row+1:02d} · {shot.name}'+(f' → {self.owner.segments[row+1].name}' if row<len(self.owner.segments)-1 else ' · used when the next shot is added')
            join.update(opts.get('shot_transitions',{}).get(shot.id,{}))
        else:hint='Settings for the first shot' if not self.owner.segments else 'Select a shot to edit its transition'
        self.blend.setCurrentText(join['blend']);self.overlap.setValue(join['overlap'])
        self.side.setCurrentIndex(self.side.findData(join['side']))
        for control in (self.blend,self.overlap,self.side):
            control.setEnabled(selected or not self.owner.segments);control.setToolTip(hint)
        self._populating=False

    def transition_changed(self,*_):
        if self._populating:return
        row=self.shots.currentRow()
        opts=self.options();join=dict(blend=self.blend.currentText(),overlap=self.overlap.value(),side=self.side.currentData())
        if not self.owner.segments:opts.update(join)
        elif 0<=row<len(self.owner.segments) and self.shots.item(row).isSelected():
            opts['shot_transitions']={**opts.get('shot_transitions',{}),self.owner.segments[row].id:join}
        else:return
        self.owner.shot_sequence_settings=settings(opts)
        self.refresh();self.owner.mark_dirty()

    def choose(self):
        paths,_=QFileDialog.getOpenFileNames(self,'Add completed shots','', 'Movies (*.mp4 *.webm *.mov *.mkv *.avi *.m4v)')
        if paths:self.add_paths(paths)

    def add_collection_movies(self):
        collection=self.owner.current_collection or ''
        paths=[record['previewVideoPath'] for record in self.owner.library_records()
               if record.get('collection','')==collection and record.get('projectType')!='shot_sequence'
               and record.get('previewVideoPath') and Path(record['previewVideoPath']).is_file()]
        if paths:self.add_paths(paths)
        else:QMessageBox.information(self,'Add collection previews','Save a rendered preview movie in another project in this collection first, or use Add shots to import movie files.')

    def add_paths(self,paths,index=None):
        if not paths:return
        project=(self.owner.current_project_id,self.owner.workspace_revision)
        index=len(self.owner.segments) if index is None or index<0 else min(index,len(self.owner.segments))
        anchor=self.owner.segments[index].id if index<len(self.owner.segments) else None
        from .ui import project_library_path
        def imported(shots):
            if project!=(self.owner.current_project_id,self.owner.workspace_revision):
                for shot in shots:Path(shot.media_path).unlink(missing_ok=True)
                return
            position=next((i for i,shot in enumerate(self.owner.segments) if shot.id==anchor),min(index,len(self.owner.segments))) if anchor else len(self.owner.segments)
            neighbor=self.owner.segments[position-1] if position>0 else self.owner.segments[0] if self.owner.segments else None
            self.owner.shot_sequence_settings=inherit_transitions([shot.id for shot in shots],self.owner.shot_sequence_settings,neighbor.id if neighbor else None)
            self.owner.segments[position:position]=shots
            self.owner.refresh_timeline();self.refresh();self.owner.mark_dirty()
            if shots:self.shots.setCurrentRow(position)
        self.owner.start_disk_job(import_shots,(list(paths),project_library_path()/'shot_sources'),imported,
                                 lambda message:QMessageBox.warning(self,'Shot import failed',message))

    def replace_paths(self,paths,target_id):
        if not paths:return
        project=(self.owner.current_project_id,self.owner.workspace_revision)
        from .ui import project_library_path
        def imported(shots):
            location=next((i for i,s in enumerate(self.owner.segments) if s.id==target_id),-1)
            if project!=(self.owner.current_project_id,self.owner.workspace_revision) or location<0:
                for shot in shots:Path(shot.media_path).unlink(missing_ok=True)
                return
            if not shots:return
            # Keep the slot identity and sequence order, adopting the full length
            # of the replacement. Additional dropped movies follow this slot.
            shots[0].id=target_id
            self.owner.shot_sequence_settings=inherit_transitions([shot.id for shot in shots[1:]],self.owner.shot_sequence_settings,target_id)
            self.owner.segments[location:location+1]=shots
            self.owner.refresh_timeline();self.shots.setCurrentRow(location);self.owner.mark_dirty()
        self.owner.start_disk_job(import_shots,(list(paths),project_library_path()/'shot_sources'),imported,
                                 lambda message:QMessageBox.warning(self,'Shot replacement failed',message))

    def set_shot_status(self,ids,status):
        available={tag['name'] for tag in self.owner.project_status_tags()}
        if status and status not in available:return
        current=getattr(self.owner,'shot_sequence_settings',{})
        statuses=dict(current.get('shot_statuses',{}))
        existing={shot.id for shot in self.owner.segments}
        for ident in ids:
            if ident not in existing:continue
            if status:statuses[ident]=status
            else:statuses.pop(ident,None)
        if statuses==current.get('shot_statuses',{}):return
        self.owner.shot_sequence_settings={**current,'shot_statuses':statuses}
        self.refresh();self.owner.mark_dirty()

    def order_changed(self,*_):
        if self._populating:return
        mapping={s.id:s for s in self.owner.segments}
        ids=[self.shots.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.shots.count())]
        self.owner.segments=[mapping[key] for key in ids]
        self.owner.refresh_timeline();self.refresh();self.owner.mark_dirty()

    def remove(self):
        ids={item.data(Qt.ItemDataRole.UserRole) for item in self.shots.selectedItems()}
        if not ids:return
        self.owner.segments=[s for s in self.owner.segments if s.id not in ids]
        opts=dict(self.owner.shot_sequence_settings)
        for field in ('shot_statuses','shot_transitions'):
            if field in opts:opts[field]={key:value for key,value in opts[field].items() if key not in ids}
        self.owner.shot_sequence_settings=opts
        self.owner.refresh_timeline();self.refresh();self.owner.mark_dirty()

    def move(self,direction):
        row=self.shots.currentRow();target=row+direction
        if not 0<=target<self.shots.count():return
        self._populating=True
        item=self.shots.takeItem(row);self.shots.insertItem(target,item);self.shots.setCurrentRow(target)
        self._populating=False;self.order_changed()
        self.shots.setCurrentRow(target)

    def export_segment(self,ident):
        for index,shot in enumerate(self.owner.segments):
            if shot.id==ident:
                self.owner.export_segment(shot,index+1)
                return

    def scrub_sequence(self,milliseconds):
        self.seek_sequence(milliseconds,audible=True)

    def seek_sequence(self,milliseconds,audible=False):
        self._pending_position=milliseconds
        self._waiting_position=milliseconds>=self._available_frames/24*1000
        if getattr(self.preview,'_memory_preview',False):
            if milliseconds < self._available_frames/24*1000:
                if audible and not self._installing:
                    self._audio_scrub.seek(max(0,min(milliseconds,self.preview.duration)))
                else:self.preview.seek_to(milliseconds)
            else:
                self._audio_scrub.cancel()
                self._installing=True;self.preview.player.pause();self._installing=False
        self.shots.set_playhead(milliseconds)

    def sequence_position_changed(self,milliseconds):
        if self._audio_scrub.active:return
        if getattr(self.preview,'_memory_preview',False) and not self._installing and not self._waiting_position:
            self._pending_position=milliseconds
            self.shots.set_playhead(milliseconds,follow=self.preview.player.playbackState()==QMediaPlayer.PlaybackState.PlayingState)

    def sequence_playback_changed(self,state):
        if self._audio_scrub.active:return
        if not self._installing and state!=QMediaPlayer.PlaybackState.StoppedState:
            self._desired_playing=state==QMediaPlayer.PlaybackState.PlayingState

    def sequence_media_status(self,status):
        if status in {QMediaPlayer.MediaStatus.LoadedMedia,QMediaPlayer.MediaStatus.BufferedMedia}:
            self.preview.play.setEnabled(True)
            if self._installing:
                self._installing=False
                self.shots.set_playhead(self._pending_position)
        elif status==QMediaPlayer.MediaStatus.EndOfMedia and self._available_frames < (self._plan.count if self._plan else 0):
            if self._audio_scrub.active:return
            # A growing prefix has ended. Resume automatically once more is linked.
            self._desired_playing=True
            self._pending_position=round(self._available_frames/24*1000)

    def install_thumbnails(self,plan,key,images,peaks):
        identity=plan.sources.get(key)
        if identity is None:return
        for shot in self.owner.segments:
            if source_identity(shot)==source_identity(identity):
                self.shots.set_thumbnails(shot.id,images);self.shots.set_waveform(shot.id,peaks)

    def install_preview(self,data,frames):
        if not data:return
        self._audio_scrub.cancel()
        self._available_frames=frames
        playable=self._pending_position<frames/24*1000
        self._waiting_position=not playable
        self._installing=True
        self.preview.set_memory_video('Shot Sequence · continuous preview',data,
                                      min(self._pending_position,max(0,round(frames/24*1000)-1)),
                                      autoplay=self._desired_playing and playable)
        self.preview.export.setEnabled(not self._baking)

    def restore_full_clips(self):
        if not self.owner.segments:return
        project=(self.owner.current_project_id,self.owner.workspace_revision)
        shots=copy.deepcopy(self.owner.segments)
        def scan():return [(shot.id,probe(materialize_source(shot))['frames']) for shot in shots]
        def ready(lengths):
            if project!=(self.owner.current_project_id,self.owner.workspace_revision):return
            by_id=dict(lengths)
            for shot in self.owner.segments:
                if shot.id in by_id:
                    shot.media_duration_frames=by_id[shot.id];shot.duration=by_id[shot.id]/24;shot.trim_start=0
            self.owner.refresh_timeline();self.owner.mark_dirty()
        self.owner.start_disk_job(scan,(),ready,lambda message:QMessageBox.warning(self,'Restore full clips',message))

    def progress_channel(self,bar,label,active):
        # Emitting a QObject signal marshals worker progress onto the GUI thread.
        channel=RenderProgress(self)
        bar.setValue(0);bar.setFormat(label+' · starting · %p%');bar.show()
        def update(value,stage):
            if active():bar.setValue(value);bar.setFormat(label+' · '+stage+' · %p%')
        channel.updated.connect(update,Qt.ConnectionType.QueuedConnection)
        return channel

    def snapshot(self):
        if not self.owner.segments:raise ValueError('Add at least one completed shot.')
        if any(s.kind!='video' for s in self.owner.segments):raise ValueError('Shot sequences accept completed movies only.')
        return copy.deepcopy(self.owner.segments),self.options()

    def build_preview(self):
        if not self._plan or not self._plan.parts:return
        self._desired_playing=True
        if self._worker_running:
            if self._available_frames and not self._installing:self.preview.player.play()
            return
        if self._available_frames==self._plan.count and getattr(self.preview,"_memory_preview",False) and not self._installing:
            self.preview.player.play();return
        self._pending=True
        self._build_timer.stop();self._start_cached_preview()

    def toggle_sequence_playback(self):
        self._audio_scrub.cancel()
        if self._available_frames and not self._installing:
            self.preview.toggle_playback()
        elif self._desired_playing:
            self._desired_playing=False
        else:self.build_preview()
        self.preview.play.setText('❚❚ Pause' if self._desired_playing else '▶ Play')

    def _start_cached_preview(self):
        if not self._active or not self._pending or not self._plan or not self._plan.parts:return
        if self._worker_running:return # latest revision starts after the cancelled job returns
        plan=self._plan;revision=self._revision
        self._pending=False;self._worker_running=True
        cancel=threading.Event();self._cancel=cancel
        self.preview.play.setEnabled(True)
        progress=RenderProgress(self)
        def cache_progress(value,stage):
            if revision!=self._revision:return
            self.cache_percent=value
            self.preview.play.setToolTip(f'Background preview: {stage} · {value}%')
        progress.updated.connect(cache_progress,Qt.ConnectionType.QueuedConnection)
        coverage=self.cache.coverage(plan)
        self.shots.set_coverage(coverage)
        def region(index):
            if revision!=self._revision:return
            start,end,_=coverage[index];coverage[index]=(start,end,True)
            self.shots.set_coverage(coverage)
        progress.region.connect(region,Qt.ConnectionType.QueuedConnection)
        progress.thumbnail.connect(lambda key,images,peaks:self.install_thumbnails(plan,key,images,peaks) if revision==self._revision else None,Qt.ConnectionType.QueuedConnection)
        progress.partial.connect(lambda data,frames:self.install_preview(data,frames) if revision==self._revision else None,Qt.ConnectionType.QueuedConnection)
        def finish():
            progress.deleteLater();self._worker_running=False
            if revision==self._revision:self._cancel=None;self.preview.play.setEnabled(True)
            elif self._active and self._pending:self._build_timer.start()
        def ready(data):
            finish()
            if revision!=self._revision or data is None:return
            self.cache_percent=100;self.preview.play.setToolTip('Play the assembled sequence')
            self.install_preview(data,plan.count)
        def failed(message):
            finish()
            if revision!=self._revision:return
            self.preview.empty.setText(message);self.preview.play.setToolTip(message)
        self.owner.start_disk_job(self.cache.build,(plan,cancel,progress.updated.emit,progress.region.emit,progress.thumbnail.emit,progress.partial.emit),ready,failed)

    def bake(self):
        if self._baking:return
        try:paths,opts=self.snapshot()
        except ValueError as error:QMessageBox.information(self,'Shot Sequence',str(error));return
        path=self.owner.next_export_path((self.owner.current_project_name or 'Shot Sequence')+'.webm')
        self.owner.statusBar().showMessage('Baking shot sequence with source audio…')
        self._baking=True;self.preview.export.setEnabled(False)
        progress=self.progress_channel(self.bake_progress,'Final bake',lambda:self._baking)
        def finish():
            self._baking=False;progress.deleteLater()
            enabled=bool(self.owner.segments)
            self.preview.export.setEnabled(enabled)
        def ready(_):
            self.bake_progress.setValue(100);self.bake_progress.setFormat('Final bake · saved · %p%')
            finish();self.bake_progress.hide();self.owner.record_export(path)
        def failed(message):
            self.bake_progress.setFormat('Final bake · failed · %p%')
            finish();self.bake_progress.hide();self.owner.export_failed(path,message)
        self.owner.start_disk_job(render_shots,(paths,opts,str(path),False,None,progress.updated.emit),ready,failed,serialized=True)
