"""Build software-compressed tile videos, retaining all original catalog media."""
from pathlib import Path
import json
from ltx_prompt_director.effects_library import ROOT, catalog
from ltx_prompt_director.effects_media import thumbnail_video, digest

destination = ROOT/'thumbnail_videos'; destination.mkdir(exist_ok=True)
manifest = []
for clip in catalog()['clips']:
    source = ROOT/clip['path']
    preview = Path(thumbnail_video(source, destination))
    manifest.append(dict(source=clip['path'], source_size=source.stat().st_size, source_sha256=digest(source),
                         preview=preview.relative_to(ROOT).as_posix(), sha256=digest(preview)))
(destination/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
print('Built', len(manifest), 'thumbnail videos:', sum((ROOT/r['preview']).stat().st_size for r in manifest), 'bytes')
