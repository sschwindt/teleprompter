# SPDX-License-Identifier: GPL-3.0-or-later

"""Exercise window transport integration without pretending to render GTK.

Small widget doubles emit scale/spin callbacks synchronously, like GTK, so the
tests cover recursion guards and timer ownership as well as document changes.
Actual widget construction and Flatpak resources still need a Linux GUI test.
"""

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


class FakeTemplate:
    def __init__(self, **kwargs):
        pass

    def __call__(self, cls):
        return cls

    @staticmethod
    def Child():
        return None

    @staticmethod
    def Callback(name):
        return lambda method: method


def load_window_module():
    """Import the real window and transport without executing src/__init__.py."""
    package_name = "_teleprompter_window_tests"
    source = Path(__file__).resolve().parents[1] / "src"
    package = types.ModuleType(package_name)
    package.__path__ = [str(source)]
    scroll = types.ModuleType(f"{package_name}.scroll_text_view")
    scroll.TeleprompterScrollTextView = object
    repository = types.ModuleType("gi.repository")
    repository.Gtk = types.SimpleNamespace(Template=FakeTemplate)
    repository.Adw = types.SimpleNamespace(ApplicationWindow=object)
    repository.GObject = types.SimpleNamespace(type_register=lambda cls: None)
    for name in ("Gdk", "Pango", "GLib", "Gio"):
        setattr(repository, name, types.SimpleNamespace())
    gi = types.ModuleType("gi")
    gi.repository = repository
    spec = importlib.util.spec_from_file_location(
        f"{package_name}.window", source / "window.py"
    )
    module = importlib.util.module_from_spec(spec)
    with patch.dict(
        sys.modules,
        {
            "gi": gi,
            "gi.repository": repository,
            package_name: package,
            f"{package_name}.scroll_text_view": scroll,
            spec.name: module,
        },
    ):
        spec.loader.exec_module(module)
    return module


window_module = load_window_module()
Timeline = window_module.SubtitleTimeline


class Clock:
    def __init__(self):
        self.seconds = 10.0

    def __call__(self):
        return self.seconds

    def advance(self, milliseconds):
        self.seconds += milliseconds / 1000


class Scheduler:
    def __init__(self):
        self.sources = {}
        self.next_id = 1

    def timeout_add(self, interval, callback):
        source_id = self.next_id
        self.next_id += 1
        self.sources[source_id] = (interval, callback)
        return source_id

    def source_remove(self, source_id):
        # Removing a source twice is an error worth catching in these tests.
        del self.sources[source_id]

    def fire(self, source_id):
        _interval, callback = self.sources[source_id]
        keep = callback()
        if not keep:
            self.sources.pop(source_id, None)
        return keep


class Widget:
    def __init__(self):
        self.visible = True
        self.sensitive = True
        self.editable = True
        self.cursor_visible = True
        self.enabled = True
        self.label = ""
        self.icon_name = ""

    def set_visible(self, value):
        self.visible = value

    def set_sensitive(self, value):
        self.sensitive = value

    def set_editable(self, value):
        self.editable = value

    def set_cursor_visible(self, value):
        self.cursor_visible = value

    def set_enabled(self, value):
        self.enabled = value

    def set_label(self, value):
        self.label = value

    def set_icon_name(self, value):
        self.icon_name = value


class Adjustment:
    def __init__(self, upper=1000, page_size=200):
        self.value = 0
        self.upper = upper
        self.page_size = page_size
        self.callback = None

    def get_value(self):
        return self.value

    def set_value(self, value):
        if self.value != value:
            self.value = value
            if self.callback:
                self.callback(self)

    def get_upper(self):
        return self.upper

    def get_page_size(self):
        return self.page_size

    def set_range(self, lower, upper):
        self.upper = upper
        self.set_value(max(lower, min(upper, self.value)))


class Buffer:
    def __init__(self):
        self.text = ""
        self.tags = []

    def set_text(self, text):
        self.text = text
        self.tags.clear()

    def apply_tag(self, tag, start, end):
        self.tags.append((tag, start, end))

    def get_start_iter(self):
        return 0

    def get_end_iter(self):
        return len(self.text)

    def get_iter_at_offset(self, offset):
        return offset


