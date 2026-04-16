"""Command-line interface for tgrep."""

import argparse
import re
import sys
import time
from datetime import timedelta

from . import __version__
from .parser import parse_file, stream_entries
from .query import (
    parse_duration,
    search_window,
    match_sequences,
    find_correlations,
    frequency_analysis,
    find_gaps,
    detect_sessions,
    search_with_context,
)
from .display import (
    C,
    display_window_results,
    display_sequences,
    display_correlations,
    display_timeline,
    display_entries,
    display_gaps,
    display_sessions,
    display_context_matches,
    display_summary,
    entries_to_json,
    entries_to_csv,
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

  # Find time gaps longer than 5 minutes
  tgrep gap 5m app.log

  # Show event rate per minute as ASCII histogram
  tgrep rate 1m app.log

  # Auto-detect sessions (idle gap > 30m)
  tgrep session app.log --idle 30m

  # Context search: 5 events before and after each match
  tgrep context "ERROR" app.log -C 5

  # Filter events between two times
  tgrep between "14:00" "14:30" app.log

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
            "nginx", "python_log",
        ],
        help="Force a timestamp format instead of auto-detecting",
    )
    parser.add_argument("--json", action="store_true", help="Output results as JSON")
    parser.add_argument("--csv", action="store_true", help="Output results as CSV")
    parser.add_argument("--head", type=int, default=None, help="Limit output to first N matches")
    parser.add_argument("--tail", type=int, default=None, help="Limit output to last N matches")
    parser.add_argument("--summary", action="store_true", help="Show summary statistics at end")

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

    # --- gap ---
    gap_p = subparsers.add_parser(
        "gap",
        help="Find time gaps larger than a threshold (outages, quiet periods)",
    )
    gap_p.add_argument("threshold", help="Minimum gap duration (e.g., 5m, 1h)")
    gap_p.add_argument("file", nargs="?", default="-", help="Log file")

    # --- rate ---
    rate_p = subparsers.add_parser(
        "rate",
        help="Show event rate over time as ASCII histogram",
    )
    rate_p.add_argument("bucket", help="Bucket size (e.g., 1m, 5s)")
    rate_p.add_argument("file", nargs="?", default="-", help="Log file")
    rate_p.add_argument("--width", type=int, default=60, help="Histogram bar width (default: 60)")

    # --- session ---
    session_p = subparsers.add_parser(
        "session",
        help="Auto-detect sessions separated by idle gaps",
    )
    session_p.add_argument("file", nargs="?", default="-", help="Log file")
    session_p.add_argument("--idle", type=str, default="30m",
                           help="Idle threshold for new session (default: 30m)")
    session_p.add_argument("--max-lines", type=int, default=10,
                           help="Max events to show per session (0 = all, default: 10)")

    # --- context ---
    ctx_p = subparsers.add_parser(
        "context",
        help="Search with N events of context before/after each match",
    )
    ctx_p.add_argument("pattern", help="Regex pattern to search for")
    ctx_p.add_argument("file", nargs="?", default="-", help="Log file")
    ctx_p.add_argument("-C", "--context-count", type=int, default=3,
                       help="Number of context events before and after (default: 3)")

    # --- between ---
    between_p = subparsers.add_parser(
        "between",
        help="Filter events between two times (HH:MM or HH:MM:SS)",
    )
    between_p.add_argument("start_time", help="Start time (e.g., 14:00 or 14:00:00)")
    between_p.add_argument("end_time", help="End time (e.g., 14:30 or 14:30:00)")
    between_p.add_argument("file", nargs="?", default="-", help="Log file")
    between_p.add_argument("--pattern", type=str, default=None,
                           help="Optional pattern filter within the time range")

    return parser


def _apply_head_tail(entries, args):
    """Apply --head and --tail limits."""
    if args.head is not None:
        entries = entries[:args.head]
    if args.tail is not None:
        entries = entries[-args.tail:]
    return entries


def _parse_time_of_day(text):
    """Parse a time-of-day string like '14:00' or '14:30:00'."""
    from datetime import datetime
    parts = text.strip().split(":")
    if len(parts) == 2:
        return int(parts[0]), int(parts[1]), 0
    elif len(parts) == 3:
        return int(parts[0]), int(parts[1]), int(parts[2])
    raise ValueError(f"Cannot parse time: {text!r}")


