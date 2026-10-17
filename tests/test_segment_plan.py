"""Combined GPT-6+ prompt/segment transport and artwork validation tests."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ltx_prompt_director.models import Segment
from ltx_prompt_director import segment_plan, artwork_tools


def seeded():
    return [
        Segment("A", "/tmp/a.png", "/tmp/a.png", "image", "start", "Old A", 3.0, id="seg-a"),
        Segment("B", "/tmp/b.png", "/tmp/b.png", "image", "end", "Old B", 3.0, id="seg-b"),
    ]


def record(item):
    return {"id": item.id, "name": item.name, "kind": item.kind,
            "role": item.role, "duration": item.duration,
            "prompt": item.prompt, "imagePrompt": item.image_prompt,
            "artworkPrompt": ""}


class SegmentPlanTests(unittest.TestCase):
    def test_gpt_six_gate(self):
        self.assertTrue(segment_plan.supports_structured_edit("chatgpt", "gpt-6"))
        self.assertTrue(segment_plan.supports_structured_edit("openai", "gpt-6.1-mini"))
        self.assertFalse(segment_plan.supports_structured_edit("gemini", "gpt-6"))
        self.assertFalse(segment_plan.supports_structured_edit("openai", "gpt-5.4-mini"))

    def test_original_media_stays_and_segment_json_is_appended(self):
        items = seeded()
        result = segment_plan.normalize_plan(json.dumps({
            "prompt": "Preserved unified production prose.",
            "segments": [record(item) for item in items],
        }), items)
        built = segment_plan.build_segment_objects(items, result["segments"])
        self.assertEqual([item.id for item in built], ["seg-a", "seg-b"])
        self.assertEqual([item.media_path for item in built], ["/tmp/a.png", "/tmp/b.png"])
        self.assertEqual([item.prompt for item in built], ["Old A", "Old B"])
        self.assertFalse(segment_plan.preview_operations(items, result["segments"]))
        instructions = segment_plan.response_contract(items, "My edited draft", refine=True)
        self.assertIn("My edited draft", instructions)
        self.assertIn("new:1", instructions)
        self.assertIn("seg-a", instructions)
        self.assertIn("segments", instructions)

    def test_reorder_and_insert_artwork_proposal_without_file_invention(self):
        items = seeded()
        new = {"id": "new:1", "name": "Bridging artwork", "kind": "text",
               "role": "text", "duration": 1.5, "prompt": "Transition slowly",
               "imagePrompt": "Forest scene", "artworkPrompt": "Landscape photo of a forest"}
        response = segment_plan.normalize_plan(json.dumps({
            "prompt": "Unified prompt matching final segments",
            "segments": [record(items[1]), new, record(items[0])],
        }), items)
        edits = segment_plan.preview_operations(items, response["segments"])
        self.assertIn("add", [op["kind"] for op in edits])
        self.assertIn("move", [op["kind"] for op in edits])
        self.assertIn("artwork", [op["kind"] for op in edits])
        merged = segment_plan.build_segment_objects(items, response["segments"])
        self.assertEqual([s.id for s in merged if s.id in {"seg-a", "seg-b"}], ["seg-b", "seg-a"])
        self.assertEqual(merged[1].kind, "text")
        self.assertFalse(merged[1].media_path)
        self.assertEqual(merged[1].duration, 1.5)

    def test_reject_unknown_media_injection_and_duplicate_ids(self):
        items = seeded()
        payloads = [
            [dict(record(items[0]), id="made-up-image"), record(items[1])],
            [record(items[0]), record(items[0])],
            [dict(record(items[0]), kind="video"), record(items[1])],
            [dict(record(items[0]), duration=float("inf")), record(items[1])],
            [dict(record(items[0]), role="text"), record(items[1])],
        ]
        for records in payloads:
            with self.subTest(records=records), self.assertRaises(ValueError):
                segment_plan.normalize_plan(json.dumps({
                    "prompt": "Complete", "segments": records,
                }), items)

    def test_progress_only_uses_completed_json_records(self):
        raw = '{"prompt":"New draft","segments":[' + json.dumps(record(seeded()[0]))
        self.assertEqual(len(segment_plan.completed_stream_records(raw)), 1)
        self.assertEqual(segment_plan.stream_operations(seeded(), raw), [])
        raw += ',{"id":"new:1","name":"New visual","kind":"text","role":"text",'
        self.assertEqual(len(segment_plan.completed_stream_records(raw)), 1)
        raw += '"duration":2.0,"prompt":"A scene","artworkPrompt":"A bright scenic image"}'
        changes = segment_plan.stream_operations(seeded(), raw)
        self.assertIn("add", [op["kind"] for op in changes])

    def test_existing_prompt_template_is_reused_not_replaced(self):
        items = seeded()
        output = json.dumps({"prompt": "Film brief", "segments": [record(s) for s in items]})
        rules_seen = []

        def output_mock(inputs, provider, model, key, rules, timeout):
            rules_seen.append(rules)
            return output

        with patch.object(segment_plan.ai, "_provider_raw", side_effect=output_mock), \
             patch.object(segment_plan.ai, "_minimax_frames_inputs", return_value=[]):
            result = segment_plan.generate_combined(
                items, "openai", "gpt-6", "key", "intent", "global",
                True, True, True, "Existing unified prompt", "Preserve action",
                30, [], "minimax_frames", "frames", True
            )
        self.assertEqual(result["prompt"], "Film brief")
        text = rules_seen[0]
        self.assertIn("CURRENT SEGMENTS:", text)
        self.assertIn("Existing unified prompt", text)
        self.assertIn("ADDITIVE GPT-6+", text)
        self.assertLess(text.index(segment_plan.ai.UNIFIED_PROMPT_TRANSPORT.strip()), text.index("ADDITIVE GPT-6+"))

    def test_gemini_flash_lite_is_preferred_when_chatgpt_oauth_is_selected(self):
        tools = artwork_tools.available_artwork_tools(
            {"gemini": "gemini-api", "openai": "openai-api", "chatgpt": "oauth"},
            preferred="chatgpt",
        )
        self.assertEqual([item.provider for item in tools], ["gemini", "openai"])
        self.assertEqual(tools[0].model, "gemini-3.1-flash-lite-image")
        self.assertEqual(artwork_tools.available_artwork_tools(
            {"chatgpt": "oauth"}, preferred="chatgpt"
        ), [])

    def test_gemini_flash_lite_calls_documented_image_endpoint(self):
        from PIL import Image
        from io import BytesIO
        import base64
        png = BytesIO()
        Image.new("RGB", (2, 2)).save(png, format="PNG")
        encoded = base64.b64encode(png.getvalue()).decode()
        class Reply:
            def raise_for_status(self):
                pass
            def json(self):
                return {"candidates": [{"content": {"parts": [
                    {"inlineData": {"mimeType": "image/png", "data": encoded}}
                ]}}]}
        tool = artwork_tools.ArtworkTool(
            "gemini", "gemini-3.1-flash-lite-image", "gemini-api"
        )
        with patch.object(artwork_tools.requests, "post", return_value=Reply()) as post:
            data = artwork_tools._gemini_image("Photographic forest", tool)
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        self.assertIn("gemini-3.1-flash-lite-image:generateContent", post.call_args.args[0])
        self.assertEqual(post.call_args.kwargs["json"]["generationConfig"]["imageConfig"],
                         {"aspectRatio": "16:9", "imageSize": "1K"})
        self.assertEqual(post.call_args.kwargs["headers"]["x-goog-api-key"], "gemini-api")

    def test_artwork_tools_return_png_and_use_provider_api_not_chatgpt(self):
        from PIL import Image
        from io import BytesIO
        png = BytesIO()
        Image.new("RGB", (2, 2)).save(png, format="PNG")
        row = {"id": "new:1", "name": "New image", "artworkPrompt": "Scenic image"}
        with tempfile.TemporaryDirectory() as tmp, \
             patch.dict("os.environ", {"XDG_DATA_HOME": tmp}), \
             patch.object(artwork_tools, "_gemini_image", return_value=png.getvalue()) as generate:
            images = artwork_tools.render_artwork(
                [row], artwork_tools.ArtworkTool("gemini", "gemini-3.1-flash-lite-image", "key")
            )
            self.assertIn("new:1", images)
            self.assertTrue(Path(images["new:1"]).is_file())
            generate.assert_called_once()

    def test_gemini_flash_reasoning_model_can_plan_segments(self):
        self.assertTrue(segment_plan.supports_structured_edit("gemini", "gemini-3.5-flash"))
        self.assertTrue(segment_plan.supports_structured_edit("gemini", "gemini-3.1-flash-lite"))
        self.assertFalse(segment_plan.supports_structured_edit("gemini", "gemini-3.1-flash-lite-image"))


if __name__ == "__main__":
    unittest.main()
