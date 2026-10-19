"""All MiniMax prompt-writing paths request an explicit role-aware first-shot timecode."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from ltx_prompt_director import ai, segment_plan
from ltx_prompt_director.models import Segment
from ltx_prompt_director.prompt_templates import (
    MINIMAX_FIRST_SHOT, MINIMAX_SHOT_TIMING, WORKSPACE_PROMPTS,
    render_workspace_prompt,
)


@pytest.mark.parametrize('folder', ['workspace_templates', 'engine_templates'])
def test_stock_minimax_templates_never_request_an_untimed_opening(folder):
    root = Path(ai.__file__).parent / folder
    for path in root.glob('minimax*.json'):
        for kind, source in json.loads(path.read_text())['generation_prompts'].items():
            assert MINIMAX_FIRST_SHOT in source, (path, kind)
            assert 'Do not add a timestamp to the first shot.' not in source
            assert 'with no timestamp on that label' not in source
            assert 'has no timestamp' not in source
            assert "Never force an END frame's timecode to zero" in source


@pytest.mark.parametrize('kind', ['generate', 'refine'])
@pytest.mark.parametrize('engine', ['minimax_frames', 'minimax_references', 'minimax_prompt_refinement'])
def test_saved_legacy_templates_get_runtime_rule_without_mutating_saved_content(engine, kind):
    old = ('Do not add a timestamp to the first shot. '
           'Use [Shot 1] for the actual opening shot, with no timestamp on that label.\n'
           'Keep my custom production rules.\n$current_prompt')
    configured = {kind: old}
    token = WORKSPACE_PROMPTS.set(configured)
    try:
        rules = render_workspace_prompt(engine, kind, {'current_prompt': '[Shot 1] My existing scene.'})
    finally:
        WORKSPACE_PROMPTS.reset(token)
    assert configured == {kind: old}
    assert 'Keep my custom production rules.' in rules
    assert '[Shot 1] My existing scene.' in rules
    assert MINIMAX_FIRST_SHOT in rules
    assert MINIMAX_SHOT_TIMING in rules
    assert 'Do not add a timestamp to the first shot.' not in rules
    assert 'with no timestamp on that label' not in rules


@pytest.mark.parametrize('operation', [ai.build_minimax_h3_prompt, ai.build_minimax_h3_reference_prompt,
                                       ai.refine_minimax_h3_prompt, ai.refine_minimax_h3_reference_prompt])
@pytest.mark.parametrize('role, timestamp', [('start', '00:00.00'), ('end', '00:03.00')])
def test_provider_receives_role_aware_rule_in_generation_and_refinement(operation, role, timestamp):
    # The first timestamp follows the same frame-role boundary as local retiming.
    segments = [Segment('Ending frame', '', '', 'image', role, 'Approach the frame.', 3)]
    args = (segments, 'gemini', 'model', 'key', '', '', False, False, False)
    if operation.__name__.startswith('refine'):
        args += ('[Shot 1] Existing untimed scene.', 'Improve motion')
    output = f'[Shot 1] At {timestamp}, Approach the frame.'
    with patch.object(ai, '_provider_raw', return_value=json.dumps({'prompt': output})) as provider:
        assert operation(*args) == output
    rules = provider.call_args.args[4]
    assert MINIMAX_SHOT_TIMING in rules
    assert 'generation AND refinement' in rules
    assert "START uses the segment's start time; END" in rules
    assert "uses the segment's end time" in rules
    assert "Never force an END frame's timecode to zero" in rules
    assert segments[0].duration == 3 and segments[0].role == role


@pytest.mark.parametrize('timestamp', ['00:00.00', '00:03.00'])
def test_prompt_only_refinement_preserves_existing_first_shot_time(timestamp):
    output = f'[Shot 1] At {timestamp}, Walk.'
    with patch.object(ai, '_provider_raw', return_value=json.dumps({'prompt': output})) as provider:
        assert ai.refine_minimax_text_prompt('gemini', 'model', 'key', output + '\n/refine Improve motion') == output
    assert MINIMAX_SHOT_TIMING in provider.call_args.args[4]
    assert 'preserve an existing first-shot timecode' in provider.call_args.args[4]


@pytest.mark.parametrize('mode', ['frames', 'references'])
@pytest.mark.parametrize('refine', [False, True])
def test_combined_generation_and_refinement_include_zero_start_rule(mode, refine):
    segments = [Segment('Walk', '', '', 'text', 'text', 'Walk.', 3)]
    row = segments[0].to_dict()
    response = {'prompt': '[Shot 1] At 00:00.00, Walk.', 'segments': [row]}
    with patch.object(ai, '_structured_raw', return_value=json.dumps(response)) as provider:
        segment_plan.generate_combined(
            segments, 'openai', 'gpt-6', 'key', '', '', False, False, False,
            '[Shot 1] Walk.', '', 400, [], 'minimax_' + mode, mode, refine)
    assert MINIMAX_SHOT_TIMING in provider.call_args.args[4]


def test_reverse_sync_requires_explicit_role_aware_time_without_editing_source_segments():
    segments = [Segment('Walk', '', '', 'text', 'text', 'Walk.', 3)]
    before = [s.to_dict() for s in segments]
    with patch.object(ai, '_structured_raw', return_value='{"prompt":"[Shot 1] At 00:00.00, Walk."}') as provider:
        segment_plan.sync_combined_from_timeline(
            segments, 'gemini', 'model', 'key', '[Shot 1] Walk.', 400, [], 'minimax_frames', 'frames')
    assert MINIMAX_SHOT_TIMING in provider.call_args.args[4]
    assert [s.to_dict() for s in segments] == before


@pytest.mark.parametrize('engine', ['ltx', 'generic'])
def test_other_engines_keep_their_existing_timing_format(engine):
    rules = render_workspace_prompt(engine, 'generate', {})
    assert MINIMAX_SHOT_TIMING not in rules
