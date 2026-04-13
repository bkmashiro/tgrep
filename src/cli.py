"""Command-line interface for tgrep."""

import argparse
import re
import sys
from datetime import timedelta

from . import __version__
from .parser import parse_file
from .query import (
    parse_duration,
    search_window,
    match_sequences,
    find_correlations,
    frequency_analysis,
)
from .display import (
    C,
    display_window_results,
    display_sequences,
    display_correlations,
    display_timeline,
    display_entries,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tgrep",
        description="Time-aware grep for log analysis. "
        "Search logs with temporal context — find what happened "
        "before crashes, match event sequences, discover correlations.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  # Basic pattern search with timestamps
  tgrep "ERROR" app.log

  # What happened in the 30s before each OOM?
  tgrep window "OOM" app.log --before 30s

  # Events in 5m window around each timeout, filtered to DB queries
  tgrep window "timeout" app.log --before 5m --after 1m --filter "SELECT|INSERT"

  # Find sequence: connection drop → retry → failure
  tgrep sequence "connection lost" "retry" "fatal" app.log --within 60s

  # Sequence with negative assertion
  tgrep sequence "deploy started" "health check passed" app.log \\
      --within 5m --not-followed-by "rollback"

  # What patterns tend to precede errors?
  tgrep correlate "ERROR" app.log --window 2m

  # Event frequency timeline
  tgrep timeline "request" app.log --bucket 1m

  # Read from stdin
  journalctl -u myapp | tgrep window "segfault" --before 10s
""",
    )
    parser.add_argument("--version", action="version", version=f"tgrep {__version__}")
    parser.add_argument("--no-color", action="store_true", help="Disable colored output")
    parser.add_argument(
        "--format",
        choices=[
            "iso8601", "iso8601_frac", "iso8601_tz", "iso8601_frac_tz",
            "iso8601_z", "iso8601_frac_z",
            "syslog", "apache", "epoch", "epoch_ms", "us_date", "eu_date",
        ],
        help="Force a timestamp format instead of auto-detecting",
    )

    subparsers = parser.add_subparsers(dest="command")

    # --- search (default-like: simple grep with timestamp awareness) ---
    search_p = subparsers.add_parser(
        "search", help="Search for a pattern (like grep, but timestamp-aware)"
    )
    search_p.add_argument("pattern", help="Regex pattern to search for")
    search_p.add_argument("file", nargs="?", default="-", help="Log file (default: stdin)")
    search_p.add_argument(
        "--after", type=str, default=None,
        help="Only show entries after this time (HH:MM:SS or duration like 5m ago)",
    )
    search_p.add_argument(
        "--before", type=str, default=None,
        help="Only show entries before this time",
    )

    # --- window ---
    window_p = subparsers.add_parser(
        "window",
        help="Find events in a time window around anchor events",
    )
    window_p.add_argument("anchor", help="Regex for anchor events")
    window_p.add_argument("file", nargs="?", default="-", help="Log file")
    window_p.add_argument("--before", type=str, default="30s", help="Window before anchor (default: 30s)")
    window_p.add_argument("--after", type=str, default="0s", help="Window after anchor (default: 0s)")
    window_p.add_argument("--filter", type=str, default=None, help="Filter window results by pattern")

    # --- sequence ---
    seq_p = subparsers.add_parser(
        "sequence",
        help="Match ordered event sequences within a time window",
    )
    seq_p.add_argument("patterns", nargs="+", help="Patterns to match in order (use --file for input)")
    seq_p.add_argument("--file", "-f", default="-", help="Log file (default: stdin)")
    seq_p.add_argument("--within", type=str, default="60s", help="Max duration for sequence (default: 60s)")
    seq_p.add_argument(
        "--not-followed-by", type=str, default=None,
        help="Negative pattern — sequence broken if this appears",
    )

    # --- correlate ---
    corr_p = subparsers.add_parser(
        "correlate",
        help="Find patterns that co-occur temporally with a target event",
    )
    corr_p.add_argument("target", help="Target event pattern")
    corr_p.add_argument("file", nargs="?", default="-", help="Log file")
    corr_p.add_argument("--window", type=str, default="2m", help="Correlation window (default: 2m)")
    corr_p.add_argument("--min", type=int, default=2, help="Minimum co-occurrences (default: 2)")
    corr_p.add_argument("--top", type=int, default=10, help="Show top N correlations (default: 10)")

    # --- timeline ---
    tl_p = subparsers.add_parser(
        "timeline",
        help="Show event frequency over time as a histogram",
    )
    tl_p.add_argument("pattern", help="Pattern to count")
    tl_p.add_argument("file", nargs="?", default="-", help="Log file")
    tl_p.add_argument("--bucket", type=str, default="1m", help="Bucket size (default: 1m)")
    tl_p.add_argument("--width", type=int, default=60, help="Histogram bar width (default: 60)")

    return parser


def _handle_search(args, entries):
    pat = re.compile(args.pattern, re.IGNORECASE)
    matches = [e for e in entries if pat.search(e.content)]
    display_entries(matches, pattern=args.pattern)


def _handle_window(args, entries):
    before = parse_duration(args.before)
    after = parse_duration(args.after)
    results = search_window(
        entries,
        anchor_pattern=args.anchor,
        window_before=before,
        window_after=after,
        content_filter=args.filter,
    )
    display_window_results(results)


def _handle_sequence(args, entries):
    # The last positional arg that looks like a file path is the file;
    # everything else is a pattern. argparse handles this via nargs="+".
    within = parse_duration(args.within)
    results = match_sequences(
        entries,
        patterns=args.patterns,
        within=within,
        not_followed_by=args.not_followed_by,
    )
    display_sequences(results)


def _handle_correlate(args, entries):
    window = parse_duration(args.window)
    correlations = find_correlations(
        entries,
        target_pattern=args.target,
        window=window,
        min_occurrences=args.min,
        top_n=args.top,
    )
    display_correlations(correlations, args.target)


def _handle_timeline(args, entries):
    bucket_size = parse_duration(args.bucket)
    buckets = frequency_analysis(entries, args.pattern, bucket_size)
    display_timeline(buckets, args.pattern, width=args.width)


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.no_color or not sys.stdout.isatty():
        C.disable()

    if not args.command:
        parser.print_help()
        return

    file_path = getattr(args, "file", "-")
    fmt = getattr(args, "format", None)
    entries = parse_file(file_path, format_name=fmt)

    if not entries:
        print(f"{C.DIM}No log entries found.{C.RESET}", file=sys.stderr)
        return

    handlers = {
        "search": _handle_search,
        "window": _handle_window,
        "sequence": _handle_sequence,
        "correlate": _handle_correlate,
        "timeline": _handle_timeline,
    }

    handler = handlers.get(args.command)
    if handler:
        handler(args, entries)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
