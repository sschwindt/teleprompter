# SPDX-License-Identifier: GPL-3.0-or-later

"""Protect the Linux launcher when checking out or packaging on Windows."""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class LauncherTemplateTests(unittest.TestCase):
    def test_launcher_bytes_have_an_lf_shebang_and_no_carriage_returns(self):
        # Do not use read_text(): universal-newline decoding would hide the bug.
        template = (ROOT / "src" / "teleprompter.in").read_bytes()
        self.assertEqual(template.split(b"\n", 1)[0], b"#!@PYTHON@")
        self.assertNotIn(
            b"\r",
            template,
            "CRLF in the launcher makes Linux look for an interpreter ending in CR",
        )
        self.assertTrue(template.endswith(b"\n"))


if __name__ == "__main__":
    unittest.main()
