"""Run selected Qt regressions with fresh XDG directories and native process lifetimes."""
import ast
import os
from pathlib import Path
import subprocess
import sys
import tempfile

paths = sys.argv[1:] or ['tests/test_effects_ui.py', 'tests/test_minimax_editor.py']
for filename in paths:
    tree = ast.parse(Path(filename).read_text())
    selections = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name.startswith('test_'):
            selections.append(f'{filename}::{node.name}')
        if isinstance(node, ast.ClassDef):
            selections.extend(f'{filename}::{node.name}::{fn.name}' for fn in node.body
                              if isinstance(fn, ast.FunctionDef) and fn.name.startswith('test_'))
    for index, selection in enumerate(selections, 1):
        with tempfile.TemporaryDirectory(prefix='director-qt-') as folder:
            env = dict(os.environ, QT_QPA_PLATFORM='offscreen', PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',
                       XDG_CONFIG_HOME=folder + '/config', XDG_DATA_HOME=folder + '/data',
                       XDG_CACHE_HOME=folder + '/cache')
            code = ('import os,sys,pytest; rc=pytest.main(' + repr(['-q', selection])
                    + '); sys.stdout.flush(); sys.stderr.flush(); os._exit(rc)')
            print(f'{index}/{len(selections)} {selection}', flush=True)
            result = subprocess.run([sys.executable, '-X', 'faulthandler', '-c', code],
                                    env=env, timeout=120)
            if result.returncode:
                raise SystemExit(result.returncode)
