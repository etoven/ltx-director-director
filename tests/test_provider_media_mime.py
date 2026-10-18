"""Media transport must identify archive/cache contents, not narrative suffixes."""
import base64
import io
import json
import subprocess
import zipfile
from unittest.mock import patch

import imageio_ffmpeg
from PIL import Image

from ltx_prompt_director import ai, project_archive
from ltx_prompt_director.ai_stream import STREAM_SINK
from ltx_prompt_director.media import data_url, media_mime, safe_media_filename
from ltx_prompt_director.models import Segment
from ltx_prompt_director.minimax_reference import reference_inventory


def test_media_signatures_override_misleading_cache_suffixes(tmp_path):
    samples = [
        (b'\x00\x00\x00\x20ftypisom\x00\x00\x02\x00', 'video/mp4'),
        (b'\x00\x00\x00\x20ftypqt  \x00\x00\x02\x00', 'video/quicktime'),
        (b'\x1aE\xdf\xa3\x00webm', 'video/webm'),
        (b'\x1aE\xdf\xa3\x00matroska', 'video/x-matroska'),
        (b'RIFF\x20\x00\x00\x00AVI ', 'video/x-msvideo'),
        (b'RIFF\x20\x00\x00\x00WAVE', 'audio/wav'),
    ]
    for index, (raw, mime) in enumerate(samples):
        path = tmp_path / f'{index}.5625 — opening breath and urgent appeal.png'
        path.write_bytes(raw)
        assert media_mime(path) == mime
        encoded = data_url(str(path))
        assert encoded.startswith(f'data:{mime};base64,')
        assert base64.b64decode(encoded.split(',', 1)[1]) == raw
    image = tmp_path / 'image.webm'
    Image.new('RGB', (24, 16), 'blue').save(image, 'PNG')
    assert media_mime(image) == 'image/png'


def test_generate_uses_mp4_mime_for_legacy_archive_and_resaves_correct_suffix(tmp_path):
    video = tmp_path / 'clip.mp4'
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-y', '-f', 'lavfi', '-i',
                    'color=c=blue:s=32x24:d=0.25', '-r', '24', '-pix_fmt', 'yuv420p',
                    str(video)], check=True, capture_output=True)
    raw = video.read_bytes()
    preview = tmp_path / 'preview.jpg'
    Image.new('RGB', (32, 24), 'blue').save(preview)
    segment = Segment('Opening breath', '', '', 'video', 'start', 'A blue opening.', .25)
    member = 'media/0000.5625 — opening breath and urgent appeal'
    archive = tmp_path / 'legacy.LTXD'
    frame = {**segment.to_dict(), 'archiveSource': member, 'archivePreview': 'previews/0000.jpg'}
    with zipfile.ZipFile(archive, 'w') as output:
        output.writestr(member, raw)
        output.writestr('previews/0000.jpg', preview.read_bytes())
        output.writestr('project.json', json.dumps({'frames': [frame]}))
    with patch.object(project_archive, 'APP_CACHE', tmp_path / 'cache'):
        loaded = project_archive.read_project(archive)['frames'][0]
        assert loaded['name'] == 'Opening_breath.mp4'
        assert loaded['_archiveMediaPath'].endswith('.mp4')
        assert not __import__('pathlib').Path(loaded['_archiveMediaPath']).exists()
        segment.media_path = loaded['_archiveMediaPath']
        segment.preview_path = loaded['_archivePreviewPath']
        segment._archive_source = (str(archive), member)
        token = STREAM_SINK.set(lambda kind, text: None)
        try:
            with patch.object(ai, '_gemini_stream', return_value='{"prompt":"A blue opening."}') as stream:
                result = ai.build_minimax_h3_reference_prompt(
                    [segment], 'gemini', 'gemini-flash-lite-latest', 'key', '', '', False, False, False, 30)
        finally:
            STREAM_SINK.reset(token)
        assert result == 'A blue opening.'
        parts = stream.call_args.args[0]
        media = next(part['inline_data'] for part in parts if 'inline_data' in part)
        assert media['mime_type'] == 'video/mp4'
        assert base64.b64decode(media['data']) == raw
        saved = tmp_path / 'saved.LTXD'
        project_archive.save_project_archive(saved, {'frames': [segment.to_dict()]}, [segment])
    with zipfile.ZipFile(saved) as output:
        source = json.loads(output.read('project.json'))['frames'][0]['archiveSource']
        assert source == 'media/0000.mp4'
        assert output.read(source) == raw
    assert video.read_bytes() == raw


