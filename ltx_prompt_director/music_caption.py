"""Progressive disclosure of MiniMax's bundled, text-only caption skill."""
import json
import re
from importlib.resources import files
from . import response_schemas as schemas


def rewrite(prompt, lyrics, credentials, timeout):
    from .ai import _structured_raw
    provider, model, key = credentials
    root = files('ltx_prompt_director').joinpath('assets/music-caption-rewriter')
    skill = root.joinpath('SKILL.md').read_text(encoding='utf-8')
    router = root.joinpath('references/genre-router.md').read_text(encoding='utf-8')
    inputs = '\nCaption:\n' + prompt + '\nLyrics (separate input; never reproduce lyric text in the caption):\n' + lyrics
    def ask(rules, schema):
        raw = _structured_raw([], provider, model, key, rules, timeout, schema=schema)
        with schemas.using(schema):
            schemas.validate(raw)
        return json.loads(raw)
    families = sorted(set(re.findall(r'\((index-[a-z0-9-]+\.md)\)', router)))
    route_schema = schemas.named('music_style_routes', schemas.record(families={
        'type': 'array', 'items': {'type': 'string', 'enum': families}, 'minItems': 1, 'maxItems': 2}))
    route = ask('Build a private music brief and choose one primary family, plus one secondary only for explicit fusion. '
                'Follow the router; return only the selected index filenames.\n' + router + inputs, route_schema)
    indexes = '\n\n'.join(root.joinpath('references/' + name).read_text(encoding='utf-8') for name in route['families'])
    paths = sorted(set(re.findall(r'`(templates/[a-zA-Z0-9_-]+\.txt)`', indexes)))
    choose_schema = schemas.named('music_style_references', schemas.record(templates={
        'type': 'array', 'items': {'type': 'string', 'enum': paths}, 'minItems': 1, 'maxItems': 3}))
    selected = ask('Choose up to three compatible references with distinct Foundation, Modifier and Arrangement roles. '
                   'Use fewer for simple requests. Rank genre, exclusions, groove, vocals and instruments before mood. '
                   'Never choose a reference that contradicts an explicit requirement. Return only selected template paths.\n'
                   + indexes + inputs, choose_schema)
    references = '\n\n'.join(root.joinpath(path).read_text(encoding='utf-8') for path in selected['templates'])
    result = ask(skill + '\n\nThe routing and reference selection steps have already been completed. '
                 'Use only these selected references. Synthesize a new caption; do not copy their sentences or inherit '
                 'unsupported facts. Complete the private timeline plan and validation, then return the final caption '
                 'in the prompt variable of the supplied JSON schema.\nSELECTED REFERENCES:\n' + references + inputs,
                 schemas.PROMPT)['prompt']
    if not all(re.search(r'(?im)^\s*(?:#{1,6}\s*)?' + title + r'\s*:?\s*$', result)
               for title in ('Global Metadata', 'Vocal Details', 'Arrangement')):
        raise ValueError('Music refinement must contain Global Metadata, Vocal Details and Arrangement.')
    return result
