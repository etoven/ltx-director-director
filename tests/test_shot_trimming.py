"""Linked NLA source intervals, lossless splits, cache reuse and codec-copy export."""
import copy
import io
from fractions import Fraction
from pathlib import Path

import av
import numpy as np
import pytest
from PySide6.QtCore import Qt,QPoint,QSettings
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QListWidgetItem
from ltx_prompt_director.models import Segment
from ltx_prompt_director.shot_sequence import DEFAULTS,probe
from ltx_prompt_director.shot_sequence_widgets import import_shots,render_shots
from ltx_prompt_director.shot_preview_cache import PreviewCache,make_plan
from ltx_prompt_director.shot_timeline import ShotTimeline


def changing_movie(path,offset=0):
    webm=path.suffix=='.webm'
    with av.open(str(path),'w') as output:
        video=output.add_stream('libvpx-vp9' if webm else 'mpeg4',rate=24)
        video.width=96;video.height=64;video.pix_fmt='yuv420p';video.codec_context.gop_size=12
        video.codec_context.max_b_frames=0
        if webm:video.options={'deadline':'realtime','cpu-used':'8'}
        audio=output.add_stream('libopus' if webm else 'aac',rate=48000);audio.layout='stereo'
        for i in range(48):
            image=np.full((64,96,3),[20+i*3,40,65],dtype=np.uint8)
            frame=av.VideoFrame.from_ndarray(image,format='rgb24');frame.pts=i+offset*24;frame.time_base=Fraction(1,24)
            for packet in video.encode(frame):output.mux(packet)
            t=np.arange(i*2000,(i+1)*2000)/48000
            samples=np.stack([(.04 if i<24 else .2)*np.sin(2*np.pi*440*t)]*2).astype('float32')
            sound=av.AudioFrame.from_ndarray(samples,format='fltp',layout='stereo')
            sound.sample_rate=48000;sound.pts=i*2000+offset*48000;sound.time_base=Fraction(1,48000)
            for packet in audio.encode(sound):output.mux(packet)
        for stream in (video,audio):
            for packet in stream.encode():output.mux(packet)


def decoded(data):
    with av.open(io.BytesIO(data)) as c:images=np.stack([f.to_ndarray(format='rgb24') for f in c.decode(video=0)])
    with av.open(io.BytesIO(data)) as c:audio=np.concatenate([f.to_ndarray() for f in c.decode(audio=0)],axis=1)
    return images,audio


def test_trimmed_audio_video_preview_bake_and_shared_source_cache(tmp_path):
    path=tmp_path/'changing.mp4';changing_movie(path)
    shot=import_shots([str(path)],tmp_path/'sources')[0]
    options={**DEFAULTS,'width':96,'height':64,'overlap':0,'blend':'cut'}
    cache=PreviewCache(root=tmp_path/'cache')
    whole=make_plan([shot],options);full_images,full_audio=decoded(cache.build(whole))
    assert cache.stats['source_builds']==1
    shot.trim_start=24;shot.duration=1;shot.shot_trimmed=True
    trimmed=make_plan([shot],options)
    assert all(ready for _,_,ready in cache.coverage(trimmed))
    images,audio=decoded(cache.build(trimmed))
    assert len(images)==24 and cache.stats['source_builds']==1
    assert np.max(abs(images.astype(float)-full_images[24:].astype(float)))<1
    assert np.sqrt(np.mean(audio**2))>.12
    baked=tmp_path/'trimmed.webm';render_shots([shot],options,str(baked))
    baked_images,baked_audio=decoded(baked.read_bytes())
    assert len(baked_images)==24 and baked_audio.shape==audio.shape
    assert np.max(abs(baked_images.astype(float)-images.astype(float)))<20
    assert np.max(abs(baked_audio-audio))<.012
    # Split the trimmed second into adjacent pieces; no duplicate source decode.
    right=copy.deepcopy(shot);right.id='split';right.trim_start=36;right.duration=.5;shot.duration=.5
    split=make_plan([shot,right],options)
    assert split.signature==trimmed.signature
    split_images,split_audio=decoded(cache.build(split))
    assert np.array_equal(split_images,images) and np.array_equal(split_audio,audio)
    assert cache.stats['source_builds']==1 and len(split.sources)==1


