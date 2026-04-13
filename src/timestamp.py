"""Timestamp detection and parsing for log lines.

Auto-detects common timestamp formats and extracts datetime objects.
Uses a voting system across sample lines to determine the dominant format.
"""

import re
from datetime import datetime, timezone
from typing import Optional

# Each entry: (compiled regex, strptime format or None for epoch, group name)
# Order matters — more specific formats come first to avoid ambiguous matches.
TIMESTAMP_FORMATS = [
    # ISO 8601 with fractional seconds and timezone
    (
        re.compile(
            r"(?P<ts>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}\.\d+[+-]\d{2}:?\d{2})"
        ),
        None,  # handled specially
        "iso8601_frac_tz",
    ),
    # ISO 8601 with timezone
    (
        re.compile(
            r"(?P<ts>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}[+-]\d{2}:?\d{2})"
        ),
        None,
        "iso8601_tz",
    ),
    # ISO 8601 with Z
    (
        re.compile(r"(?P<ts>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}\.\d+Z)"),
        None,
        "iso8601_frac_z",
    ),
    (
        re.compile(r"(?P<ts>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}Z)"),
        None,
        "iso8601_z",
    ),
    # ISO 8601 with fractional seconds (no tz)
    (
        re.compile(r"(?P<ts>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}\.\d+)"),
        None,
        "iso8601_frac",
    ),
    # ISO 8601 basic
    (
        re.compile(r"(?P<ts>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})"),
        "%Y-%m-%dT%H:%M:%S",
        "iso8601",
    ),
    # Syslog: Mon DD HH:MM:SS (no year)
    (
        re.compile(
            r"(?P<ts>[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})"
        ),
        None,
        "syslog",
    ),
    # Apache/Nginx common log format: [DD/Mon/YYYY:HH:MM:SS +ZZZZ]
    (
        re.compile(
            r"\[(?P<ts>\d{2}/[A-Z][a-z]{2}/\d{4}:\d{2}:\d{2}:\d{2}\s+[+-]\d{4})\]"
        ),
        "%d/%b/%Y:%H:%M:%S %z",
        "apache",
    ),
    # Unix epoch seconds (10 digits) with optional fractional part
    (
        re.compile(r"(?<!\d)(?P<ts>1\d{9}(?:\.\d+)?)(?!\d)"),
        None,
        "epoch",
    ),
    # Unix epoch millis (13 digits)
    (
        re.compile(r"(?<!\d)(?P<ts>1\d{12})(?!\d)"),
        None,
        "epoch_ms",
    ),
    # MM/DD/YYYY HH:MM:SS
    (
        re.compile(r"(?P<ts>\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2})"),
        "%m/%d/%Y %H:%M:%S",
        "us_date",
    ),
    # DD/MM/YYYY HH:MM:SS — ambiguous with above, tried after
    (
        re.compile(r"(?P<ts>\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2})"),
        "%d/%m/%Y %H:%M:%S",
        "eu_date",
    ),
]

MONTH_ABBR = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}


def _parse_iso_flexible(text: str) -> Optional[datetime]:
    """Parse ISO 8601 variants flexibly."""
    text = text.replace("T", " ")
    # Handle Z suffix
    if text.endswith("Z"):
        text = text[:-1]
        has_utc = True
    else:
        has_utc = False

    # Split off timezone if present
    tz_offset = None
    for sep_pos in range(len(text) - 1, max(len(text) - 7, 0), -1):
        if text[sep_pos] in ("+", "-") and sep_pos > 10:
            tz_part = text[sep_pos:]
            text = text[:sep_pos]
            # Parse tz offset
            tz_part = tz_part.replace(":", "")
            try:
                sign = 1 if tz_part[0] == "+" else -1
                hours = int(tz_part[1:3])
                minutes = int(tz_part[3:5]) if len(tz_part) >= 5 else 0
                from datetime import timedelta
                tz_offset = timezone(timedelta(hours=sign * hours, minutes=sign * minutes))
            except (ValueError, IndexError):
                pass
            break

    # Split fractional seconds
    frac = 0.0
    if "." in text:
        base, frac_str = text.rsplit(".", 1)
        frac = float("0." + frac_str)
        text = base

    try:
        dt = datetime.strptime(text.strip(), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None

    if frac:
        from datetime import timedelta
        dt = dt + timedelta(seconds=frac)

    if tz_offset:
        dt = dt.replace(tzinfo=tz_offset)
    elif has_utc:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt


def _parse_syslog(text: str, reference_year: int = 2026) -> Optional[datetime]:
    """Parse syslog format (Mon DD HH:MM:SS) using a reference year."""
    parts = text.split()
    if len(parts) < 3:
        return None
    month_str = parts[0]
    day = int(parts[1])
    time_parts = parts[2].split(":")
    if len(time_parts) != 3:
        return None
    month = MONTH_ABBR.get(month_str)
    if month is None:
        return None
    return datetime(
        reference_year, month, day,
        int(time_parts[0]), int(time_parts[1]), int(time_parts[2])
    )


def parse_timestamp(text: str, format_name: str) -> Optional[datetime]:
    """Parse a timestamp string given its detected format name.

    `text` can be either a raw log line (the regex will extract the ts)
    or an already-extracted timestamp string.
    """
    for pattern, strp_fmt, name in TIMESTAMP_FORMATS:
        if name != format_name:
            continue

        # Try to match the regex first; if the text is already the
        # extracted timestamp, it may not match (e.g. apache without
        # brackets), so fall through to direct parsing.
        m = pattern.search(text)
        ts_str = m.group("ts") if m else text

        if name.startswith("iso8601"):
            return _parse_iso_flexible(ts_str)
        elif name == "syslog":
            return _parse_syslog(ts_str)
        elif name == "epoch":
            try:
                return datetime.fromtimestamp(float(ts_str))
            except (ValueError, OSError):
                return None
        elif name == "epoch_ms":
            try:
                return datetime.fromtimestamp(int(ts_str) / 1000.0)
            except (ValueError, OSError):
                return None
        elif strp_fmt:
            try:
                return datetime.strptime(ts_str, strp_fmt)
            except ValueError:
                return None
    return None


def extract_timestamp(line: str, format_name: str) -> tuple[Optional[datetime], Optional[str]]:
    """Extract timestamp from a line, returning (datetime, remaining_text).

    Returns (None, original_line) if no timestamp found.
    """
    for pattern, _, name in TIMESTAMP_FORMATS:
        if name != format_name:
            continue
        m = pattern.search(line)
        if m:
            dt = parse_timestamp(m.group("ts"), format_name)
            if dt:
                rest = line[:m.start()] + line[m.end():]
                return dt, rest.strip()
    return None, line


def detect_format(lines: list[str], sample_size: int = 50) -> Optional[str]:
    """Detect the timestamp format used in a list of log lines.

    Uses a voting system: tries each format on sample lines,
    returns the format that successfully parses the most lines.
    """
    sample = lines[:sample_size]
    votes: dict[str, int] = {}

    for line in sample:
        for pattern, _, name in TIMESTAMP_FORMATS:
            m = pattern.search(line)
            if m:
                ts_str = m.group("ts")
                dt = parse_timestamp(ts_str, name)
                if dt is not None:
                    votes[name] = votes.get(name, 0) + 1
                    break  # first matching format wins for this line

    if not votes:
        return None
    return max(votes, key=lambda k: votes[k])
