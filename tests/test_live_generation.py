"""Regression tests: real provider stream parsing, summary isolation and Qt panels."""
from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from ltx_prompt_director import ai
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
                {"type": "response.output_text.delta", "delta": "final"},
                {"type": "response.completed", "response": {"output": []}},
            ])

        token = STREAM_SINK.set(lambda kind, text: None)
        try:
            with patch.object(ai.requests, "post", side_effect=post):
                self.assertEqual(ai._openai_raw([], "key", "Rules", 20,
                                                model="gpt-test", chatgpt=False), "final")
        finally:
            STREAM_SINK.reset(token)
        self.assertIn("reasoning", seen[0])
        self.assertNotIn("reasoning", seen[1])

    def test_qt_live_panel_collapse_and_stream_progress(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from ltx_prompt_director.stream_widgets import LiveOutputPane
        app = QApplication.instance() or QApplication([])
        panel = LiveOutputPane()
        panel.begin("Generating", "gpt-test")
        panel.event("reasoning", "Provider summary.")
        panel.event("output", '{"prompt":"hello"}')
        self.assertEqual(panel.reasoning.toPlainText(), "Provider summary.")
        self.assertEqual(panel.output.toPlainText(), '{"prompt":"hello"}')
        panel.event("reset", "Retrying")
        self.assertFalse(panel.received_reasoning)
        self.assertEqual(panel.output.toPlainText(), "")
        panel.event("output", "valid")
        panel.finish(True)
        self.assertEqual(panel.status.text(), "Complete")
        panel.toggle_content()
        self.assertTrue(panel.content.isHidden())
        panel.close()


if __name__ == "__main__":
    unittest.main()
