"""Tests for display formatting."""

import io
from datetime import datetime, timedelta

from src.parser import LogEntry
from src.query import WindowResult, SequenceMatch, Correlation, TimeBucket
from src.display import (
    C,
    display_window_results,
    display_sequences,
    display_correlations,
    display_timeline,
    display_entries,
)


def setup_module(module):
    """Disable colors for testing."""
    C.disable()


def make_entry(n: int, ts: datetime, content: str) -> LogEntry:
    return LogEntry(n, ts, f"{ts.isoformat()} {content}", content)


class TestDisplayWindowResults:
    def test_empty(self):
        buf = io.StringIO()
        display_window_results([], out=buf)
        assert "No matches" in buf.getvalue()

    def test_with_results(self):
        base = datetime(2026, 4, 13, 10, 0, 0)
        anchor = make_entry(5, base, "ERROR crash")
        matches = [
            make_entry(3, base - timedelta(seconds=10), "WARN warning"),
            make_entry(4, base - timedelta(seconds=5), "WARN another"),
        ]
        results = [WindowResult(anchor=anchor, matches=matches)]
        buf = io.StringIO()
        display_window_results(results, out=buf)
        output = buf.getvalue()
        assert "Anchor #1" in output
        assert "crash" in output
        assert "2 events" in output


class TestDisplaySequences:
    def test_empty(self):
        buf = io.StringIO()
        display_sequences([], out=buf)
        assert "No matching sequences" in buf.getvalue()

    def test_with_sequences(self):
        base = datetime(2026, 4, 13, 10, 0, 0)
        events = [
            make_entry(1, base, "event A"),
            make_entry(2, base + timedelta(seconds=5), "event B"),
        ]
        seqs = [SequenceMatch(events=events, span=timedelta(seconds=5))]
        buf = io.StringIO()
        display_sequences(seqs, out=buf)
        output = buf.getvalue()
        assert "Sequence #1" in output
        assert "5.0s" in output


class TestDisplayCorrelations:
    def test_empty(self):
        buf = io.StringIO()
        display_correlations([], "ERROR", out=buf)
        assert "No significant correlations" in buf.getvalue()

    def test_with_correlations(self):
        corrs = [
            Correlation("slow_query", 5, -15.0, "before"),
            Correlation("timeout", 3, -8.0, "before"),
        ]
        buf = io.StringIO()
        display_correlations(corrs, "ERROR", out=buf)
        output = buf.getvalue()
        assert "slow_query" in output
        assert "timeout" in output
        assert "before" in output


class TestDisplayTimeline:
    def test_empty(self):
        buf = io.StringIO()
        display_timeline([], "ERROR", out=buf)
        assert "No data" in buf.getvalue()

    def test_with_buckets(self):
        base = datetime(2026, 4, 13, 10, 0, 0)
        buckets = [
            TimeBucket(base, base + timedelta(minutes=1), 3),
            TimeBucket(base + timedelta(minutes=1), base + timedelta(minutes=2), 7),
            TimeBucket(base + timedelta(minutes=2), base + timedelta(minutes=3), 1),
        ]
        buf = io.StringIO()
        display_timeline(buckets, "ERROR", width=40, out=buf)
        output = buf.getvalue()
        assert "Timeline" in output
        assert "Total events: 11" in output

    def test_zero_counts(self):
        base = datetime(2026, 4, 13, 10, 0, 0)
        buckets = [
            TimeBucket(base, base + timedelta(minutes=1), 0),
        ]
        buf = io.StringIO()
        display_timeline(buckets, "test", out=buf)
        assert "No matching events" in buf.getvalue()


class TestDisplayEntries:
    def test_empty(self):
        buf = io.StringIO()
        display_entries([], out=buf)
        assert "No matches" in buf.getvalue()

    def test_with_entries(self):
        base = datetime(2026, 4, 13, 10, 0, 0)
        entries = [
            make_entry(1, base, "ERROR something failed"),
            make_entry(2, base + timedelta(seconds=5), "ERROR another failure"),
        ]
        buf = io.StringIO()
        display_entries(entries, pattern="ERROR", out=buf)
        output = buf.getvalue()
        assert "something failed" in output
        assert "Total: 2" in output
