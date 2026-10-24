"""Free audio placement, source trims, portable media, native gestures and audition."""
import copy
import json
from pathlib import Path
import wave
from unittest.mock import patch

import numpy as np
import pytest
from PySide6.QtCore import QObject, Signal, Qt, QPoint, QMimeData, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from ltx_prompt_director.audio_tracks import AudioClip, prepare_audio, audio_export_records
from ltx_prompt_director.audio_timeline import AudioTransport
from ltx_prompt_director.project_archive import save_project_archive, read_project

APP = QApplication.instance() or QApplication([])


def make_audio(path, duration=4):
    samples = np.arange(round(duration * 8000)) / 8000
    # Silence in the middle makes the waveform's time mapping observable.
    sound = np.sin(2 * np.pi * 330 * samples) * .6
    sound[(samples > 1) & (samples < 2)] = 0
    with wave.open(str(path), 'wb') as out:
        out.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
        out.writeframes((sound * 32767).astype('<i2').tobytes())
    return path


def test_independent_gaps_overlap_and_source_boundaries():
    clip = AudioClip('Voice', 'voice.wav', 8, start_frame=72, duration_frames=96, lane=1)
    clip.trim_left(96)
    assert (clip.start_frame, clip.trim_start, clip.end_frame) == (96, 24, 168)
    clip.trim_left(0)
    assert (clip.start_frame, clip.trim_start, clip.end_frame) == (72, 0, 168)
    clip.trim_right(10000)
    assert clip.duration_frames == 192
    clip.move(480, 3)
    assert (clip.start_frame, clip.end_frame, clip.lane) == (480, 672, 3)
    clip.trim_left(9999)
    assert clip.duration_frames == 1
    clip.trim_right(-1)
    assert clip.duration_frames == 1
    with pytest.raises(ValueError):
        AudioClip('bad', '', float('nan'))


def test_waveform_original_archive_and_ltx_export(tmp_path):
    original = make_audio(tmp_path / 'voice.wav')
    source_bytes = original.read_bytes()
    clips, errors = prepare_audio([str(original)])
    assert not errors and len(clips) == 1
    clip = clips[0]
    assert clip.source_duration == 4 and len(clip.peaks) == 1024
    assert max(clip.peaks[270:500]) == 0 and max(clip.peaks[:200]) > .5
    clip.move(120, 1); clip.trim_right(168)
    second = copy.deepcopy(clip); second.id = 'second'; second.move(144, 2)
    archive = tmp_path / 'project.LTXD'
    payload = {'frames': [], 'conditioningAudio': {'enabled': True, 'clips': [clip.to_dict(), second.to_dict()]}}
    save_project_archive(archive, payload, [])
    original.unlink(); Path(clip.media_path).unlink()
    loaded = read_project(archive)
    restored = [AudioClip.from_dict(r) for r in loaded['conditioningAudio']['clips']]
    assert Path(restored[0].media_path).read_bytes() == source_bytes
    assert [(c.start_frame, c.end_frame, c.lane) for c in restored] == [(120, 168, 1), (144, 192, 2)]
    media = tmp_path / 'input'; media.mkdir()
    records = audio_export_records(restored, media, set())
    assert [(r['start'], r['length'], r['lane']) for r in records] == [(120, 48, 1), (144, 48, 2)]
    assert len(list(media.iterdir())) == 2
    restored[0].muted = True
    assert len(audio_export_records(restored, media, set())) == 1
    # The native archive remains independently portable after a second save.
    save_project_archive(archive, {'frames': [], 'conditioningAudio': {'clips': [c.to_dict() for c in restored]}}, [])
    assert Path(read_project(archive)['conditioningAudio']['clips'][0]['media_path']).read_bytes() == source_bytes


class FakeOutput(QObject):
    def setVolume(self, value):
        pass


class FakePlayer(QObject):
    mediaStatusChanged = Signal(object)
    errorOccurred = Signal(object, str)

    def __init__(self, parent=None):
        super().__init__(parent); self.pos = 0; self.playing = False

    def setAudioOutput(self, output): pass
    def setSource(self, source): self.source_url = source
    def source(self): return self.source_url
    def position(self): return self.pos
    def setPosition(self, position): self.pos = position
    def play(self): self.playing = True
    def pause(self): self.playing = False
    def stop(self): self.playing = False


