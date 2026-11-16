"""One native queue workflow for reopen, project navigation, export and shortcuts."""
import copy,json,time
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import Qt,QMimeData,QPointF
from PySide6.QtGui import QDragMoveEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from ltx_prompt_director import ui,local_generation
from ltx_prompt_director.models import Segment
from ltx_prompt_director.queue_persistence import video_spec

APP=QApplication.instance() or QApplication([])


def wait_for(predicate,seconds=12):
    until=time.monotonic()+seconds
    while not predicate() and time.monotonic()<until:APP.processEvents();time.sleep(.01)
    assert predicate()


def test_native_queue_reopen_navigate_drag_export_and_save_shortcuts(tmp_path,monkeypatch):
    from shiboken6 import isValid
    monkeypatch.setattr(ui,'project_library_path',lambda:tmp_path)
    with patch.object(ui.MainWindow,'restore_startup_workspace'):
        w=ui.MainWindow()
    w._close_saves_queued=True;other=None
    try:
        w.new_project();first=w.current_project_id
        w.intent.setPlainText('First project');w.mark_dirty();w.save_library_project(automatic=True)
        wait_for(lambda:not w._pending_disk_jobs)
        w.new_project();second=w.current_project_id
        w.intent.setPlainText('Second project');w.mark_dirty();w.save_all_projects()
        wait_for(lambda:not w._pending_disk_jobs)
        output=tmp_path/'rendered.mp4';output.write_bytes(b'completed video')
        w.render_queue.set_paused(True)
        complete=w.render_queue.enqueue(lambda:None,(),lambda result:None,lambda error:None,
            title='Finished',project_id=first,kind='Video',source='local',model='MiniMax H3',render_group=0)
        complete.state='Complete';complete.output_path=str(output)
        spec=video_spec(dict(kind='video',prompt='Continue',duration=4,width=256,height=256,seed=23,
            profile={'video_variant':'fl2va','hf_token':'NEVER-PERSIST-THIS'}),[],[],workspace_type='minimax_frames')
        waiting=w.render_queue.enqueue(lambda:None,(),lambda result:None,lambda error:None,
            title='Queued',project_id=second,kind='Video',source='local',mode='fl2va',render_group=0,
            portable=spec,dependencies=(complete.id,))
        independent=w.render_queue.enqueue(lambda:None,(),lambda result:None,lambda error:None,
            title='Independent',project_id=first,kind='Video',source='local',portable=spec)
        image_job=w.render_queue.enqueue(lambda:None,(),lambda result:None,lambda error:None,
            title='Flux image',project_id=first,kind='Image',source='local',model='Flux',
            portable=dict(type='artwork',record={'id':'flux-frame','artworkPrompt':'A cup'},provider='local',model='flux2',config={},source_path=''))
        w.resize(1400,950);w.show();w.render_queue_dock.show();w.render_queue_dock.raise_();APP.processEvents()
        panel=w.render_queue_panel
        class Move(QDragMoveEvent):
            def source(self):return panel.list
        mime=QMimeData();mime.setData('application/x-director-render-job',independent.id.encode())
        rect=panel.list.visualItemRect(panel.list.item(1))
        move=Move(rect.topLeft()+__import__('PySide6.QtCore',fromlist=['QPoint']).QPoint(20,2),Qt.DropAction.MoveAction,
                  mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
        panel.list.dragMoveEvent(move)
        assert panel.list.drop_y is not None and panel.list.drop_valid
        painted=panel.list.viewport().grab().toImage().pixelColor(8,panel.list.drop_y)
        assert painted.green()>painted.red() and painted.blue()>200
        panel.list.clear_drop();assert panel.list.drop_y is None
        # The actual queue tile opens its saved project without losing the other project.
        QTest.mouseClick(panel.list.viewport(),Qt.MouseButton.LeftButton,pos=panel.list.visualItemRect(panel.list.item(0)).center())
        wait_for(lambda:w.current_project_id==first)
        assert w.intent.toPlainText()=='First project'
        # Exports use configured downloads and the shared Recent exports tray.
        downloads=tmp_path/'Downloads';downloads.mkdir();w.settings.setValue('segment_export_dir',str(downloads))
        panel.file_action(complete,'Export')
        wait_for(lambda:(downloads/output.name).is_file())
        wait_for(lambda:w.download_tray.list.count()>0)
        assert (downloads/output.name).read_bytes()==output.read_bytes()
        # Requested shortcuts execute actual save actions.
        w.activateWindow();w.setFocus();QTest.qWait(30)
        w.intent.setPlainText('Saved by Shift S');w.mark_dirty()
        QTest.keyClick(w,Qt.Key.Key_S,Qt.KeyboardModifier.ShiftModifier)
        wait_for(lambda:not w._pending_disk_jobs)
        from ltx_prompt_director.project_archive import read_project
        assert read_project(str(tmp_path/f'{first}.LTXD'))['directorIntent']=='Saved by Shift S'
        w.intent.setPlainText('Saved by Shift A');w.mark_dirty()
        QTest.keyClick(w,Qt.Key.Key_A,Qt.KeyboardModifier.ShiftModifier)
        wait_for(lambda:not w._pending_disk_jobs)
        assert read_project(str(tmp_path/f'{first}.LTXD'))['directorIntent']=='Saved by Shift A'
        independent.state="Rendering";independent.percent=52
        w.queue_store.freeze();wait_for(lambda:not w._pending_disk_jobs)
        stored=(tmp_path/'render-queue.json').read_text()
        assert 'NEVER-PERSIST-THIS' not in stored
        with patch.object(ui.MainWindow,'restore_startup_workspace'):
            other=ui.MainWindow();other._close_saves_queued=True
        wait_for(lambda:not other.queue_store.restoring)
        jobs=other.render_queue.jobs
        assert [job.id for job in jobs]==[complete.id,waiting.id,independent.id,image_job.id]
        assert jobs[0].state=='Complete' and jobs[0].output_path==str(output)
        assert jobs[1].state=='Waiting' and jobs[1].dependencies==(complete.id,)
        assert other.render_queue.paused and jobs[1].recipe
        assert jobs[2].state=='Waiting' and jobs[2].percent is None
        # Resumed jobs reach the real worker transport entry point with restored inputs.
        requests=[];runs=[]
        from PIL import Image
        image_output=tmp_path/'flux.png';Image.new('RGB',(32,32),'red').save(image_output)
        def render(request,**kwargs):
            requests.append(request)
            return {'path':str(image_output if request['kind']=='image' else output)}
        monkeypatch.setattr(local_generation,'run_request',render)
        monkeypatch.setattr(local_generation,'validate_profile',lambda *args:None)
        other.render_queue.executor=lambda op,args,ready,failed,**options:runs.append((op,args,ready,failed))
        other.open_render_project(second);wait_for(lambda:other.current_project_id==second)
        with patch.object(other,'attach_group_video') as attach,patch.object(other.project_preview_panel,'set_project'):
            other.render_queue.set_paused(False);APP.processEvents()
            bank=tmp_path/'resumed.rgb';bank.write_bytes(bytes((200,30,60))*6*4)
            jobs[1].progress(dict(kind='preview',media='latent_frames',path=str(bank),width=6,height=4,latent_frames=1,frames=22,fps=24))
            assert other.project_preview_panel.latent_player.active and other.project_preview_panel.latent_player.playing
            op,args,ready,failed=runs[0];ready(op(*args));APP.processEvents()
            assert requests[0]['prompt']=='Continue' and jobs[1].state=='Complete'
            assert attach.call_args.args[1]==0 and attach.call_args.args[2][1]==second
            op,args,ready,failed=runs[1];ready(op(*args));APP.processEvents()
            op,args,ready,failed=runs[2];ready(op(*args));APP.processEvents()
            assert requests[-1]['kind']=='image' and jobs[3].state=='Complete'
            assert Path(jobs[3].output_path).read_bytes()==image_output.read_bytes()
            other.render_queue_panel.file_action(jobs[3],'Export')
            wait_for(lambda:(downloads/Path(jobs[3].output_path).name).is_file())
    finally:
        for window in (other,w):
            if window is not None and isValid(window):
                window._close_saves_queued=True;window.queue_store.frozen=True;window.queue_store.timer.stop()
                window.render_queue.shutdown();window.close()
