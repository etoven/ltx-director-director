"""Show useful decoded JSON fields before the response has finished arriving."""
import json
import unittest

from ltx_prompt_director.stream_json import live_fields, structured_stream_html


class LiveJsonPresenterTests(unittest.TestCase):
    def test_partial_json_field_is_readable_before_final_closing_brace(self):
        raw = '{"prompt":"subject_definitions\\n<Subject 1> is'
        fields = live_fields(raw)
        self.assertEqual(fields[0][0], "Production prompt")
        self.assertEqual(fields[0][1], "subject_definitions\n<Subject 1> is")
        self.assertFalse(fields[0][2])
        view = structured_stream_html(raw)
        self.assertIn("subject_definitions", view)
        self.assertIn("&lt;Subject 1&gt;", view)
        self.assertNotIn('{"prompt":', view)
        self.assertNotIn("&#92;n", view)

    def test_escaped_quotes_and_mid_unicode_escape_defer_without_corruption(self):
        raw = '{"prompt":"A \\"wolf\\" scene\\nWait for \\u00'
        field = live_fields(raw)[0]
        self.assertEqual(field[1], 'A "wolf" scene\nWait for ')
        self.assertFalse(field[2])
        done = '{"prompt":"A \\"wolf\\" scene\\nWait for \\u00e9"}'
        self.assertEqual(live_fields(done)[0][1], 'A "wolf" scene\nWait for é')
        self.assertTrue(live_fields(done)[0][2])

    def test_multi_segment_json_shows_each_prompt_separately(self):
        payload = json.dumps({
            "segments": [
                {"duration": 2.5, "prompt": "Opening close-up"},
                {"duration": 3.0, "prompt": "Finishing transformation"},
            ],
            "globalPrompt": "Preserve lighting",
        })
        fields = live_fields(payload)
        self.assertEqual([x[0] for x in fields],
                         ["Duration", "Production prompt", "Duration", "Production prompt", "Global direction"])
        view = structured_stream_html(payload, complete=True)
        self.assertIn("Opening close-up", view)
        self.assertIn("Finishing transformation", view)
        self.assertIn("Global direction", view)
        self.assertNotIn(r'\"Opening close-up\"', view)

    def test_non_json_stream_shows_text_directly(self):
        self.assertEqual(live_fields("plain model text"), [("Generated text", "plain model text", False)])


if __name__ == "__main__":
    unittest.main()
