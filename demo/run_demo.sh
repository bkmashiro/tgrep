#!/usr/bin/env bash
# tgrep demo — generates sample logs and runs example queries.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "═══════════════════════════════════════════════════════════"
echo "  tgrep demo — Time-Aware Log Pattern Matching"
echo "═══════════════════════════════════════════════════════════"
echo

# Generate demo logs
echo "▸ Generating demo log files..."
python3 "$SCRIPT_DIR/generate_logs.py"
echo

# 1. Basic search
echo "═══════════════════════════════════════════════════════════"
echo "  1. Basic search: find all ERROR lines"
echo "     Command: python3 -m src search 'ERROR' demo/webserver.log"
echo "═══════════════════════════════════════════════════════════"
cd "$PROJECT_DIR"
python3 -m src search "ERROR" demo/webserver.log 2>/dev/null | head -20
echo "  ... (truncated)"
echo

# 2. Time-window search
echo "═══════════════════════════════════════════════════════════"
echo "  2. Window search: what happened 30s before each OOM kill?"
echo "     Command: python3 -m src window 'OOM' demo/webserver.log --before 30s"
echo "═══════════════════════════════════════════════════════════"
cd "$PROJECT_DIR"
python3 -m src window "OOM" demo/webserver.log --before 30s 2>/dev/null
echo

# 3. Sequence matching
echo "═══════════════════════════════════════════════════════════"
echo "  3. Sequence: payment timeout → retry → failure"
echo "     Command: python3 -m src sequence 'timeout' 'retry' 'failed after' --file demo/microservice.log --within 30s"
echo "═══════════════════════════════════════════════════════════"
cd "$PROJECT_DIR"
python3 -m src sequence "timeout" "retry" "failed after" --file demo/microservice.log --within 30s 2>/dev/null
echo

# 4. Correlation
echo "═══════════════════════════════════════════════════════════"
echo "  4. Correlation: what patterns appear near 503 errors?"
echo "     Command: python3 -m src correlate '503' demo/webserver.log --window 1m"
echo "═══════════════════════════════════════════════════════════"
cd "$PROJECT_DIR"
python3 -m src correlate "503" demo/webserver.log --window 1m 2>/dev/null
echo

# 5. Timeline
echo "═══════════════════════════════════════════════════════════"
echo "  5. Timeline: ERROR frequency over time (1-minute buckets)"
echo "     Command: python3 -m src timeline 'ERROR' demo/webserver.log --bucket 1m"
echo "═══════════════════════════════════════════════════════════"
cd "$PROJECT_DIR"
python3 -m src timeline "ERROR" demo/webserver.log --bucket 1m 2>/dev/null
echo

# 6. Window on syslog
echo "═══════════════════════════════════════════════════════════"
echo "  6. Syslog: events around OOM kills (auto-detects syslog format)"
echo "     Command: python3 -m src window 'OOM' demo/syslog.log --before 1m --after 10s"
echo "═══════════════════════════════════════════════════════════"
cd "$PROJECT_DIR"
python3 -m src window "OOM" demo/syslog.log --before 1m --after 10s 2>/dev/null
echo

echo "═══════════════════════════════════════════════════════════"
echo "  Demo complete! Try your own queries on these log files."
echo "═══════════════════════════════════════════════════════════"
