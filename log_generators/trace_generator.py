#!/usr/bin/env python3
"""Trace Generator — sends distributed traces for APM Service Map via OTLP.

Generates realistic distributed traces across NOVA-7's 9 services with proper
parent-child span relationships, service topology, and APM-compatible attributes.

Usage (standalone):
    python3 -m log_generators.trace_generator
"""

from __future__ import annotations

import logging
import os
import random
import secrets
import signal
import threading
import time

from app.telemetry import OTLPClient, _format_attributes, SCHEMA_URL
from app.config import SERVICES, CHANNEL_REGISTRY

logger = logging.getLogger("trace-generator")

# ── Configuration ─────────────────────────────────────────────────────────────
BATCH_INTERVAL_MIN = 2
BATCH_INTERVAL_MAX = 4

# Span kind constants
SPAN_KIND_INTERNAL = 1
SPAN_KIND_SERVER = 2
SPAN_KIND_CLIENT = 3

# Status codes
STATUS_OK = 1
STATUS_ERROR = 2

# ── Service topology — defines inter-service call patterns ────────────────────
# Maps caller -> list of (callee, endpoint, method) tuples
SERVICE_TOPOLOGY = {
    "mission-control": [
        ("fuel-system", "/api/v1/fuel/status", "GET"),
        ("fuel-system", "/api/v1/fuel/pressure", "GET"),
        ("navigation", "/api/v1/nav/position", "GET"),
        ("navigation", "/api/v1/nav/trajectory", "POST"),
        ("ground-systems", "/api/v1/ground/weather", "GET"),
        ("ground-systems", "/api/v1/ground/power", "GET"),
        ("comms-array", "/api/v1/comms/status", "GET"),
        ("telemetry-relay", "/api/v1/relay/health", "GET"),
    ],
    "navigation": [
        ("sensor-validator", "/api/v1/validate/imu", "POST"),
        ("sensor-validator", "/api/v1/validate/gps", "POST"),
        ("sensor-validator", "/api/v1/validate/star-tracker", "POST"),
    ],
    "fuel-system": [
        ("sensor-validator", "/api/v1/validate/pressure", "POST"),
        ("sensor-validator", "/api/v1/validate/thermal", "POST"),
        ("sensor-validator", "/api/v1/validate/flow-rate", "POST"),
    ],
    "payload-monitor": [
        ("sensor-validator", "/api/v1/validate/vibration", "POST"),
        ("sensor-validator", "/api/v1/validate/payload-thermal", "POST"),
    ],
    "range-safety": [
        ("navigation", "/api/v1/nav/position", "GET"),
        ("comms-array", "/api/v1/comms/tracking", "GET"),
    ],
    "telemetry-relay": [
        ("comms-array", "/api/v1/comms/relay", "POST"),
    ],
}

# Entry-point endpoints for each service (external or scheduled triggers)
ENTRY_ENDPOINTS = {
    "mission-control": [
        ("/api/v1/mission/status", "GET"),
        ("/api/v1/mission/countdown", "GET"),
        ("/api/v1/mission/telemetry", "POST"),
    ],
    "fuel-system": [
        ("/api/v1/fuel/monitor", "POST"),
    ],
    "navigation": [
        ("/api/v1/nav/compute", "POST"),
    ],
    "ground-systems": [
        ("/api/v1/ground/monitor", "POST"),
    ],
    "comms-array": [
        ("/api/v1/comms/poll", "POST"),
    ],
    "payload-monitor": [
        ("/api/v1/payload/scan", "POST"),
    ],
    "sensor-validator": [
        ("/api/v1/validate/batch", "POST"),
    ],
    "telemetry-relay": [
        ("/api/v1/relay/forward", "POST"),
    ],
    "range-safety": [
        ("/api/v1/safety/check", "POST"),
    ],
}

