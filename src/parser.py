"""Log file parser — reads log files into structured LogEntry objects."""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Iterator
import sys

from .timestamp import detect_format, extract_timestamp


@dataclass(frozen=True, slots=True)
class LogEntry:
    """A single parsed log line."""
    line_number: int
    timestamp: Optional[datetime]
    raw: str
    content: str  # line with timestamp stripped

    @property
    def has_timestamp(self) -> bool:
        return self.timestamp is not None


def parse_lines(lines: list[str], format_name: Optional[str] = None) -> list[LogEntry]:
    """Parse a list of raw log lines into LogEntry objects.

    If format_name is None, auto-detects the timestamp format.
    Lines without a parseable timestamp inherit the timestamp of
    the previous line (common for stack traces / multi-line messages).
    """
    if format_name is None:
        format_name = detect_format(lines)

    entries: list[LogEntry] = []
    last_ts: Optional[datetime] = None

    for i, raw in enumerate(lines):
        if not raw.strip():
            continue
        ts: Optional[datetime] = None
        content = raw

        if format_name:
            ts, content = extract_timestamp(raw, format_name)

        if ts is not None:
            last_ts = ts
        else:
            ts = last_ts  # inherit from previous

        entries.append(LogEntry(
            line_number=i + 1,
            timestamp=ts,
            raw=raw,
            content=content,
        ))

    return entries


def parse_file(path: str, format_name: Optional[str] = None) -> list[LogEntry]:
    """Parse a log file into LogEntry objects."""
    if path == "-":
        lines = sys.stdin.read().splitlines()
    else:
        with open(path, "r", errors="replace") as f:
            lines = f.read().splitlines()
    return parse_lines(lines, format_name)


def stream_entries(path: str, format_name: Optional[str] = None) -> Iterator[LogEntry]:
    """Yield LogEntry objects one at a time (memory-efficient for large files).

    For files, reads line-by-line without loading the entire file into memory.
    Auto-detection uses the first 100 lines to determine the format.
    """
    if path == "-":
        _source = sys.stdin
        _close = False
    else:
        _source = open(path, "r", errors="replace")
        _close = True

    try:
        # For auto-detection, buffer initial lines
        if format_name is None:
            sample_lines: list[str] = []
            for raw in _source:
                sample_lines.append(raw.rstrip("\n\r"))
                if len(sample_lines) >= 100:
                    break
            format_name = detect_format(sample_lines)

            # Yield entries from the sample
            last_ts: Optional[datetime] = None
            for i, raw in enumerate(sample_lines):
                if not raw.strip():
                    continue
                ts, content = (None, raw)
                if format_name:
                    ts, content = extract_timestamp(raw, format_name)
                if ts is not None:
                    last_ts = ts
                else:
                    ts = last_ts
                yield LogEntry(line_number=i + 1, timestamp=ts, raw=raw, content=content)

            line_offset = len(sample_lines)
        else:
            last_ts = None
            line_offset = 0

        # Continue streaming remaining lines
        for i, raw in enumerate(_source):
            raw = raw.rstrip("\n\r")
            if not raw.strip():
                continue
            ts, content = (None, raw)
            if format_name:
                ts, content = extract_timestamp(raw, format_name)
            if ts is not None:
                last_ts = ts
            else:
                ts = last_ts
            yield LogEntry(
                line_number=line_offset + i + 1,
                timestamp=ts, raw=raw, content=content,
            )
    finally:
        if _close:
            _source.close()
