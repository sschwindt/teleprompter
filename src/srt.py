# SPDX-License-Identifier: GPL-3.0-or-later

"""Read SubRip subtitles without changing the script's wording.

This module deliberately has no GTK dependency so parsing can also be checked
from the command line. Cue text is plain text, including any literal markup in
the input; callers must not interpret it as trusted UI markup.
"""

import re
from dataclasses import dataclass
from html import unescape


@dataclass(frozen=True)
class Cue:
    """One subtitle, active from ``start_ms`` up to (excluding) ``end_ms``."""

    start_ms: int
    end_ms: int
    text: str

    def __post_init__(self):
        if self.start_ms < 0 or self.end_ms <= self.start_ms:
            raise ValueError("A subtitle must have a nonnegative start and a later end")


class SubtitleParseError(ValueError):
    """A readable import error, optionally identifying a source line and cue."""

    def __init__(self, message, *, line=None, cue_number=None):
        self.line = line
        self.cue_number = cue_number
        context = []
        if line is not None:
            context.append(f"line {line}")
        if cue_number is not None:
            context.append(f"cue {cue_number}")
        prefix = f"{', '.join(context)}: " if context else ""
        super().__init__(prefix + message)


# A dot is accepted as a common export variant of SubRip's comma separator.
_STAMP = r"(?P<{name}>\d+:\d{{2}}:\d{{2}}[,\.]\d{{3}})"
_TIMING = re.compile(
    r"^[ \t]*"
    + _STAMP.format(name="start")
    + r"[ \t]*-->[ \t]*"
    + _STAMP.format(name="end")
    # Some SRT exporters include positioning metadata after the end time.
    + r"(?:[ \t]+X1:\d+[ \t]+X2:\d+[ \t]+Y1:\d+[ \t]+Y2:\d+)?"
    + r"[ \t]*$",
    re.IGNORECASE,
)
_COUNTER = re.compile(r"^[ \t]*\d+[ \t]*$")
_PRESENTATION_TAG = re.compile(r"</?(?:b|i|u|s|font)(?:\s+[^<>]*)?\s*>", re.IGNORECASE)
_LINE_BREAK_TAG = re.compile(r"<br\s*/?>", re.IGNORECASE)
_ENTITY = re.compile(r"&(?:#[0-9]+|#[xX][0-9a-fA-F]+|[A-Za-z][A-Za-z0-9]+);")


def display_text(text: str) -> str:
    """Remove common SRT presentation markup without interpreting arbitrary HTML.

    Cue source text remains unchanged. Entity decoding happens after tag removal
    so escaped literal ``&lt;i&gt;`` stays visible as ``<i>``. Only complete entities
    ending with a semicolon are decoded; ordinary wording such as ``&not`` stays
    intact. The result is still plain text, not safe-to-render HTML or UI markup.
    """

    text = _LINE_BREAK_TAG.sub("\n", text)
    text = _PRESENTATION_TAG.sub("", text)
    return _ENTITY.sub(lambda match: unescape(match[0]), text)


def decode_text(data: bytes) -> str:
    """Decode UTF-8 or BOM-marked UTF-16, rejecting lossy fallback decoding."""

    if data.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        raise SubtitleParseError("UTF-32 is not supported; save this file as UTF-8")
    encoding = "utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
    try:
        text = data.decode(encoding)
    except UnicodeError as error:
        raise SubtitleParseError(
            "Cannot decode this file; save it as UTF-8 or UTF-16 with a BOM"
        ) from error
    if "\x00" in text:
        raise SubtitleParseError(
            "Unexpected NUL characters; save this file as UTF-8 or UTF-16 with a BOM"
        )
    return text


def _timestamp_ms(stamp, *, line, cue_number):
    hours, minutes, seconds = stamp.replace(".", ",").split(":")
    seconds, milliseconds = seconds.split(",")
    if int(minutes) > 59 or int(seconds) > 59:
        raise SubtitleParseError(
            "Timestamp minutes and seconds must be between 00 and 59",
            line=line,
            cue_number=cue_number,
        )
    return (int(hours) * 3600 + int(minutes) * 60 + int(seconds)) * 1000 + int(
        milliseconds
    )


def parse_srt(text: str) -> tuple[Cue, ...]:
    """Parse SRT cues in file order, preserving their Unicode and line breaks.

    Counters are optional and need not be consecutive. Overlapping and
    nonchronological cues are retained. Blank lines separate cues, as in SubRip;
    line-ending conventions and a leading BOM are normalized, but cue wording,
    whitespace, and literal formatting tags are otherwise left unchanged.
    """

    normalized = text.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    cues = []
    index = 0
    while index < len(lines):
        if not lines[index].strip():
            index += 1
            continue

        cue_number = len(cues) + 1
        if _COUNTER.fullmatch(lines[index]):
            cue_number = int(lines[index].strip())
            index += 1
        line_number = index + 1
        match = _TIMING.fullmatch(lines[index]) if index < len(lines) else None
        if match is None:
            raise SubtitleParseError(
                "Expected a timestamp line such as 00:00:01,000 --> 00:00:03,000",
                line=line_number,
                cue_number=cue_number,
            )

        start_ms = _timestamp_ms(
            match["start"], line=line_number, cue_number=cue_number
        )
        end_ms = _timestamp_ms(match["end"], line=line_number, cue_number=cue_number)
        if end_ms <= start_ms:
            raise SubtitleParseError(
                "Subtitle end must be later than its start",
                line=line_number,
                cue_number=cue_number,
            )

        index += 1
        body = []
        while index < len(lines) and lines[index].strip():
            if _TIMING.fullmatch(lines[index]):
                raise SubtitleParseError(
                    "Missing blank line between subtitles",
                    line=index + 1,
                    cue_number=cue_number,
                )
            body.append(lines[index])
            index += 1
        if not body:
            raise SubtitleParseError(
                "Subtitle has no text",
                line=line_number,
                cue_number=cue_number,
            )
        cues.append(Cue(start_ms, end_ms, "\n".join(body)))

    if not cues:
        raise SubtitleParseError("No subtitles found in this file")
    return tuple(cues)
