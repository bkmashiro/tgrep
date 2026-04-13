"""Integration tests — end-to-end with generated log files."""

import os
import tempfile
import pytest
from datetime import datetime, timedelta

from src.parser import parse_lines
from src.query import search_window, match_sequences, find_correlations, frequency_analysis
from src.display import C


def setup_module(module):
    C.disable()


def _generate_cascading_failure_log() -> list[str]:
    """Generate a realistic cascading failure pattern."""
    lines = []
    base = datetime(2026, 4, 13, 14, 0, 0)

    # Normal traffic
    for i in range(20):
        t = base + timedelta(seconds=i * 2)
        lines.append(f"{t.isoformat()} INFO  [http] GET /api/data 200 (45ms)")

    # Slow queries start
    t = base + timedelta(seconds=42)
    for i in range(5):
        t += timedelta(seconds=3)
        latency = 500 + i * 300
        lines.append(f"{t.isoformat()} WARN  [db] Slow query on users table: {latency}ms")

    # Pool exhaustion
    t += timedelta(seconds=5)
    lines.append(f"{t.isoformat()} ERROR [db-pool] Connection pool exhausted, active=50/50")

    # Service errors
    for i in range(5):
        t += timedelta(seconds=1)
        lines.append(f"{t.isoformat()} ERROR [http] GET /api/data 503 Service Unavailable")

    # Recovery
    t += timedelta(seconds=10)
    lines.append(f"{t.isoformat()} INFO  [db-pool] Connection pool recovered, active=5/50")

    # Normal traffic resumes
    for i in range(10):
        t += timedelta(seconds=2)
        lines.append(f"{t.isoformat()} INFO  [http] GET /api/data 200 (35ms)")

    return lines


class TestCascadingFailureScenario:
    @pytest.fixture
    def entries(self):
        lines = _generate_cascading_failure_log()
        return parse_lines(lines)

    def test_parse_all_entries(self, entries):
        assert len(entries) > 30
        assert all(e.has_timestamp for e in entries)

    def test_window_before_503(self, entries):
        results = search_window(
            entries,
            anchor_pattern="503",
            window_before=timedelta(seconds=20),
        )
        assert len(results) > 0
        # Should find slow queries before 503s
        all_matches = []
        for r in results:
            all_matches.extend(r.matches)
        contents = " ".join(m.content for m in all_matches)
        assert "Slow query" in contents or "pool exhausted" in contents

    def test_sequence_slow_to_503(self, entries):
        results = match_sequences(
            entries,
            patterns=["Slow query", "pool exhausted", "503"],
            within=timedelta(seconds=30),
        )
        assert len(results) >= 1
        # Verify ordering
        for seq in results:
            timestamps = [e.timestamp for e in seq.events]
            for i in range(1, len(timestamps)):
                assert timestamps[i] >= timestamps[i - 1]

    def test_correlations_with_503(self, entries):
        correlations = find_correlations(
            entries,
            target_pattern="503",
            window=timedelta(seconds=30),
            min_occurrences=1,
        )
        assert len(correlations) > 0
        # "slow" or "query" or "pool" should be correlated
        tokens = {c.pattern.lower() for c in correlations}
        assert any(t in tokens for t in ["slow", "query", "pool", "exhausted", "connection"])

    def test_timeline_errors(self, entries):
        buckets = frequency_analysis(
            entries,
            pattern="ERROR",
            bucket_size=timedelta(seconds=15),
        )
        assert len(buckets) > 0
        # Errors should be concentrated, not spread evenly
        counts = [b.count for b in buckets]
        assert max(counts) > 1  # At least one bucket with multiple errors


class TestMultiFormatParsing:
    """Test that various log formats parse correctly end-to-end."""

    def test_iso8601(self):
        lines = [
            "2026-04-13T10:00:00 INFO msg1",
            "2026-04-13T10:00:01 ERROR msg2",
        ]
        entries = parse_lines(lines)
        assert len(entries) == 2
        assert entries[1].timestamp.second == 1  # type: ignore

    def test_iso8601_frac(self):
        lines = [
            "2026-04-13T10:00:00.123 INFO msg1",
            "2026-04-13T10:00:01.456 ERROR msg2",
        ]
        entries = parse_lines(lines)
        assert len(entries) == 2

    def test_syslog(self):
        lines = [
            "Apr 13 10:00:00 server1 sshd[1234]: msg1",
            "Apr 13 10:00:01 server1 cron[5678]: msg2",
        ]
        entries = parse_lines(lines)
        assert len(entries) == 2
        assert entries[0].timestamp is not None

    def test_apache(self):
        lines = [
            '10.0.0.1 - - [13/Apr/2026:10:00:00 +0000] "GET / HTTP/1.1" 200 1234',
            '10.0.0.2 - - [13/Apr/2026:10:00:01 +0000] "POST /api HTTP/1.1" 201 56',
        ]
        entries = parse_lines(lines)
        assert len(entries) == 2
        assert entries[0].timestamp is not None
