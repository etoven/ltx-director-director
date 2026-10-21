"""Actual software media decoding and derivative repair; never initializes inference."""
import json
from pathlib import Path
import threading
from unittest.mock import patch
import av
from ltx_prompt_director.effects_library import ROOT
from ltx_prompt_director.effects_media import (prepare_effect_media, thumbnail_video, valid_image, digest, bundled_previews)


def test_shipped_thumbnail_videos_are_small_silent_and_valid():
    previews=bundled_previews(); assert len(previews)==17
    total=0
    for source,row in previews.items():
        target=ROOT/row['preview']; total+=target.stat().st_size
        assert digest(source)==row['source_sha256'] and digest(target)==row['sha256']
        with av.open(str(target)) as video:
            assert not video.streams.audio
            frame=next(video.decode(video=0)); assert (frame.width,frame.height)==(320,200)
            assert video.duration/av.time_base<=8.1
    assert total<500_000


def test_startup_repairs_bad_posters_and_corrupted_video_cache(tmp_path):
    source=ROOT/'reference_clips/CLIP001.mp4'; original=digest(source)
    broken=tmp_path/'broken.jpg'; broken.write_bytes(b'broken thumbnail')
    entries=[dict(id='test:video',name='Video',thumbnail=str(broken),preview=str(source)),
             dict(id='test:sound',name='Sound',thumbnail=str(broken),audio=str(ROOT/'audio_samples/source/CLIP001.wav'))]
    with patch('ltx_prompt_director.effects_media.bundled_previews',return_value={}):
        result=prepare_effect_media(entries,tmp_path/'cache')
        assert result['repaired']==2 and not result['issues']
        assert all(valid_image(row['thumbnail']) for row in result['entries'].values())
        target=Path(result['entries']['test:video']['videos'][str(source)])
        target.write_bytes(b'corrupted cache')
        fixed=prepare_effect_media(entries,tmp_path/'cache')
        assert fixed['checked']==2 and not fixed['issues']
        with av.open(str(target)) as video:assert next(video.decode(video=0)).width==320
    assert digest(source)==original and broken.read_bytes()==b'broken thumbnail'
    cancelled=threading.Event();cancelled.set()
    assert prepare_effect_media(entries,tmp_path/'cancelled',cancelled)['checked']==0


def test_missing_media_does_not_abort_other_thumbnail_checks(tmp_path):
    entries=[dict(id='missing',name='Missing',thumbnail=str(tmp_path/'absent.jpg'),preview=str(tmp_path/'absent.mp4')),
             dict(id='valid',name='Valid',thumbnail=str(ROOT/'thumbnails/FX001.jpg'))]
    result=prepare_effect_media(entries,tmp_path/'cache')
    assert result['checked']==2 and result['issues']
    assert valid_image(result['entries']['valid']['thumbnail'])
