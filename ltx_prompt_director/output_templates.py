"""One data-only document defines the entire production layout and AI variables."""
from __future__ import annotations
import copy
import json
import re
from contextvars import ContextVar
from string import Template
from . import response_schemas as schemas
from .prompt_timecodes import format_timecode

ACTIVE_OUTPUT = ContextVar('production_output_template', default=None)
BLOCKS = ('global_prompt', 'prompt', 'index', 'name', 'start', 'end', 'duration', 'role')
META = {'index', 'name', 'start', 'end', 'duration', 'role'}
LOOP = re.compile(r'\{\{([#/])(subjects|segments)\}\}')
MARKER = re.compile(r'\{\{(?:(#)(subjects|segments|section)(?:\s+("(?:\\.|[^"\\])*"))?|(/)(subjects|segments|section))\}\}')


def parse_template(source):
    """A portable tree of text, named sections and model-owned repeat groups."""
    cursor = 0
    def read(closing=None):
        nonlocal cursor
        result = []
        while cursor < len(source):
            match = MARKER.search(source, cursor)
            if match is None:
                result.append(dict(type='text', text=source[cursor:])); cursor = len(source)
                break
            if match.start() > cursor:
                result.append(dict(type='text', text=source[cursor:match.start()]))
            cursor = match.end()
            if match[4]:
                if match[5] != closing:
                    raise ValueError('A template container closes outside its parent.')
                return result
            kind = match[2]
            heading = json.loads(match[3]) if match[3] else ''
            result.append(dict(type=kind, heading=heading, children=read(kind)))
        if closing is not None:
            raise ValueError('Close the ' + closing + ' container before saving.')
        return result
    return read()


def serialize_template(blocks):
    return ''.join(block.get('text', '') if block['type'] == 'text' else
                   '{{#' + block['type'] + (' ' + json.dumps(block.get('heading', ''), ensure_ascii=False) if block['type'] == 'section' else '')
                   + '}}' + serialize_template(block.get('children', [])) + '{{/' + block['type'] + '}}'
                   for block in blocks)


def flatten_sections(blocks, repeat=None):
    result = []
    for block in blocks:
        kind = block['type']
        if kind == 'text':
            result.append(block['text'])
        elif kind == 'section':
            heading = block.get('heading', '')
            label = heading.replace('$', '$$')
            result.append(((label if label.endswith(':') else '[' + label + ']') + '\n' if heading else '')
                          + flatten_sections(block['children'], repeat) + '\n\n')
        else:
            if repeat is not None:
                raise ValueError('Repeat groups belong inside sections, not inside another repeat group.')
            result.append('{{#' + kind + '}}' + flatten_sections(block['children'], kind) + '{{/' + kind + '}}')
    return ''.join(result)


def default_layout(engine='ltx'):
    section = lambda heading, children: dict(type='section', heading=heading, children=children)
    text = lambda value: dict(type='text', text=value)
    subjects = dict(type='subjects', children=[text('[Subject ${index}: ${name}]\n${prompt}\n\n')])
    shots = dict(type='segments', children=[text('[Shot ${index}] At ${start} – ${end}\n${prompt}\n\n')])
    if engine == 'minimax_references':
        return serialize_template([section('subject_definitions:', [subjects]), section('summary:', [text('${global_prompt}')]),
            section('retention_analysis:', [text('${retention}')]), section('detailed_description:', [shots]),
            section('overall_soundscape:', [text('${sound}')]), section('non_diegetic_music:', [text('${music}')])])
    if engine.startswith('minimax'):
        return serialize_template([section('integrated_multimodal_description:', [text('${global_prompt}\n\n'), subjects, shots]),
            section('overall_soundscape:', [text('${sound}')]), section('non_diegetic_music:', [text('${music}')])])
    return serialize_template([section('GLOBAL', [text('${global_prompt}')]), section('SUBJECTS', [subjects]),
        section('TIMELINE', [dict(type='segments', children=[text('[Segment ${index}] ${start} – ${end}\n${prompt}\n\n')])])])


