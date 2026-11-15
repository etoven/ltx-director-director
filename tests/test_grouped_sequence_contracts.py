from pathlib import Path
import copy
import av
import numpy as np
import pytest
from ltx_prompt_director.local_generation import workspace_video_profile
from ltx_prompt_director.video_extensions import continuation_reference
from ltx_prompt_director.local_worker import read_reference_audio, read_reference_video
from ltx_prompt_director.project_archive import save_project_archive, read_project
from ltx_prompt_director.models import Segment


def test_workspace_modes_and_timed_av_continuation_archive(tmp_path):
    ref=tmp_path/'minimax_ref2va_pruned.safetensors';ref.touch()
    frame=tmp_path/'minimax_fl2va_pruned.safetensors';frame.touch()
    profile={'video_model':str(ref),'video_variant':'ref2va'}
    assert workspace_video_profile(profile,{'engine':'minimax_frames'})['video_model']==str(frame)
    assert workspace_video_profile(profile,{'engine':'minimax_frames'})['video_variant']=='fl2va'
    assert workspace_video_profile(profile,{'engine':'minimax_references'})['video_variant']=='ref2va'
    frame.unlink()
    with pytest.raises(ValueError,match='FL2VA'):workspace_video_profile(profile,{'engine':'minimax_frames'})
    path=tmp_path/'audiovisual.mp4'
    with av.open(str(path),'w') as container:
        video=container.add_stream('libx264',rate=24);video.width=128;video.height=96;video.pix_fmt='yuv420p'
        audio=container.add_stream('aac',rate=32000);audio.layout='stereo'
        for index in range(96):
            image=av.VideoFrame.from_ndarray(np.full((96,128,3),index,np.uint8),format='rgb24');image.pts=index
            for packet in video.encode(image):container.mux(packet)
        for packet in video.encode():container.mux(packet)
        samples=av.AudioFrame.from_ndarray(np.zeros((2,128000),np.float32),format='fltp',layout='stereo');samples.sample_rate=32000;samples.pts=0
        for packet in audio.encode(samples):container.mux(packet)
        for packet in audio.encode():container.mux(packet)
    reference=continuation_reference(path,4,1)
    assert reference['duration']==22/24 and reference['timeline_time']==0
    assert reference['audio_path']==str(path) and reference['continuation']
    frames=read_reference_video(str(path),height=96,width=128,start=reference['start'],duration=reference['duration'],fps=24)
    pcm,rate=read_reference_audio(str(path),start=reference['start'],duration=reference['duration'])
    assert len(frames)==22 and abs(pcm.shape[-1]-round(22/24*rate))<=1
    segment=Segment('Prompt','','','text','text','Keep this wording.',4)
    payload={'frames':[segment.to_dict()],'renderGroupOptions':{'0':{'clip':{'path':str(path),'duration':4}}},'groupedSequence':{'authoring':[segment.to_dict()]}}
    archive=tmp_path/'portable.LTXD';save_project_archive(archive,copy.deepcopy(payload),[segment])
    path.unlink()
    restored=read_project(archive)
    attachment=Path(restored['renderGroupOptions']['0']['clip']['path'])
    assert attachment.is_file() and restored['groupedSequence']['authoring'][0]['prompt']=='Keep this wording.'
    with av.open(str(attachment)) as movie:assert movie.streams.audio and movie.streams.video
