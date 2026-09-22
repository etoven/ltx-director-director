import unittest

from ltx_prompt_director.ui import toolbar_icon


class ToolbarIconTests(unittest.TestCase):
    def test_every_toolbar_icon_is_bundled_and_loadable(self):
        icon_names = (
            "projects",
            "preview",
            "project-files",
            "new",
            "open",
            "save",
            "import",
            "export-ltx",
            "export-minimax",
            "delete",
            "settings",
        )

        for name in icon_names:
            with self.subTest(name=name):
                self.assertFalse(toolbar_icon(name).isNull())


if __name__ == "__main__":
    unittest.main()
