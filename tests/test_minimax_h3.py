import json
import re
import unittest
from unittest.mock import patch

from ltx_prompt_director import ai
from ltx_prompt_director.models import Segment


class MiniMaxH3PromptTests(unittest.TestCase):
    def segments(self, count=3):
        kinds = ("image", "video", "text")
        return [
            Segment(
                f"segment-{index + 1}",
                "",
                "",
                kinds[index % len(kinds)],
                "text" if kinds[index % len(kinds)] == "text" else "start",
                f"Continuous action {index + 1}.",
                2.5,
            )
            for index in range(count)
        ]

    @staticmethod
    def response_prompt(cues, detailed_lines=None):
        lines = detailed_lines or [f"{cue} Continuous action carries forward naturally." for cue in cues]
        return "\n".join((
            "subject_definitions:",
            "<Subject 1>: A persistent subject.",
            "",
            "summary:",
            "[keyframe completion + reference generation] One continuous progression.",
            "",
            "retention_analysis:",
            "<Subject 1>: fully_preserved - identity remains continuous.",
            "",
            "detailed_description:",
            "Realistic continuous motion with a persistent environment and camera baseline.",
            *lines,
            "",
            "overall_soundscape:",
            "None specified.",
            "",
            "non_diegetic_music:",
            "None.",
        ))

    def build_with_prompt(self, segments, prompt_text):
        response = json.dumps({"prompt": prompt_text})
        with patch.object(ai, "_provider_raw", return_value=response):
            return ai.build_minimax_h3_prompt(
                segments, "gemini", "gemini-3.5-flash-lite", "unused", "", "", False, False, True,
            )

    def test_timestamp_format_uses_minutes_seconds_milliseconds(self):
        self.assertEqual(ai._minimax_timestamp(0), "00:00:000")
        self.assertEqual(ai._minimax_timestamp(12.5), "00:12:500")
        self.assertEqual(ai._minimax_timestamp(65.125), "01:05:125")

    def test_template_has_exact_dynamic_cue_count(self):
        for count in (1, 3, 7):
            with self.subTest(count=count):
                rules = ai._minimax_h3_rules(self.segments(count), "", "", True, False, True)
                template = rules.split(f"FORMAT TEMPLATE FOR THIS {count}-SEGMENT TIMELINE", 1)[1]
                template = template.split("Before returning", 1)[0]
                cues = re.findall(r"^\d{2,}:\d{2}:\d{3} ", template, re.MULTILINE)
                self.assertEqual(len(cues), count)
                self.assertNotIn("six headings", rules.casefold())
                self.assertNotIn("[Static shot]", rules)
                self.assertIn("ordinary sentence—not a bracketed camera command", rules)

    def test_mixed_media_inputs_are_timed_and_video_aware(self):
        segments = self.segments(3)
        inputs = ai._minimax_h3_inputs(segments)
        self.assertEqual([item["cue_timestamp"] for item in inputs], ["00:00:000", "00:02:500", "00:05:000"])
        self.assertEqual(inputs[0]["picture_number"], 1)
        self.assertEqual(inputs[1]["video_number"], 1)
        rules = ai._minimax_h3_rules(segments, "", "", True, False, True)
        self.assertIn("video reference contributes its full temporal behavior", rules)

    def test_accepts_bare_timestamp_action_lines(self):
        segments = self.segments(3)
        cues = ["00:00:000", "00:02:500", "00:05:000"]
        prompt_text = self.response_prompt(cues)
        self.assertEqual(self.build_with_prompt(segments, prompt_text), prompt_text)

    def test_rejects_wrong_or_missing_cues(self):
        segments = self.segments(3)
        prompt_text = self.response_prompt(["00:00:000", "00:02:500"])
        with self.assertRaises(ai.AIResponseFormatError):
            self.build_with_prompt(segments, prompt_text)

    def test_rejects_structural_labels_and_edit_commands(self):
        segments = self.segments(1)
        invalid_lines = (
            "[Shot 1] 00:00:000 Continuous action.",
            "00:00:000 Matching <Picture 1>, continuous action.",
            "00:00:000 [Static shot] Continuous action.",
            "00:00:000 The camera cuts to the transformed subject.",
        )
        for line in invalid_lines:
            with self.subTest(line=line):
                prompt_text = self.response_prompt(["00:00:000"], [line])
                with self.assertRaises(ai.AIResponseFormatError):
                    self.build_with_prompt(segments, prompt_text)


if __name__ == "__main__":
    unittest.main()
