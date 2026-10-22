# SPDX-License-Identifier: GPL-3.0-or-later

"""Deterministic playback checks; no wall-clock waits or GTK required."""

import sys
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from srt import Cue
from timeline import SubtitleTimeline


class FakeClock:
    def __init__(self):
        self.seconds = 10.0

    def __call__(self):
        return self.seconds

    def advance(self, milliseconds):
        self.seconds += milliseconds / 1000


class SubtitleTimelineTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.cues = (Cue(1000, 2500, "First"), Cue(3000, 4000, "Second"))
        self.timeline = SubtitleTimeline(self.cues, clock=self.clock)

    def test_initial_delay_start_end_and_gap_boundaries(self):
        expected = (
            (0, (), 0),
            (999, (), 0),
            (1000, (0,), 1),
            (2499, (0,), 1),
            (2500, (), 1),
            (2999, (), 1),
            (3000, (1,), None),
            (3999, (1,), None),
            (4000, (), None),
        )
        for position, active, next_index in expected:
            with self.subTest(position=position):
                self.timeline.seek(position)
                self.assertEqual(self.timeline.position_ms, position)
                self.assertEqual(self.timeline.active_indices, active)
                self.assertEqual(self.timeline.next_index, next_index)

    def test_snapshot_is_coherent_when_clock_crosses_a_cue_boundary_between_reads(self):
        class AdvancingClock:
            def __init__(self):
                self.seconds = 0.0
                self.reads = 0

            def __call__(self):
                current = self.seconds
                self.seconds += 0.002
                self.reads += 1
                return current

        clock = AdvancingClock()
        timeline = SubtitleTimeline(self.cues, clock=clock)
        timeline.play()
        clock.seconds = 0.999
        reads_before = clock.reads
        snapshot = timeline.snapshot()
        self.assertEqual(clock.reads - reads_before, 1)
        self.assertEqual(snapshot.position_ms, 999)
        self.assertEqual(snapshot.active_indices, ())
        self.assertEqual(snapshot.next_index, 0)
        self.assertTrue(snapshot.playing)
        # A second sample has crossed the cue start; every field follows it.
        snapshot = timeline.snapshot()
        self.assertEqual(snapshot.position_ms, 1001)
        self.assertEqual(snapshot.active_indices, (0,))
        self.assertEqual(snapshot.next_index, 1)
        with self.assertRaises(FrozenInstanceError):
            snapshot.position_ms = 0

    def test_snapshot_at_end_clears_active_cues_and_reports_stopped(self):
        self.timeline.play()
        self.clock.advance(4000)
        snapshot = self.timeline.snapshot()
        self.assertEqual(snapshot.position_ms, 4000)
        self.assertEqual(snapshot.active_indices, ())
        self.assertIsNone(snapshot.next_index)
        self.assertFalse(snapshot.playing)

    def test_pause_resume_uses_elapsed_time_not_callback_counts(self):
        self.timeline.play()
        self.clock.advance(1234)
        self.timeline.pause()
        self.assertEqual(self.timeline.position_ms, 1234)
        self.assertFalse(self.timeline.playing)
        self.clock.advance(10000)
        self.assertEqual(self.timeline.position_ms, 1234)
        self.timeline.play()
        self.clock.advance(766)
        self.assertEqual(self.timeline.position_ms, 2000)
        self.assertEqual(self.timeline.active_indices, (0,))
        self.assertTrue(self.timeline.playing)

    def test_delayed_callback_skips_expired_cues_without_drift(self):
        self.timeline.play()
        self.clock.advance(3201)
        self.assertEqual(self.timeline.position_ms, 3201)
        self.assertEqual(self.timeline.active_indices, (1,))
        # Reading state repeatedly must not itself advance transport.
        for _ in range(50):
            self.assertEqual(self.timeline.position_ms, 3201)
        self.clock.advance(10000)
        self.assertEqual(self.timeline.position_ms, 4000)
        self.assertFalse(self.timeline.playing)
        self.assertTrue(self.timeline.finished)

    def test_exact_millisecond_after_fractional_anchor(self):
        self.timeline.play()
        self.clock.advance(1)
        self.assertEqual(self.timeline.position_ms, 1)
        self.clock.advance(998)
        self.assertEqual(self.timeline.active_indices, ())
        self.clock.advance(1)
        self.assertEqual(self.timeline.active_indices, (0,))

    def test_seek_while_playing_reanchors_the_clock(self):
        self.timeline.play()
        self.clock.advance(500)
        self.timeline.seek(3000)
        self.assertTrue(self.timeline.playing)
        self.assertEqual(self.timeline.active_indices, (1,))
        self.clock.advance(200)
        self.assertEqual(self.timeline.position_ms, 3200)
        self.timeline.seek(1000)
        self.clock.advance(123)
        self.assertEqual(self.timeline.position_ms, 1123)

    def test_seek_preserves_pause_and_clamps_bounds(self):
        self.timeline.seek(-500)
        self.assertEqual(self.timeline.position_ms, 0)
        self.timeline.seek(1500)
        self.assertFalse(self.timeline.playing)
        self.clock.advance(2000)
        self.assertEqual(self.timeline.position_ms, 1500)
        self.timeline.play()
        self.timeline.seek(999999)
        self.assertEqual(self.timeline.position_ms, 4000)
        self.assertFalse(self.timeline.playing)
        self.assertTrue(self.timeline.finished)

    def test_play_after_completion_restarts(self):
        self.timeline.seek(4000)
        self.timeline.play()
        self.assertTrue(self.timeline.playing)
        self.assertEqual(self.timeline.position_ms, 0)
        self.clock.advance(1000)
        self.assertEqual(self.timeline.active_indices, (0,))

    def test_repeated_play_does_not_reset_anchor(self):
        self.timeline.play()
        self.clock.advance(750)
        self.timeline.play()
        self.clock.advance(250)
        self.assertEqual(self.timeline.position_ms, 1000)

    def test_reset_pauses_at_zero(self):
        self.timeline.play()
        self.clock.advance(2000)
        self.timeline.reset()
        self.clock.advance(2000)
        self.assertEqual(self.timeline.position_ms, 0)
        self.assertFalse(self.timeline.playing)
        self.assertFalse(self.timeline.finished)

    def test_all_overlapping_cues_and_nonchronological_input(self):
        timeline = SubtitleTimeline(
            (
                Cue(3000, 5000, "Third"),
                Cue(1000, 6000, "Long first"),
                Cue(1000, 2500, "Simultaneous first"),
                Cue(2000, 4000, "Second"),
            ),
            clock=self.clock,
        )
        self.assertEqual(timeline.duration_ms, 6000)
        self.assertEqual(timeline.next_index, 1)
        timeline.seek(1000)
        self.assertEqual(timeline.active_indices, (1, 2))
        self.assertEqual(timeline.next_index, 3)
        timeline.seek(3000)
        self.assertEqual(timeline.active_indices, (0, 1, 3))
        self.assertIsNone(timeline.next_index)
        timeline.seek(4000)
        self.assertEqual(timeline.active_indices, (0, 1))

    def test_positive_offset_delays_each_cue_and_the_end(self):
        self.timeline.offset_ms = 500
        self.assertEqual(self.timeline.duration_ms, 4500)
        self.assertEqual(self.timeline.active_indices, ())
        self.assertEqual(self.timeline.next_index, 0)
        self.timeline.seek(1499)
        self.assertEqual(self.timeline.active_indices, ())
        self.timeline.seek(1500)
        self.assertEqual(self.timeline.active_indices, (0,))
        self.timeline.seek(4499)
        self.assertEqual(self.timeline.active_indices, (1,))
        self.timeline.seek(4500)
        self.assertEqual(self.timeline.active_indices, ())

    def test_negative_offset_shows_cues_earlier_including_at_zero(self):
        self.timeline.offset_ms = -1500
        self.assertEqual(self.timeline.duration_ms, 2500)
        self.assertEqual(self.timeline.position_ms, 0)
        self.assertEqual(self.timeline.active_indices, (0,))
        self.assertEqual(self.timeline.next_index, 1)
        self.timeline.seek(1500)
        self.assertEqual(self.timeline.active_indices, (1,))
        self.timeline.seek(2500)
        self.assertEqual(self.timeline.active_indices, ())

    def test_offset_change_preserves_transport_and_play_pause_state(self):
        self.timeline.seek(1500)
        self.timeline.offset_ms = 1000
        self.assertEqual(self.timeline.position_ms, 1500)
        self.assertFalse(self.timeline.playing)
        self.assertEqual(self.timeline.active_indices, ())
        self.timeline.play()
        self.clock.advance(100)
        self.timeline.offset_ms = -500
        self.assertEqual(self.timeline.position_ms, 1600)
        self.assertTrue(self.timeline.playing)
        self.clock.advance(100)
        self.assertEqual(self.timeline.position_ms, 1700)
        self.assertEqual(self.timeline.active_indices, (0,))

    def test_shorter_offset_clamps_and_pauses_at_new_end(self):
        self.timeline.seek(3500)
        self.timeline.play()
        self.timeline.offset_ms = -1000
        self.assertEqual(self.timeline.duration_ms, 3000)
        self.assertEqual(self.timeline.position_ms, 3000)
        self.assertFalse(self.timeline.playing)
        self.assertTrue(self.timeline.finished)

    def test_offset_before_all_cues_results_in_zero_duration(self):
        self.timeline.offset_ms = -5000
        self.assertEqual(self.timeline.duration_ms, 0)
        self.timeline.play()
        self.assertFalse(self.timeline.playing)
        self.assertTrue(self.timeline.finished)
        self.assertEqual(self.timeline.active_indices, ())
        self.assertIsNone(self.timeline.next_index)

    def test_reset_retains_offset(self):
        self.timeline.offset_ms = 500
        self.timeline.seek(3500)
        self.timeline.reset()
        self.assertEqual(self.timeline.position_ms, 0)
        self.assertEqual(self.timeline.offset_ms, 500)

    def test_empty_timeline_cannot_play(self):
        timeline = SubtitleTimeline((), clock=self.clock, offset_ms=500)
        timeline.play()
        self.assertFalse(timeline.playing)
        self.assertEqual(timeline.duration_ms, 0)
        self.assertEqual(timeline.active_indices, ())
        self.assertIsNone(timeline.next_index)

    def test_long_recording_does_not_accumulate_callback_drift(self):
        timeline = SubtitleTimeline((Cue(0, 7200000, "Two hours"),), clock=self.clock)
        timeline.play()
        self.clock.advance(7199999)
        self.assertEqual(timeline.position_ms, 7199999)
        self.assertEqual(timeline.active_indices, (0,))
        self.clock.advance(1)
        self.assertEqual(timeline.position_ms, 7200000)
        self.assertFalse(timeline.playing)

    def test_non_integer_milliseconds_are_rejected(self):
        with self.assertRaises(TypeError):
            self.timeline.seek(0.5)
        with self.assertRaises(TypeError):
            self.timeline.offset_ms = 0.5


if __name__ == "__main__":
    unittest.main()
