#!/usr/bin/env python3
"""JVM Metrics Generator — sends JVM runtime metrics via OTLP.

Generates metrics that populate the [Metrics JVM] Overview dashboard panels:
  - jvm.cpu.recent_utilization (gauge)
  - jvm.cpu.time (counter, seconds)
  - jvm.cpu.count (gauge)
  - jvm.memory.used (gauge, bytes, per type + pool)
  - jvm.memory.committed (gauge, bytes, per type + pool)
  - jvm.memory.limit (gauge, bytes, per type + pool)
  - jvm.memory.used_after_last_gc (gauge, bytes, per type + pool)
  - jvm.thread.count (gauge, per state + daemon)
  - jvm.class.count (gauge)
  - jvm.class.loaded (counter)
  - jvm.class.unloaded (counter)
  - jvm.gc.duration (histogram, seconds)

Only emits for services whose language == "java" in the active scenario.

Usage (standalone):
    python3 -m log_generators.jvm_metrics_generator
"""

from __future__ import annotations

import logging
import os
import random
import signal
import threading
import time

from app.telemetry import OTLPClient, _format_attributes, SCHEMA_URL, _now_ns
from app.config import ACTIVE_SCENARIO, SERVICES, NAMESPACE

logger = logging.getLogger("jvm-metrics-generator")

METRICS_INTERVAL = int(os.getenv("JVM_METRICS_INTERVAL", "10"))

SCOPE_NAME = "io.opentelemetry.jvm"
SCOPE_VERSION = "2.12.0-alpha"

# Memory pool definitions: (pool_name, memory_type, limit_mb)
HEAP_POOLS = [
    ("G1 Eden Space", "heap", 256),
    ("G1 Old Gen", "heap", 1024),
    ("G1 Survivor Space", "heap", 64),
]
NON_HEAP_POOLS = [
    ("Metaspace", "non_heap", 256),
    ("CodeCache", "non_heap", 240),
    ("Compressed Class Space", "non_heap", 64),
]
ALL_POOLS = HEAP_POOLS + NON_HEAP_POOLS

# GC definitions: (gc_name, gc_action)
GC_TYPES = [
    ("G1 Young Generation", "end of minor GC"),
    ("G1 Old Generation", "end of major GC"),
]

# Thread states
THREAD_STATES = ["runnable", "blocked", "waiting", "timed_waiting"]


def _load_java_services():
    """Return list of (service_name, service_cfg) for Java services."""
    from scenarios import get_scenario
    scenario = get_scenario(ACTIVE_SCENARIO)
    services = scenario.services
    return [(name, cfg) for name, cfg in services.items() if cfg.get("language") == "java"]


def _build_resource(service_name: str, cfg: dict, namespace: str) -> dict:
    attrs = {
        "service.name": service_name,
        "service.namespace": namespace,
        "service.version": "1.0.0",
        "service.instance.id": f"{service_name}-001",
        "telemetry.sdk.name": "opentelemetry",
        "telemetry.sdk.version": "2.12.0",
        "telemetry.sdk.language": "java",
        "cloud.provider": cfg["cloud_provider"],
        "cloud.platform": cfg["cloud_platform"],
        "cloud.region": cfg["cloud_region"],
        "cloud.availability_zone": cfg["cloud_availability_zone"],
        "host.name": f"{service_name}-host",
        "host.architecture": "amd64",
        "os.type": "linux",
        "process.runtime.name": "OpenJDK Runtime Environment",
        "process.runtime.version": "21.0.5+11-LTS",
        "process.runtime.description": "Eclipse Adoptium OpenJDK 64-Bit Server VM 21.0.5+11-LTS",
        "data_stream.type": "metrics",
        "data_stream.dataset": "generic.otel",
        "data_stream.namespace": "default",
    }
    return {"attributes": _format_attributes(attrs), "schemaUrl": SCHEMA_URL}


