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
    def test_sync_retimes_existing_images_without_replacing_pixels(self):
        originals = seeded()
        original_prompt = "00:00–00:01 opening; 00:01–00:08 transform"
        rows = [
            {**record(originals[0]), "duration": 1.0, "prompt": "Opening image held one second"},
            {**record(originals[1]), "duration": 7.0, "prompt": "Final image reached at eight seconds"},
        ]
        result = segment_plan.normalize_timeline_sync(
            json.dumps({"prompt": "Unwanted model revision", "segments": rows}),
            originals, original_prompt)
        self.assertNotIn("prompt", result)
        fresh = segment_plan.build_segment_objects(originals, result["segments"])
        self.assertEqual([x.duration for x in fresh], [1.0, 7.0])
        self.assertEqual([x.id for x in fresh], ["seg-a", "seg-b"])
        self.assertEqual([x.media_path for x in fresh], ["/tmp/a.png", "/tmp/b.png"])
        self.assertEqual([x.role for x in fresh], ["start", "end"])
        self.assertEqual([x.prompt for x in fresh],
                         ["Opening image held one second", "Final image reached at eight seconds"])
        self.assertEqual([op["kind"] for op in segment_plan.preview_operations(originals, result["segments"])],
                         ["timing", "rewrite", "timing", "rewrite"])

    def test_sync_rebuilds_new_intervals_and_optional_image_placeholders(self):
        originals = seeded()
        first, last = [record(x) for x in originals]
        first["duration"] = 1.5
        last["duration"] = 1.5
        inserted_text = {"id": "new:1", "name": "Motion bridge", "kind": "text",
                         "role": "text", "duration": 2.0, "prompt": "Gradual change",
                         "imagePrompt": "", "artworkPrompt": ""}
        inserted_picture = {"id": "new:2", "name": "Missing checkpoint", "kind": "image",
                            "role": "end", "duration": 3.0, "prompt": "Ending pose",
                            "imagePrompt": "Close frame in identical light",
                            "artworkPrompt": "Render a visually matching missing ending frame"}
        result = segment_plan.normalize_timeline_sync(
            json.dumps({"segments": [first, inserted_text, inserted_picture, last]}),
            originals, "fixed master")
        items = segment_plan.build_segment_objects(originals, result["segments"])
        self.assertEqual([x.kind for x in items], ["image", "text", "text", "image"])
        self.assertEqual(items[2].artwork_target_role, "end")
        self.assertEqual(items[2].artwork_status, "")
        self.assertEqual([x.media_path for x in items if x.kind == "image"],
                         ["/tmp/a.png", "/tmp/b.png"])

    def test_sync_rejects_image_deletion_and_existing_artwork_modification(self):
        originals = seeded()
        with self.assertRaisesRegex(Exception, "retain media checkpoints"):
            segment_plan.normalize_timeline_sync(
                json.dumps({"segments": [record(originals[0])]}),
                originals, "unchanged")
        rows = [record(x) for x in originals]
        rows[0]["artworkPrompt"] = "Regenerate existing frame"
        with self.assertRaisesRegex(Exception, "cannot regenerate"):
            segment_plan.normalize_timeline_sync(
                json.dumps({"segments": rows}), originals, "unchanged")

    def test_sync_sends_existing_json_and_images_with_uuid_to_provider(self):
        originals = seeded()
        visuals = [{"name": "First frame", "kind": "image", "role": "start",
                    "image": "data:image/png;base64,AAAA"},
                   {"name": "Ending frame", "kind": "image", "role": "end",
                    "image": "data:image/png;base64,BBBB"}]
        rows = [record(x) for x in originals]
        rows[0]["duration"] = 1.0
        rows[1]["duration"] = 7.0
        captured = {}
        def reply(inputs, provider, model, api_key, rules, timeout):
            captured["inputs"] = inputs
            captured["rules"] = rules
            return json.dumps({"segments": rows})
        with (patch.object(segment_plan.ai, "_minimax_frames_inputs", return_value=visuals),
              patch.object(segment_plan.ai, "_provider_raw", side_effect=reply)):
            result = segment_plan.sync_timeline_to_prompt(
                originals, "gemini", "gemini-3.5-flash", "test-key",
                "00:00-00:01: original image; 00:01-00:08: ending image",
                30, [], "minimax_frames", "frames")
        self.assertEqual(result["segments"][0]["duration"], 1.0)
        self.assertIn("seg-a", captured["inputs"][0]["name"])
        self.assertIn("seg-b", captured["inputs"][1]["name"])
        self.assertIn("data:image/png;base64,AAAA", captured["inputs"][0]["image"])
        self.assertIn('"id": "seg-a"', captured["rules"])
        self.assertIn("AUTHORITATIVE COMBINED PROMPT", captured["rules"])
        self.assertIn("retime", captured["rules"].lower())

    def test_reverse_sync_uses_every_segment_as_authoritative(self):
        originals = seeded()
        originals.insert(1, Segment("Action", "", "", "text", "text",
                                    "Intermediate change", 2.5, id="middle"))
        supplied = [
            {"name": "First", "kind": "image", "role": "start",
             "image": "data:image/png;base64,AAAA"},
            {"name": "Second", "kind": "image", "role": "end",
             "image": "data:image/png;base64,BBBB"},
        ]
        seen = {}
        def reply(inputs, provider, model, key, rules, timeout):
            seen["rules"] = rules
            seen["inputs"] = inputs
            return json.dumps({"prompt": "All three timeline beats in order"})
        with (patch.object(segment_plan.ai, "_minimax_frames_inputs", return_value=supplied),
              patch.object(segment_plan.ai, "_provider_raw", side_effect=reply)):
            result = segment_plan.sync_combined_from_timeline(
                originals, "gemini", "gemini-3.5-flash", "key", "Old combined",
                40, [], "minimax_frames", "frames", "Final notes")
        self.assertEqual(result["prompt"], "All three timeline beats in order")
        for sid in ("seg-a", "middle", "seg-b"):
            self.assertIn(sid, seen["rules"])
        self.assertIn("Intermediate change", seen["rules"])
        self.assertIn("Final notes", seen["rules"])
        self.assertIn("ALL CURRENT TIMELINE SEGMENTS", seen["rules"])
        self.assertNotIn("segments", result)

    def test_global_magic_refinement_uses_all_existing_prompts_and_count(self):
        from ltx_prompt_director import ai
        items = seeded()
        seen = {}
        def reply(images, provider, model, key, rules, timeout):
            seen["rules"] = rules
            seen["images"] = images
            return json.dumps({"globalPrompt": "Same subject across whole story",
                "segments": [
                    {"prompt": "Improved introduction", "duration": 1.5},
                    {"prompt": "Improved final beat", "duration": 4.5},
                ]})
        with (patch.object(ai, "_segment_input", side_effect=lambda item: {
                  "name": item.name, "kind": item.kind, "role": item.role}),
              patch.object(ai, "_provider_raw", side_effect=reply)):
            result = ai.refine_sequence_prompts(
                items, "gemini", "gemini-3.5-flash", "key",
                "/refine-global Make progress gradual", "Old global text",
                False, False, False, False, 40)
        self.assertEqual(len(result["segments"]), 2)
        self.assertEqual(result["segments"][0]["prompt"], "Improved introduction")
        self.assertIn("Old A", seen["rules"])
        self.assertIn("Old B", seen["rules"])
        self.assertIn("Make progress gradual", seen["rules"])
        self.assertIn("SEQUENCE-WIDE MAGIC BUILD REFINEMENT", seen["rules"])

    def test_gpt_six_gate(self):
        self.assertTrue(segment_plan.supports_structured_edit("chatgpt", "gpt-6"))
        self.assertTrue(segment_plan.supports_structured_edit("openai", "gpt-6.1-mini"))
        self.assertFalse(segment_plan.supports_structured_edit("gemini", "gpt-6"))
        self.assertFalse(segment_plan.supports_structured_edit("openai", "gpt-5.4-mini"))
        for model in ("gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-flash-lite-latest"):
            self.assertTrue(segment_plan.supports_structured_edit("gemini", model))
        self.assertFalse(segment_plan.supports_structured_edit("gemini", "gemini-3.1-flash-lite-image"))

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
        ]
        for records in payloads:
            with self.subTest(records=records), self.assertRaises(ValueError):
                segment_plan.normalize_plan(json.dumps({
                    "prompt": "Complete", "segments": records,
                }), items)

    def test_frame_roles_canonicalized_for_text_and_preserved_for_visuals(self):
        items = seeded()
        items.append(Segment("Text", "", "", "text", "text",
                             "Old direction", 2.0, id="seg-text"))
        records = [record(item) for item in items]
        records[0]["role"] = "text"  # exact 1.13.0a73 failure: model labels image as text
        records[1]["role"] = "source"  # conditioning label is NOT a frame role
        records[2]["role"] = "end"  # model borrowed visual role for text
        records[2]["prompt"] = "New direction for the text segment"
        outcome = segment_plan.normalize_plan(json.dumps({
            "prompt": "Continuous original brief", "segments": records
        }), items)
        self.assertEqual([r["role"] for r in outcome["segments"]],
                         ["start", "end", "text"])
        rebuilt = segment_plan.build_segment_objects(items, outcome["segments"])
        self.assertEqual([s.kind for s in rebuilt], ["image", "image", "text"])
        self.assertEqual(rebuilt[2].prompt, "New direction for the text segment")
        changes = segment_plan.preview_operations(items, outcome["segments"])
        self.assertEqual(next(c["prompt"] for c in changes if c["kind"] == "rewrite"),
                         "New direction for the text segment")

    def test_new_image_placeholder_keeps_image_role_and_text_media_safety(self):
        items = seeded()
        proposal = {"id":"new:1", "kind":"image", "role":"end",
                    "name":"Requested last frame", "duration":2.0,
                    "prompt":"Bridge", "imagePrompt":"Photo", "artworkPrompt":"Render"}
        result = segment_plan.normalize_plan(json.dumps({
            "prompt": "A coherent timeline", "segments": [
                record(items[0]), proposal, record(items[1])]
        }), items)
        self.assertEqual(result["segments"][1]["role"], "text")
        self.assertEqual(result["segments"][1]["artworkRole"], "end")
        self.assertEqual(result["segments"][1]["kind"], "text")
        existing = segment_plan.normalize_plan(json.dumps({
            "prompt": "Original", "segments": [
                dict(record(items[0]), role="text"), record(items[1])]
        }), items)
        self.assertEqual(existing["segments"][0]["kind"], "image")
        self.assertEqual(existing["segments"][0]["role"], "start")
        self.assertEqual(segment_plan.build_segment_objects(
            items, existing["segments"])[0].media_path, items[0].media_path)

    def test_existing_video_with_text_role_can_rewrite_without_losing_media(self):
        items = seeded()
        items[0] = Segment(
            "Original movie", "/tmp/shot.mp4", "/tmp/shot-preview.png",
            "video", "end", "Original continuity", 4.0,
            id="7ef289b4-aa34-4389-82f8-fb1357862450"
        )
        rows = [record(s) for s in items]
        rows[0]["role"] = "text"
        rows[0]["prompt"] = "New seamless continuity and timing"
        result = segment_plan.normalize_plan(json.dumps({
            "prompt": "Updated production prompt", "segments": rows
        }), items)
        self.assertEqual(result["segments"][0]["role"], "end")
        built = segment_plan.build_segment_objects(items, result["segments"])
        self.assertEqual(built[0].kind, "video")
        self.assertEqual(built[0].id, items[0].id)
        self.assertEqual(built[0].media_path, "/tmp/shot.mp4")
        self.assertEqual(built[0].preview_path, "/tmp/shot-preview.png")
        self.assertEqual(built[0].prompt, "New seamless continuity and timing")
        self.assertFalse(any(op["kind"] == "role"
                             for op in segment_plan.preview_operations(
                                 items, result["segments"])))
        self.assertIn("NEVER send role=text for an existing image/video",
                      segment_plan.response_contract(items, "Draft", refine=True))

    def test_existing_image_artwork_edit_is_not_dropped(self):
        items = seeded()
        edit = dict(record(items[0]),
                    artworkPrompt="Remove the unwanted object from this frame",
                    imagePrompt="Same original composition, with object removed")
        response = segment_plan.normalize_plan(json.dumps({
            "prompt": "Same original production wording",
            "segments": [edit, record(items[1])]
        }), items)
        self.assertEqual(response["segments"][0]["artworkPrompt"],
                         "Remove the unwanted object from this frame")
        self.assertIn("artwork", [op["kind"] for op in
                                  segment_plan.preview_operations(items, response["segments"])])
        built = segment_plan.build_segment_objects(items, response["segments"])
        self.assertEqual(built[0].media_path, "/tmp/a.png")
        self.assertEqual(built[0].kind, "image")
        self.assertEqual(built[0].role, "start")
        self.assertIn("To EDIT OR REGENERATE AN EXISTING IMAGE FRAME",
                      segment_plan.response_contract(items, "Draft", refine=True))

    def test_image_edit_rejects_nonimage_existing_segment(self):
        items = seeded()
        items[0].kind = "video"
        data = dict(record(items[0]), artworkPrompt="Edit this existing movie")
        with self.assertRaisesRegex(segment_plan.ai.AIResponseFormatError,
                                    "Only existing image frames"):
            segment_plan.normalize_plan(json.dumps({
                "prompt": "Original", "segments": [data, record(items[1])]
            }), items)

    def test_explicit_refine_edit_not_regular_image_prose(self):
        self.assertEqual(
            segment_plan.explicit_existing_image_edit(
                "Continue story\n/refine Edit this image: remove the background object"),
            "Edit this image: remove the background object"
        )
        self.assertEqual(segment_plan.explicit_existing_image_edit(
            "Generate imagery from this picture slowly."), "")
        self.assertEqual(segment_plan.explicit_existing_image_edit(
            "/refine Move character toward the image in the background"), "")

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
        self.assertIn("ADDITIVE STRUCTURED", text)
        self.assertNotIn("Do not return timed_actions, segment records", text)
        self.assertNotIn("Prompt editing never rebuilds the timeline", text)
        self.assertIn("ALWAYS include the complete nonempty segments array", text)

    def test_image_only_combined_reply_gets_one_schema_repair_with_original_prose(self):
        items = seeded()
        original = "Original production brief, with requested generated artwork."
        missing = json.dumps({"prompt": original})
        new = {"id": "new:1", "name": "Requested frame", "kind": "image",
               "role": "end", "duration": 2.5,
               "prompt": "Continue the same shot to the generated frame",
               "imagePrompt": "Lioness reference frame",
               "artworkPrompt": "Render a photorealistic lioness at night"}
        repaired = json.dumps({"prompt": "Unwanted replacement text",
                               "segments": [record(items[0]), new, record(items[1])]})
        with patch.object(segment_plan.ai, "_provider_raw", side_effect=[missing, repaired]) as provider, \
             patch.object(segment_plan.ai, "_minimax_frames_inputs", return_value=[]):
            result = segment_plan.generate_combined(
                items, "openai", "gpt-6", "key", "intent: render an image",
                "global", True, True, True, "Current draft",
                "/refine create an image", 30, [], "minimax_frames",
                "frames", True
            )
        self.assertEqual(provider.call_count, 2)
        self.assertEqual(result["prompt"], original)
        self.assertEqual([s["id"] for s in result["segments"]],
                         ["seg-a", "new:1", "seg-b"])
        self.assertEqual(result["segments"][1]["artworkPrompt"],
                         "Render a photorealistic lioness at night")
        self.assertEqual(result["segments"][1]["artworkRole"], "end")
        self.assertIn("SCHEMA-ONLY REPAIR", provider.call_args.args[4])
        built = segment_plan.build_segment_objects(items, result["segments"])
        self.assertEqual([built[0].media_path, built[2].media_path],
                         ["/tmp/a.png", "/tmp/b.png"])
        self.assertIn("artwork", [x["kind"] for x in
                                  segment_plan.preview_operations(items, result["segments"])])

    def test_genuinely_invalid_segment_plan_is_not_silently_repaired(self):
        items = seeded()
        invalid = json.dumps({"prompt": "Untouched", "segments": [
            dict(record(items[0]), id="invented-file"), record(items[1])]})
        with patch.object(segment_plan.ai, "_provider_raw", return_value=invalid) as provider, \
             patch.object(segment_plan.ai, "_minimax_frames_inputs", return_value=[]):
            with self.assertRaises(segment_plan.ai.AIResponseFormatError):
                segment_plan.generate_combined(
                    items, "openai", "gpt-6", "key", "intent", "",
                    True, True, True, "Current draft", "", 30, [],
                    "minimax_frames", "frames", True
                )
        provider.assert_called_once()

    def test_missing_segment_list_repair_fails_safely_without_looping(self):
        items = seeded()
        prompt_only = json.dumps({"prompt": "A complete production prompt."})
        with patch.object(segment_plan.ai, "_provider_raw", side_effect=[
                prompt_only, prompt_only]) as provider, \
             patch.object(segment_plan.ai, "_minimax_frames_inputs", return_value=[]):
            with self.assertRaisesRegex(segment_plan.ai.AIResponseFormatError,
                                        "after one repair"):
                segment_plan.generate_combined(
                    items, "openai", "gpt-6", "key", "intent", "",
                    True, True, True, "Current draft", "", 30, [],
                    "minimax_frames", "frames", True
                )
        self.assertEqual(provider.call_count, 2)

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

    def test_selected_image_model_is_independent_of_text_provider(self):
        keys = {"gemini": "g-api", "openai": "o-api", "chatgpt": "oauth"}
        cases = [
            ("gemini:gemini-3.1-flash-lite-image", "gemini", "gemini-3.1-flash-lite-image"),
            ("gemini:gemini-3.1-flash-image", "gemini", "gemini-3.1-flash-image"),
            ("gemini:gemini-3-pro-image", "gemini", "gemini-3-pro-image"),
            ("openai:gpt-image-2.5-flare", "openai", "gpt-image-2.5-flare"),
            ("openai:gpt-image-2.5-sunburst", "openai", "gpt-image-2.5-sunburst"),
            ("openai:gpt-image-2", "openai", "gpt-image-2"),
            ("openai:gpt-image-1", "openai", "gpt-image-1"),
            ("openai:gpt-image-1-mini", "openai", "gpt-image-1-mini"),
        ]
        for selection, provider, model in cases:
            with self.subTest(model=model):
                selected = artwork_tools.available_artwork_tools(
                    keys, preferred="chatgpt", selected=selection
                )
                self.assertEqual(len(selected), 1)
                self.assertEqual(selected[0].provider, provider)
                self.assertEqual(selected[0].model, model)
                self.assertEqual(selected[0].api_key, keys[provider])

    def test_explicit_image_preference_never_falls_back_to_other_billed_provider(self):
        keys = {"gemini": "g-api", "chatgpt": "oauth"}
        self.assertEqual(
            artwork_tools.available_artwork_tools(
                keys, preferred="gemini", selected="openai:gpt-image-2"
            ), []
        )
        self.assertEqual(artwork_tools.available_artwork_tools(
            {"chatgpt": "oauth"}, selected="gemini:gemini-3.1-flash-image"
        ), [])
        self.assertEqual(artwork_tools.available_artwork_tools(
            keys, selected="openai:unsupported-image-model"
        ), [])
        automatic = artwork_tools.available_artwork_tools(
            {"gemini": "g-api", "openai": "o-api"}, preferred="openai"
        )
        self.assertEqual([tool.provider for tool in automatic], ["openai", "gemini"])

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
        config = post.call_args.kwargs["json"]["generationConfig"]
        self.assertEqual(config["imageConfig"], {"aspectRatio": "16:9"})
        self.assertNotIn("responseFormat", config)
        self.assertNotIn("imageSize", config["imageConfig"])
        self.assertEqual(post.call_args.kwargs["headers"]["x-goog-api-key"], "gemini-api")

    def test_gemini_images_use_documented_v1beta_route(self):
        from io import BytesIO
        from PIL import Image
        import base64
        buffer = BytesIO()
        Image.new("RGB", (6, 4)).save(buffer, "PNG")
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        class Reply:
            def raise_for_status(self): pass
            def json(self): return {"candidates":[{"content":{"parts":[{
                "inlineData": {"mimeType": "image/png", "data": encoded}
            }]}}]}
        tool = artwork_tools.ArtworkTool("gemini", "gemini-3.1-flash-lite-image", "api")
        with patch.object(artwork_tools.requests, "post", return_value=Reply()) as post:
            artwork_tools._gemini_image("A 16:9 still", tool)
        self.assertIn("/v1beta/models/gemini-3.1-flash-lite-image:generateContent",
                      post.call_args.args[0])
        config = post.call_args.kwargs["json"]["generationConfig"]
        self.assertEqual(config["responseModalities"], ["IMAGE"])
        self.assertEqual(config["imageConfig"]["aspectRatio"], "16:9")
        self.assertNotIn("responseFormat", config)

    def test_gemini_rejects_old_enum_fields_but_accepts_correct_image_config(self):
        # The exact response seen with v1beta responseFormat.image fields.
        # Do not regress to the schema that rejects string 16:9 and 1K.
        import requests
        import base64
        from io import BytesIO
        from PIL import Image
        b = BytesIO()
        Image.new("RGB", (16, 9)).save(b, "PNG")
        encoded = base64.b64encode(b.getvalue()).decode("ascii")
        class Reply:
            status_code = 200
            def raise_for_status(self): pass
            def json(self):
                return {"candidates": [{"content": {"parts": [{
                    "inlineData": {"mimeType": "image/png", "data": encoded}
                }]}}]}
        def server(url, **kwargs):
            config = kwargs["json"]["generationConfig"]
            old = config.get("responseFormat", {}).get("image", {})
            if old:
                message = (
                    "Invalid value at 'generation_config.response_format.image.aspect_ratio' "
                    '(type.googleapis.com/google.ai.generativelanguage.v1beta.ImageResponseFormat.AspectRatio), "16:9"\\n'
                    "Invalid value at 'generation_config.response_format.image.image_size' "
                    '(type.googleapis.com/google.ai.generativelanguage.v1beta.ImageResponseFormat.ImageSize), "1K"'
                )
                raise requests.HTTPError("HTTP 400: " + message)
            self.assertEqual(config["imageConfig"], {"aspectRatio": "16:9"})
            self.assertNotIn("imageSize", config["imageConfig"])
            return Reply()
        tool = artwork_tools.ArtworkTool(
            "gemini", "gemini-3.1-flash-lite-image", "fake-api-key"
        )
        with patch.object(artwork_tools.requests, "post", side_effect=server) as post:
            data = artwork_tools._gemini_image("Edit my 16:9 frame", tool)
        self.assertTrue(data.startswith(bytes((137, 80, 78, 71))))
        self.assertEqual(post.call_count, 1)

    def test_openai_400_reports_provider_error_code_message_and_safe_model(self):
        import requests
        tool = artwork_tools.ArtworkTool("openai", "gpt-image-2.5-flare", "sk-secret")
        class Reply:
            status_code = 400
            def raise_for_status(self):
                raise requests.HTTPError("400 Client Error: Bad Request for url")
            def json(self):
                return {"error": {"message": "Unsupported image size 9999x9999",
                                  "type":"invalid_request_error",
                                  "code":"invalid_size", "param":"size"}}
        with patch.object(artwork_tools.requests, "post", return_value=Reply()):
            with self.assertRaisesRegex(artwork_tools.ArtworkAPIError,
                                        "invalid_size.*parameter: size.*Unsupported image size"):
                artwork_tools._openai_image("new frame", tool)

    def test_gemini_400_reports_actual_provider_detail_without_api_key(self):
        import requests
        tool = artwork_tools.ArtworkTool("gemini", "gemini-3.1-flash-lite-image", "secret-key")
        class Reply:
            status_code = 400
            def raise_for_status(self):
                raise requests.HTTPError("400 Client Error")
            def json(self):
                return {"error":{"message":"Invalid field 'imageSize'; secret-key",
                                 "status":"INVALID_ARGUMENT"}}
        with patch.object(artwork_tools.requests, "post", return_value=Reply()):
            with self.assertRaises(artwork_tools.ArtworkAPIError) as raised:
                artwork_tools._gemini_image("make a still", tool)
        self.assertIn("INVALID_ARGUMENT", str(raised.exception))
        self.assertIn("Invalid field", str(raised.exception))
        self.assertNotIn("secret-key", str(raised.exception))

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

    def test_reference_artwork_reaches_gemini_image_provider_with_roles(self):
        from io import BytesIO
        from PIL import Image
        import base64
        buffer = BytesIO()
        Image.new("RGB", (12, 8)).save(buffer, format="PNG")
        payload = buffer.getvalue()
        encoded = base64.b64encode(payload).decode("ascii")
        class Reply:
            def raise_for_status(self): pass
            def json(self):
                return {"candidates": [{"content": {"parts": [
                    {"inlineData": {"data": encoded}}
                ]}}]}
        refs = [{"label": "Reference 1: Hair — identity role",
                 "image": "data:image/png;base64," + encoded}]
        tool = artwork_tools.ArtworkTool("gemini", "gemini-3.1-flash-lite-image", "key")
        with patch.object(artwork_tools.requests, "post", return_value=Reply()) as post:
            result = artwork_tools._gemini_image("Make a new shot", tool, references=refs)
        parts = post.call_args.kwargs["json"]["contents"][0]["parts"]
        self.assertTrue(result.startswith(bytes((137, 80, 78, 71))))
        self.assertIn("identity role", parts[0]["text"])
        self.assertEqual(len(parts), 2)
        self.assertEqual(base64.b64decode(parts[1]["inlineData"]["data"]), payload)

    def test_reference_artwork_reaches_openai_multifile_edit_endpoint(self):
        from io import BytesIO
        from PIL import Image
        import base64
        buffer = BytesIO()
        Image.new("RGB", (12, 8)).save(buffer, format="PNG")
        payload = buffer.getvalue()
        encoded = base64.b64encode(payload).decode("ascii")
        refs = [{"label": "Ref 1: costume — wardrobe", "image":
                 "data:image/png;base64," + encoded}]
        class Reply:
            def raise_for_status(self): pass
            def json(self): return {"data": [{"b64_json": encoded}]}
        tool = artwork_tools.ArtworkTool("openai", "gpt-image-2", "key")
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.png"
            source.write_bytes(payload)
            with patch.object(artwork_tools.requests, "post", return_value=Reply()) as post:
                artwork_tools._openai_image("Edit shirt", tool, str(source), refs)
        self.assertTrue(post.call_args.args[0].endswith("/images/edits"))
        self.assertIn("wardrobe", post.call_args.kwargs["data"]["prompt"])
        file_parts = post.call_args.kwargs["files"]
        self.assertEqual([name for name, _ in file_parts], ["image[]", "image[]"])
        self.assertEqual(file_parts[0][1][2], "image/png")
        self.assertEqual(file_parts[1][1][1], payload)

    def test_reference_images_turn_new_openai_generation_into_reference_edit(self):
        from io import BytesIO
        from PIL import Image
        import base64
        source = BytesIO()
        Image.new("RGB", (9, 6)).save(source, format="PNG")
        encoded = base64.b64encode(source.getvalue()).decode("ascii")
        class Reply:
            def raise_for_status(self): pass
            def json(self): return {"data": [{"b64_json": encoded}]}
        tool = artwork_tools.ArtworkTool("openai", "gpt-image-2", "key")
        with patch.object(artwork_tools.requests, "post", return_value=Reply()) as post:
            artwork_tools._openai_image(
                "Create next frame", tool, references=[
                    {"label": "Previous timeline frame", "image":
                     "data:image/png;base64," + encoded}])
        self.assertTrue(post.call_args.args[0].endswith("/images/edits"))
        self.assertIn("continuity", post.call_args.kwargs["data"]["prompt"])
        self.assertIn("image", post.call_args.kwargs["files"])

    def test_existing_image_edit_calls_gemini_with_source_bytes(self):
        from io import BytesIO
        from PIL import Image
        import base64
        output = BytesIO()
        Image.new("RGB", (12, 8)).save(output, format="PNG")
        encoded = base64.b64encode(output.getvalue()).decode()
        class Reply:
            def raise_for_status(self): pass
            def json(self):
                return {"candidates":[{"content":{"parts":[{
                    "inlineData":{"mimeType":"image/png","data":encoded}
                }]}}]}
        tool = artwork_tools.ArtworkTool("gemini", "gemini-3.1-flash-lite-image", "key")
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "original.png"
            source.write_bytes(output.getvalue())
            with patch.object(artwork_tools.requests, "post", return_value=Reply()) as post:
                result = artwork_tools._gemini_image("Edit fur", tool, str(source))
            self.assertTrue(result.startswith(b"\x89PNG"))
            parts = post.call_args.kwargs["json"]["contents"][0]["parts"]
            self.assertTrue(parts[0]["text"].startswith("Edit fur"))
            self.assertIn("SOURCE IMAGE", parts[0]["text"])
            self.assertEqual(base64.b64decode(parts[1]["inlineData"]["data"]),
                             output.getvalue())

    def test_existing_image_edit_uses_openai_edit_endpoint_not_generate(self):
        from io import BytesIO
        from PIL import Image
        import base64
        output = BytesIO()
        Image.new("RGB", (12, 8)).save(output, format="PNG")
        class Reply:
            def raise_for_status(self): pass
            def json(self): return {"data":[{"b64_json":base64.b64encode(output.getvalue()).decode()}]}
        tool = artwork_tools.ArtworkTool("openai", "gpt-image-2", "key")
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "frame.png"
            source.write_bytes(output.getvalue())
            with patch.object(artwork_tools.requests, "post", return_value=Reply()) as post:
                result = artwork_tools._openai_image("Edit frame", tool, str(source))
            self.assertTrue(result.startswith(b"\x89PNG"))
            self.assertTrue(post.call_args.args[0].endswith("/images/edits"))
            self.assertIn("image", post.call_args.kwargs["files"])
            self.assertEqual(post.call_args.kwargs["data"]["model"], "gpt-image-2")

    def test_gemini_flash_reasoning_model_can_plan_segments(self):
        self.assertTrue(segment_plan.supports_structured_edit("gemini", "gemini-3.5-flash"))
        self.assertTrue(segment_plan.supports_structured_edit("gemini", "gemini-3.1-flash-lite"))
        self.assertFalse(segment_plan.supports_structured_edit("gemini", "gemini-3.1-flash-lite-image"))


if __name__ == "__main__":
    unittest.main()
