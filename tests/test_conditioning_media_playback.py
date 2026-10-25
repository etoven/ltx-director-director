"""Real trimmed media and native conditioning-panel preview regressions."""
import base64
import hashlib
from pathlib import Path
import av
from PIL import Image
from ltx_prompt_director.models import Segment


def source_movie(path,rate=24,offset=0):
    with av.open(str(path),'w') as movie:
        stream=movie.add_stream('libx264',rate=rate);stream.width=stream.height=32;stream.pix_fmt='yuv420p'
        stream.options={'g':'240','bf':'0'}
        for index in range(rate*6):
            color=('red','lime','blue')[min(2,index//(rate*2))]
            frame=av.VideoFrame.from_image(Image.new('RGB',(32,32),color));frame.pts=index+offset
            for packet in stream.encode(frame):movie.mux(packet)
        for packet in stream.encode():movie.mux(packet)
    return str(path)


def assert_green(image):
    r,g,b=image.convert('RGB').getpixel((16,16))
    assert g>150 and r<50 and b<50,(r,g,b)


def test_analysis_upload_and_storyboard_only_contain_selected_interval(tmp_path):
    import io
    from ltx_prompt_director import ai
    from ltx_prompt_director.media import video_storyboard_data_urls
    source=source_movie(tmp_path/'source.mp4');original=hashlib.sha256(Path(source).read_bytes()).hexdigest()
    preview=tmp_path/'preview.png';Image.new('RGB',(32,32),'lime').save(preview)
    segment=Segment('Selected source',source,str(preview),'video','start','Only the selected middle.',1,144,60)
    frames=video_storyboard_data_urls(source,start=2.5,duration=1)
    assert len(frames)==6 and all(2.5<=frame['timestamp']<3.5 for frame in frames)
    for frame in frames:assert_green(Image.open(io.BytesIO(base64.b64decode(frame['image'].split(',',1)[1]))))
    visual=ai._minimax_h3_inputs([segment],'openai')[0]
    assert 'video' not in visual and all(2.5<=frame['timestamp']<3.5 for frame in visual['video_frames'])
    uploaded=ai._minimax_h3_inputs([segment],'gemini')[0]
    assert 'selected source interval' in uploaded['video_analysis_mode']
    clip=tmp_path/'uploaded.mp4';clip.write_bytes(base64.b64decode(uploaded['video'].split(',',1)[1]))
    with av.open(str(clip)) as media:
        decoded=list(media.decode(video=0))
        assert 23<=len(decoded)<=25
        for frame in decoded:assert_green(frame.to_image())
    assert hashlib.sha256(Path(source).read_bytes()).hexdigest()==original


def test_local_condition_frames_stop_at_trim_out_even_with_offset_and_low_source_rate(tmp_path):
    from ltx_prompt_director.local_worker import read_reference_video
    source=source_movie(tmp_path/'offset.mp4',rate=12,offset=120)
    frames=read_reference_video(source,start=3.5,duration=.5,width=32,height=32,fps=24)
    assert len(frames)==12
    for frame in frames:assert_green(frame)
    assert read_reference_video(source,start=8,duration=1,width=32,height=32)==[]


def test_real_audio_scrub_decodes_at_source_trim_and_playback_advances(tmp_path):
    from PySide6.QtMultimedia import QAudioBufferOutput,QMediaPlayer
    from PySide6.QtTest import QTest
    from test_conditioning_audio import make_audio,APP
    from ltx_prompt_director.audio_timeline import AudioTransport
    from ltx_prompt_director.audio_tracks import AudioClip
    path=make_audio(tmp_path/'audio.wav')
    transport=AudioTransport();transport.duration=3000
    clip=AudioClip('Trimmed audio',str(path),4,trim_start=24,duration_frames=48)
    transport.set_clips([clip]);player,output=transport.players[clip.id];output.setMuted(True)
    sink=QAudioBufferOutput(player);player.setAudioBufferOutput(sink);buffers=[]
    sink.audioBufferReceived.connect(lambda buffer:buffers.append(buffer.startTime()) if buffer.byteCount() else None)
    transport.seek(500,True)
    for _ in range(60):
        QTest.qWait(20)
        if buffers:break
    assert buffers and 1_490_000<=buffers[0]<=1_570_000
    QTest.qWait(250)
    assert player.playbackState()==QMediaPlayer.PlaybackState.PausedState and abs(player.position()-1500)<70
    transport.play();QTest.qWait(250)
    assert transport.playing and transport.position>650 and player.playbackState()==QMediaPlayer.PlaybackState.PlayingState
    transport.pause();transport.set_clips([])


def test_conditioning_audio_panel_seeks_and_plays_unrendered_preview(tmp_path):
    from PySide6.QtTest import QTest
    from test_video_extension_ui import window,APP
    from test_conditioning_audio import make_audio
    from ltx_prompt_director.audio_tracks import AudioClip
    with window(tmp_path) as w:
        w.set_project_type('minimax_references')
        w.segments=[Segment('First','','','text','text','First condition',5),Segment('Second','','','text','text','Second condition',5)]
        w.refresh_timeline();w.start_disk_job=lambda op,args,ready,failed,**kwargs:ready(op(*args))
        panel=w.audio_panel;audio=make_audio(tmp_path/'audio.wav')
        panel.set_project([AudioClip('Audio',str(audio),4).to_dict()])
        for player,output in panel.transport.players.values():output.setMuted(True)
        panel.seek(6500,True);w.timeline_preview.timer.stop();w.timeline_preview._resolve()
        assert w.project_preview_panel.condition_text.toPlainText()=='Second condition'
        panel.seek(200,False);panel.toggle_play();QTest.qWait(250)
        assert panel.transport.playing and panel.transport.position>=350
        assert w.project_preview_panel.condition_text.toPlainText()=='First condition'
        panel.toggle_play();assert not panel.transport.playing


def test_conditioning_audio_transport_follows_rendered_preview_and_restores_mute(tmp_path):
    from PySide6.QtTest import QTest
    from PySide6.QtMultimedia import QMediaPlayer
    from test_video_extension_ui import window,APP
    from test_video_extensions import clip
    from test_conditioning_audio import make_audio
    from ltx_prompt_director.audio_tracks import AudioClip
    from ltx_prompt_director.render_groups import input_stamp
    with window(tmp_path) as w:
        w.set_project_type('minimax_references');w.segments=[Segment('Condition','','','text','text','Source',4)];w.refresh_timeline()
        movie=clip(tmp_path,'rendered',4,'purple');w.store_render_group()
        w._video_preview_cache[w.video_preview_key()]={'kind':'final','path':movie,'groupStamps':{'0':input_stamp(w.segments,w.capture_render_group())},'blendSettings':[]}
        w.start_disk_job=lambda op,args,ready,failed,**kwargs:ready(op(*args))
        panel=w.audio_panel;audio=make_audio(tmp_path/'audio.wav');panel.set_project([AudioClip('Audio',str(audio),4).to_dict()])
        for player,output in panel.transport.players.values():output.setMuted(True)
        w.project_preview_panel.audio.setMuted(False)
        w.resize(1300,900);w.show();APP.processEvents()
        panel.seek(500,False)
        # Establish the paused poster before testing playback, as when opening
        # an already rendered project. Decoder startup under host memory pressure
        # must not consume this four-second test clip's playback interval.
        for _ in range(250):
            QTest.qWait(20)
            if w.project_preview_panel.player.source().toLocalFile()==movie and w.project_preview_panel.player.duration()>0 and w.project_preview_panel.player.mediaStatus() in {QMediaPlayer.MediaStatus.LoadedMedia,QMediaPlayer.MediaStatus.BufferedMedia,QMediaPlayer.MediaStatus.BufferingMedia}:break
        panel.toggle_play()
        for _ in range(80):
            QTest.qWait(20)
            if w.project_preview_panel.player.source().toLocalFile()==movie and w.project_preview_panel.player.playbackState()==QMediaPlayer.PlaybackState.PlayingState:break
        assert w.project_preview_panel.player.source().toLocalFile()==movie
        assert w.project_preview_panel.player.playbackState()==QMediaPlayer.PlaybackState.PlayingState
        assert w.project_preview_panel.audio.isMuted() # conditioning sound has its own transport
        panel.toggle_play();QTest.qWait(50)
        assert not w.project_preview_panel.audio.isMuted()
        assert w.project_preview_panel.player.playbackState()==QMediaPlayer.PlaybackState.PausedState
