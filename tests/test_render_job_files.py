"""Completed job file actions keep final renders and unrelated files safe."""
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication,QMessageBox
from ltx_prompt_director.render_queue import RenderQueue
from ltx_prompt_director.render_widgets import RenderQueuePanel

APP=QApplication.instance() or QApplication([])


def test_cleanup_only_selected_completed_group_and_file_export_drag(tmp_path):
    queue=RenderQueue(lambda op,args,ready,failed,**kw:ready(op(*args)))
    queue.set_paused(True)
    jobs=[]
    for index in range(3):
        path=tmp_path/f'job{index}.mp4';path.write_bytes(f'video{index}'.encode())
        job=queue.enqueue(lambda:None,(),lambda _:None,lambda _:None,title=f'Job {index}',kind='Video',
                          group_id='group',group_title='Generation 1',intermediate=index<2)
        job.state='Complete';job.output_path=str(path);jobs.append(job)
    unrelated=tmp_path/'original.mp4';unrelated.write_bytes(b'original')
    panel=RenderQueuePanel(queue);panel.refresh()
    assert panel.can_cleanup(panel.selected_job())
    jobs[-1].state='Waiting';assert not panel.can_cleanup(panel.selected_job());jobs[-1].state='Complete'
    destination=tmp_path/'export.mp4'
    with patch('ltx_prompt_director.render_widgets.choose_document_save',return_value=str(destination)):
        panel.file_action(jobs[-1],'Export…')
    assert destination.read_bytes()==b'video2'
    # Check the same filesystem URL a real external drag receives.
    drags=[]
    class Drag:
        def __init__(self,parent):pass
        def setMimeData(self,mime):drags.append(mime.urls()[0].toLocalFile())
        def exec(self,action):assert action==Qt.DropAction.CopyAction
    panel.list.setCurrentRow(3)
    with patch('ltx_prompt_director.render_widgets.QDrag',Drag):panel.list.startDrag(Qt.DropAction.CopyAction)
    assert drags==[jobs[-1].output_path]
    panel.list.setCurrentRow(0)
    with patch('ltx_prompt_director.render_widgets.QMessageBox.question',return_value=QMessageBox.StandardButton.Yes):
        panel.cleanup_selected()
    assert not (tmp_path/'job0.mp4').exists() and not (tmp_path/'job1.mp4').exists()
    assert (tmp_path/'job2.mp4').is_file() and unrelated.read_bytes()==b'original'
    assert destination.is_file() and not panel.can_cleanup(panel.selected_job())
    assert jobs[0].output_path==jobs[1].output_path==''
    panel.close();queue.shutdown()


def test_folder_reveal_uses_native_platform_file_manager(tmp_path):
    path=tmp_path/'render.mp4';path.write_bytes(b'video')
    queue=RenderQueue(lambda *args,**kwargs:None);queue.set_paused(True)
    job=queue.enqueue(lambda:None,(),lambda _:None,lambda _:None,title='Video',kind='Video');job.output_path=str(path)
    panel=RenderQueuePanel(queue)
    for platform,program in [('win32','explorer.exe'),('darwin','/usr/bin/open')]:
        with patch('ltx_prompt_director.render_widgets.sys.platform',platform),patch('ltx_prompt_director.render_widgets.QProcess.startDetached',return_value=(True,42)) as launch:
            panel.file_action(job,'Folder')
            assert launch.call_args.args[0]==program and str(path) in launch.call_args.args[1]
    panel.close();queue.shutdown()
