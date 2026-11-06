"""Unified definitions share the media inventory used by provider requests."""
import copy
import json
import re
from .minimax_reference import reference_inventory


def label_key(value):
    return re.sub(r'[^a-z0-9]', '', value.lower()).replace('picture', 'image')


def subjects(segments, references, state):
    custom = copy.deepcopy(state.get('custom_subjects', []))
    legacy = {label_key(item['name']): item for item in custom if item['id'].startswith('subject:legacy:')}
    bound = set(); records = []
    for item in reference_inventory(segments, references):
        if not item.get('label'):continue
        slot = item.get('reference_slot')
        source = references[slot] if slot is not None else segments[item['timeline_index']]
        key = f'reference:{slot}' if slot is not None else 'subject:timeline:' + source.id
        labels = [item['label']] + ([item['audio_label']] if item.get('audio_label') else [])
        for label in labels:
            saved = legacy.get(label_key(label))
            record = dict(saved) if saved else dict(id=key if label == item['label'] else key+':audio',
                name=label, role=item['role'], prompt=source.get('description' if label == item['label'] else 'audio_description') or source.get('notes', '')
                if isinstance(source, dict) else source.image_prompt)
            if saved:bound.add(saved['id'])
            record.update(reference_slot=slot, timeline_id=source.id if slot is None else None,
                          media_kind='audio' if label.startswith('Audio') else item['kind'], label=label,
                          notes=source.get('notes', '') if isinstance(source, dict) else source.prompt,
                          source_name=source.get('name', '') if isinstance(source, dict) else source.name,
                          source_path=source.get('path', '') if isinstance(source, dict) else source.media_path)
            if record['id'] not in state.get('excluded_subjects', []):records.append(record)
    records.extend(item for item in custom if item['id'] not in bound and item['id'] not in state.get('excluded_subjects', []))
    order = {key: index for index, key in enumerate(state.get('subject_order', []))}
    return sorted(records, key=lambda item: order.get(item['id'], len(order)))


def generate_definitions(segments, references, selected, provider, model, key, timeout):
    from . import ai, response_schemas as schemas
    # Media preparation and provider requests run entirely in the AI worker.
    selected_labels = {item['label'] for item in selected if item.get('label')}
    inputs = []
    for record in reference_inventory(segments, references):
        if record.get('label') in selected_labels:
            if 'reference_slot' in record:
                inputs.append(ai._mixed_reference_input(record, references[record['reference_slot']], provider))
            else:
                media = ai._minimax_h3_inputs([segments[record['timeline_index']]], provider)[0]
                if record['kind'] == 'video':media['video_number'] = int(record['label'].removeprefix('Video'))
                inputs.append({**media, **record, 'reference_mode_input': True})
        if record.get('audio_label') in selected_labels:
            audio = dict(record, kind='audio', label=record['audio_label'])
            inputs.append(ai._mixed_reference_input(audio, references[record['reference_slot']], provider))
    schema = schemas.named('reference_definitions', schemas.record(definitions=schemas.array(
        schemas.record(id=schemas.text('Exact requested definition ID'), prompt=schemas.text('Reference definition only')), len(selected))))
    rules = ('Write only the requested reference definitions. Preserve exact IDs. Describe what each supplied image, '
             'video or audio controls, respecting its role and the existing notes. For paired video/audio describe '
             'each modality separately. Do not invent unheard audio or claim visual access for audio-only inputs. '
             'When a provider cannot audition audio, use explicit supplied notes and say what remains unspecified. '
             'Local writing models receive text context only. Do not generate a sequence, headings, shots, summary, '
             'timing or music plan. Existing definitions may be regenerated using the requested media and notes.\n'
             + json.dumps(selected, ensure_ascii=False) + '\nReturn JSON matching:\n' + json.dumps(schema))
    raw = ai._structured_raw(inputs, provider, model, key, rules, timeout, schema=schema)
    with schemas.using(schema):schemas.validate(raw)
    result = json.loads(raw)['definitions']
    if len({row['id'] for row in result}) != len(selected) or {row['id'] for row in result} != {row['id'] for row in selected}:
        raise ai.AIResponseFormatError('Reference definitions did not preserve the requested IDs.')
    return {row['id']: row['prompt'] for row in result}


def reference_roster(records):
    return [{key: item[key] for key in ('id', 'label', 'name', 'media_kind', 'role', 'notes', 'prompt', 'source_name', 'source_path') if key in item}
            for item in records]



def reassess_references(document, roster, changes, provider, model, key, timeout):
    from . import ai, response_schemas as schemas
    schema = schemas.named('reference_reassessment', schemas.record(fields=schemas.record(**{
        name: schemas.text('Complete revised section; preserve unrelated wording verbatim. Empty if removal leaves no content.', empty=True)
        for name in document})))
    rules = ('Reassess reference usage throughout ALL supplied project prompt text. Return the complete revised text '
             'of every supplied field in fields. This includes timeline/segment prompts (timeline:*:prompt), '
             'image and artwork prompts, Global source direction (source:global), director instructions (source:intent), '
             'all Unified sections, and remaining reference notes/descriptions (media:*). Reconcile EACH surface; '
             'updating Unified alone is insufficient. Timeline and Unified prompts are independent drafts: edit '
             'each existing draft minimally instead of copying one over the other. Timing, segment order, media files '
             'and unrelated project data are outside this text-only operation and must never be altered. '
             'You decide which references and source-dependent wording '
             'need removal, addition or adjustment. CURRENT REFERENCES is authoritative; REFERENCE CHANGES records '
             'earlier labels and definitions, including removed or replaced sources. Reconcile reference usage '
             'throughout subject definitions, summary, retention, detailed shot prose, sound and music. '
             'Remove obsolete source attributions and dependencies on deleted media; incorporate current definitions '
             'and notes where relevant. Correct renumbered labels by source identity: never redirect a deleted '
             'reference to a different file that inherited its number. If history is unavailable, reconcile '
             'the existing draft against the current inventory. Make the smallest wording changes needed. '
             'Preserve all unrelated content, dialogue, language, speaker identity, actions, timing and explicitly '
             'requested sounds. Removing a voice file does not cancel spoken dialogue. Do not invent media or '
             'unheard audio. Use the supplied definitions and notes. Leave consistent sections exactly unchanged. '
             'Return revised fields directly, not text-match patches, snippets or an edit plan. A field may '
             'be empty when its content exists solely to describe a removed reference.\n'
             + json.dumps(dict(current_references=roster, reference_changes=changes, editable_fields=document), ensure_ascii=False)
             + '\nOUTPUT SCHEMA:\n' + json.dumps(schema))
    raw = ai._structured_raw([], provider, model, key, rules, timeout, schema=schema)
    return json.loads(raw)['fields']