class JvmState:
    """Tracks stateful metric values for one JVM instance."""

    def __init__(self, rng: random.Random):
        self._rng = rng
        self.cpu_time_s = rng.uniform(500.0, 5000.0)
        self.classes_loaded_total = rng.randint(8000, 15000)
        self.classes_unloaded_total = rng.randint(10, 200)
        # Per-pool memory state: {pool_name: used_mb}
        self.pool_used = {}
        for pool_name, _, limit_mb in ALL_POOLS:
            self.pool_used[pool_name] = rng.uniform(limit_mb * 0.2, limit_mb * 0.6)
        # GC counts
        self.gc_counts = {gc_name: rng.randint(100, 5000) for gc_name, _ in GC_TYPES}

    def tick(self):
        rng = self._rng
        # CPU time advances
        self.cpu_time_s += rng.uniform(0.5, 3.0)
        # Classes change slowly
        if rng.random() < 0.1:
            new_loaded = rng.randint(1, 10)
            self.classes_loaded_total += new_loaded
        if rng.random() < 0.02:
            self.classes_unloaded_total += rng.randint(1, 3)
        # Memory usage fluctuates
        for pool_name, _, limit_mb in ALL_POOLS:
            delta = rng.uniform(-limit_mb * 0.05, limit_mb * 0.08)
            self.pool_used[pool_name] = max(
                limit_mb * 0.1,
                min(limit_mb * 0.9, self.pool_used[pool_name] + delta),
            )
        # GC counts advance
        for gc_name, _ in GC_TYPES:
            if "Young" in gc_name:
                self.gc_counts[gc_name] += rng.randint(1, 5)
            else:
                if rng.random() < 0.3:
                    self.gc_counts[gc_name] += 1


def _build_gauge(name: str, unit: str, value, attributes: dict | None = None) -> dict:
    now = _now_ns()
    dp: dict = {"timeUnixNano": now}
    if isinstance(value, int):
        dp["asInt"] = str(value)
    else:
        dp["asDouble"] = float(value)
    if attributes:
        dp["attributes"] = _format_attributes(attributes)
    return {"name": name, "unit": unit, "gauge": {"dataPoints": [dp]}}


def _build_cumulative_sum(name: str, unit: str, value, is_monotonic: bool = True) -> dict:
    now = _now_ns()
    dp: dict = {
        "startTimeUnixNano": str(int(now) - 3_600_000_000_000),
        "timeUnixNano": now,
    }
    if isinstance(value, int):
        dp["asInt"] = str(value)
    else:
        dp["asDouble"] = float(value)
    return {
        "name": name,
        "unit": unit,
        "sum": {
            "dataPoints": [dp],
            "aggregationTemporality": 2,  # CUMULATIVE
            "isMonotonic": is_monotonic,
        },
    }


def _build_histogram(name: str, unit: str, count: int, sum_val: float,
                     bucket_counts: list[int], bounds: list[float],
                     attributes: dict | None = None) -> dict:
    now = _now_ns()
    dp: dict = {
        "startTimeUnixNano": str(int(now) - 3_600_000_000_000),
        "timeUnixNano": now,
        "count": str(count),
        "sum": sum_val,
        "bucketCounts": [str(c) for c in bucket_counts],
        "explicitBounds": bounds,
    }
    if attributes:
        dp["attributes"] = _format_attributes(attributes)
    return {
        "name": name,
        "unit": unit,
        "histogram": {
            "dataPoints": [dp],
            "aggregationTemporality": 2,
        },
    }


