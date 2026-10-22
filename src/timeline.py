# SPDX-License-Identifier: GPL-3.0-or-later

"""A timestamp-driven subtitle transport, independent of GTK timer frequency."""

import time
from bisect import bisect_right
from dataclasses import dataclass
from math import floor
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .srt import Cue


@dataclass(frozen=True)
class TimelineSnapshot:
    """A coherent display state captured at one transport timestamp."""

    position_ms: int
    active_indices: tuple[int, ...]
    next_index: int | None
    playing: bool


class SubtitleTimeline:
    """Play subtitles against an injected monotonic clock in seconds.

    ``position_ms`` is the transport clock. A positive ``offset_ms`` displays
    every cue later: a cue at 1000 ms with a +500 ms offset starts when transport
    reaches 1500 ms. Negative offsets show cues earlier. Effective subtitle time
    is ``position_ms - offset_ms`` and can be negative during an initial delay.

    UI callbacks only read the clock; their frequency cannot accumulate drift.
    Cue intervals include the start and exclude the end. Overlapping cues are
    all active, in original file order. ``next_index`` identifies the earliest
    upcoming cue, even if another cue is currently active.
    """

    def __init__(self, cues, *, clock=time.monotonic, offset_ms=0):
        self.cues: tuple[Cue, ...] = tuple(cues)
        self._clock = clock
        self._position_ms = 0
        self._anchor = None
        self._last_end_ms = max((cue.end_ms for cue in self.cues), default=0)
        self._offset_ms = self._milliseconds(offset_ms)
        self._start_order = tuple(
            sorted(range(len(self.cues)), key=lambda index: self.cues[index].start_ms)
        )
        self._starts = tuple(self.cues[index].start_ms for index in self._start_order)

    @staticmethod
    def _milliseconds(value):
        if not isinstance(value, int):
            raise TypeError("Milliseconds must be an integer")
        return value

    @property
    def duration_ms(self):
        """Transport end, including a signed subtitle offset."""

        if not self.cues:
            return 0
        return max(0, self._last_end_ms + self._offset_ms)

    @property
    def position_ms(self):
        position = self._position_ms
        if self._anchor is not None:
            # The tiny tolerance avoids losing an exact millisecond to binary
            # float representation (e.g. (10.001 - 10.0) * 1000 < 1).
            elapsed_ms = max(0, floor((self._clock() - self._anchor) * 1000 + 1e-6))
            position += elapsed_ms
        if position >= self.duration_ms:
            self._position_ms = self.duration_ms
            self._anchor = None
            return self.duration_ms
        return position

    @property
    def playing(self):
        # Reading the position also stops transport at the last cue's end.
        _ = self.position_ms
        return self._anchor is not None

    @property
    def finished(self):
        return self.position_ms >= self.duration_ms

    @property
    def offset_ms(self):
        return self._offset_ms

    @offset_ms.setter
    def offset_ms(self, value):
        value = self._milliseconds(value)
        position = self.position_ms
        was_playing = self._anchor is not None
        self._offset_ms = value
        self._position_ms = min(position, self.duration_ms)
        self._anchor = (
            self._clock()
            if was_playing and self._position_ms < self.duration_ms
            else None
        )

    @property
    def active_indices(self):
        return self._active_indices_at(self.position_ms)

    def _active_indices_at(self, position_ms):
        cue_time = position_ms - self._offset_ms
        return tuple(
            index
            for index, cue in enumerate(self.cues)
            if cue.start_ms <= cue_time < cue.end_ms
        )

    @property
    def next_index(self):
        return self._next_index_at(self.position_ms)

    def _next_index_at(self, position_ms):
        cue_time = position_ms - self._offset_ms
        next_position = bisect_right(self._starts, cue_time)
        if next_position == len(self._start_order):
            return None
        return self._start_order[next_position]

    def snapshot(self) -> TimelineSnapshot:
        """Read the clock once so a redraw cannot mix states across a cue boundary."""

        position = self.position_ms
        return TimelineSnapshot(
            position_ms=position,
            active_indices=self._active_indices_at(position),
            next_index=self._next_index_at(position),
            playing=self._anchor is not None,
        )

    def play(self):
        """Start/resume transport; restart from zero after completion."""

        if self.playing or self.duration_ms == 0:
            return
        if self.finished:
            self._position_ms = 0
        self._anchor = self._clock()

    def pause(self):
        """Freeze at the current timestamp, preserving resume position."""

        self._position_ms = self.position_ms
        self._anchor = None

    def reset(self):
        """Pause and return to transport zero, retaining the offset."""

        self._position_ms = 0
        self._anchor = None

    def seek(self, position_ms):
        """Seek within transport bounds; preserve playback unless at the end."""

        position_ms = self._milliseconds(position_ms)
        was_playing = self.playing
        self._position_ms = max(0, min(position_ms, self.duration_ms))
        self._anchor = (
            self._clock()
            if was_playing and self._position_ms < self.duration_ms
            else None
        )
