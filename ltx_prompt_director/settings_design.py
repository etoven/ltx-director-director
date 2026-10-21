"""Sidebar Settings presentation; all provider/editor panels retain their behavior."""
from pathlib import Path
from PySide6.QtCore import Qt, QTimer, QEvent
from PySide6.QtGui import QColor, QPainter, QIcon
from PySide6.QtWidgets import (QWidget, QFrame, QLabel, QPushButton, QLineEdit,
    QHBoxLayout, QVBoxLayout, QScrollArea, QFormLayout, QSizePolicy)
from .onboarding_design import StatusBadge
from .workspace_style import load_workspace_font

PAGES = ('Application', 'Local models', 'ChatGPT plan', 'Workspaces', 'Storage and backups')
SECTIONS = ('Engine', 'Video · H3', 'Images · Flux', 'Writing · local')
DESCRIPTIONS = (
    'Choose your providers, artwork defaults, editor preferences and project labels.',
    "Choose your own files or download a matching set. Generation runs inside Director’s private "
    'Python worker. Model weights, configurations and the inference engine are separate setup steps.',
    'Connect your ChatGPT account and choose an available writing model.',
    'Create, customize and exchange the workspaces used by your projects.',
    'Manage cached previews, back up your complete setup and restore saved configurations.',
)


def settings_style(scale=1):
    def px(value): return round(value * scale)
    arrow = (Path(__file__).parent / 'assets/spin-down.svg').as_posix()
    check = (Path(__file__).parent / 'assets/onboarding/check.svg').as_posix()
    return f'''
    QDialog#settingsDialog {{background:#171a25;color:#d8dbe4;font: {px(20)}px "Roboto Condensed";}}
    QDialog#settingsDialog QWidget {{background:transparent;color:#d8dbe4;font-family:"Roboto Condensed";font-size:{px(20)}px;}}
    QDialog#settingsDialog QLabel {{background:transparent;}}
    QDialog#settingsDialog QFrame#settingsRail {{background:#171924;border:0;border-right:1px solid #303844;}}
    QDialog#settingsDialog QLabel#settingsBrand {{font-size:{px(24)}px;font-weight:bold;color:#828994;}}
    QDialog#settingsDialog QLabel#settingsPageTitle {{font-family:Arial;font-size:{px(32)}px;font-weight:bold;}}
    QDialog#settingsDialog QLabel#settingsDescription {{color:#b3b9c4;font-size:{px(20)}px;}}
    QDialog#settingsDialog QLabel#settingsSectionTitle {{font-size:{px(24)}px;font-weight:bold;}}
    QDialog#settingsDialog QFrame#settingsCard {{background:#222a32;border:1px solid #48535e;border-radius:{px(14)}px;}}
    QDialog#settingsDialog QTabWidget::pane {{border:0;background:transparent;}}
    QDialog#settingsDialog QScrollArea {{border:0;background:transparent;}}
    QDialog#settingsDialog QScrollArea > QWidget > QWidget {{background:transparent;}}
    QDialog#settingsDialog QLineEdit,QDialog#settingsDialog QComboBox,QDialog#settingsDialog QAbstractSpinBox {{
      background:#171c26;border:1px solid #424b59;border-radius:{px(5)}px;padding:{px(3)}px {px(8)}px;min-height:{px(24)}px;}}
    QDialog#settingsDialog QLineEdit:focus,QDialog#settingsDialog QComboBox:focus,QDialog#settingsDialog QAbstractSpinBox:focus {{border-color:#228fa6;}}
    QDialog#settingsDialog QComboBox {{padding-right:{px(30)}px;}}
    QDialog#settingsDialog QComboBox::drop-down {{width:{px(30)}px;border-left:1px solid #3d6574;background:#294d5b;border-top-right-radius:{px(4)}px;border-bottom-right-radius:{px(4)}px;}}
    QDialog#settingsDialog QComboBox::down-arrow {{image:url("{arrow}");width:{px(9)}px;height:{px(6)}px;}}
    QDialog#settingsDialog QPushButton {{background:#2b333f;border:1px solid #4a5362;border-radius:{px(6)}px;padding:{px(4)}px {px(10)}px;min-height:{px(24)}px;}}
    QDialog#settingsDialog QPushButton:hover {{background:#34424e;border-color:#358d9e;}}
    QDialog#settingsDialog QPushButton#settingsIconButton {{padding:0;}}
    QDialog#settingsDialog QAbstractSpinBox QLineEdit {{border:0;padding:0;min-height:0;background:transparent;}}
    QDialog#settingsDialog QAbstractItemView {{background:#1a222b;selection-background-color:#285061;}}
    QDialog#settingsDialog QPushButton:disabled {{color:#858c97;border-color:#3c4552;}}
    QDialog#settingsDialog QPushButton[primary="true"] {{background:#00889f;border-color:#149bb4;color:white;}}
    QDialog#settingsDialog QDialogButtonBox QPushButton {{min-height:{px(28)}px;padding:{px(5)}px {px(14)}px;}}
    QDialog#settingsDialog QPushButton#settingsNav {{text-align:left;background:transparent;border:0;border-left:{px(4)}px solid transparent;padding:{px(10)}px {px(28)}px;font-size:{px(20)}px;min-height:{px(28)}px;}}
    QDialog#settingsDialog QPushButton#settingsNav[active="true"] {{background:#252e39;border-left-color:#119cb5;color:#f6f7fa;}}
    QDialog#settingsDialog QPushButton#settingsNav[substep="true"] {{border:0;padding:{px(4)}px {px(4)}px;font-size:{px(20)}px;min-height:{px(27)}px;color:#959eac;}}
    QDialog#settingsDialog QPushButton#settingsNav[substep="true"][active="true"] {{background:transparent;color:#f3f6f8;}}
    QDialog#settingsDialog QPushButton#settingsNav:hover {{background:#253240;}}
    QDialog#settingsDialog QWidget#settingsSubsteps {{border-left:1px solid #37414f;}}
    QDialog#settingsDialog QLineEdit#settingsSearch {{border:1px solid #397083;background:#171e2a;font-size:{px(20)}px;min-height:{px(32)}px;}}
    QDialog#settingsDialog QLabel#settingsRailNote {{color:#9ca6b5;font-size:{px(18)}px;}}
    QDialog#settingsDialog QPlainTextEdit,QDialog#settingsDialog QTextEdit {{background:#1a222b;border:1px solid #35414c;border-radius:{px(4)}px;padding:{px(6)}px;}}
    QDialog#settingsDialog QGroupBox {{border:1px solid #414d5c;border-radius:{px(8)}px;margin-top:{px(18)}px;padding:{px(12)}px;}}
    QDialog#settingsDialog QGroupBox::title {{subcontrol-origin:margin;left:{px(12)}px;padding:0 {px(5)}px;}}
    QDialog#settingsDialog QCheckBox::indicator {{width:{px(16)}px;height:{px(16)}px;border:1px solid #566675;border-radius:{px(3)}px;background:#171c26;}}
    QDialog#settingsDialog QCheckBox::indicator:checked {{border-color:#3d91a0;background:#1f3d45;image:url("{check}");}}
    QDialog#settingsDialog QCheckBox::indicator:hover {{border-color:#268fa3;}}
    '''


