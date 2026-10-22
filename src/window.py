# window.py
#
# Copyright 2025 Nokse <nokse@posteo.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
# SPDX-License-Identifier: GPL-3.0-or-later

from gettext import gettext as _
from pathlib import Path

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango

from .scroll_text_view import TeleprompterScrollTextView
from .srt import SubtitleParseError, decode_text, display_text, parse_srt
from .timeline import SubtitleTimeline

GObject.type_register(TeleprompterScrollTextView)


def format_time(milliseconds):
    """Format the transport position without rounding into the next cue."""
    milliseconds = max(0, int(milliseconds))
    seconds, milliseconds = divmod(milliseconds, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}.{milliseconds:03}"


@Gtk.Template(resource_path="/io/github/nokse22/teleprompter/gtk/window.ui")
class TeleprompterWindow(Adw.ApplicationWindow):
    __gtype_name__ = "TeleprompterWindow"

    scroll_text_view = Gtk.Template.Child()
    start_button = Gtk.Template.Child()
    fullscreen_button = Gtk.Template.Child()
    overlay = Gtk.Template.Child()
    title_widget = Gtk.Template.Child()
    increase_speed_button = Gtk.Template.Child()
    decrease_speed_button = Gtk.Template.Child()
    srt_controls = Gtk.Template.Child()
    srt_status = Gtk.Template.Child()
    srt_seek = Gtk.Template.Child()
    srt_offset = Gtk.Template.Child()

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.playing = False
        self.timeline = None
        self._playback_source_id = 0
        self._updating_srt_controls = False
        self._rendered_cues = None
        self._closed = False
        self.settings = Gio.Settings("io.github.nokse22.teleprompter")

        self.text_buffer = self.scroll_text_view.get_buffer()
        self.scrolled_window = self.scroll_text_view.get_scrolled_window()
        self.prompt_text_view = self.scroll_text_view.text_view._text_view

        self.text_tag = Gtk.TextTag()
        self.highlight_tag = Gtk.TextTag()
        self.current_cue_tag = Gtk.TextTag(weight=Pango.Weight.BOLD)
        self.preview_tag = Gtk.TextTag(scale=0.7)
        for tag in (
            self.text_tag,
            self.highlight_tag,
            self.current_cue_tag,
            self.preview_tag,
        ):
            self.text_buffer.get_tag_table().add(tag)
        self.update_text_tags()

        self.settings.bind("hmirror", self.scroll_text_view, "hmirror", 0)
        self.settings.bind("vmirror", self.scroll_text_view, "vmirror", 0)
        adjustment = self.scrolled_window.get_vadjustment()
        adjustment.connect("value-changed", self.on_scroll_changed)

        for key in ("text-color", "highlight-color", "font", "bold-highlight"):
            self.settings.connect(f"changed::{key}", self.update_text_tags)
        self.text_buffer.connect("paste-done", self.update_text_tags)
        self.text_buffer.connect("changed", self.update_text_tags)
        self.connect("close-request", self._on_close_request)

        for name, callback in (
            ("restart", self.reset_playback),
            ("previous-cue", lambda *_: self.step_cue(-1)),
            ("next-cue", lambda *_: self.step_cue(1)),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)
        self._update_mode_controls()

    @property
    def is_srt_mode(self):
        return self.timeline is not None

    @property
    def playback_fraction(self):
        if self.is_srt_mode:
            duration = self.timeline.duration_ms
            return self.timeline.position_ms / duration if duration else 0.0
        adjustment = self.scrolled_window.get_vadjustment()
        maximum = adjustment.get_upper() - adjustment.get_page_size()
        return adjustment.get_value() / maximum if maximum > 0 else 0.0

    def on_scroll_changed(self, adjustment):
        self.title_widget.set_visible(self.is_srt_mode or adjustment.get_value() == 0)

    def show_file_chooser_dialog(self):
        formats = Gtk.FileFilter(name=_("Text and SRT files"))
        for pattern in ("*.txt", "*.text", "*.srt", "*.SRT"):
            formats.add_pattern(pattern)
        all_files = Gtk.FileFilter(name=_("All files"))
        all_files.add_pattern("*")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(formats)
        filters.append(all_files)
        dialog = Gtk.FileDialog(
            title=_("Open File"), filters=filters, default_filter=formats
        )
        dialog.open(self, None, self.on_open_file_response)

    def on_open_file_response(self, dialog, response):
        try:
            selected_file = dialog.open_finish(response)
        except GLib.Error:
            # Dismissing the chooser is not a document load.
            return
        if selected_file:
            # Gio also supports files selected through the Flatpak file portal.
            selected_file.load_contents_async(
                None, self._on_file_contents_loaded, selected_file.get_basename()
            )

    def _on_file_contents_loaded(self, file, response, name):
        if self._closed:
            return
        try:
            success, contents, _etag = file.load_contents_finish(response)
            if not success:
                raise OSError(_("Could not read the selected file"))
            self.load_document(contents, name or "")
            self.add_toast(_("File successfully opened"))
        except (GLib.Error, OSError, SubtitleParseError) as error:
            self.add_toast(str(error))

    def load_file(self, file_path):
        """Shared loader for the file chooser and OSC; failed loads are atomic."""
        path = Path(file_path)
        self.load_document(path.read_bytes(), path.name)

    def load_document(self, contents, name):
        text = decode_text(contents)
        if Path(name).suffix.lower() == ".srt":
            # Validate before replacing the current document or stopping playback.
            cues = parse_srt(text)
            timeline = SubtitleTimeline(cues)
            self.pause()
            self.timeline = timeline
            self._rendered_cues = None
            self._updating_srt_controls = True
            try:
                self.srt_offset.set_value(0)
            finally:
                self._updating_srt_controls = False
            self._update_mode_controls()
            self.title_widget.set_label(name)
            self._render_srt()
        else:
            self.set_plain_text(text)

    def set_plain_text(self, content):
        self.pause()
        self.timeline = None
        self._rendered_cues = None
        self._update_mode_controls()
        self.title_widget.set_label(_("Teleprompter"))
        self.text_buffer.set_text(content)
        self.scrolled_window.get_vadjustment().set_value(0)

    def _update_mode_controls(self):
        srt_mode = self.is_srt_mode
        self.srt_controls.set_visible(srt_mode)
        self.increase_speed_button.set_sensitive(not srt_mode)
        self.decrease_speed_button.set_sensitive(not srt_mode)
        self.prompt_text_view.set_editable(not srt_mode)
        self.prompt_text_view.set_cursor_visible(not srt_mode)
        for name in ("previous-cue", "next-cue"):
            self.lookup_action(name).set_enabled(srt_mode)

    def _cancel_playback_source(self):
        if self._playback_source_id:
            GLib.source_remove(self._playback_source_id)
            self._playback_source_id = 0

    def _update_play_button(self):
        self.start_button.set_icon_name(
            "media-playback-pause-symbolic"
            if self.playing
            else "media-playback-start-symbolic"
        )

    def pause(self):
        self._cancel_playback_source()
        if self.is_srt_mode:
            self.timeline.pause()
        self.playing = False
        self._update_play_button()
        if self.is_srt_mode:
            self._render_srt()

    def reset_playback(self, *args):
        self.pause()
        if self.is_srt_mode:
            self.timeline.reset()
            self._render_srt()
        else:
            self.scrolled_window.get_vadjustment().set_value(0)

    def seek_fraction(self, fraction):
        fraction = max(0.0, min(1.0, float(fraction)))
        if self.is_srt_mode:
            self.timeline.seek(round(fraction * self.timeline.duration_ms))
            self._after_srt_seek()
        else:
            adjustment = self.scrolled_window.get_vadjustment()
            maximum = max(0, adjustment.get_upper() - adjustment.get_page_size())
            adjustment.set_value(fraction * maximum)

    def _after_srt_seek(self):
        self.playing = self.timeline.playing
        if not self.playing:
            self._cancel_playback_source()
        self._update_play_button()
        self._render_srt()

    def step_cue(self, direction):
        if not self.is_srt_mode:
            return
        position = self.timeline.position_ms
        starts = sorted({
            max(0, cue.start_ms + self.timeline.offset_ms) for cue in self.timeline.cues
        })
        if direction > 0:
            target = next(
                (start for start in starts if start > position),
                self.timeline.duration_ms,
            )
        else:
            target = next((start for start in reversed(starts) if start < position), 0)
        self.timeline.seek(target)
        self._after_srt_seek()

    @Gtk.Template.Callback("srt_seek_changed")
    def on_srt_seek_changed(self, scale):
        if self.is_srt_mode and not self._updating_srt_controls:
            self.timeline.seek(round(scale.get_value() * 1000))
            self._after_srt_seek()

    @Gtk.Template.Callback("srt_offset_changed")
    def on_srt_offset_changed(self, spin):
        if self.is_srt_mode and not self._updating_srt_controls:
            self.timeline.offset_ms = round(spin.get_value() * 1000)
            self._after_srt_seek()

    def _render_srt(self):
        timeline = self.timeline
        snapshot = timeline.snapshot()
        position = snapshot.position_ms
        active = snapshot.active_indices
        upcoming = snapshot.next_index
        state = (active, upcoming)
        if state != self._rendered_cues:
            current = "\n\n".join(
                display_text(timeline.cues[index].text) for index in active
            )
            preview = ""
            if upcoming is not None:
                preview = (
                    _("Up next") + "\n" + display_text(timeline.cues[upcoming].text)
                )
            separator = "\n\n" if current and preview else ""
            self.text_buffer.set_text(current + separator + preview)
            if current:
                self.text_buffer.apply_tag(
                    self.current_cue_tag,
                    self.text_buffer.get_start_iter(),
                    self.text_buffer.get_iter_at_offset(len(current)),
                )
            if preview:
                self.text_buffer.apply_tag(
                    self.preview_tag,
                    self.text_buffer.get_iter_at_offset(len(current + separator)),
                    self.text_buffer.get_end_iter(),
                )
            self.scrolled_window.get_vadjustment().set_value(0)
            self._rendered_cues = state

        self._updating_srt_controls = True
        try:
            self.srt_seek.set_range(0, max(0.001, timeline.duration_ms / 1000))
            self.srt_seek.set_value(position / 1000)
        finally:
            self._updating_srt_controls = False

        if active:
            cue_status = _("Cue %(cue)d of %(total)d") % {
                "cue": active[0] + 1,
                "total": len(timeline.cues),
            }
        elif upcoming is not None:
            until = timeline.cues[upcoming].start_ms + timeline.offset_ms - position
            cue_status = _("Next cue in %(time)s") % {"time": format_time(until)}
        else:
            cue_status = _("End of script")
        playback_state = _("Playing") if self.playing else _("Paused")
        self.srt_status.set_label(
            f"{playback_state} · {format_time(position)} / "
            f"{format_time(timeline.duration_ms)} · {cue_status}"
        )

    def _on_playback_tick(self):
        if not self.playing or self._closed:
            self._playback_source_id = 0
            return False
        if self.is_srt_mode:
            self._render_srt()
            self.playing = self.timeline.playing
            if not self.playing:
                self._update_play_button()
                self._render_srt()
        else:
            self.autoscroll(self.scrolled_window)
        if not self.playing:
            self._playback_source_id = 0
        return self.playing

    def autoscroll(self, scrolled_window):
        adjustment = scrolled_window.get_vadjustment()
        maximum = max(0, adjustment.get_upper() - adjustment.get_page_size())
        adjustment.set_value(min(maximum, adjustment.get_value() + self.wpm_to_speed()))
        if adjustment.get_value() >= maximum:
            self.playing = False
            self._update_play_button()
        return self.playing

    def update_text_tags(self, *args):
        self.text_tag.set_property("foreground", self.settings.get_string("text-color"))
        self.text_tag.set_property(
            "font-desc", Pango.FontDescription(self.settings.get_string("font"))
        )
        self.highlight_tag.set_property(
            "foreground", self.settings.get_string("highlight-color")
        )
        self.highlight_tag.set_property(
            "weight",
            Pango.Weight.BOLD
            if self.settings.get_boolean("bold-highlight")
            else Pango.Weight.NORMAL,
        )
        self.text_buffer.apply_tag(
            self.text_tag,
            self.text_buffer.get_start_iter(),
            self.text_buffer.get_end_iter(),
        )
        if not self.is_srt_mode:
            self.search_and_mark_highlight()

    def search_and_mark_highlight(self):
        # Iterative so long scripts with many marked phrases cannot exhaust recursion.
        start = self.text_buffer.get_start_iter()
        end = self.text_buffer.get_end_iter()
        while match := start.forward_search("[", Gtk.TextSearchFlags(0), end):
            match_start, after_open = match
            closing = after_open.forward_search("]", Gtk.TextSearchFlags(0), end)
            if not closing:
                break
            _closing_start, after_close = closing
            nested = after_open.forward_search("[", Gtk.TextSearchFlags(0), after_close)
            if not nested:
                self.text_buffer.apply_tag(self.highlight_tag, match_start, after_close)
            start = after_open

    def wpm_to_speed(self):
        font = Pango.FontDescription(self.settings.get_string("font")).get_size()
        points = font / Pango.SCALE
        width = max(1, self.scroll_text_view.get_width())
        return max(0.25, self.settings.get_int("speed") * points * 0.2 / width)

    def set_playback_speed(self, speed):
        if self.is_srt_mode:
            raise ValueError(_("SRT playback uses its timestamps, not a reading speed"))
        self.settings.set_int("speed", round(speed))

    def set_font_size(self, points):
        description = Pango.FontDescription(self.settings.get_string("font"))
        description.set_size(round(max(10, points) * Pango.SCALE))
        self.settings.set_string("font", description.to_string())

    def change_font_size(self, amount):
        description = Pango.FontDescription(self.settings.get_string("font"))
        self.set_font_size(description.get_size() / Pango.SCALE + amount)

    def add_toast(self, message):
        toast = Adw.Toast(title=message, timeout=5)
        self.overlay.add_toast(toast)

    @Gtk.Template.Callback("play_button_clicked")
    def play(self, *args):
        if self.playing:
            self.pause()
            return
        self._cancel_playback_source()
        if self.is_srt_mode:
            self.timeline.play()
            self.playing = self.timeline.playing
            self._render_srt()
        else:
            self.playing = True
        self._update_play_button()
        if self.playing:
            # The timeout only refreshes the display; the SRT clock is monotonic.
            interval = 20 if self.is_srt_mode else 10
            self._playback_source_id = GLib.timeout_add(
                interval, self._on_playback_tick
            )

    @Gtk.Template.Callback("open_button_clicked")
    def open_button_clicked(self, *args):
        self.show_file_chooser_dialog()

    @Gtk.Template.Callback("increase_speed_button_clicked")
    def increase_speed_button_clicked(self, *args):
        if not self.is_srt_mode:
            self.set_playback_speed(self.settings.get_int("speed") + 10)

    @Gtk.Template.Callback("decrease_speed_button_clicked")
    def decrease_speed_button_clicked(self, *args):
        if not self.is_srt_mode:
            self.set_playback_speed(max(40, self.settings.get_int("speed") - 10))

    @Gtk.Template.Callback("paste_button_clicked")
    def paste_button_clicked(self, *args):
        clipboard = Gdk.Display.get_default().get_clipboard()

        def callback(clipboard, response, _data):
            if self._closed:
                return
            try:
                text = clipboard.read_text_finish(response)
                if text is not None:
                    self.set_plain_text(text)
                    self.add_toast(_("Pasted Clipboard Content"))
            except GLib.Error as error:
                self.add_toast(str(error))

        clipboard.read_text_async(None, callback, None)

    @Gtk.Template.Callback("decrease_font_button_clicked")
    def decrease_font_button_clicked(self, *args):
        self.change_font_size(-5)

    @Gtk.Template.Callback("increase_font_button_clicked")
    def increase_font_button_clicked(self, *args):
        self.change_font_size(5)

    @Gtk.Template.Callback("fullscreen_button_clicked")
    def toggle_fullscreen(self, *args):
        self.unfullscreen() if self.is_fullscreen() else self.fullscreen()

    @Gtk.Template.Callback("on_fullscreened_changed")
    def on_fullscreened_changed(self, *_args):
        self.fullscreen_button.set_icon_name(
            "view-restore-symbolic"
            if self.is_fullscreen()
            else "view-fullscreen-symbolic"
        )

    def _on_close_request(self, *_args):
        self._closed = True
        self.pause()
        return False
