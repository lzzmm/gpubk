import unittest

from bk.terminal import color_enabled, colorize_help, style


class _Stream:
    def __init__(self, terminal: bool):
        self.terminal = terminal

    def isatty(self):
        return self.terminal


class TerminalTests(unittest.TestCase):
    def test_color_requires_terminal_and_respects_no_color(self):
        self.assertTrue(color_enabled(_Stream(True), {"TERM": "xterm-256color"}))
        self.assertFalse(color_enabled(_Stream(False), {"TERM": "xterm-256color"}))
        self.assertFalse(
            color_enabled(_Stream(True), {"TERM": "xterm-256color", "NO_COLOR": "1"})
        )
        self.assertFalse(color_enabled(_Stream(True), {"TERM": "dumb"}))

    def test_styles_and_help_keep_plain_output_clean(self):
        self.assertEqual(style("ok", "success", enabled=False), "ok")
        self.assertIn("\x1b[32m", style("ok", "success", enabled=True))
        plain = "GPUBK - test\nBOOK\n  bk 1 30m\n"
        self.assertEqual(colorize_help(plain, enabled=False), plain)
        colored = colorize_help(plain, enabled=True)
        self.assertIn("\x1b[1;36mBOOK", colored)
        self.assertTrue(colored.endswith("\n"))
