"""Media transport must identify archive/cache contents, not narrative suffixes."""
import base64
import json
import subprocess
import zipfile
from unittest.mock import patch

import imageio_ffmpeg
from PIL import Image

from ltx_prompt_director import ai, project_archive
from ltx_prompt_director.ai_stream import STREAM_SINK
from ltx_prompt_director.media import data_url, media_mime
from ltx_prompt_director.models import Segment


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
