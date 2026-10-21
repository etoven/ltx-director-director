import json
import tempfile
from pathlib import Path
from PySide6.QtCore import QSettings
from ltx_prompt_director.effects_library import (ROOT, load_effects, save_effect,
    remove_effect, refinement, media_path, catalog)
from ltx_prompt_director.quick_fixes import save_presets


def settings_at(folder):
    return QSettings(str(Path(folder) / 'effects.ini'), QSettings.Format.IniFormat)


def test_migration_menu_defaults_and_round_trip_preserve_edits():
    with tempfile.TemporaryDirectory() as folder:
        settings = settings_at(folder)
        save_presets(settings, [{'name': 'My old fix', 'prompt': 'Keep this.', 'category': 'Camera', 'target': 'both'}])
        entries = load_effects(settings)
        assert len(entries) == 135
        assert [e['name'] for e in entries if e['show_in_menu']] == ['My old fix']
        old = entries[0]
        save_effect(settings, {**old, 'name': 'Edited', 'show_in_menu': False})
        effect = next(e for e in entries if e['id'] == 'FX001')
        save_effect(settings, {**effect, 'prompt': 'My revision', 'show_in_menu': True, 'target': 'both'})
        settings.sync()
        reopened = load_effects(settings_at(folder))
        assert reopened[0]['name'] == 'Edited'
        assert [e['id'] for e in reopened if e['show_in_menu']] == ['FX001']
        assert next(e for e in reopened if e['id'] == 'FX001')['prompt'] == 'My revision'
        # Bundled source records never change when edited.
        assert catalog()['effects'][0]['minimax_segment'] != 'My revision'
        remove_effect(settings, reopened[0])
        remove_effect(settings, effect)
        assert not any(e['id'] in {old['id'], effect['id']} for e in load_effects(settings))
        assert not any(e['name'] == 'My old fix' for e in load_effects(settings))


def test_all_catalog_artwork_and_related_media_resolve_and_no_audio_is_implied():
    clips = {e['id']: e for e in catalog()['clips']}
    audio = {e['id']: e for e in catalog()['audio']}
    with tempfile.TemporaryDirectory() as folder:
        entries = load_effects(settings_at(folder))
        effects = [e for e in entries if e['id'].startswith('FX')]
        assert len(effects) == 100
        for e in effects:
            assert Path(media_path(e['thumbnail'])).is_file()
            assert not e['tested_in_model']
            for cid in e['reference_clip_ids']:
                assert Path(media_path(clips[cid]['path'])).is_file()
            for aid in e['source_audio_ids']:
                assert Path(media_path(audio[aid]['path'])).is_file()
            prompt = refinement(e)
            assert '<Subject 1>' not in prompt
            assert 'No reference media is attached' in prompt
        assert media_path('../../secrets.txt') == ''
        assert not any(e['show_in_menu'] for e in effects)


def test_source_sounds_and_videos_have_editable_refinement_prompts():
    from ltx_prompt_director.effects_library import OVERRIDES_KEY, preview_sources
    with tempfile.TemporaryDirectory() as folder:
        settings = settings_at(folder)
        settings.setValue(OVERRIDES_KEY, json.dumps({'SRC001':{'prompt':''}, 'SRC002':{'prompt':'Keep my authored sound.'}}))
        entries = load_effects(settings)
        sounds = [e for e in entries if e['id'].startswith('SRC')]
        videos = [e for e in entries if e['id'].startswith('CLIP')]
        assert len(sounds)==17 and len(videos)==17
        assert all(e.get('prompt','').strip() for e in entries)
        assert all(not e['show_in_menu'] for e in sounds+videos)
        assert next(e for e in sounds if e['id']=='SRC002')['prompt']=='Keep my authored sound.'
        assert 'sound design' in next(e for e in sounds if e['id']=='SRC001')['prompt']
        assert all(preview_sources(e) for e in sounds+videos)
        assert all(c['kind']=='video' and Path(media_path(c['path'])).is_file() for e in videos for c in preview_sources(e))
        assert all('no reference media is attached' in e['prompt'].lower() for e in videos)
