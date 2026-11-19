"""One native, mocked-provider context completion/save/reopen/stale workflow."""
import base64,copy,io,json,threading,time
from pathlib import Path
from unittest.mock import patch
import av,numpy as np
from PIL import Image
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from ltx_prompt_director import ui,ai
from ltx_prompt_director.models import Segment
from ltx_prompt_director.project_archive import read_project

APP=QApplication.instance() or QApplication([])

def wait_for(condition):
    until=time.monotonic()+12
    while not condition() and time.monotonic()<until:APP.processEvents();time.sleep(.01)
    assert condition()


def test_whole_timeline_context_button_preserves_sources_and_discards_changed_project(tmp_path,monkeypatch):
    picture=io.BytesIO();Image.new('RGB',(32,32),'purple').save(picture,format='PNG')
    encoded='data:image/png;base64,'+base64.b64encode(picture.getvalue()).decode()
    preview=tmp_path/'source.png';preview.write_bytes(picture.getvalue())
    movie=tmp_path/'source.mp4'
    with av.open(str(movie),'w') as output:
        stream=output.add_stream('libx264',rate=24);stream.width=32;stream.height=32;stream.pix_fmt='yuv420p'
        for i in range(48):
            for packet in stream.encode(av.VideoFrame.from_ndarray(np.full((32,32,3),(i,50,90),np.uint8),format='rgb24')):output.mux(packet)
        for packet in stream.encode():output.mux(packet)
    library=tmp_path/'projects';library.mkdir()
    monkeypatch.setattr(ui,'project_library_path',lambda:library)
    settings=QSettings(str(tmp_path/'settings.ini'),QSettings.Format.IniFormat)
    with patch('ltx_prompt_director.ui.QSettings',return_value=settings),patch.object(ui.MainWindow,'restore_startup_workspace'):
        w=ui.MainWindow();w._close_saves_queued=True
    failures=[]
    monkeypatch.setattr(ui.QMessageBox,'critical',lambda owner,title,message:failures.append(str(message)))
    gate=threading.Event();started=threading.Event();stale=False;calls=[]
    try:
        w.new_project();w.set_project_type('minimax_references');w.requested_length.setValue(4)
        reference=dict(kind='image',image=encoded,role='identity',name='Authored hero',notes='Keep the purple coat.',description='Hero wearing a purple coat.')
        w.minimax_reference_images=[copy.deepcopy(reference) for _ in range(4)]
        full=w.capture_render_group();full['minimaxH3']['prompt']='Original source-group Unified wording.'
        empty=copy.deepcopy(full);empty['minimaxH3']['referenceImages']=[None]*4
        empty['minimaxH3']['prompt']='Independent target Unified wording.'
        w.set_project_type('minimax_frames');standard=w.capture_render_group();w.set_project_type('minimax_references')
        source=Segment('Source','','','text','text','Original source action.',4,render_group=0)
        first=Segment('Missing identity','','','text','text','The hero walks.',2,render_group=1)
        second=Segment('Missing setting','','','text','text','The hero reaches the doorway.',2,render_group=1)
        video=Segment('Later clip',str(movie),str(preview),'video','start','Later authored scene.',2,render_group=2)
        w.segments=[source,first,second,video];w.video_extensions=2
        w.render_group_options={
            '0':dict(requestedLength=4,context=full),
            '1':dict(requestedLength=4,context=empty),
            '2':dict(requestedLength=2,context=standard,clip=dict(path=str(movie),duration=2,trim_start=0))}
        w._active_render_group=1;w.apply_render_group(empty);w.refresh_timeline(1)
        w.prompt_surface.structured.refresh();w.store_render_group();w.grouped_sequence.refresh()
        before=[s.to_dict() for s in w.segments];old_source=copy.deepcopy(w.render_group_options['0']['context'])
        monkeypatch.setattr(w,'ai_credentials',lambda **kw:('openai','fixture','fixture'))
        def transport(images,provider,model,key,rules,timeout,*,schema):
            calls.append((copy.deepcopy(images),rules,schema['title']))
            if schema['title']=='timeline_context_selection':
                if stale:started.set();assert gate.wait(8)
                result={g:[] for g in schema['properties']['groups']['properties']}
                result['1']=[dict(source='group:0:reference:0',name='Hero identity from Group 1',role='identity',
                    description='Purple coat identity.',notes='Keep identity through the earlier action.',segment_ids=[first.id]),
                    dict(source='group:2:attached:opening',name='Doorway setting from Group 3 opening',role='scene',
                    description='Doorway visual context.',notes='Later setting, after the intervening walk.',segment_ids=[second.id])]
                return json.dumps(dict(groups=result))
            assert schema['title']=='timeline_context_refinement'
            document=json.loads(rules.split('\nORDERED TIMELINE:')[0].split('\n',1)[1])
            result={g:copy.deepcopy(doc['fields']) for g,doc in document.items()}
            result['1']['global']='Identity and doorway continuity using Image1 and Image2.'
            return json.dumps(dict(groups=result))
        monkeypatch.setattr(ai,'_structured_raw',transport)
        assert not w.timeline_context_tile.isHidden()
        w.timeline_context_tile.click();wait_for(lambda:not w.ai_busy and len(calls)==2)
        wait_for(lambda:not w._pending_disk_jobs)
        assert not failures,failures
        refs=w.minimax_reference_images
        assert refs[0]['image']==encoded and refs[0]['name']=='Hero identity from Group 1'
        assert refs[1]['kind']=='image' and refs[1]['image'].startswith('data:image/')
        assert second.id in refs[1]['notes'] and 'Group 3' in refs[1]['notes']
        assert [s.to_dict() for s in w.segments]==before
        assert w.render_group_options['0']['context']==old_source
        assert 'Image1 and Image2' in w.minimax_prompt_text
        assert len([ref for ref in refs if ref])==2
        ids={image['asset_id'] for image in calls[0][0]}
        assert {'group:0:reference:0','group:2:attached:opening','group:2:attached:ending',
                f'segment:{video.id}:opening',f'segment:{video.id}:ending'}<=ids
        assert 'Hero wearing a purple coat.' in calls[0][1]
        assert 'The hero reaches the doorway.' in calls[0][1]
        assert 'Image1' in calls[1][1] and 'Image2' in calls[1][1]
        saved=read_project(w.current_library_metadata()['projectPath'])
        w.load_project_payload(saved);w.select_render_group(1)
        assert len([ref for ref in w.minimax_reference_images if ref])==2
        assert w.segments[1].prompt==first.prompt
        # Real Remove remains usable; a late review must not replace newer text.
        w.set_minimax_reference_image(1,None);assert w.minimax_reference_images[1] is None
        stale=True;w.timeline_context_tile.click();wait_for(started.is_set)
        w.segments[2].prompt='Newer user wording while the request is in flight.'
        gate.set();wait_for(lambda:not w.ai_busy)
        assert w.minimax_reference_images[1] is None
        assert w.segments[2].prompt=='Newer user wording while the request is in flight.'
        w.set_project_type('minimax_frames');assert w.timeline_context_tile.isHidden()
    finally:
        gate.set();w.queue_store.frozen=True;w.queue_store.timer.stop();w.close()


