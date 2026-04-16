"""Tests for new tgrep features: gap, session, context, between, JSON/CSV, extended timestamps."""

import io
import json
import os
import tempfile
from datetime import datetime, timedelta

import pytest

from src.parser import LogEntry, parse_lines, stream_entries
from src.query import (
    find_gaps,
    detect_sessions,
    search_with_context,
    parse_duration,
)
from src.display import (
    C,
    display_gaps,
    display_sessions,
    display_context_matches,
    entries_to_json,
    entries_to_csv,
    display_summary,
)
from src.timestamp import detect_format, parse_timestamp, extract_timestamp
from src.cli import main


def setup_module(module):
    C.disable()


def make_entry(n: int, ts: datetime, content: str) -> LogEntry:
    return LogEntry(n, ts, f"{ts.isoformat()} {content}", content)


def make_gapped_entries() -> list[LogEntry]:
    """Entries with deliberate gaps."""
    base = datetime(2026, 4, 13, 10, 0, 0)
    entries = []
    # Cluster 1: 10 events over 50 seconds
    for i in range(10):
        entries.append(make_entry(i + 1, base + timedelta(seconds=i * 5), f"INFO event {i}"))
    # GAP: 10 minutes
    # Cluster 2: 10 events starting 10 minutes later
    gap_start = base + timedelta(seconds=45)
    gap_end = base + timedelta(minutes=10)
    for i in range(10):
        entries.append(make_entry(11 + i, gap_end + timedelta(seconds=i * 5), f"INFO event {10 + i}"))
    # GAP: 30 minutes
    gap2_end = gap_end + timedelta(seconds=45) + timedelta(minutes=30)
    for i in range(5):
        entries.append(make_entry(21 + i, gap2_end + timedelta(seconds=i * 5), f"INFO event {20 + i}"))
    return entries


# ---------------------------------------------------------------------------
# Gap detection
# ---------------------------------------------------------------------------

class TestFindGaps:
    def test_finds_large_gaps(self):
        entries = make_gapped_entries()
        gaps = find_gaps(entries, min_gap=timedelta(minutes=5))
        assert len(gaps) == 2
        # Sorted by duration descending
        assert gaps[0].duration > gaps[1].duration

    def test_finds_no_gaps_below_threshold(self):
        entries = make_gapped_entries()
        gaps = find_gaps(entries, min_gap=timedelta(hours=1))
        assert len(gaps) == 0

    def test_finds_all_gaps_above_10s(self):
        base = datetime(2026, 4, 13, 10, 0, 0)
        entries = [
            make_entry(1, base, "a"),
            make_entry(2, base + timedelta(seconds=1), "b"),
            make_entry(3, base + timedelta(seconds=20), "c"),  # 19s gap
            make_entry(4, base + timedelta(seconds=21), "d"),
        ]
        gaps = find_gaps(entries, min_gap=timedelta(seconds=10))
        assert len(gaps) == 1
        assert gaps[0].duration.total_seconds() == 19

    def test_empty_entries(self):
        gaps = find_gaps([], min_gap=timedelta(seconds=1))
        assert gaps == []


class TestDisplayGaps:
    def test_display_gaps(self):
        entries = make_gapped_entries()
        gaps = find_gaps(entries, min_gap=timedelta(minutes=5))
        buf = io.StringIO()
        display_gaps(gaps, out=buf)
        output = buf.getvalue()
        assert "Time Gaps" in output
        assert "gap" in output

    def test_display_no_gaps(self):
        buf = io.StringIO()
        display_gaps([], out=buf)
        assert "No time gaps" in buf.getvalue()


# ---------------------------------------------------------------------------
# Session detection
# ---------------------------------------------------------------------------

