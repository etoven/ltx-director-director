"""One-time, local migration of saved production prose into template fields."""
import copy
import re
from .output_templates import root_fields, layout_for

VERSION = 1
PROJECT_VERSION = 11
LABELS = ('subject_definitions', 'summary', 'retention_analysis', 'detailed_description',
          'overall_soundscape', 'non_diegetic_music', 'integrated_multimodal_description')
_LABEL = '|'.join(re.escape(name).replace('_', r'[_ -]+') for name in LABELS)
HEADERS = re.compile(r'^[ \t]*(?:\#{1,6}[ \t]+)?(?:\*\*)?\[?(' + _LABEL +
    r')\]?[ \t]*(?:\*\*)?[ \t]*(?::[ \t]*(?:\*\*)?[ \t]*(.*))?[ \t]*$', re.I | re.M)
SHOTS = re.compile(r'\[(?:Shot|Segment)\s+(\d+)\][ \t]*(?:(?:At[ \t]+)?\d{1,2}:\d{2}(?:\.\d+)?'
    r'(?:[ \t]*[–-][ \t]*\d{1,2}:\d{2}(?:\.\d+)?)?[ \t,.:–-]*)?', re.I)
SUBJECTS = re.compile(r'^[ \t]*(?:[-*][ \t]+)?<((?:Subject|Video|Picture|Image|Audio)[ \t]+\d+)>[^\S\n]*', re.I | re.M)


def sections(text):
    matches = list(HEADERS.finditer(text)); result = {}
    for i, match in enumerate(matches):
        name = re.sub(r'[_ -]+', '_', match[1].lower())
        end = matches[i+1].start() if i+1 < len(matches) else len(text)
        body = '\n'.join(value for value in ((match[2] or '').strip(), text[match.end():end].strip()) if value)
        result[name] = '\n\n'.join(value for value in (result.get(name, ''), body) if value)
    return result, text[:matches[0].start()].strip() if matches else ''


def migrate_state(text, frames, references, definition, previous=None):
    old = copy.deepcopy(previous or {})
    original = old.get('original_draft') or text
    parts, preamble = sections(original)
    if not parts:
        return old or None
    state = old
    fields = state.setdefault('fields', {})
    available = root_fields(layout_for(definition))
    for heading, candidates in [('overall_soundscape', ('sound','overall_soundscape')),
                                ('non_diegetic_music', ('music','non_diegetic_music')),
                                ('retention_analysis', ('retention','retention_analysis'))]:
        key = next((name for name in candidates if name in available), candidates[0])
        if not fields.get(key) and heading in parts:fields[key] = parts[heading]
    base_h3 = definition.get('engine') == 'minimax_frames'
    direction = parts.get('summary', '' if base_h3 else parts.get('integrated_multimodal_description', ''))
    if not state.get('global') or HEADERS.search(state['global']):
        state['global'] = '\n\n'.join(value for value in (preamble, direction) if value)
    detail = parts.get('integrated_multimodal_description' if base_h3 else 'detailed_description', '')
    owns_shot_labels = '[Shot ${index}]' not in layout_for(definition)
    prompts = {frame['id']: '' for frame in frames}
    matches = list(SHOTS.finditer(detail))
    if matches and frames:
        introduction = detail[:matches[0].start()].strip()
        for i, match in enumerate(matches):
            end = matches[i+1].start() if i+1 < len(matches) else len(detail)
            index = min(max(0, int(match[1])-1), len(frames)-1)
            body = detail[match.start() if owns_shot_labels else match.end():end].strip()
            if i == 0 and introduction:body = introduction + '\n\n' + body
            key = frames[index]['id'];prompts[key] = '\n\n'.join(x for x in (prompts[key], body) if x)
    elif detail and frames:
        prompts[frames[0]['id']] = detail
    if 'detailed_description' in available:fields.setdefault('detailed_description', detail)
    previous_prompts = state.get('prompts', {})
    for frame in frames:
        key = frame['id']
        # Already-authored template edits take priority over imported prose.
        if previous_prompts.get(key) and previous_prompts[key] != frame.get('prompt', ''):
            prompts[key] = previous_prompts[key]
    state['prompts'] = prompts
    state['sources'] = {frame['id']: frame.get('prompt', '') for frame in frames}
    definitions = parts.get('subject_definitions', '')
    custom = state.setdefault('custom_subjects', [])
    identities = {item['id'] for item in custom}
    markers = list(SUBJECTS.finditer(definitions))
    records = []
    for i, match in enumerate(markers):
        end = markers[i+1].start() if i+1 < len(markers) else len(definitions)
        body = definitions[match.start():end].strip()
        if i == 0 and definitions[:match.start()].strip():body = definitions[:match.start()].strip()+'\n\n'+body
        records.append(dict(id='subject:legacy:'+re.sub(r'\s+', '-', match[1].lower()),
                            name=match[1], role='identity', prompt=body))
    if definitions and not markers:
        records.append(dict(id='subject:legacy:definitions',name='Saved subject definitions',role='identity',prompt=definitions))
    for record in records:
        if record['id'] not in identities:custom.append(record);identities.add(record['id'])
    state['subject_order'] = list(dict.fromkeys([record['id'] for record in records]+state.get('subject_order', [])))
    counters = {}; covered = set(state.get('covered_references', []))
    for index, ref in enumerate(references):
        if not ref:continue
        kind = ref.get('kind', 'image'); label = 'audio' if kind == 'audio' else 'video' if kind == 'video' else 'picture'
        counters[label] = counters.get(label, 0)+1
        if re.search(r'<'+label+r'\s+'+str(counters[label])+r'>', definitions, re.I):covered.add('reference:'+str(index))
    state['covered_references'] = sorted(covered)
    state['original_draft'] = original
    state['last_render'] = text
    state['migration_source_pending'] = True
    return state


