"""Versioned bundled effects plus user-owned presets and non-destructive overrides."""
from __future__ import annotations
import json
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

from .quick_fixes import load_presets

ROOT = Path(__file__).parent / 'assets' / 'effects'
MIME = 'application/x-director-effect'
USER_KEY = 'effects/user_entries_v1'
OVERRIDES_KEY = 'effects/overrides_v1'
AUDIO_SUFFIXES = {'.wav', '.mp3', '.ogg', '.flac', '.m4a', '.aac', '.opus'}

SOUND_DIRECTIONS = {
    'SRC001': 'Short fibrous creaks, irregular small joint clicks and tense breathing around the hand movement.',
    'SRC002': 'Localized jaw creaks, small dental clicks and a strained breath, timed to the mouth movement.',
    'SRC003': 'Subtle surface-tension creaks and restrained breathing, with small rising accents as facial contours shift.',
    'SRC004': 'A brief rising jaw creak, cartilage ticks, coat rustle and a restrained nasal breath.',
    'SRC005': 'Deep muscular strain, fine dry coat rustle and a heavier exhale as the body settles.',
    'SRC006': 'Tension releasing through descending creaks, soft cloth movement and a breath returning to a human rhythm.',
    'SRC007': 'A close, chest-resonant animal growl with breath through the teeth and a short snarl attack.',
    'SRC008': 'Low tendon strain, small contact clicks and a soft weighted impact at each braced hand movement.',
    'SRC009': 'Slow shoulder creaks, cloth tension and a low exhale, rising and settling with the back contour.',
    'SRC010': 'Fine dry coat rustle over a sustained low creak, localized to the lower torso movement.',
    'SRC011': 'Slow shoulder and back creaks, deep muscular strain and cloth tension that release as the posture settles.',
    'SRC012': 'Restrained uneven breathing, small facial tension creaks and a low tremor that follows the performance.',
    'SRC013': 'Layered body strain and coat rustle within a contained space; time any cage contact to visible impacts.',
    'SRC014': 'A fibrous hand strain followed by a soft palm contact and slight scrape against the wall.',
    'SRC015': 'Feathered air movement, fine quill ticks and a shoulder creak synchronized to each wing movement.',
    'SRC016': 'Small dry dental clicks, a quiet bite-set click and a short controlled breath.',
    'SRC017': 'Quiet uneven breathing, restrained throat tension and subtle clothing movement during the early symptoms.',
}


def soundtrack_prompt(sample):
    return ('Refine only the sound design of the existing shot. ' + SOUND_DIRECTIONS[sample['id']]
            + ' Synchronize these sounds with the existing action and keep the existing dialogue, music, '
              'timing and visual description unless explicitly changed. Describe the sound in words; '
              'do not add reference labels or assume the preview recording is attached. The preview is a mixed soundtrack.')


def preview_sources(entry):
    """Available preview choices; original source files stay available for export."""
    choices = []
    def add(title, path, kind=None):
        if path and not any(c['path'] == path for c in choices):
            choices.append(dict(title=title, path=path, kind=kind or ('audio' if Path(path).suffix.lower() in AUDIO_SUFFIXES else 'video')))
    if entry.get('audio'):add('Sound preview', entry['audio'], 'audio')
    if entry.get('preview'):add('Preview', entry['preview'])
    clips = {c['id']:c for c in catalog()['clips']}
    for cid in entry.get('reference_clip_ids', []):
        if cid in clips:add(clips[cid]['title'], clips[cid]['path'], 'video')
    samples = {c['id']:c for c in catalog()['audio']}
    for aid in entry.get('source_audio_ids', []):
        if aid in samples:add(samples[aid]['title'], samples[aid]['path'], 'audio')
    return choices


@lru_cache(maxsize=1)
def catalog():
    def read(name):
        return json.loads((ROOT / name).read_text(encoding='utf-8'))
    return {name: read(path) for name, path in {
        'effects': 'effects.json', 'clips': 'sources/clips.json',
        'audio': 'sources/audio.json', 'species': 'species.json',
        'footage': 'sources/footage.json'}.items()}


