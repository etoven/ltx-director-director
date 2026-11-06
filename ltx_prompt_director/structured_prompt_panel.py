"""A section-restricted production editor with local timeline synchronization."""
from __future__ import annotations
import copy
import math
import re
from uuid import uuid4

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPlainTextEdit,
    QLineEdit, QPushButton, QScrollArea, QFrame, QMenu)

from .models import Segment
from .output_templates import layout_for, render, split_legacy, parse_rendered, stamp, document_parts, root_fields


def seconds(text):
    pieces = text.strip().split(':')
    if not 1 <= len(pieces) <= 3 or not all(re.fullmatch(r'\d+(?:\.\d+)?', p) for p in pieces):
        raise ValueError('Use seconds, MM:SS.xx or HH:MM:SS.xx.')
    value = 0.0
    for piece in pieces:
        value = value * 60 + float(piece)
    if not math.isfinite(value):
        raise ValueError('Time must be finite.')
    return value


class StructuredPromptPanel(QWidget):
    def __init__(self, owner):
        super().__init__(); self.owner = owner; self.updating = False; self.rows = {}; self.signature = None
        self.selected_id = None
        self.reveal_pending = False
        self.timer = QTimer(self); self.timer.setSingleShot(True); self.timer.setInterval(0)
        self.timer.timeout.connect(self.refresh)
        self.reveal_timer = QTimer(self); self.reveal_timer.setSingleShot(True); self.reveal_timer.setInterval(40)
        self.reveal_timer.timeout.connect(self.reveal_selected)
        outer = QHBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0)
        gutter = QVBoxLayout(); outer.addLayout(gutter)
        self.add = QPushButton('+'); self.add.setFixedWidth(32); self.add.setToolTip('Add a timeline section or subject')
        menu = QMenu(self.add); menu.addAction('Timeline section', self.add_segment); menu.addAction('Subject definition', self.add_subject)
        self.add.setMenu(menu); gutter.addWidget(self.add); gutter.addStretch()
        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True); outer.addWidget(self.scroll, 1)
        self.content = QWidget(); self.parts = QVBoxLayout(self.content); self.parts.setContentsMargins(3, 3, 3, 3)
        self.scroll.setWidget(self.content)
        owner.unified_prompt.textChanged.connect(self.schedule)
        owner.global_prompt.textChanged.connect(self.schedule)
        owner.intent.textChanged.connect(self.schedule)
        owner.timeline.currentRowChanged.connect(self.schedule)
        self.status = QLabel(); self.status.setWordWrap(True)
        self.source = owner.unified_prompt

    def schedule(self, *_):
        if not self.updating and not self.owner._loading:
            self.timer.start()

    def state(self):
        owner = self.owner
        if not hasattr(owner, 'prompt_template_states'):
            owner.prompt_template_states = {}
        key = f'{owner.project_type}:{getattr(owner, "_active_render_group", 0)}'
        return owner.prompt_template_states.setdefault(key, {})

    def segments(self):
        owner = self.owner
        if hasattr(owner, 'render_groups'):
            return list(owner.render_groups().get(getattr(owner, '_active_render_group', 0), []))
        return list(owner.segments)

    def subjects(self):
        owner = self.owner; state = self.state(); records = []
        for index, reference in enumerate(owner.active_reference_images()):
            reference_id = f'reference:{index}'
            if reference and (reference_id not in state.get('covered_references', []) or state.get('subjects', {}).get(reference_id)):
                records.append(dict(id=f'reference:{index}', name=reference.get('name', f'Reference {index + 1}'),
                                    role=reference.get('role', 'identity'), prompt=reference.get('description') or reference.get('prompt') or reference.get('notes', '')))
        records += copy.deepcopy(state.get('custom_subjects', []))
        order = {key: index for index, key in enumerate(state.get('subject_order', []))}
        return sorted(records, key=lambda item: order.get(item['id'], len(order)))

    def refresh(self):
        owner = self.owner
        if self.updating or owner._loading or not owner.unified_workspace or owner.prompt_only_workspace or owner.workspace_engine == 'shot_sequence':
            return
        self.updating = True
        try:
            state = self.state(); segments = self.segments(); subjects = self.subjects(); layout = layout_for(owner.workspace_definition)
            state.setdefault('prompts', {}); state.setdefault('sources', {}); state.setdefault('subjects', {}); state.setdefault('fields', {})
            state.setdefault('subject_sources', {})
            source_text = owner.unified_prompt.toPlainText() if owner.unified_workspace else state.get('last_render', '')
            if state.pop('migration_source_pending', False):
                state['last_render'] = source_text
            if 'global' not in state:
                legacy = source_text
                global_text, prompts = split_legacy(legacy, segments) if legacy else ('', {})
                state.update({'global': global_text or owner.global_prompt.toPlainText() or owner.intent.toPlainText(), 'prompts': prompts})
                if legacy:
                    state['original_draft'] = legacy
                state['sources'] = {s.id: s.prompt for s in segments}
            elif source_text and source_text != state.get('last_render', ''):
                parsed = parse_rendered(source_text, layout, segments, subjects)
                if parsed:
                    state['global'] = parsed.get('global', state.get('global', ''))
                    state['fields'].update({key.removeprefix('field:'): value for key, value in parsed.items() if key.startswith('field:')})
                    state['subjects'].update({s['id']: parsed[s['id']] for s in subjects if s['id'] in parsed})
                    state['prompts'].update({s.id: parsed[s.id] for s in segments if s.id in parsed})
                else:
                    # Older free-form responses remain available as an editable part.
                    state['global'], prompts = split_legacy(source_text, segments)
                    state['prompts'].update(prompts)
                    state['original_draft'] = source_text
            for segment in segments:
                if state['sources'].get(segment.id) != segment.prompt:
                    state['prompts'][segment.id] = segment.prompt
                state['sources'][segment.id] = segment.prompt
                state['prompts'].setdefault(segment.id, segment.prompt)
            global_source = dict(production=owner.global_prompt.toPlainText(), intent=owner.intent.toPlainText())
            previous_source = state.get('global_source')
            if isinstance(previous_source, dict):
                for key in ('production', 'intent'):
                    if global_source[key] != previous_source.get(key):
                        state['global'] = global_source[key]
            state['global_source'] = global_source
            for subject in subjects:
                previous = state['subject_sources'].get(subject['id'])
                if previous is not None and previous != subject['prompt']:
                    state['subjects'][subject['id']] = subject['prompt']
                state['subject_sources'][subject['id']] = subject['prompt']
                state['subjects'].setdefault(subject['id'], subject['prompt'])
                subject['prompt'] = state['subjects'][subject['id']]
            records = [{**s.to_dict(), 'prompt': state['prompts'][s.id]} for s in segments]
            text = render(layout, state['global'], records, subjects, state['fields'])
            state['last_render'] = text
            if owner.unified_prompt.toPlainText() != text:
                owner.unified_prompt.setPlainText(text)
            if owner.unified_workspace:
                owner.minimax_prompt_text = text
            spans = document_parts(layout, state['global'], records, subjects, state['fields'])
            specs = []; prefix = ''
            for span in spans:
                if span['kind'] == 'literal':
                    prefix += span['text']
                else:
                    specs.append({**span, 'heading': prefix}); prefix = ''
            structure = (owner.current_project_id, owner.project_type, layout,
                         tuple((part['key'], part.get('record', {}).get('name', '') if part.get('record') else '') for part in specs))
            if structure != self.signature:
                self.rebuild(specs, segments, prefix); self.signature = structure
            for spec in specs:
                self.set_text(spec['key'], spec['text'])
                self.headings[spec['key']].setText(spec['heading'].strip('\n'))
            start = 0.0
            for segment in segments:
                if segment.id in self.rows:
                    row = self.rows[segment.id]
                    for field, value in zip(row[1:], (stamp(start), stamp(start + segment.duration))):
                        if not field.hasFocus():
                            field.setText(value)
                start += segment.duration
            self.add.setEnabled(not owner.ai_busy)
            for row in self.rows.values():
                for field in row:
                    field.setReadOnly(owner.ai_busy)
            selected = owner.current_segment()
            selected_id = selected.id if selected else None
            for key, row in self.rows.items():
                row[0].parentWidget().setStyleSheet('QFrame { border-left: 2px solid #5d9bcb; }' if key == selected_id else '')
            if (selected_id != self.selected_id or self.reveal_pending) and selected_id in self.rows:
                self.reveal_timer.start()
            self.reveal_pending = False
            self.selected_id = selected_id
        finally:
            self.updating = False

    def request_reveal(self):
        self.reveal_pending = True
        self.schedule()
        self.reveal_selected()
        self.reveal_timer.start()

    def reveal_selected(self):
        segment = self.owner.current_segment()
        if segment is not None and segment.id in self.rows:
            self.parts.activate()
            section = self.rows[segment.id][0].parentWidget()
            self.scroll.verticalScrollBar().setValue(max(0, section.y() - 4))

    def set_text(self, key, value):
        editor = self.rows[key][0]
        if editor.toPlainText() != value:
            editor.blockSignals(True); editor.setPlainText(value); editor.blockSignals(False)

    def rebuild(self, specs, segments, trailing):
        position = self.scroll.verticalScrollBar().value()
        for row in self.rows.values():
            self.owner._effect_drop_targets.pop(row[0].viewport(), None)
            getattr(self.owner, '_effect_part_segments', {}).pop(row[0].viewport(), None)
        self.rows = {}; self.headings = {}
        while self.parts.count():
            item = self.parts.takeAt(0)
            if item.widget() and item.widget() is not self.status:
                item.widget().deleteLater()
        by_id = {segment.id: segment for segment in segments}
        for spec in specs:
            self.make_part(spec['key'], spec['heading'].strip('\n'), by_id.get(spec['key']) if spec['timed'] else None)
        if trailing.strip():
            label = QLabel(trailing); label.setTextFormat(Qt.TextFormat.PlainText); label.setWordWrap(True); self.parts.addWidget(label)
        self.parts.addWidget(self.status); self.parts.addStretch()
        self.scroll.verticalScrollBar().setValue(position)

    def make_part(self, key, title, segment=None):
        frame = QFrame(); layout = QVBoxLayout(frame); layout.setContentsMargins(6, 6, 6, 9)
        header = QHBoxLayout(); heading = QLabel(title); heading.setTextFormat(Qt.TextFormat.PlainText); heading.setWordWrap(True); heading.setMinimumWidth(0); header.addWidget(heading, 1); self.headings[key] = heading
        fields = []
        if segment:
            for end, label in [(False, 'Start'), (True, 'End')]:
                field = QLineEdit(); field.setMaximumWidth(106); field.setAccessibleName(f'{title} {label}')
                field.setToolTip(f'{label} time · edit to move or resize the timeline segment')
                field.textEdited.connect(lambda _, sid=key, is_end=end: self.edit_time(sid, is_end))
                header.addWidget(field); fields.append(field)
        layout.addLayout(header)
        editor = QPlainTextEdit(); editor.setMinimumHeight(70); editor.setMaximumHeight(180)
        editor.setStyleSheet('QPlainTextEdit { background: rgba(128, 148, 170, 0.09); border: 1px solid #485360; border-radius: 4px; }')
        editor.setAccessibleName(title + ' text'); editor.textChanged.connect(lambda: self.edit(key))
        if hasattr(self.owner, '_effect_drop_targets'):
            self.owner._effect_drop_targets[editor.viewport()] = 'segment' if segment else 'unified'
            self.owner._effect_part_segments = getattr(self.owner, '_effect_part_segments', {})
            if segment:
                self.owner._effect_part_segments[editor.viewport()] = key
            editor.viewport().installEventFilter(self.owner)
        layout.addWidget(editor); self.rows[key] = [editor, *fields]; self.parts.addWidget(frame)

    def edit(self, key):
        if self.updating or self.owner.ai_busy:
            return
        state = self.state(); text = self.rows[key][0].toPlainText()
        if key == 'global':
            state['global'] = text
        elif key.startswith('field:'):
            state['fields'][key.removeprefix('field:')] = text
        elif key.startswith(('reference:', 'subject:')):
            state['subjects'][key] = text
        else:
            state['prompts'][key] = text
            segment = next((s for s in self.owner.segments if s.id == key), None)
            if segment:
                segment.prompt = text; state['sources'][key] = text
                if self.owner.current_segment() is segment:
                    self.owner.refresh_segment_prompt_box(self.owner.timeline.currentRow())
                self.owner.refresh_text_segment_previews()
        self.owner.mark_dirty(); self.schedule()

    def edit_time(self, sid, end):
        if self.updating or self.owner.ai_busy:
            return
        owner = self.owner; index = next((i for i, s in enumerate(owner.segments) if s.id == sid), -1)
        if index < 0:
            return
        segment = owner.segments[index]; row = self.rows[sid]
        try:
            value = seconds(row[2 if end else 1].text())
            start = sum(s.duration for s in self.segments()[:self.segments().index(segment)])
            if end:
                duration = value - start
                if duration < .01 or duration > owner.available_segment_duration(segment) + .000001:
                    raise ValueError('The end must follow the start and fit the sequence limit.')
                segment.duration = duration
            else:
                group_segments = self.segments(); local_index = group_segments.index(segment)
                if not local_index:
                    if value != 0:
                        raise ValueError('The first timeline section starts at zero.')
                    return
                previous = group_segments[local_index - 1]
                delta = value - start
                if previous.duration + delta < .01 or segment.duration - delta < .01:
                    raise ValueError('Keep at least 0.01 seconds in both neighboring sections.')
                previous.duration += delta; segment.duration -= delta
            self.status.clear(); owner.refresh_timeline(index); owner.mark_dirty(); self.schedule()
        except ValueError as error:
            self.status.setText(str(error))

    def add_segment(self):
        owner = self.owner
        if owner.ai_busy:
            return
        from .timing_limits import shot_limit
        maximum_shots = shot_limit(owner.workspace_definition)
        if maximum_shots and len(self.segments()) >= maximum_shots:
            self.status.setText('This workspace has reached its section limit.'); return
        remaining = (owner.maximum_sequence_duration - sum(s.duration for s in self.segments())
                     if owner.maximum_sequence_duration is not None else 5)
        if remaining < .01:
            self.status.setText('Increase the workspace duration limit before adding a section.'); return
        owner.segments.append(Segment(name=f'Section {len(owner.segments) + 1}', media_path='', preview_path='',
                                      kind='text', role='text', duration=min(5, remaining), render_group=owner._active_render_group))
        owner.refresh_timeline(len(owner.segments) - 1); owner.mark_dirty(); self.schedule()

    def add_subject(self):
        state = self.state(); records = state.setdefault('custom_subjects', [])
        records.append(dict(id='subject:' + uuid4().hex, name=f'Subject {len(records) + 1}', prompt='', role='identity'))
        self.owner.mark_dirty(); self.schedule()
