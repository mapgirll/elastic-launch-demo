"""Configuration — loads active scenario and exposes settings for backward compatibility."""

from __future__ import annotations

import os
from typing import Any

from dotenv import load_dotenv

load_dotenv()

# ── Environment Configuration ──────────────────────────────────────────────
OTLP_ENDPOINT = os.getenv("OTLP_ENDPOINT", "")
OTLP_API_KEY = os.getenv("OTLP_API_KEY", "")
OTLP_AUTH_TYPE = os.getenv("OTLP_AUTH_TYPE", "ApiKey")  # "ApiKey" or "Bearer"

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER", "")
TWILIO_TO_NUMBER = os.getenv("TWILIO_TO_NUMBER", "")

SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")

SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", "nova7-ops@mission-control.local")

APP_PORT = int(os.getenv("APP_PORT", "8080"))
APP_HOST = os.getenv("APP_HOST", "0.0.0.0")

# ── Active Scenario ───────────────────────────────────────────────────────
ACTIVE_SCENARIO = os.getenv("ACTIVE_SCENARIO", "space")
# True only when ACTIVE_SCENARIO is explicitly provided via the environment.
ACTIVE_SCENARIO_SET = bool(os.getenv("ACTIVE_SCENARIO"))

# ── Auto-deploy Credentials (optional) ───────────────────────────────────────
# If set, the app will automatically deploy ACTIVE_SCENARIO on startup
# without requiring manual input via the UI.
KIBANA_URL = os.getenv("KIBANA_URL", "")
_kibana_proxy_raw = os.getenv("KIBANA_PROXY", "").strip().rstrip("/")
if _kibana_proxy_raw and not _kibana_proxy_raw.startswith(("http://", "https://")):
    _kibana_proxy_raw = "https://" + _kibana_proxy_raw
KIBANA_PROXY = _kibana_proxy_raw
ELASTIC_API_KEY = os.getenv("ELASTIC_API_KEY", "")
ELASTIC_URL = os.getenv("ELASTIC_URL", "")

from scenarios import get_scenario  # noqa: E402

_scenario = get_scenario(ACTIVE_SCENARIO)

# ── Scenario-derived Configuration ────────────────────────────────────────
# These module-level variables ensure all existing imports continue working:
#   from app.config import SERVICES, CHANNEL_REGISTRY, MISSION_ID, etc.

NAMESPACE = _scenario.namespace
SERVICES: dict[str, dict[str, Any]] = _scenario.services
CHANNEL_REGISTRY: dict[int, dict[str, Any]] = _scenario.channel_registry

# Mission/scenario identity
MISSION_ID = _scenario.namespace.upper()  # "NOVA7", "FANATICS", etc.
MISSION_NAME = _scenario.scenario_name

# Countdown (from scenario or defaults)
_countdown = _scenario.countdown_config
COUNTDOWN_START_SECONDS = _countdown.start_seconds if _countdown.enabled else 600
COUNTDOWN_SPEED = _countdown.speed if _countdown.enabled else 1.0
COUNTDOWN_ENABLED = _countdown.enabled

# Severity Number Mapping (shared across all scenarios)
SEVERITY_MAP = {
    "TRACE": 1,
    "DEBUG": 5,
    "INFO": 9,
    "WARN": 13,
    "ERROR": 17,
    "FATAL": 21,
}