# Database operations for services that access databases
DB_OPERATIONS = {
    "mission-control": [
        ("SELECT", "mission_events", "SELECT * FROM mission_events WHERE phase = ? ORDER BY timestamp DESC LIMIT 100"),
        ("INSERT", "telemetry_readings", "INSERT INTO telemetry_readings (service, metric, value, ts) VALUES (?, ?, ?, NOW())"),
    ],
    "fuel-system": [
        ("SELECT", "sensor_data", "SELECT reading, baseline FROM sensor_data WHERE sensor_type = 'pressure' AND ts > NOW() - INTERVAL 5 MINUTE"),
        ("UPDATE", "sensor_registry", "UPDATE sensor_registry SET last_reading = ?, last_seen = NOW() WHERE sensor_id = ?"),
    ],
    "navigation": [
        ("SELECT", "calibration_epochs", "SELECT epoch, baseline FROM calibration_epochs WHERE sensor_type IN ('imu', 'gps', 'star_tracker')"),
    ],
    "sensor-validator": [
        ("SELECT", "validation_results", "SELECT * FROM validation_results WHERE sensor_id = ? AND validated_at > NOW() - INTERVAL 1 MINUTE"),
        ("INSERT", "validation_results", "INSERT INTO validation_results (sensor_id, result, confidence, validated_at) VALUES (?, ?, ?, NOW())"),
    ],
    "ground-systems": [
        ("SELECT", "weather_stations", "SELECT station_id, temp, wind_speed, visibility FROM weather_stations WHERE last_update > NOW() - INTERVAL 30 SECOND"),
    ],
}


# ── Helpers ───────────────────────────────────────────────────────────────────
def _gen_trace_id() -> str:
    return secrets.token_hex(16)


def _gen_span_id() -> str:
    return secrets.token_hex(8)


def _build_resource(service_name: str) -> dict:
    cfg = SERVICES[service_name]
    attrs = {
        "service.name": service_name,
        "service.namespace": "nova7",
        "service.version": "1.0.0",
        "service.instance.id": f"{service_name}-001",
        "telemetry.sdk.language": cfg.get("language", "python"),
        "telemetry.sdk.name": "opentelemetry",
        "telemetry.sdk.version": "1.24.0",
        "cloud.provider": cfg["cloud_provider"],
        "cloud.platform": cfg["cloud_platform"],
        "cloud.region": cfg["cloud_region"],
        "cloud.availability_zone": cfg["cloud_availability_zone"],
        "deployment.environment": "production",
        "host.name": f"{service_name}-host",
        "host.architecture": "amd64",
        "os.type": "linux",
        "data_stream.type": "traces",
        "data_stream.dataset": "generic",
        "data_stream.namespace": "default",
    }
    return {
        "attributes": _format_attributes(attrs),
        "schemaUrl": SCHEMA_URL,
    }


