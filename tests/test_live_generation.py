"""Regression tests: real provider stream parsing, summary isolation and Qt panels."""
from __future__ import annotations

import json
import os
import unittest
from unittest.mock import Mock, patch
from concurrent.futures import ThreadPoolExecutor
import copy
from threading import Barrier

import pytest

from ltx_prompt_director import ai, generic_ai, response_schemas as schemas, segment_plan
from ltx_prompt_director.models import Segment
from ltx_prompt_director.segment_split import divide_segment_prompt
from ltx_prompt_director.timeline_timing import plan_sequence_timing
from ltx_prompt_director.ai_stream import STREAM_SINK


class StreamResponse:
    status_code = 200

    def __init__(self, events):
        self.events = events

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def close(self):
        pass

    def raise_for_status(self):
        pass

    def iter_lines(self, decode_unicode=True):
        for event in self.events:
            yield "data: " + json.dumps(event)


class ProviderStreams(unittest.TestCase):
    def test_gemini_never_accepts_a_token_limited_prompt_even_with_valid_json(self):
        candidate = {"content": {"parts": [{"text": '{"prompt":"Only the opening beat"}'}]},
                     "finishReason": "MAX_TOKENS"}
        with self.assertRaisesRegex(ai.AIResponseFormatError, "incomplete result was not accepted"):
            ai._gemini_response_text({"candidates": [candidate]})
        token = STREAM_SINK.set(lambda kind, text: None)
        try:
            with patch.object(ai.requests, "post", return_value=StreamResponse([
                    {"candidates": [{"content": {"parts": [{"text": '{"prompt":"Opening'}]}}]},
                    {"candidates": [candidate]}])):
                with self.assertRaisesRegex(ai.AIResponseFormatError, "incomplete result was not accepted"):
                    ai._gemini_raw([], "demo", "gemini-demo", "Return JSON", 30)
        finally:
            STREAM_SINK.reset(token)

    def test_openai_never_accepts_an_incomplete_nonstream_response(self):
        from unittest.mock import Mock
        response = Mock()
        response.json.return_value = {"status": "incomplete", "output_text": '{"prompt":"Opening only"}',
                                      "incomplete_details": {"reason": "max_output_tokens"}}
        with patch.object(ai.requests, "post", return_value=response):
            with self.assertRaisesRegex(ai.AIResponseFormatError, "partial prompt was not accepted"):
                ai._openai_raw([], "demo", "Return JSON", 30)

    def test_gemini_sends_thought_summaries_and_text_in_separate_channels(self):
        messages = []
        response = StreamResponse([
            {"candidates": [{"content": {"parts": [
                {"thought": True, "text": "Checking frame continuity."}
            ]}}]},
            {"candidates": [{"content": {"parts": [
                {"text": '{"prompt":"Final'}
            ]}}]},
            {"candidates": [{"content": {"parts": [
                {"text": ' prompt"}'}
            ]}, "finishReason": "STOP"}]},
        ])
        token = STREAM_SINK.set(lambda kind, text: messages.append((kind, text)))
        try:
            with patch.object(ai.requests, "post", return_value=response) as call:
                result = ai._gemini_raw([], "demo", "gemini-demo", "Return JSON", 30)
        finally:
            STREAM_SINK.reset(token)
        self.assertEqual(result, '{"prompt":"Final prompt"}')
        self.assertEqual(messages[0], ("reasoning", "Checking frame continuity."))
        self.assertEqual("".join(x for name, x in messages if name == "output"), result)
        self.assertEqual(call.call_args.kwargs["json"]["generationConfig"]["thinkingConfig"],
                         {"includeThoughts": True})
        self.assertTrue(call.call_args.kwargs["stream"])

    def test_gemini_retries_without_thought_opt_in_if_unsupported(self):
        seen = []
        class Unsupported(StreamResponse):
            status_code = 400

        def post(url, **kwargs):
            seen.append(kwargs["json"]["generationConfig"])
            if len(seen) == 1:
                return Unsupported([])
            return StreamResponse([{"candidates": [{"content": {
                "parts": [{"text": '{"prompt":"ok"}'}]}, "finishReason": "STOP"}]}])

        token = STREAM_SINK.set(lambda kind, text: None)
        try:
            with patch.object(ai.requests, "post", side_effect=post):
                result = ai._gemini_raw([], "demo", "gemini-demo", "Return JSON", 30)
        finally:
            STREAM_SINK.reset(token)
        self.assertEqual(result, '{"prompt":"ok"}')
        self.assertIn("thinkingConfig", seen[0])
        self.assertNotIn("thinkingConfig", seen[1])

    def test_gemini_preserves_error_body_after_stream_closes(self):
        import io
        import requests
        class ClosingResponse(requests.Response):
            def close(self):
                self.raw.close()
                super().close()
        response = ClosingResponse()
        response.status_code = 400
        response.url = "https://generativelanguage.googleapis.com/v1beta/models/demo:streamGenerateContent"
        response.raw = io.BytesIO(b'{"error":{"message":"The supplied media is not supported."}}')
        unsupported = StreamResponse([])
        unsupported.status_code = 400
        with patch.object(ai.requests, "post", side_effect=[unsupported, response]):
            with self.assertRaises(requests.HTTPError) as caught:
                ai._gemini_stream([], "key", "demo", 20)
        self.assertTrue(response.raw.closed)
        self.assertIn("The supplied media is not supported.",
                      ai.provider_error_message(caught.exception))

    def test_openai_stream_shows_only_published_summary_and_generated_text(self):
        messages = []
        events = [
            {"type": "response.reasoning_summary_text.delta",
             "delta": "Working through scene timing."},
            {"type": "response.output_text.delta", "delta": '{"prompt":'},
            {"type": "response.output_text.delta", "delta": '"A filmic wide shot"}'},
            {"type": "response.completed", "response": {"output": []}},
        ]
        token = STREAM_SINK.set(lambda kind, text: messages.append((kind, text)))
        try:
            with patch.object(ai.requests, "post", return_value=StreamResponse(events)) as call:
                result = ai._openai_raw([], "test-key", "Return JSON.", 30,
                                        model="gpt-test", chatgpt=False)
        finally:
            STREAM_SINK.reset(token)
        self.assertEqual(result, '{"prompt":"A filmic wide shot"}')
        self.assertEqual(messages[0], ("reasoning", "Working through scene timing."))
        self.assertNotIn("Working through", result)
        self.assertEqual(call.call_args.kwargs["json"]["reasoning"], {"summary": "auto"})
        self.assertEqual(call.call_args.kwargs["json"]["model"], "gpt-test")
        self.assertTrue(call.call_args.kwargs["json"]["stream"])

    def test_openai_models_without_summary_option_still_stream_text(self):
        seen = []
        class Unsupported(StreamResponse):
            status_code = 400

        def post(url, **kwargs):
            seen.append(kwargs["json"])
            if len(seen) == 1:
                return Unsupported([])
            return StreamResponse([
                {"type": "response.output_text.delta", "delta": '{"prompt":"final"}'},
                {"type": "response.completed", "response": {"output": []}},
            ])

        token = STREAM_SINK.set(lambda kind, text: None)
        try:
            with patch.object(ai.requests, "post", side_effect=post):
                self.assertEqual(ai._openai_raw([], "key", "Rules", 20,
                                                model="gpt-test", chatgpt=False), '{"prompt":"final"}')
        finally:
            STREAM_SINK.reset(token)
        self.assertIn("reasoning", seen[0])
        self.assertNotIn("reasoning", seen[1])
        self.assertEqual(seen[0]["text"], seen[1]["text"])
        self.assertEqual(seen[1]["text"]["format"]["type"], "json_schema")

    def test_qt_live_panel_collapse_and_stream_progress(self):
        # Other tests may have initialized a QCoreApplication; widgets require
        # QApplication, and Qt cannot upgrade a running QCoreApplication.
        # Test widgets in a fresh isolated GUI process to avoid a C++ crash.
        import subprocess
        import sys
        child = r'''
from PySide6.QtWidgets import QApplication
from ltx_prompt_director.stream_widgets import LiveOutputPane
app = QApplication([])
panel = LiveOutputPane()
panel.show()
app.processEvents()
panel.begin("Generating", "gpt-test")
panel.receive_stream("reasoning", "Provider summary.")
panel.receive_stream("output", '{"prompt":"hello"}')
assert panel.reasoning.toPlainText() == "Provider summary."
app.processEvents()
panel.finish(True)
assert "hello" in panel.output.toPlainText()
assert r'{"prompt":' not in panel.output.toPlainText()
panel.receive_stream("reset", "Retrying")
assert panel.output.toPlainText() == ""
panel.receive_stream("output", "valid")
panel.finish(True)
assert panel.status.text() == "Complete"
panel.toggle_content()
assert panel.content.isHidden()
panel.close()
'''
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
        result = subprocess.run([sys.executable, "-c", child],
                                text=True, capture_output=True, env=env, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + "\n" + result.stderr)


