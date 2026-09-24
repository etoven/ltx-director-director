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
        with patch.object(ai, "_provider_raw", return_value=json.dumps({"prompt": prompt_text})):
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
                rules = ai._minimax_h3_rules(self.segments(count), "", "", True, False, True)
                template = rules.split(f"PRODUCTION-PROMPT TEMPLATE FOR THIS {count}-INTERVAL TIMELINE", 1)[1]
                template = template.split("Before returning", 1)[0]
                cues = re.findall(r"^\d{2,}:\d{2}:\d{3} ", template, re.MULTILINE)
                self.assertEqual(len(cues), count)
                self.assertNotIn('"continuityPlan"', rules)
                self.assertIn("each frame directs its matching interval", rules)
                self.assertIn("compare every pair of adjacent visual checkpoints", rules)
                visual_count = sum(item.kind in ("image", "video") for item in self.segments(count))
                self.assertIn(f"add exactly {max(visual_count - 1, 0)} intermediate timestamped Frame bridge", rules)
                self.assertIn("for EVERY pair of successive visual checkpoints", rules)
                self.assertIn("MM:SS:mmm Frame bridge:", rules)
                self.assertIn("subject's action progressing during camera travel", rules)
                self.assertIn("near-identical images need only a brief, grounded account", rules)
                self.assertIn("a fall, collapse, substantial head tilt", rules)
                self.assertIn("Combine simultaneous camera, subject, and environmental motion in that same bridge", rules)
                self.assertIn("objects, clothing, environment, lighting", rules)
                self.assertIn("do not force a camera path", rules)
                self.assertIn("zooms out", rules)
                self.assertIn("pans down", rules)
                self.assertIn("dollies backward-left", rules)
                self.assertIn("HARD INTERVAL-DETAIL CONTRACT", rules)
                self.assertIn("complete, punctuated sentences", rules)
                self.assertIn("physical causality and execution", rules)
                self.assertIn("weight transfer", rules)
                self.assertIn("secondary motion", rules)
                self.assertIn("PRIVATE FRAME-TRANSITION PASS", rules)
                self.assertIn("PRIVATE WHOLE-SEQUENCE ACTION-TRAJECTORY PASS", rules)
                self.assertIn("examine every supplied frame", rules)
                self.assertIn("complete initial-to-final action trajectory", rules)
                self.assertIn("fabric pulls taut", rules)
                self.assertIn("individual threads snap", rules)
                self.assertIn("hair or fur changes", rules)
                self.assertIn("strands emerge or lengthen", rules)
                self.assertIn("never plan an interval in isolation", rules)
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
        self.assertIn("required visual-checkpoint bridge windows: 00:00:000 → 00:02:500; 00:02:500 → 00:07:500", rules)
        self.assertIn("add exactly 2 intermediate timestamped Frame bridge", rules)
        self.assertIn("When text-only cues occur between visual checkpoints", rules)
        self.assertIn("A video-to-image boundary still gets one bridge", rules)

    def test_nearly_identical_images_still_have_a_grounded_bridge(self):
        images = [self.segments(1)[0], self.segments(1)[0]]
        rules = ai._minimax_h3_rules(images, "", "", False, False, True)
        self.assertIn("add exactly 1 intermediate timestamped Frame bridge", rules)
        self.assertIn("Never invent a zoom, pose change, or event merely to fill a required bridge", rules)

    def test_subject_pose_change_gets_timed_action_bridge_without_camera_motion(self):
        rules = ai._minimax_h3_rules(self.segments(2), "", "", True, False, True)
        self.assertIn("substantial head tilt", rules)
        self.assertIn("intermediate pose before the next checkpoint", rules)
        self.assertIn("Keep the camera steady when the frames show only subject movement", rules)
        self.assertIn("stable views remain stable when only the subject or environment changes", rules)

    def test_frontal_to_profile_example_directs_a_separate_timed_bridge(self):
        segments = self.segments(3)
        segments[0].duration = 1.0
        segments[1].duration = 7.0
        rules = ai._minimax_h3_rules(segments, "", "", True, False, True)
        self.assertIn("00:01:000", rules)
        self.assertIn("00:08:000", rules)
        self.assertIn("00:04:000 Frame bridge:", rules)
        self.assertIn("00:00:500", rules)
        self.assertIn("complete continuous zoom in", rules)
        self.assertIn("two distinct timed bridges", rules)
        self.assertIn("A push alone cannot explain a frontal-to-profile checkpoint", rules)
        self.assertIn("bridge belongs on its own line", rules)
        self.assertIn("facial action continues", rules)

    def test_reference_master_uses_official_six_section_contract(self):
        rules = ai._minimax_h3_reference_rules(self.segments(4), "", "", True, True, False)
        headings = (
            "subject_definitions:",
            "summary:",
            "retention_analysis:",
            "detailed_description:",
            "overall_soundscape:",
            "non_diegetic_music:",
        )
        positions = [rules.index(heading) for heading in headings]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("<Picture 1>", rules)
        self.assertIn("<Video 1>", rules)
        self.assertIn("[Shot 1]", rules)
        self.assertIn("MM:SS.mmm", rules)
        self.assertIn("350–500 detailed English words", rules)
        self.assertIn("do not turn every timeline frame into a cut", rules)
        self.assertIn("fully_preserved", rules)
        self.assertIn("attribute_transfer", rules)
        self.assertIn("keyframe completion", rules)
        self.assertIn("reference generation", rules)
        self.assertIn("exactly one top-level field", rules)

    def test_reference_builder_returns_prompt_without_semantic_validation(self):
        segments = self.segments(2)
        response = json.dumps({"prompt": "User-controlled full-reference output"})
        with patch.object(ai, "_provider_raw", return_value=response) as provider:
            result = ai.build_minimax_h3_reference_prompt(
                segments, "gemini", "gemini-3.5-flash-lite", "unused", "", "", False, False, True,
            )
        self.assertEqual(result, "User-controlled full-reference output")
        self.assertIn("REQUIRED SIX-SECTION OUTPUT", provider.call_args.args[4])

    def test_returns_prompt_without_semantic_validation(self):
        segments = self.segments(3)
        unconventional = "A short prompt with no sections, timestamps, camera terms, or continuity metadata."
        self.assertEqual(self.build_with_prompt(segments, unconventional), unconventional)

    def test_only_rejects_unreadable_transport_or_missing_prompt(self):
        segments = self.segments(1)
        for response in ("not json", "{}", '{"prompt": ""}'):
            with self.subTest(response=response), patch.object(ai, "_provider_raw", return_value=response):
                with self.assertRaises(ai.AIResponseFormatError):
                    ai.build_minimax_h3_prompt(
                        segments, "gemini", "gemini-3.5-flash-lite", "unused", "", "", False, False, True,
                    )

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
