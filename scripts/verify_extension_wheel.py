"""Check extension modules/assets and exercise a clean installed wheel."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

ROOT=Path(__file__).resolve().parents[1]
wheel=Path(sys.argv[1]).resolve()
with zipfile.ZipFile(wheel) as package:
    count=0
    for name in package.namelist():
        if not name.startswith('ltx_prompt_director/') or name.endswith('/'):continue
        assert not name.endswith('.pyc'),name
        source=ROOT/name
        assert source.is_file() and source.read_bytes()==package.read(name),name
        count+=1
    for name in ('video_extensions.py','render_cache.py','extension_timeline.py','collection_combine.py'):
        assert 'ltx_prompt_director/'+name in package.namelist()
    assert 'ltx_prompt_director/assets/help/images/video-extensions-timeline.png' in package.namelist()
print(f'Wheel contents match {count} source/assets.',flush=True)
with tempfile.TemporaryDirectory(prefix='director-extensions-wheel-') as folder:
    root=Path(folder);target=root/'installed'
    subprocess.run([sys.executable,'-m','pip','install','--no-deps','--target',str(target),str(wheel)],check=True)
    cases=[
        ['tests/test_video_extensions.py','tests/test_render_cache.py','tests/test_collection_combine.py','tests/test_render_job_files.py'],
        ['tests/test_video_extension_ui.py::test_single_mix_box_has_only_edge_length_controls_and_stays_centered'],
        ['tests/test_video_extension_ui.py::test_original_single_video_is_reused_when_extension_is_added'],
        ['tests/test_video_extension_ui.py::test_edit_next_group_reuses_completed_prefix_and_blend_only_reuses_both'],
        ['tests/test_video_extension_ui.py::test_repeated_generate_and_switch_restores_inactive_latent_then_final'],
    ]
    for index,case in enumerate(cases):
        selections=[str(ROOT/value) for value in case]
        env={**os.environ,'PYTHONPATH':str(target),'QT_QPA_PLATFORM':'offscreen','PYTEST_DISABLE_PLUGIN_AUTOLOAD':'1',
             **{f'XDG_{key}_HOME':str(root/f'{index}-{key.lower()}') for key in ('CONFIG','DATA','CACHE')}}
        code=('import requests,os,sys;from pathlib import Path;import ltx_prompt_director;'
              'assert Path(ltx_prompt_director.__file__).resolve().is_relative_to(Path(os.environ["PYTHONPATH"]));'
              'import pytest;rc=pytest.main('+repr(['-q',*selections])+');'
              'sys.stdout.flush();sys.stderr.flush();os._exit(rc)')
        print('Installed extension regression:',', '.join(case),flush=True)
        subprocess.run([sys.executable,'-X','faulthandler','-c',code],cwd=root,env=env,check=True,timeout=180)
print('Installed extension wheel verified.',flush=True)