class TestDetectSessions:
    def test_detects_sessions(self):
        entries = make_gapped_entries()
        sessions = detect_sessions(entries, idle_threshold=timedelta(minutes=5))
        assert len(sessions) == 3
        assert sessions[0].number == 1
        assert sessions[1].number == 2
        assert sessions[2].number == 3

    def test_single_session_no_gaps(self):
        base = datetime(2026, 4, 13, 10, 0, 0)
        entries = [make_entry(i, base + timedelta(seconds=i), f"event {i}") for i in range(20)]
        sessions = detect_sessions(entries, idle_threshold=timedelta(minutes=5))
        assert len(sessions) == 1
        assert sessions[0].count == 20

    def test_custom_idle_threshold(self):
        entries = make_gapped_entries()
        # Only break at 15+ minute gaps
        sessions = detect_sessions(entries, idle_threshold=timedelta(minutes=15))
        assert len(sessions) == 2  # Only the 30m gap triggers a break

    def test_empty_entries(self):
        sessions = detect_sessions([])
        assert sessions == []

    def test_session_properties(self):
        entries = make_gapped_entries()
        sessions = detect_sessions(entries, idle_threshold=timedelta(minutes=5))
        for s in sessions:
            assert s.count > 0
            assert s.start <= s.end
            assert s.duration >= timedelta(0)


class TestDisplaySessions:
    def test_display_sessions(self):
        entries = make_gapped_entries()
        sessions = detect_sessions(entries, idle_threshold=timedelta(minutes=5))
        buf = io.StringIO()
        display_sessions(sessions, out=buf)
        output = buf.getvalue()
        assert "Session 1" in output
        assert "Session 2" in output
        assert "Total:" in output

    def test_display_sessions_with_limit(self):
        entries = make_gapped_entries()
        sessions = detect_sessions(entries, idle_threshold=timedelta(minutes=5))
        buf = io.StringIO()
        display_sessions(sessions, max_entries_per_session=3, out=buf)
        output = buf.getvalue()
        assert "more" in output

    def test_display_no_sessions(self):
        buf = io.StringIO()
        display_sessions([], out=buf)
        assert "No sessions" in buf.getvalue()


# ---------------------------------------------------------------------------
# Context search
# ---------------------------------------------------------------------------

class TestSearchWithContext:
    def test_finds_matches_with_context(self):
        base = datetime(2026, 4, 13, 10, 0, 0)
        entries = [
            make_entry(1, base + timedelta(seconds=0), "INFO start"),
            make_entry(2, base + timedelta(seconds=1), "INFO running"),
            make_entry(3, base + timedelta(seconds=2), "ERROR crash"),
            make_entry(4, base + timedelta(seconds=3), "INFO recovered"),
            make_entry(5, base + timedelta(seconds=4), "INFO done"),
        ]
        results = search_with_context(entries, "ERROR", context_count=2)
        assert len(results) == 1
        assert "crash" in results[0].match.content
        assert len(results[0].before) == 2
        assert len(results[0].after) == 2

    def test_context_at_start_of_file(self):
        base = datetime(2026, 4, 13, 10, 0, 0)
        entries = [
            make_entry(1, base, "ERROR first line crash"),
            make_entry(2, base + timedelta(seconds=1), "INFO after"),
        ]
        results = search_with_context(entries, "ERROR", context_count=5)
        assert len(results) == 1
        assert len(results[0].before) == 0  # no context before first line
        assert len(results[0].after) == 1

    def test_context_at_end_of_file(self):
        base = datetime(2026, 4, 13, 10, 0, 0)
        entries = [
            make_entry(1, base, "INFO before"),
            make_entry(2, base + timedelta(seconds=1), "ERROR last crash"),
        ]
        results = search_with_context(entries, "ERROR", context_count=5)
        assert len(results) == 1
        assert len(results[0].after) == 0

    def test_no_matches(self):
        base = datetime(2026, 4, 13, 10, 0, 0)
        entries = [make_entry(1, base, "INFO ok")]
        results = search_with_context(entries, "NONEXISTENT")
        assert results == []


class TestDisplayContextMatches:
    def test_display(self):
        base = datetime(2026, 4, 13, 10, 0, 0)
        entries = [
            make_entry(1, base, "INFO before1"),
            make_entry(2, base + timedelta(seconds=1), "INFO before2"),
            make_entry(3, base + timedelta(seconds=2), "ERROR crash"),
            make_entry(4, base + timedelta(seconds=3), "INFO after1"),
        ]
        results = search_with_context(entries, "ERROR", context_count=2)
        buf = io.StringIO()
        display_context_matches(results, pattern="ERROR", out=buf)
        output = buf.getvalue()
        assert "Match #1" in output
        assert "crash" in output


