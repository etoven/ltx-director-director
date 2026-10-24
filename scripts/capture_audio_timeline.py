"""Capture the actual conditioning-audio UI using locally synthesized demo sound."""
import os
from pathlib import Path
import sys
import tempfile
import subprocess
from unittest.mock import patch
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
session = tempfile.TemporaryDirectory(prefix='director-audio-proof-')
for kind in ('CONFIG', 'DATA', 'CACHE'):
    os.environ[f'XDG_{kind}_HOME'] = str(Path(session.name) / kind.lower())

import numpy as np
from PIL import Image
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QFont, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from ltx_prompt_director import ui
from ltx_prompt_director.models import Segment
from ltx_prompt_director.audio_tracks import prepare_audio
from ltx_prompt_director.workspaces import WorkspaceStore

app = QApplication([]); app.setOrganizationName('DirectorProof'); app.setApplicationName('ConditioningAudio')
app.setStyle('Fusion'); app.setFont(QFont('DejaVu Sans', 10))
store = WorkspaceStore(Path(session.name) / 'workspaces'); store.restore_stock()
with patch.object(ui, 'WorkspaceStore', return_value=store), patch.object(ui.MainWindow, 'restore_startup_workspace'), patch.object(ui.MainWindow, 'keep_window_on_screen'):
    window = ui.MainWindow()
window._close_saves_queued = True
for dock in window.findChildren(ui.QDockWidget): dock.hide()
window.current_project_name = 'Aurora crossing · Audio UI proof'; window.update_window_title()
window.set_project_type('minimax_official_skill_reference')
source = str(ROOT / 'ltx_prompt_director/assets/splash-aurora-background.png')
window.segments = [Segment('Opening', source, source, 'image', 'start', 'A traveler watches the aurora above the mountain ridge.', 3),
                   Segment('Crossing', '', '', 'text', 'text', 'Track beside the traveler crossing the quiet ridge. Snow drifts through the moonlight.', 6),
                   Segment('Horizon', source, source, 'image', 'end', 'The traveler reaches the overlook. Hold on the luminous horizon.', 3)]
window.refresh_timeline(1)
window.unified_prompt.setPlainText('Establish the aurora-lit ridge, follow the traveler through drifting snow, and settle on the luminous horizon. Layer a quiet wind bed, a brief radio cue and distant footsteps.')
paths = []
for index, (name, duration, frequency) in enumerate((('Ridge wind.wav', 4, 120), ('Radio cue.wav', 5, 330), ('Footsteps.wav', 3, 210), ('Wind return.wav', 3, 150))):
    time = np.arange(int(duration * 16000)) / 16000
    envelope = np.clip(.1 + .8 * np.sin(time * (1.2 + index) * np.pi) ** 4, 0, 1)
    samples = (np.sin(time * 2 * np.pi * frequency) * envelope * .7 * 32767).astype('<i2')
    path = Path(session.name) / name
    with wave.open(str(path), 'wb') as out:
        out.setparams((1, 2, 16000, 0, 'NONE', 'not compressed')); out.writeframes(samples.tobytes())
    paths.append(str(path))
# The production import uses a background worker. This standalone capture has
# no visible window until the local demonstration files have been prepared.
clips, errors = prepare_audio(paths); assert not errors, errors
for clip, start, lane in zip(clips, (0, 2.5, 7.5, 9), (0, 1, 1, 0)):
    clip.move(round(start * 24), lane)
panel = window.audio_panel
panel.set_project([c.to_dict() for c in clips]); panel.select(clips[1].id)
panel.set_body_height(202)
window.set_conditioning_height(96)
window.resize(1500, 1090); window.show(); QTest.qWait(120)
window.autofit_timeline(); app.processEvents()
panel.transport.seek(4250)
target = ROOT / 'docs/images'; target.mkdir(parents=True, exist_ok=True)
assert window.grab().save(str(target / 'audio-timeline-proof.png'))
start = window.timeline_shell.mapTo(window, QPoint(0, 0))
bottom = panel.mapTo(window, QPoint(panel.width(), panel.height()))
crop = QRect(start, bottom)
frames = []
canvas = panel.canvas
for frame in range(0, 265, 8):
    x = round(panel.left + frame / 24 * panel.scale)
    if frame == 0:
        QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=QPoint(x, 13))
    QTest.mouseMove(canvas, QPoint(x, 13)); app.processEvents()
    shot = window.grab(crop).toImage().convertToFormat(QImage.Format.Format_RGBA8888)
    frames.append(Image.frombytes('RGBA', (shot.width(), shot.height()), bytes(shot.bits())).convert('RGB'))
QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=QPoint(x, 13))
panel.transport.pause()
frames[0].save(target / 'audio-timeline-scrub.gif', save_all=True, append_images=frames[1:], duration=80, loop=0)
# Capture every workspace using the same audio so visibility can be inspected.
for mode, filename in (('ltx', 'audio-timeline-ltx-proof.png'), ('minimax_prompt_refinement', 'audio-timeline-prompt-proof.png')):
    window.set_project_type(mode); app.processEvents(); window.autofit_timeline(); app.processEvents()
    assert panel.isVisible(); assert window.grab().save(str(target / filename))
window.set_project_type('shot_sequence')
window.shot_sequence_panel._build_timer.stop()
import imageio_ffmpeg
movie = Path(session.name) / 'Ridge-crossing.webm'
subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-y', '-loop', '1', '-i', source,
                '-t', '12', '-vf', 'scale=640:360', '-r', '24', '-c:v', 'libvpx-vp9',
                '-deadline', 'realtime', '-cpu-used', '8', '-an', str(movie)],
               check=True, capture_output=True)
window.segments = [Segment('Ridge crossing.webm', str(movie), source, 'video', 'start', '', 12,
                           media_duration_frames=288, trim_start=0)]
window.refresh_timeline(); window.shot_sequence_panel._build_timer.stop()
app.processEvents(); assert panel.isVisible()
assert window.grab().save(str(target / 'audio-timeline-shot-proof.png'))
print('Native audio UI proof captured in docs/images/audio-timeline-*.png and audio-timeline-scrub.gif', flush=True)
window.project_dirty = False; window.close()
os._exit(0)
