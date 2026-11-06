"""One visual document: interlocking sections, pill fields and local output preview."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton,
    QPlainTextEdit, QStackedWidget, QScrollArea)
from .output_templates import default_layout, validate_layout, render, root_fields
from .template_blocks import TemplateCanvas, PalettePill


class TemplateEditor(QWidget):
    save_requested=Signal()
    def __init__(self, owner=None, parent=None):
        super().__init__(parent);self.owner=owner;self.engine='minimax_frames';self.setMinimumWidth(0)
        outer=QVBoxLayout(self);outer.setContentsMargins(0,0,0,0);outer.setSpacing(7)
        self.note=QLabel('');self.note.setWordWrap(True);outer.addWidget(self.note)
        body=QHBoxLayout();body.setSpacing(12);outer.addLayout(body,1)
        self.blocks=QWidget();self.blocks.setFixedWidth(184);palette=QVBoxLayout(self.blocks);palette.setContentsMargins(0,0,0,0);palette.setSpacing(3)
        self.editor=TemplateCanvas()
        title=QLabel('Blocks');title.setStyleSheet('font:15px "Roboto Condensed";color:#dfe8ef;');palette.addWidget(title)
        for category,items in [
            ('STRUCTURE',[('section','Section','section'),('segments','Each segment','segments'),('subjects','Each subject','subjects')]),
            ('PROMPT FIELDS',[('global_prompt','Global direction','field'),('segment_prompt','Segment prompt','field'),
                              ('subject_prompt','Subject details','subjects'),('sound','Sound','field'),('music','Music','field'),('custom','Named field','field')]),
            ('TIMING',[('index','Number','segments'),('name','Name','subjects'),('start','Start time','timing'),
                       ('end','End time','timing'),('duration','Duration','timing'),('role','Frame role','segments')]),
            ('TEXT',[('text','Text','text'),('space','Blank line','text')])]:
            label=QLabel(category);label.setStyleSheet('font:11px "Roboto Condensed";color:#9eafc2;padding-top:10px;');palette.addWidget(label)
            for key,caption,color in items:palette.addWidget(PalettePill(self.editor,key,caption,color))
        palette.addStretch()
        palette_scroll=QScrollArea();palette_scroll.setWidgetResizable(True);palette_scroll.setWidget(self.blocks)
        palette_scroll.setFixedWidth(194);palette_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        palette_scroll.setStyleSheet('QScrollArea{border:0;background:transparent;}');self.palette_scroll=palette_scroll;body.addWidget(palette_scroll)
        canvas=QWidget();right=QVBoxLayout(canvas);right.setContentsMargins(0,0,0,0);right.setSpacing(5)
        header=QHBoxLayout();heading=QLabel('Output layout');heading.setStyleSheet('font:14px "Roboto Condensed";color:#a6b5c5;');header.addWidget(heading);header.addStretch()
        self.preview_button=QPushButton('◉  Preview output');self.preview_button.setCheckable(True)
        self.preview_button.setStyleSheet('QPushButton{font:13px "Roboto Condensed";background:transparent;border:0;padding:3px;min-height:0;color:#a6b5c5;}')
        self.preview_button.toggled.connect(self.show_preview);header.addWidget(self.preview_button);right.addLayout(header)
        self.stack=QStackedWidget();self.stack.setMinimumWidth(0);self.stack.addWidget(self.editor)
        self.preview=QPlainTextEdit();self.preview.setReadOnly(True);self.preview.setStyleSheet('QPlainTextEdit{font:14px "Roboto Condensed";background:#151c26;color:#e0e8ef;border:0;padding:12px;}')
        self.stack.addWidget(self.preview);right.addWidget(self.stack,1);body.addWidget(canvas,1);self.canvas_area=canvas
        footer=QHBoxLayout();self.status=QLabel('Matching sockets highlight as you drag.');self.status.setWordWrap(True);self.status.setStyleSheet('font:12px "Roboto Condensed";color:#a6b5c5;');footer.addWidget(self.status,1)
        self.reset=QPushButton('↺');self.reset.setToolTip('Use model default layout');self.reset.setFixedWidth(30);self.reset.clicked.connect(lambda:self.set_value(default_layout(self.engine)));footer.addWidget(self.reset)
        self.save_button=QPushButton('Save workspace');self.save_button.setStyleSheet('QPushButton{font:14px "Roboto Condensed";background:#235d60;color:#e0e8ef;border:1px solid #378b8b;border-radius:5px;padding:6px 10px;min-height:24px;}');self.save_button.clicked.connect(self.save_requested);footer.addWidget(self.save_button);outer.addLayout(footer)
        self.editor.status_changed.connect(self.status.setText);self.editor.changed.connect(self.changed)
        self.set_value(default_layout(self.engine))
    def changed(self):
        if self.preview_button.isChecked():self.show_preview(True)
    def set_available(self,available):
        for widget in (self.palette_scroll,self.canvas_area,self.reset,self.save_button,self.status):widget.setVisible(available)
        self.note.setVisible(not available)
        self.note.setText('Generic Prompt Refine works directly on the supplied text and does not use an output template.' if self.engine == 'minimax_prompt' else 'LTX uses separate global and segment prompts. A unified template does not apply to this workspace.')
    def set_value(self,value):
        self.preview_button.setChecked(False);self.editor.setPlainText(validate_layout(value))
    def value(self):return validate_layout(self.editor.toPlainText())
    def show_preview(self,enabled):
        self.preview_button.setText('←  Back to template' if enabled else '◉  Preview output')
        if not enabled:self.stack.setCurrentWidget(self.editor);return
        try:
            layout=self.value()
            from .models import Segment
            owner=self.owner;segments=list(owner.active_group_segments()) if owner and owner.segments else [
                Segment('Opening','','',kind='text',role='text',prompt='The courier enters quietly.',duration=4),
                Segment('Beacon','','',kind='text',role='text',prompt='The beacon casts a soft blue light.',duration=3.5)]
            subjects=[dict(id='preview-subject',name='Courier',prompt='A courier in a weathered navy coat.',role='identity')]
            direction=owner.intent.toPlainText() if owner and owner.intent.toPlainText().strip() else 'A quiet blue evening. A courier follows the beacon through the city.'
            variables={name:('Soft city ambience and distant footsteps.' if name=='sound' else 'A sparse piano theme.' if name=='music' else '('+name.replace('_',' ')+')') for name in root_fields(layout)}
            self.preview.setPlainText(render(layout,direction,segments,subjects,variables));self.stack.setCurrentWidget(self.preview)
        except ValueError as error:
            self.preview.setPlainText(str(error));self.stack.setCurrentWidget(self.preview)