def test_scrub_mixes_overlaps_silences_gaps_and_preserves_play_intent():
    transport = AudioTransport(player_factory=FakePlayer, output_factory=FakeOutput)
    a = AudioClip('A', 'a.wav', 5, start_frame=24, trim_start=24, duration_frames=48)
    b = AudioClip('B', 'b.wav', 5, start_frame=48, duration_frames=48, lane=1)
    transport.duration = 8000; transport.set_clips([a, b])
    pa, pb = transport.players[a.id][0], transport.players[b.id][0]
    transport.seek(2500, audible=True)
    assert pa.playing and pb.playing and (pa.pos, pb.pos) == (2500, 500)
    transport.audition.stop(); transport.finish_scrub()
    assert not pa.playing and not pb.playing and transport.position == 2500
    transport.play(); transport.seek(3500, audible=True)
    assert not pa.playing and pb.playing and transport.playing
    transport.seek(5000, audible=True)
    assert not pa.playing and not pb.playing
    transport.seek(2500); b.muted = True; transport.sync(True)
    assert pa.playing and not pb.playing
    transport.enabled = False; transport.sync(True)
    assert not pa.playing
    transport.pause()
    a.media_path = 'replacement.wav'
    transport.set_clips([a])
    assert transport.players[a.id][0] is pa and pa.source().toLocalFile().endswith('replacement.wav')
    transport.set_clips([])
    assert not transport.players


def test_ltx_import_keeps_offsets_trims_and_missing_file_associations(tmp_path):
    from ltx_prompt_director.audio_timeline import prepare_ltx_audio
    source = make_audio(tmp_path / 'sound.wav')
    records = [{'id': 'missing', 'start': 0, 'length': 30},
               {'id': 'voice', 'start': 90, 'length': 45, 'trimStart': 15, 'lane': 2}]
    clips, errors = prepare_ltx_audio([str(tmp_path / 'missing.wav'), str(source)], records, 30)
    assert len(errors) == 1 and len(clips) == 1
    assert (clips[0].id, clips[0].start_frame, clips[0].duration_frames, clips[0].trim_start, clips[0].lane) == ('voice', 72, 36, 12, 2)


def make_window(tmp_path):
    from ltx_prompt_director import ui
    from ltx_prompt_director.workspaces import WorkspaceStore
    store = WorkspaceStore(tmp_path / 'workspaces'); store.restore_stock()
    with patch.object(ui, 'WorkspaceStore', return_value=store), patch.object(ui.MainWindow, 'restore_startup_workspace'):
        window = ui.MainWindow()
    window._close_saves_queued = True
    for dock in window.findChildren(ui.QDockWidget): dock.hide()
    window.resize(1500, 1050); window.show(); APP.processEvents()
    return window


