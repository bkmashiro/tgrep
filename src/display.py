"""Terminal display and formatting for tgrep results.

Produces colored output with timeline visualizations.
"""

import json
import csv
import io
import sys
import time
from datetime import timedelta
from typing import Optional, TextIO

from .parser import LogEntry
from .query import (
    WindowResult, SequenceMatch, Correlation, TimeBucket,
    GapResult, Session, ContextMatch,
)


# ANSI color codes
class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"
    BG_RED = "\033[41m"
    BG_GREEN = "\033[42m"
    BG_YELLOW = "\033[43m"

    @classmethod
    def disable(cls):
        for attr in dir(cls):
            if attr.isupper() and not attr.startswith("_"):
                setattr(cls, attr, "")


def _ts_str(entry: LogEntry) -> str:
    if entry.timestamp:
        return entry.timestamp.strftime("%H:%M:%S.%f")[:-3]
    return "??:??:??"


def _line_ref(entry: LogEntry) -> str:
    return f"{C.DIM}L{entry.line_number}{C.RESET}"


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def print_entry(entry: LogEntry, highlight: str = "", out: Optional[TextIO] = None):
    """Print a single log entry with optional highlighting."""
    out = out or sys.stdout
    ts = f"{C.CYAN}{_ts_str(entry)}{C.RESET}"
    ref = _line_ref(entry)
    content = entry.content
    if highlight:
        import re
        content = re.sub(
            f"({re.escape(highlight)})",
            f"{C.BG_YELLOW}{C.BOLD}\\1{C.RESET}",
            content,
            flags=re.IGNORECASE,
        )
    out.write(f"  {ts} {ref} {content}\n")


def display_window_results(
    results: list[WindowResult],
    out: Optional[TextIO] = None,
):
    """Display time-window search results."""
    out = out or sys.stdout
    if not results:
        out.write(f"{C.DIM}No matches found.{C.RESET}\n")
        return

    for i, result in enumerate(results):
        out.write(
            f"\n{C.BOLD}{C.MAGENTA}━━━ Anchor #{i+1}: "
            f"{_ts_str(result.anchor)} ━━━{C.RESET}\n"
        )
        out.write(f"  {C.RED}{C.BOLD}{result.anchor.content}{C.RESET}\n")
        out.write(
            f"  {C.DIM}({len(result.matches)} events in window){C.RESET}\n\n"
        )
        for m in result.matches:
            offset = ""
            if m.timestamp and result.anchor.timestamp:
                delta = (m.timestamp - result.anchor.timestamp).total_seconds()
                sign = "+" if delta >= 0 else ""
                offset = f" {C.YELLOW}[{sign}{delta:.1f}s]{C.RESET}"
            ts = f"{C.CYAN}{_ts_str(m)}{C.RESET}"
            ref = _line_ref(m)
            out.write(f"  {ts} {ref}{offset} {m.content}\n")

    out.write(f"\n{C.BOLD}Total: {len(results)} anchor event(s){C.RESET}\n")


def display_sequences(
    sequences: list[SequenceMatch],
    out: Optional[TextIO] = None,
):
    """Display event sequence matches."""
    out = out or sys.stdout
    if not sequences:
        out.write(f"{C.DIM}No matching sequences found.{C.RESET}\n")
        return

    for i, seq in enumerate(sequences):
        out.write(
            f"\n{C.BOLD}{C.GREEN}━━━ Sequence #{i+1} "
            f"(span: {seq.span.total_seconds():.1f}s) ━━━{C.RESET}\n"
        )
        for j, event in enumerate(seq.events):
            marker = f"{C.GREEN}▶{C.RESET}" if j == 0 else f"{C.YELLOW}→{C.RESET}"
            ts = f"{C.CYAN}{_ts_str(event)}{C.RESET}"
            ref = _line_ref(event)
            out.write(f"  {marker} {ts} {ref} {event.content}\n")

    out.write(f"\n{C.BOLD}Total: {len(sequences)} sequence(s){C.RESET}\n")


