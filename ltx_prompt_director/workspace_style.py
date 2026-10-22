"""Shared slate workspace palette and compact native control proportions."""
from pathlib import Path
from PySide6.QtGui import QFontDatabase

_font_loaded=False


def load_workspace_font():
    global _font_loaded
    if not _font_loaded:
        QFontDatabase.addApplicationFont(str(Path(__file__).parent/'assets/onboarding/RobotoCondensed.ttf'))
        _font_loaded=True


def workspace_style(theme, scale):
    load_workspace_font()
    def size(value):return max(1,round(value*scale))
    palette={
        '#24292c':'#272935','#1b2023':'#20222d','#303436':'#343743',
        '#101213':'#444957','#202527':'#252733','#252728':'#282a36',
        '#191d1f':'#252732','#0d0f10':'#111216','#1e2122':'#1e2029',
        '#171c1e':'#1e2029','#1a2023':'#252733','#354047':'#444956',
        '#374044':'#444956','#323638':'#414551','#1b2022':'#20222b',
        '#d9dcde':'#d5d8e0','#3b78a5':'#2d5c66','#5994bd':'#47828c',
    }
    for old,new in palette.items():theme=theme.replace(old,new)
    theme=theme.replace(f'font:{max(7,size(11))}px Arial',f'font:{size(14)}px "Roboto Condensed"')
    theme=theme.replace(f"font:{max(7,size(11))}px 'Courier New'",f"font:{size(13)}px 'DejaVu Sans Mono'")
    arrows=Path(__file__).parent/'assets'
    theme+=f'''
    #workspaceBrand QLabel{{background:transparent;color:#aeb9c9;font:{size(16)}px "Roboto Condensed"}}
    #mainToolbar{{background:#20222c;padding:{size(4)}px;spacing:{size(3)}px}}
    #mainToolbar QToolButton{{padding:{size(4)}px {size(6)}px}}
    QDockWidget::title{{background:#20222b;color:#c9ced9;padding:{size(7)}px {size(9)}px;font-size:{size(14)}px}}
    #directorPanel,#promptPanel{{background:#272935;border:1px solid #444956;border-radius:{size(7)}px}}
    #promptEditor{{background:#252733;padding:{size(9)}px}}
    #promptAction,#quickFixAction{{font-weight:normal;min-height:{size(20)}px;padding:{size(3)}px {size(7)}px;border-radius:{size(4)}px}}
    #panelTitle,#sectionLabel{{font-size:{size(13)}px;letter-spacing:0;font-weight:normal;background:transparent;color:#bac6d5}}
    #minimaxCacheState{{font-size:{size(11)}px}}
    QComboBox{{padding-right:{size(24)}px}}
    QComboBox::drop-down{{width:{size(20)}px}}
    QComboBox::down-arrow,QSpinBox::down-arrow,QDoubleSpinBox::down-arrow{{image:url("{(arrows/'spin-down.svg').as_posix()}");width:{size(7)}px;height:{size(5)}px}}
    QSpinBox::up-arrow,QDoubleSpinBox::up-arrow{{image:url("{(arrows/'spin-up.svg').as_posix()}");width:{size(7)}px;height:{size(5)}px}}
    QSpinBox,QDoubleSpinBox{{padding-right:{size(20)}px}}
    QSpinBox::up-button,QDoubleSpinBox::up-button,QSpinBox::down-button,QDoubleSpinBox::down-button{{width:{size(16)}px}}
    #referenceImageSlot{{background:#22242e;border:1px solid #404653;border-radius:{size(7)}px}}
    #referenceImageSlot #sectionLabel{{background:transparent;color:#b4bdcc;font-size:{size(14)}px;letter-spacing:0;font-weight:normal}}
    #referenceImagePreview{{background:#1c222b;border:1px solid #465160;border-radius:{size(5)}px}}
    #referenceImageSlot #copyButton{{font-size:{size(14)}px;color:#b1b9c8;padding:{size(2)}px {size(4)}px}}
    #referenceImageSlot QLineEdit{{background:#1e2029;border:1px solid #303642}}
    #effectsPanel{{background:#272935}}
    #effectsPanel QLineEdit{{background:#20222c;border:1px solid #444957;border-radius:{size(7)}px;padding:{size(7)}px}}
    #effectsPanel QComboBox{{border-radius:{size(5)}px;min-height:{size(24)}px}}
    #effectTiles{{background:#171820;border:1px solid #1e2029;border-radius:{size(7)}px;padding:{size(4)}px}}
    #effectTiles::item{{background:#30323e;border:1px solid #454957;border-radius:{size(7)}px;padding:{size(5)}px;color:#e1e3e9;font-size:{size(14)}px}}
    #effectTiles::item:hover{{background:#343d48;border-color:#64939d}}
    #effectTiles::item:selected{{background:#2d343d;border:2px solid #53a2a5}}
    #effectsPanel QToolButton{{border:1px solid #414a58;border-radius:{size(5)}px;padding:{size(3)}px {size(6)}px}}
    #effectsPanel QToolButton:checked{{background:#28515b;border-color:#58a5ac}}
    #effectsPanel QPushButton{{border-radius:{size(16)}px;border:1px solid #4a6574;padding:{size(5)}px {size(9)}px}}
    #effectsPanel #effectApply{{background:#2e5962;border-color:#3f6a73;min-height:{size(24)}px;color:#e7f2f2}}
    #effectsPanel #effectSecondary,#effectsPanel #effectDelete{{background:transparent;border-radius:{size(16)}px}}
    #effectsPanel #effectDelete{{color:#d78288;border-color:#815157}}
    #mainToolbar{{background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #18232d,stop:1 #131b24);border-bottom:1px solid #293b48}}
    #workspaceBrand QLabel{{font:{size(15)}px "Roboto Condensed";color:#d0e3f1}}
    #workspaceBrand{{background:transparent}}
    #projectLibrarySurface{{background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #101a23,stop:1 #121b24)}}
    #projectLibrarySurface QToolButton{{background:#17232f;border:1px solid #2a3b4a;border-radius:{size(4)}px;padding:{size(3)}px {size(5)}px;font-size:{size(13)}px}}
    #projectLibrarySurface QToolButton:hover{{background:#253b4b;border-color:#4e8b9b}}
    #projectLibrarySurface QToolButton:checked{{background:#264153;border-color:#4495aa}}
    #projectMore::menu-indicator{{image:none;width:0;height:0}}
    #projectLibrarySurface QComboBox{{background:#17232f;border:1px solid #304352;border-radius:{size(4)}px;padding:{size(4)}px;font-size:{size(13)}px}}
    #projectBrowser{{background:#101920;border:1px solid #2c3c49;border-radius:{size(5)}px}}
    #projectSearch{{background:#121e28;border:1px solid #304453;border-radius:{size(4)}px;padding:{size(5)}px;font-size:{size(14)}px}}
    #projectList{{background:transparent;border:0;padding:0}}
    #projectList::item,#projectList::item:hover,#projectList::item:selected{{background:transparent;border:0;margin:0;padding:0}}
    #unifiedPromptSurface{{background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #17222d,stop:1 #121b25);border:1px solid #2d3c4a;border-radius:{size(6)}px}}
    #promptSurfaceContent{{background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #071c25,stop:1 #081a22);border:1px solid #24404d;border-radius:{size(4)}px}}
    #surfacePromptEditor{{background:transparent;border:0;padding:{size(7)}px;color:#e5e9e9;font:{size(15)}px "Roboto Condensed"}}
    #surfacePromptEditor QWidget,#surfaceEditorStack{{background:transparent;border:0}}
    #promptSurfaceBody{{background:transparent;border:0}}
    #promptActionRail,#promptActionRail QWidget{{background:transparent;border:0}}
    #promptActionRail QPushButton,#promptActionRail QComboBox{{font:{size(13)}px "Roboto Condensed";padding:{size(1)}px {size(5)}px;min-height:0;min-width:0;border:1px solid #344d5c;border-radius:{size(5)}px;background:#20313f;color:#eff5f7}}
    #promptActionRail #surfaceGenerate{{background:#165b48;border-color:#26785a}}
    #promptActionRail QPushButton:hover{{background:#2c4a59;border-color:#65a7ad}}
    #surfacePromptCount{{font-size:{size(11)}px;color:#8ca8b8;background:transparent}}
    #surfaceCollapse{{background:transparent;border:0;padding:0;margin:0;min-width:0;min-height:0}}
    #surfaceScope{{background:#172630;border:1px solid #344d5c;border-radius:{size(4)}px;padding:{size(1)}px {size(6)}px;color:#d0e3f1;font:{size(13)}px "Roboto Condensed";min-width:0;min-height:0}}
    QMainWindow QTabBar::tab{{background:#121b24;color:#c0cfda;border:0;border-bottom:2px solid transparent;padding:{size(4)}px {size(7)}px;font-size:{size(13)}px}}
    QMainWindow QTabBar::tab:selected{{border-bottom-color:#38bbd0;color:#e1f3f5}}
    #libraryDockTabs::tab{{min-width:{size(130)}px}}
    '''
    return theme
