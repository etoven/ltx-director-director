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
    '''
    return theme
