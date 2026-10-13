import unittest
from ltx_prompt_director.timing_limits import fit_sequence, sequence_limit

class SequenceLimitTests(unittest.TestCase):
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
