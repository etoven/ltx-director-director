import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ltx_prompt_director import ai
from ltx_prompt_director.models import Segment


class MiniMaxH3PromptTests(unittest.TestCase):
    def segments(self, count=3):
        kinds = ("image", "video", "text")
        return [
            Segment(
                f"segment-{index + 1}", "", "", kinds[index % len(kinds)],
                "text" if kinds[index % len(kinds)] == "text" else ("end" if index == 3 else "start"),
                f"Continuous action {index + 1}.", 2.5,
            )
            for index in range(count)
        ]

    def build_with_prompt(self, segments, prompt_text):
        _, slots = ai._minimax_frame_slots(segments)
        fields = {"opening": "Opening view.", "soundscape": "Water sounds.", "music": "None."}
        fields.update({key: prompt_text for key, _, _ in slots})
        with patch.object(ai, "_provider_raw", return_value=json.dumps(fields)):
            return ai.build_minimax_h3_prompt(
                segments, "gemini", "gemini-3.5-flash-lite", "unused", "", "", False, False, True,
            )

    def test_timestamp_format_uses_minutes_seconds_milliseconds(self):
        self.assertEqual(ai._minimax_timestamp(0), "00:00:000")
        self.assertEqual(ai._minimax_timestamp(12.5), "00:12:500")
        self.assertEqual(ai._minimax_timestamp(65.125), "01:05:125")
        self.assertEqual(ai._minimax_reference_timestamp(65.125), "01:05.125")

    def test_detail_standard_scales_with_interval_duration(self):
        self.assertEqual(ai.minimax_interval_detail_standard(2.5), (2, 25))
        self.assertEqual(ai.minimax_interval_detail_standard(5.0), (3, 50))
        self.assertEqual(ai.minimax_interval_detail_standard(8.0), (4, 50))

    def test_master_prompt_has_dynamic_action_cues_and_required_timed_bridges(self):
        for count in (1, 3, 7):
            with self.subTest(count=count):
                items = self.segments(count)
                rules = ai._minimax_h3_rules(items, "", "", True, False, True)
                template = rules.split("OUTPUT TEMPLATE — return these three lowercase sections in order:", 1)[1]
                template = template.split("soundscape:", 1)[0]
                cues = re.findall(r"^\d{2,}:\d{2}:\d{3} \{describe", template, re.MULTILINE)
                self.assertEqual(len(cues), count)
                visual_count = sum(item.kind in ("image", "video") for item in items)
                self.assertEqual(len(re.findall(r"^\d{2,}:\d{2}:\d{3} Frame bridge:", rules, re.MULTILINE)), max(visual_count - 1, 0))
                self.assertIn("Fill ALL of them", rules)
                self.assertIn("Do not omit or merge slots", rules)
                self.assertIn("exactly one top-level field", rules)

    def test_mixed_media_inputs_have_interval_roles_and_video_awareness(self):
        inputs = ai._minimax_h3_inputs(self.segments(4))
        self.assertEqual([item["cue_timestamp"] for item in inputs], ["00:00:000", "00:02:500", "00:05:000", "00:07:500"])
        self.assertEqual([item["next_cue_timestamp"] for item in inputs], ["00:02:500", "00:05:000", "00:07:500", None])
        self.assertEqual(inputs[0]["picture_number"], 1)
        self.assertIn("opening visual anchor", inputs[0]["continuity_function"])
        self.assertEqual(inputs[1]["video_number"], 1)
        self.assertIn("temporal motion evidence", inputs[1]["continuity_function"])
        self.assertIn("action instruction", inputs[2]["continuity_function"])
        self.assertIn("resolve by this interval's end", inputs[3]["continuity_function"])
        self.assertTrue(all("complete action path" in item["action_trajectory_requirement"] for item in inputs))
        self.assertTrue(all("exactly one timed Frame bridge" in item["camera_continuity_requirement"] for item in inputs))

    def test_bridge_windows_include_video_to_image_and_skip_text_only_cues(self):
        items = self.segments(4)  # image, video, text, image
        rules = ai._minimax_h3_rules(items, "", "", False, False, True)
        self.assertIn("2 required Frame bridges", rules)
        self.assertIn("00:01:250 Frame bridge:", rules)
        self.assertIn("00:06:250 Frame bridge:", rules)
        self.assertLess(rules.index("00:05:000 {describe"), rules.index("00:06:250 Frame bridge:"))
        self.assertLess(rules.index("00:06:250 Frame bridge:"), rules.index("00:07:500 {describe"))

    def test_nearly_identical_images_still_have_a_grounded_bridge(self):
        images = [self.segments(1)[0], self.segments(1)[0]]
        rules = ai._minimax_h3_rules(images, "", "", False, False, True)
        self.assertIn("1 required Frame bridges", rules)
        self.assertIn("00:01:250 Frame bridge:", rules)
        self.assertIn("Never invent a zoom, turn, fall, or other event to fill a bridge", rules)

    def test_subject_pose_change_gets_timed_action_bridge_without_camera_motion(self):
        rules = ai._minimax_h3_rules(self.segments(2), "", "", True, False, True)
        self.assertIn("pose, anatomy, expression", rules)
        self.assertIn("camera path or physical action stages", rules)
        self.assertIn("Keep simultaneous camera and subject action moving together", rules)

    def test_frontal_to_profile_example_directs_a_separate_timed_bridge(self):
        segments = self.segments(3)
        segments[0].duration = 1.0
        segments[1].duration = 7.0
        segments[2].kind = "image"
        rules = ai._minimax_h3_rules(segments, "", "", True, False, True)
        self.assertIn("00:01:000", rules)
        self.assertIn("00:08:000", rules)
        self.assertIn("00:04:500 Frame bridge:", rules)
        self.assertIn("00:00:500", rules)
        self.assertIn("2 required Frame bridges", rules)
        self.assertLess(rules.index("00:00:500 Frame bridge:"), rules.index("00:01:000 {describe"))
        self.assertLess(rules.index("00:04:500 Frame bridge:"), rules.index("00:08:000 {describe"))

    def test_reference_master_uses_workflow_brief(self):
        rules = ai._minimax_h3_reference_rules(self.segments(4), "", "", True, True, False)
        self.assertIn("Mixed references (R2V)", rules)
        self.assertIn("[REFERENCE USE]", rules)
        self.assertIn("[TIMED ACTION]", rules)
        self.assertIn("Image1", rules)
        self.assertIn("Video1", rules)
        self.assertNotIn("retention_analysis:", rules)
        self.assertNotIn("350–500", rules)

    def test_reference_builder_returns_prompt_without_semantic_validation(self):
        segments = self.segments(2)
        response = json.dumps({"prompt": "User-controlled full-reference output"})
        with patch.object(ai, "_provider_raw", return_value=response) as provider:
            result = ai.build_minimax_h3_reference_prompt(
                segments, "gemini", "gemini-3.5-flash-lite", "unused", "", "", False, False, True,
            )
        self.assertEqual(result, "User-controlled full-reference output")
        self.assertIn("[REFERENCE USE]", provider.call_args.args[4])

    def test_returns_prompt_without_semantic_validation(self):
        segments = self.segments(3)
        unconventional = "A short prompt with no sections, timestamps, camera terms, or continuity metadata."
        result = self.build_with_prompt(segments, unconventional)
        self.assertIn("00:00:000 " + unconventional, result)
        self.assertIn("00:01:250 Frame bridge: " + unconventional, result)
        self.assertIn("00:02:500 " + unconventional, result)

    def test_only_rejects_unreadable_transport_or_missing_structural_fields(self):
        segments = self.segments(1)
        for response in ("not json", "{}", '{"prompt": ""}'):
            with self.subTest(response=response), patch.object(ai, "_provider_raw", return_value=response):
                with self.assertRaises(ai.AIResponseFormatError):
                    ai.build_minimax_h3_prompt(
                        segments, "gemini", "gemini-3.5-flash-lite", "unused", "", "", False, False, True,
                    )

    def test_project_shape_assembles_both_bridges_and_reached_image_states(self):
        segments = self.segments(3)
        segments[0].kind = "video"
        segments[0].duration = 1.0
        segments[1].kind = "image"
        segments[1].role = "end"
        segments[1].duration = 7.0
        segments[2].kind = "image"
        segments[2].role = "end"
        segments[2].duration = 7.0
        fields = {
            "opening": "Woman under shower water.",
            "cue_1": "Her hand falls away as the zoom starts and forehead tenses.",
            "bridge_after_1": "The camera closes in as the fissure starts and eyes change.",
            "cue_2": "The fissure and lion eyes are already visible as her temples swell.",
            "bridge_after_2": "The bulges rise and ears emerge while her eyes close.",
            "cue_3": "The lion ears are fully protruding and water runs over them.",
            "soundscape": "Shower and strained breathing.",
            "music": "None.",
        }
        with patch.object(ai, "_provider_raw", return_value=json.dumps(fields)) as provider:
            prompt = ai.build_minimax_h3_prompt(
                segments, "gemini", "gemini-3.5-flash-lite", "unused", "", "", True, False, True,
            )
        lines = [line for line in prompt.splitlines() if line[:2].isdigit()]
        self.assertEqual([line[:9] for line in lines], ["00:00:000", "00:00:500", "00:01:000", "00:04:500", "00:08:000"])
        self.assertEqual(sum("Frame bridge:" in line for line in lines), 2)
        self.assertIn("lion eyes are already visible", prompt)
        self.assertIn("lion ears are fully protruding", prompt)
        self.assertIn("bridge_after_2", provider.call_args.args[4])
        self.assertIn("already shows its reached state", provider.call_args.args[4])
        inputs = provider.call_args.args[0]
        self.assertIn("already reached at its cue timestamp", inputs[1]["continuity_function"])
        self.assertIn("already reached at its cue timestamp", inputs[2]["continuity_function"])
        self.assertTrue(inputs[1]["frame_mode_checkpoint"])
        self.assertTrue(inputs[2]["frame_mode_checkpoint"])

    def test_missing_bridge_field_retries_instead_of_silently_omitting_it(self):
        items = self.segments(2)
        response = json.dumps({"opening": "Scene.", "cue_1": "Action.", "cue_2": "Action.", "soundscape": "Water.", "music": "None."})
        with patch.object(ai, "_provider_raw", return_value=response):
            with self.assertRaisesRegex(ai.AIResponseFormatError, "bridge_after_1"):
                ai.build_minimax_h3_prompt(items, "gemini", "gemini-3.5-flash-lite", "unused", "", "", False, False, True)

    def test_refinement_uses_edited_prompt_and_private_instructions(self):
        segments = self.segments(2)
        edited = "User-edited current MiniMax prompt."
        instructions = "Keep my action, pan down slowly, then zoom out."
        refined = "User-edited action continues while the camera pans down slowly, then zooms out."
        captured = {}

        def provider(inputs, provider, model, key, rules, timeout):
            captured["inputs"] = inputs
            captured["rules"] = rules
            return json.dumps({"prompt": refined})

        with patch.object(ai, "_provider_raw", side_effect=provider):
            result = ai.refine_minimax_h3_prompt(
                segments, "gemini", "gemini-3.5-flash-lite", "unused", "", "",
                False, False, True, edited, instructions,
            )

        self.assertEqual(result, refined)
        self.assertIn(edited, captured["rules"])
        self.assertIn(instructions, captured["rules"])
        self.assertIn("PRIVATE REFINEMENT INSTRUCTIONS are the highest-priority", captured["rules"])
        self.assertIn("CURRENT EDITOR PROMPT is the authoritative", captured["rules"])
        self.assertIn("never rebuild it from the references", captured["rules"])
        self.assertTrue(all("SECONDARY CONTINUITY EVIDENCE ONLY" in item["guidance_priority"] for item in captured["inputs"]))

    def test_cache_key_is_stable_across_paths_and_changes_with_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            first_path = Path(directory) / "first.png"
            second_path = Path(directory) / "restored.png"
            first_path.write_bytes(b"same-media-content")
            second_path.write_bytes(b"same-media-content")
            first = Segment("frame.png", str(first_path), str(first_path), prompt="Motion", duration=2.5, id="stable-id")
            restored = Segment("frame.png", str(second_path), str(second_path), prompt="Motion", duration=2.5, id="stable-id")

            def key(segment):
                return ai.minimax_h3_cache_key([segment], "gemini", "gemini-3.5-flash-lite", "Intent", "Global", True, False, True)

            self.assertEqual(key(first), key(restored))
            restored.prompt = "Changed motion"
            self.assertNotEqual(key(first), key(restored))
            restored.prompt = first.prompt
            restored.duration = 3.0
            self.assertNotEqual(key(first), key(restored))
            restored.duration = first.duration
            second_path.write_bytes(b"different-media-content")
            self.assertNotEqual(key(first), key(restored))


if __name__ == "__main__":
    unittest.main()
