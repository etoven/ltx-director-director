"""Build the shipped offline manual from product guides. Requires markdown (dev only)."""
from pathlib import Path
import re
import shutil
import markdown
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'ltx_prompt_director/assets/help'
(OUT / 'images').mkdir(parents=True, exist_ok=True)
chapters = [
 ('start', 'Start here', ROOT / 'usage.md'),
 ('setup', 'Setup and local generation', ROOT / 'docs/local-generation-and-setup.md'),
 ('timeline', 'Media and timeline', ROOT / 'docs/media-and-timeline.md'),
 ('prompts', 'Prompt workflows', ROOT / 'docs/unified-workflows.md'),
 ('refinement', 'Refinement and audio', ROOT / 'docs/refinement-and-audio.md'),
 ('references', 'MiniMax references', ROOT / 'docs/minimax-references.md'),
 ('effects', 'Effects Library', ROOT / 'docs/effects-library.md'),
 ('projects', 'Projects and properties', ROOT / 'docs/performance-and-properties.md'),
 ('review', 'Video and project files', ROOT / 'docs/video-review-and-files.md'),
 ('shots', 'Finished shot sequences', ROOT / 'docs/shot-sequences.md'),
 ('workspaces', 'Workspaces and exports', ROOT / 'docs/workspace-definitions-and-exports.md'),
 ('comfy', 'ComfyUI handoff', ROOT / 'docs/comfyui-artwork.md'),
 ('install', 'Installation', ROOT / 'install.md'),
]
lookup = {str(path.relative_to(ROOT)): key for key, _, path in chapters}
sections = []
for key, title, path in chapters:
    source = path.read_text(encoding='utf-8')
    # Legacy guides retain useful screenshots; current scope rules below take priority.
    source = re.sub(r'\*The examples throughout.*?\*\n', '', source)
    html = markdown.markdown(source, extensions=['tables', 'fenced_code', 'sane_lists', 'toc'])
    def image(match):
        reference = match.group(1)
        original = (path.parent / reference).resolve()
        if not original.is_file(): raise FileNotFoundError(original)
        target = OUT / 'images' / original.name
        if not target.exists() or original.stat().st_size != target.stat().st_size or original.stat().st_mtime > target.stat().st_mtime:
            shutil.copy2(original, target)
        with Image.open(original) as picture: width, height = picture.size
        return f'loading="lazy" decoding="async" width="{width}" height="{height}" src="images/{target.name}"'
    html = re.sub(r'src="([^"]+)"', image, html)
    def link(match):
        value = match.group(1)
        if value.startswith(('https:', 'http:', '#', 'mailto:')): return match.group(0)
        target = (path.parent / value.split('#')[0]).resolve()
        try: relative = str(target.relative_to(ROOT))
        except ValueError: return match.group(0)
        if relative in lookup: return 'href="#' + lookup[relative] + '"'
        if relative.endswith('.png'): return 'href="images/' + Path(relative).name + '"'
        if relative.startswith('docs/') or relative.endswith('.md'): return 'href="#start"'
        return match.group(0)
    html = re.sub(r'href="([^"]+)"', link, html)
    sections.append(f'<section id="{key}" class="chapter" data-title="{title}"><span class="eyebrow">DIRECTOR GUIDE</span>{html}</section>')
nav = ''.join(f'<a href="#{key}">{title}</a>' for key, title, _ in chapters)
correction = '''<aside class="scope-note"><strong>Current editor scope:</strong> Segment Generate/Refine changes the selected beat only. Global acts on the full ordered sequence and rebuilds the combined prompt. The unified editor has independent Generate/Refine controls. Synchronization preserves the source side; timeline-to-combined synchronization uses the whole ordered timeline.</aside>'''
page = '''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Director · Interactive illustrated manual</title><link rel="stylesheet" href="manual.css"></head><body>
<aside id="sidebar"><a class="brand" href="#start">DIRECTOR<span>Interactive illustrated manual</span></a><label class="search-label" for="search">Find a feature</label><input id="search" type="search" placeholder="Search the guide…"><nav aria-label="Chapters">''' + nav + '''</nav><p id="search-status" aria-live="polite"></p><button id="tour-start">Take the quick tour</button><p class="small">Available offline · Aurora 1.13.0a101<br>External provider and source links need internet.</p></aside>
<main><header class="hero"><div><span class="eyebrow">YOUR CREATIVE WORKSPACE</span><h1>From first idea<br>to a directed sequence.</h1><p>A practical guide to planning, refinement, effects, local generation and production handoff.</p></div><img src="../onboarding/welcome.png" alt="Line-art director’s chair, camera and storyboard frames"></header>''' + correction + '''
<div id="sandbox"><h2>Try the scope controls</h2><p>This guide-only example shows which authored beats a refinement targets.</p><div class="beats"><button class="beat selected" data-beat="0">Opening</button><button class="beat" data-beat="1">Action</button><button class="beat" data-beat="2">Hold</button></div><label>Refine scope <select id="scope"><option value="segment">Selected segment</option><option value="global">Global sequence</option><option value="unified">Unified prompt</option></select></label><p id="scope-result" aria-live="polite"></p></div>
''' + ''.join(sections) + '''<footer><h2>Keep creating.</h2><p>Open Help → Setup wizard whenever you want to revisit your sources. The donation link will be added in a future update.</p><a href="../effects/index.html">Open the full source Effects catalog</a><p>Wizard header art was created for Director. Product screenshots illustrate the native controls; draft progress displays are interface examples, not model benchmarks.</p></footer></main>
<dialog id="tour"><button id="tour-close" aria-label="Close tour">×</button><span class="eyebrow">A SHORT CREATIVE LOOP</span><h2 id="tour-title"></h2><p id="tour-text"></p><p id="tour-count"></p><div class="tour-controls"><button id="tour-prev">Back</button><button id="tour-next">Next</button></div></dialog><script src="manual.js"></script></body></html>'''
(OUT / 'index.html').write_text(page, encoding='utf-8')
print('Built offline manual:', len(chapters), 'chapters')
