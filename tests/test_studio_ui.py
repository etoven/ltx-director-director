"""Native v3 studio presentation keeps the experimental controllers and groups."""
import copy
from unittest.mock import patch
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from ltx_prompt_director.models import Segment
from ltx_prompt_director.studio_ui import snap, dialogue
from test_video_extension_ui import window, APP


def test_studio_transport_snap_and_three_native_groups(tmp_path):
    with window(tmp_path) as w:
        w.set_project_type('minimax_frames')
        w.segments=[Segment('Opening','','','text','text','Keep this shot and wording.',15)]
        w.requested_length.setValue(15);w.refresh_timeline(0)
        w.add_extension_tile.click();w.add_extension_tile.click()
        w.resize(1500,950);w.show();APP.processEvents();w.autofit_timeline();APP.processEvents()
        assert [w.render_group_duration(group) for group in range(3)]==[15,15,15]
        assert len(w.extension_brackets.spans())==3
        assert w.extension_transition(1)['overlap']==1
        assert w.segments[0].prompt=='Keep this shot and wording.'
        w.studio_snap.setChecked(True)
        assert snap(w,14.95,100)==15
        w.studio_snap.click()
        assert snap(w,14.95,100)==14.95
        assert not w.studio.play.icon().isNull() and w.studio.play.text()==''
        image=w.studio.play.grab().toImage()
        assert image.pixelColor(0,0).name()=='#1c1d25'
        w.studio.play.click();assert w.audio_panel.transport.playing
        w.studio.play.click();assert not w.audio_panel.transport.playing
        before=[segment.to_dict() for segment in w.segments]
        payload=w.project_payload(False);w.load_project_payload(copy.deepcopy(payload));APP.processEvents()
        assert [segment.to_dict() for segment in w.segments]==before
        assert len(w.extension_brackets.spans())==3
        assert dialogue('Spoken Dialog: "Keep these words."')=='Keep these words.'
