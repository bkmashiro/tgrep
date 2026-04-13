# tgrep — Time-Aware Grep for Log Analysis

**tgrep** adds temporal intelligence to log searching. Instead of grepping for text patterns and manually correlating timestamps, tgrep lets you query logs the way you think about incidents: "what happened in the 30 seconds before the crash?", "does pattern A always precede pattern B?", "show me the error rate over time."

## Why?

Every developer investigating a production incident does the same dance: `grep ERROR`, scan timestamps, mentally compute windows, pipe through `awk` to extract time ranges, squint at the results. Log aggregation platforms (Splunk, ELK, Datadog) solve this but require infrastructure. tgrep brings temporal log queries to the command line with zero setup.

## Features

| Command | What it does |
|---|---|
| `search` | Grep with timestamp awareness — filter by time range |
| `window` | Find events in a time window around anchor events |
| `sequence` | Match ordered event sequences within a time window |
| `correlate` | Discover patterns that co-occur temporally with a target |
| `timeline` | Visualize event frequency as a terminal histogram |

**Auto-detection** of timestamp formats: ISO 8601 (with/without fractional seconds, timezones, Z suffix), syslog, Apache/Nginx, Unix epoch (seconds and milliseconds), US/EU date formats.

**Multi-line support**: stack traces and continuation lines automatically inherit the timestamp of their parent log entry.

## Install

No dependencies beyond Python 3.10+.

```bash
# Clone and use directly
git clone <repo-url> && cd tgrep
python -m src search "ERROR" /var/log/app.log

# Or install as a CLI tool
pip install -e .
tgrep search "ERROR" /var/log/app.log
```

## Usage

### Basic search
```bash
# Find all errors
tgrep search "ERROR" app.log

# Case-insensitive regex supported
tgrep search "timeout|connection refused" app.log
```

### Window search — what happened before a crash?
```bash
# Show everything in the 30 seconds before each OOM kill
tgrep window "OOM" app.log --before 30s

# 5-minute window before, 1-minute after, filtered to DB queries
tgrep window "timeout" app.log --before 5m --after 1m --filter "SELECT|INSERT"
```

### Sequence matching — ordered event chains
```bash
# Connection drop → retry → failure within 60 seconds
tgrep sequence "connection lost" "retry" "fatal" app.log --within 60s

# Deploy started → health check passed, but NOT if rollback happened
tgrep sequence "deploy started" "health check passed" app.log \
    --within 5m --not-followed-by "rollback"
```

### Correlation — what always precedes errors?
```bash
# What tokens frequently appear near 503 errors?
tgrep correlate "503" app.log --window 2m

# Narrower analysis with higher threshold
tgrep correlate "OOM" app.log --window 1m --min 3 --top 5
```

### Timeline — event frequency histogram
```bash
# Error rate over 1-minute buckets
tgrep timeline "ERROR" app.log --bucket 1m

# Request rate in 5-second buckets
tgrep timeline "GET /api" access.log --bucket 5s
```

### Reading from stdin
```bash
journalctl -u myapp --since "1 hour ago" | tgrep window "segfault" --before 10s
kubectl logs deployment/api | tgrep correlate "500"
docker logs webapp 2>&1 | tgrep timeline "ERROR" --bucket 30s
```

### Forcing a timestamp format
```bash
tgrep --format syslog window "OOM" /var/log/syslog --before 1m
```

## Architecture

```
src/
├── __init__.py        # Package metadata
├── __main__.py        # python -m src entry point
├── timestamp.py       # Timestamp auto-detection and parsing
├── parser.py          # Log file → LogEntry objects
├── query.py           # Temporal query engine (window, sequence, correlate, timeline)
├── display.py         # Terminal output with ANSI colors
└── cli.py             # Argument parsing and command dispatch

tests/
├── test_timestamp.py  # Timestamp format detection and parsing
├── test_parser.py     # Log line parsing and multi-line handling
├── test_query.py      # All temporal query operations
├── test_display.py    # Output formatting
├── test_cli.py        # CLI argument parsing and integration
└── test_integration.py # End-to-end scenarios

demo/
├── generate_logs.py   # Generates realistic demo log files
└── run_demo.sh        # Runs all example queries on generated logs
```

### Design decisions

- **Zero dependencies**: only the Python standard library. Installs anywhere Python runs.
- **Auto-detection over configuration**: the timestamp format voter examines sample lines and picks the most common format, so you rarely need `--format`.
- **Inherited timestamps**: lines without a parseable timestamp (stack traces, multi-line messages) inherit from the previous line — this prevents orphaned entries in temporal queries.
- **Regex throughout**: all pattern arguments accept Python regex, so `tgrep search "error|fatal|panic"` works naturally.

## Demo

```bash
# Generate sample logs and run all example queries
bash demo/run_demo.sh
```

## Tests

```bash
python -m pytest tests/ -v
```

## License

MIT
