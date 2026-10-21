"""Desktop picker routing without launching dialogs or accessing inference."""
import ast
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch
from ltx_prompt_director import file_dialogs as dialogs


def test_kde_open_save_folder_multiple_and_cancel():
    cases = [
        (lambda: dialogs.choose_document_open(None,'Model','/models','Weights (*.safetensors)'), '--getopenfilename', '/tmp/a model.safetensors\n', '/tmp/a model.safetensors'),
        (lambda: dialogs.choose_document_save(None,'Backup','/tmp/setup.zip','ZIP (*.zip)'), '--getsavefilename', '/tmp/setup.zip\n', '/tmp/setup.zip'),
        (lambda: dialogs.choose_directory(None,'Folder','/models'), '--getexistingdirectory', '/tmp/model files\n', '/tmp/model files'),
        (lambda: dialogs.choose_files(None,'Shots','/shots','Movies (*.mp4)'), '--getopenfilename', '/tmp/one shot.mp4\n/tmp/two.mp4\n', ['/tmp/one shot.mp4','/tmp/two.mp4']),
        (lambda: dialogs.choose_media_files(None,True,'/media'), '--getopenfilename', '/tmp/media.mp4\n', ['/tmp/media.mp4']),
    ]
    with patch.object(dialogs.sys,'platform','linux'), patch.object(dialogs.shutil,'which',return_value='/usr/bin/kdialog'), patch.object(dialogs,'QFileDialog') as qt:
        for choose, mode, output, expected in cases:
            with patch.object(dialogs.subprocess,'run',return_value=CompletedProcess([],0,output,'')) as run:
                assert choose() == expected
                command = run.call_args.args[0]
                assert command[0]=='kdialog' and mode in command
                if isinstance(expected,list):assert '--multiple' in command and '--separate-output' in command
            with patch.object(dialogs.subprocess,'run',return_value=CompletedProcess([],1,'','')):
                assert choose() == ([] if isinstance(expected,list) else '')
        qt.getOpenFileName.assert_not_called(); qt.getSaveFileName.assert_not_called()


def test_qt_and_zenity_fallbacks_keep_filters_and_selection():
    with patch.object(dialogs.sys,'platform','win32'), patch.object(dialogs.subprocess,'run') as run, patch.object(dialogs,'QFileDialog') as qt:
        qt.getOpenFileName.return_value=('/model','Weights')
        qt.getOpenFileNames.return_value=(['/a','/b'],'Movies')
        qt.getSaveFileName.return_value=('/backup','ZIP')
        qt.getExistingDirectory.return_value='/folder'
        assert dialogs.choose_document_open(None,'Model','/models','Weights (*.safetensors)')=='/model'
        assert qt.getOpenFileName.call_args.args[3]=='Weights (*.safetensors)'
        assert dialogs.choose_files(None,'Shots','','Movies (*.mp4)')==['/a','/b']
        assert dialogs.choose_document_save(None,'Save','backup.zip','ZIP (*.zip)')=='/backup'
        assert dialogs.choose_directory(None,'Folder','')=='/folder'
        run.assert_not_called()
    with patch.object(dialogs.sys,'platform','linux'), patch.object(dialogs.shutil,'which',side_effect=lambda name:'/usr/bin/zenity' if name=='zenity' else None), patch.object(dialogs.subprocess,'run',return_value=CompletedProcess([],0,'/a\n/b\n','')) as run:
        assert dialogs.choose_files(None,'Shots','','Movies (*.mp4)')==['/a','/b']
        assert run.call_args.args[0][0]=='zenity' and '--separator=\n' in run.call_args.args[0]


def test_all_file_pickers_share_desktop_routing():
    root=Path(dialogs.__file__).parent
    for path in root.glob('*.py'):
        if path.name=='file_dialogs.py':continue
        for node in ast.walk(ast.parse(path.read_text())):
            assert not (isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute)
                        and isinstance(node.func.value,ast.Name) and node.func.value.id=='QFileDialog'), path
