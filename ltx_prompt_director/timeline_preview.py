"""Coalesced timeline scrubbing: rendered audio/video, then conditioning media."""
from __future__ import annotations
import copy
from pathlib import Path
from functools import lru_cache
import av
from PySide6.QtCore import QObject,QTimer
from PySide6.QtGui import QImageReader
from .audio_scrub import AudioScrubber
from .project_archive import materialize_source


def segment_at(segments,seconds):
    if not segments:return None,0.,0.
    total=sum(segment.duration for segment in segments)
    seconds=max(0.,min(float(seconds),max(0.,total-1/24)))
    start=0.
    for segment in segments:
        if seconds<start+segment.duration:return segment,seconds-start,start
        start+=segment.duration
    return segments[-1],max(0.,segments[-1].duration-1/24),start-segments[-1].duration


def movie_duration(path):
    try:
        stat=Path(path).stat()
        return _movie_duration(str(path),stat.st_size,stat.st_mtime_ns)
    except OSError:return 0.


@lru_cache(maxsize=64)
def _movie_duration(path,size,modified):
    try:
        with av.open(str(path)) as media:
            stream=media.streams.video[0]
            return float(stream.duration*stream.time_base) if stream.duration is not None else float(media.duration/av.time_base)
    except (OSError,ValueError,av.FFmpegError,IndexError,TypeError):return 0.


def resolve_preview(segments,seconds,final_path='',block_cache=None):
    """Runs off the UI thread, including archive extraction and image decoding."""
    segment,local,start=segment_at(segments,seconds)
    if segment is None:return {'kind':'empty'}
    seconds=start+local
    if final_path and Path(final_path).is_file() and seconds<movie_duration(final_path):
        return dict(kind='rendered',path=final_path,position=round(seconds*1000),name='Rendered sequence')
    cache=(block_cache or {}).get(str(segment.render_group),{})
    result=cache.get('result',{})
    path=result.get('path','')
    group_start=sum(item.duration for item in segments[:segments.index(segment)] if item.render_group!=segment.render_group)
    if path and Path(path).is_file() and seconds-group_start<movie_duration(path):
        return dict(kind='rendered',path=path,position=round((seconds-group_start)*1000),name='Rendered block')
    if segment.kind=='text':return dict(kind='text',text=segment.prompt or segment.name,name=segment.name)
    if segment.kind=='image':
        path=segment.preview_path
        if not path or not Path(path).is_file():path=materialize_source(segment)
        reader=QImageReader(str(path));size=reader.size()
        if size.isValid() and max(size.width(),size.height())>1280:
            from PySide6.QtCore import Qt
            reader.setScaledSize(size.scaled(1280,1280,Qt.AspectRatioMode.KeepAspectRatio))
        image=reader.read()
        if image.isNull():raise ValueError('Conditioning image is unavailable: '+segment.name)
        return dict(kind='image',image=image,name=segment.name)
    path=materialize_source(segment)
    if not Path(path).is_file():raise ValueError('Conditioning video is unavailable: '+segment.name)
    return dict(kind='video',path=path,position=round(((segment.trim_start or 0)/24+local)*1000),name=segment.name)


