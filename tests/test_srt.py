# SPDX-License-Identifier: GPL-3.0-or-later

"""Run with python3 -m unittest discover -s tests (no GTK required)."""

import sys
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

# Import the standalone modules without src/__init__.py, which initializes GTK.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from srt import (
    Cue,
    SubtitleParseError,
    decode_text,
    display_text,
    parse_srt,
)


class SubtitleParsingTests(unittest.TestCase):
    def test_preserves_unicode_multiline_whitespace_and_literal_markup(self):
        text = (
            "\ufeff7\r\n00:00:01,025 --> 00:00:03,999\r\n"
            "  Grüß Gott — α & <i>hello</i>!  \r\n第二行\r\n\r\n"
            "9\r\n01:02:03,004 --> 01:02:05,006\r\nLast line"
        )
        self.assertEqual(
            parse_srt(text),
            (
                Cue(1025, 3999, "  Grüß Gott — α & <i>hello</i>!  \n第二行"),
                Cue(3723004, 3725006, "Last line"),
            ),
        )

    def test_cues_are_immutable(self):
        cue = Cue(0, 1000, "word")
        with self.assertRaises(FrozenInstanceError):
            cue.text = "changed"

    def test_accepts_no_counter_period_times_and_export_coordinates(self):
        result = parse_srt(
            "\n00:00:00.005 --> 00:00:01.050 X1:10 X2:20 Y1:30 Y2:40\n"
            "A line\n\n\n00:00:02,000 --> 00:00:03,000\nAnother\n\n"
        )
        self.assertEqual(result, (Cue(5, 1050, "A line"), Cue(2000, 3000, "Another")))

    def test_preserves_overlapping_cues_and_original_order(self):
        result = parse_srt(
            "2\n00:00:02,000 --> 00:00:04,000\nLater\n\n"
            "1\n00:00:01,000 --> 00:00:03,000\nEarlier"
        )
        self.assertEqual(result, (Cue(2000, 4000, "Later"), Cue(1000, 3000, "Earlier")))

    def test_numeric_spoken_line_is_not_a_counter(self):
        self.assertEqual(
            parse_srt("1\n00:00:00,000 --> 00:00:01,000\n2026\nnext line")[0].text,
            "2026\nnext line",
        )

    def test_invalid_timestamp_has_line_and_cue_diagnostics(self):
        with self.assertRaises(SubtitleParseError) as caught:
            parse_srt("42\n00:60:00,000 --> 01:01:00,000\nText")
        self.assertEqual(caught.exception.line, 2)
        self.assertEqual(caught.exception.cue_number, 42)
        self.assertIn("between 00 and 59", str(caught.exception))

    def test_rejects_empty_invalid_and_incomplete_files(self):
        invalid_files = (
            "",
            "\ufeff\n \n",
            "ordinary text",
            "1",
            "1\n",
            "1\n00:00:00,000 -> 00:00:01,000\nText",
            "1\n00:00:00,00 --> 00:00:01,000\nText",
            "1\n00:00:00,000 --> 00:00:01,000\n",
            "1\n00:00:00,000 --> 00:00:01,000\n \n",
            "1\n00:00:00,000 --> 00:00:00,000\nText",
            "1\n00:00:02,000 --> 00:00:01,000\nText",
            "1\n00:00:00,000 --> 00:00:60,000\nText",
            "1\n00:00:00,000 --> 00:00:01,000\nText\n\n2\ninvalid\nText",
        )
        for text in invalid_files:
            with self.subTest(text=text), self.assertRaises(SubtitleParseError):
                parse_srt(text)

    def test_rejects_missing_blank_between_cues(self):
        with self.assertRaisesRegex(SubtitleParseError, "Missing blank line"):
            parse_srt(
                "1\n00:00:00,000 --> 00:00:01,000\nFirst\n"
                "2\n00:00:02,000 --> 00:00:03,000\nSecond"
            )

    def test_lone_carriage_return_file(self):
        self.assertEqual(
            parse_srt("1\r00:00:00,000 --> 00:00:01,000\rText\r"),
            (Cue(0, 1000, "Text"),),
        )


class TextDecodingTests(unittest.TestCase):
    def test_utf8_utf8_bom_and_both_utf16_byte_orders(self):
        text = "1\n00:00:00,000 --> 00:00:01,000\nÜñîçødé 第二行"
        variants = (
            text.encode("utf-8"),
            text.encode("utf-8-sig"),
            b"\xff\xfe" + text.encode("utf-16-le"),
            b"\xfe\xff" + text.encode("utf-16-be"),
        )
        for data in variants:
            with self.subTest(bom=data[:4]):
                self.assertEqual(decode_text(data), text)

    def test_invalid_encoding_is_rejected_without_replacement_characters(self):
        invalid_data = (
            b"\xffinvalid",
            b"\xff\xfe\x41",  # Truncated UTF-16 code unit.
            b"\xff\xfe\x00\x00A\x00\x00\x00",  # UTF-32 LE.
            b"\x00\x00\xfe\xff\x00\x00\x00A",  # UTF-32 BE.
            b"A\x00B\x00",  # UTF-16 with no BOM, otherwise mistaken for UTF-8.
        )
        for data in invalid_data:
            with self.subTest(data=data), self.assertRaises(SubtitleParseError):
                decode_text(data)


class DisplayTextTests(unittest.TestCase):
    def test_common_presentational_tags_line_breaks_and_entities(self):
        text = (
            "<b>Bold</b> <I>italic</I> <u>underlined</u> <s>struck</s>\n"
            '<font color="#ff0000" size="24">Grüße</font><br />'
            "α &amp; β &lt; 3 &gt; 1 &#169; &#x1F642;"
        )
        self.assertEqual(
            display_text(text),
            "Bold italic underlined struck\nGrüße\nα & β < 3 > 1 © 🙂",
        )

    def test_preserves_arbitrary_angle_brackets_unknown_tags_and_plain_ampersands(self):
        text = "2 < 3; x > y; <speaker>Ada</speaker>; <bigger>; &not; A & B; &unknown;"
        self.assertEqual(
            display_text(text),
            "2 < 3; x > y; <speaker>Ada</speaker>; <bigger>; ¬ A & B; &unknown;",
        )
        self.assertEqual(display_text("We write &not here"), "We write &not here")

    def test_escaped_tags_remain_literal_and_source_cue_is_unchanged(self):
        cue = parse_srt(
            "1\n00:00:00,000 --> 00:00:01,000\n<i>Hello</i> &lt;b&gt;literal&lt;/b&gt;"
        )[0]
        self.assertEqual(display_text(cue.text), "Hello <b>literal</b>")
        self.assertEqual(cue.text, "<i>Hello</i> &lt;b&gt;literal&lt;/b&gt;")


if __name__ == "__main__":
    unittest.main()
