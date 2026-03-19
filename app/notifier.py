"""
ntfy.sh notification dispatch.
Sends push notifications to the user's subscribed topic.
Notifications include an HTTP action button to stop watching directly from the notification.
"""

import logging
from typing import Optional

import httpx

from .chargepoint.base import PortData
from .config import settings

logger = logging.getLogger(__name__)


def _acknowledge_url() -> str:
    return f"http://{settings.pi_host}:{settings.app_port}/api/watch/acknowledge"


async def send_available(ports: list[PortData]) -> None:
    """Send a push notification that one or more ports have become available."""
    if len(ports) == 0:
        return

    if len(ports) == 1:
        port = ports[0]
        if port.status_source == "per_port":
            message = f"Port {port.port_number} is available."
        else:
            message = "A charger port is available."
    else:
        message = "Both charger ports are available."

    message += " Tap 'Stop Watching' to cancel alerts."

    payload = {
        "topic": settings.ntfy_topic,
        "title": "EV Charger Available",
        "message": message,
        "priority": 4,
        "tags": ["electric_plug", "white_check_mark"],
        "actions": [
            {
                "action": "http",
                "label": "Stop Watching",
                "url": _acknowledge_url(),
                "method": "POST",
                "clear": True,
            }
        ],
    }
    await _post(payload)
    logger.info("Sent availability notification to topic '%s'", settings.ntfy_topic)


async def send_reminder(watch_duration_minutes: int) -> None:
    """Send an hourly reminder that watch is still active but no port is free."""
    hours = watch_duration_minutes // 60
    mins = watch_duration_minutes % 60
    if hours > 0:
        duration_str = f"{hours}h {mins}m" if mins else f"{hours}h"
    else:
        duration_str = f"{mins}m"

    payload = {
        "topic": settings.ntfy_topic,
        "title": "Still Waiting: EV Charger Occupied",
        "message": f"No ports available yet. You've been watching for {duration_str}.",
        "priority": 3,
        "tags": ["electric_plug", "hourglass_flowing_sand"],
        "actions": [
            {
                "action": "http",
                "label": "Stop Watching",
                "url": _acknowledge_url(),
                "method": "POST",
                "clear": True,
            }
        ],
    }
    await _post(payload)
    logger.info("Sent hourly reminder notification (watching for %s)", duration_str)


async def _post(payload: dict) -> None:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(settings.ntfy_url, json=payload)
            response.raise_for_status()
    except httpx.HTTPStatusError as e:
        logger.error("ntfy HTTP error %s: %s", e.response.status_code, e.response.text[:200])
    except Exception as e:
        logger.error("ntfy request failed: %s", e)
