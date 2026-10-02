"""Legacy frame-workspace regressions explicitly install that optional definition.

It remains supported for old/custom projects but is no longer a factory workspace.
"""
import json
from importlib.resources import files
import pytest
from ltx_prompt_director.workspaces import WorkspaceStore


@pytest.fixture(autouse=True)
def install_legacy_workspace_for_existing_regressions(request, monkeypatch):
    if request.module.__name__.split('.')[-1] in {'test_catalog_release', 'test_shared_segment_editor'}:
        return
    original = WorkspaceStore.__init__
    def initialize(self, *args, **kwargs):
        original(self, *args, **kwargs)
        marker = self.root / '.test-legacy-workspaces'
        if marker.exists():
            return
        marker.touch()
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