def test_native_gestures_fields_drop_collapse_and_all_workspaces(tmp_path):
    from ltx_prompt_director.models import Segment
    window = make_window(tmp_path)
    try:
        window.segments = [Segment('Beat', '', '', 'text', 'text', 'A timed action', 12)]
        window.refresh_timeline()
        panel = window.audio_panel
        panel.transport.player_factory = FakePlayer; panel.transport.output_factory = FakeOutput
        panel.set_project([AudioClip('Voice', 'voice.wav', 8, start_frame=24, duration_frames=72).to_dict(),
                           AudioClip('Music', 'music.wav', 8, start_frame=168, duration_frames=48, lane=1).to_dict()])
        panel.set_timeline(12, 70); panel.select(panel.clips[0].id)
        canvas = panel.canvas; clip = panel.clips[0]
        point = canvas.clip_rect(clip).center().toPoint()
        QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=point)
        QTest.mouseMove(canvas, point + QPoint(70, 52))
        QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=point + QPoint(70, 52))
        assert (clip.start_frame, clip.end_frame, clip.lane) == (48, 120, 1)
        point = canvas.clip_rect(clip).topRight().toPoint() + QPoint(-2, 25)
        QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=point)
        QTest.mouseMove(canvas, point + QPoint(-35, 0))
        QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=point + QPoint(-35, 0))
        assert clip.end_frame == 108
        panel.start.setValue(3); panel.fields_edited()
        assert (clip.start_frame, clip.end_frame) == (72, 132)
        panel.end.setValue(5); panel.fields_edited(); assert clip.end_frame == 120
        panel.source_in.setValue(1); panel.fields_edited(); assert clip.trim_start == 24
        QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=QPoint(248, 12))
        QTest.mouseMove(canvas, QPoint(318, 12)); QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=QPoint(318, 12))
        assert panel.time.text() == '00:04.000' and window.ruler.playhead == 4
        panel.transport.audition.stop(); panel.transport.finish_scrub()
        mime = QMimeData(); mime.setUrls([QUrl.fromLocalFile('/tmp/voice.wav'), QUrl.fromLocalFile('/tmp/music.mp3')])
        enter = QDragEnterEvent(QPoint(388, 45), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        canvas.dragEnterEvent(enter); assert enter.isAccepted()
        with patch.object(panel, 'add_paths') as dropped:
            drop = QDropEvent(QPoint(388, 45), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
            canvas.dropEvent(drop)
            assert dropped.call_args.args == (['/tmp/voice.wav', '/tmp/music.mp3'], 120, 0)
        panel.set_body_height(230); QTest.qWait(20)
        grip = panel.handle.rect().center()
        QTest.mousePress(panel.handle, Qt.MouseButton.LeftButton, pos=grip)
        QTest.mouseMove(panel.handle, grip + QPoint(0, -300))
        QTest.mouseRelease(panel.handle, Qt.MouseButton.LeftButton, pos=grip + QPoint(0, -300))
        assert panel.body.isHidden() and panel.body.height() == 0
        QTest.mouseDClick(panel.handle, Qt.MouseButton.LeftButton); assert panel.body.height() == 230
        QTest.mouseDClick(panel.handle, Qt.MouseButton.LeftButton); assert panel.body.isHidden() and panel.body.height() == 0
        QTest.mouseDClick(panel.handle, Qt.MouseButton.LeftButton); assert panel.body.height() == 230
        for mode in window.workspace_definitions:
            window.set_project_type(mode); APP.processEvents()
            assert panel.isVisible(), mode
            assert [(c.start_frame, c.end_frame) for c in panel.clips] == [(72, 120), (168, 216)]
            assert panel.body.height() == 230
        window.set_project_type('ltx'); window.refresh_timeline()
        state = window.capture_workspace_state(); panel.set_project([])
        window.restore_workspace_state(state)
        assert len(panel.clips) == 2 and panel.clips[0].trim_start == 24
        jobs = []
        with patch.object(window, 'start_disk_job', side_effect=lambda *args, **kwargs: jobs.append(args)):
            panel.add_paths(['/tmp/old.wav'])
        panel.set_project([])
        jobs[0][2](([AudioClip('stale', 'stale.wav', 4)], []))
        assert not panel.clips
    finally:
        window.project_dirty = False; window.close()


def test_native_project_and_ltx_snapshot_preserve_audio(tmp_path):
    from ltx_prompt_director import ui
    from ltx_prompt_director.models import Segment
    window = make_window(tmp_path)
    try:
        window.audio_panel.transport.player_factory = FakePlayer
        window.audio_panel.transport.output_factory = FakeOutput
        audio = make_audio(tmp_path / 'source.wav')
        clip = AudioClip(audio.name, str(audio), 4, start_frame=24, trim_start=12, duration_frames=48)
        window.audio_panel.set_project([clip.to_dict()])
        payload = window.project_payload()
        window.audio_panel.set_project([]); audio.unlink()
        window.load_project_payload(payload)
        restored = window.audio_panel.clips[0]
        assert restored.trim_start == 12 and restored.start_frame == 24
        assert Path(restored.media_path).is_file()
        assert window.has_project_content
        media = tmp_path / 'input'; output = tmp_path / 'timeline.json'
        ui._export_ltx_snapshot([Segment('Beat', '', '', 'text', 'text', '', 8)], '', 1280, 704, media, str(output), [restored])
        exported = json.loads(output.read_text())
        assert exported['timeline']['audioTrackEnabled'] and exported['settings']['use_custom_audio']
        assert exported['timeline']['audioSegments'][0]['trimStart'] == 12
        assert exported['timeline']['audioSegments'][0]['start'] == 24
        window.load_project_payload({'app': 'ltx-director-director', 'projectType': 'ltx',
                                     'frames': [Segment('Beat', '', '', 'text', 'text').to_dict()]})
        assert not window.audio_panel.clips
    finally:
        window.project_dirty = False; window.close()
