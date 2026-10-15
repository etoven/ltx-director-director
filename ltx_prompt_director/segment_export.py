"""Fast, lossless exports of source movie intervals without re-encoding."""
import os
from pathlib import Path
import subprocess
from uuid import uuid4

import imageio_ffmpeg
import av


def copy_video_interval(source,target,start,duration):
    target=Path(target);target.parent.mkdir(parents=True,exist_ok=True)
    temporary=target.with_name('.'+target.stem+'.'+uuid4().hex+target.suffix)
    # Input seeking retains the preceding keyframe needed by dependent frames.
    # Stream-copy boundary accuracy is consequently limited by the source GOP.
    with av.open(str(source)) as container:
        first=next(container.decode(video=0),None)
        if first is None:raise ValueError('The source movie has no decodable video frames.')
        origin=first.time or 0
    # Seek in video timestamps. In particular, Opus preroll may give WebM a
    # negative container start; relative seeking would miss an exact keyframe.
    command=[imageio_ffmpeg.get_ffmpeg_exe(),'-nostdin','-hide_banner','-loglevel','error','-y',
             '-seek_timestamp','1','-ss',f'{origin+start:.9f}','-i',str(source),'-t',f'{duration:.9f}',
             '-map','0:v:0','-map','0:a?','-map_metadata','0','-c','copy',str(temporary)]
    try:
        result=subprocess.run(command,capture_output=True,check=False)
        if result.returncode or not temporary.is_file() or not temporary.stat().st_size:
            message=result.stderr.decode('utf-8',errors='replace').strip()
            raise RuntimeError(message or 'The trimmed movie could not be exported.')
        os.replace(temporary,target)
        return str(target)
    finally:
        temporary.unlink(missing_ok=True)
