"""Completed-shot ordering and rendering controls, using Director's existing player."""
from __future__ import annotations
import copy
import shutil
import threading
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import Qt, Signal, QObject
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QComboBox,
                              QDoubleSpinBox,QSpinBox,QListWidget,QListWidgetItem,QAbstractItemView,
                              QSplitter,QFileDialog,QMessageBox,QProgressBar)
from .models import Segment
from .media import prepare_media, safe_media_filename, TIMELINE_VIDEO_SUFFIXES
from .project_archive import materialize_source
from .shot_sequence import MODES, settings, probe, render_sequence, overlaps
from .shot_timeline import ShotTimeline


def render_shots(shots,options,output=None,preview=False,cancel=None,progress=None):
    # Resolve portable originals off the GUI thread, including archived source movies.
    paths=[]
    for shot in shots:
        if cancel is not None and cancel.is_set():raise RuntimeError('Shot sequence operation cancelled.')
        paths.append(materialize_source(shot))
    options={**options,'_frame_limits':[max(1,round(s.duration*24)) for s in shots]}
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
            result.append(Segment(source.name,str(dest),preview,kind,'start','',info['frames']/24,
                                  media_duration_frames=info['frames'],trim_start=0))
        except Exception:
            dest.unlink(missing_ok=True)
            # Remove earlier copies when a batch import fails before it can be committed.
            for shot in result:Path(shot.media_path).unlink(missing_ok=True)
            raise
    return result


class RenderProgress(QObject):
    updated=Signal(int,str)


