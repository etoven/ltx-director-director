"""A section-restricted production editor with local timeline synchronization."""
from __future__ import annotations
import copy
import math
import re
import threading
from uuid import uuid4

from PySide6.QtCore import QTimer, QSignalBlocker
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QMenu, QSizePolicy)

from .diff_review import ParagraphDiffReview, ParagraphChange, paragraph_changes
from .continuous_prompt_editor import ContinuousPromptEditor, SectionText
from .prompt_tags import install_prompt_tags
from .spellcheck import install_spellcheck
from .models import Segment
from .output_templates import layout_for, render, split_legacy, parse_rendered, stamp, document_parts


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


class PromptSnapshot(dict):
    def __init__(self, values, context):
        super().__init__(values)
        self.context = context


class StructuredPromptPanel(QWidget):
    def __init__(self, owner):
        super().__init__(); self.owner = owner; self.updating = False; self.rows = {}; self.signature = None
        self.selected_id = None
        self.reviews = {}
        self.reveal_pending = False
        self.timer = QTimer(self); self.timer.setSingleShot(True); self.timer.setInterval(0)
        self.timer.timeout.connect(self.refresh)
        self.reveal_timer = QTimer(self); self.reveal_timer.setSingleShot(True); self.reveal_timer.setInterval(40)
        self.reveal_timer.timeout.connect(self.reveal_selected)
        self.definition_timer = QTimer(self); self.definition_timer.setSingleShot(True); self.definition_timer.setInterval(700)
        self.definition_timer.timeout.connect(self.drain_definition_queue)
        self.definition_job = None; self.definition_result = None; self.reassess_after_definitions = None
        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0)
        controls = QHBoxLayout(); outer.addLayout(controls)
        self.add = QPushButton('+'); self.add.setFixedWidth(32); self.add.setToolTip('Add a timeline section, subject, image, video or audio reference')
        menu = QMenu(self.add); menu.addAction('Timeline section', self.add_segment); menu.addAction('Subject definition', self.add_subject)
        for kind in ('image', 'video', 'audio'):
            menu.addAction(kind.title() + ' reference…', lambda checked=False, kind=kind: self.add_reference(kind))
        self.add.setMenu(menu); controls.addWidget(self.add)
        self.section_label = QLabel('Unified prompt'); controls.addWidget(self.section_label, 1)
        self.section_label.setMinimumWidth(0)
        self.section_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.time_fields = []
        for end, label in ((False, 'Start'), (True, 'End')):
            field = QLineEdit(); field.setMaximumWidth(106); field.setAccessibleName(label + ' time')
            field.setToolTip(label + ' time · edit the selected timeline section')
            field.textEdited.connect(lambda _, is_end=end: self.edit_time(self.active_key, is_end))
            controls.addWidget(field); self.time_fields.append(field)
        self.remove = QPushButton('×'); self.remove.setFixedWidth(26)
        self.remove.setAccessibleName('Remove selected section')
        self.remove.clicked.connect(lambda: self.remove_part(self.active_key))
        controls.addWidget(self.remove)
        self.active_key = None
        self.editor = ContinuousPromptEditor(self)
        self.editor.writable = lambda: not owner.ai_busy
        self.review = ParagraphDiffReview(self.editor)
        self.editor.review_applying = lambda: self.review._applying
        self.editor.textChanged.connect(self.document_edited)
        self.editor.cursorPositionChanged.connect(self.cursor_section_changed)
        install_spellcheck(self.editor); install_prompt_tags(self.editor)
        outer.addWidget(self.editor, 1)
        self.scroll = self.editor
        self.status = QLabel(); self.status.setWordWrap(True); outer.addWidget(self.status)
        self.specs = {}; self.document_spans = []
        self.editor.viewport().installEventFilter(self)
        owner.unified_prompt.textChanged.connect(self.schedule)
        owner.global_prompt.textChanged.connect(self.schedule)
        owner.intent.textChanged.connect(self.schedule)
        owner.timeline.currentRowChanged.connect(self.schedule)
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

    def refresh(self, *, refinement_baseline=None):
        owner = self.owner
        if self.updating or owner._loading:
            return
        if not owner.unified_workspace or owner.prompt_only_workspace or owner.workspace_engine == 'shot_sequence':
            self.finish_reviews()
            self.reveal_timer.stop()
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
            if refinement_baseline is not None:
                for segment in segments:
                    previous = refinement_baseline.get(segment.id)
                    revised = state['prompts'].get(segment.id)
                    if previous is not None and revised is not None and revised != previous:
                        segment.prompt = revised
                        state['sources'][segment.id] = revised
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
                         tuple(part['key'] for part in specs))
            if self.review.editor is not None:
                if structure != self.signature or self.review.source_document.toPlainText() != text:
                    self.finish_reviews()
            self.specs = {spec['key']: spec for spec in specs}
            self.document_spans = spans
            if self.review.editor is None:
                self.editor.set_parts(spans)
            self.signature = structure
            self.rows = {key: self.rows.get(key, [SectionText(self, key), *self.time_fields]) for key in self.specs}
            self.reviews = {row[0]: self.review for row in self.rows.values()}
            self.add.setEnabled(not owner.ai_busy)
            if self.review.editor is None:
                self.editor.setReadOnly(owner.ai_busy)
            selected = owner.current_segment()
            selected_id = selected.id if selected else None
            if selected_id != self.selected_id or self.reveal_pending:
                if selected_id in self.rows:
                    self.active_key = selected_id
                    self.reveal_timer.start()
            self.reveal_pending = False
            self.selected_id = selected_id
            if self.active_key not in self.specs:
                self.active_key = next(iter(self.specs), None)
            self.update_section_controls()
            if self.review.editor is None:
                self.editor.select_section(self.active_key)
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
            self.active_key = segment.id
            self.editor.select_section(segment.id, reveal=True)
            self.update_section_controls()

    def cursor_section_changed(self):
        if self.updating or self.review.editor is not None:
            return
        section = self.editor.section_for_cursor()
        if section:
            self.active_key = section['key']
            self.update_section_controls()
            self.editor.select_section(self.active_key)

    def update_section_controls(self):
        spec = self.specs.get(self.active_key, {})
        title = ((spec.get('record') or {}).get('name') or spec.get('heading', '').strip()
                 or spec.get('name', 'Unified prompt').replace('_', ' ').title())
        self.section_label.setText(title)
        self.section_label.setToolTip(title)
        segment = next((s for s in self.segments() if s.id == self.active_key), None)
        timed = segment is not None and spec.get('timed', False)
        start = 0
        if segment:
            start = sum(s.duration for s in self.segments()[:self.segments().index(segment)])
        busy = self.owner.ai_busy or self.review.editor is not None
        for field, value in zip(self.time_fields, (stamp(start), stamp(start + (segment.duration if segment else 0)))):
            field.setVisible(timed)
            field.setReadOnly(busy)
            if not field.hasFocus(): field.setText(value)
        removable = bool(segment or str(self.active_key).startswith(('reference:', 'subject:')))
        self.remove.setVisible(removable); self.remove.setEnabled(not busy)

    def review_snapshot(self):
        return PromptSnapshot({key: self.editor.field_text(key) for key in self.specs}, self.review_context())

    def review_context(self):
        return (self.owner.workspace_revision, self.owner.current_project_id, self.owner.project_type,
                getattr(self.owner, '_active_render_group', 0), tuple(self.specs))

    def review_refinement(self, before):
        if isinstance(before, PromptSnapshot) and before.context != self.review_context():
            return
        self.finish_reviews()
        parts = []
        for span in self.document_spans:
            if span['kind'] == 'literal':
                parts.append(ParagraphChange(span['text'], span['text'], 'equal'))
            else:
                parts.extend(paragraph_changes(before.get(span['key'], span['text']), span['text']))
        old = ''.join(p.old for p in parts)
        self.review.begin(self.editor, old, self.editor.toPlainText(), changes=parts)
        self.update_section_controls()

    def finish_reviews(self):
        self.review.finish()

    def document_edited(self):
        if self.updating:
            return
        if self.review._applying:
            parsed = parse_rendered(self.editor.toPlainText(), layout_for(self.owner.workspace_definition), self.segments(), self.subjects())
            if parsed is None:
                return
            values = parsed
        elif self.review.editor is not None:
            return
        else:
            values = {key: self.editor.field_text(key) for key in self.specs}
        changed = False
        for key, value in values.items():
            if key not in self.specs:
                continue
            state = self.state()
            previous = (state.get('global', '') if key == 'global' else
                        state.get('fields', {}).get(key.removeprefix('field:'), '') if key.startswith('field:') else
                        state.get('subjects', {}).get(key, '') if key.startswith(('reference:', 'subject:')) else
                        state.get('prompts', {}).get(key, ''))
            if value != previous:
                self.edit(key, value)
                changed = True
        if changed and self.review._applying:
            # Reindex the authoritative document before its diff projection returns.
            state = self.state()
            records = [{**s.to_dict(), 'prompt': state['prompts'].get(s.id, s.prompt)} for s in self.segments()]
            spans = document_parts(layout_for(self.owner.workspace_definition), state.get('global', ''), records, self.subjects(), state.get('fields', {}))
            self.editor.set_parts(spans)
            self.document_spans = spans
        if changed:
            text = self.editor._text
            self.state()['last_render'] = text
            with QSignalBlocker(self.owner.unified_prompt):
                self.owner.unified_prompt.setPlainText(text)
            self.owner.minimax_prompt_text = text

    def eventFilter(self, watched, event):
        from PySide6.QtCore import QEvent
        from .effects_library import MIME as EFFECT_MIME
        if watched is self.editor.viewport() and event.type() in (QEvent.Type.DragEnter, QEvent.Type.DragMove, QEvent.Type.Drop) and event.mimeData().hasFormat(EFFECT_MIME):
            section = self.editor.section_at(self.editor.cursorForPosition(event.position().toPoint()).position())
            if section is None or self.owner.ai_busy:
                event.ignore(); return True
            self.active_key = section['key']; self.update_section_controls()
            target = 'segment' if any(s.id == self.active_key for s in self.segments()) else 'unified'
            self.owner._effect_drop_targets[self.editor.viewport()] = target
            self.owner._effect_part_segments = getattr(self.owner, '_effect_part_segments', {})
            if target == 'segment':self.owner._effect_part_segments[self.editor.viewport()] = self.active_key
            else:self.owner._effect_part_segments.pop(self.editor.viewport(), None)
            return self.owner.eventFilter(watched, event)
        return super().eventFilter(watched, event)

    def sync_segment_edit(self, segment):
        state = self.state()
        if 'global' not in state:
            self.refresh()
        state.setdefault('prompts', {})[segment.id] = segment.prompt
        state.setdefault('sources', {})[segment.id] = segment.prompt
        if segment.id in self.rows:
            self.refresh()
        self.schedule()

    def edit(self, key, text=None):
        if self.updating or self.owner.ai_busy:
            return
        state = self.state()
        if text is None: text = self.editor.field_text(key)
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
