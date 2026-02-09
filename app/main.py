"""NOVA-7 Space Launch Mission Control — FastAPI entry point."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import APP_HOST, APP_PORT, MISSION_ID, MISSION_NAME

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
# Suppress noisy httpx request logging
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger("nova7")

# Late imports to avoid circular deps — populated at startup
service_manager = None
chaos_controller = None
dashboard_ws = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start all services and chaos controller on startup; stop on shutdown."""
    global service_manager, chaos_controller, dashboard_ws

    from app.chaos.controller import ChaosController
    from app.dashboard.websocket import DashboardWebSocket
    from app.services import manager

    chaos_controller = ChaosController()
    dashboard_ws = DashboardWebSocket()
    service_manager = manager.ServiceManager(chaos_controller, dashboard_ws)
    service_manager.start_all()
    logger.info("NOVA-7 Mission Control online — all services started")

    yield

    service_manager.stop_all()
    logger.info("NOVA-7 Mission Control shutdown complete")


app = FastAPI(
    title="NOVA-7 Launch Mission Control",
    version="1.0.0",
    lifespan=lifespan,
)

# ── Static file mounts ─────────────────────────────────────────────────────
_base = os.path.dirname(__file__)
app.mount(
    "/dashboard/static",
    StaticFiles(directory=os.path.join(_base, "dashboard", "static")),
    name="dashboard-static",
)
app.mount(
    "/chaos/static",
    StaticFiles(directory=os.path.join(_base, "chaos_ui", "static")),
    name="chaos-static",
)
app.mount(
    "/landing/static",
    StaticFiles(directory=os.path.join(_base, "landing", "static")),
    name="landing-static",
)

# ── Landing Page ───────────────────────────────────────────────────────────

_kibana_url = os.getenv("KIBANA_URL", "https://localhost:5601").rstrip("/")
_demo_url = os.getenv("DEMO_URL", "/").rstrip("/")


@app.get("/", response_class=HTMLResponse)
async def landing_page():
    path = os.path.join(_base, "landing", "static", "index.html")
    with open(path) as f:
        html = f.read().replace("KIBANA_URL_PLACEHOLDER", _kibana_url)
    return HTMLResponse(content=html)


@app.get("/slides", response_class=HTMLResponse)
async def slides_page():
    path = os.path.join(_base, "landing", "static", "slides.html")
    with open(path) as f:
        html = f.read().replace("DEMO_URL_PLACEHOLDER", _demo_url)
    return HTMLResponse(content=html)


# ── Health ──────────────────────────────────────────────────────────────────


@app.get("/health")
async def health():
    return {"status": "ok", "mission": MISSION_ID}


# ── Dashboard ───────────────────────────────────────────────────────────────


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page():
    path = os.path.join(_base, "dashboard", "static", "index.html")
    with open(path) as f:
        return HTMLResponse(content=f.read())


@app.websocket("/ws/dashboard")
async def ws_dashboard(websocket: WebSocket):
    await dashboard_ws.connect(websocket)
    try:
        while True:
            # Keep connection alive; client sends pings
            data = await websocket.receive_text()
    except WebSocketDisconnect:
        dashboard_ws.disconnect(websocket)


# ── Chaos Controller UI ────────────────────────────────────────────────────


@app.get("/chaos", response_class=HTMLResponse)
async def chaos_page():
    path = os.path.join(_base, "chaos_ui", "static", "index.html")
    with open(path) as f:
        return HTMLResponse(content=f.read())


# ── Chaos API ───────────────────────────────────────────────────────────────


@app.post("/api/chaos/trigger")
async def chaos_trigger(body: dict):
    channel = int(body.get("channel", 0))
    mode = body.get("mode", "calibration")
    se_name = body.get("se_name", "")
    callback_url = body.get("callback_url", "")
    user_email = body.get("user_email", "")
    result = chaos_controller.trigger(channel, mode, se_name, callback_url, user_email)
    if dashboard_ws:
        await dashboard_ws.broadcast_status(chaos_controller, service_manager)
    return result


@app.post("/api/chaos/resolve")
async def chaos_resolve(body: dict):
    channel = int(body.get("channel", 0))
    result = chaos_controller.resolve(channel)
    if dashboard_ws:
        await dashboard_ws.broadcast_status(chaos_controller, service_manager)
    return result


@app.get("/api/chaos/status")
async def chaos_status():
    return chaos_controller.get_status()


@app.get("/api/chaos/status/{channel}")
async def chaos_channel_status(channel: int):
    return chaos_controller.get_channel_status(channel)


# ── Status API ──────────────────────────────────────────────────────────────


@app.get("/api/status")
async def system_status():
    return {
        "mission_id": MISSION_ID,
        "mission_name": MISSION_NAME,
        "services": service_manager.get_all_status() if service_manager else {},
        "generators": service_manager.get_generator_status() if service_manager else {},
        "chaos": chaos_controller.get_status() if chaos_controller else {},
        "countdown": service_manager.get_countdown() if service_manager else {},
    }


# ── Countdown Control ──────────────────────────────────────────────────────


@app.post("/api/countdown/start")
async def countdown_start():
    service_manager.countdown_start()
    return {"status": "started"}


@app.post("/api/countdown/pause")
async def countdown_pause():
    service_manager.countdown_pause()
    return {"status": "paused"}


@app.post("/api/countdown/reset")
async def countdown_reset():
    service_manager.countdown_reset()
    return {"status": "reset"}


@app.post("/api/countdown/speed")
async def countdown_speed(body: dict):
    speed = float(body.get("speed", 1.0))
    service_manager.countdown_set_speed(speed)
    return {"status": "speed_set", "speed": speed}


# ── Remediation endpoint (called by Elastic Workflow) ──────────────────────


@app.post("/api/remediate/{channel}")
async def remediate_channel(channel: int):
    result = chaos_controller.resolve(channel)
    if dashboard_ws:
        await dashboard_ws.broadcast_status(chaos_controller, service_manager)
    return {"action": "remediated", "channel": channel, **result}


# ── User Info (for auto-populating email) ─────────────────────────────────


@app.get("/api/user/info")
async def user_info(request: Request):
    email = request.headers.get("X-Forwarded-User", "")
    return {"email": email}


# ── Email Notification endpoint (called by Elastic Workflow) ──────────────


@app.post("/api/notify/email")
async def notify_email(body: dict):
    from app.notify.email_handler import send_email

    to = body.get("to", "")
    subject = body.get("subject", "")
    message = body.get("body", "")
    result = await send_email(to, subject, message)
    return result


# ── Run ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=APP_HOST, port=APP_PORT, reload=False)
