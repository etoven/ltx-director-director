"""Dialogue controls reach the IndexTTS adapter without loading a model."""
import sys
from types import ModuleType, SimpleNamespace
import wave
import pytest
from ltx_prompt_director.dialogue_controls import inference_options


def test_native_dialogue_controls_preserve_default_and_capture_changes(tmp_path):
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    from ltx_prompt_director.audio_widgets import AudioGenerationPanel
    app=QApplication.instance() or QApplication([])
    owner=SimpleNamespace(settings=QSettings(str(tmp_path/'settings.ini'),QSettings.Format.IniFormat))
    panel=AudioGenerationPanel(owner,'dialog')
    assert inference_options(panel.request())=={'duration_factor':1.0}
    assert not panel.emotion_strength.isEnabled()
    before=panel.serial
    panel.emotion.setCurrentIndex(panel.emotion.findData('sad'))
    panel.emotion_strength.setValue(.6);panel.speed.setValue(1.25)
    assert panel.serial>before and panel.emotion_strength.isEnabled()
    options=inference_options(panel.request())
    assert options['duration_factor']==.8
    assert options['emo_vector']==[0,0,.8,0,0,0,0,0]
    assert options['emo_alpha']==.6
    panel.emotion.setCurrentIndex(0)
    assert not panel.emotion_strength.isEnabled()
    assert inference_options(panel.request())=={'duration_factor':.8}
    panel.dispose();panel.close()


def test_dialogue_adapter_passes_emotion_and_duration_to_indextts(tmp_path,monkeypatch):
    from ltx_prompt_director import local_audio
    captured={}
    class IndexTTS:
        def __init__(self,**kwargs):pass
        def infer(self,**kwargs):
            captured.update(kwargs)
            with wave.open(kwargs['output_path'],'wb') as wav:
                wav.setparams((1,2,16000,0,'NONE','not compressed'));wav.writeframes(b'\0\0'*160)
    parent=ModuleType('indextts');module=ModuleType('indextts.infer_v2_5');module.IndexTTS2=IndexTTS
    monkeypatch.setitem(sys.modules,'indextts',parent);monkeypatch.setitem(sys.modules,'indextts.infer_v2_5',module)
    monkeypatch.chdir(tmp_path);monkeypatch.setattr(sys,'path',list(sys.path))
    monkeypatch.setattr(local_audio,'progress',lambda *_:None);monkeypatch.setattr(local_audio,'emit',lambda *a,**k:None)
    request=dict(audio_kind='dialog',audio_config=dict(source=str(tmp_path),model=str(tmp_path)),
                 job_dir=str(tmp_path),profile=dict(device='cpu'),sample='voice.wav',prompt='Keep these words.',
                 language='EN',emotion='happy',emotion_strength=.7,speed=.5)
    local_audio._generate_audio(request)
    assert captured['text']=='Keep these words.' and captured['spk_audio_prompt']=='voice.wav'
    assert captured['duration_factor']==2
    assert captured['emo_vector']==[.8,0,0,0,0,0,0,0] and captured['emo_alpha']==.7
    assert (tmp_path/'audio.wav').is_file()


@pytest.mark.parametrize('changes',[{'speed':0},{'speed':2.1},{'speed':float('nan')},
    {'emotion_strength':-1},{'emotion_strength':float('inf')},{'emotion':'unknown'}])
def test_invalid_dialogue_controls_are_rejected(changes):
    with pytest.raises(ValueError):inference_options(changes)