def test_all_group_batch_reuses_long_sources_and_queues_short_bridges(tmp_path):
    from types import SimpleNamespace
    from PySide6.QtWidgets import QComboBox
    from ltx_prompt_director.local_generation import align_h3_frames
    source=tmp_path/'existing.webm';source.write_bytes(b'Already managed source; no media processing in dispatch.')
    segments={g:[Segment(str(g),str(source) if g%2==0 else '', '',
                'video' if g%2==0 else 'text','start' if g%2==0 else 'text','Keep authored prompt.',length,render_group=g)]
              for g,length in enumerate((14.04,4,15.4583,3,16.5))}
    options={str(g):{} for g in segments}
    combo=QComboBox();combo.addItem('All groups',None)
    for g in segments:combo.addItem(f'Group {g+1}',g)
    queue=SimpleNamespace(jobs=[],paused=False,changed=SimpleNamespace(emit=lambda:None))
    queue.set_paused=lambda value:setattr(queue,'paused',value)
    selected=[]
    def generate():
        g=combo.currentData();selected.append(g)
        queue.jobs.append(SimpleNamespace(id=str(g),group_id='',group_title=''))
    owner=SimpleNamespace(store_render_group=lambda:None,render_groups=lambda:segments,
        render_group_options=options,render_queue=queue,render_scope=combo,
        generate_local_video=generate,mark_dirty=lambda:None,current_project_name='Fixture')
    ui.MainWindow.generate_group_video_batch(owner)
    assert selected==[1,3] and combo.currentData() is None and not queue.paused
    assert [options[str(g)]['clip']['duration'] for g in (0,2,4)]==[14.04,15.4583,16.5]
    assert all(options[str(g)]['clip']['path']==str(source) for g in (0,2,4))
    assert all(job.group_id==queue.jobs[0].group_id for job in queue.jobs)
    assert align_h3_frames(4.46)==107  # centisecond editor round-trip stays on the same grid