def display_correlations(
    correlations: list[Correlation],
    target_pattern: str,
    out: Optional[TextIO] = None,
):
    """Display temporal correlation results."""
    out = out or sys.stdout
    if not correlations:
        out.write(f"{C.DIM}No significant correlations found.{C.RESET}\n")
        return

    out.write(
        f"\n{C.BOLD}Patterns correlated with {C.RED}{target_pattern}{C.RESET}"
        f"{C.BOLD}:{C.RESET}\n\n"
    )
    out.write(
        f"  {C.DIM}{'Pattern':<30} {'Count':>5} {'Avg Offset':>12} "
        f"{'Direction':>10}{C.RESET}\n"
    )
    out.write(f"  {C.DIM}{'─' * 62}{C.RESET}\n")

    for c in correlations:
        direction_color = {
            "before": C.YELLOW,
            "after": C.BLUE,
            "both": C.MAGENTA,
        }.get(c.direction, C.WHITE)

        out.write(
            f"  {C.BOLD}{c.pattern:<30}{C.RESET} "
            f"{c.count:>5} "
            f"{c.avg_offset_seconds:>+10.1f}s "
            f"{direction_color}{c.direction:>10}{C.RESET}\n"
        )


def display_timeline(
    buckets: list[TimeBucket],
    pattern: str,
    width: int = 60,
    out: Optional[TextIO] = None,
):
    """Display a terminal-based timeline histogram."""
    out = out or sys.stdout
    if not buckets:
        out.write(f"{C.DIM}No data for timeline.{C.RESET}\n")
        return

    max_count = max(b.count for b in buckets)
    if max_count == 0:
        out.write(f"{C.DIM}No matching events.{C.RESET}\n")
        return

    out.write(
        f"\n{C.BOLD}Timeline: {C.CYAN}{pattern}{C.RESET}\n"
    )
    out.write(f"{C.DIM}{'─' * (width + 25)}{C.RESET}\n")

    for bucket in buckets:
        ts_label = bucket.start.strftime("%H:%M:%S")
        bar_len = int((bucket.count / max_count) * width) if max_count > 0 else 0
        bar = "█" * bar_len

        if bucket.count > max_count * 0.8:
            color = C.RED
        elif bucket.count > max_count * 0.5:
            color = C.YELLOW
        else:
            color = C.GREEN

        count_str = f"({bucket.count})"
        out.write(
            f"  {C.DIM}{ts_label}{C.RESET} "
            f"{color}{bar}{C.RESET} "
            f"{C.DIM}{count_str}{C.RESET}\n"
        )

    total = sum(b.count for b in buckets)
    out.write(f"\n{C.BOLD}Total events: {total}{C.RESET}\n")


def display_entries(
    entries: list[LogEntry],
    pattern: str = "",
    out: Optional[TextIO] = None,
):
    """Display a list of matching log entries."""
    out = out or sys.stdout
    if not entries:
        out.write(f"{C.DIM}No matches found.{C.RESET}\n")
        return

    for e in entries:
        print_entry(e, highlight=pattern, out=out)

    out.write(f"\n{C.BOLD}Total: {len(entries)} match(es){C.RESET}\n")


# ---------------------------------------------------------------------------
# Gap display
# ---------------------------------------------------------------------------

def _format_duration(td: timedelta) -> str:
    """Format a timedelta as a human-readable string."""
    total = int(td.total_seconds())
    if total < 60:
        return f"{total}s"
    minutes, seconds = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    parts = []
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if seconds:
        parts.append(f"{seconds}s")
    return " ".join(parts)


def display_gaps(
    gaps: list[GapResult],
    out: Optional[TextIO] = None,
):
    """Display detected time gaps."""
    out = out or sys.stdout
    if not gaps:
        out.write(f"{C.DIM}No time gaps found exceeding threshold.{C.RESET}\n")
        return

    out.write(f"\n{C.BOLD}Time Gaps ({len(gaps)} found){C.RESET}\n")
    out.write(f"{C.DIM}{'─' * 70}{C.RESET}\n")

    for i, gap in enumerate(gaps):
        ts_start = _ts_str(gap.start) if gap.start.timestamp else "??:??:??"
        ts_end = _ts_str(gap.end) if gap.end.timestamp else "??:??:??"
        dur = _format_duration(gap.duration)
        out.write(
            f"  {C.CYAN}[{ts_start}]{C.RESET} → "
            f"{C.CYAN}[{ts_end}]{C.RESET} = "
            f"{C.YELLOW}{C.BOLD}{dur}{C.RESET} gap "
            f"{C.DIM}(no events){C.RESET}\n"
        )

    out.write(f"\n{C.BOLD}Largest gap: {_format_duration(gaps[0].duration)}{C.RESET}\n")


