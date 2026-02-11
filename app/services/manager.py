"""Service Manager — starts/stops all 9 simulated services, generators, and manages countdown."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Optional

from app.config import COUNTDOWN_SPEED, COUNTDOWN_START_SECONDS, SERVICES
from app.telemetry import OTLPClient

logger = logging.getLogger("nova7.manager")


class ServiceManager:
    """Manages all service instances, log generators, and the mission countdown clock."""

    def __init__(self, chaos_controller, dashboard_ws=None):
        self.chaos_controller = chaos_controller
        self.dashboard_ws = dashboard_ws
        self.otlp = OTLPClient()
        self.services: dict[str, Any] = {}

        # Countdown state
        self._countdown_total = COUNTDOWN_START_SECONDS
        self._countdown_remaining = float(COUNTDOWN_START_SECONDS)
        self._countdown_speed = COUNTDOWN_SPEED
        self._countdown_running = False
        self._countdown_thread: Optional[threading.Thread] = None
        self._countdown_lock = threading.Lock()
        self._stop_event = threading.Event()

        # Generator threads
        self._generator_threads: list[threading.Thread] = []

        self._init_services()

    def _init_services(self) -> None:
        """Lazy-import and instantiate all services."""
        from app.services.comms_array import CommsArrayService
        from app.services.fuel_system import FuelSystemService
        from app.services.ground_systems import GroundSystemsService
        from app.services.mission_control import MissionControlService
        from app.services.navigation import NavigationService
        from app.services.payload_monitor import PayloadMonitorService
        from app.services.range_safety import RangeSafetyService
        from app.services.sensor_validator import SensorValidatorService
        from app.services.telemetry_relay import TelemetryRelayService

        service_classes = [
            MissionControlService,
            FuelSystemService,
            GroundSystemsService,
            NavigationService,
            CommsArrayService,
            PayloadMonitorService,
            SensorValidatorService,
            TelemetryRelayService,
            RangeSafetyService,
        ]
        for cls in service_classes:
            svc = cls(self.chaos_controller, self.otlp)
            self.services[svc.SERVICE_NAME] = svc

    def start_all(self) -> None:
        for svc in self.services.values():
            svc.start()
        self._start_countdown_thread()
        self._start_generators()
        logger.info("All %d services + 7 generators started", len(self.services))

    def stop_all(self) -> None:
        self._stop_event.set()
        if self._countdown_thread and self._countdown_thread.is_alive():
            self._countdown_thread.join(timeout=3)
        for t in self._generator_threads:
            t.join(timeout=5)
        for svc in self.services.values():
            svc.stop()
        self.otlp.close()
        logger.info("All services and generators stopped")

    # ── Generators ────────────────────────────────────────────────────

    def _start_generators(self) -> None:
        """Start log/trace/metrics generators as daemon threads."""
        from log_generators.trace_generator import run as run_traces
        from log_generators.host_metrics_generator import run as run_metrics
        from log_generators.nginx_log_generator import run as run_nginx
        from log_generators.mysql_log_generator import run as run_mysql
        from log_generators.k8s_metrics_generator import run as run_k8s
        from log_generators.nginx_metrics_generator import run as run_nginx_metrics
        from log_generators.vpc_flow_generator import run as run_vpc

        generators = [
            ("gen-traces", run_traces, (self.otlp, self._stop_event, self.chaos_controller)),
            ("gen-host-metrics", run_metrics, (self.otlp, self._stop_event)),
            ("gen-nginx", run_nginx, (self.otlp, self._stop_event)),
            ("gen-mysql", run_mysql, (self.otlp, self._stop_event)),
            ("gen-k8s-metrics", run_k8s, (self.otlp, self._stop_event)),
            ("gen-nginx-metrics", run_nginx_metrics, (self.otlp, self._stop_event)),
            ("gen-vpc-flow", run_vpc, (self.otlp, self._stop_event)),
        ]
        for name, fn, args in generators:
            t = threading.Thread(
                target=fn, args=args,
                name=name, daemon=True,
            )
            t.start()
            self._generator_threads.append(t)
            logger.info("Started generator thread: %s", name)

    def get_generator_status(self) -> dict[str, str]:
        """Return status of each generator thread."""
        return {
            t.name: "running" if t.is_alive() else "stopped"
            for t in self._generator_threads
        }

    # ── Countdown ──────────────────────────────────────────────────────

    def _start_countdown_thread(self) -> None:
        self._countdown_thread = threading.Thread(
            target=self._countdown_loop, name="countdown", daemon=True
        )
        self._countdown_thread.start()

    def _countdown_loop(self) -> None:
        last_tick = time.time()
        while not self._stop_event.is_set():
            now = time.time()
            dt = now - last_tick
            last_tick = now

            with self._countdown_lock:
                if self._countdown_running and self._countdown_remaining > 0:
                    self._countdown_remaining -= dt * self._countdown_speed
                    if self._countdown_remaining < 0:
                        self._countdown_remaining = 0

                    # Phase transitions based on countdown
                    remaining = self._countdown_remaining
                    if remaining > 300:
                        phase = "PRE-LAUNCH"
                    elif remaining > 60:
                        phase = "COUNTDOWN"
                    elif remaining > 0:
                        phase = "FINAL-COUNTDOWN"
                    else:
                        phase = "LAUNCH"

                    for svc in self.services.values():
                        svc.set_phase(phase)

            self._stop_event.wait(0.5)

    def countdown_start(self) -> None:
        with self._countdown_lock:
            self._countdown_running = True

    def countdown_pause(self) -> None:
        with self._countdown_lock:
            self._countdown_running = False

    def countdown_reset(self) -> None:
        with self._countdown_lock:
            self._countdown_remaining = float(self._countdown_total)
            self._countdown_running = False

    def countdown_set_speed(self, speed: float) -> None:
        with self._countdown_lock:
            self._countdown_speed = max(0.1, min(100.0, speed))

    def get_countdown(self) -> dict[str, Any]:
        with self._countdown_lock:
            remaining = max(0.0, self._countdown_remaining)
            minutes = int(remaining // 60)
            seconds = int(remaining % 60)
            return {
                "remaining_seconds": round(remaining, 1),
                "display": f"T-{minutes:02d}:{seconds:02d}",
                "running": self._countdown_running,
                "speed": self._countdown_speed,
            }

    def get_all_status(self) -> dict[str, Any]:
        return {name: svc.get_status() for name, svc in self.services.items()}