def test_native_video_audio_trim_handles_clamp_and_cancel():
    app=QApplication.instance() or QApplication([])
    timeline=ShotTimeline();timeline.resize(700,340);timeline.show();QTest.qWait(20)
    shot=Segment('Movie.mp4','','','video',duration=4,media_duration_frames=96,trim_start=0)
    item=QListWidgetItem(shot.name);item.setData(Qt.ItemDataRole.UserRole,shot.id);timeline.addItem(item)
    timeline.set_shots([shot],{'overlap':0});events=[];timeline.trim_requested.connect(lambda *args:events.append(args))
    def drag(edge,delta,audio=False,cancel=False):
        rect=timeline.clip_rect(0,audio);x=rect.left()+1 if edge=='start' else rect.right()-1
        point=QPoint(round(x),round(rect.center().y()));canvas=timeline.canvas
        QTest.mouseMove(canvas,point);QTest.mousePress(canvas,Qt.MouseButton.LeftButton,pos=point)
        assert canvas.trimming is not None
        QTest.mouseMove(canvas,point+QPoint(delta,0))
        if cancel:QTest.keyClick(timeline,Qt.Key.Key_Escape)
        QTest.mouseRelease(canvas,Qt.MouseButton.LeftButton,pos=point+QPoint(delta,0))
    try:
        drag('start',round(timeline.scale))
        assert events[-1]==(shot.id,24,96) and shot.trim_start==0
        shot.trim_start=24;shot.duration=3;timeline.set_shots([shot],{'overlap':0})
        drag('end',-round(timeline.scale),True)
        assert events[-1]==(shot.id,24,72)
        old=len(events);drag('start',30,cancel=True)
        assert len(events)==old and timeline.clips[0]['source_start']==24
        drag('start',-5000)
        assert events[-1]==(shot.id,0,96)
        drag('end',5000,True)
        assert events[-1]==(shot.id,24,96)
        drag('end',-5000,True)
        assert events[-1]==(shot.id,24,25)
        drag('start',5000)
        assert events[-1]==(shot.id,95,96)
    finally:timeline.close()