def media_path(value):
    """Bundled relative paths may never escape their package."""
    if not value:
        return ''
    path = Path(value)
    if path.is_absolute():
        return str(path)  # User-selected local media.
    resolved = (ROOT / path).resolve()
    return str(resolved) if resolved.is_relative_to(ROOT.resolve()) else ''


def _read(settings, key, default):
    try:
        data = json.loads(settings.value(key, json.dumps(default)))
        return data if isinstance(data, type(default)) else default
    except (ValueError, TypeError):
        return default


def load_effects(settings):
    if not settings.contains(USER_KEY):
        # Snapshot legacy presets exactly once; the original settings remain recoverable.
        users = [{**entry, 'id': 'user:' + uuid4().hex, 'show_in_menu': True,
                  'origin': 'Migrated Quick Fix'} for entry in load_presets(settings)]
        settings.setValue(USER_KEY, json.dumps(users))
    overrides = _read(settings, OVERRIDES_KEY, {})
    entries = []
    for effect in catalog()['effects']:
        entry = {**effect, 'name': effect['effect_name'], 'target': 'segment',
                 'prompt': effect['minimax_segment'], 'show_in_menu': False,
                 'origin': 'Werecreature 1.2.0'}
        entry.update(overrides.get(entry['id'], {}))
        entries.append(entry)
    for sample in catalog()['audio']:
        entry = {**sample, 'name': sample['title'], 'category': 'Source soundtracks',
                 'target': 'segment', 'prompt': soundtrack_prompt(sample), 'show_in_menu': False,
                 'audio': sample['path'], 'origin': 'Mixed source soundtrack'}
        entry.update(overrides.get(entry['id'], {}))
        if not entry.get('prompt', '').strip():entry['prompt'] = soundtrack_prompt(sample)
        entries.append(entry)
    for clip in catalog()['clips']:
        entry = {**clip, 'name':clip['title'], 'category':'Video references', 'target':'segment',
                 'thumbnail':clip['poster'], 'preview':clip['path'], 'audio':'', 'show_in_menu':False,
                 'source_audio_note':clip.get('audio',''),
                 'origin':'Catalog video reference',
                 'prompt':('Refine the existing shot with this visual direction: ' + clip['visible_evidence']
                           + ' ' + clip['transfer_scope'] + ' Adapt the movement to the existing subject and retain '
                             'their identity, framing, lighting, background and unrelated action. Do not copy the '
                             'preview actor or add reference labels; no reference media is attached.')}
        entry.update(overrides.get(entry['id'], {})); entries.append(entry)
    return _read(settings, USER_KEY, []) + [e for e in entries if not e.get('hidden')]


def save_effect(settings, entry):
    if entry['id'].startswith('user:'):
        entries = _read(settings, USER_KEY, [])
        entries = [e for e in entries if e['id'] != entry['id']] + [entry]
        settings.setValue(USER_KEY, json.dumps(entries, ensure_ascii=False))
    else:
        overrides = _read(settings, OVERRIDES_KEY, {})
        overrides[entry['id']] = entry
        settings.setValue(OVERRIDES_KEY, json.dumps(overrides, ensure_ascii=False))


def remove_effect(settings, entry):
    if entry['id'].startswith('user:'):
        entries = [e for e in _read(settings, USER_KEY, []) if e['id'] != entry['id']]
        settings.setValue(USER_KEY, json.dumps(entries, ensure_ascii=False))
    else:
        save_effect(settings, {**entry, 'hidden': True})


def refinement(entry):
    """No reference is implied attached, and visual entries never opt into audio."""
    instruction = entry.get('prompt', '').strip()
    if entry.get('origin', '').startswith('Werecreature'):
        instruction = instruction.replace('<Subject 1>', 'the existing target subject in this shot')
        instruction += ('\n' + entry.get('match_instruction', '')
                        + '\nPreserve existing subject labels, shot structure, identity, composition, '
                        'lighting and unrelated motion. Apply only the requested region and endpoint. '
                        'No reference media is attached by this instruction. Do not introduce new '
                        'reference or audio labels or assume the preview demonstrates this effect.')
    return instruction
