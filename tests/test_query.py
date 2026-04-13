"""Tests for the temporal query engine."""

import pytest
from datetime import datetime, timedelta

from src.parser import LogEntry, parse_lines
from src.query import (
    parse_duration,
    search_window,
    match_sequences,
    find_correlations,
    frequency_analysis,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_entry(line_number: int, ts: datetime, content: str) -> LogEntry:
    return LogEntry(
        line_number=line_number,
        timestamp=ts,
        raw=f"{ts.isoformat()} {content}",
        content=content,
    )


def make_log_entries() -> list[LogEntry]:
    """Create a realistic set of log entries for testing."""
    base = datetime(2026, 4, 13, 10, 0, 0)
    return [
        make_entry(1, base + timedelta(seconds=0), "INFO Starting application"),
        make_entry(2, base + timedelta(seconds=5), "INFO Database connected"),
        make_entry(3, base + timedelta(seconds=10), "WARN Slow query: 500ms"),
        make_entry(4, base + timedelta(seconds=15), "WARN Slow query: 800ms"),
        make_entry(5, base + timedelta(seconds=20), "WARN Slow query: 1200ms"),
        make_entry(6, base + timedelta(seconds=25), "ERROR Connection pool exhausted"),
        make_entry(7, base + timedelta(seconds=27), "ERROR Request timeout on /api/users"),
        make_entry(8, base + timedelta(seconds=30), "ERROR 503 Service Unavailable"),
        make_entry(9, base + timedelta(seconds=35), "INFO Connection pool recovered"),
        make_entry(10, base + timedelta(seconds=40), "INFO Normal operation resumed"),
        make_entry(11, base + timedelta(seconds=60), "WARN Memory usage at 80%"),
        make_entry(12, base + timedelta(seconds=70), "WARN Memory usage at 90%"),
        make_entry(13, base + timedelta(seconds=80), "ERROR OOM killed"),
        make_entry(14, base + timedelta(seconds=85), "INFO Process restarting"),
        make_entry(15, base + timedelta(seconds=90), "INFO Process ready"),
    ]


# ---------------------------------------------------------------------------
# Duration parsing
# ---------------------------------------------------------------------------

class TestParseDuration:
    def test_seconds(self):
        assert parse_duration("30s") == timedelta(seconds=30)

    def test_minutes(self):
        assert parse_duration("5m") == timedelta(minutes=5)

    def test_hours(self):
        assert parse_duration("2h") == timedelta(hours=2)

    def test_days(self):
        assert parse_duration("1d") == timedelta(days=1)

    def test_combined(self):
        assert parse_duration("1h30m") == timedelta(hours=1, minutes=30)

    def test_milliseconds(self):
        assert parse_duration("500ms") == timedelta(milliseconds=500)

    def test_complex(self):
        assert parse_duration("1d2h30m15s") == timedelta(days=1, hours=2, minutes=30, seconds=15)

    def test_pure_number_as_seconds(self):
        assert parse_duration("60") == timedelta(seconds=60)

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            parse_duration("abc")


# ---------------------------------------------------------------------------
# Window search
# ---------------------------------------------------------------------------

class TestSearchWindow:
    def test_window_before(self):
        entries = make_log_entries()
        results = search_window(
            entries,
            anchor_pattern="503",
            window_before=timedelta(seconds=20),
        )
        assert len(results) == 1
        anchor = results[0].anchor
        assert "503" in anchor.content
        # Should find slow queries and pool exhaustion
        assert len(results[0].matches) > 0
        contents = [m.content for m in results[0].matches]
        assert any("Slow query" in c for c in contents)

    def test_window_after(self):
        entries = make_log_entries()
        results = search_window(
            entries,
            anchor_pattern="Connection pool exhausted",
            window_after=timedelta(seconds=15),
        )
        assert len(results) == 1
        contents = [m.content for m in results[0].matches]
        assert any("503" in c for c in contents)
        assert any("recovered" in c for c in contents)

    def test_window_both_directions(self):
        entries = make_log_entries()
        results = search_window(
            entries,
            anchor_pattern="Connection pool exhausted",
            window_before=timedelta(seconds=20),
            window_after=timedelta(seconds=10),
        )
        assert len(results) == 1
        assert len(results[0].matches) >= 3

    def test_window_with_filter(self):
        entries = make_log_entries()
        results = search_window(
            entries,
            anchor_pattern="503",
            window_before=timedelta(seconds=30),
            content_filter="Slow query",
        )
        assert len(results) == 1
        for m in results[0].matches:
            assert "Slow query" in m.content

    def test_no_matches(self):
        entries = make_log_entries()
        results = search_window(
            entries,
            anchor_pattern="NONEXISTENT",
            window_before=timedelta(seconds=10),
        )
        assert len(results) == 0

    def test_multiple_anchors(self):
        entries = make_log_entries()
        results = search_window(
            entries,
            anchor_pattern="OOM|503",
            window_before=timedelta(seconds=15),
        )
        assert len(results) == 2


# ---------------------------------------------------------------------------
# Sequence matching
# ---------------------------------------------------------------------------

class TestMatchSequences:
    def test_basic_sequence(self):
        entries = make_log_entries()
        results = match_sequences(
            entries,
            patterns=["Slow query", "pool exhausted", "503"],
            within=timedelta(seconds=30),
        )
        # Each "Slow query" entry starts a valid sequence
        assert len(results) >= 1
        for r in results:
            assert len(r.events) == 3
            assert r.span.total_seconds() <= 30

    def test_two_event_sequence(self):
        entries = make_log_entries()
        results = match_sequences(
            entries,
            patterns=["pool exhausted", "recovered"],
            within=timedelta(seconds=15),
        )
        assert len(results) == 1

    def test_sequence_timeout(self):
        entries = make_log_entries()
        results = match_sequences(
            entries,
            patterns=["Starting", "OOM"],
            within=timedelta(seconds=5),  # too short
        )
        assert len(results) == 0

    def test_not_followed_by(self):
        entries = make_log_entries()
        # Slow query followed by 503, but NOT if pool exhaustion appears
        results = match_sequences(
            entries,
            patterns=["Slow query", "503"],
            within=timedelta(seconds=30),
            not_followed_by="pool exhausted",
        )
        # Should not match because "pool exhausted" appears between them
        assert len(results) == 0

    def test_not_followed_by_allows(self):
        entries = make_log_entries()
        # OOM → restarting, with no "rollback" between them
        results = match_sequences(
            entries,
            patterns=["OOM", "restarting"],
            within=timedelta(seconds=10),
            not_followed_by="rollback",
        )
        assert len(results) == 1

    def test_empty_patterns(self):
        entries = make_log_entries()
        results = match_sequences(entries, patterns=[], within=timedelta(seconds=10))
        assert len(results) == 0


# ---------------------------------------------------------------------------
# Correlation
# ---------------------------------------------------------------------------

class TestFindCorrelations:
    def test_finds_correlations(self):
        entries = make_log_entries()
        correlations = find_correlations(
            entries,
            target_pattern="503",
            window=timedelta(seconds=30),
            min_occurrences=1,
        )
        assert len(correlations) > 0
        tokens = [c.pattern for c in correlations]
        # "query" or "slow" should appear as correlated
        assert any("query" in t or "slow" in t or "pool" in t for t in tokens)

    def test_no_correlations_for_nonexistent(self):
        entries = make_log_entries()
        correlations = find_correlations(
            entries,
            target_pattern="ZZZZZ_NONEXISTENT",
            window=timedelta(seconds=10),
        )
        assert len(correlations) == 0

    def test_direction(self):
        entries = make_log_entries()
        correlations = find_correlations(
            entries,
            target_pattern="503",
            window=timedelta(seconds=30),
            min_occurrences=1,
        )
        # Events before 503 should have direction "before"
        for c in correlations:
            assert c.direction in ("before", "after", "both")

    def test_top_n_limit(self):
        entries = make_log_entries()
        correlations = find_correlations(
            entries,
            target_pattern="ERROR",
            window=timedelta(seconds=30),
            min_occurrences=1,
            top_n=3,
        )
        assert len(correlations) <= 3


# ---------------------------------------------------------------------------
# Frequency analysis
# ---------------------------------------------------------------------------

class TestFrequencyAnalysis:
    def test_basic_buckets(self):
        entries = make_log_entries()
        buckets = frequency_analysis(
            entries,
            pattern="ERROR",
            bucket_size=timedelta(seconds=30),
        )
        assert len(buckets) > 0
        total = sum(b.count for b in buckets)
        # We have 4 ERROR entries in the test data
        assert total == 4

    def test_narrow_buckets(self):
        entries = make_log_entries()
        buckets = frequency_analysis(
            entries,
            pattern="WARN",
            bucket_size=timedelta(seconds=10),
        )
        assert len(buckets) > 0

    def test_no_matches(self):
        entries = make_log_entries()
        buckets = frequency_analysis(
            entries,
            pattern="NONEXISTENT",
            bucket_size=timedelta(seconds=10),
        )
        assert len(buckets) == 0

    def test_bucket_boundaries(self):
        entries = make_log_entries()
        buckets = frequency_analysis(
            entries,
            pattern="INFO",
            bucket_size=timedelta(seconds=30),
        )
        # Verify buckets are contiguous
        for i in range(1, len(buckets)):
            assert buckets[i].start == buckets[i - 1].end
