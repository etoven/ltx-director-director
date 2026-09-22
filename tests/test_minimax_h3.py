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

    @staticmethod
    def response_prompt(cues, continuous_lines=None):
        lines = continuous_lines or [f"{cue} The existing motion carries forward through a gradual physical change." for cue in cues]
        return "\n".join((
            "continuous_video:",
            "The same subject, environment, lighting, and coherent camera path persist throughout.",
            *lines,
            "",
            "soundscape:",
            "None specified.",
            "",
            "music:",
            "None.",
        ))

    @staticmethod
    def continuity_plan(count):
        return {
            "persistentAnchors": ["The same subject and spatial environment persist."],
            "bridges": [
                {
                    "fromSegment": index,
                    "toSegment": index + 1,
                    "divergence": "high" if index == 1 else "medium",
                    "progressiveChange": "Momentum and pose evolve progressively across the boundary.",
                    "boundaryState": "The subject is midway through the evolving pose.",
                    "carriedMotion": "The same direction and velocity continue through the cue.",
                    "resolution": "carry_forward" if index == 1 else "reach",
                }
                for index in range(1, count)
            ],
        }

    def build_with_prompt(self, segments, prompt_text, continuity_plan=None):
        response = json.dumps({
            "continuityPlan": self.continuity_plan(len(segments)) if continuity_plan is None else continuity_plan,
            "prompt": prompt_text,
        })
        with patch.object(ai, "_provider_raw", return_value=response):
            return ai.build_minimax_h3_prompt(
                segments, "gemini", "gemini-3.5-flash-lite", "unused", "", "", False, False, True,
            )

    def test_timestamp_format_uses_minutes_seconds_milliseconds(self):
        self.assertEqual(ai._minimax_timestamp(0), "00:00:000")
        self.assertEqual(ai._minimax_timestamp(12.5), "00:12:500")
        self.assertEqual(ai._minimax_timestamp(65.125), "01:05:125")

    def test_template_has_exact_dynamic_cue_and_bridge_counts(self):
        for count in (1, 3, 7):
            with self.subTest(count=count):
                rules = ai._minimax_h3_rules(self.segments(count), "", "", True, False, True)
                template = rules.split(f"PRODUCTION-PROMPT TEMPLATE FOR THIS {count}-INTERVAL TIMELINE", 1)[1]
                template = template.split("Before returning", 1)[0]
                cues = re.findall(r"^\d{2,}:\d{2}:\d{3} ", template, re.MULTILINE)
                self.assertEqual(len(cues), count)
                self.assertEqual(rules.count('"fromSegment"'), max(0, count - 1))
                self.assertNotIn("six segments", rules.casefold())
                self.assertNotIn("[Static shot]", rules)
                self.assertIn("the number of cue lines is generated from the input timeline", rules)

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
        rules = ai._minimax_h3_rules(self.segments(4), "", "", True, False, True)
        self.assertIn("video reference contributes its full temporal behavior", rules.casefold())

    def test_accepts_private_bridge_plan_and_returns_only_prompt(self):
        segments = self.segments(3)
        prompt_text = self.response_prompt(["00:00:000", "00:02:500", "00:05:000"])
        result = self.build_with_prompt(segments, prompt_text)
        self.assertEqual(result, prompt_text)
        self.assertNotIn("continuityPlan", result)
        self.assertNotIn("Picture", result)

    def test_rejects_wrong_or_missing_cues(self):
        segments = self.segments(3)
        with self.assertRaises(ai.AIResponseFormatError):
            self.build_with_prompt(segments, self.response_prompt(["00:00:000", "00:02:500"]))

    def test_rejects_invalid_or_missing_bridge_plan(self):
        segments = self.segments(3)
        prompt_text = self.response_prompt(["00:00:000", "00:02:500", "00:05:000"])
        invalid_plans = (
            {},
            {"persistentAnchors": ["same subject"], "bridges": []},
            {
                "persistentAnchors": ["same subject"],
                "bridges": [
                    {"fromSegment": 1, "toSegment": 2, "divergence": "extreme", "progressiveChange": "change", "boundaryState": "state", "carriedMotion": "motion", "resolution": "reach"},
                    {"fromSegment": 2, "toSegment": 3, "divergence": "low", "progressiveChange": "change", "boundaryState": "state", "carriedMotion": "motion", "resolution": "reach"},
                ],
            },
        )
        for plan in invalid_plans:
            with self.subTest(plan=plan):
                with self.assertRaises(ai.AIResponseFormatError):
                    self.build_with_prompt(segments, prompt_text, plan)

    def test_rejects_structural_labels_edit_commands_and_public_analysis(self):
        segments = self.segments(1)
        invalid_lines = (
            "[Shot 1] 00:00:000 Continuous action.",
            "00:00:000 Matching <Picture 1>, continuous action.",
            "00:00:000 [Static shot] Continuous action.",
            "00:00:000 The camera cuts to the transformed subject.",
        )
        for line in invalid_lines:
            with self.subTest(line=line):
                with self.assertRaises(ai.AIResponseFormatError):
                    self.build_with_prompt(segments, self.response_prompt(["00:00:000"], [line]))
        legacy_prompt = self.response_prompt(["00:00:000"]) + "\nretention_analysis:\nPicture is preserved."
        with self.assertRaises(ai.AIResponseFormatError):
            self.build_with_prompt(segments, legacy_prompt)

    def test_refinement_uses_edited_prompt_and_private_instructions(self):
        segments = self.segments(2)
        current = self.response_prompt(["00:00:000", "00:02:500"])
        edited = current.replace("gradual physical change", "slow shoulder growth that preserves the user's revised motion")
        instructions = "Keep my revised shoulder motion, then make the final settling action less abrupt."
        refined = current.replace("gradual physical change", "slow shoulder growth flows continuously into a gentle settling action")
        response = json.dumps({
            "continuityPlan": self.continuity_plan(2),
            "prompt": refined,
        })
        captured = {}

        def provider(inputs, provider, model, key, rules, timeout):
            captured["rules"] = rules
            return response

        with patch.object(ai, "_provider_raw", side_effect=provider):
            result = ai.refine_minimax_h3_prompt(
                segments, "gemini", "gemini-3.5-flash-lite", "unused", "", "",
                False, False, True, edited, instructions,
            )

        self.assertEqual(result, refined)
        self.assertIn(edited, captured["rules"])
        self.assertIn(instructions, captured["rules"])
        self.assertIn("CURRENT EDITOR TEXT IS AUTHORITATIVE", captured["rules"])
        self.assertIn("Never quote, summarize, mention, or append", captured["rules"])
        self.assertNotIn(instructions, result)

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
