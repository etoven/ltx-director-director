"""Capture every native Settings section, narrow layout and 120% text scale."""
import os
from pathlib import Path
import sys
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from ltx_prompt_director.ui import MainWindow,SettingsDialog


def demo(dialog):
    dialog.segment_save_dir.setText('~/Downloads'); dialog.comfy_root_dir.setText('~/Programs/ComfyUI')
    panel=dialog.local_models
    files=dict(image_model='flux-2-klein-base-4b-fp8.safetensors',image_encoder='qwen_3_4b.safetensors',image_vae='flux2-vae.safetensors',
        video_model='minimax_h3_ref2va_pruned_int8_convrot.safetensors',video_encoder='qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors',
        video_vae='minimax_h3_video_vae_fp16.safetensors',audio_vae='minimax_h3_audio_vae_fp32.safetensors')
    for key,name in files.items():panel.fields[key].setText('~/Models/'+name)
    panel.fields['models_root'].setText('~/Models')
    panel.fields['python'].setText('~/.local/share/PromptDirector/local-generation/engine/bin/python')
    panel.fields['image_assets'].setText('~/Director/models/klein-4b-assets')
    panel.fields['video_assets'].setText('~/Director/models/h3-assets/Ref2VA/processor')
    panel.loras['image'].add_row(dict(path='~/Models/loras/ink-illustration.safetensors',trigger='ink illustration, fine linework',weight=.65))
    panel.loras['image'].add_row(dict(path='~/Models/loras/paper-texture.safetensors',trigger='textured paper',weight=.3))


def capture():
    app=QApplication([])
    with patch.object(MainWindow,'restore_startup_workspace'):window=MainWindow()
    window._close_saves_queued=True
    private=ROOT/'.private/ui/captures';private.mkdir(parents=True,exist_ok=True)
    dialog=SettingsDialog(window.settings,window);dialog.resize(1280,956);demo(dialog);dialog.show()
    stages=[('local-loras',1,2),('settings-local-engine',1,0),('local-video-settings',1,1),('application-settings',0,None),
            ('settings-chatgpt',2,None),('settings-workspaces',3,None),('storage-settings',4,None),('settings-narrow',1,2)]
    def advance():
        nonlocal dialog
        if not stages:
            dialog.reject();window.settings.setValue('ui_text_scale',120)
            dialog=SettingsDialog(window.settings,window);dialog.resize(1536,1148);demo(dialog);dialog.surface.select(1,2);dialog.show()
            def final():
                dialog.grab().save(str(private/'settings-scaled.png'))
                dialog.reject();window.close();app.quit();print('Captured all Settings pages, narrow layout and 120% text scale.',flush=True)
            QTimer.singleShot(500,final);return
        name,page,section=stages.pop(0)
        if name=='settings-narrow':dialog.resize(1040,760)
        dialog.surface.select(page,section)
        def save():
            if page==4:
                location=dialog.storage_panel.location
                location.setText(location.text().replace(str(Path.home()),'~'))
            folder=private if name in ('settings-chatgpt','settings-workspaces','settings-narrow') else ROOT/'docs/images'
            dialog.grab().save(str(folder/f'{name}.png'));advance()
        QTimer.singleShot(350,save)
    QTimer.singleShot(500,advance);app.exec();sys.stdout.flush();os._exit(0)


if __name__=='__main__':capture()
