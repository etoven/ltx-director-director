import json
import unittest

from ltx_prompt_director.ai import AIResponseFormatError, _extract_minimax_h3_prompt


class MiniMaxStructuredResponseTests(unittest.TestCase):
    def test_section_response_from_reported_failure(self):
        fields = {
            'reference_use': 'Image1 defines identity.',
            'continuity': 'Keep the camera stationary.',
            'scene': 'A wet shower.',
            'timed_action': '00:00:00:00 - 00:00:03:00: She raises her hand.',
            'sound': 'Running water.',
            'avoid': 'No cuts.',
        }
        output = _extract_minimax_h3_prompt(json.dumps(fields), [])
        for key, value in fields.items():
            self.assertIn('[' + key.replace('_', ' ').upper() + ']\n' + value, output)

    def test_section_lists_preserve_prose_and_timecodes(self):
        output = _extract_minimax_h3_prompt(json.dumps({
            'frame_use': ['Image1 starts the sequence.', 'Image2 ends it.'],
            'timed_action': ['00:00:00:00 - 00:00:03:00: Move.',
                             '00:00:03:00 - 00:00:06:00: Stop.'],
        }))
        self.assertIn('[FRAME USE]\nImage1 starts the sequence.\nImage2 ends it.', output)
        self.assertIn('00:00:03:00 - 00:00:06:00: Stop.', output)

    def test_singular_structured_actions_use_existing_timing_validation(self):
        output = _extract_minimax_h3_prompt(json.dumps({
            'scene': 'A shower.',
            'timed_action': [{'start': '00:00:00:00', 'end': '00:00:03:00', 'action': 'Move.'}],
            'sound': 'Water.',
        }), [])
        self.assertIn('[SCENE]\nA shower.', output)
        self.assertIn('[TIMED ACTION]\n00:00:00:00 - 00:00:03:00: Move.', output)
        self.assertIn('[SOUND]\nWater.', output)

    def test_existing_prompt_takes_precedence(self):
        self.assertEqual(_extract_minimax_h3_prompt(json.dumps({
            'prompt': 'Existing brief', 'scene': 'Other scene', 'timed_action': 'Other action',
        })), 'Existing brief')

    def test_missing_or_invalid_actions_are_rejected(self):
        for fields in ({'scene': 'Only a scene'}, {'timed_action': []},
                       {'scene': 'Shower', 'timed_action': [{'start': 3, 'end': 1, 'action': 'Move'}]}):
            with self.subTest(fields=fields), self.assertRaises(AIResponseFormatError):
                _extract_minimax_h3_prompt(json.dumps(fields), [])


if __name__ == '__main__':
    unittest.main()
