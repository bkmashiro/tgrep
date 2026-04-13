"""Tests for timestamp detection and parsing."""

import pytest
from datetime import datetime, timezone, timedelta

from src.timestamp import (
    detect_format,
    parse_timestamp,
    extract_timestamp,
)


class TestDetectFormat:
    def test_iso8601_basic(self):
        lines = [
            "2026-04-13T08:00:01 INFO Starting up",
            "2026-04-13T08:00:02 INFO Ready",
            "2026-04-13T08:00:03 ERROR Something broke",
        ]
        assert detect_format(lines) == "iso8601"

    def test_iso8601_fractional(self):
        lines = [
            "2026-04-13T08:00:01.123 INFO Starting up",
            "2026-04-13T08:00:02.456 INFO Ready",
        ]
        assert detect_format(lines) == "iso8601_frac"

    def test_iso8601_with_tz(self):
        lines = [
            "2026-04-13T08:00:01+05:30 INFO Starting up",
            "2026-04-13T08:00:02-04:00 INFO Ready",
        ]
        assert detect_format(lines) == "iso8601_tz"

    def test_iso8601_with_z(self):
        lines = [
            "2026-04-13T08:00:01Z INFO msg1",
            "2026-04-13T08:00:02Z INFO msg2",
        ]
        assert detect_format(lines) == "iso8601_z"

    def test_syslog_format(self):
        lines = [
            "Apr 13 08:00:01 server1 sshd[1234]: Accepted publickey",
            "Apr 13 08:00:02 server1 cron[5678]: Running job",
        ]
        assert detect_format(lines) == "syslog"

    def test_apache_format(self):
        lines = [
            '10.0.0.1 - - [13/Apr/2026:08:00:01 +0000] "GET / HTTP/1.1" 200 1234',
            '10.0.0.2 - - [13/Apr/2026:08:00:02 +0000] "POST /api HTTP/1.1" 201 56',
        ]
        assert detect_format(lines) == "apache"

    def test_epoch_format(self):
        lines = [
            "1744531200 INFO message one",
            "1744531201 INFO message two",
        ]
        assert detect_format(lines) == "epoch"

    def test_no_timestamps(self):
        lines = ["just some text", "no timestamps here"]
        assert detect_format(lines) is None

    def test_empty_input(self):
        assert detect_format([]) is None

    def test_mixed_with_majority_wins(self):
        lines = [
            "2026-04-13T08:00:01 INFO msg",
            "2026-04-13T08:00:02 INFO msg",
            "2026-04-13T08:00:03 INFO msg",
            "random line without timestamp",
        ]
        assert detect_format(lines) == "iso8601"


class TestParseTimestamp:
    def test_iso8601_basic(self):
        dt = parse_timestamp("2026-04-13T08:30:45", "iso8601")
        assert dt == datetime(2026, 4, 13, 8, 30, 45)

    def test_iso8601_with_space(self):
        dt = parse_timestamp("2026-04-13 08:30:45", "iso8601")
        assert dt == datetime(2026, 4, 13, 8, 30, 45)

    def test_iso8601_fractional(self):
        dt = parse_timestamp("2026-04-13T08:30:45.123456", "iso8601_frac")
        assert dt is not None
        assert dt.year == 2026
        assert dt.second == 45

    def test_iso8601_z(self):
        dt = parse_timestamp("2026-04-13T08:30:45Z", "iso8601_z")
        assert dt is not None
        assert dt.tzinfo == timezone.utc

    def test_iso8601_tz_positive(self):
        dt = parse_timestamp("2026-04-13T08:30:45+05:30", "iso8601_tz")
        assert dt is not None
        assert dt.tzinfo is not None

    def test_syslog(self):
        dt = parse_timestamp("Apr 13 08:30:45", "syslog")
        assert dt is not None
        assert dt.month == 4
        assert dt.day == 13
        assert dt.hour == 8

    def test_apache(self):
        dt = parse_timestamp("[13/Apr/2026:08:30:45 +0000]", "apache")
        assert dt is not None
        assert dt.day == 13
        assert dt.hour == 8

    def test_epoch(self):
        dt = parse_timestamp("1744531200", "epoch")
        assert dt is not None
        assert dt.year >= 2025

    def test_epoch_ms(self):
        dt = parse_timestamp("1744531200000", "epoch_ms")
        assert dt is not None

    def test_nonmatching_returns_none(self):
        dt = parse_timestamp("not a timestamp", "iso8601")
        assert dt is None


class TestExtractTimestamp:
    def test_extracts_and_strips(self):
        dt, rest = extract_timestamp(
            "2026-04-13T08:30:45 INFO Server started", "iso8601"
        )
        assert dt == datetime(2026, 4, 13, 8, 30, 45)
        assert "INFO Server started" in rest

    def test_no_match(self):
        dt, rest = extract_timestamp("no timestamp here", "iso8601")
        assert dt is None
        assert rest == "no timestamp here"

    def test_syslog_extract(self):
        dt, rest = extract_timestamp(
            "Apr 13 08:30:45 server1 sshd[1234]: Accepted key", "syslog"
        )
        assert dt is not None
        assert "sshd" in rest
