"""Scope selector styled as a compact full-width panel heading."""
from pathlib import Path
import re

def scope_style(scale):
    arrow=(Path(__file__).parent/'assets/spin-down.svg').as_posix()
    style='QComboBox#surfaceScope{background:transparent;border:0;border-bottom:1px solid transparent;border-radius:0;padding:4px 30px 4px 1px;color:#e0e7ed;font:600 14px "Roboto Condensed";min-height:22px;max-height:32px}\nQComboBox#surfaceScope:hover{background:#1b2a36}\nQComboBox#surfaceScope:focus{border-bottom:1px solid #478d9e}\nQComboBox#surfaceScope::drop-down{subcontrol-origin:padding;subcontrol-position:top right;width:26px;background:transparent;border:0}\nQComboBox#surfaceScope::down-arrow{image:url("{arrow}");width:9px;height:6px}\nQComboBox#surfaceScope QAbstractItemView{background:#1b2733;border:1px solid #3c5364;color:#e5edf2;selection-background-color:#254b5b;selection-color:#ffffff;outline:0;padding:4px;font:14px "Roboto Condensed"}\nQComboBox#surfaceScope QAbstractItemView::item{min-height:28px;padding:4px 8px;border:0}\n'
    style=style.replace('{arrow}',arrow)
    return re.sub(r"(\d+)px",lambda match:str(max(1,round(int(match[1])*scale)))+"px",style)
