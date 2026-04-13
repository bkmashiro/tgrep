#!/usr/bin/env python3
"""Generate realistic demo log files for tgrep.

Creates several log files demonstrating different scenarios:
1. A web server log with periodic errors preceded by warning signs
2. A microservice log showing cascading failures
3. A syslog-format system log
"""

import os
import random
from datetime import datetime, timedelta

random.seed(9435)  # From seed numbers for reproducibility

OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))


def generate_webserver_log():
    """Web server log with error patterns."""
    lines = []
    t = datetime(2026, 4, 13, 8, 0, 0)
    endpoints = ["/api/users", "/api/orders", "/api/products", "/health", "/api/search"]
    methods = ["GET", "POST", "GET", "GET", "GET"]

    for _ in range(500):
        t += timedelta(seconds=random.uniform(0.1, 3.0))
        endpoint = random.choice(endpoints)
        method = random.choice(methods)
        status = random.choices([200, 201, 304, 400, 404, 500, 503], weights=[60, 10, 10, 5, 5, 3, 2])[0]
        latency = random.uniform(5, 200)

        # Inject failure cascade pattern every ~100 lines
        if random.random() < 0.02:
            # Slow queries → connection pool exhaustion → 503s
            for j in range(5):
                t += timedelta(seconds=random.uniform(0.5, 2.0))
                lines.append(
                    f"{t.isoformat()} WARN  [db-pool] Slow query on /api/orders: {random.randint(800, 3000)}ms"
                )
            for j in range(3):
                t += timedelta(seconds=random.uniform(1, 5))
                lines.append(
                    f"{t.isoformat()} ERROR [db-pool] Connection pool exhausted, active={random.randint(48, 50)}/50"
                )
            for j in range(8):
                t += timedelta(seconds=random.uniform(0.1, 1.0))
                lines.append(
                    f"{t.isoformat()} ERROR [http] {method} {endpoint} 503 Service Unavailable ({random.randint(5000, 15000)}ms)"
                )
            t += timedelta(seconds=random.uniform(5, 15))
            lines.append(f"{t.isoformat()} INFO  [db-pool] Connection pool recovered, active=5/50")
            continue

        # Inject memory pressure pattern
        if random.random() < 0.01:
            for j in range(4):
                t += timedelta(seconds=random.uniform(10, 30))
                usage = 70 + j * 7
                lines.append(f"{t.isoformat()} WARN  [memory] Heap usage at {usage}%: {usage * 10}MB / 1024MB")
            t += timedelta(seconds=random.uniform(2, 5))
            lines.append(f"{t.isoformat()} ERROR [memory] OOM killed: heap exceeded 95% threshold")
            lines.append(f"{t.isoformat()} INFO  [system] Process restarting...")
            t += timedelta(seconds=random.uniform(5, 10))
            lines.append(f"{t.isoformat()} INFO  [system] Process started, PID={random.randint(1000, 9999)}")
            continue

        level = "INFO" if status < 400 else ("WARN" if status < 500 else "ERROR")
        lines.append(
            f"{t.isoformat()} {level:<5} [http] {method} {endpoint} {status} ({latency:.0f}ms)"
        )

    with open(os.path.join(OUTPUT_DIR, "webserver.log"), "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"Generated webserver.log ({len(lines)} lines)")


def generate_microservice_log():
    """Microservice log showing cascading failures across services."""
    lines = []
    t = datetime(2026, 4, 13, 10, 0, 0)
    services = ["api-gateway", "user-service", "order-service", "payment-service", "notification-service"]

    for _ in range(400):
        t += timedelta(seconds=random.uniform(0.5, 5.0))
        svc = random.choice(services)
        trace_id = f"tr-{random.randint(10000, 99999)}"

        # Normal request
        if random.random() > 0.03:
            lines.append(
                f"{t.isoformat()} INFO  [{svc}] [{trace_id}] Request processed successfully"
            )
            continue

        # Cascading failure: payment-service timeout → order-service retry → api-gateway 500
        tid = f"tr-{random.randint(10000, 99999)}"
        lines.append(f"{t.isoformat()} INFO  [api-gateway] [{tid}] POST /api/orders/checkout")
        t += timedelta(seconds=random.uniform(0.1, 0.5))
        lines.append(f"{t.isoformat()} INFO  [order-service] [{tid}] Processing checkout")
        t += timedelta(seconds=random.uniform(0.5, 1.0))
        lines.append(f"{t.isoformat()} INFO  [order-service] [{tid}] Calling payment-service")
        t += timedelta(seconds=random.uniform(5, 10))
        lines.append(f"{t.isoformat()} WARN  [payment-service] [{tid}] Upstream timeout from bank API")
        t += timedelta(seconds=random.uniform(0.1, 0.5))
        lines.append(f"{t.isoformat()} ERROR [payment-service] [{tid}] Payment processing failed: timeout")
        t += timedelta(seconds=random.uniform(0.5, 2))
        lines.append(f"{t.isoformat()} WARN  [order-service] [{tid}] Payment failed, retry 1/3")
        t += timedelta(seconds=random.uniform(5, 10))
        lines.append(f"{t.isoformat()} ERROR [order-service] [{tid}] Payment failed after 3 retries")
        t += timedelta(seconds=random.uniform(0.1, 0.5))
        lines.append(f"{t.isoformat()} ERROR [api-gateway] [{tid}] POST /api/orders/checkout 500 Internal Server Error")
        t += timedelta(seconds=random.uniform(0.1, 0.5))
        lines.append(f"{t.isoformat()} INFO  [notification-service] [{tid}] Sending failure notification to user")

    with open(os.path.join(OUTPUT_DIR, "microservice.log"), "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"Generated microservice.log ({len(lines)} lines)")


def generate_syslog():
    """System log in syslog format."""
    lines = []
    t = datetime(2026, 4, 13, 6, 0, 0)
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

    processes = ["sshd", "cron", "systemd", "kernel", "nginx", "docker"]

    for _ in range(300):
        t += timedelta(seconds=random.uniform(1, 30))
        month = months[t.month - 1]
        ts_str = f"{month} {t.day:2d} {t.strftime('%H:%M:%S')}"
        proc = random.choice(processes)
        pid = random.randint(100, 9999)

        if proc == "sshd":
            if random.random() < 0.1:
                ip = f"{random.randint(1,254)}.{random.randint(0,254)}.{random.randint(0,254)}.{random.randint(1,254)}"
                lines.append(f"{ts_str} server1 sshd[{pid}]: Failed password for invalid user admin from {ip} port {random.randint(30000,60000)}")
            else:
                lines.append(f"{ts_str} server1 sshd[{pid}]: Accepted publickey for deploy from 10.0.1.5 port {random.randint(30000,60000)}")
        elif proc == "kernel":
            if random.random() < 0.05:
                lines.append(f"{ts_str} server1 kernel: [UFW BLOCK] IN=eth0 SRC={random.randint(1,254)}.{random.randint(0,254)}.{random.randint(0,254)}.{random.randint(1,254)} DST=10.0.1.1 PROTO=TCP DPT={random.randint(1,65535)}")
            else:
                lines.append(f"{ts_str} server1 kernel: [{t.timestamp():.6f}] NIC Link is Up 1000 Mbps")
        elif proc == "docker":
            container = random.choice(["web-1", "api-1", "db-1", "cache-1"])
            if random.random() < 0.05:
                lines.append(f"{ts_str} server1 docker[{pid}]: Container {container} OOM killed")
            else:
                lines.append(f"{ts_str} server1 docker[{pid}]: Container {container} health check passed")
        else:
            lines.append(f"{ts_str} server1 {proc}[{pid}]: Normal operation")

    with open(os.path.join(OUTPUT_DIR, "syslog.log"), "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"Generated syslog.log ({len(lines)} lines)")


if __name__ == "__main__":
    generate_webserver_log()
    generate_microservice_log()
    generate_syslog()
    print(f"\nDemo logs written to {OUTPUT_DIR}/")
