"""Abstract base class for all NOVA-7 simulated services."""

from __future__ import annotations

import logging
import random
import secrets
import threading
import time
from abc import ABC, abstractmethod
from typing import Any, Optional

from app.config import CHANNEL_REGISTRY, MISSION_ID, SERVICES
from app.telemetry import OTLPClient

logger = logging.getLogger("nova7.services")


class BaseService(ABC):
    """Base class providing telemetry emission, threading, and fault injection hooks."""

    # Subclasses MUST set this
    SERVICE_NAME: str = ""

    def __init__(self, chaos_controller, otlp_client: OTLPClient):
        self.chaos_controller = chaos_controller
        self.otlp = otlp_client
        self.service_cfg = SERVICES[self.SERVICE_NAME]
        self.resource = OTLPClient.build_resource(self.SERVICE_NAME, self.service_cfg)

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._phase = "PRE-LAUNCH"
        self._status = "NOMINAL"
        self._last_status_change = time.time()

    # ── Lifecycle ──────────────────────────────────────────────────────

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop, name=f"svc-{self.SERVICE_NAME}", daemon=True
        )
        self._thread.start()
        logger.info("Service %s started", self.SERVICE_NAME)

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        self._thread = None
        logger.info("Service %s stopped", self.SERVICE_NAME)

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.generate_telemetry()
            except Exception:
                logger.exception("Error in %s telemetry loop", self.SERVICE_NAME)
            interval = random.uniform(1.5, 3.0)
            self._stop_event.wait(interval)

    # ── Abstract ───────────────────────────────────────────────────────

    @abstractmethod
    def generate_telemetry(self) -> None:
        """Produce one cycle of logs/metrics/traces. Called every 1.5-3s."""
        ...

    # ── Fault injection hooks ──────────────────────────────────────────

    def is_channel_active(self, channel: int) -> bool:
        """Check if a specific chaos channel is currently active."""
        return self.chaos_controller.is_active(channel)

    def get_active_channels_for_service(self) -> list[int]:
        """Return list of active channels that affect this service."""
        active = []
        for ch_id, ch_def in CHANNEL_REGISTRY.items():
            if self.SERVICE_NAME in ch_def["affected_services"]:
                if self.is_channel_active(ch_id):
                    active.append(ch_id)
        return active

    def get_cascade_channels_for_service(self) -> list[int]:
        """Return list of active channels where this service is in the cascade."""
        active = []
        for ch_id, ch_def in CHANNEL_REGISTRY.items():
            if self.SERVICE_NAME in ch_def.get("cascade_services", []):
                if self.is_channel_active(ch_id):
                    active.append(ch_id)
        return active

    # ── Status ─────────────────────────────────────────────────────────

    def get_status(self) -> dict[str, Any]:
        active = self.get_active_channels_for_service()
        cascade = self.get_cascade_channels_for_service()
        if active:
            status = "CRITICAL"
        elif cascade:
            status = "WARNING"
        else:
            status = "NOMINAL"
        self._status = status
        return {
            "service": self.SERVICE_NAME,
            "subsystem": self.service_cfg["subsystem"],
            "cloud_provider": self.service_cfg["cloud_provider"],
            "cloud_region": self.service_cfg["cloud_region"],
            "status": status,
            "phase": self._phase,
            "active_faults": active,
            "cascade_faults": cascade,
        }

    def set_phase(self, phase: str) -> None:
        self._phase = phase

    # ── Telemetry helpers ──────────────────────────────────────────────

    def _base_log_attrs(self) -> dict[str, Any]:
        """Fields required on every log record."""
        return {
            "launch.mission_id": MISSION_ID,
            "launch.phase": self._phase,
            "system.subsystem": self.service_cfg["subsystem"],
            "system.status": self._status,
        }

    def emit_log(
        self,
        level: str,
        message: str,
        extra_attrs: dict[str, Any] | None = None,
        event_name: str | None = None,
    ) -> None:
        attrs = self._base_log_attrs()
        if extra_attrs:
            attrs.update(extra_attrs)
        record = self.otlp.build_log_record(
            severity=level, body=message, attributes=attrs, event_name=event_name,
        )
        self.otlp.send_logs(self.resource, [record])

    def emit_metric(
        self,
        name: str,
        value: float,
        unit: str = "",
        extra_attrs: dict[str, Any] | None = None,
    ) -> None:
        attrs = extra_attrs or {}
        metric = self.otlp.build_gauge(name, value, unit, attrs)
        self.otlp.send_metrics(self.resource, [metric])

    def emit_trace(
        self,
        span_name: str,
        duration_ms: int = 50,
        extra_attrs: dict[str, Any] | None = None,
        status_code: int = 1,
    ) -> None:
        trace_id = secrets.token_hex(16)
        span_id = secrets.token_hex(8)
        attrs = self._base_log_attrs()
        if extra_attrs:
            attrs.update(extra_attrs)
        span = self.otlp.build_span(
            name=span_name,
            trace_id=trace_id,
            span_id=span_id,
            duration_ms=duration_ms,
            attributes=attrs,
            status_code=status_code,
        )
        self.otlp.send_traces(self.resource, [span])

    @staticmethod
    def _safe_format(template: str, params: dict) -> str:
        """Format a template string, ignoring missing keys."""
        import string
        class SafeDict(dict):
            def __missing__(self, key):
                return f"{{{key}}}"
        return string.Formatter().vformat(template, (), SafeDict(params))

    def emit_fault_logs(self, channel: int) -> None:
        """Emit error logs matching the channel's exact error signature."""
        ch = CHANNEL_REGISTRY.get(channel)
        if not ch:
            return

        # Generate 2-4 error logs per cycle when channel is active
        for _ in range(random.randint(2, 4)):
            fault_params = self._generate_fault_params(channel)
            msg = self._safe_format(ch["error_message"], fault_params)
            stack = self._safe_format(ch["stack_trace"], fault_params)

            attrs = self._base_log_attrs()
            attrs.update(
                {
                    "error.type": ch["error_type"],
                    "sensor.type": ch["sensor_type"],
                    "vehicle_section": ch["vehicle_section"],
                    "chaos.channel": channel,
                    "chaos.fault_type": ch["name"],
                    "exception.type": ch["error_type"],
                    "exception.message": msg,
                    "exception.stacktrace": stack,
                    "system.status": "CRITICAL",
                }
            )
            # Inject callback URL and user email for workflow auto-remediation
            meta = self.chaos_controller.get_channel_metadata(channel)
            if meta.get("callback_url"):
                attrs["chaos.callback_url"] = meta["callback_url"]
            if meta.get("user_email"):
                attrs["chaos.user_email"] = meta["user_email"]

            # Set event_name with remediation metadata (indexed keyword field)
            ev_name = None
            if meta.get("callback_url") or meta.get("user_email"):
                import json as _json
                ev_name = _json.dumps({
                    "callback_url": meta.get("callback_url", ""),
                    "user_email": meta.get("user_email", ""),
                })
            self.emit_log("ERROR", msg, attrs, event_name=ev_name)

    def emit_cascade_logs(self, channel: int) -> None:
        """Emit warning logs for cascading effects (not matching the SE query)."""
        ch = CHANNEL_REGISTRY.get(channel)
        if not ch:
            return
        messages = [
            f"Degraded readings detected from {ch['subsystem']} subsystem — possible upstream fault",
            f"Anomalous data pattern from {ch['vehicle_section']} sensors, monitoring closely",
            f"Health check shows elevated error rate in {ch['subsystem']} dependency",
        ]
        attrs = self._base_log_attrs()
        attrs.update(
            {
                "cascade.source_channel": channel,
                "cascade.source_subsystem": ch["subsystem"],
                "system.status": "WARNING",
            }
        )
        self.emit_log("WARN", random.choice(messages), attrs)

    def _generate_fault_params(self, channel: int) -> dict[str, Any]:
        """Generate realistic random parameters for fault messages."""
        # Each channel gets contextually appropriate random values
        params: dict[str, Any] = {
            "deviation": round(random.uniform(3.0, 12.0), 1),
            "epoch": int(time.time()) - random.randint(100, 5000),
            "tank_id": random.choice(["LOX-1", "LOX-2", "RP1-1", "RP1-2"]),
            "pressure": round(random.uniform(180, 350), 1),
            "expected_min": 200,
            "expected_max": 310,
            "measured": round(random.uniform(2.0, 8.0), 2),
            "commanded": round(random.uniform(4.0, 6.0), 2),
            "delta": round(random.uniform(4.0, 15.0), 1),
            "num_satellites": random.randint(3, 8),
            "uncertainty": round(random.uniform(5.0, 50.0), 1),
            "drift_ms": round(random.uniform(5.0, 25.0), 1),
            "threshold_ms": 3.0,
            "axis": random.choice(["X", "Y", "Z"]),
            "error_arcsec": round(random.uniform(10.0, 45.0), 1),
            "limit_arcsec": 5.0,
            "snr_db": round(random.uniform(3.0, 8.0), 1),
            "min_snr_db": 12.0,
            "rf_channel": random.choice(["S1", "S2", "S3"]),
            "loss_pct": round(random.uniform(5.0, 25.0), 1),
            "threshold_pct": 2.0,
            "link_id": random.choice(["XB-PRIMARY", "XB-SECONDARY"]),
            "az_error": round(random.uniform(1.0, 5.0), 2),
            "el_error": round(random.uniform(0.5, 3.0), 2),
            "zone": random.choice(["A", "B", "C", "D"]),
            "temp": round(random.uniform(55.0, 85.0), 1),
            "safe_min": -10.0,
            "safe_max": 45.0,
            "amplitude": round(random.uniform(2.0, 8.0), 2),
            "frequency": round(random.uniform(20.0, 200.0), 1),
            "limit": 1.5,
            "source_cloud": random.choice(["aws", "gcp", "azure"]),
            "dest_cloud": random.choice(["aws", "gcp", "azure"]),
            "latency_ms": random.randint(500, 3000),
            "threshold_ms_relay": 200,
            "corrupted_count": random.randint(5, 50),
            "total_count": random.randint(100, 500),
            "route_id": random.choice(["AWS-GCP-01", "GCP-AZ-01", "AWS-AZ-01"]),
            "bus_id": random.choice(["PWR-A", "PWR-B", "PWR-C"]),
            "voltage": round(random.uniform(105, 135), 1),
            "nominal_v": 120.0,
            "deviation_pct": round(random.uniform(8.0, 20.0), 1),
            "station_id": random.choice(["WX-NORTH", "WX-SOUTH", "WX-EAST", "WX-WEST"]),
            "gap_seconds": random.randint(30, 180),
            "max_gap": 15,
            "system_id": random.choice(["HYD-A", "HYD-B"]),
            "min_pressure": 2800,
            "queue_depth": random.randint(500, 5000),
            "rate": round(random.uniform(1.0, 10.0), 1),
            "min_rate": 50.0,
            "sensor_id": f"SENS-{random.randint(1000, 9999)}",
            "actual_epoch": int(time.time()) - random.randint(86400, 604800),
            "expected_epoch": int(time.time()) - 3600,
            "unit_id": random.choice(["FTS-A", "FTS-B"]),
            "error_code": f"0x{random.randint(1, 255):02X}",
            "radar_id": random.choice(["RDR-1", "RDR-2", "RDR-3"]),
            "gap_ms": random.randint(500, 5000),
            "max_gap_ms": 250,
        }
        return params
