"""
Secondary ChargePoint provider using the python-chargepoint library.
This library is designed for ChargePoint Home chargers and does NOT officially
support public/shared stations. This provider attempts to use the library's
authenticated session to reach the mapcache endpoint for station status.

In practice, this provider is unlikely to work for apartment/public stations.
The HttpProvider is the primary implementation.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone

from .base import ChargePointAuthError, ChargePointError, ChargePointProvider, PortData, StationData

logger = logging.getLogger(__name__)

MAP_URL = "https://mc.chargepoint.com/map-prod/get"


class LibraryProvider(ChargePointProvider):
    def __init__(self, username: str, password: str) -> None:
        self._username = username
        self._password = password
        self._client = None  # python_chargepoint.ChargePoint instance

    async def authenticate(self) -> None:
        try:
            from python_chargepoint import ChargePoint  # type: ignore[import]
        except ImportError as e:
            raise ChargePointError("python-chargepoint is not installed") from e

        loop = asyncio.get_event_loop()
        try:
            # python-chargepoint is synchronous; run in executor to avoid blocking
            self._client = await loop.run_in_executor(
                None, lambda: ChargePoint(self._username, self._password)
            )
            logger.info("python-chargepoint library authenticated successfully")
        except Exception as e:
            raise ChargePointAuthError(f"Library auth failed: {e}") from e

    async def get_station_status(self, station_id: int) -> StationData:
        if self._client is None:
            await self.authenticate()

        # Borrow the underlying requests.Session to reach the mapcache endpoint
        session = getattr(self._client, "session", None) or getattr(self._client, "_session", None)
        if session is None:
            raise ChargePointError("Cannot access underlying session from python-chargepoint client")

        query_payload = json.dumps({"station_id": station_id, "user_id": 0})
        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: session.get(MAP_URL, params={"json": query_payload}, timeout=20),
            )
        except Exception as e:
            raise ChargePointError(f"Library provider map request failed: {e}") from e

        if response.status_code in (401, 403):
            self._client = None
            raise ChargePointAuthError("Session expired (library provider)")

        if not response.ok:
            raise ChargePointError(f"Library provider map endpoint HTTP {response.status_code}")

        polled_at = datetime.now(timezone.utc)
        try:
            data = response.json()
        except ValueError as e:
            raise ChargePointError(f"Library provider: non-JSON response: {e}") from e

        # Parse aggregate counts (same logic as HttpProvider._parse_map_response)
        try:
            summaries = data["station_list"]["summaries"]
            pc = summaries[0]["port_count"]
            available_count = int(pc.get("available", 0))
            total_count = int(pc.get("total", 2))
        except (KeyError, IndexError, TypeError, ValueError) as e:
            raise ChargePointError(f"Library provider: unexpected map response shape: {e}") from e

        ports = [
            PortData(
                port_number=i,
                is_available=i <= available_count,
                status_source="aggregate",
            )
            for i in range(1, total_count + 1)
        ]
        return StationData(station_id=station_id, ports=ports, polled_at=polled_at)