class SettingsSurface(QWidget):
    def __init__(self, dialog, tabs, panel, scale=1):
        super().__init__(); self.dialog=dialog; self.tabs=tabs; self.panel=panel; self.scale=scale
        self.serial=0; self.disposed=False
        load_workspace_font(); dialog.setObjectName('settingsDialog'); dialog.setStyleSheet(settings_style(scale))
        root=QHBoxLayout(self); root.setContentsMargins(0,0,0,0); root.setSpacing(0)
        self.rail=QFrame(); self.rail.setObjectName('settingsRail'); self.rail.setFixedWidth(round(244*scale))
        rail=QVBoxLayout(self.rail); rail.setContentsMargins(round(10*scale),round(12*scale),round(10*scale),round(24*scale)); rail.setSpacing(round(8*scale))
        brand=QLabel('workspace'); brand.setObjectName('settingsBrand'); brand.setContentsMargins(5,0,0,17); rail.addWidget(brand)
        self.search=QLineEdit(); self.search.setObjectName('settingsSearch'); self.search.setPlaceholderText('Search settings…'); self.search.setClearButtonEnabled(True)
        self.search.addAction(QIcon(str(Path(__file__).parent/'assets/settings-search.svg')),QLineEdit.ActionPosition.LeadingPosition)
        self.search.setAccessibleName('Search settings')
        search_box=QWidget(); search_row=QHBoxLayout(search_box); search_row.setContentsMargins(round(6*scale),0,round(4*scale),0); search_row.addWidget(self.search)
        rail.addWidget(search_box); rail.addSpacing(round(4*scale))
        navigation=QWidget(); nav=QVBoxLayout(navigation); nav.setContentsMargins(0,0,0,0); nav.setSpacing(round(8*scale))
        self.navigation_scroll=QScrollArea(); self.navigation_scroll.setWidgetResizable(True); self.navigation_scroll.setWidget(navigation)
        self.navigation_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        rail.addWidget(self.navigation_scroll,1)
        self.buttons=[]; self.subbuttons=[]; self.badges=[]
        for index,title in enumerate(PAGES):
            button=QPushButton(title); button.setObjectName('settingsNav'); button.clicked.connect(lambda _,i=index:self.select(i)); nav.addWidget(button); self.buttons.append(button)
            if index==1:
                self.substeps=QWidget(); self.substeps.setObjectName('settingsSubsteps')
                sub=QVBoxLayout(self.substeps); sub.setContentsMargins(round(18*scale),0,0,0); sub.setSpacing(1)
                for section,name in enumerate(SECTIONS):
                    item=QPushButton('· '+name); item.setObjectName('settingsNav'); item.setProperty('substep',True)
                    item.clicked.connect(lambda _,i=section:self.select(1,i)); sub.addWidget(item)
                    badge=StatusBadge(); badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
                    line=QHBoxLayout(item); line.setContentsMargins(0,0,6,0); line.addStretch(); line.addWidget(badge)
                    self.subbuttons.append(item); self.badges.append(badge)
                branch=QWidget(); branch_layout=QHBoxLayout(branch); branch_layout.setContentsMargins(round(16*scale),0,0,0); branch_layout.addWidget(self.substeps)
                nav.addWidget(branch)
        self.no_results=QLabel('No matching settings'); self.no_results.setWordWrap(True); self.no_results.hide(); nav.addWidget(self.no_results)
        nav.addStretch()
        accent=QFrame(); accent.setFixedHeight(5); accent.setStyleSheet('background:#455569;border-radius:2px')
        accent_box=QWidget(); accent_row=QHBoxLayout(accent_box); accent_row.setContentsMargins(12,0,10,0); accent_row.addWidget(accent); rail.addWidget(accent_box)
        note=QLabel('A few choices now…\nA whole sequence of\npossibilities.'); note.setObjectName('settingsRailNote'); note.setContentsMargins(12,0,0,0); rail.addWidget(note)
        root.addWidget(self.rail)
        body=QWidget(); self.body_layout=QVBoxLayout(body)
        self.body_layout.setContentsMargins(round(42*scale),round(28*scale),round(42*scale),round(24*scale)); self.body_layout.setSpacing(round(16*scale))
        header=QVBoxLayout(); header.setSpacing(round(8*scale)); title_row=QHBoxLayout()
        self.title=QLabel(); self.title.setObjectName('settingsPageTitle'); title_row.addWidget(self.title,1)
        self.description=QLabel(); self.description.setObjectName('settingsDescription'); self.description.setWordWrap(True); self.description.setMaximumWidth(round(840*scale))
        self.description.setAlignment(Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignTop)
        self.donate=QPushButton('♥ Support && Donate'); self.donate.setEnabled(False); self.donate.setToolTip('The donation link will be added later.')
        self.donate.setFixedHeight(round(44*scale))
        title_row.addWidget(self.donate,0,Qt.AlignmentFlag.AlignTop); header.addLayout(title_row); header.addWidget(self.description); self.body_layout.addLayout(header)
        self.card=QFrame(); self.card.setObjectName('settingsCard'); card=QVBoxLayout(self.card); card.setContentsMargins(round(16*scale),round(14*scale),round(16*scale),round(14*scale))
        tabs.tabBar().hide(); card.addWidget(tabs); self.body_layout.addWidget(self.card,1); root.addWidget(body,1)
        panel.note.hide(); panel.layout().setContentsMargins(0,0,round(16*scale),0); panel.layout().setSpacing(round(10*scale))
        self.section_title=QLabel(); self.section_title.setObjectName('settingsSectionTitle'); self.section_title.setContentsMargins(round(16*scale),0,0,round(8*scale))
        panel.layout().insertWidget(0,self.section_title)
        panel.tabs.tabBar().hide(); panel.tabs.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Maximum)
        for index in range(panel.tabs.count()):
            form=panel.tabs.widget(index).layout(); form.setContentsMargins(round(16*scale),0,0,0); form.setVerticalSpacing(round(8*scale)); form.setHorizontalSpacing(round(10*scale))
            for row in range(form.rowCount()):
                item=form.itemAt(row,QFormLayout.ItemRole.LabelRole)
                if item and item.widget():item.widget().setFixedWidth(round(210*scale))
        for kind,loras in panel.loras.items():
            form=panel.tabs.widget(1 if kind=='video' else 2).layout(); form.takeRow(loras)
            caption=QLabel(loras.heading.text()); caption.setFixedWidth(round(210*scale)); caption.setAlignment(Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignTop)
            caption.setContentsMargins(0,4,0,0); loras.heading.hide(); form.addRow(caption,loras)
            loras.changed.connect(self.schedule_readiness); loras.changed.connect(self.schedule_size)
        panel.log.output.setMinimumHeight(round(60*scale)); panel.log.output.setMaximumHeight(round(100*scale))
        panel.status.setStyleSheet(f'font-size:{round(14*scale)}px'); panel.links.setStyleSheet(f'font-size:{round(14*scale)}px')
        self.size_timer=QTimer(self); self.size_timer.setSingleShot(True); self.size_timer.timeout.connect(self.fit_local_page)
        panel.tabs.installEventFilter(self); panel.installEventFilter(self); self.description.installEventFilter(self)
        self.readiness_timer=QTimer(self); self.readiness_timer.setSingleShot(True); self.readiness_timer.setInterval(180); self.readiness_timer.timeout.connect(self.refresh_readiness)
        for field in panel.fields.values():field.textChanged.connect(self.schedule_readiness)
        for field in (panel.device,panel.variant,panel.video_variant,panel.video_sampler):field.currentIndexChanged.connect(self.schedule_readiness)
        for spin in (panel.video_width,panel.video_height,panel.video_duration):spin.valueChanged.connect(self.schedule_readiness)
        panel.stateChanged.connect(self.schedule_readiness)
        tabs.currentChanged.connect(self.refresh_navigation); panel.tabs.currentChanged.connect(self.local_changed)
        self.search.textChanged.connect(self.filter_navigation); self.search.returnPressed.connect(self.jump_to_match)
        self.search_index=self.build_search_index()
        for form in tabs.findChildren(QFormLayout):form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.select(0); self.schedule_readiness()

    def paintEvent(self,event):
        painter=QPainter(self); painter.fillRect(self.rect(),QColor('#171a25')); painter.setPen(QColor('#232733'))
        step=round(100*self.scale)
        for x in range(self.rail.width(),self.width(),step):painter.drawLine(x,0,x,self.height())
        for y in range(0,self.height(),step):painter.drawLine(self.rail.width(),y,self.width(),y)

    def attach_buttons(self,buttons):
        buttons.button(buttons.StandardButton.Save).setProperty('primary',True)
        self.body_layout.addWidget(buttons)

    def select(self,page,section=None):
        if section is not None:self.panel.tabs.setCurrentIndex(section)
        self.tabs.setCurrentIndex(page); self.refresh_navigation()

    def local_changed(self,index):
        if index:self.panel.task.setCurrentIndex(self.panel.task.findData(('','video','image','text')[index]))
        self.section_title.setText(SECTIONS[index]); self.refresh_navigation(); self.schedule_size()

    def refresh_navigation(self,*_):
        page=self.tabs.currentIndex(); self.title.setText(PAGES[page]); self.description.setText(DESCRIPTIONS[page])
        for index,button in enumerate(self.buttons):self.active(button,index==page)
        for index,button in enumerate(self.subbuttons):self.active(button,page==1 and index==self.panel.tabs.currentIndex())
        self.substeps.setVisible(page==1 or bool(self.search.text().strip()))
        self.section_title.setText(SECTIONS[self.panel.tabs.currentIndex()]); self.schedule_size()

    @staticmethod
    def active(button,state):
        button.setProperty('active',state); button.style().unpolish(button); button.style().polish(button)

    def schedule_size(self,*_):
        if hasattr(self,'size_timer') and not self.disposed:self.size_timer.start(0)

    def fit_local_page(self):
        if self.disposed:return
        description_height=max(0,self.description.heightForWidth(self.description.width()))
        if description_height!=self.description.minimumHeight():self.description.setMinimumHeight(description_height)
        for loras in self.panel.loras.values():loras.update_visibility()
        for button in self.panel.findChildren(QPushButton):
            if button.text() in ('…','+','×'):
                if button.objectName()!='settingsIconButton':button.setObjectName('settingsIconButton'); button.style().unpolish(button); button.style().polish(button)
                size=(round(36*self.scale),round(32*self.scale))
                if (button.width(),button.height())!=size:button.setFixedSize(*size)
        page=self.panel.tabs.currentWidget(); form=page.layout()
        height=form.totalHeightForWidth(page.width()) if form.hasHeightForWidth() else form.totalSizeHint().height()
        height=max(height,form.totalMinimumSize().height())+2
        if height!=self.panel.tabs.height():self.panel.tabs.setFixedHeight(height)

    def eventFilter(self,watched,event):
        if event.type() in (QEvent.Type.LayoutRequest,QEvent.Type.Show,QEvent.Type.FontChange,QEvent.Type.Resize):self.schedule_size()
        return super().eventFilter(watched,event)

    def build_search_index(self):
        def labels(widget):return ' '.join(label.text() for label in widget.findChildren(QLabel)).casefold()
        self.local_search=[(name+' '+labels(self.panel.tabs.widget(i))+' '+('seed sampler resolution preview references lora trigger weight' if i==1 else 'lora trigger weight artwork' if i==2 else 'python device offload sampling' if i==0 else 'qwen llm instruction')).casefold() for i,name in enumerate(SECTIONS)]
        return [(title+' '+labels(self.tabs.widget(i))).casefold() for i,title in enumerate(PAGES)]

    def matches(self,text):
        return all(word in text for word in self.search.text().casefold().split())

    def filter_navigation(self,*_):
        query=self.search.text().strip(); self.matches_list=[]
        local=[self.matches(text) for text in self.local_search]
        for index,button in enumerate(self.buttons):
            found=any(local) if index==1 else self.matches(self.search_index[index])
            if index==1 and self.matches('local models'):local=[True]*4; found=True
            button.setVisible(found)
            if found and index!=1:self.matches_list.append((index,None))
            if index==1:
                for section,(item,show) in enumerate(zip(self.subbuttons,local)):
                    item.setVisible(show)
                    if show:self.matches_list.append((1,section))
        self.no_results.setVisible(not self.matches_list); self.substeps.setVisible(bool(query) or self.tabs.currentIndex()==1)
        if query and self.matches_list:self.jump_to_match()

    def jump_to_match(self):
        if getattr(self,'matches_list',None):self.select(*self.matches_list[0])

    def schedule_readiness(self,*_):
        if self.disposed:return
        self.serial+=1
        engine=('busy','Preparing the local runtime') if self.panel.busy else ('ready','Runtime checked successfully for this interpreter and device') if self.panel.verified_engine==self.panel.runtime_identity() else ('pending','Use Check engine to verify this interpreter and device')
        self.set_status(0,*engine); self.readiness_timer.start()

    def refresh_readiness(self):
        serial=self.serial; profile=self.panel.value()
        def ready(states):
            if self.disposed or serial!=self.serial:return
            for index,state in enumerate(states,1):self.set_status(index,*state)
        self.panel.owner.start_disk_job(model_file_states,(profile,),ready,
            lambda message:ready([('pending','File check failed: '+str(message))]*3))

    def set_status(self,index,state,message):
        badge=self.badges[index]; badge.state=state; badge.update()
        self.subbuttons[index].setToolTip(message); self.subbuttons[index].setAccessibleName(SECTIONS[index]+': '+message)

    def dispose(self):
        self.disposed=True; self.serial+=1; self.readiness_timer.stop(); self.size_timer.stop()


def model_file_states(profile):
    """Only filesystem preflight, in a background worker. Never imports ML or probes CUDA."""
    from .local_generation import validate_profile
    states=[]
    for kind in ('video','image','text'):
        try:validate_profile(profile,kind); states.append(('ready','Files configured; use Check engine to verify the runtime'))
        except (ValueError,OSError) as error:states.append(('pending',str(error)))
    return states
