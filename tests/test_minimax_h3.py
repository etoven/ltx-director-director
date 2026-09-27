import json
import base64
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image

from ltx_prompt_director import ai
from ltx_prompt_director.models import Segment


class MiniMaxH3PromptTests(unittest.TestCase):
    def segments(self):
        return [
            Segment('opening.png', '', '', 'image', 'start', 'She raises her hand.', 3.0),
            Segment('motion.mp4', '', '', 'video', 'start', 'She turns slowly.', 3.0),
            Segment('direction', '', '', 'text', 'text', 'The movement continues.', 3.0),
            Segment('ending.png', '', '', 'image', 'end', 'She faces the camera.', 3.86),
        ]

    def test_timestamp_format(self):
        self.assertEqual(ai._minimax_timestamp(12.86), '00:12:860')
        self.assertEqual(ai._minimax_timestamp(65.125), '01:05:125')

    def test_frames_rules_use_reference_style_sections_and_exact_ranges(self):
        rules = ai._minimax_h3_rules(self.segments(), 'Intent', 'Global', True, False, True)
        for section in ('[FRAME USE]', '[CONTINUITY]', '[SCENE]', '[TIMED ACTION]', '[SOUND]', '[AVOID]'):
            self.assertIn(section, rules)
        self.assertLess(rules.index('[FRAME USE]'), rules.index('[TIMED ACTION]'))
        for interval in ('00:00:000 - 00:03:000', '00:03:000 - 00:06:000',
                         '00:06:000 - 00:09:000', '00:09:000 - 00:12:860'):
            self.assertIn(interval, rules)
        self.assertIn('Image1 (opening.png)', rules)
        self.assertIn('Image2 (ending.png)', rules)
        self.assertIn('end frame reached at 00:12:860', rules)
        self.assertIn('start frame at 00:00:000', rules)
        self.assertIn('Video1 (motion.mp4)', rules)
        self.assertIn('Frame images are conditioning checkpoints', rules)
        self.assertNotIn('exactly one timed Frame bridge', rules)
        self.assertNotIn('sentence quota', rules.split('Use only facts')[0])

    def test_frame_input_respects_start_and_end_roles(self):
        inputs = ai._minimax_h3_inputs(self.segments(), frame_checkpoints=True)
        self.assertIn('opening visual anchor', inputs[0]['continuity_function'])
        self.assertIn('end-frame target reached by this interval\'s end', inputs[-1]['continuity_function'])
        self.assertEqual([item['cue_timestamp'] for item in inputs],
                         ['00:00:000', '00:03:000', '00:06:000', '00:09:000'])
        self.assertTrue(inputs[-1]['frame_mode_checkpoint'])
        self.assertNotIn('recommended_detail', inputs[-1])

    def test_first_end_frame_is_not_mistaken_for_start_frame(self):
        end = Segment('last.png', '', '', 'image', 'end', '', 4.0)
        inputs = ai._minimax_h3_inputs([end], frame_checkpoints=True)
        self.assertIn('end-frame target', inputs[0]['continuity_function'])
        self.assertIn('00:00:000 - 00:04:000', ai._minimax_h3_rules([end], '', '', False, False, True))

    def test_builder_returns_reference_style_brief_verbatim(self):
        result = '[FRAME USE] Image1 is the opening frame.\n\n[TIMED ACTION]\n00:00:000 - 00:03:000: She moves.\n\n[SOUND] Water. '
        with patch.object(ai, '_provider_raw', return_value=json.dumps({'prompt': result})) as provider:
            output = ai.build_minimax_h3_prompt(self.segments(), 'gemini', 'model', '', '', '', False, False, True)
        self.assertEqual(output, result.strip())
        self.assertIn('[FRAME USE]', provider.call_args.args[4])
        self.assertTrue(provider.call_args.args[0][0]['frame_mode_checkpoint'])

    def test_two_untimed_references_follow_timed_frames_with_distinct_roles(self):
        buffer = io.BytesIO()
        Image.new('RGB', (8, 8), 'blue').save(buffer, format='PNG')
        encoded = 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode()
        refs = [{'name': 'face.png', 'role': 'identity', 'image': encoded, 'notes': 'face only'},
                {'name': 'claws.png', 'role': 'object', 'image': encoded, 'notes': 'claw shape'}]
        rules = ai._minimax_h3_rules(self.segments(), '', '', False, False, True, refs)
        self.assertIn('Image3 (face.png): untimed identity reference; notes: face only', rules)
        self.assertIn('Image4 (claws.png): untimed object reference; notes: claw shape', rules)
        self.assertIn('one blank line between sections', rules)
        self.assertIn('each range on its own line', rules)
        self.assertEqual(rules.split('UNTIMED REFERENCE IMAGES', 1)[0].count('00:00:000 - 00:03:000'), 1)
        with patch.object(ai, '_provider_raw', return_value='{"prompt":"[FRAME USE] Done."}') as provider:
            ai.build_minimax_h3_prompt(self.segments(), 'gemini', 'model', '', '', '', False, False, True,
                                       reference_images=refs)
        sent = provider.call_args.args[0]
        self.assertEqual(len(sent), len(self.segments()) + 2)
        self.assertEqual([item['label'] for item in sent[-2:]], ['Image3', 'Image4'])
        self.assertEqual([item['role'] for item in sent[-2:]], ['identity', 'object'])
        self.assertTrue(all('reference_slot' in item and 'start_time' not in item for item in sent[-2:]))
        self.assertTrue(all(item['image'] == encoded for item in sent[-2:]))
        with patch.object(ai, '_provider_raw', return_value='{"prompt":"[FRAME USE] Refined."}') as provider:
            ai.refine_minimax_h3_prompt(self.segments(), 'gemini', 'model', '', '', '', False, False,
                                        True, '[FRAME USE] Current.', 'Keep it.', reference_images=refs)
        self.assertEqual(len(provider.call_args.args[0]), len(sent))
        self.assertIn('SECONDARY CONTINUITY EVIDENCE', provider.call_args.args[0][-1]['guidance_priority'])

    def test_bad_transport_retries(self):
        for response in ('not json', '{}', '{"prompt": ""}'):
            with self.subTest(response=response), patch.object(ai, '_provider_raw', return_value=response):
                with self.assertRaises(ai.AIResponseFormatError):
                    ai.build_minimax_h3_prompt(self.segments(), 'gemini', 'model', '', '', '', False, False, True)

    def test_reference_workflow_remains_untimed_reference_brief(self):
        rules = ai._minimax_h3_reference_rules(self.segments(), '', '', False, False, True)
        self.assertIn('[REFERENCE USE]', rules)
        self.assertIn('Mixed references (R2V)', rules)
        self.assertNotIn('[FRAME USE]', rules)

    def test_refinement_keeps_private_edits_and_section_style(self):
        with patch.object(ai, '_provider_raw', return_value=json.dumps({'prompt': '[FRAME USE] Edited.'})) as provider:
            result = ai.refine_minimax_h3_prompt(self.segments(), 'gemini', 'model', '', '', '',
                                                  False, False, True, '[FRAME USE] Current.', 'Preserve my camera.')
        self.assertEqual(result, '[FRAME USE] Edited.')
        self.assertIn('PRIVATE REFINEMENT INSTRUCTIONS are the highest-priority', provider.call_args.args[4])
        self.assertIn('Preserve my camera.', provider.call_args.args[4])
        self.assertIn('production-brief sections', provider.call_args.args[4])

    def test_cache_key_tracks_prompt_duration_and_original_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            first_path, second_path = Path(directory) / 'first.png', Path(directory) / 'copy.png'
            first_path.write_bytes(b'same-media-content')
            second_path.write_bytes(b'same-media-content')
            first = Segment('frame.png', str(first_path), str(first_path), prompt='Motion', duration=2.5, id='stable-id')
            restored = Segment('frame.png', str(second_path), str(second_path), prompt='Motion', duration=2.5, id='stable-id')
            def key(segment):
                return ai.minimax_h3_cache_key([segment], 'gemini', 'model', 'Intent', 'Global', True, False, True)
            self.assertEqual(key(first), key(restored))
            restored.prompt = 'Changed'
            self.assertNotEqual(key(first), key(restored))
            restored.prompt = first.prompt
            restored.duration = 3.0
            self.assertNotEqual(key(first), key(restored))
            restored.duration = first.duration
            second_path.write_bytes(b'different-media-content')
            self.assertNotEqual(key(first), key(restored))


if __name__ == '__main__':
    unittest.main()
