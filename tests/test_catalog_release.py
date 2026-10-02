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
    assert set(store.load()) == {'ltx', 'minimax_base_guide', 'minimax_full_reference_guide'}
    assert store.load()['minimax_full_reference_guide']['name'] == 'MiniMax · Full Reference'
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
    finish_catalog_import(catalog)
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
    assert set(values) == {'ltx', 'minimax_base_guide', 'minimax_full_reference_guide', 'my_custom'}
    assert 'imagePrompt' not in values['ltx']['generation_prompts']['generate']
    assert values['minimax_full_reference_guide']['engine'] == 'minimax_references'
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
                assert catalog.property("dropActive") is (event.type() != event.Type.Drop)
            finish_catalog_import(catalog)
            assert str(path) in [entry['source_path'] for entry in catalog.store.entries]
            imported = catalog.store.entries[-1]
            assert Path(imported['path']).read_bytes() == path.read_bytes()
            assert Path(imported['path']).is_relative_to(catalog.store.media_root)
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
    finish_catalog_import(catalog)
    assert catalog.store.entries[0]['folder'] == 'Selected/Incoming'
    assert catalog.current_folder == 'Selected'
    QThreadPool.globalInstance().waitForDone(10000)
    app.processEvents()
    catalog.close()


def test_catalog_copies_full_media_survives_source_removal_and_reuses_copy(tmp_path):
    source = tmp_path / 'source' / 'frame.png'
    source.parent.mkdir()
    Image.new('RGB', (64, 64), 'red').save(source)
    original = source.read_bytes()
    store = CatalogStore(tmp_path / 'director' / 'media-catalog.json')
    entry = store.add([str(source)], 'First')[0]
    managed = Path(entry['path'])
    assert managed != source
    assert managed.is_relative_to(store.media_root)
    assert managed.read_bytes() == original
    assert source.read_bytes() == original
    assert store.add([str(source)], 'Second')[0]['id'] == entry['id']
    assert len(store.entries) == 1
    source.unlink()
    assert managed.read_bytes() == original
    assert store.add([str(managed)], 'Third')[0]['id'] == entry['id']
    assert entry['folder'] == 'Third'
    assert len(store.entries) == 1
    assert Path(CatalogStore(store.path).entries[0]['path']).read_bytes() == original


def test_same_named_files_and_video_bytes_stay_distinct(tmp_path):
    store = CatalogStore(tmp_path / 'director' / 'media-catalog.json')
    files = []
    for index in range(2):
        path = tmp_path / str(index) / 'clip.mp4'
        path.parent.mkdir()
        path.write_bytes(bytes([index]) * 1024)
        files.append(path)
    entries = store.add([str(p) for p in files])
    assert entries[0]['path'] != entries[1]['path']
    for entry, original in zip(entries, files):
        assert Path(entry['path']).read_bytes() == original.read_bytes()


def test_copy_failure_does_not_register_incomplete_import(tmp_path):
    source = tmp_path / 'frame.png'
    Image.new('RGB', (16, 16), 'blue').save(source)
    store = CatalogStore(tmp_path / 'director' / 'media-catalog.json')
    import pytest
    with patch('ltx_prompt_director.catalog.shutil.copy2', side_effect=OSError('Disk full')):
        with pytest.raises(OSError):
            store.add([str(source)])
    assert store.entries == []
    assert not list(store.media_root.rglob('*.tmp'))
    assert source.is_file()


def finish_catalog_import(catalog):
    from PySide6.QtCore import QThreadPool
    app = QApplication.instance()
    QThreadPool.globalInstance().waitForDone(10000)
    app.processEvents()
    assert not catalog.import_busy


def test_directory_import_does_not_reimport_its_managed_copies(tmp_path):
    from ltx_prompt_director.catalog import import_catalog_paths
    source = tmp_path / 'Collection'
    source.mkdir()
    Image.new('RGB', (16, 16), 'red').save(source / 'image.png')
    store = CatalogStore(source / 'Director' / 'media-catalog.json')
    import_catalog_paths(store, [str(source)], '')
    assert len(store.entries) == 1
    import_catalog_paths(store, [str(source)], '')
    assert len(store.entries) == 1
    assert len(list(store.media_root.rglob('*.png'))) == 1


def test_context_edit_updates_canonical_name_tags_description_and_search(tmp_path):
    from PySide6.QtCore import QThreadPool
    from PySide6.QtWidgets import QDialog, QLineEdit, QTextEdit
    app = QApplication.instance() or QApplication([])
    source = tmp_path / 'frame.png'
    Image.new('RGB', (16, 16), 'red').save(source)
    store = CatalogStore(tmp_path / 'director' / 'media-catalog.json')
    store.add([str(source)])
    catalog = MediaCatalog(store=store)
    detached = catalog.tiles.item(0).data(Qt.ItemDataRole.UserRole)
    def accept(dialog):
        name, tags = dialog.findChildren(QLineEdit)
        name.setText('Opening portrait')
        tags.setText('hero, night, hero')
        dialog.findChild(QTextEdit).setPlainText('The opening frame')
        return QDialog.DialogCode.Accepted
    with patch.object(QDialog, 'exec', accept):
        catalog.edit_details([detached])
    assert store.entries[0]['name'] == 'Opening portrait'
    assert store.entries[0]['tags'] == ['hero', 'night']
    assert store.entries[0]['description'] == 'The opening frame'
    assert catalog.tiles.item(0).text() == 'Opening portrait'
    assert catalog.description.toPlainText() == 'The opening frame'
    assert CatalogStore(store.path).entries[0]['tags'] == ['hero', 'night']
    catalog.search.setText('hero')
    assert catalog.tiles.count() == 1
    QThreadPool.globalInstance().waitForDone(10000)
    app.processEvents()
    catalog.close()


