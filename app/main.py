"""
FastAPI application entry point.

Serves the frontend from /static/ and exposes the /api/* routes.
Background polling is started on app startup and stopped on shutdown.
"""

import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import database, scheduler
from .chargepoint.factory import get_provider
from .config import settings
from .models import PortStatus, StatusResponse, WatchRequest, WatchResponse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

_start_time = time.monotonic()
STATIC_DIR = Path(__file__).parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    database.init_db()
    logger.info("Database ready")
    try:
        provider = await get_provider(settings.chargepoint_username, settings.chargepoint_password)
        scheduler.start(provider)
    except Exception as e:
        logger.error("Failed to initialise ChargePoint provider: %s", e)
        logger.error(
            "The app will still start, but polling is disabled. "
            "Fix credentials in .env and restart."
        )

    yield

    # Shutdown
    scheduler.stop()


app = FastAPI(title="ChargePoint Monitor", lifespan=lifespan)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/", include_in_schema=False)
async def root():
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/api/status", response_model=StatusResponse)
async def get_status():
    return _build_status_response()


@app.post("/api/refresh", response_model=StatusResponse)
async def refresh():
    """Trigger an immediate ChargePoint poll and return the updated status."""
    await scheduler.poll_now()
    return _build_status_response()


@app.post("/api/watch", response_model=WatchResponse)
async def set_watch(body: WatchRequest):
    database.set_watch_active(body.enabled)
    watch = database.get_watch_state()
    return WatchResponse(active=watch.is_active, since=watch.activated_at)


@app.post("/api/watch/acknowledge", response_model=WatchResponse)
async def acknowledge_watch():
    """
    Stop watch mode. Called by the ntfy.sh notification action button.
    Idempotent — safe to call when already inactive.
    """
    database.set_watch_active(False)
    return WatchResponse(active=False, since=None)


@app.get("/api/health")
async def health():
    uptime = int(time.monotonic() - _start_time)
    return {"status": "ok", "uptime_seconds": uptime}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_status_response() -> StatusResponse:
    port_rows = database.get_all_ports()
    watch = database.get_watch_state()
    now = datetime.now(timezone.utc)

    # Derive a single poll error from the ports (any port having one is enough to surface)
    last_poll_error: Optional[str] = None
    for row in port_rows:
        if row.last_poll_error:
            last_poll_error = row.last_poll_error
            break

    ports = []
    for row in port_rows:
        status_since = database.parse_dt(row.status_since) or now
        last_polled_at = database.parse_dt(row.last_polled_at)
        duration_seconds = int((now - status_since).total_seconds())

        ports.append(
            PortStatus(
                station_id=row.station_id,
                port_number=row.port_number,
                is_available=row.is_available,
                since=status_since,
                duration_seconds=max(0, duration_seconds),
                status_source=row.status_source,
                last_polled_at=last_polled_at,
                last_poll_error=row.last_poll_error,
            )
        )

    return StatusResponse(
        station_ids=settings.chargepoint_station_ids,
        ports=ports,
        watch_mode_active=watch.is_active,
        watch_mode_since=watch.activated_at,
        last_poll_error=last_poll_error,
    )