class TimelinePreviewController(QObject):
    def __init__(self,owner):
        super().__init__(owner);self.owner=owner;self.panel=owner.project_preview_panel
        self.scrubber=AudioScrubber(self.panel.player,self)
        self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.setInterval(40)
        self.timer.timeout.connect(self._resolve)
        self.serial=0;self.epoch=0;self.worker=False;self.latest=0.;self.last_scope=None
        self.pending_seek=None;self.dragging=False;self.queued=False
        self.panel.player.mediaStatusChanged.connect(self._media_ready)
        owner.ruler.scrub_started.connect(self.begin)
        owner.ruler.scrub_requested.connect(self.seek)
        owner.ruler.scrub_finished.connect(self.end)
        for slider,callback in [(self.panel.seek,self.panel.seek_to),
                                (self.panel.fullscreen_window.seek,self.panel.fullscreen_window.seek_to)]:
            slider.seek_requested.disconnect(callback)
            slider.seek_requested.connect(self.seek_player)
            slider.scrub_started.connect(self.begin_player)
            slider.scrub_finished.connect(self.scrubber.end)

    def scope(self):
        return self.owner.workspace_revision,self.owner.current_project_id,self.owner.project_type,self.owner._edit_serial

    def begin(self):
        if self.panel._rendering or self.owner.workspace_engine=='shot_sequence':return
        self.dragging=True;self.scrubber.begin()

    def end(self):
        self.dragging=False;self.scrubber.end()

    def cancel(self):
        self.serial+=1;self.epoch+=1;self.timer.stop();self.pending_seek=None;self.dragging=False;self.queued=False;self.scrubber.cancel()
        if self.owner.ruler._scrubbing:self.owner.ruler._scrubbing=False;self.owner.ruler.releaseMouse()

    def begin_player(self):
        if not self.panel._rendering and self.panel.duration>0 and self.panel.player.source().isLocalFile():self.scrubber.begin()

    def seek_player(self,position):
        if self.panel._rendering or self.panel.duration<=0 or not self.panel.player.source().isLocalFile():return
        self.panel._initial_media_pending=False
        self.scrubber.seek(position)

    def seek(self,seconds):
        if self.panel._rendering or self.owner.workspace_engine=='shot_sequence':return
        self.latest=max(0.,min(float(seconds),self.owner.total_duration()))
        self.serial+=1;self.queued=True;self.owner.ruler.playhead=self.latest;self.owner.ruler.update()
        if not self.worker and not self.timer.isActive():self.timer.start()

    def _resolve(self):
        if self.worker or not self.owner.segments:return
        self.worker=True;self.queued=False;serial=self.serial;epoch=self.epoch;scope=self.scope();seconds=self.latest
        value=self.owner._video_preview_cache.get(self.owner.video_preview_key(),{})
        meta=self.owner.current_library_metadata() or {}
        final=value.get('path','') if value.get('kind')=='final' else meta.get('previewVideoPath','')
        args=(copy.deepcopy(self.owner.segments),seconds,final,copy.deepcopy(self.owner.video_render_cache))
        def finish(result=None,error=None):
            self.worker=False
            current=scope==self.scope() and epoch==self.epoch and not self.panel._rendering
            before=segment_at(args[0],seconds)[0];now=segment_at(self.owner.segments,self.latest)[0]
            same=before is not None and now is not None and before.id==now.id
            if current and (serial==self.serial or same and abs(seconds-self.latest)<.2):
                if result:self.show(result)
                elif error:self.owner.statusBar().showMessage(str(error),5000)
            if self.queued and not self.panel._rendering:self.timer.start()
        self.owner.start_disk_job(resolve_preview,args,lambda value:finish(value),lambda error:finish(error=error),allow_close_on_error=True)

    def show(self,result):
        kind=result['kind']
        if kind=='empty':return
        self.owner.project_preview_dock.show()
        if kind=='text':
            self.pending_seek=None;self.scrubber.cancel()
            self.panel.show_conditioning_text(result['name'],result['text']);return
        if kind=='image':
            self.pending_seek=None;self.scrubber.cancel()
            self.panel.show_static_image('Conditioning image · '+result['name'],result['image']);return
        rendered=kind=='rendered'
        name=result['name'] if rendered else 'Conditioning video · '+result['name']
        position=result['position'];path=result['path']
        current=self.panel.pending_preview_path==path and self.panel.player.source().toLocalFile()==path
        self.pending_seek=(path,position,rendered)
        if not current:self.panel.set_project(name,path)
        else:self.panel.title.setText(name)
        from PySide6.QtMultimedia import QMediaPlayer
        if current and self.panel.player.mediaStatus() in {QMediaPlayer.MediaStatus.LoadedMedia,QMediaPlayer.MediaStatus.BufferedMedia}:self._apply_seek()

    def _media_ready(self,status):
        from PySide6.QtMultimedia import QMediaPlayer
        ready={QMediaPlayer.MediaStatus.LoadedMedia,QMediaPlayer.MediaStatus.BufferedMedia}
        if status in ready and self.panel.player.mediaStatus() in ready:self._apply_seek()

    def _apply_seek(self):
        if self.pending_seek is None:return
        path,position,audible=self.pending_seek
        if self.panel.player.source().toLocalFile()!=path:return
        self.pending_seek=None;self.panel._initial_media_pending=False
        if audible:
            self.scrubber.seek(position)
            if not self.dragging:self.scrubber.end()
        else:
            self.scrubber.cancel();self.panel.player.setPosition(position);self.panel.player.pause()
