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
panel = w.effects_panel
assert panel.tiles.count() == len(panel.entries)
bundled_ids = {entry['id'] for group in ('effects', 'clips', 'audio') for entry in catalog()[group]}
assert bundled_ids <= {entry['id'] for entry in panel.entries}
if (Path(ltx_prompt_director.__file__).parent / 'onboarding.py').is_file():
    from ltx_prompt_director.onboarding import OnboardingWizard
    wizard = OnboardingWizard(w)
    assert wizard.pages.count() == 6 and not wizard.header_source.isNull()
    wizard.reject()
    root = Path(ltx_prompt_director.__file__).parent / 'assets'
    assert len(list((root / 'onboarding').glob('*.png'))) == 6
    assert (root / 'help/index.html').is_file()
    assert (root / 'help/manual.js').is_file()
    assert (root / 'help/images/setup-wizard.png').is_file()
w._close_saves_queued = True
w.close()
print('Installed wheel verified:', ltx_prompt_director.__version__, flush=True)
sys.stderr.flush(); os._exit(0)
'''
    subprocess.run([sys.executable, '-c', code], cwd=folder, env=env, check=True, timeout=int(os.environ.get("DIRECTOR_WHEEL_TIMEOUT", "120")))
