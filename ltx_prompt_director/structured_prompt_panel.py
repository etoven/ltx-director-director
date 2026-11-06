"""A section-restricted production editor with local timeline synchronization."""
from __future__ import annotations
import copy
import math
import re
import threading
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
        self.definition_timer = QTimer(self); self.definition_timer.setSingleShot(True); self.definition_timer.setInterval(700)
        self.definition_timer.timeout.connect(self.drain_definition_queue)
        self.definition_job = None; self.definition_result = None; self.reassess_after_definitions = None
        outer = QHBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0)
        gutter = QVBoxLayout(); outer.addLayout(gutter)
        self.add = QPushButton('+'); self.add.setFixedWidth(32); self.add.setToolTip('Add a timeline section, subject, image, video or audio reference')
        menu = QMenu(self.add); menu.addAction('Timeline section', self.add_segment); menu.addAction('Subject definition', self.add_subject)
        for kind in ('image', 'video', 'audio'):
            menu.addAction(kind.title() + ' reference…', lambda checked=False, kind=kind: self.add_reference(kind))
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
        from .prompt_references import subjects
        return subjects(self.segments(), self.owner.active_reference_images(), self.state())

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
                parsed = parse_rendered(legacy, layout, segments, subjects) if legacy else None
                if parsed is not None:
                    global_text = parsed.get('global', '')
                    prompts = {s.id: parsed[s.id] for s in segments if s.id in parsed}
                    state['fields'].update({key.removeprefix('field:'): value for key, value in parsed.items() if key.startswith('field:')})
                    state['subjects'].update({s['id']: parsed[s['id']] for s in subjects if s['id'] in parsed})
                else:
                    global_text, prompts = split_legacy(legacy, segments) if legacy else ('', {})
                state.update({'global': global_text, 'prompts': prompts})
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
            # Global is instruction input. Summary/scene prose is independent
            # model output, and must not be overwritten with raw edit requests.
            state['global_source'] = global_source
            for subject in subjects:
                previous = state['subject_sources'].get(subject['id'])
                if previous is not None and previous != subject['prompt']:
                    state['subjects'][subject['id']] = subject['prompt']
                state['subject_sources'][subject['id']] = subject['prompt']
                state['subjects'].setdefault(subject['id'], subject['prompt'])
                subject['prompt'] = state['subjects'][subject['id']]
            from .prompt_references import reference_roster
            roster = reference_roster(subjects)
            previous_roster = state.get('reference_inventory')
            if state.pop('_reference_inventory_restored', False):previous_roster = None
            if previous_roster is not None and previous_roster != roster:
                changes = state.setdefault('reference_changes', [])
                old = {item['id']: item for item in previous_roster}; new = {item['id']: item for item in roster}
                changed = {key for key in old.keys() | new.keys() if old.get(key) != new.get(key)}
                before = [item for item in previous_roster if item['id'] in changed]
                after = [item for item in roster if item['id'] in changed]
                if (changes and before and after and changes[-1]['after'] == before
                        and {item['id'] for item in changes[-1]['before']} == {item['id'] for item in after}):
                    changes[-1]['after'] = after
                else:changes.append(dict(before=before, after=after))
                state['reference_changes'] = changes[-64:]
                state['references_need_reassessment'] = True
                # Imported slots are queued by their setter. Detect newly added
                # or replaced timeline media here, without reacting to prompt edits.
                timeline_ids = [item['id'] for item in roster if item.get('id', '').startswith('subject:timeline:')
                    and (item['id'] not in old or any(old[item['id']].get(key) != item.get(key)
                         for key in ('source_path', 'source_name', 'media_kind', 'role')))]
                if timeline_ids:self.queue_definition_generation(timeline_ids)
            state['reference_inventory'] = copy.deepcopy(roster)
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
        if state.get('pending_reference_definitions') and not state.get('reference_definition_error') and not self.definition_job and not self.definition_timer.isActive():
            self.definition_timer.start(700)
        self.update_reference_flag()

    @property
    def definition_running(self):
        return (self.definition_job is not None and self.definition_job['context'] == self.definition_context()
                and any(item['id'] in self.state().get('pending_reference_definitions', []) for item in self.definition_job['selected']))

    def definition_context(self):
        owner = self.owner
        return (owner.workspace_revision, owner.current_project_id, owner.project_type, getattr(owner, '_active_render_group', 0))

    def update_reference_flag(self):
        surface = getattr(self.owner, 'prompt_surface', None)
        if surface is not None:surface.update_reference_flag()

    def queue_definition_generation(self, identities):
        state = self.state(); pending = state.setdefault('pending_reference_definitions', [])
        pending[:] = list(dict.fromkeys([*pending, *identities]))
        state['references_need_reassessment'] = True
        state.pop('reference_definition_error', None)
        self.definition_timer.start(700); self.update_reference_flag()

    def drain_definition_queue(self):
        owner = self.owner
        if owner.render_queue.closing:return
        if owner.ai_busy:
            self.definition_timer.start(350); return
        if self.definition_result is not None:
            result = self.definition_result; self.definition_result = None
            self.definition_finished(result); return
        if self.definition_job is not None or owner._loading or not owner.unified_workspace or owner.prompt_only_workspace:return
        self.refresh()
        state = self.state(); pending = state.setdefault('pending_reference_definitions', [])
        current = {item['id']: item for item in self.subjects()}
        pending[:] = [key for key in pending if key in current]
        if not pending or state.get('reference_definition_error'):return
        credentials = owner.ai_credentials(interactive=False)
        if not credentials:
            state['reference_definition_error'] = 'Choose a connected writing model in Settings to generate reference definitions.'
            self.update_reference_flag(); return
        selected = [dict(current[key], prompt=state.get('subjects', {}).get(key, current[key]['prompt'])) for key in pending]
        segments = copy.deepcopy(self.segments()); references = copy.deepcopy(owner.active_reference_images())
        cancel = threading.Event()
        self.definition_job = dict(context=self.definition_context(), selected=selected, references=references, cancel=cancel)
        self.update_reference_flag()
        from .prompt_references import generate_definitions
        self.launch_definition_worker(generate_definitions, (segments, references, selected, *credentials,
            owner.settings.value('api_timeout', 400, int)), cancel)

    def launch_definition_worker(self, operation, args, cancel):
        from .ui import BackgroundWorker
        worker = BackgroundWorker(operation, args, cancel)
        self.definition_job['worker'] = worker
        worker.signals.finished.connect(self.definition_finished)
        worker.signals.failed.connect(self.definition_failed)
        self.owner.media_pool.start(worker)

    def definition_finished(self, result):
        owner = self.owner; job = self.definition_job
        if job is None:return
        if owner.render_queue.closing:
            self.definition_job = None; return
        if owner.ai_busy:
            self.definition_result = result; self.definition_timer.start(350); return
        self.definition_job = None
        if not self.definition_sources_current(job):
            self.definition_timer.start(); self.update_reference_flag(); return
        state = self.state(); pending = state.setdefault('pending_reference_definitions', [])
        for item in job['selected']:
            key = item['id']
            if key in pending:pending.remove(key)
            # A newer hand edit to this definition takes precedence.
            if state.get('subjects', {}).get(key, item['prompt']) != item['prompt']:continue
            text = result[key]; state.setdefault('subjects', {})[key] = text
            slot = item.get('reference_slot')
            if slot is not None:
                reference = owner.minimax_reference_images[slot]
                field = 'audio_description' if item.get('media_kind') == 'audio' and reference.get('kind') == 'video' and not reference.get('audio_only') else 'description'
                reference[field] = text
        state.pop('reference_definition_error', None)
        owner.minimax_prompt_cache_key = ''; owner.minimax_panel.refresh_references()
        owner.mark_dirty(); self.refresh(); self.definition_timer.start()
        if self.reassess_after_definitions == self.definition_context() and not pending:
            self.reassess_after_definitions = None
            QTimer.singleShot(0, self.reassess_references)

    def definition_sources_current(self, job):
        if job['context'] != self.definition_context():return False
        current = {item['id']: item for item in self.subjects()}
        references = self.owner.active_reference_images()
        for item in job['selected']:
            live = current.get(item['id'])
            if live is None or any(item.get(key) != live.get(key) for key in ('label', 'media_kind', 'source_name', 'source_path', 'role', 'notes')):return False
            slot = item.get('reference_slot')
            if slot is not None:
                before = job['references'][slot] or {}; after = references[slot] or {}
                if any(before.get(key) != after.get(key) for key in ('kind', 'path', 'image', 'audio_path', 'audio_only', 'role', 'notes')):return False
        return True

    def definition_failed(self, message):
        job = self.definition_job; self.definition_job = None; self.definition_result = None
        current = job is not None and self.definition_sources_current(job)
        if current:
            self.state()['reference_definition_error'] = str(message)
        if current:self.reassess_after_definitions = None
        self.update_reference_flag()
        if job is not None and not current:self.definition_timer.start(700)

    def cancel_definition_generation(self):
        self.definition_timer.stop()
        if self.definition_job is not None:self.definition_job['cancel'].set()

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
        if segment or key.startswith(('reference:', 'subject:')):
            remove = QPushButton('×'); remove.setFixedWidth(26)
            remove.setAccessibleName('Remove ' + title)
            remove.setToolTip('Remove timeline section' if segment else 'Remove reference or subject definition')
            remove.clicked.connect(lambda checked=False: self.remove_part(key))
            header.addWidget(remove)
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
            pending = state.get('pending_reference_definitions', [])
            if key in pending:pending.remove(key)
            if not pending:state.pop('reference_definition_error', None)
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
        if self.owner.ai_busy:return
        state = self.state(); records = state.setdefault('custom_subjects', [])
        records.append(dict(id='subject:' + uuid4().hex, name=f'Subject {len(records) + 1}', prompt='', role='identity'))
        self.owner.mark_dirty(); self.schedule()

    def add_reference(self, kind):
        if self.owner.ai_busy:return
        owner = self.owner
        limit = owner.workspace_definition.get('references', {}).get('untimed_slots', 0)
        slot = next((i for i, item in enumerate(owner.minimax_reference_images[:limit]) if not item), None)
        if slot is None:
            self.status.setText('No reference slots available in this workspace. Remove a reference or choose a reference workspace.')
            return
        from .file_dialogs import choose_document_open
        filters = dict(image='Images (*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff)',
                       video='Videos (*.mp4 *.webm *.mov *.mkv)', audio='Audio (*.wav *.mp3 *.flac *.ogg *.m4a *.aac *.opus)')
        path = choose_document_open(self, 'Add ' + kind + ' reference', '', filters[kind])
        if path:
            owner.show_reference_images()
            owner.minimax_panel.reference_targets[slot].load_path(path)

    def remove_part(self, key):
        if self.owner.ai_busy:return
        owner = self.owner; state = self.state()
        subject = next((item for item in self.subjects() if item['id'] == key), None)
        if subject:
            slot = subject.get('reference_slot')
            if slot is not None:
                # Clear the source reference as well as all its paired definitions.
                related = [item['id'] for item in self.subjects() if item.get('reference_slot') == slot]
                owner.set_minimax_reference_image(slot, None)
            else:
                related = [key]
                if subject.get('timeline_id'):
                    state.setdefault('excluded_subjects', []).append(key)
            state['custom_subjects'] = [item for item in state.get('custom_subjects', []) if item['id'] not in related]
            for field in ('subjects', 'subject_sources'):
                for identity in related:state.get(field, {}).pop(identity, None)
        else:
            owner.delete_by_id(key)
            for field in ('prompts', 'sources'):state.get(field, {}).pop(key, None)
        owner.mark_dirty(); self.refresh()

    def generate_references(self, slot=None):
        owner = self.owner
        if owner.ai_busy or not owner.unified_workspace:return
        self.refresh()
        state = self.state()
        selected = [dict(item, prompt=state.get('subjects', {}).get(item['id'], item['prompt']))
                    for item in self.subjects() if slot is None or item.get('reference_slot') == slot]
        if not selected:
            self.status.setText('Add reference media or a subject definition first.'); return
        credentials = owner.ai_credentials()
        if not credentials:return
        from .prompt_references import generate_definitions
        segments = copy.deepcopy(self.segments()); references = copy.deepcopy(owner.active_reference_images())
        snapshot = (owner.workspace_revision, owner.current_project_id, owner.project_type, owner._active_render_group)
        def finished(result):
            owner.set_ai_controls_enabled(True)
            current = (owner.workspace_revision, owner.current_project_id, owner.project_type, owner._active_render_group)
            if current != snapshot or references != owner.active_reference_images() or [s.to_dict() for s in segments] != [s.to_dict() for s in self.segments()]:
                self.status.setText('References changed; generated definitions were discarded.'); return
            live = self.state()
            if any(live.get('subjects', {}).get(item['id'], item['prompt']) != item['prompt'] for item in selected):
                self.status.setText('Definitions changed; generated definitions were discarded.'); return
            live.setdefault('subjects', {}).update(result)
            for index in {item['reference_slot'] for item in selected if item.get('reference_slot') is not None}:
                value = dict(owner.minimax_reference_images[index])
                for item in selected:
                    if item.get('reference_slot') == index:
                        field = 'audio_description' if item.get('media_kind') == 'audio' and value.get('kind') == 'video' and not value.get('audio_only') else 'description'
                        value[field] = result[item['id']]
                owner.set_minimax_reference_image(index, value)
            owner.mark_dirty(); self.refresh()
            self.status.setText('Reference definitions updated.')
        owner.start_ai_worker(generate_definitions,
            (segments, references, selected, *credentials, owner.settings.value('api_timeout', 400, int)),
            'Generating reference definitions…', finished, review_text=False)

    def reassessment_document(self):
        state = self.state()
        document = {'global': state.get('global', '')}
        document.update({'field:' + key: value for key, value in state.get('fields', {}).items()})
        document.update({s.id: state.get('prompts', {}).get(s.id, s.prompt) for s in self.segments()})
        document.update({item['id']: state.get('subjects', {}).get(item['id'], item['prompt']) for item in self.subjects()})
        document['source:global'] = self.owner.global_prompt.toPlainText()
        document['source:intent'] = self.owner.intent.toPlainText()
        for segment in self.segments():
            for field in ('prompt', 'image_prompt', 'artwork_prompt'):
                document[f'timeline:{segment.id}:{field}'] = getattr(segment, field)
        for slot, reference in enumerate(self.owner.active_reference_images()):
            if reference:
                for field in ('notes', 'description', 'audio_description'):
                    if field in reference:document[f'media:{slot}:{field}'] = reference[field]
        return document

    def reassess_references(self):
        owner = self.owner
        if owner.ai_busy or not owner.unified_workspace:return
        self.refresh()
        credentials = owner.ai_credentials()
        if not credentials:return
        if self.state().get('pending_reference_definitions'):
            self.state().pop('reference_definition_error', None)
            self.reassess_after_definitions = self.definition_context()
            self.definition_timer.start(0); self.update_reference_flag(); return
        from .prompt_references import reassess_references, reference_roster
        document = copy.deepcopy(self.reassessment_document())
        roster = reference_roster([dict(item, prompt=document[item['id']]) for item in self.subjects()])
        references = copy.deepcopy(owner.active_reference_images())
        segments = [s.to_dict() for s in self.segments()]
        snapshot = (owner.workspace_revision, owner.current_project_id, owner.project_type, owner._active_render_group)
        changes = copy.deepcopy(self.state().get('reference_changes', []))
        def finished(updates):
            owner.set_ai_controls_enabled(True)
            current = (owner.workspace_revision, owner.current_project_id, owner.project_type, owner._active_render_group)
            if snapshot == current:self.refresh()
            if (snapshot != current or references != owner.active_reference_images() or
                    segments != [s.to_dict() for s in self.segments()] or document != self.reassessment_document()):
                self.status.setText('Prompt or references changed; reassessment was discarded.'); return
            state = self.state()
            updates = {key: text for key, text in updates.items() if text != document[key]}
            state['reference_reassessment_backup'] = {key: document[key] for key in updates}
            timeline_changed = references_changed = False
            for key, text in updates.items():
                if key == 'global':state['global'] = text
                elif key.startswith('field:'):state['fields'][key.removeprefix('field:')] = text
                elif key.startswith('timeline:'):
                    sid, field = key.removeprefix('timeline:').rsplit(':', 1)
                    segment = next(item for item in self.segments() if item.id == sid)
                    setattr(segment, field, text); timeline_changed = True
                    if field == 'prompt':state['sources'][sid] = text
                elif key.startswith('media:'):
                    _, slot, field = key.split(':', 2)
                    owner.minimax_reference_images[int(slot)][field] = text; references_changed = True
                elif key in ('source:global', 'source:intent'):
                    editor = owner.global_prompt if key == 'source:global' else owner.intent
                    blocked = editor.blockSignals(True)
                    try:editor.setPlainText(text)
                    finally:editor.blockSignals(blocked)
                elif key.startswith(('reference:', 'subject:')):state['subjects'][key] = text
                else:state['prompts'][key] = text
            # All surfaces were reassessed together; source synchronization must
            # not replace the independently returned Unified passages afterward.
            state['global_source'] = dict(production=owner.global_prompt.toPlainText(), intent=owner.intent.toPlainText())
            for subject in self.subjects():state['subject_sources'][subject['id']] = subject['prompt']
            if timeline_changed:
                owner.refresh_segment_prompt_box(owner.timeline.currentRow())
                owner.refresh_text_segment_previews()
            if references_changed:owner.minimax_panel.refresh_references()
            owner.mark_dirty(); self.refresh()
            state['reference_changes'] = []
            state['references_need_reassessment'] = False
            self.update_reference_flag()
            self.status.setText(f'References reassessed · {len(updates)} fields updated.' if updates else 'References are consistent; prompt unchanged.')
        owner.start_ai_worker(reassess_references,
            (document, roster, changes, *credentials, owner.settings.value('api_timeout', 400, int)),
            'Reassessing reference usage with minimal prompt edits…', finished, review_text=False)