def test_context_batch_tag_edit_updates_every_selected_entry(tmp_path):
    from PySide6.QtCore import QThreadPool
    from PySide6.QtWidgets import QDialog, QLineEdit
    app = QApplication.instance() or QApplication([])
    store = CatalogStore(tmp_path / 'director' / 'media-catalog.json')
    paths = []
    for index in range(2):
        path = tmp_path / f'frame{index}.png'
        Image.new('RGB', (16, 16), 'red').save(path)
        paths.append(str(path))
    store.add(paths)
    catalog = MediaCatalog(store=store)
    detached = [catalog.tiles.item(i).data(Qt.ItemDataRole.UserRole) for i in range(2)]
    def accept(dialog):
        dialog.findChildren(QLineEdit)[-1].setText('review, actor')
        return QDialog.DialogCode.Accepted
    with patch.object(QDialog, 'exec', accept):
        catalog.edit_details(detached)
    assert all(entry['tags'] == ['review', 'actor'] for entry in store.entries)
    assert [entry['name'] for entry in store.entries] == ['frame0.png', 'frame1.png']
    assert all(entry['tags'] == ['review', 'actor'] for entry in CatalogStore(store.path).entries)
    QThreadPool.globalInstance().waitForDone(10000)
    app.processEvents()
    catalog.close()


def test_f2_inline_rename_and_confirmed_delete(tmp_path):
    from PySide6.QtCore import QThreadPool
    from PySide6.QtWidgets import QLineEdit, QMessageBox
    from PySide6.QtTest import QTest
    app = QApplication.instance() or QApplication([])
    source = tmp_path / 'image.png'
    Image.new('RGB', (16, 16), 'blue').save(source)
    store = CatalogStore(tmp_path / 'catalog.json')
    entry = store.add([str(source)])[0]
    catalog = MediaCatalog(store=store)
    catalog.resize(640, 500)
    catalog.show()
    catalog.tiles.setCurrentRow(0)
    catalog.tiles.setFocus()
    app.processEvents()
    QTest.keyClick(catalog.tiles, Qt.Key.Key_F2)
    app.processEvents()
    editor = catalog.tiles.findChild(QLineEdit)
    assert editor and editor.isVisible()
    assert editor.geometry().top() > catalog.tiles.visualItemRect(catalog.tiles.item(0)).top() + 70
    editor.selectAll()
    QTest.keyClicks(editor, 'New name')
    QTest.keyClick(editor, Qt.Key.Key_Return)
    app.processEvents()
    assert store.entries[0]['name'] == 'New name'
    assert CatalogStore(store.path).entries[0]['name'] == 'New name'
    with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.No) as confirm:
        QTest.keyClick(catalog.tiles, Qt.Key.Key_Delete)
        assert confirm.called
    assert len(store.entries) == 1
    with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes):
        QTest.keyClick(catalog.tiles, Qt.Key.Key_Delete)
    assert store.entries == []
    assert Path(entry['path']).is_file()
    QThreadPool.globalInstance().waitForDone(10000)
    app.processEvents()
    catalog.close()


def test_reference_closed_preference_and_project_width_survive_workspace_changes(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = WorkspaceStore(tmp_path / 'workspaces')
    with patch.object(ui, 'WorkspaceStore', return_value=store), patch.object(ui.MainWindow, 'restore_startup_workspace'):
        window = ui.MainWindow()
    window.resize(1600, 1000)
    window.show()
    window._restoring_layout = False
    old = window.settings.value('reference_panel_open', False, bool)
    try:
        window.project_dock.show()
        app.processEvents()
        window.set_project_panel_width(350)
        window.set_project_type('minimax_references')
        window.show_reference_images()
        app.processEvents()
        window.minimax_panel.reference_dock.close()
        app.processEvents()
        assert not window.settings.value('reference_panel_open', True, bool)
        for mode in ['ltx', 'minimax_references', 'ltx', 'minimax_references']:
            window.set_project_type(mode)
            window.restore_project_type(mode, {})
            window.sync_minimax_panel()
            app.processEvents()
            assert window.minimax_panel.reference_dock.isHidden()
            assert abs(window.project_dock.width() - 350) <= 2
        assert window.settings.value('reference_panel_open', True, bool) is False
        with patch.object(ui, 'WorkspaceStore', return_value=store), patch.object(ui.MainWindow, 'restore_startup_workspace'):
            reopened = ui.MainWindow()
        reopened.set_project_type('minimax_references')
        assert reopened.minimax_panel.reference_dock.isHidden()
        reopened._close_saves_queued = True
        reopened.project_dirty = False
        reopened.close()
    finally:
        window.settings.setValue('reference_panel_open', old)
        window._close_saves_queued = True
        window.project_dirty = False
        window.close()


def test_bottom_library_extent_survives_tab_changes(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = WorkspaceStore(tmp_path / 'workspaces')
    with patch.object(ui, 'WorkspaceStore', return_value=store), patch.object(ui.MainWindow, 'restore_startup_workspace'):
        window = ui.MainWindow()
    window.resize(1600, 1200)
    window.show()
    window._restoring_layout = False
    try:
        window.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, window.project_dock)
        window.tabifyDockWidget(window.project_dock, window.project_files_dock)
        window.project_dock.show()
        window.project_files_dock.show()
        window.project_panel_height = 360
        window.restore_project_panel_width()
        window.project_dock.raise_()
        app.processEvents()
        for dock in [window.project_files_dock, window.project_dock, window.project_files_dock, window.project_dock]:
            dock.raise_()
            app.processEvents()
            app.processEvents()
            assert abs(window.project_dock.height() - 360) <= 3
    finally:
        window._close_saves_queued = True
        window.project_dirty = False
        window.close()
