"""Software-only, bounded thumbnail preparation and startup integrity checks."""
from __future__ import annotations
from array import array
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

from PIL import Image, ImageDraw
import imageio_ffmpeg
from .effects_library import ROOT, media_path, preview_sources

VIDEO_SIZE = (320, 200)
VERSION = 'effects-preview-v1'


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest() if hasattr(hashlib, 'file_digest') else hashlib.sha256(stream.read()).hexdigest()


def valid_image(path):
    try:
        with Image.open(path) as image:
            if min(image.size) < 16:return False
            image.verify()
        with Image.open(path) as image:image.load()
        return True
    except (OSError, ValueError, SyntaxError):return False


def _key(path):
    path = Path(path); info = path.stat()
    return hashlib.sha256(f'{VERSION}:{path.resolve()}:{info.st_size}:{info.st_mtime_ns}'.encode()).hexdigest()[:24]


def _ffmpeg(arguments, *, output=False):
    result = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-nostdin', '-hide_banner', '-loglevel', 'error',
                             '-hwaccel', 'none', '-threads', '1', *arguments],
                            capture_output=True, timeout=45, check=True)
    return result.stdout if output else None


def _video_valid(path, record):
    try:return Path(path).is_file() and Path(path).stat().st_size > 0 and digest(path) == record['sha256']
    except (OSError, KeyError):return False


def thumbnail_video(source, cache, bundled=None):
    source = Path(source); cache = Path(cache); cache.mkdir(parents=True, exist_ok=True)
    record = (bundled or {}).get(str(source.resolve()))
    if record and source.stat().st_size == record['source_size'] and digest(source) == record['source_sha256']:
        target = ROOT / record['preview']
        if _video_valid(target, record):return str(target)
    target = cache / (_key(source) + '.mp4'); metadata = target.with_suffix('.json')
    try:
        if _video_valid(target, json.loads(metadata.read_text())):return str(target)
    except (OSError, ValueError):pass
    temporary = target.with_name(target.stem + '.' + uuid4().hex + '.tmp.mp4')
    try:
        _ffmpeg(['-i', str(source), '-t', '8', '-an', '-vf',
                 'scale=320:200:force_original_aspect_ratio=decrease,pad=320:200:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=12',
                 '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '28', '-pix_fmt', 'yuv420p', '-threads', '1',
                 '-movflags', '+faststart', '-y', str(temporary)])
        # Require one real frame, rather than accepting an empty video container.
        frame = _ffmpeg(['-i', str(temporary), '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'], output=True)
        if len(frame) != VIDEO_SIZE[0]*VIDEO_SIZE[1]*3:raise ValueError('No usable video thumbnail frame')
        temporary.replace(target)
        info = {'sha256':digest(target), 'size':list(VIDEO_SIZE)}
        meta_tmp = metadata.with_name(metadata.name + '.' + uuid4().hex + '.tmp')
        meta_tmp.write_text(json.dumps(info)); meta_tmp.replace(metadata)
        return str(target)
    finally:temporary.unlink(missing_ok=True)


def repair_poster(source, kind, cache):
    target = Path(cache) / (_key(source) + '-poster.jpg')
    if valid_image(target):return str(target)
    temporary = target.with_name(target.stem + '.' + uuid4().hex + '.tmp.jpg')
    try:
        if kind == 'video':
            _ffmpeg(['-i', str(source), '-frames:v', '1', '-vf',
                     'scale=320:200:force_original_aspect_ratio=decrease,pad=320:200:(ow-iw)/2:(oh-ih)/2',
                     '-threads', '1', '-y', str(temporary)])
        else:
            raw = _ffmpeg(['-i', str(source), '-t', '8', '-vn', '-ac', '1', '-ar', '8000', '-f', 's16le', '-'], output=True)
            samples = array('h'); samples.frombytes(raw[:len(raw)//2*2])
            if sys.byteorder != 'little':samples.byteswap()
            image = Image.new('RGB', VIDEO_SIZE, '#222a36'); draw = ImageDraw.Draw(image)
            for x in range(16, 304, 3):
                left = (x-16)*len(samples)//288; right = max(left+1,(x-13)*len(samples)//288)
                peak = max((abs(v) for v in samples[left:right]), default=0)/32768
                height = max(2,round(peak*70)); draw.line((x,100-height,x,100+height), fill='#69c6d8', width=2)
            image.save(temporary, 'JPEG', quality=85)
        if not valid_image(temporary):raise ValueError('No usable thumbnail frame')
        temporary.replace(target); return str(target)
    finally:temporary.unlink(missing_ok=True)


def bundled_previews():
    try:
        rows = json.loads((ROOT/'thumbnail_videos/manifest.json').read_text())
        return {str((ROOT/row['source']).resolve()):row for row in rows}
    except (OSError, ValueError, KeyError):return {}


def prepare_effect_media(entries, cache, cancel=None):
    """Runs outside Qt's thread; repairs derivatives without editing source media/settings."""
    cache = Path(cache); cache.mkdir(parents=True, exist_ok=True)
    result = {'entries':{}, 'issues':[], 'repaired':0, 'checked':0}
    prepared = {}; bundled = bundled_previews()
    for entry in entries:
        if cancel is not None and cancel.is_set():break
        media = preview_sources(entry); output = {'videos':{}}; result['checked'] += 1
        for choice in media:
            if cancel is not None and cancel.is_set():return result
            if choice['kind'] != 'video':
                if not Path(media_path(choice['path'])).is_file():result['issues'].append(entry['name'] + ': sound preview unavailable')
                continue
            source = media_path(choice['path'])
            if source not in prepared:
                try:prepared[source] = thumbnail_video(source, cache, bundled)
                except (OSError, ValueError, subprocess.SubprocessError) as error:
                    prepared[source] = ''; result['issues'].append(entry['name'] + ': video preview unavailable (' + type(error).__name__ + ')')
            if prepared[source]:output['videos'][choice['path']] = prepared[source]
        thumbnail = media_path(entry.get('thumbnail'))
        if thumbnail and valid_image(thumbnail):output['thumbnail'] = thumbnail
        elif thumbnail or media:
            for choice in media:
                source = output['videos'].get(choice['path']) or media_path(choice['path'])
                try:
                    output['thumbnail'] = repair_poster(source, choice['kind'], cache)
                    result['repaired'] += 1; break
                except (OSError, ValueError, subprocess.SubprocessError):continue
            if not output.get('thumbnail'):result['issues'].append(entry['name'] + ': thumbnail unavailable')
        result['entries'][entry['id']] = output
    return result
