"""Shared types and HTTP helper functions for the scenario deployer."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


# ── Progress reporting ──────────────────────────────────────────────────────

@dataclass
class DeployStep:
    name: str
    status: str = "pending"      # pending | running | ok | failed | skipped
    detail: str = ""
    items_total: int = 0
    items_done: int = 0


@dataclass
class DeployProgress:
    steps: list[DeployStep] = field(default_factory=list)
    finished: bool = False
    error: str = ""
    otlp_endpoint: str = ""

    def to_dict(self) -> dict:
        return {
            "finished": self.finished,
            "error": self.error,
            "otlp_endpoint": self.otlp_endpoint,
            "steps": [
                {
                    "name": s.name,
                    "status": s.status,
                    "detail": s.detail,
                    "items_total": s.items_total,
                    "items_done": s.items_done,
                }
                for s in self.steps
            ],
        }


ProgressCallback = Callable[[DeployProgress], None]


# ── HTTP helpers ────────────────────────────────────────────────────────────

def _kibana_headers(api_key: str) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "kbn-xsrf": "true",
        "x-elastic-internal-origin": "kibana",
        "Authorization": f"ApiKey {api_key}",
    }


def _es_headers(api_key: str) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Authorization": f"ApiKey {api_key}",
    }
