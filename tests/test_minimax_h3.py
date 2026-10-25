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
    def test_refine_excludes_old_segment_wording_and_uses_current_sound_controls(self):
        from ltx_prompt_director.generic_ai import refine_generic_prompt
        segment = Segment('Beat', '', '', kind='text', duration=5,
                          prompt='The traveler says "STALE SPOKEN LINE" with lip sync.',
                          image_prompt='STALE ART DIRECTION')
        before = segment.to_dict()
        current = 'The traveler opens the door. A sign reads "Welcome". Footsteps echo.'
        for custom in (None, {'generate': 'Invent original dialogue.', 'refine': 'Improve clarity.'}):
            token = ai.WORKSPACE_INSTRUCTIONS.set(custom)
            try:
                for refiner in (ai.refine_minimax_h3_prompt, ai.refine_minimax_h3_reference_prompt,
                                refine_generic_prompt):
                    with self.subTest(refiner=refiner.__name__, custom=custom), patch.object(
                            ai, '_provider_raw', return_value=json.dumps({'prompt': current})) as raw:
                        self.assertEqual(refiner([segment], 'gemini', 'model', 'key', '', '',
                                                 True, False, True, current, ''), current)
                        inputs, _, _, _, rules, *_ = raw.call_args.args
                        request = json.dumps(inputs) + rules
                        self.assertNotIn('STALE SPOKEN LINE', request)
                        self.assertNotIn('STALE ART DIRECTION', request)
                        self.assertNotIn('Invent original dialogue.', rules)
                        self.assertIn(current, rules)
                        self.assertIn('Spoken Dialog is OFF', rules)
                        self.assertIn('Preserve visible written text and wordless physical sounds', rules)
                        self.assertEqual(inputs[0]['start_time'], 0)
                        self.assertEqual(inputs[0]['end_time'], 5)
                        self.assertEqual(segment.to_dict(), before)
            finally:
                ai.WORKSPACE_INSTRUCTIONS.reset(token)

    def test_refine_dialogue_enabled_does_not_invent_lines_from_old_drafts(self):
        current = 'The traveler quietly opens the door.'
        with patch.object(ai, '_provider_raw', return_value=json.dumps({'prompt': current})) as raw:
            ai.refine_minimax_h3_reference_prompt(self.segments(), 'gemini', 'model', 'key', '', '',
                                                 True, True, True, current, '')
        rules = raw.call_args.args[4]
        self.assertIn('Do not invent dialogue merely because this option is enabled', rules)
        self.assertNotIn('if none are supplied, write suitable original spoken lines', rules)

    def test_refine_rejects_speech_markup_when_dialogue_is_off(self):
        from ltx_prompt_director.generic_ai import refine_generic_prompt
        for refiner in (ai.refine_minimax_h3_prompt, ai.refine_minimax_h3_reference_prompt,
                        refine_generic_prompt):
            with self.subTest(refiner=refiner.__name__), patch.object(
                    ai, '_provider_raw', return_value=json.dumps({'prompt': '<d>[English] Wait here.</d>'})):
                with self.assertRaisesRegex(ai.AIResponseFormatError, 'Spoken Dialog is OFF'):
                    refiner(self.segments(), 'gemini', 'model', 'key', '', '', True, False,
                            True, 'The traveler opens the door.', '')

    def test_refiners_prioritize_current_details_over_saved_template_brevity(self):
        from ltx_prompt_director.generic_ai import refine_generic_prompt
        current = 'The traveler raises the copper compass, turns it twice, then says "Hold here."'
        token = ai.WORKSPACE_INSTRUCTIONS.set({"generate": "Keep it brief.", "refine": "Keep it brief."})
        try:
            for refiner in (ai.refine_minimax_h3_prompt, ai.refine_minimax_h3_reference_prompt, refine_generic_prompt):
                with self.subTest(refiner=refiner.__name__), patch.object(
                        ai, "_provider_raw", return_value=json.dumps({"prompt": current})) as raw:
                    result = refiner(self.segments(), "gemini", "model", "key", "", "", True,
                                     True, True, current, "Improve notation only.")
                    self.assertEqual(result, current)
                    rules = raw.call_args.args[4]
                    self.assertIn(current, rules)
                    self.assertIn("DETAIL FIDELITY TAKES PRIORITY OVER LENGTH GUIDANCE", rules)
                    self.assertIn("An explicit user request to remove, replace or shorten", rules)
                    self.assertNotIn("CURRENT TIMELINE — AUTHORITATIVE GENERATION PLAN", rules)
            with patch.object(ai, "_provider_raw", return_value=json.dumps({"prompt": current})) as raw:
                self.assertEqual(ai.refine_minimax_text_prompt("gemini", "model", "key", current), current)
                self.assertIn("DETAIL FIDELITY TAKES PRIORITY OVER LENGTH GUIDANCE", raw.call_args.args[4])
        finally:
            ai.WORKSPACE_INSTRUCTIONS.reset(token)

    def test_combined_generation_preserves_long_detail_and_overrides_saved_brevity_rules(self):
        from ltx_prompt_director.generic_ai import build_generic_prompt
        segments = [Segment(f"Beat {i}", "", "", "text", "text",
                            (f"At checkpoint {i}, the subject grips prop {i} with the left hand; "
                             f"keep marker {i} illuminated, hold the camera stationary and "
                             f"synchronize sound cue {i}. ") * 8, 2)
                    for i in range(1, 5)]
        production = "detailed_description:\n" + "\n\n".join(s.prompt for s in segments)
        self.assertGreater(len(production.split()), 500)
        token = ai.WORKSPACE_INSTRUCTIONS.set({"generate": "Keep the result under 500 words."})
        try:
            for builder in (ai.build_minimax_h3_prompt, ai.build_minimax_h3_reference_prompt, build_generic_prompt):
                with self.subTest(builder=builder.__name__), patch.object(
                        ai, "_provider_raw", return_value=json.dumps({"prompt": production})) as raw:
                    result = builder(segments, "gemini", "model", "key", "", "", True, True, False)
                    self.assertEqual(result, production.strip())
                    rules = raw.call_args.args[4]
                    self.assertLess(rules.index("under 500 words"), rules.index("COMPLETENESS OVERRIDES LENGTH"))
                    self.assertIn("ONE-FOR-ONE CONTENT TRANSFER", rules)
                    self.assertIn("EVERY source sentence/clause", rules)
                    for segment in segments:
                        self.assertIn(segment.prompt, rules)
        finally:
            ai.WORKSPACE_INSTRUCTIONS.reset(token)

    def test_all_combined_generators_end_with_the_current_complete_timeline(self):
        from ltx_prompt_director.generic_ai import build_generic_prompt
        segments = [Segment("Video", "", "", "video", "start", "Current opening", 1, trim_start=48),
                    Segment("Inserted", "", "", "text", "text", "Current inserted action", 2),
                    Segment("Final", "", "", "text", "text", "Current final state", 3)]
        for builder in (ai.build_minimax_h3_prompt, ai.build_minimax_h3_reference_prompt, build_generic_prompt):
            with self.subTest(builder=builder.__name__):
                with patch.object(ai, "_provider_raw", return_value='{"prompt":"New complete brief"}') as raw:
                    result = builder(segments, "gemini", "model", "key", "Style guidance",
                                     "Older global notes", False, False, False, 30)
                self.assertEqual(result, "New complete brief")
                rules = raw.call_args.args[4]
                plan = json.loads(rules.rsplit("\n", 1)[1])
                self.assertEqual([r["id"] for r in plan], [s.id for s in segments])
                self.assertEqual([r["prompt"] for r in plan], [s.prompt for s in segments])
                self.assertEqual([(r["start"], r["end"]) for r in plan], [(0, 1), (1, 3), (3, 6)])
                self.assertEqual((plan[0]["sourceStart"], plan[0]["sourceEnd"]), (2, 3))
                self.assertIn("split segments", rules)
                self.assertIn("override conflicting older global direction", rules)

    def segments(self):
        return [
            Segment('opening.png', '', '', 'image', 'start', 'She raises her hand.', 3.0),
            Segment('motion.mp4', '', '', 'video', 'start', 'She turns slowly.', 3.0),
            Segment('direction', '', '', 'text', 'text', 'The movement continues.', 3.0),
            Segment('ending.png', '', '', 'image', 'end', 'She faces the camera.', 3.86),
        ]

    def test_timestamp_format(self):
        self.assertEqual(ai._minimax_timestamp(12.86), '00:00:12:21')
        self.assertEqual(ai._minimax_timestamp(65.125), '00:01:05:03')

    def test_frames_rules_use_reference_style_sections_and_exact_ranges(self):
        rules = ai._minimax_h3_rules(self.segments(), 'Intent', 'Global', True, False, True)
        for section in ('integrated_multimodal_description', 'overall_soundscape', 'non_diegetic_music'):
            self.assertIn(section, rules)
        for interval in ('00:00:00:00 - 00:00:03:00', '00:00:03:00 - 00:00:06:00',
                         '00:00:06:00 - 00:00:09:00', '00:00:09:00 - 00:00:12:21'):
            self.assertIn(interval, rules)
        self.assertIn('Image1 (opening.png)', rules)
        self.assertIn('Image2 (ending.png)', rules)
        self.assertIn('end frame reached at 00:00:12:21', rules)
        self.assertIn('start frame at 00:00:00:00', rules)
        self.assertIn('Video1 (motion.mp4)', rules)
        self.assertIn('I2V', rules)
        self.assertNotIn('exactly one timed Frame bridge', rules)
        self.assertNotIn('sentence quota', rules.split('Use only facts')[0])

    def test_frame_input_respects_start_and_end_roles(self):
        inputs = ai._minimax_h3_inputs(self.segments(), frame_checkpoints=True)
        self.assertIn('opening visual anchor', inputs[0]['continuity_function'])
        self.assertIn('end-frame target reached by this interval\'s end', inputs[-1]['continuity_function'])
        self.assertEqual([item['cue_timestamp'] for item in inputs],
                         ['00:00:00:00', '00:00:03:00', '00:00:06:00', '00:00:12:21'])
        self.assertTrue(inputs[-1]['frame_mode_checkpoint'])
        self.assertNotIn('recommended_detail', inputs[-1])

    def test_first_end_frame_is_not_mistaken_for_start_frame(self):
        end = Segment('last.png', '', '', 'image', 'end', '', 4.0)
        inputs = ai._minimax_h3_inputs([end], frame_checkpoints=True)
        self.assertIn('end-frame target', inputs[0]['continuity_function'])
        self.assertIn('00:00:00:00 - 00:00:04:00', ai._minimax_h3_rules([end], '', '', False, False, True))

    def test_builder_returns_reference_style_brief_verbatim(self):
        result = '[FRAME USE] Image1 is the opening frame.\n\n[TIMED ACTION]\n00:00:00:00 - 00:00:03:00: She moves.\n\n[SOUND] Water. '
        with patch.object(ai, '_provider_raw', return_value=json.dumps({'prompt': result})) as provider:
            output = ai.build_minimax_h3_prompt(self.segments(), 'gemini', 'model', '', '', '', False, False, True)
        self.assertEqual(output, result.strip())
        self.assertIn('integrated_multimodal_description', provider.call_args.args[4])
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
        self.assertIn('non_diegetic_music', rules)
        self.assertIn('single', rules)
        self.assertEqual(rules.split('UNTIMED REFERENCE IMAGES', 1)[0].count('00:00:00:00 - 00:00:03:00'), 1)
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
        self.assertIn('subject_definitions', rules)
        self.assertIn('Mixed references (R2V)', rules)
        self.assertNotIn('[FRAME USE]', rules)

    def test_refinement_keeps_private_edits_and_section_style(self):
        with patch.object(ai, '_provider_raw', return_value=json.dumps({'prompt': '[FRAME USE] Edited.'})) as provider:
            result = ai.refine_minimax_h3_prompt(self.segments(), 'gemini', 'model', '', '', '',
                                                  False, False, True, '[FRAME USE] Current.', 'Preserve my camera.')
        self.assertEqual(result, '[FRAME USE] Edited.')
        self.assertIn('/refine-global', provider.call_args.args[4])
        self.assertIn('Preserve my camera.', provider.call_args.args[4])
        self.assertIn('Preserve untouched content', provider.call_args.args[4])

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