class ShotSequencePanel(QWidget):
    def __init__(self,owner,player_class,parent=None):
        super().__init__(parent)
        self.owner=owner;self._revision=0;self._cancel=None;self._populating=False
        self._signature=None;self._baking=False
        layout=QVBoxLayout(self)
        heading=QLabel('SHOT SEQUENCE · completed movies');heading.setObjectName('previewTitle')
        layout.addWidget(heading)
        row=QHBoxLayout()
        self.blend=QComboBox();self.blend.addItems(MODES)
        self.side=QComboBox();self.side.addItem('Source side','source');self.side.addItem('Destination side','destination')
        self.side.setToolTip('Source: fade source tail toward destination head. Destination: reverse overlap weighting. For cut, trim the selected side.')
        self.overlap=QDoubleSpinBox();self.overlap.setRange(0,60);self.overlap.setDecimals(2);self.overlap.setSuffix(' s');self.overlap.setValue(1)
        self.crf=QSpinBox();self.crf.setRange(0,63);self.crf.setValue(15);self.crf.setToolTip('WebM CRF: lower values retain more detail.')
        self.width=QSpinBox();self.height=QSpinBox()
        for field,value in [(self.width,1280),(self.height,704)]:field.setRange(32,8192);field.setSingleStep(2);field.setValue(value)
        row2=QHBoxLayout()
        for text,widget in [('Blend',self.blend),('Overlap',self.overlap),('Side',self.side),('Width',self.width),('Height',self.height),('CRF',self.crf)]:
            target=row2 if text in {"Width","Height","CRF"} else row
            target.addWidget(QLabel(text));target.addWidget(widget)
        layout.addLayout(row)
        row2.addStretch();layout.addLayout(row2)
        split=QSplitter(Qt.Orientation.Vertical)
        left=QWidget();column=QVBoxLayout(left)
        self.shots=ShotTimeline()
        self.shots.movies_dropped.connect(self.add_paths)
        self.shots.remove_requested.connect(self.remove)
        self.shots.order_changed.connect(self.order_changed)
        self.shots.itemDoubleClicked.connect(self.preview_source)
        column.addWidget(QLabel('NLA timeline · linked video/audio strips · drag to reorder · ruler to seek'))
        column.addWidget(self.shots,1)
        buttons=QHBoxLayout();buttons2=QHBoxLayout()
        for title,action in [('Add shots…',self.choose),('Add collection previews…',self.add_collection_movies),('Restore full clips',self.restore_full_clips),('Remove',self.remove),('←',lambda:self.move(-1)),('→',lambda:self.move(1)),('−',lambda:self.shots.zoom(.8)),('+',lambda:self.shots.zoom(1.25)),('Fit',self.shots.fit)]:
            button=QPushButton(title);button.clicked.connect(action)
            if title == "Add collection previews…":button.setToolTip("Add the saved rendered preview movies from other projects in this collection.")
            buttons.addWidget(button)
        column.addLayout(buttons)
        self.preview=player_class(self)
        self.preview.choose.setText('Add Shots…')
        self.preview.export.setText('Bake WebM…')
        self.preview.choose_requested.connect(self.choose)
        self.preview.export_requested.connect(self.bake)
        self.preview.video_dropped.connect(lambda path:self.add_paths([path]))
        split.addWidget(self.preview);split.addWidget(left)
        split.setStretchFactor(0,3);split.setStretchFactor(1,1)
        self.shots.seek_requested.connect(self.seek_sequence)
        self.preview.player.positionChanged.connect(self.sequence_position_changed)
        layout.addWidget(split,1)
        bottom=QHBoxLayout()
        self.summary=QLabel('No shots yet')
        self.preview_button=QPushButton('▶ Build RAM preview');self.preview_button.clicked.connect(self.build_preview)
        self.bake_button=QPushButton('Bake WebM…');self.bake_button.clicked.connect(self.bake)
        bottom.addWidget(self.summary,1);bottom.addWidget(self.preview_button);bottom.addWidget(self.bake_button)
        layout.addLayout(bottom)
        self.preview_progress=QProgressBar();self.bake_progress=QProgressBar()
        for bar in (self.preview_progress,self.bake_progress):
            bar.setRange(0,100);bar.setValue(0);bar.hide();layout.addWidget(bar)
        for control,signal in [(self.blend,self.blend.currentIndexChanged),(self.side,self.side.currentIndexChanged),
                               (self.overlap,self.overlap.valueChanged),(self.crf,self.crf.valueChanged),
                               (self.width,self.width.valueChanged),(self.height,self.height.valueChanged)]:signal.connect(self.options_changed)
        self.refresh()

    def options(self):
        return settings(dict(blend=self.blend.currentText(),side=self.side.currentData(),overlap=self.overlap.value(),
                             crf=self.crf.value(),width=self.width.value(),height=self.height.value()))

    def invalidate(self):
        self._revision+=1
        if self._cancel:self._cancel.set()
        self._cancel=None
        self.preview.clear_project()
        self.preview.choose.setEnabled(True)
        self.preview.export.setEnabled(bool(self.owner.segments))
        self.preview_button.setEnabled(bool(self.owner.segments));self.bake_button.setEnabled(bool(self.owner.segments) and not self._baking)
        self.preview.export.setEnabled(bool(self.owner.segments) and not self._baking)
        self.preview_progress.hide();self.shots.set_playhead(0)
        self.preview.title.setText('Shot Sequence · RAM preview')
        self.preview.empty.setText('Drop completed shots, then build a continuous RAM preview')

    def deactivate(self):
        self.invalidate();self._signature=None

    def refresh(self):
        opts=settings(getattr(self.owner,'shot_sequence_settings',None))
        signature=(self.owner.workspace_revision,tuple((s.id,s.media_path,s.duration) for s in self.owner.segments),tuple(opts.items()))
        if signature==self._signature:return
        self._signature=signature;self._populating=True
        self.blend.setCurrentText(opts['blend']);self.side.setCurrentIndex(self.side.findData(opts['side']))
        self.overlap.setValue(opts['overlap']);self.crf.setValue(opts['crf']);self.width.setValue(opts['width']);self.height.setValue(opts['height'])
        self.shots.clear()
        for index,shot in enumerate(self.owner.segments):
            item=QListWidgetItem(f'{index+1:02d} · {shot.name}\n{shot.duration:.2f}s')
            item.setData(Qt.ItemDataRole.UserRole,shot.id);item.setToolTip(shot.media_path)
            from PySide6.QtGui import QIcon
            item.setIcon(QIcon(shot.preview_path))
            self.shots.addItem(item)
        self.shots.set_shots(self.owner.segments,opts['overlap'])
        self._populating=False
        clips=[{'frames':max(1,round(s.duration*24))} for s in self.owner.segments]
        joins,total=overlaps(clips,opts['overlap'])
        self.summary.setText(f'{len(clips)} shots · {total/24:.2f}s · {opts["blend"]} · audio included')
        self.invalidate()

    def options_changed(self,*_):
        if self._populating:return
        self.owner.shot_sequence_settings=self.options()
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

    def add_paths(self,paths):
        if not paths:return
        project=(self.owner.current_project_id,self.owner.workspace_revision)
        from .ui import project_library_path
        def imported(shots):
            if project!=(self.owner.current_project_id,self.owner.workspace_revision):
                for shot in shots:Path(shot.media_path).unlink(missing_ok=True)
                return
            self.owner.segments.extend(shots)
            self.owner.refresh_timeline();self.refresh();self.owner.mark_dirty()
        self.owner.start_disk_job(import_shots,(list(paths),project_library_path()/'shot_sources'),imported,
                                 lambda message:QMessageBox.warning(self,'Shot import failed',message))

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
        self.owner.refresh_timeline();self.refresh();self.owner.mark_dirty()

    def move(self,direction):
        row=self.shots.currentRow();target=row+direction
        if not 0<=target<self.shots.count():return
        self._populating=True
        item=self.shots.takeItem(row);self.shots.insertItem(target,item);self.shots.setCurrentRow(target)
        self._populating=False;self.order_changed()
        self.shots.setCurrentRow(target)

    def preview_source(self,item):
        shot=next((s for s in self.owner.segments if s.id==item.data(Qt.ItemDataRole.UserRole)),None)
        if shot:
            revision=self._revision
            def ready(path):
                if revision==self._revision:
                    self.preview.set_project(shot.name,path);self.preview.export.setEnabled(True)
            self.owner.start_disk_job(materialize_source,(copy.deepcopy(shot),),ready,
                                     lambda message:QMessageBox.warning(self,'Source movie',message))

    def seek_sequence(self,milliseconds):
        if getattr(self.preview,'_memory_preview',False):self.preview.seek_to(milliseconds)

    def sequence_position_changed(self,milliseconds):
        if getattr(self.preview,'_memory_preview',False):self.shots.set_playhead(milliseconds)

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
        try:paths,opts=self.snapshot()
        except ValueError as error:QMessageBox.information(self,'Shot Sequence',str(error));return
        self.invalidate();revision=self._revision
        cancel=threading.Event();self._cancel=cancel
        self.preview_button.setEnabled(False)
        self.preview.empty.setText('Building continuous preview in RAM…')
        progress=self.progress_channel(self.preview_progress,'RAM preview',lambda:revision==self._revision)
        def ready(data):
            progress.deleteLater()
            if revision!=self._revision:return
            self._cancel=None;self.preview_button.setEnabled(True)
            self.preview_progress.setValue(100);self.preview_progress.setFormat('RAM preview · ready · %p%')
            self.preview.set_memory_video('Shot Sequence · continuous preview',data)
            self.preview.export.setEnabled(not self._baking)
        def failed(message):
            progress.deleteLater()
            if revision!=self._revision:return
            self._cancel=None;self.preview_button.setEnabled(True)
            self.preview.empty.setText(message)
            self.preview_progress.setFormat('RAM preview · failed · %p%')
        self.owner.start_disk_job(render_preview,(paths,opts,cancel,progress.updated.emit),ready,failed)

    def bake(self):
        if self._baking:return
        try:paths,opts=self.snapshot()
        except ValueError as error:QMessageBox.information(self,'Shot Sequence',str(error));return
        path=self.owner.next_export_path((self.owner.current_project_name or 'Shot Sequence')+'.webm')
        self.owner.statusBar().showMessage('Baking shot sequence with source audio…')
        self._baking=True;self.bake_button.setEnabled(False);self.preview.export.setEnabled(False)
        progress=self.progress_channel(self.bake_progress,'Final bake',lambda:self._baking)
        def finish():
            self._baking=False;progress.deleteLater()
            enabled=bool(self.owner.segments)
            self.bake_button.setEnabled(enabled);self.preview.export.setEnabled(enabled)
        def ready(_):
            self.bake_progress.setValue(100);self.bake_progress.setFormat('Final bake · saved · %p%')
            finish();self.owner.record_export(path)
        def failed(message):
            self.bake_progress.setFormat('Final bake · failed · %p%')
            finish();self.owner.export_failed(path,message)
        self.owner.start_disk_job(render_shots,(paths,opts,str(path),False,None,progress.updated.emit),ready,failed,serialized=True)
