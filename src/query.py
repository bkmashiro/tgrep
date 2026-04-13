"""Temporal query engine — the core of tgrep.

Supports:
- Time-windowed searches (before/after an event within a duration)
- Event sequence matching (A then B then C within N seconds)
- Temporal correlation detection (what co-occurs with a pattern?)
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from .parser import LogEntry


# ---------------------------------------------------------------------------
# Duration parsing
# ---------------------------------------------------------------------------

_DURATION_RE = re.compile(
    r"(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?(?:(\d+)ms)?"
)


def parse_duration(text: str) -> timedelta:
    """Parse a human duration string like '5m30s', '2h', '500ms' into timedelta."""
    text = text.strip().lower()
    # Try pure-number shorthand: treat as seconds
    try:
        return timedelta(seconds=float(text))
    except ValueError:
        pass

    m = _DURATION_RE.fullmatch(text)
    if not m or not any(m.groups()):
        raise ValueError(f"Cannot parse duration: {text!r}")

    days = int(m.group(1) or 0)
    hours = int(m.group(2) or 0)
    minutes = int(m.group(3) or 0)
    seconds = int(m.group(4) or 0)
    millis = int(m.group(5) or 0)

    return timedelta(
        days=days, hours=hours, minutes=minutes,
        seconds=seconds, milliseconds=millis,
    )


# ---------------------------------------------------------------------------
# Window search: find entries near an anchor event
# ---------------------------------------------------------------------------

@dataclass
class WindowResult:
    """Result of a time-window search."""
    anchor: LogEntry
    matches: list[LogEntry]


def search_window(
    entries: list[LogEntry],
    anchor_pattern: str,
    window_before: Optional[timedelta] = None,
    window_after: Optional[timedelta] = None,
    content_filter: Optional[str] = None,
) -> list[WindowResult]:
    """Find all entries within a time window around anchor events.

    Args:
        entries: Parsed log entries (must be sorted by timestamp).
        anchor_pattern: Regex to identify anchor events.
        window_before: How far before the anchor to look.
        window_after: How far after the anchor to look.
        content_filter: Optional regex to filter window matches.
    """
    anchor_re = re.compile(anchor_pattern, re.IGNORECASE)
    filter_re = re.compile(content_filter, re.IGNORECASE) if content_filter else None

    # Find anchor indices
    anchors = [
        (i, e) for i, e in enumerate(entries)
        if e.has_timestamp and anchor_re.search(e.content)
    ]

    results: list[WindowResult] = []
    for _idx, anchor in anchors:
        t = anchor.timestamp
        assert t is not None
        t_start = t - window_before if window_before else t
        t_end = t + window_after if window_after else t
        matches = []
        for e in entries:
            if e is anchor:
                continue
            if not e.has_timestamp:
                continue
            assert e.timestamp is not None
            if t_start <= e.timestamp <= t_end:
                if filter_re is None or filter_re.search(e.content):
                    matches.append(e)
        if matches:
            results.append(WindowResult(anchor=anchor, matches=matches))

    return results


# ---------------------------------------------------------------------------
# Sequence matching: A followed by B [followed by C] within duration
# ---------------------------------------------------------------------------

@dataclass
class SequenceMatch:
    """A matched sequence of events."""
    events: list[LogEntry]
    span: timedelta  # total time from first to last event


def match_sequences(
    entries: list[LogEntry],
    patterns: list[str],
    within: timedelta,
    not_followed_by: Optional[str] = None,
) -> list[SequenceMatch]:
    """Find sequences of events matching patterns in order within a time window.

    Args:
        entries: Parsed log entries.
        patterns: List of regex patterns to match in order.
        within: Maximum duration from first to last event.
        not_followed_by: Optional pattern that must NOT appear between first and last.
    """
    if not patterns:
        return []

    compiled = [re.compile(p, re.IGNORECASE) for p in patterns]
    nfb_re = re.compile(not_followed_by, re.IGNORECASE) if not_followed_by else None

    timed_entries = [(i, e) for i, e in enumerate(entries) if e.has_timestamp]
    results: list[SequenceMatch] = []

    for start_idx, (entry_idx, first_entry) in enumerate(timed_entries):
        if not compiled[0].search(first_entry.content):
            continue

        assert first_entry.timestamp is not None
        t_start = first_entry.timestamp
        t_limit = t_start + within

        chain: list[LogEntry] = [first_entry]
        pattern_idx = 1

        for _, candidate in timed_entries[start_idx + 1:]:
            assert candidate.timestamp is not None
            if candidate.timestamp > t_limit:
                break

            # Check negative pattern
            if nfb_re and nfb_re.search(candidate.content):
                break

            if pattern_idx < len(compiled) and compiled[pattern_idx].search(candidate.content):
                chain.append(candidate)
                pattern_idx += 1

            if pattern_idx == len(compiled):
                span = chain[-1].timestamp - t_start  # type: ignore[operator]
                results.append(SequenceMatch(events=chain, span=span))
                break

    return results


# ---------------------------------------------------------------------------
# Temporal correlation: what patterns tend to appear near a target?
# ---------------------------------------------------------------------------

@dataclass
class Correlation:
    """A pattern that co-occurs temporally with the target."""
    pattern: str
    count: int
    avg_offset_seconds: float
    direction: str  # 'before', 'after', 'both'


def find_correlations(
    entries: list[LogEntry],
    target_pattern: str,
    window: timedelta,
    min_occurrences: int = 2,
    top_n: int = 10,
    token_min_len: int = 4,
) -> list[Correlation]:
    """Find content tokens that frequently appear near target events.

    Extracts "interesting" tokens from log lines in the window around
    each target event, then ranks by frequency.
    """
    target_re = re.compile(target_pattern, re.IGNORECASE)

    # Tokens to ignore (too generic)
    stop_tokens = {
        "the", "and", "for", "that", "this", "with", "from", "have", "been",
        "not", "are", "was", "were", "will", "can", "but", "info", "debug",
        "warn", "error", "trace", "log", "http", "https", "null", "none",
        "true", "false",
    }

    target_entries = [e for e in entries if e.has_timestamp and target_re.search(e.content)]
    if not target_entries:
        return []

    # Collect tokens near each target event
    token_stats: dict[str, list[float]] = {}  # token -> list of offsets in seconds
    token_re = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*")

    for target in target_entries:
        t = target.timestamp
        assert t is not None
        for e in entries:
            if e is target or not e.has_timestamp:
                continue
            assert e.timestamp is not None
            offset = (e.timestamp - t).total_seconds()
            if abs(offset) > window.total_seconds():
                continue
            # Extract tokens
            tokens = set(token_re.findall(e.content.lower()))
            for tok in tokens:
                if len(tok) < token_min_len or tok in stop_tokens:
                    continue
                if target_re.search(tok):
                    continue  # skip tokens that match the target itself
                token_stats.setdefault(tok, []).append(offset)

    # Rank by frequency, filter by min_occurrences
    correlations: list[Correlation] = []
    for token, offsets in token_stats.items():
        if len(offsets) < min_occurrences:
            continue
        avg_offset = sum(offsets) / len(offsets)
        if avg_offset < -1:
            direction = "before"
        elif avg_offset > 1:
            direction = "after"
        else:
            direction = "both"
        correlations.append(Correlation(
            pattern=token,
            count=len(offsets),
            avg_offset_seconds=avg_offset,
            direction=direction,
        ))

    correlations.sort(key=lambda c: c.count, reverse=True)
    return correlations[:top_n]


# ---------------------------------------------------------------------------
# Frequency analysis: event rate over time
# ---------------------------------------------------------------------------

@dataclass
class TimeBucket:
    """Event count within a time bucket."""
    start: datetime
    end: datetime
    count: int
    entries: list[LogEntry] = field(default_factory=list)


def frequency_analysis(
    entries: list[LogEntry],
    pattern: str,
    bucket_size: timedelta,
) -> list[TimeBucket]:
    """Count matching events per time bucket."""
    pat_re = re.compile(pattern, re.IGNORECASE)
    matching = [e for e in entries if e.has_timestamp and pat_re.search(e.content)]

    if not matching:
        return []

    assert matching[0].timestamp is not None
    assert matching[-1].timestamp is not None
    t_start = matching[0].timestamp
    t_end = matching[-1].timestamp

    buckets: list[TimeBucket] = []
    current = t_start
    while current <= t_end:
        bucket_end = current + bucket_size
        bucket_entries = [
            e for e in matching
            if e.timestamp is not None and current <= e.timestamp < bucket_end
        ]
        buckets.append(TimeBucket(
            start=current,
            end=bucket_end,
            count=len(bucket_entries),
            entries=bucket_entries,
        ))
        current = bucket_end

    return buckets
