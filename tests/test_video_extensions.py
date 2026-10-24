"""Real CPU video joins; no provider or model calls."""
import subprocess
import threading
from pathlib import Path
import av
import imageio_ffmpeg
import pytest
from ltx_prompt_director.video_extensions import blend_blocks, continuation_reference, duration


def clip(folder, name, seconds, color, audio=True):
    path=folder/(name+'.mp4')
    command=[imageio_ffmpeg.get_ffmpeg_exe(),'-hide_banner','-loglevel','error','-y',
             '-f','lavfi','-i',f'color=c={color}:s=64x64:r=24:d={seconds}']
    if audio:command+=['-f','lavfi','-i',f'sine=frequency=440:duration={seconds}','-c:a','aac']
    command+=['-c:v','libx264','-pix_fmt','yuv420p',str(path)]
    subprocess.run(command,check=True,capture_output=True)
    return str(path)


@pytest.mark.parametrize('lengths,audio', [([15,15],True),([10,5,5],True),([4,4],False)])
def test_blends_preserve_duration_and_mix_boundary_pixels(tmp_path,lengths,audio):
    paths=[clip(tmp_path,str(i),length,color,audio) for i,(length,color) in enumerate(zip(lengths,['red','blue','green']))]
    result=blend_blocks(paths,threading.Event(),durations=lengths)
    assert abs(duration(result['path'])-sum(lengths))<=1/24
    assert result['duration']==sum(lengths)
    with av.open(result['path']) as container:
        assert bool(container.streams.audio)==audio
        if audio:
            stream=container.streams.audio[0]
            assert abs(float(stream.duration*stream.time_base)-sum(lengths))<.1
        for index,frame in enumerate(container.decode(video=0)):
            if index==lengths[0]*24:
                pixel=frame.to_ndarray(format='rgb24')[32,32]
                assert pixel[0]>30 and pixel[2]>30, pixel
                break
    ref=continuation_reference(paths[0],lengths[0])
    assert ref['timeline_time']==0 and ref['duration']==1 and ref['start']==lengths[0]-1
    assert all(Path(path).is_file() for path in paths)


def test_cancelled_blend_preserves_blocks(tmp_path):
    paths=[clip(tmp_path,str(i),4,'red',False) for i in range(2)]
    cancel=threading.Event();cancel.set()
    with pytest.raises(RuntimeError,match='cancelled'):blend_blocks(paths,cancel)
    assert all(Path(path).is_file() for path in paths)
    assert not (tmp_path/'extended-blended.mp4').exists()
    assert not (tmp_path/'.extended-blended.partial.mp4').exists()


@pytest.mark.parametrize('mode',['cut','linear_blend','filmic_crossfade','perceptual_crossfade'])
def test_sequence_editor_modes_keep_total_duration(tmp_path,mode):
    paths=[clip(tmp_path,'first',4,'red'),clip(tmp_path,'second',4,'blue')]
    result=blend_blocks(paths,threading.Event(),durations=[4,4],transitions=[dict(blend=mode,overlap=2)])
    assert duration(result['path'])==8
    with av.open(result['path']) as media:
        frames=list(media.decode(video=0))
        assert len(frames)==192
        middle=frames[96].to_ndarray(format='rgb24')[32,32]
        if mode=='cut':assert middle[0]<20 and middle[2]>200
        else:assert middle[0]>30 and middle[2]>30


@pytest.mark.parametrize('side,middle',[('source',3),('destination',5)])
def test_blend_lives_inside_its_selected_side_without_changing_duration(tmp_path,side,middle):
    paths=[clip(tmp_path,'first',4,'red'),clip(tmp_path,'next',4,'blue')]
    result=blend_blocks(paths,threading.Event(),durations=[4,4],transitions=[dict(blend='linear_blend',overlap=2,side=side)])
    assert duration(result['path'])==8
    with av.open(result['path']) as media:
        frames=list(media.decode(video=0))
        assert len(frames)==192
        pixel=frames[middle*24].to_ndarray(format='rgb24')[32,32]
        assert pixel[0]>30 and pixel[2]>30
        outside=frames[(5 if side=='source' else 3)*24].to_ndarray(format='rgb24')[32,32]
        assert outside[0]<20 if side=='source' else outside[2]<20