# Operation contracts at the HTTP boundary; all provider replies below are mocked.


class Reply:
    status_code = 200

    def __init__(self, raw, provider, *, terminal=True):
        self.raw, self.provider, self.terminal = raw, provider, terminal

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def close(self):
        pass

    def raise_for_status(self):
        pass

    def json(self):
        if self.provider == 'gemini':
            return {'candidates': [{'content': {'parts': [
                {'thought': True, 'text': 'Checking continuity.'}, {'text': self.raw}]}, 'finishReason': 'STOP'}]}
        return {'status': 'completed', 'output_text': self.raw}

    def iter_lines(self, **_):
        midpoint = len(self.raw) // 2
        if self.provider == 'gemini':
            events = [
                {'candidates': [{'content': {'parts': [{'thought': True, 'text': 'Checking.'}]}}]},
                *[{'candidates': [{'content': {'parts': [{'text': text}]}}]}
                  for text in (self.raw[:midpoint], self.raw[midpoint:])],
            ]
            if self.terminal:
                events.append({'candidates': [{'finishReason': 'STOP'}]})
        else:
            events = [{'type': 'response.output_text.delta', 'delta': text}
                      for text in (self.raw[:midpoint], self.raw[midpoint:])]
            if self.terminal:
                events.append({'type': 'response.completed', 'response': {'output': []}})
        yield from ('data: ' + json.dumps(event) for event in events)


