import json
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import Qt, QMimeData, QUrl
from PySide6.QtWidgets import QApplication
from PIL import Image
from ltx_prompt_director.catalog import CatalogStore, MediaCatalog, local_paths
from ltx_prompt_director.workspaces import WorkspaceStore
from ltx_prompt_director.prompt_templates import stock_prompt
from ltx_prompt_director import ai, ui


def test_factory_workspaces_and_audio_only_ltx_response(tmp_path):
    store = WorkspaceStore(tmp_path / 'workspaces')
    assert set(store.load()) == {'ltx', 'minimax_references'}
    assert store.load()['minimax_references']['name'] == 'MiniMax'
    for kind in ['generate', 'refine']:
        text = stock_prompt('ltx', kind)
        assert 'imagePrompt' not in text
        assert 'Gemini' not in text
    result = ai._validate(json.dumps({'segments': [{'duration': 3, 'prompt': 'Move forward.'}], 'globalPrompt': 'A room.'}), 1)
    assert result['segments'][0]['prompt'] == 'Move forward.'


def test_catalog_metadata_persistence_and_url_interchange(tmp_path):
    source = tmp_path / 'image.png'
    Image.new('RGB', (32, 32), 'red').save(source)
    store = CatalogStore(tmp_path / 'catalog.json')
    store.folders = ['Scene', 'Scene/References']
    entry = store.add([str(source)], 'Scene')[0]
    entry.update(tags=['actor', 'night'], description='Opening frame')
    store.save()
    store.move([str(source)], 'Scene/References')
    restored = CatalogStore(store.path)
    assert restored.entries[0]['folder'] == 'Scene/References'
    assert restored.entries[0]['tags'] == ['actor', 'night']
    assert restored.entries[0]['description'] == 'Opening frame'
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(source))])
    assert local_paths(mime) == [str(source)]
    assert len(store.add([str(source)])) == 1
    assert len(store.entries) == 1