class Settings:
    def __init__(self):
        self.values = {"speed": 100}

    def get_int(self, key):
        return self.values[key]

    def set_int(self, key, value):
        self.values[key] = value


SCRIPT = b"""1
00:00:01,000 --> 00:00:02,000
First line
and its continuation.

2
00:00:03,500 --> 00:00:05,000
Second line.

3
00:00:06,500 --> 00:00:08,000
Last line.
"""


class WindowTransportTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.scheduler = Scheduler()
        self.glib_patch = patch.object(window_module, "GLib", self.scheduler)
        self.glib_patch.start()
        self.addCleanup(self.glib_patch.stop)
        self.timeline_patch = patch.object(
            window_module,
            "SubtitleTimeline",
            lambda cues: Timeline(cues, clock=self.clock),
        )
        self.timeline_patch.start()
        self.addCleanup(self.timeline_patch.stop)

        self.win = object.__new__(window_module.TeleprompterWindow)
        self.win.playing = False
        self.win.timeline = None
        self.win._playback_source_id = 0
        self.win._updating_srt_controls = False
        self.win._rendered_cues = None
        self.win._closed = False
        self.win.settings = Settings()
        self.win.text_buffer = Buffer()
        self.win.current_cue_tag = "current"
        self.win.preview_tag = "preview"
        for name in (
            "start_button",
            "title_widget",
            "increase_speed_button",
            "decrease_speed_button",
            "srt_controls",
            "srt_status",
            "prompt_text_view",
        ):
            setattr(self.win, name, Widget())
        self.adjustment = Adjustment()
        self.win.scrolled_window = types.SimpleNamespace(
            get_vadjustment=lambda: self.adjustment
        )
        self.win.srt_seek = Adjustment()
        self.win.srt_seek.callback = self.win.on_srt_seek_changed
        self.win.srt_offset = Adjustment()
        self.win.srt_offset.callback = self.win.on_srt_offset_changed
        self.actions = {name: Widget() for name in ("previous-cue", "next-cue")}
        self.win.lookup_action = self.actions.get

    def load_srt(self):
        self.win.load_document(SCRIPT, "lecture.SRT")

    def test_failed_load_preserves_document_timeline_and_running_source(self):
        self.load_srt()
        self.win.play()
        self.clock.advance(1200)
        source_id = self.win._playback_source_id
        timeline = self.win.timeline
        text = self.win.text_buffer.text
        for contents, name in (
            (b"1\ninvalid timestamp\nWrong text", "broken.srt"),
            (b"\xff\x80bad encoding", "broken.srt"),
            (b"\xff\x80bad encoding", "broken.txt"),
        ):
            with self.subTest(name=name, contents=contents):
                with self.assertRaises(window_module.SubtitleParseError):
                    self.win.load_document(contents, name)
                self.assertIs(self.win.timeline, timeline)
                self.assertTrue(self.win.playing)
                self.assertEqual(self.win._playback_source_id, source_id)
                self.assertEqual(set(self.scheduler.sources), {source_id})
                self.assertEqual(self.win.text_buffer.text, text)
                self.assertEqual(self.win.title_widget.label, "lecture.SRT")

    def test_mode_changes_stop_playback_and_restore_plain_text_controls(self):
        self.win.set_plain_text("Editable script")
        self.win.play()
        self.load_srt()
        self.assertTrue(self.win.is_srt_mode)
        self.assertFalse(self.win.playing)
        self.assertFalse(self.scheduler.sources)
        self.assertTrue(self.win.srt_controls.visible)
        self.assertFalse(self.win.prompt_text_view.editable)
        self.assertFalse(self.win.prompt_text_view.cursor_visible)
        self.assertFalse(self.win.increase_speed_button.sensitive)
        self.assertFalse(self.win.decrease_speed_button.sensitive)
        self.assertTrue(all(action.enabled for action in self.actions.values()))

        self.win.play()
        self.win.set_plain_text("A new script")
        self.assertFalse(self.win.is_srt_mode)
        self.assertFalse(self.win.playing)
        self.assertFalse(self.scheduler.sources)
        self.assertFalse(self.win.srt_controls.visible)
        self.assertTrue(self.win.prompt_text_view.editable)
        self.assertTrue(self.win.prompt_text_view.cursor_visible)
        self.assertTrue(self.win.increase_speed_button.sensitive)
        self.assertTrue(self.win.decrease_speed_button.sensitive)
        self.assertFalse(any(action.enabled for action in self.actions.values()))
        self.assertEqual(self.win.text_buffer.text, "A new script")
        self.assertEqual(self.win.settings.get_int("speed"), 100)

    def test_play_pause_resume_restart_own_exactly_one_timeout(self):
        self.load_srt()
        self.win.play()
        first_source = self.win._playback_source_id
        self.assertEqual(set(self.scheduler.sources), {first_source})
        self.clock.advance(1250)
        self.win.play()  # The shared Play/Pause action toggles to pause.
        self.assertFalse(self.scheduler.sources)
        self.assertEqual(self.win.timeline.position_ms, 1250)
        self.clock.advance(5000)
        self.win.pause()  # Repeated pause must not remove an old source twice.
        self.assertEqual(self.win.timeline.position_ms, 1250)
        self.win.play()
        self.assertNotEqual(self.win._playback_source_id, first_source)
        self.assertEqual(len(self.scheduler.sources), 1)
        self.clock.advance(250)
        self.win.reset_playback()
        self.assertEqual(self.win.timeline.position_ms, 0)
        self.assertFalse(self.win.playing)
        self.assertFalse(self.scheduler.sources)

    def test_close_cancels_timeout_and_freezes_timeline(self):
        self.load_srt()
        self.win.play()
        self.clock.advance(1200)
        self.assertFalse(self.win._on_close_request())
        self.assertTrue(self.win._closed)
        self.assertFalse(self.scheduler.sources)
        self.assertEqual(self.win.timeline.position_ms, 1200)
        self.clock.advance(5000)
        self.assertFalse(self.win._on_playback_tick())
        self.assertEqual(self.win.timeline.position_ms, 1200)

    def test_seek_end_stops_timeout_and_play_restarts_at_zero(self):
        self.load_srt()
        self.win.play()
        self.win.seek_fraction(1)
        self.assertEqual(self.win.timeline.position_ms, 8000)
        self.assertFalse(self.win.playing)
        self.assertFalse(self.scheduler.sources)
        self.assertIn("End of script", self.win.srt_status.label)
        self.win.play()
        self.assertTrue(self.win.playing)
        self.assertEqual(self.win.timeline.position_ms, 0)
        self.assertEqual(len(self.scheduler.sources), 1)

    def test_delayed_tick_renders_current_cue_and_finishes_without_drift(self):
        self.load_srt()
        self.win.play()
        source_id = self.win._playback_source_id
        self.clock.advance(4000)
        self.assertTrue(self.scheduler.fire(source_id))
        self.assertEqual(self.win.timeline.position_ms, 4000)
        self.assertIn("Second line.", self.win.text_buffer.text)
        self.assertNotIn("First line", self.win.text_buffer.text)
        self.clock.advance(5000)
        self.assertFalse(self.scheduler.fire(source_id))
        self.assertEqual(self.win.timeline.position_ms, 8000)
        self.assertFalse(self.win.playing)
        self.assertEqual(self.win._playback_source_id, 0)
        self.assertFalse(self.scheduler.sources)
        self.assertIn("Paused", self.win.srt_status.label)

    def test_fraction_seeks_use_time_for_srt_and_scroll_for_plain_text(self):
        self.load_srt()
        self.win.play()
        self.win.seek_fraction(0.5)
        self.assertEqual(self.win.timeline.position_ms, 4000)
        self.assertAlmostEqual(self.win.playback_fraction, 0.5)
        self.assertTrue(self.win.playing)
        self.assertEqual(len(self.scheduler.sources), 1)
        self.win.set_plain_text("Plain text")
        for fraction, expected in ((0.5, 400), (-1, 0), (2, 800)):
            with self.subTest(fraction=fraction):
                self.win.seek_fraction(fraction)
                self.assertEqual(self.adjustment.get_value(), expected)
                self.assertEqual(self.win.playback_fraction, expected / 800)

    def test_cue_steps_use_signed_offset_and_preserve_playing_state(self):
        self.load_srt()
        self.win.srt_offset.set_value(0.5)
        self.win.step_cue(1)
        self.assertEqual(self.win.timeline.position_ms, 1500)
        self.assertFalse(self.win.playing)
        self.win.play()
        self.win.step_cue(1)
        self.assertEqual(self.win.timeline.position_ms, 4000)
        self.win.step_cue(1)
        self.assertEqual(self.win.timeline.position_ms, 7000)
        self.win.step_cue(-1)
        self.assertEqual(self.win.timeline.position_ms, 4000)
        self.assertTrue(self.win.playing)
        self.assertEqual(len(self.scheduler.sources), 1)
        self.win.srt_offset.set_value(-0.5)
        self.win.step_cue(-1)
        self.assertEqual(self.win.timeline.position_ms, 3000)

    def test_offset_changes_keep_transport_position_and_stop_at_shortened_end(self):
        self.load_srt()
        self.win.play()
        self.clock.advance(1200)
        self.win.srt_offset.set_value(0.5)
        self.assertEqual(self.win.timeline.position_ms, 1200)
        self.assertEqual(self.win.timeline.offset_ms, 500)
        self.assertEqual(self.win.timeline.active_indices, ())
        self.assertTrue(self.win.playing)
        self.win.seek_fraction(0.9)
        self.win.srt_offset.set_value(-1)
        self.assertEqual(self.win.timeline.duration_ms, 7000)
        self.assertEqual(self.win.timeline.position_ms, 7000)
        self.assertFalse(self.win.playing)
        self.assertFalse(self.scheduler.sources)

    def test_rendering_scale_changes_do_not_seek_recursively(self):
        self.load_srt()
        self.win.timeline.seek(4000)
        with patch.object(
            self.win.timeline, "seek", wraps=self.win.timeline.seek
        ) as seek:
            self.win._render_srt()
            seek.assert_not_called()
            self.assertEqual(self.win.srt_seek.get_value(), 4.0)
            self.win.srt_seek.set_value(6.5)  # A real user scale change.
            seek.assert_called_once_with(6500)
            self.assertFalse(self.win._updating_srt_controls)

    def test_srt_speed_controls_cannot_change_timestamps_or_saved_wpm(self):
        self.load_srt()
        self.win.increase_speed_button_clicked()
        self.win.decrease_speed_button_clicked()
        with self.assertRaises(ValueError):
            self.win.set_playback_speed(120)
        self.assertEqual(self.win.settings.get_int("speed"), 100)
        self.win.set_plain_text("Plain script")
        self.win.increase_speed_button_clicked()
        self.assertEqual(self.win.settings.get_int("speed"), 110)

    def test_render_preserves_multiline_script_and_separates_upcoming_preview(self):
        self.load_srt()
        self.win.seek_fraction(0.125)  # First cue starts at exactly 1000 ms.
        self.assertEqual(
            self.win.text_buffer.text,
            "First line\nand its continuation.\n\nUp next\nSecond line.",
        )
        current, preview = self.win.text_buffer.tags
        self.assertEqual(
            current, ("current", 0, len("First line\nand its continuation."))
        )
        self.assertEqual(preview[0], "preview")
        self.win.seek_fraction(0.25)  # Exact cue end is a gap, not stale wording.
        self.assertEqual(self.win.text_buffer.text, "Up next\nSecond line.")
        self.assertIn("Next cue in 00:00:01.500", self.win.srt_status.label)


if __name__ == "__main__":
    unittest.main()