def _generate_metrics(state: JvmState, rng: random.Random) -> list:
    state.tick()
    metrics = []
    MB = 1024 * 1024

    # ── CPU ────────────────────────────────────────────────────────────
    cpu_util = rng.uniform(0.05, 0.45)
    metrics.append(_build_gauge("jvm.cpu.recent_utilization", "1", cpu_util))
    metrics.append(_build_cumulative_sum("jvm.cpu.time", "s", state.cpu_time_s))
    metrics.append(_build_gauge("jvm.cpu.count", "{cpu}", 4))

    # ── Memory (per pool) ─────────────────────────────────────────────
    for pool_name, mem_type, limit_mb in ALL_POOLS:
        pool_attrs = {"jvm.memory.type": mem_type, "jvm.memory.pool.name": pool_name}
        used_bytes = int(state.pool_used[pool_name] * MB)
        limit_bytes = int(limit_mb * MB)
        committed_bytes = int(min(limit_bytes, used_bytes * rng.uniform(1.1, 1.4)))
        after_gc_bytes = int(used_bytes * rng.uniform(0.3, 0.7))

        metrics.append(_build_gauge("jvm.memory.used", "By", used_bytes, pool_attrs))
        metrics.append(_build_gauge("jvm.memory.committed", "By", committed_bytes, pool_attrs))
        metrics.append(_build_gauge("jvm.memory.limit", "By", limit_bytes, pool_attrs))
        metrics.append(_build_gauge("jvm.memory.used_after_last_gc", "By", after_gc_bytes, pool_attrs))

    # ── Threads ───────────────────────────────────────────────────────
    for thread_state in THREAD_STATES:
        for daemon in [True, False]:
            if thread_state == "runnable":
                count = rng.randint(10, 40) if daemon else rng.randint(2, 8)
            elif thread_state == "timed_waiting":
                count = rng.randint(5, 20) if daemon else rng.randint(1, 5)
            elif thread_state == "waiting":
                count = rng.randint(3, 15) if daemon else rng.randint(1, 4)
            else:  # blocked
                count = rng.randint(0, 2) if daemon else rng.randint(0, 1)
            metrics.append(_build_gauge(
                "jvm.thread.count", "{thread}", count,
                {"jvm.thread.state": thread_state, "jvm.thread.daemon": daemon},
            ))

    # ── Classes ───────────────────────────────────────────────────────
    current_classes = state.classes_loaded_total - state.classes_unloaded_total
    metrics.append(_build_gauge("jvm.class.count", "{class}", current_classes))
    metrics.append(_build_cumulative_sum("jvm.class.loaded", "{class}", state.classes_loaded_total))
    metrics.append(_build_cumulative_sum("jvm.class.unloaded", "{class}", state.classes_unloaded_total))

    # ── GC duration histogram ─────────────────────────────────────────
    bounds = [0.01, 0.1, 1.0, 10.0]
    for gc_name, gc_action in GC_TYPES:
        gc_count = state.gc_counts[gc_name]
        if "Young" in gc_name:
            avg_dur = rng.uniform(0.005, 0.03)
        else:
            avg_dur = rng.uniform(0.05, 0.3)
        gc_sum = gc_count * avg_dur
        # Distribute counts across buckets (most in first 1-2 buckets)
        b0 = int(gc_count * rng.uniform(0.3, 0.5))
        b1 = int(gc_count * rng.uniform(0.3, 0.5))
        b2 = int(gc_count * rng.uniform(0.05, 0.15))
        b3 = max(0, gc_count - b0 - b1 - b2 - rng.randint(0, 3))
        b_overflow = max(0, gc_count - b0 - b1 - b2 - b3)
        bucket_counts = [b0, b1, b2, b3, b_overflow]
        metrics.append(_build_histogram(
            "jvm.gc.duration", "s", gc_count, gc_sum, bucket_counts, bounds,
            {"jvm.gc.name": gc_name, "jvm.gc.action": gc_action},
        ))

    return metrics


def run(client: OTLPClient, stop_event: threading.Event, scenario_data: dict | None = None) -> None:
    """Run JVM metrics generator loop until stop_event is set."""
    rng = random.Random()

    if scenario_data:
        ns = scenario_data["namespace"]
        services = scenario_data["services"]
        java_services = [(name, cfg) for name, cfg in services.items() if cfg.get("language") == "java"]
    else:
        ns = NAMESPACE
        java_services = _load_java_services()

    if not java_services:
        logger.info("No Java services found in active scenario — JVM metrics generator idle")
        return

    resources = {name: _build_resource(name, cfg, ns) for name, cfg in java_services}
    states = {name: JvmState(rng) for name, _ in java_services}

    logger.info("JVM metrics generator started (interval=%ds, services=%s)",
                METRICS_INTERVAL, [name for name, _ in java_services])

    scrape_count = 0
    while not stop_event.is_set():
        resource_metrics = []
        for name, _ in java_services:
            metrics = _generate_metrics(states[name], rng)
            resource_metrics.append({
                "resource": resources[name],
                "scopeMetrics": [{
                    "scope": {"name": SCOPE_NAME, "version": SCOPE_VERSION},
                    "metrics": metrics,
                }],
            })

        payload = {"resourceMetrics": resource_metrics}
        client._send(f"{client.endpoint}/v1/metrics", payload, "jvm-metrics")

        scrape_count += 1
        if scrape_count % 6 == 0:
            logger.info("JVM metrics scrape %d complete (%d services)",
                        scrape_count, len(java_services))

        stop_event.wait(METRICS_INTERVAL)

    logger.info("JVM metrics generator stopped after %d scrapes", scrape_count)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    client = OTLPClient()
    stop_event = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop_event.set())
    signal.signal(signal.SIGTERM, lambda *_: stop_event.set())

    duration = int(os.environ.get("RUN_DURATION", "60"))
    timer = threading.Timer(duration, stop_event.set)
    timer.daemon = True
    timer.start()
    logger.info("Running for %ds (standalone)", duration)
    run(client, stop_event)
    timer.cancel()
    client.close()


if __name__ == "__main__":
    main()
