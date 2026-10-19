"""Verify a wheel independently of the source checkout and user settings."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

wheel = str(Path(sys.argv[1]).resolve())
with tempfile.TemporaryDirectory(prefix='director-wheel-') as folder:
    target = Path(folder) / 'installed'
    subprocess.run([sys.executable, '-m', 'pip', 'install', '--no-deps', '--target', str(target), wheel], check=True)
    env = dict(os.environ, PYTHONPATH=str(target), QT_QPA_PLATFORM='offscreen',
               XDG_CONFIG_HOME=folder + '/config', XDG_DATA_HOME=folder + '/data', XDG_CACHE_HOME=folder + '/cache')
    code = '''
import os, sys
from pathlib import Path
from unittest.mock import patch
from PySide6.QtWidgets import QApplication
import ltx_prompt_director
from ltx_prompt_director.effects_library import catalog, ROOT, media_path
assert str(Path(ltx_prompt_director.__file__).resolve()).startswith(os.environ['PYTHONPATH'])
assert len(catalog()['effects']) == 100
for entry in catalog()['effects']:
    assert Path(media_path(entry['thumbnail'])).is_file()
for entry in catalog()['clips'] + catalog()['audio']:
    assert Path(media_path(entry['path'])).is_file()
from ltx_prompt_director.ui import MainWindow
app = QApplication([])
with patch.object(MainWindow, 'restore_startup_workspace'):
    w = MainWindow()
assert w.effects_panel.tiles.count() == 119
w._close_saves_queued = True
w.close()
print('Installed wheel verified:', ltx_prompt_director.__version__, flush=True)
sys.stderr.flush(); os._exit(0)
'''
    subprocess.run([sys.executable, '-c', code], cwd=folder, env=env, check=True, timeout=120)
