"""Run every conditioning-audio regression with a bounded Qt process lifetime."""
import ast
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    path = ROOT / 'tests/test_conditioning_audio.py'
    names = [node.name for node in ast.parse(path.read_text()).body
             if isinstance(node, ast.FunctionDef) and node.name.startswith('test_')]
    for name in names:
        with tempfile.TemporaryDirectory(prefix='director-audio-test-') as folder:
            environment = dict(os.environ, QT_QPA_PLATFORM='offscreen', PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
            for kind in ('CONFIG', 'DATA', 'CACHE'):
                environment[f'XDG_{kind}_HOME'] = str(Path(folder) / kind.lower())
            selection = f'tests/test_conditioning_audio.py::{name}'
            code = ('import os,sys,pytest; '
                    f'rc=pytest.main({["-q", selection]!r}); '
                    'sys.stdout.flush(); sys.stderr.flush(); os._exit(rc)')
            print(selection, flush=True)
            result = subprocess.run([sys.executable, '-X', 'faulthandler', '-c', code],
                                    cwd=ROOT, env=environment, timeout=90)
            if result.returncode:
                return result.returncode
    print(f'All {len(names)} conditioning-audio regressions passed.', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