# ---------------------------------------------------------------------------
# Session display
# ---------------------------------------------------------------------------

def display_sessions(
    sessions: list[Session],
    max_entries_per_session: int = 0,
    out: Optional[TextIO] = None,
):
    """Display detected sessions."""
    out = out or sys.stdout
    if not sessions:
        out.write(f"{C.DIM}No sessions detected.{C.RESET}\n")
        return

    for session in sessions:
        dur = _format_duration(session.duration) if session.duration.total_seconds() > 0 else "<1s"
        start_str = session.start.strftime("%H:%M:%S")
        end_str = session.end.strftime("%H:%M:%S")

        out.write(
            f"\n{C.BOLD}{C.GREEN}=== Session {session.number}: "
            f"{start_str} – {end_str} "
            f"({dur}, {session.count} events) ==={C.RESET}\n"
        )

        entries_to_show = session.entries
        if max_entries_per_session > 0 and len(entries_to_show) > max_entries_per_session:
            for e in entries_to_show[:max_entries_per_session]:
                print_entry(e, out=out)
            out.write(
                f"  {C.DIM}... and {len(entries_to_show) - max_entries_per_session} more{C.RESET}\n"
            )
        else:
            for e in entries_to_show:
                print_entry(e, out=out)

    total_events = sum(s.count for s in sessions)
    out.write(
        f"\n{C.BOLD}Total: {len(sessions)} session(s), "
        f"{total_events} event(s){C.RESET}\n"
    )


# ---------------------------------------------------------------------------
# Context display
# ---------------------------------------------------------------------------

def display_context_matches(
    results: list[ContextMatch],
    pattern: str = "",
    out: Optional[TextIO] = None,
):
    """Display context-aware search results."""
    out = out or sys.stdout
    if not results:
        out.write(f"{C.DIM}No matches found.{C.RESET}\n")
        return

    for i, result in enumerate(results):
        out.write(
            f"\n{C.BOLD}{C.MAGENTA}━━━ Match #{i+1} "
            f"{_line_ref(result.match)} ━━━{C.RESET}\n"
        )
        for e in result.before:
            ts = f"{C.DIM}{_ts_str(e)}{C.RESET}"
            ref = _line_ref(e)
            out.write(f"  {ts} {ref} {C.DIM}{e.content}{C.RESET}\n")

        print_entry(result.match, highlight=pattern, out=out)

        for e in result.after:
            ts = f"{C.DIM}{_ts_str(e)}{C.RESET}"
            ref = _line_ref(e)
            out.write(f"  {ts} {ref} {C.DIM}{e.content}{C.RESET}\n")

    out.write(f"\n{C.BOLD}Total: {len(results)} match(es){C.RESET}\n")


# ---------------------------------------------------------------------------
# JSON / CSV output
# ---------------------------------------------------------------------------

def entries_to_json(entries: list[LogEntry]) -> str:
    """Serialize log entries to JSON string."""
    records = []
    for e in entries:
        records.append({
            "line_number": e.line_number,
            "timestamp": e.timestamp.isoformat() if e.timestamp else None,
            "content": e.content,
            "raw": e.raw,
        })
    return json.dumps(records, indent=2)


def entries_to_csv(entries: list[LogEntry]) -> str:
    """Serialize log entries to CSV string."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["line_number", "timestamp", "content"])
    for e in entries:
        writer.writerow([
            e.line_number,
            e.timestamp.isoformat() if e.timestamp else "",
            e.content,
        ])
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def display_summary(
    match_count: int,
    elapsed_seconds: float,
    out: Optional[TextIO] = None,
):
    """Display a summary line."""
    out = out or sys.stderr
    out.write(
        f"{C.DIM}Found {match_count} matches in {elapsed_seconds:.2f}s{C.RESET}\n"
    )
