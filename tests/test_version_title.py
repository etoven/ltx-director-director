import unittest

from ltx_prompt_director import __version__
from ltx_prompt_director.ui import application_window_title


class VersionTitleTests(unittest.TestCase):
    def test_title_always_contains_running_version(self):
        self.assertEqual(application_window_title(), f"LTX Director - Director v{__version__}")
        self.assertEqual(
            application_window_title("Horse Transformation"),
            f"LTX Director - Director v{__version__} :: Horse Transformation",
        )


if __name__ == "__main__":
    unittest.main()