def test_media_names_are_safe_and_keep_the_actual_extension():
    assert safe_media_filename('0.5625 — opening breath', suffix='.mp4') == '0.5625_opening_breath.mp4'
    assert safe_media_filename('Wrong.png', suffix='.mp4') == 'Wrong.mp4'
    assert safe_media_filename(r'C:\folder\NUL.png', suffix='.jpg') == '_NUL.jpg'
    assert safe_media_filename('../bad:*?name.webm', suffix='.mp4') == 'bad_name.mp4'
    assert len(safe_media_filename('🐾' * 300 + 'a' * 300, suffix='.mp4').encode()) < 180


def test_reference_source_names_survive_thumbnails_load_and_resave(tmp_path):
    thumbnail = io.BytesIO()
    Image.new('RGB', (24, 16), 'blue').save(thumbnail, 'JPEG')
    image = io.BytesIO()
    Image.new('RGB', (24, 16), 'red').save(image, 'PNG')
    video = b'\x00\x00\x00\x20ftypisom\x00\x00\x02\x00original video bytes'
    audio = b'RIFF\x20\x00\x00\x00WAVEoriginal audio bytes'
    references = [
        dict(name='Leadin-Marker.jpg', kind='video', role='scene', notes='Opening shot',
             archiveMedia='references/1-path.jpg', archiveAudio='references/1-audio.png',
             archiveImage='references/1.png', audio_only=False),
        dict(name='Voice.jpg', kind='audio', role='voice', notes='Voice reference',
             archiveMedia='references/2-path.png', archiveAudio='references/2-audio.png',
             archiveImage='references/2.png'),
        dict(name='Still.webm', role='style', notes='Still reference', archiveImage='references/3.png'),
    ]
    legacy = tmp_path / 'legacy.LTXD'
    with zipfile.ZipFile(legacy, 'w') as archive:
        archive.writestr('project.json', json.dumps(dict(frames=[], minimaxH3=dict(referenceImages=references))))
        archive.writestr('references/1-path.jpg', video)
        archive.writestr('references/1-audio.png', audio)
        archive.writestr('references/2-path.png', audio)
        archive.writestr('references/2-audio.png', audio)
        archive.writestr('references/1.png', thumbnail.getvalue())
        archive.writestr('references/2.png', thumbnail.getvalue())
        archive.writestr('references/3.png', image.getvalue())
    original = legacy.read_bytes()
    with patch.object(project_archive, 'APP_CACHE', tmp_path / 'cache'):
        loaded = project_archive.read_project(legacy)
        refs = loaded['minimaxH3']['referenceImages']
        assert [ref['name'] for ref in refs] == ['Leadin-Marker.mp4', 'Voice.wav', 'Still.png']
        assert [ref['label'] for ref in reference_inventory([], refs)] == ['Video1', 'Audio2', 'Image1']
        assert reference_inventory([], refs)[0]['audio_label'] == 'Audio1'
        assert refs[0]['notes'] == 'Opening shot'
        assert refs[0]['kind'] == 'video'
        assert base64.b64decode(refs[0]['image'].split(',', 1)[1]) == thumbnail.getvalue()
        # Also repair a stale display name and mismatched source suffix on save.
        refs[0]['name'] = 'Leadin-Marker.jpg'
        stale_source = tmp_path / 'original.jpg'
        stale_source.write_bytes(video)
        refs[0]['path'] = str(stale_source)
        saved = tmp_path / 'saved.LTXD'
        project_archive.save_project_archive(saved, loaded, [])
        with zipfile.ZipFile(saved) as archive:
            manifest = json.loads(archive.read('project.json'))['minimaxH3']['referenceImages']
            assert manifest[0]['name'] == 'Leadin-Marker.mp4'
            assert manifest[0]['archiveMedia'].endswith('.mp4')
            assert manifest[0]['archiveAudio'].endswith('.wav')
            assert archive.read(manifest[0]['archiveMedia']) == video
            assert archive.read(manifest[0]['archiveAudio']) == audio
        reopened = project_archive.read_project(saved)['minimaxH3']['referenceImages']
        assert [ref['name'] for ref in reopened] == ['Leadin-Marker.mp4', 'Voice.wav', 'Still.png']
        assert stale_source.read_bytes() == video
    assert legacy.read_bytes() == original
