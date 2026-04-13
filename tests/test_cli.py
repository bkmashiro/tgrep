"""Tests for the CLI interface."""

import os
import sys
import tempfile
import pytest

from src.cli import build_parser, main
from src.display import C


@pytest.fixture(autouse=True)
def disable_colors():
    C.disable()
    # Force no-color via non-tty detection — cli.main checks isatty
    yield


@pytest.fixture
def sample_log():
    """Create a temporary log file for CLI tests."""
    content = """2026-04-13T10:00:00 INFO Starting application
2026-04-13T10:00:05 INFO Database connected
2026-04-13T10:00:10 WARN Slow query: 500ms
2026-04-13T10:00:15 WARN Slow query: 800ms
2026-04-13T10:00:20 ERROR Connection pool exhausted
2026-04-13T10:00:25 ERROR 503 Service Unavailable
2026-04-13T10:00:30 INFO Recovered
2026-04-13T10:00:60 WARN Memory at 80%
2026-04-13T10:01:10 WARN Memory at 90%
2026-04-13T10:01:20 ERROR OOM killed
2026-04-13T10:01:25 INFO Restarting
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
        f.write(content)
        f.flush()
        yield f.name
    os.unlink(f.name)


class TestBuildParser:
    def test_parser_created(self):
        parser = build_parser()
        assert parser is not None

    def test_search_subcommand(self):
        parser = build_parser()
        args = parser.parse_args(["search", "ERROR", "test.log"])
        assert args.command == "search"
        assert args.pattern == "ERROR"
        assert args.file == "test.log"

    def test_window_subcommand(self):
        parser = build_parser()
        args = parser.parse_args(["window", "OOM", "test.log", "--before", "30s"])
        assert args.command == "window"
        assert args.anchor == "OOM"
        assert args.before == "30s"

    def test_sequence_subcommand(self):
        parser = build_parser()
        args = parser.parse_args([
            "sequence", "error", "retry", "failed",
            "--file", "test.log", "--within", "1m"
        ])
        assert args.command == "sequence"
        assert args.patterns == ["error", "retry", "failed"]
        assert args.within == "1m"
        assert args.file == "test.log"

    def test_correlate_subcommand(self):
        parser = build_parser()
        args = parser.parse_args(["correlate", "ERROR", "test.log", "--window", "5m"])
        assert args.command == "correlate"
        assert args.target == "ERROR"
        assert args.window == "5m"

    def test_timeline_subcommand(self):
        parser = build_parser()
        args = parser.parse_args(["timeline", "ERROR", "test.log", "--bucket", "5m"])
        assert args.command == "timeline"
        assert args.pattern == "ERROR"
        assert args.bucket == "5m"


class TestMainIntegration:
    def test_search(self, sample_log, capsys):
        main(["--no-color", "search", "ERROR", sample_log])
        output = capsys.readouterr().out
        assert "pool exhausted" in output
        assert "503" in output
        assert "OOM" in output

    def test_window(self, sample_log, capsys):
        main(["--no-color", "window", "503", sample_log, "--before", "15s"])
        output = capsys.readouterr().out
        assert "Anchor" in output

    def test_sequence(self, sample_log, capsys):
        main(["--no-color", "sequence", "Slow query", "pool exhausted",
              "--file", sample_log, "--within", "30s"])
        output = capsys.readouterr().out
        assert "Sequence" in output

    def test_correlate(self, sample_log, capsys):
        main(["--no-color", "correlate", "503", sample_log, "--window", "30s", "--min", "1"])
        output = capsys.readouterr().out
        assert "correlated" in output.lower() or "Pattern" in output

    def test_timeline(self, sample_log, capsys):
        main(["--no-color", "timeline", "ERROR", sample_log, "--bucket", "30s"])
        output = capsys.readouterr().out
        assert "Timeline" in output

    def test_no_command_shows_help(self, capsys):
        main(["--no-color"])
        output = capsys.readouterr().out
        assert "usage" in output.lower() or "tgrep" in output.lower()

    def test_format_flag(self, sample_log, capsys):
        main(["--no-color", "--format", "iso8601", "search", "ERROR", sample_log])
        output = capsys.readouterr().out
        assert "ERROR" in output