def sequence():
    return [Segment('Opening', '', '', 'text', 'text', 'Walk toward the lantern.', 3, id='a'),
            Segment('Ending', '', '', 'text', 'text', 'Raise the lantern.', 3, id='b')]


def operation(name, segments, provider):
    common = (segments, provider, 'gemini-3.5-flash' if provider == 'gemini' else 'gpt-6', 'unused-key')
    production = (*common, '', '', False, False, False)
    selected = (segments, 0, *common[1:], '', 6)
    if name == 'build_prompts':
        return ai.build_prompts(*common, '', False, False, False, False)
    if name == 'refine_sequence':
        return ai.refine_sequence_prompts(*common, '', 'Keep identity.', False, False, False, False)
    if name == 'segment_generate':
        return ai.generate_segment_prompt(*selected)
    if name == 'segment_refine':
        return ai.refine_segment_prompt(*selected)
    if name == 'selected_timing':
        return ai.refine_timing(*selected)
    if name == 'global_generate':
        return ai.generate_global_direction(*common, '')
    if name == 'global_refine':
        return ai.refine_global_prompt(*common, '', 'Keep identity.')
    if name == 'generic_segments':
        return generic_ai.build_generic_segments(*common, '', False, False, False, False)
    if name == 'generic_segment':
        return generic_ai.refine_generic_segment(*selected)
    if name == 'generic_generate':
        return generic_ai.build_generic_prompt(*production)
    if name == 'generic_refine':
        return generic_ai.refine_generic_prompt(*production, 'Existing draft.', 'Improve camera.')
    if name == 'frames_generate':
        return ai.build_minimax_h3_prompt(*production)
    if name == 'references_generate':
        return ai.build_minimax_h3_reference_prompt(*production)
    if name == 'frames_refine':
        return ai.refine_minimax_h3_prompt(*production, 'Existing draft.', 'Improve camera.')
    if name == 'references_refine':
        return ai.refine_minimax_h3_reference_prompt(*production, 'Existing draft.', 'Improve camera.')
    if name == 'text_refine':
        return ai.refine_minimax_text_prompt(*common[1:], 'Existing draft.\n/refine Camera.')
    if name == 'combined':
        return segment_plan.generate_combined(*production, 'Existing draft.', 'Camera.', 30, [], 'generic', 'references', True)
    if name == 'timeline_sync':
        return segment_plan.sync_timeline_to_prompt(*common, 'Existing draft.', 30, [], 'generic', 'references')
    if name == 'reverse_sync':
        return segment_plan.sync_combined_from_timeline(*common, 'Existing draft.', 30, [], 'generic', 'references')
    if name.startswith('split_'):
        count = int(name[-1])
        return divide_segment_prompt(segments, 0, count, 'divide', *common[1:])
    if name == 'all_timing':
        return plan_sequence_timing(*common, 6, '', 'Existing draft.')
    raise AssertionError(name)


