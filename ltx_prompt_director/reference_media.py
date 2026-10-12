"""Managed mixed MiniMax reference media and lossless soundtrack extraction."""
from pathlib import Path
import shutil
import subprocess
from uuid import uuid4
import imageio_ffmpeg
from PySide6.QtCore import QStandardPaths
from .media import prepare_media, data_url, TIMELINE_VIDEO_SUFFIXES

AUDIO_SUFFIXES = {'.wav', '.mp3', '.flac', '.ogg', '.m4a', '.aac', '.opus'}

def reference_audio(source, destination):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-y', '-v', 'error', '-i', str(source),
        '-map', '0:a:0', '-vn', '-c:a', 'pcm_s16le', str(destination)], capture_output=True)
    if result.returncode or not destination.exists() or destination.stat().st_size <= 44:
        destination.unlink(missing_ok=True)
        raise ValueError('This reference has no decodable audio track.')
    return destination


def import_reference(path, role='identity', notes=''):
    source = Path(path)
    kind = 'video' if source.suffix.lower() in TIMELINE_VIDEO_SUFFIXES else 'audio'
    root = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)) / 'media' / 'minimax-references' / uuid4().hex
    root.mkdir(parents=True, exist_ok=True)
    target = root / source.name
    shutil.copy2(source, target)
    value = {'name':source.name, 'kind':kind, 'path':str(target), 'role':role, 'notes':notes, 'audio_only':False}
    if kind == 'video':
        _, preview, frames, _ = prepare_media(str(target))
        value.update(image=data_url(preview), frame_count=frames)
        try:
            value['audio_path'] = str(reference_audio(target, root / 'soundtrack.wav'))
        except ValueError:
            value['audio_path'] = ''
    else:
        value['audio_path'] = str(reference_audio(target, root / 'soundtrack.wav'))
    return value
