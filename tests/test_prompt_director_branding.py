from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from ltx_prompt_director.__main__ import migrate_settings
from ltx_prompt_director.desktop import desktop_contents


def test_product_launcher_and_settings_migration(tmp_path,monkeypatch):
    stores={}
    def settings(organization=None,name=None):
        key=(organization or 'new',name or 'new')
        if key not in stores:stores[key]=QSettings(str(tmp_path/('settings-'+str(len(stores))+'.ini')),QSettings.Format.IniFormat)
        return stores[key]
    monkeypatch.setattr('PySide6.QtCore.QSettings',settings)
    stores_old=settings('LTXDirectorDirector','LTX Director - Director')
    stores_old.setValue('provider','openai');stores_old.setValue('workspace','shot_sequence')
    legacy=settings('LTXPromptDirector','LTX Prompt Director');legacy.setValue('provider','gemini');legacy.setValue('font_scale',120)
    migrate_settings()
    assert settings().value('provider')=='openai'
    assert settings().value('workspace')=='shot_sequence'
    assert settings().value('font_scale',type=int)==120
    settings().setValue('provider','custom');migrate_settings()
    assert settings().value('provider')=='custom'
    launcher=desktop_contents()
    assert 'Name=Prompt Director\n' in launcher
    assert 'StartupWMClass=Prompt Director\n' in launcher
