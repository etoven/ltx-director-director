"""Legacy frame-workspace regressions explicitly install that optional definition.

It remains supported for old/custom projects but is no longer a factory workspace.
"""
import json
from importlib.resources import files
import pytest
from ltx_prompt_director.workspaces import WorkspaceStore


@pytest.fixture(autouse=True)
def install_legacy_workspace_for_existing_regressions(request, monkeypatch):
    if request.module.__name__.split('.')[-1] in {'test_catalog_release'}:
        return
    original = WorkspaceStore.__init__
    def initialize(self, *args, **kwargs):
        original(self, *args, **kwargs)
        if not (self.root / 'minimax_frames.json').exists():
            self.save(json.loads(files('ltx_prompt_director').joinpath('engine_templates/minimax_frames.json').read_text()))
    monkeypatch.setattr(WorkspaceStore, '__init__', initialize)
