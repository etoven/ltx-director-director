import json
from importlib.resources import files
import zipfile
import pytest
from PySide6.QtCore import QSettings, QByteArray
from ltx_prompt_director.config_migration import copy_legacy_data
from ltx_prompt_director.storage_management import (
    export_setup, stage_import, apply_pending_setup, stage_reset, disk_usage, clear_cache,
    settings_snapshot, validate_setup)


def roots(path):
    result={'data':path/'data','configuration':path/'config'}
    for root in result.values():root.mkdir(parents=True)
    return result


def test_legacy_data_merges_and_rebases_custom_workspaces(tmp_path):
    latest=tmp_path/'a44';older=tmp_path/'legacy';target=tmp_path/'current'
    for root in (latest,older,target):(root/'workspaces').mkdir(parents=True)
    stock=json.loads(files('ltx_prompt_director').joinpath('workspace_templates/ltx.json').read_text())
    (latest/'workspaces/ltx.json').write_text(json.dumps(stock))
    custom={**stock,'name':'My customized workspace'}
    (older/'workspaces/ltx.json').write_text(json.dumps(custom))
    (older/'media').mkdir();(older/'media/shot.webm').write_bytes(b'original')
    metadata={'media':str(older/'media/shot.webm'),'external':'/external/shot.webm'}
    (older/'catalog.json').write_text(json.dumps(metadata))
    (older/'existing.json').write_text('{"old":true}');(target/'existing.json').write_text('{"new":true}')
    mappings=[(latest,target),(older,target)]
    assert copy_legacy_data(mappings)==4
    assert json.loads((target/'workspaces/ltx.json').read_text())['name']==custom['name']
    assert json.loads((target/'catalog.json').read_text())=={**metadata,'media':str(target/'media/shot.webm')}
    assert (target/'media/shot.webm').read_bytes()==b'original'
    assert (older/'catalog.json').read_text()==json.dumps(metadata)
    assert (target/'existing.json').read_text()=='{"new":true}'
    assert copy_legacy_data(mappings)==0


def test_complete_backup_restore_and_reset_preserve_media(tmp_path):
    original=roots(tmp_path/'old');destination=roots(tmp_path/'new')
    media=original['data']/'media/shot.webm';media.parent.mkdir();media.write_bytes(b'video and audio')
    project=original['data']/'projects/one.LTXD';project.parent.mkdir()
    project.write_text(json.dumps({'source':str(media)}))
    workspace=original['data']/'workspaces/custom.json';workspace.parent.mkdir();workspace.write_text('{"workspace":"custom"}')
    (original['configuration']/'extra.conf').write_text('complete configuration')
    preferences=QSettings(str(tmp_path/'old-settings.ini'),QSettings.Format.IniFormat)
    preferences.setValue('window/geometry',QByteArray(b'\x00\x01qtgeometry'))
    preferences.setValue('references',json.dumps([{'path':str(media)}]))
    preferences.setValue('api_key','test-only-placeholder')
    output=tmp_path/'backup.zip';export_setup(output,original,settings_snapshot(preferences))
    with zipfile.ZipFile(output) as archive:
        assert archive.read('data/media/shot.webm')==media.read_bytes()
        assert archive.read('configuration/extra.conf')==b'complete configuration'
    restored=QSettings(str(tmp_path/'new-settings.ini'),QSettings.Format.IniFormat)
    restored.setValue('old_setting',True)
    stage_import(output,destination['data'])
    assert apply_pending_setup(destination,restored)=='import'
    assert not restored.contains('old_setting')
    assert restored.value('window/geometry')==preferences.value('window/geometry')
    assert json.loads(restored.value('references'))[0]['path']==str(destination['data']/'media/shot.webm')
    assert json.loads((destination['data']/'projects/one.LTXD').read_text())['source']==str(destination['data']/'media/shot.webm')
    assert apply_pending_setup(destination,restored) is None
    stage_reset(destination['data']);assert apply_pending_setup(destination,restored)=='reset'
    assert not restored.allKeys() and not (destination['data']/'workspaces').exists()
    assert (destination['data']/'media/shot.webm').read_bytes()==b'video and audio'
    assert (destination['data']/'projects/one.LTXD').is_file()


def test_backup_validation_and_restore_rollback(tmp_path,monkeypatch):
    source=roots(tmp_path/'source');destination=roots(tmp_path/'destination')
    (source['data']/'first.json').write_text('{"new":true}')
    (source['data']/'second.json').write_text('{"new":true}')
    (destination['data']/'first.json').write_text('{"old":true}')
    output=tmp_path/'backup.zip';export_setup(output,source,{'setting':'new'})
    settings=QSettings(str(tmp_path/'preferences.ini'),QSettings.Format.IniFormat);settings.setValue('setting','old')
    stage_import(output,destination['data'])
    from ltx_prompt_director import storage_management as module
    replace=module.os.replace
    def fail_second(old,new):
        if str(new).endswith('second.json'):raise OSError('simulated full disk')
        return replace(old,new)
    monkeypatch.setattr(module.os,'replace',fail_second)
    with pytest.raises(OSError):apply_pending_setup(destination,settings)
    assert (destination['data']/'first.json').read_text()=='{"old":true}'
    assert not (destination['data']/'second.json').exists() and settings.value('setting')=='old'
    with zipfile.ZipFile(output,'a') as archive:archive.writestr('data/../../escape','bad')
    with pytest.raises(ValueError,match='invalid file path'):validate_setup(output)


def test_cache_usage_and_protected_open_media(tmp_path):
    unused=tmp_path/'unused.work';unused.write_bytes(b'12345')
    active=tmp_path/'open-source.webm';active.write_bytes(b'123456789')
    assert disk_usage([tmp_path])['bytes']==14
    assert clear_cache([tmp_path],[active])==5
    assert active.read_bytes()==b'123456789' and not unused.exists()
