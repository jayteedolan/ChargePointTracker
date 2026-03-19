"""
Background polling loop and notification trigger logic.

The scheduler runs as a single asyncio Task created at app startup.
It polls ChargePoint every POLL_INTERVAL_SECONDS and updates SQLite.
Notification logic runs after every successful poll.

External callers:
  start(provider)  — called from main.py startup
  stop()           — called from main.py shutdown
  poll_now()       — called by POST /api/refresh; runs a poll immediately
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from . import database, notifier
from .chargepoint.base import ChargePointAuthError, ChargePointError, ChargePointProvider
from .config import settings

logger = logging.getLogger(__name__)

_provider: Optional[ChargePointProvider] = None
_stop_event: asyncio.Event = asyncio.Event()
_task: Optional[asyncio.Task] = None


def start(provider: ChargePointProvider) -> None:
    global _provider, _stop_event, _task
    _provider = provider
    _stop_event = asyncio.Event()
    _task = asyncio.create_task(_poll_loop(), name="chargepoint-poller")
    logger.info("Scheduler started (interval=%ds)", settings.poll_interval_seconds)


def stop() -> None:
    _stop_event.set()
    if _task and not _task.done():
        _task.cancel()
    logger.info("Scheduler stopped")


async def poll_now() -> None:
    """Run an immediate poll. Called by POST /api/refresh."""
    await _do_poll()


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

async def _poll_loop() -> None:
    while not _stop_event.is_set():
        await _do_poll()
        # Wait for the poll interval, but wake up immediately if stop is requested
        try:
            await asyncio.wait_for(
                asyncio.shield(_stop_event.wait()),
                timeout=float(settings.poll_interval_seconds),
            )
        except asyncio.TimeoutError:
            pass  # Normal: timeout means it's time to poll again


async def _do_poll() -> None:
    if _provider is None:
        logger.warning("poll called but no provider is set")
        return

    logger.debug("Polling ChargePoint station %s", settings.chargepoint_station_id)
    try:
        station_data = await _provider.get_station_status(settings.chargepoint_station_id)
    except ChargePointAuthError:
        logger.warning("Auth error during poll — re-authenticating")
        try:
            await _provider.authenticate()
            station_data = await _provider.get_station_status(settings.chargepoint_station_id)
        except ChargePointError as e:
            _record_error(str(e))
            return
    except ChargePointError as e:
        _record_error(str(e))
        return
    except Exception as e:
        _record_error(f"Unexpected error: {e}")
        logger.exception("Unexpected error during poll")
        return

    # Persist results
    database.update_port_status(station_data)
    database.log_poll(success=True, payload=_station_data_to_dict(station_data))

    # Check whether we need to fire any notifications
    await _check_notifications()


def _record_error(msg: str) -> None:
    logger.error("Poll failed: %s", msg)
    database.set_poll_error(msg)
    database.log_poll(success=False, error=msg)


async def _check_notifications() -> None:
    watch = database.get_watch_state()
    if not watch.is_active or watch.activated_at is None:
        return

    now = datetime.now(timezone.utc)
    any_available = database.any_port_available()

    if any_available:
        # Send availability notification if we haven't already since watch was activated
        already_notified = (
            watch.last_notified_at is not None
            and watch.last_notified_at >= watch.activated_at
        )
        if not already_notified:
            available_ports = database.get_available_ports()
            from .chargepoint.base import PortData
            port_data = [
                PortData(
                    port_number=p.port_number,
                    is_available=True,
                    status_source=p.status_source,
                )
                for p in available_ports
            ]
            await notifier.send_available(port_data)
            database.set_last_notified(now)
            database.set_last_reminded(now)
    else:
        # No ports free — check if hourly reminder is due
        watch_duration_secs = (now - watch.activated_at).total_seconds()
        if watch_duration_secs < 3600:
            return  # Haven't been watching long enough for a reminder yet

        if watch.last_reminded_at is None:
            secs_since_reminder = float("inf")
        else:
            secs_since_reminder = (now - watch.last_reminded_at).total_seconds()

        if secs_since_reminder >= 3600:
            minutes = int(watch_duration_secs / 60)
            await notifier.send_reminder(minutes)
            database.set_last_reminded(now)


def _station_data_to_dict(station_data) -> dict:
    return {
        "station_id": station_data.station_id,
        "polled_at": station_data.polled_at.isoformat(),
        "ports": [
            {
                "port_number": p.port_number,
                "is_available": p.is_available,
                "status_source": p.status_source,
            }
            for p in station_data.ports
        ],
    }
