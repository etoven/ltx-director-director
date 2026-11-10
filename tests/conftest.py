"""Legacy frame-workspace regressions explicitly install that optional definition.

It remains supported for old/custom projects but is no longer a factory workspace.
"""
import json
from importlib.resources import files
import pytest
from ltx_prompt_director.workspaces import WorkspaceStore


@pytest.fixture(autouse=True)
def isolated_desktop_settings(tmp_path, monkeypatch):
    """Keep saved dock layouts and preferences inside the current test."""
    from PySide6.QtCore import QSettings
    config = tmp_path / 'desktop-config'
    for key, folder in [('CONFIG', config), ('DATA', tmp_path / 'desktop-data'),
                        ('CACHE', tmp_path / 'desktop-cache')]:
        folder.mkdir(exist_ok=True)
        monkeypatch.setenv('XDG_' + key + '_HOME', str(folder))
    # QApplication can be created during test collection, before fixtures run.
    # Set Qt's path explicitly as well as isolating child-process environments.
    for format in (QSettings.Format.NativeFormat, QSettings.Format.IniFormat):
        QSettings.setPath(format, QSettings.Scope.UserScope, str(config))


@pytest.fixture(autouse=True)
def install_legacy_workspace_for_existing_regressions(request, monkeypatch):
    if request.module.__name__.split('.')[-1] in {'test_h3_official_settings', 'test_global_summary', 'test_reference_definitions', 'test_catalog_release', 'test_shared_segment_editor', 'test_prompt_only_refinement', 'test_comfy_minimax_export', 'test_prompt_timecodes', 'test_segment_prompt_review'}:
        return
    original = WorkspaceStore.__init__
    def initialize(self, *args, **kwargs):
        original(self, *args, **kwargs)
        marker = self.root / '.test-legacy-workspaces'
        if marker.exists():
            return
        marker.touch()
        self.delete('minimax_official_skill_base')
        self.delete('minimax_official_skill_reference')
        for stem, engine in [('minimax_base_guide', 'minimax_frames'), ('minimax_full_reference_guide', 'minimax_references')]:
            value = json.loads(files('ltx_prompt_director').joinpath(f'engine_templates/{engine}.json').read_text())
            value['id'] = engine
            value['global_prompt'] = False
            if engine == 'minimax_frames':
                value['references']['untimed_slots'] = 2
                value['references']['kinds'] += ['identity', 'style', 'object', 'scene']
            self.save(value)
            self.delete(stem)
    monkeypatch.setattr(WorkspaceStore, '__init__', initialize)


@pytest.fixture(autouse=True)
def automatic_reference_transport_is_explicit(request, monkeypatch):
    """Unrelated UI regressions never launch background provider requests."""
    if request.module.__name__.split('.')[-1] == 'test_reference_definitions':return
    from ltx_prompt_director.structured_prompt_panel import StructuredPromptPanel
    monkeypatch.setattr(StructuredPromptPanel, 'drain_definition_queue', lambda self: None)
