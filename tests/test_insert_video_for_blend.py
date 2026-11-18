"""One native bridge insertion/refine/sync workflow; no provider/model render."""
import copy,hashlib,time
from pathlib import Path
from unittest.mock import patch
import av,numpy as np
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QSettings
from ltx_prompt_director import ui,local_generation
from ltx_prompt_director.models import Segment
from ltx_prompt_director.group_prompt_context import snapshot,augment,GROUP_PROMPT_CONTEXT
from ltx_prompt_director import ai

APP=QApplication.instance() or QApplication([])

def wait_for(condition):
    until=time.monotonic()+15
    while not condition() and time.monotonic()<until:APP.processEvents();time.sleep(.01)
    assert condition()


def test_insert_blend_button_attaches_full_video_refines_only_three_second_bridge_and_syncs(tmp_path,monkeypatch):
    source=tmp_path/'full-video.mp4'
    with av.open(str(source),'w') as media:
        stream=media.add_stream('libx264',rate=24);stream.width=32;stream.height=32;stream.pix_fmt='yuv420p'
        for i in range(24*20):
            frame=av.VideoFrame.from_ndarray(np.full((32,32,3),(20,150,60),np.uint8),format='rgb24')
            for packet in stream.encode(frame):media.mux(packet)
        for packet in stream.encode():media.mux(packet)
    digest=hashlib.sha256(source.read_bytes()).hexdigest()
    library=tmp_path/'projects';library.mkdir()
    monkeypatch.setattr(ui,'project_library_path',lambda:library)
    settings=QSettings(str(tmp_path/'settings.ini'),QSettings.Format.IniFormat)
    with patch('ltx_prompt_director.ui.QSettings',return_value=settings),patch.object(ui.MainWindow,'restore_startup_workspace'):
        w=ui.MainWindow();w._close_saves_queued=True
    try:
        w.new_project();w.set_project_type('minimax_frames')
        w.segments=[Segment('Existing','','','text','text','Keep this original wording.',4)]
        w.requested_length.setValue(4);w.minimax_prompt_text='Keep this original wording.'
        w.render_group_options={'0':dict(requestedLength=4,context=w.capture_render_group(),clip=dict(path=str(source),duration=4,trim_start=0))}
        w.refresh_timeline();original=w.segments[0].to_dict()
        assert w.add_tile_layout.indexOf(w.insert_blend_video_tile)==w.add_tile_layout.indexOf(w.add_extension_tile)+1
        monkeypatch.setattr(ui,'choose_document_open',lambda *a:'')
        w.insert_blend_video_tile.click();assert len(w.segments)==1
        calls=[]
        monkeypatch.setattr(w,'ai_credentials',lambda **kw:('openai','fixture','fixture'))
        monkeypatch.setattr(w,'start_ai_worker',lambda *a,**kw:calls.append((a,kw)))
        monkeypatch.setattr(ui,'choose_document_open',lambda *a:str(source))
        w.insert_blend_video_tile.click();wait_for(lambda:len(calls)==1)
        assert len(w.render_groups())==3
        bridge,movie=w.segments[1:]
        assert bridge.kind=='text' and bridge.duration==3 and movie.kind=='video' and movie.duration==20
        assert w.segments[0].to_dict()==original
        assert w.workspace_engine=='minimax_references'
        assert all(w.render_group_options[str(g)]['context']['workspaceDefinition']['engine']=='minimax_references' for g in (1,2))
        assert w.render_group_options['0']['context']['workspaceDefinition']['engine']=='minimax_frames'
        assert all(w.extension_transition(g)['blend']=='perceptual_crossfade' for g in (1,2))
        clip=w.render_group_options['2']['clip']
        assert Path(clip['path']).is_file() and clip['duration']==20 and clip['path']==movie.media_path
        assert clip['path']!=str(source)
        assert w.render_scope.currentData()==1 and w.current_segment().id==bridge.id
        token=GROUP_PROMPT_CONTEXT.set(snapshot(w,[1],[bridge.id]))
        try:images,rules=augment([],'Bridge rules','openai',ai._mixed_reference_input)
        finally:GROUP_PROMPT_CONTEXT.reset(token)
        assert any(i.get('group_start_anchor') for i in images) and any(i.get('group_end_anchor') for i in images)
        args,options=calls[0]
        assert args[0] is ui.refine_segment_prompt and args[1][1]==1 and args[1][-2]==3
        assert options['refinement_scope']=='segment'
        w.refinement_segment_id=bridge.id;w.ai_refinement_scope='segment'
        args[3](dict(prompt='A refined visual bridge.',duration=14,imagePrompt=''))
        assert bridge.prompt=='A refined visual bridge.' and bridge.duration==3 and movie.duration==20
        assert w.segments[0].to_dict()==original
        w.grouped_sequence.refresh()
        assert [s.duration for s in w.grouped_sequence.shots]==[4,3,20]
        assert w.grouped_sequence.shots[-1].media_path==clip['path']
        w.set_project_type('shot_sequence');assert [s.duration for s in w.segments]==[4,3,20]
        w.set_project_type('minimax_references');assert [s.duration for s in w.segments]==[4,3,20]
        w.set_project_type('minimax_frames');assert [s.duration for s in w.segments]==[4,3,20]
        w.save_library_project(automatic=True);wait_for(lambda:not w._pending_disk_jobs)
        from ltx_prompt_director.project_archive import read_project
        saved=read_project(w.current_library_metadata()['projectPath'])
        assert saved['renderGroupOptions']['2']['clip']['duration']==20
        assert saved['renderGroupOptions']['2']['attachedVideoOnly']
        assert hashlib.sha256(source.read_bytes()).hexdigest()==digest
        assert local_generation.align_h3_frames(3)==73
        assert local_generation.align_h3_frames(1)>0
        from ltx_prompt_director.local_widgets import LocalModelsPanel
        panel=LocalModelsPanel(w);assert panel.video_duration.minimum()<3;panel.dispose()
    finally:
        w.queue_store.frozen=True;w.queue_store.timer.stop();w.close()
