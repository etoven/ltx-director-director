"""Frame roles and surrounding visuals survive real provider request assembly."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

from ltx_prompt_director import ai
from ltx_prompt_director.media import data_url
from ltx_prompt_director.minimax_reference import timeline_motion_context
from ltx_prompt_director.models import Segment


class FrameMotionContextTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        images = []
        for index, color in enumerate(('red', 'green', 'blue')):
            path = Path(self.folder.name) / f'frame-{index}.png'
            Image.new('RGB', (32, 24), color).save(path)
            images.append(str(path))
        self.segments = [
            Segment('Opening', images[0], images[0], 'image', 'start', 'Hold the opening pose.', 2),
            Segment('Motion', '', '', 'text', 'text', 'Continue the camera pan.', 1),
            Segment('Arrival', images[1], images[1], 'image', 'end', 'Reach the target pose.', 3),
            Segment('Hold', '', '', 'text', 'text', 'Pause without reversing the pan.', 2),
            Segment('Departure', images[2], images[2], 'image', 'start', 'Continue onward.', 4),
        ]

    def test_end_arrival_and_start_departure_use_distinct_anchor_times(self):
        contexts = timeline_motion_context(self.segments)
        target = contexts[2]
        self.assertEqual((target['interval_start'], target['interval_end']), (3, 6))
        self.assertEqual(target['current_visual']['role'], 'end')
        self.assertEqual(target['current_visual']['anchor_time'], 6)
        self.assertEqual(target['previous_visual']['anchor_time'], 0)
        self.assertEqual(target['next_visual']['anchor_time'], 8)
        self.assertEqual(contexts[3]['previous_visual']['segment_id'], self.segments[2].id)
        self.assertEqual(contexts[3]['next_visual']['segment_id'], self.segments[4].id)
        self.segments[2].role = 'start'
        self.assertEqual(timeline_motion_context(self.segments)[2]['current_visual']['anchor_time'], 3)

    def test_first_end_frame_is_never_described_as_the_opening_state(self):
        self.segments[0].role = 'end'
        for checkpoints in (False, True):
            with self.subTest(checkpoints=checkpoints):
                first = ai._minimax_h3_inputs(self.segments, frame_checkpoints=checkpoints)[0]
                self.assertNotIn('opening visual anchor', first['continuity_function'])
                self.assertEqual(first['motion_context']['current_visual']['anchor_time'], 2)
                self.assertEqual(first['cue_timestamp'], '00:00:02:00')

    def test_both_providers_receive_roles_times_and_visual_neighbors_across_text(self):
        original = copy.deepcopy([s.to_dict() for s in self.segments])
        refs = [{'name': 'Style only', 'image': data_url(self.segments[0].media_path), 'role': 'style'}]
        builders = [lambda: ai._refinement_images(self.segments, 2),
                    lambda: ai._minimax_frames_inputs(self.segments, 'gemini', refs),
                    lambda: ai._minimax_reference_inputs(self.segments, 'gemini', refs)]
        for make_inputs in builders:
            inputs = make_inputs()
            target = next(r for r in inputs if r.get('segment_id') == self.segments[2].id)
            self.assertEqual(target['motion_context']['current_visual']['anchor_time'], 6)
            for reference in (r for r in inputs if 'reference_slot' in r):
                self.assertNotIn('motion_context', reference)
            for provider in ('gemini', 'openai'):
                with self.subTest(provider=provider, reference_mode=target.get('reference_mode_input', False)):
                    response = Mock()
                    response.json.return_value = (
                        {'candidates': [{'content': {'parts': [{'text': '{"prompt":"OK"}'}]}}]}
                        if provider == 'gemini' else {'output_text': '{"prompt":"OK"}'})
                    with patch.object(ai.requests, 'post', return_value=response) as post:
                        ai._provider_raw(inputs, provider, 'model', 'key', ai.FRAME_MOTION_GUIDANCE, 30)
                    payload = post.call_args.kwargs['json']
                    parts = payload['contents'][0]['parts'] if provider == 'gemini' else payload['input'][0]['content']
                    texts = [part['text'] for part in parts if 'text' in part]
                    contexts = []
                    for text in texts:
                        if text.startswith('TIMED FRAME MOTION CONTEXT: '):
                            contexts.append(json.loads(text.split(': ', 1)[1]))
                        elif text.startswith('REFERENCE INPUT: '):
                            record = json.loads(text.split(': ', 1)[1])
                            if 'motion_context' in record:
                                contexts.append(record['motion_context'])
                    transmitted = next(c for c in contexts if c['current_visual'] and
                                       c['current_visual']['segment_id'] == self.segments[2].id)
                    self.assertEqual(transmitted['current_visual']['anchor_time'], 6)
                    self.assertEqual(transmitted['previous_visual']['label'], 'Image1')
                    self.assertEqual(transmitted['next_visual']['label'], 'Image3')
                    self.assertEqual(sum('inline_data' in p or p.get('type') == 'input_image' for p in parts),
                                     4 if any('reference_slot' in r for r in inputs) else 3)
        selected_inputs = ai._refinement_images(self.segments, 2)
        self.assertEqual([r['timeline_index'] for r in selected_inputs], [0, 1, 2, 3, 4])
        self.assertEqual([r['timeline_index'] for r in selected_inputs if r['selected']], [2])
        self.assertEqual([s.to_dict() for s in self.segments], original)


if __name__ == '__main__':
    unittest.main()
