"""Run a real, small inference through the same private worker as the desktop UI.

Example: python scripts/check_local_models.py video --reference /path/to/image.png
The low step count verifies loading, sampling, previews and encoding, not quality.
"""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QSettings
from ltx_prompt_director.local_generation import load_profile, run_request

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('kind', choices=['probe', 'image', 'video', 'text'])
parser.add_argument('--reference', action='append', default=[])
parser.add_argument('--encoder', help='Override the H3 encoder for this check only')
parser.add_argument('--python', dest='engine_python', help='Override the private inference interpreter for this check only')
parser.add_argument('--width', type=int, default=256)
parser.add_argument('--height', type=int, default=256)
parser.add_argument('--steps', type=int, default=2)
parser.add_argument('--duration', type=float, default=4)
parser.add_argument('--seed', type=int, default=None)
parser.add_argument('--prompt-file', type=Path, help='Use an exported unified production prompt verbatim')
parser.add_argument('--stop-after-step', type=int, default=0, help='Diagnostic only: decode after this many steps of the configured schedule; output remains unfinished')
args = parser.parse_args()
profile = load_profile(QSettings('PromptDirector', 'Prompt Director'))
if args.encoder: profile['video_encoder'] = str(Path(args.encoder).expanduser().resolve())
if args.engine_python: profile['python'] = str(Path(args.engine_python).expanduser().absolute())
references = []
for source in args.reference:
    path = Path(source).expanduser().resolve()
    if not path.is_file(): parser.error('Reference file does not exist: ' + str(path))
    kind = 'video' if path.suffix.lower() in ('.mp4', '.mov', '.webm') else 'audio' if path.suffix.lower() in ('.wav', '.mp3', '.flac') else 'image'
    references.append(dict(path=str(path), kind=kind))
prompt = 'A red ceramic cup on a wooden table in soft daylight.'
if args.kind == 'video':
    prompt = '''subject_definitions
<Subject 1> is a red ceramic cup on a wooden table. <Picture 1> supplies the visual style only.

summary
[reference generation] A single continuous shot slowly approaches <Subject 1> as a delicate curl of steam rises. The target adopts the illustrated visual style of <Picture 1>.

retention_analysis
<Picture 1> (visual style): partially_preserved - retain its illustration style while creating the requested cup scene.

detailed_description
[Shot 1] At 00:00.00, soft daylight illuminates a red ceramic cup resting on a wooden table. The camera begins a slow, steady push in. A thin curl of steam rises and gently drifts sideways. The camera gradually eases to a stop and holds on the warm cup through the end of the clip. There are no cuts, dialogue or visible text. SFX: quiet room tone and a faint breeze.

overall_soundscape
Quiet indoor room tone with a faint continuous breeze. No voices.

non_diegetic_music
N/A'''
if args.prompt_file: prompt = args.prompt_file.expanduser().read_text(encoding='utf-8')
request = dict(kind=args.kind, profile=profile, prompt=prompt,
               width=args.width, height=args.height, steps=args.steps, duration=args.duration, references=references,
               seed=args.seed if args.seed is not None else profile.get('video_seed', -1) if args.kind == 'video' else 42,
               diagnostic_steps=args.stop_after_step,
               max_new_tokens=256, images=[], schema={'type':'object','properties':{'prompt':{'type':'string'}},'required':['prompt']})
def display(event):
    if event['kind'] in ('progress', 'log', 'error'): print(event.get('message', ''), flush=True)
    else: print(json.dumps(event), flush=True)
try:
    result = run_request(request, display)
except KeyboardInterrupt:
    raise SystemExit('Check cancelled; its worker was stopped.')
except Exception as error:
    raise SystemExit(str(error))
print('CHECK PASSED: ' + str(result.get('path') or result.get('message') or result.get('text')), flush=True)