def test_dock_and_persistent_intent_toggle(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = WorkspaceStore(tmp_path / 'workspaces')
    with patch.object(ui, 'WorkspaceStore', return_value=store), patch.object(ui.MainWindow, 'restore_startup_workspace'):
        window = ui.MainWindow()
    original = window.direction_toggle.isChecked()
    try:
        window.direction_toggle.setChecked(False)
        assert window.segment_header.indexOf(window.direction_toggle) >= 0
        for mode in ['minimax_references', 'ltx']:
            window.set_project_type(mode)
            assert window.director_panel.isHidden()
            assert not window.direction_toggle.isHidden()
        assert window.catalog_dock.objectName() == 'mediaCatalogDock'
        assert not window.downloads_button.icon().isNull()
        assert not ui.toolbar_icon('export-project').isNull()
    finally:
        window.direction_toggle.setChecked(original)
        window._close_saves_queued = True
        window.project_dirty = False
        window.close()


def test_catalog_folder_import_filter_and_batch_move(tmp_path):
    app = QApplication.instance() or QApplication([])
    source = tmp_path / 'Shots'
    (source / 'Night').mkdir(parents=True)
    for file in [source / 'opening.png', source / 'Night' / 'ending.png']:
        Image.new('RGB', (32, 32), 'blue').save(file)
    store = CatalogStore(tmp_path / 'catalog.json')
    catalog = MediaCatalog(store=store)
    catalog.import_paths([str(source)], '')
    assert set(store.folders) == {'Shots', 'Shots/Night'}
    assert len(store.entries) == 2
    catalog.current_folder = 'Shots/Night'
    catalog.refresh_folders()
    catalog.refresh_tiles()
    assert catalog.current_folder == 'Shots/Night'
    assert catalog.tiles.count() == 1
    catalog.move_entries(store.entries[:], 'Shots')
    assert catalog.tiles.count() == 0
    catalog.current_folder = None
    store.entries[0]['tags'] = ['hero']
    catalog.search.setText('hero')
    assert catalog.tiles.count() == 1
    from PySide6.QtCore import QThreadPool
    QThreadPool.globalInstance().waitForDone(10000)
    app.processEvents()
    catalog.close()


def test_refinement_accepts_video_prompt_without_still_image(tmp_path):
    from ltx_prompt_director.models import Segment
    segment = Segment('Beat', '', '', kind='text', prompt='Walk', duration=3)
    with patch.object(ai, '_provider_raw', return_value='{"prompt":"Walk gently.","duration":3}'):
        result = ai.refine_segment_prompt([segment], 0, 'gemini', 'test', 'unused', '', 0, 400)
    assert result['prompt'] == 'Walk gently.'


def test_untouched_factory_workspace_upgrade_and_custom_preservation(tmp_path):
    import subprocess
    root = tmp_path / 'workspaces'
    root.mkdir()
    (root / '.initialized').touch()
    for key in ['ltx', 'minimax_frames', 'minimax_references']:
        old = subprocess.check_output(['git', 'show', f'38ee3d5:ltx_prompt_director/workspace_templates/{key}.json'])
        (root / f'{key}.json').write_bytes(old)
    custom = json.loads((root / 'ltx.json').read_text())
    custom.update(id='my_custom', name='Personal workspace')
    (root / 'my_custom.json').write_text(json.dumps(custom))
    values = WorkspaceStore(root).load()
    assert set(values) == {'ltx', 'minimax_references', 'my_custom'}
    assert 'imagePrompt' not in values['ltx']['generation_prompts']['generate']
    assert values['minimax_references']['name'] == 'MiniMax'
    assert values['my_custom'] == custom


def test_native_filesystem_drop_on_every_catalog_surface(tmp_path):
    from PySide6.QtCore import QPoint, QPointF, QThreadPool
    from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent
    app = QApplication.instance() or QApplication([])
    catalog = MediaCatalog(store=CatalogStore(tmp_path / 'catalog.json'))
    catalog.resize(760, 500)
    catalog.show()
    app.processEvents()
    try:
        for index, target in enumerate([catalog, catalog.tiles.viewport(), catalog.description.viewport(), catalog.search]):
            path = tmp_path / f'Frame {index}.png'
            Image.new('RGB', (16, 16), 'green').save(path)
            mime = QMimeData()
            mime.setUrls([QUrl.fromLocalFile(str(path))])
            for event in [QDragEnterEvent(QPoint(5, 5), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier),
                          QDragMoveEvent(QPoint(5, 5), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier),
                          QDropEvent(QPointF(5, 5), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)]:
                app.sendEvent(target, event)
                assert event.isAccepted()
            assert str(path) in [entry['path'] for entry in catalog.store.entries]
        assert len(catalog.store.entries) == 4
        assert catalog.search.text() == ''
        assert len(CatalogStore(catalog.store.path).entries) == 4
    finally:
        QThreadPool.globalInstance().waitForDone(10000)
        app.processEvents()
        catalog.close()


def test_native_directory_drop_uses_target_folder(tmp_path):
    from PySide6.QtCore import QPointF, QThreadPool
    from PySide6.QtGui import QDropEvent
    app = QApplication.instance() or QApplication([])
    source = tmp_path / 'Incoming'
    source.mkdir()
    Image.new('RGB', (16, 16), 'blue').save(source / 'image.png')
    catalog = MediaCatalog(store=CatalogStore(tmp_path / 'catalog.json'))
    catalog.store.folders = ['Selected']
    catalog.current_folder = 'Selected'
    catalog.refresh_folders()
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(source))])
    event = QDropEvent(QPointF(5, 5), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    catalog.dropEvent(event)
    assert event.isAccepted()
    assert catalog.store.entries[0]['folder'] == 'Selected/Incoming'
    assert catalog.current_folder == 'Selected'
    QThreadPool.globalInstance().waitForDone(10000)
    app.processEvents()
    catalog.close()