def migrate_project(payload, definitions):
    if payload.get('promptTemplateVersion', 0) >= VERSION:return payload
    payload = copy.deepcopy(payload)
    states = payload.setdefault('promptTemplateStates', {})
    aliases = {name: 'minimax_official_skill_base' for name in ('frames','minimax_frames','base_guide','minimax_base_guide')}
    aliases.update({name: 'minimax_official_skill_reference' for name in ('references','minimax_references','full_reference_guide','minimax_full_reference_guide')})
    active_group = int(payload.get('activeRenderGroup', 0))
    contexts = {int(key): value['context'] for key, value in payload.get('renderGroupOptions', {}).items()
                if isinstance(value, dict) and isinstance(value.get('context'), dict)}
    contexts[active_group] = payload
    for group, context in contexts.items():
        minimax = context.get('minimaxH3', {})
        if not isinstance(minimax, dict):minimax = {'prompt': minimax} if isinstance(minimax, str) else {}
        active = context.get('projectType', '')
        candidates = {key: value.get('prompt', '') for key, value in minimax.get('drafts', {}).items() if isinstance(value,dict)}
        candidates[active] = minimax.get('prompt', '')
        frames = [frame for frame in payload.get('frames', [])
                  if int(frame.get('render_group', frame.get('renderGroup', 0))) == group]
        for key, text in candidates.items():
            workspace = key if key in definitions else aliases.get(key, key)
            definition = definitions.get(workspace) or (context.get('workspaceDefinition') if key == active else None)
            if not isinstance(definition, dict) or definition.get('prompt_mode') != 'unified' or not text:continue
            state_key = workspace+':'+str(group)
            migrated = migrate_state(text, frames, minimax.get('referenceImages', []), definition,
                                     states.get(state_key, states.get(key+':'+str(group))))
            if migrated:states[state_key] = migrated
    payload['promptTemplateVersion'] = VERSION
    payload['projectVersion'] = PROJECT_VERSION
    return payload