PROMPT = {'prompt': 'Complete revised production prompt.'}
SEGMENT = {'prompt': 'Walk toward the lantern.', 'imagePrompt': '', 'duration': 3}
SEQUENCE = {'segments': [SEGMENT, SEGMENT], 'globalPrompt': 'Keep identity.'}
TIMELINE = {'segments': [{'id': item.id, 'name': item.name, 'kind': item.kind, 'role': item.role,
                          'duration': item.duration, 'prompt': item.prompt, 'imagePrompt': '', 'artworkPrompt': ''}
                         for item in sequence()]}
CASES = [
    ('build_prompts', SEQUENCE, {'segments', 'globalPrompt'}),
    ('refine_sequence', SEQUENCE, {'segments', 'globalPrompt'}),
    ('segment_generate', SEGMENT, {'prompt', 'imagePrompt', 'duration'}),
    ('segment_refine', SEGMENT, {'prompt', 'imagePrompt', 'duration'}),
    ('selected_timing', {'duration': 3}, {'duration'}),
    ('global_generate', {'globalPrompt': 'Keep identity.'}, {'globalPrompt'}),
    ('global_refine', {'globalPrompt': 'Keep identity.', 'durations': [3, 3], 'proportionalTotal': None},
     {'globalPrompt', 'durations', 'proportionalTotal'}),
    ('generic_segments', {'segments': [{'prompt': 'Walk.', 'duration': 3}] * 2, 'globalPrompt': ''},
     {'segments', 'globalPrompt'}),
    ('generic_segment', {'prompt': 'Walk.', 'duration': 3}, {'prompt', 'duration'}),
    *[(name, PROMPT, {'prompt'}) for name in ('generic_generate', 'generic_refine', 'frames_generate',
       'references_generate', 'frames_refine', 'references_refine', 'text_refine', 'reverse_sync')],
    ('combined', {**PROMPT, **TIMELINE}, {'prompt', 'segments'}),
    ('timeline_sync', TIMELINE, {'segments'}),
    ('split_2', {'prompts': ['Walk.', 'Raise.']}, {'prompts'}),
    ('split_3', {'prompts': ['Walk.', 'Stop.', 'Raise.']}, {'prompts'}),
    ('all_timing', {'durations': [3, 3]}, {'durations'}),
]


@pytest.mark.parametrize('provider,stream', [('gemini', False), ('gemini', True),
                                           ('openai', False), ('openai', True), ('chatgpt', True)])
@pytest.mark.parametrize('name,response,fields', CASES, ids=[case[0] for case in CASES])
def test_every_operation_sends_its_own_schema_and_preserves_inputs(provider, stream, name, response, fields):
    segments = sequence()
    before = copy.deepcopy([vars(item) for item in segments])
    sink = STREAM_SINK.set((lambda *_: None) if stream else None)
    try:
        with patch.object(ai.requests, 'post', return_value=Reply(json.dumps(response), provider)) as post:
            operation(name, segments, provider)
    finally:
        STREAM_SINK.reset(sink)
    assert [vars(item) for item in segments] == before
    assert post.call_count == 1
    payload = post.call_args.kwargs['json']
    if provider == 'gemini':
        config = payload['generationConfig']
        assert config['responseMimeType'] == 'application/json'
        schema = config['responseJsonSchema']
    else:
        fmt = payload['text']['format']
        assert fmt['type'] == 'json_schema' and fmt['strict'] is True
        assert fmt['name']
        schema = fmt['schema']
    assert set(schema['properties']) == fields
    assert set(schema['required']) == fields
    assert schema['additionalProperties'] is False
    for field in ('segments', 'prompts', 'durations'):
        field_schema = schema['properties'].get(field, {})
        if field_schema.get('type') == 'array' and name not in ('combined', 'timeline_sync'):
            expected = 3 if name == 'split_3' else 2
            assert field_schema['minItems'] == field_schema['maxItems'] == expected
    assert schemas.current() is schemas.PROMPT


@pytest.mark.parametrize('provider,stream', [('gemini', False), ('gemini', True),
                                           ('openai', False), ('openai', True), ('chatgpt', True)])
