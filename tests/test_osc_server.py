"""Check that OSC uses the same timed playback operations as the GUI.

The GI stub permits these adapter tests to run without GTK or a UDP socket.
It does not exercise rendering, the GTK event loop, or the OSC wire format.
"""

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


def load_osc_module():
    repository = types.ModuleType("gi.repository")
    repository.GLib = types.SimpleNamespace()
    gi = types.ModuleType("gi")
    gi.repository = repository
    spec = importlib.util.spec_from_file_location(
        "teleprompter_osc_under_test",
        Path(__file__).parents[1] / "src" / "osc_server.py",
    )
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"gi": gi, "gi.repository": repository}):
        spec.loader.exec_module(module)
    return module


osc = load_osc_module()


class Settings:
    def __init__(self):
        self.values = {"speed": 100, "hmirror": False, "vmirror": False}

    def get_int(self, key):
        return self.values[key]

    def get_boolean(self, key):
        return self.values[key]

    def set_boolean(self, key, value):
        self.values[key] = value


class OSCPlaybackTests(unittest.TestCase):
    def setUp(self):
        self.window = Mock(
            spec=[
                "add_toast",
                "load_file",
                "set_plain_text",
                "pause",
                "play",
                "reset_playback",
                "seek_fraction",
                "set_playback_speed",
                "set_font_size",
                "is_fullscreen",
                "settings",
                "playing",
                "is_srt_mode",
                "playback_fraction",
                "scroll_text_view",
            ]
        )
        self.window.settings = Settings()
        self.window.playing = False
        self.window.is_srt_mode = True
        self.window.playback_fraction = 0.4
        self.window.scroll_text_view = types.SimpleNamespace(
            hmirror=False, vmirror=False
        )
        self.window.is_fullscreen.return_value = False
        self.server = osc.OSCServer(self.window)
        self.server._send_response = Mock()
        self.address = ("127.0.0.1", 7401)

    def test_load_uses_the_shared_srt_file_loader(self):
        self.server.handle_load_file(["lecture.srt"], self.address)
        self.window.load_file.assert_called_once_with("lecture.srt")
        self.server._send_response.assert_called_once_with(
            self.address, "/teleprompter/loaded", "lecture.srt"
        )

    def test_failed_srt_load_reports_error_instead_of_success(self):
        self.window.load_file.side_effect = ValueError("Invalid SRT timestamp")
        self.server.handle_load_file(["broken.srt"], self.address)
        self.server._send_response.assert_called_once_with(
            self.address,
            "/teleprompter/error",
            "Failed to load file: Invalid SRT timestamp",
        )

    def test_remote_text_uses_plain_text_mode_transition(self):
        self.server.handle_set_text(["A fresh script"], self.address)
        self.window.set_plain_text.assert_called_once_with("A fresh script")

    def test_pause_does_not_toggle_or_change_saved_speed(self):
        self.window.playing = True
        self.server.handle_pause([], self.address)
        self.window.pause.assert_called_once_with()
        self.window.play.assert_not_called()
        self.assertEqual(self.window.settings.get_int("speed"), 100)

    def test_repeated_play_cannot_pause_a_running_script(self):
        self.window.playing = True
        self.server.handle_play([], self.address)
        self.window.play.assert_not_called()
        self.server._send_response.assert_called_once_with(
            self.address, "/teleprompter/already_playing"
        )

    def test_stop_resets_timeline_in_both_playback_states(self):
        for playing in (False, True):
            with self.subTest(playing=playing):
                self.window.playing = playing
                self.window.reset_playback.reset_mock()
                self.server.handle_stop([], self.address)
                self.window.reset_playback.assert_called_once_with()

    def test_position_uses_timeline_seek_in_srt_mode(self):
        self.server.handle_set_position([0.75], self.address)
        self.window.seek_fraction.assert_called_once_with(0.75)

    def test_invalid_position_never_seeks(self):
        for position in (-0.1, 1.1, "not a number", None, float("nan")):
            with self.subTest(position=position):
                self.server._send_response.reset_mock()
                self.server.handle_set_position([position], self.address)
                self.assertEqual(
                    self.server._send_response.call_args.args[1],
                    "/teleprompter/error",
                )
        self.window.seek_fraction.assert_not_called()

    def test_srt_speed_rejection_preserves_timestamp_pacing(self):
        self.window.set_playback_speed.side_effect = ValueError(
            "SRT playback uses its original timestamps"
        )
        self.server.handle_set_speed([120], self.address)
        self.window.set_playback_speed.assert_called_once_with(120.0)
        self.server._send_response.assert_called_once_with(
            self.address,
            "/teleprompter/error",
            "SRT playback uses its original timestamps",
        )

    def test_font_size_uses_shared_font_settings_helper(self):
        self.server.handle_set_fontsize([55], self.address)
        self.window.set_font_size.assert_called_once_with(55)

    def test_mirror_toggles_persist_through_gsettings(self):
        for key in ("hmirror", "vmirror"):
            with self.subTest(key=key):
                handler = getattr(self.server, f"handle_{key}")
                handler([True], self.address)
                self.assertTrue(self.window.settings.get_boolean(key))
                handler([], self.address)
                self.assertFalse(self.window.settings.get_boolean(key))

    def test_reset_persists_mirrors_and_resets_the_timeline(self):
        self.window.settings.set_boolean("hmirror", True)
        self.window.settings.set_boolean("vmirror", True)
        self.server.handle_reset([], self.address)
        self.assertFalse(self.window.settings.get_boolean("hmirror"))
        self.assertFalse(self.window.settings.get_boolean("vmirror"))
        self.window.reset_playback.assert_called_once_with()

    def test_status_uses_time_fraction_and_zero_wpm_for_srt(self):
        for is_srt, expected_speed in ((True, 0), (False, 100)):
            with self.subTest(is_srt=is_srt):
                self.window.is_srt_mode = is_srt
                self.server._send_response.reset_mock()
                self.server.handle_get_status([], self.address)
                self.server._send_response.assert_any_call(
                    self.address, "/teleprompter/status/position", 0.4
                )
                self.server._send_response.assert_any_call(
                    self.address, "/teleprompter/status/speed", expected_speed
                )


if __name__ == "__main__":
    unittest.main()
