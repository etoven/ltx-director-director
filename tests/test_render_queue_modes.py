from unittest.mock import patch
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from ltx_prompt_director.local_widgets import progress_text
from ltx_prompt_director.render_queue import RenderQueue
from ltx_prompt_director.render_widgets import RenderQueuePanel

APP=QApplication.instance() or QApplication([])


def test_modes_pills_and_queue_estimates(tmp_path):
    clock=[1000.0];callbacks=[]
    with patch('ltx_prompt_director.render_queue.time.monotonic',side_effect=lambda:clock[0]):
        queue=RenderQueue(lambda *args,**kwargs:callbacks.append((args,kwargs)))
        queue.set_paused(True)
        ref=queue.enqueue(lambda:None,(),lambda value:None,lambda error:None,title='Reference group',model='MiniMax H3',kind='Video',mode='ref2va',group_id='batch')
        frames=queue.enqueue(lambda:None,(),lambda value:None,lambda error:None,title='Keyframes group',model='MiniMax H3',kind='Video',mode='fl2va',group_id='batch')
        third=queue.enqueue(lambda:None,(),lambda value:None,lambda error:None,title='Final group',model='MiniMax H3',kind='Video',mode='fl2va',group_id='batch')
        panel=RenderQueuePanel(queue);panel.resize(370,780);panel.show();APP.processEvents()
        try:
            tiles=[panel.list.item(i) for i in range(panel.list.count()) if panel.list.item(i).data(256).kind!='Group']
            assert ref.state==frames.state=='Waiting'
            assert 'Ref2VA' in tiles[0].toolTip() and 'FL2VA' in tiles[1].toolTip()
            queue.set_paused(False);APP.processEvents()
            assert ref.state=='Rendering' and frames.state=='Waiting'
            callbacks[0][1]['progress']({'kind':'progress','percent':37,'eta_seconds':48*60,'message':'Rendering video'})
            QTest.qWait(90)
            assert progress_text(ref.progress_event)=='37% · ~48m 00s left'
            assert frames.expected_seconds==third.expected_seconds==48*60
            assert progress_text(frames.progress_event)==progress_text(third.progress_event)=='Pending'
            assert queue.estimated_video_seconds==48*3*60
            assert '144m 00s' in panel.summary.text()
            panel.list.setCurrentRow(1);panel.update_details();assert 'Ref2VA' in panel.details.text()
            panel.grab().save(str(tmp_path/'queue-modes.png'))
            # One completed render supplies full-duration estimates for waiting work.
            clock[0]+=40*60;callbacks[0][0][2](None);APP.processEvents()
            assert ref.elapsed_seconds==40*60 and frames.state=='Rendering'
            assert third.expected_seconds==40*60
            # Two completed renders: the third waiting sequence uses their average.
            queue.set_paused(True)
            clock[0]+=60*60;callbacks[1][0][2](None);APP.processEvents()
            assert frames.elapsed_seconds==60*60 and third.state=='Waiting'
            assert third.expected_seconds==50*60 and queue.estimated_video_seconds==50*60
            assert progress_text(third.progress_event)=='Pending'
            queue.set_paused(False);APP.processEvents()
            callbacks[2][1]['progress']({'kind':'progress','percent':80,'eta_seconds':8*60})
            QTest.qWait(90)
            assert len(queue.remaining_video_jobs)==1 and queue.estimated_video_seconds==8*60
            queue.cancel_job(third.id)
            assert not queue.remaining_video_jobs and queue.estimated_video_seconds==0
        finally:queue.shutdown();panel.close()