# ---------------------------------------------------------------------------
# JSON/CSV output
# ---------------------------------------------------------------------------

class TestJSONOutput:
    def test_entries_to_json(self):
        base = datetime(2026, 4, 13, 10, 0, 0)
        entries = [
            make_entry(1, base, "ERROR test"),
            make_entry(2, base + timedelta(seconds=1), "INFO ok"),
        ]
        result = entries_to_json(entries)
        data = json.loads(result)
        assert len(data) == 2
        assert data[0]["line_number"] == 1
        assert data[0]["content"] == "ERROR test"
        assert "2026-04-13" in data[0]["timestamp"]

    def test_entries_to_json_no_timestamp(self):
        entry = LogEntry(1, None, "no ts", "no ts")
        result = entries_to_json([entry])
        data = json.loads(result)
        assert data[0]["timestamp"] is None


class TestCSVOutput:
    def test_entries_to_csv(self):
        base = datetime(2026, 4, 13, 10, 0, 0)
        entries = [
            make_entry(1, base, "ERROR test"),
            make_entry(2, base + timedelta(seconds=1), "INFO ok"),
        ]
        result = entries_to_csv(entries)
        lines = result.strip().splitlines()
        assert lines[0].strip() == "line_number,timestamp,content"
        assert "ERROR test" in lines[1]


# ---------------------------------------------------------------------------
# Extended timestamp formats
# ---------------------------------------------------------------------------

class TestNginxFormat:
    def test_detect_nginx(self):
        lines = [
            "2024/01/15 14:23:01 INFO message one",
            "2024/01/15 14:23:02 INFO message two",
        ]
        assert detect_format(lines) == "nginx"

    def test_parse_nginx(self):
        dt = parse_timestamp("2024/01/15 14:23:01", "nginx")
        assert dt is not None
        assert dt.year == 2024
        assert dt.month == 1
        assert dt.hour == 14

    def test_extract_nginx(self):
        dt, rest = extract_timestamp("2024/01/15 14:23:01 INFO test", "nginx")
        assert dt is not None
        assert "INFO test" in rest


class TestPythonLogFormat:
    def test_detect_python_log(self):
        lines = [
            "2024-01-15 14:23:01,123 INFO message one",
            "2024-01-15 14:23:02,456 INFO message two",
        ]
        assert detect_format(lines) == "python_log"

    def test_parse_python_log(self):
        dt = parse_timestamp("2024-01-15 14:23:01,123", "python_log")
        assert dt is not None
        assert dt.year == 2024
        assert dt.second == 1

    def test_extract_python_log(self):
        dt, rest = extract_timestamp("2024-01-15 14:23:01,123 INFO test", "python_log")
        assert dt is not None
        assert "INFO test" in rest


class TestEpochMsDetection:
    def test_detect_epoch_ms(self):
        lines = [
            "1705329781000 INFO message one",
            "1705329782000 INFO message two",
        ]
        assert detect_format(lines) == "epoch_ms"

    def test_parse_epoch_ms_value(self):
        dt = parse_timestamp("1705329781000", "epoch_ms")
        assert dt is not None
        assert dt.year == 2024


class TestTimestampMalformedInput:
    def test_garbage_doesnt_crash(self):
        dt = parse_timestamp("not a timestamp at all!", "iso8601")
        assert dt is None

    def test_partial_timestamp_doesnt_crash(self):
        dt = parse_timestamp("2024-01-", "iso8601")
        assert dt is None

    def test_epoch_overflow_doesnt_crash(self):
        dt = parse_timestamp("99999999999999", "epoch")
        # Should return None or a valid datetime, never raise
        # (the regex won't match 14 digits for epoch anyway)