@pytest.mark.parametrize('raw', [
    '{"prompt":"unfinished', '{"prompt":"wrong scope"}',
    '{"prompt":"Walk","imagePrompt":"","duration":"3"}',
    '{"prompt":"Walk","imagePrompt":"","duration":true}',
    '{"prompt":"Walk","imagePrompt":"","duration":NaN}',
    '{"prompt":"Walk","imagePrompt":"","duration":0}',
    '{"prompt":"Walk","imagePrompt":"","duration":3,"segments":[]}',
    '{"prompt":"Walk","prompt":"Overwrite","imagePrompt":"","duration":3}',
    '{"prompt":"  ","imagePrompt":"","duration":3}', '[]',
])
def test_segment_refine_rejects_invalid_provider_results_before_returning(provider, stream, raw):
    segments = sequence()
    before = copy.deepcopy([vars(item) for item in segments])
    sink = STREAM_SINK.set((lambda *_: None) if stream else None)
    try:
        with patch.object(ai.requests, 'post', return_value=Reply(raw, provider)):
            with pytest.raises(ai.AIResponseFormatError, match='selected_segment schema'):
                operation('segment_refine', segments, provider)
    finally:
        STREAM_SINK.reset(sink)
    assert [vars(item) for item in segments] == before
    assert schemas.current() is schemas.PROMPT


@pytest.mark.parametrize('provider', ['gemini', 'openai', 'chatgpt'])
def test_summary_fallback_retains_segment_contract(provider):
    rejected = Reply('', provider)
    rejected.status_code = 400
    sink = STREAM_SINK.set(lambda *_: None)
    try:
        with patch.object(ai.requests, 'post', side_effect=[rejected, Reply(json.dumps(SEGMENT), provider)]) as post:
            operation('segment_refine', sequence(), provider)
    finally:
        STREAM_SINK.reset(sink)
    first, second = [call.kwargs['json'] for call in post.call_args_list]
    if provider == 'gemini':
        first, second = first['generationConfig'], second['generationConfig']
        assert first['responseJsonSchema'] == second['responseJsonSchema']
        assert 'thinkingConfig' not in second
    else:
        assert first['text'] == second['text']
        assert 'reasoning' not in second


@pytest.mark.parametrize('provider', ['gemini', 'openai'])
def test_valid_json_without_terminal_event_is_never_accepted(provider):
    sink = STREAM_SINK.set(lambda *_: None)
    try:
        with patch.object(ai.requests, 'post', return_value=Reply(json.dumps(SEGMENT), provider, terminal=False)):
            with pytest.raises(ai.AIResponseFormatError, match='before completion'):
                operation('segment_refine', sequence(), provider)
    finally:
        STREAM_SINK.reset(sink)


def test_parallel_requests_do_not_exchange_contracts_and_reset_after_failure():
    barrier = Barrier(2)
    # Use a single shared patched dispatcher to avoid racing mock teardown.
    def transport(*_):
        schema = schemas.current()
        barrier.wait(timeout=5)
        raw = '{"duration":3}' if schema is schemas.TIMING else '{"prompt":"A scene."}'
        return ai._checked_response(raw)
    def run(schema):
        result = ai._structured_raw([], 'gemini', 'model', 'key', '', 30, schema=schema)
        assert schemas.current() is schemas.PROMPT
        return json.loads(result)
    with patch.object(ai, '_provider_raw', side_effect=transport), ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, [schemas.TIMING, schemas.PROMPT]))
    assert results == [{'duration': 3}, {'prompt': 'A scene.'}]
    with patch.object(ai, '_provider_raw', side_effect=RuntimeError('connection lost')):
        with pytest.raises(RuntimeError):
            run(schemas.TIMING)
    assert schemas.current() is schemas.PROMPT


@pytest.mark.parametrize('schema,raw', [
    (schemas.timings(2), '{"durations":[3]}'),
    (schemas.timings(2), '{"durations":[3,3],"prompt":"Wrong scope"}'),
    (schemas.split(3), '{"prompts":["First","Second"]}'),
    (schemas.timeline(combined=False), json.dumps({**TIMELINE, **PROMPT})),
])
def test_wrong_count_and_cross_scope_outputs_are_rejected(schema, raw):
    with schemas.using(schema), pytest.raises(ai.AIResponseFormatError):
        ai._checked_response(raw)


def test_openai_refusal_is_reported_as_refusal_not_malformed_json():
    response = Mock()
    response.json.return_value = {'status': 'completed', 'output': [
        {'content': [{'type': 'refusal', 'refusal': 'Unable to fulfill this request.'}]}]}
    with patch.object(ai.requests, 'post', return_value=response):
        with pytest.raises(RuntimeError, match='declined this request'):
            operation('segment_refine', sequence(), 'openai')


if __name__ == "__main__":
    unittest.main()
