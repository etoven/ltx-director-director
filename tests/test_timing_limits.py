import unittest
from ltx_prompt_director.timing_limits import fit_sequence, sequence_limit, shot_limit, SHOT_COUNT_LIMIT

class SequenceLimitTests(unittest.TestCase):
    def test_shot_defaults_and_explicit_unlimited(self):
        self.assertEqual(shot_limit({'engine': 'minimax_references'}), 9)
        self.assertEqual(shot_limit({'engine': 'minimax_frames'}), 9)
        self.assertIsNone(shot_limit({'engine': 'generic'}))
        self.assertIsNone(shot_limit({'engine': 'minimax_references', 'max_shots': 0}))
        self.assertEqual(shot_limit({'engine': 'generic', 'max_shots': 24}), 24)

    def test_shot_validation_defaults_and_custom_limits(self):
        import json
        from importlib.resources import files
        from ltx_prompt_director.workspaces import validate_definition
        original = json.loads(files('ltx_prompt_director').joinpath('workspace_templates/minimax_full_reference_guide.json').read_text())
        for invalid in (-1, True, 2.5, '9', None):
            with self.subTest(value=invalid), self.assertRaises(ValueError):
                validate_definition({**original, 'max_shots': invalid})
        for count in (0, 9, 200):
            self.assertEqual(validate_definition({**original, 'max_shots': count})['max_shots'], count)
        del original['max_shots']
        self.assertEqual(validate_definition(original)['max_shots'], 9)

    def test_ai_shot_contract_enforces_limit_and_unlimited_has_no_old_cap(self):
        import json
        from ltx_prompt_director.ai import AIResponseFormatError
        from ltx_prompt_director.segment_plan import normalize_plan, response_contract, completed_stream_records
        records = [dict(id=f'new:{i}', name=f'Beat {i}', kind='text', role='text', duration=1, prompt=f'Action {i}') for i in range(1, 101)]
        raw = json.dumps(dict(prompt='Full sequence', segments=records))
        token = SHOT_COUNT_LIMIT.set(9)
        try:
            self.assertIn('maximum shots: 9', response_contract([], '', refine=False))
            with self.assertRaises(AIResponseFormatError):
                normalize_plan(raw, [])
        finally:
            SHOT_COUNT_LIMIT.reset(token)
        token = SHOT_COUNT_LIMIT.set(None)
        try:
            self.assertEqual(len(normalize_plan(raw, [])['segments']), 100)
            self.assertEqual(len(completed_stream_records(raw)), 100)
        finally:
            SHOT_COUNT_LIMIT.reset(token)

    def test_defaults_and_no_limit(self):
        self.assertEqual(sequence_limit({'engine':'ltx'}),20)
        self.assertEqual(sequence_limit({'engine':'minimax_references'}),15)
        self.assertIsNone(sequence_limit({'engine':'ltx','max_sequence_duration':None}))
        self.assertEqual(fit_sequence([10,30],None),[10,30])

    def test_reduce_last_only(self):
        self.assertEqual(fit_sequence([4,4,10],15),[4,4,7])
        self.assertEqual(fit_sequence([3,4],15),[3,4])

    def test_proportional_fallback_uses_original_ratios(self):
        values=fit_sequence([20,30,10],15)
        self.assertEqual(values,[5,7.5,2.5])

    def test_minimums_and_budget(self):
        values=fit_sequence([0.01,100,100],15)
        self.assertGreaterEqual(min(values),0.01)
        self.assertLessEqual(sum(values),15)
        self.assertAlmostEqual(values[1],values[2])

    def test_many_segments_and_rounding(self):
        for n in range(1,100):
            values=fit_sequence([1.37]*n,15)
            self.assertEqual(len(values),n)
            self.assertLessEqual(sum(values),15)
            self.assertGreater(min(values),0)

if __name__=='__main__':unittest.main()