def test_split_context_trim_roundtrip_and_download_export(tmp_path,monkeypatch):
    from ltx_prompt_director import ui
    from ltx_prompt_director.workspaces import WorkspaceStore
    from ltx_prompt_director.project_archive import save_project_archive,read_project
    app=QApplication.instance() or QApplication([])
    store=WorkspaceStore(tmp_path/'ws');store.restore_stock()
    monkeypatch.setattr(ui,'WorkspaceStore',lambda:store)
    monkeypatch.setattr(ui.MainWindow,'restore_startup_workspace',lambda self:None)
    monkeypatch.setattr(ui,'project_library_path',lambda:tmp_path/'projects')
    path=tmp_path/'changing.mp4';changing_movie(path)
    window=ui.MainWindow();window.settings=QSettings(str(tmp_path/'settings.ini'),QSettings.Format.IniFormat)
    try:
        window.set_project_type('shot_sequence');window.segments=import_shots([str(path)],tmp_path/'sources')
        window.shot_sequence_settings={**DEFAULTS,'width':96,'height':64}
        window.refresh_timeline();panel=window.shot_sequence_panel;ident=window.segments[0].id
        panel.cache=PreviewCache(root=tmp_path/'preview-cache')
        panel.cache.build(panel._plan)
        panel.set_shot_status([ident],'Done');panel.trim_shot(ident,12,48)
        panel.shots.set_playhead(500)
        menu=panel.shots.status_menu(0)
        next(a for a in menu.actions() if a.text()=='Split at playhead').trigger()
        left,right=window.segments
        assert left.id==ident and right.id!=ident and left.shot_trimmed and right.shot_trimmed
        assert (left.trim_start,left.duration,right.trim_start,right.duration)==(12,.5,24,1)
        assert left.media_path==right.media_path and left.media_duration_frames==right.media_duration_frames==48
        opts=window.shot_sequence_settings
        assert opts['shot_transitions'][left.id]==dict(blend='cut',overlap=0,side='source')
        assert opts['shot_transitions'][right.id]['blend']=='perceptual_crossfade'
        assert opts['shot_statuses'][right.id]=='Done' and panel.shots.total==1.5
        assert all(clip['thumbs'] and clip['peaks'] for clip in panel.shots.clips)
        assert panel.cache.stats['source_builds']==1
        panel.shots.set_playhead(0)
        assert not next(a for a in panel.shots.status_menu(0).actions() if a.text()=='Split at playhead').isEnabled()
        jobs=[];monkeypatch.setattr(window,'start_disk_job',lambda *a,**kw:jobs.append((a,kw)))
        panel.shots.set_playhead(700)
        next(a for a in panel.shots.status_menu(1).actions() if a.text()=='Export segment video').trigger()
        function,args,success=jobs[-1][0][:3]
        assert args[0].id==right.id and args[0].trim_start==24 and args[0].duration==1
        function(*args);success(None)
        assert Path(args[1]).is_file() # success enters the existing download tray
        archive=tmp_path/'trimmed.LTXD';save_project_archive(archive,window.project_payload(False),window.segments)
        window.load_project_payload(read_project(archive));window.refresh_timeline()
        assert [(s.trim_start,s.duration,s.shot_trimmed) for s in window.segments]==[(12,.5,True),(24,1,True)]
        panel.shots.reorder([1],0)
        assert [(s.trim_start,s.duration) for s in window.segments]==[(24,1),(12,.5)]
    finally:
        window.project_dirty=False;window._close_saves_queued=True;window.close()


@pytest.mark.parametrize('suffix',['.mp4','.webm'])
@pytest.mark.parametrize('start,duration',[(0,1),(1,1),(.5,1)])
def test_stream_copy_keeps_original_packets_and_audio(tmp_path,suffix,start,duration):
    from ltx_prompt_director.ui import _export_segment_source
    source=tmp_path/('original'+suffix);changing_movie(source)
    target=tmp_path/('export'+suffix)
    shot=Segment(source.name,str(source),'','video',duration=duration,media_duration_frames=48,trim_start=round(start*24),shot_trimmed=True)
    _export_segment_source(shot,target)
    def packets(path):
        with av.open(str(path)) as c:
            codecs=[s.codec_context.name for s in c.streams]
            data={(p.stream.type,bytes(p)) for p in c.demux() if p.size}
            return codecs,data
    source_codecs,source_packets=packets(source);codecs,data=packets(target)
    assert codecs==source_codecs and {kind for kind,_ in data}=={'video','audio'}
    assert data<=source_packets # the encoded packets themselves are unchanged
    assert probe(target)['frames']==24
    assert not list(tmp_path.glob('.export.*'))
    shot.duration=2;shot.trim_start=0;shot.shot_trimmed=False
    _export_segment_source(shot,target)
    assert target.read_bytes()==source.read_bytes()


def test_stream_copy_trim_uses_video_origin_for_offset_movie(tmp_path):
    from ltx_prompt_director.segment_export import copy_video_interval
    source=tmp_path/'offset.mp4';changing_movie(source,offset=5)
    target=tmp_path/'trimmed.mp4';copy_video_interval(source,target,1,1)
    assert probe(target)['frames']==24
    with av.open(str(target)) as c:
        assert next(c.decode(video=0)).to_ndarray(format='rgb24')[:,:,0].mean()>85