# ---------------------------------------------------------------------------
# CLI integration tests for new commands
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_log_with_gaps():
    """Log file with deliberate time gaps for testing."""
    content = """2026-04-13T10:00:00 INFO Starting application
2026-04-13T10:00:05 INFO Database connected
2026-04-13T10:00:10 WARN Slow query: 500ms
2026-04-13T10:00:15 ERROR Connection pool exhausted
2026-04-13T10:00:20 ERROR 503 Service Unavailable
2026-04-13T10:00:25 INFO Recovered
2026-04-13T10:30:00 INFO Session 2 start
2026-04-13T10:30:05 INFO Processing batch
2026-04-13T10:30:10 WARN Memory at 80%
2026-04-13T10:30:15 ERROR OOM killed
2026-04-13T11:15:00 INFO Session 3 start
2026-04-13T11:15:05 INFO All clear
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
        f.write(content)
        f.flush()
        yield f.name
    os.unlink(f.name)


class TestCLINewCommands:
    def test_gap(self, sample_log_with_gaps, capsys):
        main(["--no-color", "gap", "5m", sample_log_with_gaps])
        output = capsys.readouterr().out
        assert "Time Gaps" in output
        assert "gap" in output

    def test_session(self, sample_log_with_gaps, capsys):
        main(["--no-color", "session", sample_log_with_gaps, "--idle", "10m"])
        output = capsys.readouterr().out
        assert "Session 1" in output
        assert "Session 2" in output

    def test_context(self, sample_log_with_gaps, capsys):
        main(["--no-color", "context", "ERROR", sample_log_with_gaps, "-C", "2"])
        output = capsys.readouterr().out
        assert "Match" in output

    def test_between(self, sample_log_with_gaps, capsys):
        main(["--no-color", "between", "10:30", "10:31", sample_log_with_gaps])
        output = capsys.readouterr().out
        assert "Session 2" in output or "batch" in output

    def test_rate(self, sample_log_with_gaps, capsys):
        main(["--no-color", "rate", "1m", sample_log_with_gaps])
        output = capsys.readouterr().out
        assert "Timeline" in output

    def test_json_output(self, sample_log_with_gaps, capsys):
        main(["--no-color", "--json", "search", "ERROR", sample_log_with_gaps])
        output = capsys.readouterr().out
        data = json.loads(output)
        assert isinstance(data, list)
        assert len(data) > 0

    def test_csv_output(self, sample_log_with_gaps, capsys):
        main(["--no-color", "--csv", "search", "ERROR", sample_log_with_gaps])
        output = capsys.readouterr().out
        assert "line_number,timestamp,content" in output

    def test_head_limit(self, sample_log_with_gaps, capsys):
        main(["--no-color", "--head", "2", "search", "INFO", sample_log_with_gaps])
        output = capsys.readouterr().out
        # Should show at most 2 matches
        assert "Total: 2" in output

    def test_summary_flag(self, sample_log_with_gaps, capsys):
        main(["--no-color", "--summary", "search", "ERROR", sample_log_with_gaps])
        captured = capsys.readouterr()
        assert "Found" in captured.err


class TestDisplaySummary:
    def test_display_summary(self):
        buf = io.StringIO()
        display_summary(42, 0.123, out=buf)
        output = buf.getvalue()
        assert "42" in output
        assert "0.12" in output


# ---------------------------------------------------------------------------
# Streaming parser test
# ---------------------------------------------------------------------------

class TestStreamEntries:
    def test_stream_entries_from_file(self):
        content = "2026-04-13T10:00:00 INFO msg1\n2026-04-13T10:00:01 INFO msg2\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            f.write(content)
            path = f.name
        try:
            entries = list(stream_entries(path))
            assert len(entries) == 2
            assert entries[0].timestamp is not None
            assert entries[1].timestamp is not None
        finally:
            os.unlink(path)

    def test_stream_large_file(self):
        """Ensure streaming works for files with more than 100 lines."""
        lines = []
        for i in range(200):
            ts = f"2026-04-13T10:{i // 60:02d}:{i % 60:02d}"
            lines.append(f"{ts} INFO event {i}")
        content = "\n".join(lines) + "\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            f.write(content)
            path = f.name
        try:
            entries = list(stream_entries(path))
            assert len(entries) == 200
        finally:
            os.unlink(path)
