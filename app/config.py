"""Configuration — loads active scenario and exposes settings for backward compatibility."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# Repo-root .env (not only cwd). Systemd/Uvicorn often start with a cwd where a
# bare load_dotenv() would miss ./.env — breaks AUTO_DEPLOY_SCENARIOS etc.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")
load_dotenv()  # optional: cwd .env for local dev; does not override existing keys

# ── Environment Configuration ──────────────────────────────────────────────
OTLP_ENDPOINT = os.getenv("OTLP_ENDPOINT", "http://otel-collector:4318")
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

APP_PORT = int(os.getenv("APP_PORT", "80"))
APP_HOST = os.getenv("APP_HOST", "0.0.0.0")

# ── Locked demo credentials (server-side only; selector hides connection form) ─
# When DEMO_CREDENTIALS_LOCKED=1, /api/setup/launch and test-connection use ONLY
# these env vars — the browser never receives the API key.
CREDENTIALS_LOCKED = os.getenv("DEMO_CREDENTIALS_LOCKED", "").lower() in (
    "1",
    "true",
    "yes",
)
DEMO_KIBANA_URL = os.getenv("DEMO_KIBANA_URL", "").strip().rstrip("/")
DEMO_ELASTIC_API_KEY = os.getenv("DEMO_ELASTIC_API_KEY", "").strip()
DEMO_ELASTIC_URL = os.getenv("DEMO_ELASTIC_URL", "").strip().rstrip("/")
DEMO_OTLP_URL = os.getenv("DEMO_OTLP_URL", "").strip().rstrip("/")

# ── Auto-deploy on startup (systemd / uvicorn restart) ───────────────────────
# Comma-separated scenario_id values. Each runs a full Elastic deploy + telemetry
# instance, sequentially (avoids API races). Example: gcp,financial,banking
# Set to empty or "0" to disable.
_auto_raw = os.getenv("AUTO_DEPLOY_SCENARIOS", "").strip()
if _auto_raw.lower() in ("0", "false", "none", "off", "no"):
    AUTO_DEPLOY_SCENARIO_IDS: list[str] = []
else:
    AUTO_DEPLOY_SCENARIO_IDS = [x.strip() for x in _auto_raw.split(",") if x.strip()]

# ── Active Scenario ───────────────────────────────────────────────────────
ACTIVE_SCENARIO = os.getenv("ACTIVE_SCENARIO", "space")

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