def upgrade_layout(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and set(value) == {'global_prompt', 'subject', 'segment', 'separator'}:
        separator = value['separator']
        return (value['global_prompt'].replace('${prompt}', '${global_prompt}') + separator
                + '{{#subjects}}' + value['subject'] + separator + '{{/subjects}}'
                + '{{#segments}}' + value['segment'] + separator + '{{/segments}}')
    raise ValueError('An output template must be one text document.')


def nodes(source):
    """Flatten structural sections while retaining the model's repeat scopes."""
    source = flatten_sections(parse_template(source))
    result = []; cursor = 0; seen = set()
    for match in LOOP.finditer(source):
        if match.start() < cursor:
            continue
        if match[1] != '#':
            raise ValueError('A repeat block closes before it opens.')
        kind = match[2]
        if kind in seen:
            raise ValueError('Use one ' + kind + ' repeat block and move it to the desired position.')
        seen.add(kind)
        close = '{{/' + kind + '}}'; end = source.find(close, match.end())
        if end < 0:
            raise ValueError('Close the ' + kind + ' block with ' + close + '.')
        body = source[match.end():end]
        if LOOP.search(body):
            raise ValueError('Place subject and segment repeat blocks beside one another, without nesting.')
        result.append(('text', source[cursor:match.start()]))
        result.append((kind, body)); cursor = end + len(close)
    result.append(('text', source[cursor:]))
    return result


def names(source):
    matches = list(Template.pattern.finditer(source))
    if any(m.group('invalid') is not None for m in matches):
        raise ValueError('Use ${field_name} for an editable block and $$ for a literal dollar sign.')
    return [m.group('named') or m.group('braced') for m in matches if m.group('named') or m.group('braced')]


def validate_layout(value):
    source = upgrade_layout(value)
    if len(source) > 40000:
        raise ValueError('Keep the output template below 40,000 characters.')
    fields = []
    for kind, body in nodes(source):
        variables = names(body)
        if kind == 'text':
            if any(v in META or v == 'prompt' for v in variables):
                raise ValueError('Use ${global_prompt} or a named field outside repeat blocks; put ${prompt} inside a repeat block.')
            fields += variables
        else:
            if variables.count('prompt') != 1 or any(v not in META | {'prompt'} for v in variables):
                raise ValueError('Each repeat block needs one ${prompt}; use index, name, start, end, duration and role for its metadata.')
    if len(fields) != len(set(fields)):
        raise ValueError('Each named editable field may appear once in the document.')
    if not fields and not any(kind != 'text' for kind, _ in nodes(source)):
        raise ValueError('Add an editable field or a timeline / subject repeat block.')
    return source


def layout_for(definition):
    return validate_layout(definition.get('output_template', default_layout(definition.get('engine', 'ltx'))))


def root_fields(layout):
    return [name for kind, body in nodes(layout) if kind == 'text' for name in names(body)]


def stamp(seconds):
    return format_timecode(seconds, '00:00.00')


def document_parts(layout, global_prompt, segments, subjects=(), variables=None):
    """Ordered literal/field spans used by both output rendering and the native UI."""
    values = {**(variables or {}), 'global_prompt': global_prompt}
    parts = []
    def expand(body, metadata, key, kind, record=None):
        cursor = 0
        for match in Template.pattern.finditer(body):
            if match.start() > cursor:
                parts.append(dict(kind='literal', text=body[cursor:match.start()]))
            name = match.group('named') or match.group('braced')
            if match.group('escaped'):
                parts.append(dict(kind='literal', text='$'))
            elif name in metadata:
                parts.append(dict(kind='literal', text=str(metadata[name])))
            else:
                field_key = key if kind != 'text' else ('global' if name == 'global_prompt' else 'field:' + name)
                text = record.get('prompt', '') if record else str(values.get(name, ''))
                parts.append(dict(kind='field', key=field_key, name=name, scope=kind, text=text, record=record,
                                  timed=kind == 'segments' and any(n in ('start', 'end') for n in names(body))))
            cursor = match.end()
        if cursor < len(body):
            parts.append(dict(kind='literal', text=body[cursor:]))
    for kind, body in nodes(layout):
        if kind == 'text':
            expand(body, {}, '', kind)
            continue
        start = 0.0
        records = subjects if kind == 'subjects' else segments
        for index, item in enumerate(records, 1):
            record = item if isinstance(item, dict) else item.to_dict()
            duration = float(record.get('duration', 0))
            metadata = dict(index=index, name=record.get('name', ''), start=stamp(start), end=stamp(start + duration),
                            duration=f'{duration:g}', role=record.get('role', ''))
            expand(body, metadata, record['id'], kind, record)
            start += duration
    return parts


def render(layout, global_prompt, segments, subjects=(), variables=None):
    return ''.join(part['text'] for part in document_parts(layout, global_prompt, segments, subjects, variables))


def split_legacy(text, segments):
    markers = list(re.finditer(r'(?im)^\[(?:Shot|Segment)\s+\d+\][^\n]*\n?', text))
    if len(markers) != len(segments) or not markers:
        return text, {}
    prompts = {}
    for index, marker in enumerate(markers):
        stop = markers[index + 1].start() if index + 1 < len(markers) else len(text)
        inline = re.match(r'^\[(?:Shot|Segment)\s+\d+\]\s+At\s+[\d:.]+,\s*(.*)', marker.group().rstrip('\n'), re.I)
        prompts[segments[index].id] = ((inline.group(1) + '\n') if inline else '') + text[marker.end():stop].strip('\n')
    return text[:markers[0].start()].strip('\n'), prompts


def request_context(definition, segments, subjects):
    return dict(layout=layout_for(definition), segments=[s.to_dict() for s in segments], subjects=copy.deepcopy(subjects))


def template_contract(context):
    variables = schemas.record(**{name: schemas.text('Complete text for the ${' + name + '} block, without template headings.', empty=True)
                                 for name in root_fields(context['layout'])})
    return schemas.named('production_template_variables', schemas.record(
        variables=variables,
        subjects={'type': 'array', 'items': schemas.record(id=schemas.text('Exact input subject ID.'),
                  prompt=schemas.text('Subject description; no template headings.', empty=True)),
                  'minItems': len(context['subjects']), 'maxItems': len(context['subjects'])},
        segments={'type': 'array', 'items': schemas.record(id=schemas.text('Exact input segment UUID, in timeline order.'),
                  prompt=schemas.text('Complete section body; no template headings or timecodes.', empty=True)),
                  'minItems': len(context['segments']), 'maxItems': len(context['segments'])}))


def render_response(raw, context):
    value = json.loads(raw); segments = copy.deepcopy(context['segments']); subjects = copy.deepcopy(context['subjects'])
    for key, records in [('segments', segments), ('subjects', subjects)]:
        if [item['id'] for item in value[key]] != [item['id'] for item in records]:
            raise ValueError('Template variables must retain every input ID in its original order.')
        for record, output in zip(records, value[key]):
            record['prompt'] = output['prompt']
    variables = value['variables']
    return json.dumps({'prompt': render(context['layout'], variables.get('global_prompt', ''), segments, subjects, variables)}, ensure_ascii=False)


def parse_rendered(text, layout, segments, subjects):
    parts = document_parts(layout, '', segments, subjects)
    fields = [part for part in parts if part['kind'] == 'field']
    pattern = ''.join('(.*?)' if part['kind'] == 'field' else re.escape(part['text']) for part in parts)
    match = re.fullmatch(pattern, text, re.S)
    if not match:
        return None
    return {part['key']: value for part, value in zip(fields, match.groups())}
