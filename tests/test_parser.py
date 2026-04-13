"""Tests for the log parser."""

import pytest
from datetime import datetime

from src.parser import parse_lines, LogEntry


class TestParseLines:
    def test_basic_parsing(self):
        lines = [
            "2026-04-13T10:00:00 INFO Starting",
            "2026-04-13T10:00:01 INFO Ready",
            "2026-04-13T10:00:02 ERROR Failed",
        ]
        entries = parse_lines(lines)
        assert len(entries) == 3
        assert entries[0].timestamp == datetime(2026, 4, 13, 10, 0, 0)
        assert entries[2].timestamp == datetime(2026, 4, 13, 10, 0, 2)
        assert "ERROR" in entries[2].content

    def test_line_numbers(self):
        lines = [
            "2026-04-13T10:00:00 first",
            "2026-04-13T10:00:01 second",
        ]
        entries = parse_lines(lines)
        assert entries[0].line_number == 1
        assert entries[1].line_number == 2

    def test_multiline_inherits_timestamp(self):
        lines = [
            "2026-04-13T10:00:00 ERROR NullPointerException",
            "  at com.example.Main.run(Main.java:42)",
            "  at com.example.Main.main(Main.java:10)",
            "2026-04-13T10:00:01 INFO Recovered",
        ]
        entries = parse_lines(lines)
        assert len(entries) == 4
        # Stack trace lines inherit the ERROR timestamp
        assert entries[1].timestamp == datetime(2026, 4, 13, 10, 0, 0)
        assert entries[2].timestamp == datetime(2026, 4, 13, 10, 0, 0)
        assert entries[3].timestamp == datetime(2026, 4, 13, 10, 0, 1)

    def test_empty_lines_skipped(self):
        lines = [
            "2026-04-13T10:00:00 first",
            "",
            "   ",
            "2026-04-13T10:00:01 second",
        ]
        entries = parse_lines(lines)
        assert len(entries) == 2

    def test_forced_format(self):
        lines = [
            "Apr 13 10:00:00 server1 test",
            "Apr 13 10:00:01 server1 test2",
        ]
        entries = parse_lines(lines, format_name="syslog")
        assert len(entries) == 2
        assert entries[0].timestamp is not None
        assert entries[0].timestamp.month == 4

    def test_no_timestamps_at_all(self):
        lines = ["just text", "more text"]
        entries = parse_lines(lines)
        assert len(entries) == 2
        assert entries[0].timestamp is None

    def test_raw_preserved(self):
        lines = ["2026-04-13T10:00:00 INFO message with  spaces"]
        entries = parse_lines(lines)
        assert entries[0].raw == lines[0]

    def test_has_timestamp_property(self):
        lines = [
            "2026-04-13T10:00:00 with ts",
            "without ts",
        ]
        # Force iso8601 so the second line doesn't get inherited ts from auto-detect
        entries = parse_lines(lines, format_name="iso8601")
        assert entries[0].has_timestamp
        # Second line inherits from first
        assert entries[1].has_timestamp