def _handle_search(args, entries):
    pat = re.compile(args.pattern, re.IGNORECASE)
    matches = [e for e in entries if pat.search(e.content)]
    matches = _apply_head_tail(matches, args)

    if args.json:
        print(entries_to_json(matches))
    elif args.csv:
        print(entries_to_csv(matches), end="")
    else:
        display_entries(matches, pattern=args.pattern)
    return len(matches)


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

    if args.json:
        records = []
        for r in results:
            records.append({
                "anchor": {
                    "line_number": r.anchor.line_number,
                    "timestamp": r.anchor.timestamp.isoformat() if r.anchor.timestamp else None,
                    "content": r.anchor.content,
                },
                "matches": [
                    {
                        "line_number": m.line_number,
                        "timestamp": m.timestamp.isoformat() if m.timestamp else None,
                        "content": m.content,
                    }
                    for m in r.matches
                ],
            })
        import json
        print(json.dumps(records, indent=2))
    else:
        display_window_results(results)

    return sum(len(r.matches) for r in results)


def _handle_sequence(args, entries):
    within = parse_duration(args.within)
    results = match_sequences(
        entries,
        patterns=args.patterns,
        within=within,
        not_followed_by=args.not_followed_by,
    )
    display_sequences(results)
    return len(results)


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
    return len(correlations)


def _handle_timeline(args, entries):
    bucket_size = parse_duration(args.bucket)
    buckets = frequency_analysis(entries, args.pattern, bucket_size)
    display_timeline(buckets, args.pattern, width=args.width)
    return sum(b.count for b in buckets)


def _handle_gap(args, entries):
    min_gap = parse_duration(args.threshold)
    gaps = find_gaps(entries, min_gap)

    if args.json:
        import json
        records = [
            {
                "start_timestamp": g.start.timestamp.isoformat() if g.start.timestamp else None,
                "end_timestamp": g.end.timestamp.isoformat() if g.end.timestamp else None,
                "duration_seconds": g.duration.total_seconds(),
            }
            for g in gaps
        ]
        print(json.dumps(records, indent=2))
    else:
        display_gaps(gaps)

    return len(gaps)


def _handle_rate(args, entries):
    bucket_size = parse_duration(args.bucket)
    # Rate is just timeline with ".*" as pattern (all events)
    buckets = frequency_analysis(entries, ".*", bucket_size)
    display_timeline(buckets, "(all events)", width=args.width)
    return sum(b.count for b in buckets)


def _handle_session(args, entries):
    idle = parse_duration(args.idle)
    sessions = detect_sessions(entries, idle_threshold=idle)

    if args.json:
        import json
        records = [
            {
                "session": s.number,
                "start": s.start.isoformat(),
                "end": s.end.isoformat(),
                "duration_seconds": s.duration.total_seconds(),
                "event_count": s.count,
            }
            for s in sessions
        ]
        print(json.dumps(records, indent=2))
    else:
        display_sessions(sessions, max_entries_per_session=args.max_lines)

    return sum(s.count for s in sessions)


def _handle_context(args, entries):
    results = search_with_context(entries, args.pattern, context_count=args.context_count)

    if args.json:
        import json
        records = [
            {
                "match": {
                    "line_number": r.match.line_number,
                    "timestamp": r.match.timestamp.isoformat() if r.match.timestamp else None,
                    "content": r.match.content,
                },
                "before": [
                    {"line_number": e.line_number, "content": e.content}
                    for e in r.before
                ],
                "after": [
                    {"line_number": e.line_number, "content": e.content}
                    for e in r.after
                ],
            }
            for r in results
        ]
        print(json.dumps(records, indent=2))
    else:
        display_context_matches(results, pattern=args.pattern)

    return len(results)


def _handle_between(args, entries):
    from datetime import datetime as dt

    start_h, start_m, start_s = _parse_time_of_day(args.start_time)
    end_h, end_m, end_s = _parse_time_of_day(args.end_time)

    pat = re.compile(args.pattern, re.IGNORECASE) if args.pattern else None

    matches = []
    for e in entries:
        if not e.has_timestamp or e.timestamp is None:
            continue
        t = e.timestamp
        t_tuple = (t.hour, t.minute, t.second)
        if (start_h, start_m, start_s) <= t_tuple <= (end_h, end_m, end_s):
            if pat is None or pat.search(e.content):
                matches.append(e)

    matches = _apply_head_tail(matches, args)

    if args.json:
        print(entries_to_json(matches))
    elif args.csv:
        print(entries_to_csv(matches), end="")
    else:
        display_entries(matches)

    return len(matches)


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.no_color or not sys.stdout.isatty():
        C.disable()

    if not args.command:
        parser.print_help()
        return

    t_start = time.monotonic()

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
        "gap": _handle_gap,
        "rate": _handle_rate,
        "session": _handle_session,
        "context": _handle_context,
        "between": _handle_between,
    }

    handler = handlers.get(args.command)
    if handler:
        match_count = handler(args, entries)
        if args.summary:
            elapsed = time.monotonic() - t_start
            from .display import display_summary
            display_summary(match_count or 0, elapsed)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
