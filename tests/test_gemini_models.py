import unittest

from ltx_prompt_director.ai import GEMINI_MODELS


class GeminiModelPickerTests(unittest.TestCase):
    def test_current_flash_lite_models_are_available(self):
        expected = {
            "gemini-3.5-flash-lite",
            "gemini-3.1-flash-lite",
            "gemini-flash-lite-latest",
            "gemini-2.5-flash-lite",
        }
        self.assertTrue(expected.issubset(GEMINI_MODELS))
        self.assertEqual(len(GEMINI_MODELS), len(set(GEMINI_MODELS)))
        self.assertFalse(any("preview" in model for model in GEMINI_MODELS if "flash-lite" in model))


if __name__ == "__main__":
    unittest.main()
