# Teleprompter with timed SRT playback

This fork of [Nokse22/teleprompter](https://github.com/Nokse22/teleprompter) adds playback of SubRip (`.srt`) files. It displays the script using the file's original cue times, with the current cue in large, bold text and the upcoming cue in a smaller preview. Ordinary scrolling text is also supported.

## Build and launch this fork

To use this fork's changes, namely SRT playback, build it locally.

Requirements: Git, Flatpak, and Flatpak Builder. On Mint, install any missing tools:

```sh
sudo apt install git flatpak flatpak-builder
flatpak remote-add --user --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo
```

Clone the fork and enter its directory. If you already have the clone, just open a terminal in that directory.

```sh
git clone https://github.com/sschwindt/teleprompter.git
cd teleprompter
```

Build the code in this clone:

```sh
flatpak-builder --user --install --export-only builddir io.github.nokse22.teleprompter.json
```

Wait for the build to finish successfully. If it reports `FAILED` or `Error`, fix that error and rebuild before trying to launch. A completed build creates the launcher at `builddir/files/bin/teleprompter` inside your clone.

Close any other Teleprompter window, then launch this local build:

```sh
flatpak run --user --branch=master --command=/usr/bin/python3 io.github.nokse22.teleprompter /app/bin/teleprompter
```

Here, `/app/bin/teleprompter` is the launcher's path inside the build sandbox, where `builddir/files` is mounted as `/app`. It is not a directory to look for in your home folder. The command uses the sandbox's Python explicitly to run the launcher.

Run that last command from the project directory whenever you want to open this fork. After changing the code, run the build command again before launching.

The GNOME 49 SDK supplies the GTK/libadwaita versions this code needs. The commands above build and run your clone directly from `builddir`; they do not install the application or require your fork to be published on Flathub.

## Play an SRT file

1. In the window opened by the launch command above, click the **open a file button** or press **Ctrl+O**, then select your `.srt` file. To try the included example, choose [examples/timed-demo.srt](examples/timed-demo.srt).
2. The file opens **paused at 00:00:00.000**, with its timing offset set to zero. The smaller **Up next** preview lets you prepare for the first cue.
3. Click **Play** or press **Ctrl+Space**. The timeline starts at zero and each cue becomes active at its SRT start time.
4. Press **Ctrl+Space** again to pause. Press it once more to resume from the same position.
5. Playback stops at the last cue's end. Press **Play** again to start over.

Open the file through the app's file chooser; passing an SRT filename to the launch command is not currently supported.

### What you see

- **Current cue:** large, bold text, visible from its start time until its end time.
- **Up next:** the next cue in smaller text. This preview is visible before that cue's start time, including during gaps.
- **Status and timeline:** playback state, elapsed/total time, and the current cue number or a countdown to the next cue.

When cues overlap, all active cues appear together in their original file order. When a cue ends, its current-cue text clears; during a gap, the next-cue preview remains available.

### Controls

| Control | Action |
| --- | --- |
| **Ctrl+O** / folder button | Open an SRT or text file. |
| **Ctrl+Space** / Play button | Play or pause. |
| **Timeline slider** | Seek to a different time. |
| **Alt+Left** / previous button | Jump to the preceding cue start; from inside a cue, return to that cue's start first. |
| **Alt+Right** / next button | Jump to the next cue's start. |
| **Ctrl+Home** / restart button | Pause and return to zero. |
| **Offset (s)** | Shift when cues appear, in steps of 0.1 seconds. |
| **Font buttons / Preferences** | Adjust readability without changing cue timing. |
| **F11** | Toggle fullscreen. |

Seeking and cue navigation preserve the playing/paused state unless you reach the end of the script.

### Adjust the timing offset

Positive offsets show cues **later**; negative offsets show them **earlier**. For example, a cue beginning at 5 seconds appears at 7 seconds with an offset of `+2.0`, or at 3 seconds with an offset of `-2.0`.

Changing the offset shifts the cue schedule without moving the current timeline position or editing the file. A negative offset can move initial cues before zero, so those cues may be partly or completely skipped. Restart retains the offset; opening another SRT resets it to zero.

## SRT format and text handling

A simple file looks like this. Leave a blank line between cues:

```srt
1
00:00:02,000 --> 00:00:05,000
Welcome to the lecture.

2
00:00:06,000 --> 00:00:10,000
This cue contains
two lines of text.
```

UTF-8, UTF-8 with a BOM, and UTF-16 with a BOM are supported. Save files in other encodings as UTF-8 before opening them. Invalid files produce an error identifying the cue/line where possible and leave the current document and playback intact.

The app preserves Unicode, wording, and multiline cues. Common presentation tags (`<b>`, `<i>`, `<u>`, `<s>`, and `<font>`) are hidden in the display, `<br>` becomes a line break, and HTML entities such as `&amp;` display as characters. The source file is never modified.

SRT playback runs at **1×**. Font size, window size, mirroring, and the normal reading-speed setting do not affect the timestamps. The app uses elapsed monotonic time to avoid accumulating drift when UI updates run late; visible updates occur approximately every 20 ms while playing.

The displayed SRT text is read-only. To change the wording or timestamps, edit the source file separately and reopen it.

## Return to ordinary scrolling text

Open a normal text file or click **Paste from Clipboard**. This returns to editable, speed-based scrolling. Clipboard paste always uses plain-text mode; open an SRT file to activate timed playback.

## Run the automated checks

The parser, timeline, and controller tests use Python's standard library. They can run without GTK, a display server, or extra Python packages:

```sh
python3 -m unittest discover -s tests -v
python3 -m compileall -q src tests
git diff --check
```

The controller tests use GTK test doubles. Run the local build using the launch command above to check widget layout and rendering on your Linux desktop.

## Upstream and license

Based on [Nokse22/teleprompter](https://github.com/Nokse22/teleprompter). See [COPYING](COPYING) and [LICENSE](LICENSE) for license information.


