"""Thread-safe shared context store for log-trace correlation.

The trace generator writes (trace_id, span_id) per service after each trace batch.
Service log emitters read the latest context to correlate their logs with active traces.
Always returns the most recent trace context — no TTL expiry.
"""

from __future__ import annotations

import threading


class TraceContextStore:
    """Maps service_name -> (trace_id, span_id). Always returns the last known context."""

    def __init__(self):
        self._store: dict[str, tuple[str, str]] = {}
        self._lock = threading.Lock()

    def set(self, service_name: str, trace_id: str, span_id: str) -> None:
        with self._lock:
            self._store[service_name] = (trace_id, span_id)

    def get(self, service_name: str) -> tuple[str | None, str | None]:
        with self._lock:
            entry = self._store.get(service_name)
            if entry is None:
                return None, None
            return entry


# Module-level singleton — imported by trace_generator and base_service
_trace_context_store = TraceContextStore()
