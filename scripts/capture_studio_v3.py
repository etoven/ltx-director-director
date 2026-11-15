"""Capture the experimental app with authored demo prompts and local test audio."""
import os
from pathlib import Path
import sys
import tempfile
import wave
import copy
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
if not os.environ.get('STUDIO_INSTALLED_PROOF'):sys.path.insert(0,str(ROOT))
os.environ.setdefault('QT_QPA_PLATFORM','offscreen');os.environ.setdefault('QT_AUDIO_BACKEND','null')
session=tempfile.TemporaryDirectory(prefix='studio-v3-proof-')
for kind in ('CONFIG','DATA','CACHE'):os.environ['XDG_'+kind+'_HOME']=str(Path(session.name)/kind)
import numpy as np
from PySide6.QtWidgets import QApplication,QDockWidget
from PySide6.QtTest import QTest
from ltx_prompt_director import ui
from ltx_prompt_director.models import Segment
from ltx_prompt_director.audio_tracks import prepare_audio

app=QApplication([]);app.setOrganizationName('StudioProof');app.setApplicationName('Experimental')
with patch.object(ui.MainWindow,'restore_startup_workspace'),patch.object(ui.MainWindow,'keep_window_on_screen'):
    w=ui.MainWindow()
w._close_saves_queued=True
w.set_project_type('minimax_frames')
photo='/workspace/studio-proof-assets/restaurant.jpg'
if not Path(photo).is_file():photo=str(ROOT/'ltx_prompt_director/assets/splash-aurora-background.png')
w.segments=[Segment('Media',photo,photo,'image','start','Woman in a green silk dress, leaning in close at an elegant candlelit dinner table. Restaurant soft ambiance. Spoken Dialog: "Mmmm... So I am looking forward to spending the night later. But first I need to freshen up at the hotel before I leave. Is that okay?"',7),
            Segment('Text','','','text','text','"Sounds perfect... just don’t keep me waiting too long."',3)]
w.current_project_name='Sequence_01_DinnerScene';w.update_window_title();w.refresh_timeline()
paths=[]
for index,(kind,length,frequency) in enumerate((('Dialogue',7,220),('Ambience',10,380),('Music',10,130))):
    t=np.arange(int(length*8000))/8000
    envelope=(.12+.55*np.abs(np.sin(t*(2.7+index)))**3)*(.3+.7*np.abs(np.sin(t*8.6)))
    samples=(24000*envelope*np.sin(2*np.pi*frequency*t)).astype('<i2')
    path=Path(session.name)/(kind+'.wav')
    with wave.open(str(path),'wb') as sound:sound.setparams((1,2,8000,0,'NONE','not compressed'));sound.writeframes(samples.tobytes())
    paths.append(str(path))
clips,errors=prepare_audio(paths);assert not errors,errors
for lane,clip in enumerate(clips):clip.move(0,lane)
w.audio_panel.set_project([clip.to_dict() for clip in clips]);w.audio_panel.set_body_height(166)
w.set_conditioning_height(0)
for dock in w.findChildren(QDockWidget):dock.hide()
w.timeline_dock.show()
w.resize(1855,850);w.show();QTest.qWait(120)
w.autofit_timeline();QTest.qWait(80)
w.audio_panel.transport.seek(2500)
app.processEvents()
target=ROOT/'docs/images/studio-v3-experimental.png';target.parent.mkdir(exist_ok=True)
assert w.grab().save(str(target))
print('Experimental proof:',target,'version',ui.__version__,flush=True)
# Exercise the native group controllers and capture their bracket/blend UI.
w.segments=[Segment(f'Prompt {index+1}','','','text','text',f'[Shot {index+1}] Preserve authored motion and camera direction.',5,render_group=index//3) for index in range(9)]
w.video_extensions=2;w._active_render_group=0;w.requested_length.setValue(15)
context=w.capture_render_group()
w.render_group_options={str(group):dict(requestedLength=15,context=copy.deepcopy(context),blendLength=1,blend='perceptual_crossfade') for group in range(3)}
w.refresh_timeline(0);w.autofit_timeline();QTest.qWait(60)
original=[segment.to_dict() for segment in w.segments]
payload=w.project_payload(False);w.load_project_payload(copy.deepcopy(payload));QTest.qWait(80);app.processEvents()
assert [segment.to_dict() for segment in w.segments]==original
assert len(w.extension_brackets.spans())==3
assert [w.render_group_duration(group) for group in range(3)]==[15,15,15]
assert w.extension_transition(1)['overlap']==1
w.timeline_shell.grab().save(str(ROOT/'docs/images/studio-v3-groups.png'))
print('Three groups and one-second blends preserved through native project round-trip.',flush=True)
w.project_dirty=False;w.close();os._exit(0)