def _generate_trace(client: OTLPClient, resources: dict, rng: random.Random,
                    chaos_affected: set[str] | None = None) -> dict[str, list]:
    """Generate a single distributed trace across multiple services.

    Returns a dict mapping service_name -> list of spans for that service.
    When chaos_affected is provided, those services get high error rates (70%)
    and elevated latency; all others use a healthy 3% baseline.
    """
    trace_id = _gen_trace_id()
    spans_by_service: dict[str, list] = {}

    # Pick a random entry-point service (weighted toward mission-control)
    entry_services = ["mission-control"] * 4 + [
        "fuel-system", "navigation", "ground-systems",
        "payload-monitor", "range-safety", "telemetry-relay",
    ]
    entry_service = rng.choice(entry_services)
    entry_endpoint, entry_method = rng.choice(ENTRY_ENDPOINTS[entry_service])

    # Determine if this trace has errors — chaos-aware probability
    if chaos_affected and entry_service in chaos_affected:
        is_error_trace = rng.random() < 0.70
    else:
        is_error_trace = rng.random() < 0.03

    error_service = None
    if is_error_trace:
        # If entry service is affected by chaos, it is the error source
        if chaos_affected and entry_service in chaos_affected:
            error_service = entry_service
        else:
            downstream = SERVICE_TOPOLOGY.get(entry_service, [])
            if downstream:
                error_service = rng.choice(downstream)[0]

    # Latency: affected services get 200-2000ms, normal get 50-500ms
    if chaos_affected and entry_service in chaos_affected:
        total_duration = rng.randint(200, 2000)
    else:
        total_duration = rng.randint(50, 500)

    # Root SERVER span for the entry-point service
    root_span_id = _gen_span_id()
    root_status = STATUS_ERROR if (is_error_trace and error_service == entry_service) else STATUS_OK
    root_http_status = rng.choice([500, 502, 503]) if root_status == STATUS_ERROR else 200

    root_span = client.build_span(
        name=f"{entry_method} {entry_endpoint}",
        trace_id=trace_id,
        span_id=root_span_id,
        kind=SPAN_KIND_SERVER,
        duration_ms=total_duration,
        status_code=root_status,
        attributes={
            "http.request.method": entry_method,
            "url.path": entry_endpoint,
            "http.response.status_code": root_http_status,
            "server.address": f"{entry_service}-host",
            "server.port": 8080,
            "network.protocol.version": "1.1",
        },
    )
    spans_by_service.setdefault(entry_service, []).append(root_span)

    # Add DB span if this service does DB operations
    if entry_service in DB_OPERATIONS and rng.random() < 0.6:
        op, table, statement = rng.choice(DB_OPERATIONS[entry_service])
        db_span_id = _gen_span_id()
        db_duration = rng.randint(2, min(30, total_duration // 3))
        db_span = client.build_span(
            name=f"{op} {table}",
            trace_id=trace_id,
            span_id=db_span_id,
            parent_span_id=root_span_id,
            kind=SPAN_KIND_CLIENT,
            duration_ms=db_duration,
            status_code=STATUS_OK,
            attributes={
                "db.system": "mysql",
                "db.name": "nova7_telemetry",
                "db.statement": statement,
                "db.operation": op,
                "db.sql.table": table,
                "net.peer.name": "nova7-mysql-host",
                "net.peer.port": 3306,
            },
        )
        spans_by_service.setdefault(entry_service, []).append(db_span)

    # Generate downstream CLIENT+SERVER spans based on topology
    downstream_calls = SERVICE_TOPOLOGY.get(entry_service, [])
    if downstream_calls:
        # Pick 1-3 downstream calls
        num_calls = min(len(downstream_calls), rng.randint(1, 3))
        selected_calls = rng.sample(downstream_calls, num_calls)

        for callee_service, callee_endpoint, callee_method in selected_calls:
            # Chaos-aware: affected callee services get elevated latency + error chance
            if chaos_affected and callee_service in chaos_affected:
                call_duration = rng.randint(100, max(200, total_duration // 2))
                callee_error = rng.random() < 0.70
            else:
                call_duration = rng.randint(10, max(20, total_duration // 2))
                callee_error = False

            is_this_error = (is_error_trace and callee_service == error_service) or callee_error
            call_status = STATUS_ERROR if is_this_error else STATUS_OK
            call_http_status = rng.choice([500, 502, 503, 504]) if is_this_error else 200

            # CLIENT span on the caller side
            client_span_id = _gen_span_id()
            client_span = client.build_span(
                name=f"{callee_method} {callee_endpoint}",
                trace_id=trace_id,
                span_id=client_span_id,
                parent_span_id=root_span_id,
                kind=SPAN_KIND_CLIENT,
                duration_ms=call_duration,
                status_code=call_status,
                attributes={
                    "http.request.method": callee_method,
                    "url.path": callee_endpoint,
                    "http.response.status_code": call_http_status,
                    "server.address": f"{callee_service}-host",
                    "server.port": 8080,
                    "net.peer.name": f"{callee_service}-host",
                    "net.peer.port": 8080,
                },
            )
            spans_by_service.setdefault(entry_service, []).append(client_span)

            # SERVER span on the callee side
            server_span_id = _gen_span_id()
            server_duration = call_duration - rng.randint(1, max(1, call_duration // 5))
            server_span = client.build_span(
                name=f"{callee_method} {callee_endpoint}",
                trace_id=trace_id,
                span_id=server_span_id,
                parent_span_id=client_span_id,
                kind=SPAN_KIND_SERVER,
                duration_ms=max(1, server_duration),
                status_code=call_status,
                attributes={
                    "http.request.method": callee_method,
                    "url.path": callee_endpoint,
                    "http.response.status_code": call_http_status,
                    "server.address": f"{callee_service}-host",
                    "server.port": 8080,
                },
            )
            spans_by_service.setdefault(callee_service, []).append(server_span)

            # DB span on the callee side (if applicable)
            if callee_service in DB_OPERATIONS and rng.random() < 0.5:
                op, table, statement = rng.choice(DB_OPERATIONS[callee_service])
                db_span_id = _gen_span_id()
                db_duration = rng.randint(1, max(1, server_duration // 3))
                db_span = client.build_span(
                    name=f"{op} {table}",
                    trace_id=trace_id,
                    span_id=db_span_id,
                    parent_span_id=server_span_id,
                    kind=SPAN_KIND_CLIENT,
                    duration_ms=db_duration,
                    status_code=STATUS_OK,
                    attributes={
                        "db.system": "mysql",
                        "db.name": "nova7_telemetry",
                        "db.statement": statement,
                        "db.operation": op,
                        "db.sql.table": table,
                        "net.peer.name": "nova7-mysql-host",
                        "net.peer.port": 3306,
                    },
                )
                spans_by_service.setdefault(callee_service, []).append(db_span)

            # Second-level downstream calls (e.g., navigation -> sensor-validator)
            second_downstream = SERVICE_TOPOLOGY.get(callee_service, [])
            if second_downstream and rng.random() < 0.4:
                second_callee, second_endpoint, second_method = rng.choice(second_downstream)
                second_duration = rng.randint(5, max(5, server_duration // 2))
                second_status = STATUS_OK

                # CLIENT span
                second_client_id = _gen_span_id()
                second_client_span = client.build_span(
                    name=f"{second_method} {second_endpoint}",
                    trace_id=trace_id,
                    span_id=second_client_id,
                    parent_span_id=server_span_id,
                    kind=SPAN_KIND_CLIENT,
                    duration_ms=second_duration,
                    status_code=second_status,
                    attributes={
                        "http.request.method": second_method,
                        "url.path": second_endpoint,
                        "http.response.status_code": 200,
                        "server.address": f"{second_callee}-host",
                        "server.port": 8080,
                        "net.peer.name": f"{second_callee}-host",
                        "net.peer.port": 8080,
                    },
                )
                spans_by_service.setdefault(callee_service, []).append(second_client_span)

                # SERVER span
                second_server_id = _gen_span_id()
                second_server_span = client.build_span(
                    name=f"{second_method} {second_endpoint}",
                    trace_id=trace_id,
                    span_id=second_server_id,
                    parent_span_id=second_client_id,
                    kind=SPAN_KIND_SERVER,
                    duration_ms=max(1, second_duration - 2),
                    status_code=second_status,
                    attributes={
                        "http.request.method": second_method,
                        "url.path": second_endpoint,
                        "http.response.status_code": 200,
                        "server.address": f"{second_callee}-host",
                        "server.port": 8080,
                    },
                )
                spans_by_service.setdefault(second_callee, []).append(second_server_span)

    return spans_by_service


# ── Run loop (used by ServiceManager and standalone) ──────────────────────────
def run(client: OTLPClient, stop_event: threading.Event, chaos_controller=None) -> None:
    """Run trace generator loop until stop_event is set."""
    rng = random.Random()
    resources = {svc: _build_resource(svc) for svc in SERVICES}
    total_traces = 0
    total_spans = 0

    logger.info("Trace generator started (chaos_aware=%s)", chaos_controller is not None)

    while not stop_event.is_set():
        # Build set of services affected by active chaos channels
        chaos_affected: set[str] = set()
        if chaos_controller:
            for ch_id in chaos_controller.get_active_channels():
                ch = CHANNEL_REGISTRY.get(ch_id)
                if ch:
                    chaos_affected.update(ch["affected_services"])

        num_traces = rng.randint(2, 5)

        batch_by_service: dict[str, list] = {}
        for _ in range(num_traces):
            trace_spans = _generate_trace(client, resources, rng, chaos_affected or None)
            for svc, spans in trace_spans.items():
                batch_by_service.setdefault(svc, []).extend(spans)

        batch_span_count = 0
        for svc, spans in batch_by_service.items():
            if spans:
                client.send_traces(resources[svc], spans)
                batch_span_count += len(spans)

        total_traces += num_traces
        total_spans += batch_span_count
        logger.info(
            "Sent %d traces (%d spans) — total: %d traces, %d spans",
            num_traces, batch_span_count, total_traces, total_spans,
        )

        sleep_time = rng.uniform(BATCH_INTERVAL_MIN, BATCH_INTERVAL_MAX)
        stop_event.wait(sleep_time)

    logger.info("Trace generator stopped. Total: %d traces, %d spans", total_traces, total_spans)


# ── Standalone entry point ────────────────────────────────────────────────────
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
    logger.info("Running for %ds (standalone mode)", duration)

    run(client, stop_event)
    timer.cancel()
    client.close()


if __name__ == "__main__":
    main()
