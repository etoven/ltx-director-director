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
                 'target': 'segment', 'prompt': '', 'show_in_menu': False,
                 'audio': sample['path'], 'origin': 'Mixed source soundtrack'}
        entry.update(overrides.get(entry['id'], {}))
        entries.append(entry)
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
