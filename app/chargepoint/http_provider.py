"""
Primary ChargePoint provider using the consumer REST API (same as the mobile app).
This is an unofficial/reverse-engineered API — it may break if ChargePoint changes
their backend. Raw API responses are logged to poll_log.raw_payload for debugging.

Auth endpoint: POST https://na.chargepoint.com/users/validate
Station detail: POST https://na.chargepoint.com/index.php/maps/getMarkerDetails
Map fallback:   GET  https://mc.chargepoint.com/map-prod/get?{json}
"""

import json
import logging
import re
from datetime import datetime, timezone

import httpx

from .base import ChargePointAuthError, ChargePointError, ChargePointProvider, PortData, StationData

logger = logging.getLogger(__name__)

AUTH_URL = "https://na.chargepoint.com/users/validate"
MARKER_URL = "https://na.chargepoint.com/index.php/maps/getMarkerDetails"
MAP_URL = "https://mc.chargepoint.com/map-prod/get"

# Mimic a browser/app request so ChargePoint doesn't block us
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 13) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Mobile Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.chargepoint.com",
    "Referer": "https://www.chargepoint.com/",
}


class HttpProvider(ChargePointProvider):
    def __init__(self, username: str, password: str) -> None:
        self._username = username
        self._password = password
        self._client = httpx.AsyncClient(headers=_HEADERS, follow_redirects=True, timeout=20.0)
        self._user_id: int | None = None
        self._authenticated = False

    async def authenticate(self) -> None:
        try:
            response = await self._client.post(
                AUTH_URL,
                data={"user_name": self._username, "user_password": self._password},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as e:
            raise ChargePointAuthError(f"Auth HTTP error: {e.response.status_code}") from e
        except Exception as e:
            raise ChargePointAuthError(f"Auth failed: {e}") from e

        # ChargePoint returns a JSON object; shape varies but commonly has 'auth' and 'user_id'/'userid'
        auth_token = data.get("auth") or data.get("token")
        user_id = data.get("user_id") or data.get("userid")

        if not auth_token:
            # Some versions return a different shape — log the raw response for debugging
            logger.error("Unexpected auth response shape: %s", json.dumps(data)[:500])
            raise ChargePointAuthError("Auth response missing token field")

        # Set session cookie that ChargePoint expects on subsequent requests
        self._client.cookies.set("coulomb_sess", str(auth_token), domain="na.chargepoint.com")
        self._user_id = int(user_id) if user_id else None
        self._authenticated = True
        logger.info("ChargePoint authentication successful (user_id=%s)", self._user_id)

    async def get_station_status(self, station_id: int) -> StationData:
        if not self._authenticated:
            await self.authenticate()

        # Try per-port detail endpoint first; fall back to aggregate map endpoint
        try:
            return await self._get_marker_details(station_id)
        except ChargePointAuthError:
            raise
        except ChargePointError as e:
            logger.warning("getMarkerDetails failed (%s), trying map endpoint", e)
            return await self._get_map_status(station_id)

    async def _get_marker_details(self, station_id: int) -> StationData:
        """
        POST to getMarkerDetails — ideally returns per-port availability strings.
        The response is an HTML fragment or JSON with a 'port_list' or similar structure.
        We log the raw payload so you can inspect it during initial setup.
        """
        try:
            response = await self._client.post(
                MARKER_URL,
                data={
                    "deviceId": str(station_id),
                    "level1": "1",
                    "level2": "1",
                    "levelDC": "1",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        except httpx.RequestError as e:
            raise ChargePointError(f"Network error on getMarkerDetails: {e}") from e

        if response.status_code in (401, 403):
            self._authenticated = False
            raise ChargePointAuthError("Session expired (getMarkerDetails returned 401/403)")

        if not response.is_success:
            raise ChargePointError(f"getMarkerDetails HTTP {response.status_code}")

        raw = response.text
        polled_at = datetime.now(timezone.utc)

        # Attempt JSON parse; ChargePoint may return JSON or HTML depending on version
        try:
            data = response.json()
            ports = self._parse_marker_json(data, station_id)
            if ports:
                logger.debug("getMarkerDetails returned per-port data for station %s", station_id)
                return StationData(
                    station_id=station_id,
                    ports=ports,
                    polled_at=polled_at,
                )
        except (ValueError, KeyError) as e:
            logger.debug("getMarkerDetails JSON parse failed: %s", e)

        # If JSON didn't work or had no ports, try parsing as HTML text
        ports = self._parse_marker_html(raw, station_id)
        if ports:
            return StationData(station_id=station_id, ports=ports, polled_at=polled_at)

        # Could not extract port data from this endpoint — fall through to map endpoint
        logger.warning(
            "getMarkerDetails returned unrecognised format for station %s. "
            "Raw (first 500 chars): %s",
            station_id,
            raw[:500],
        )
        raise ChargePointError("getMarkerDetails: could not parse port status from response")

    def _parse_marker_json(self, data: dict, station_id: int) -> list[PortData]:
        """
        Attempt to extract per-port status from the JSON response.
        ChargePoint's response shape is undocumented and may vary.
        Returns an empty list if the expected fields aren't found.
        """
        ports: list[PortData] = []

        # Common shape: { "port_list": [ { "port_number": 1, "status": "AVAILABLE" }, ... ] }
        port_list = data.get("port_list") or data.get("ports") or []
        for i, port in enumerate(port_list, start=1):
            port_num = port.get("port_number") or port.get("portNumber") or i
            status_str = (port.get("status") or port.get("portStatus") or "").upper()
            if status_str in ("AVAILABLE", "FREE", "OPEN"):
                is_available = True
            elif status_str in ("IN_USE", "INUSE", "CHARGING", "OCCUPIED", "CONNECTED"):
                is_available = False
            else:
                continue  # Unknown status — skip this port
            ports.append(PortData(port_number=int(port_num), is_available=is_available, status_source="per_port"))

        return ports

    def _parse_marker_html(self, html: str, station_id: int) -> list[PortData]:
        """
        Fallback: extract per-port status from the HTML response.

        ChargePoint embeds port status in a pattern like:
          <strong>Port 1: </strong>...<i>Occupied</i>...<strong>Port 2: </strong>...<i>Available</i>

        Multiple ports often appear on a single line, so we use regex to find all
        Port N / status pairs rather than iterating line-by-line.
        """
        ports: list[PortData] = []

        # Primary: match "Port N: ... <i>STATUS</i>" pairs anywhere in the HTML
        matches = re.findall(
            r'Port\s+(\d+)\s*:.*?<i>(.*?)</i>',
            html,
            re.IGNORECASE | re.DOTALL,
        )
        for port_num_str, status_str in matches:
            status_upper = status_str.strip().upper()
            if status_upper in ("AVAILABLE", "FREE", "OPEN"):
                is_available = True
            elif status_upper in ("OCCUPIED", "IN USE", "IN_USE", "CHARGING", "CONNECTED"):
                is_available = False
            else:
                logger.debug("Port %s: unrecognised status %r — skipping", port_num_str, status_str)
                continue
            ports.append(PortData(
                port_number=int(port_num_str),
                is_available=is_available,
                status_source="per_port",
            ))

        if ports:
            return ports

        # Fallback: line-by-line scan for any availability keyword (no port number info)
        port_num = 0
        for line in html.split("\n"):
            line_lower = line.lower()
            if "available" in line_lower and "unavailable" not in line_lower:
                port_num += 1
                ports.append(PortData(port_number=port_num, is_available=True, status_source="per_port"))
            elif "in use" in line_lower or "charging" in line_lower or "occupied" in line_lower:
                port_num += 1
                ports.append(PortData(port_number=port_num, is_available=False, status_source="per_port"))
        return ports

    async def _get_map_status(self, station_id: int) -> StationData:
        """
        Fallback: GET from the mapcache endpoint which returns aggregate port counts.
        Returns StationData with status_source="aggregate" on each PortData.
        """
        # The map endpoint expects a JSON payload encoded as a URL query param
        query_payload = json.dumps({"station_id": station_id, "user_id": self._user_id or 0})
        try:
            response = await self._client.get(
                MAP_URL,
                params={"json": query_payload},
            )
        except httpx.RequestError as e:
            raise ChargePointError(f"Network error on map endpoint: {e}") from e

        if response.status_code in (401, 403):
            self._authenticated = False
            raise ChargePointAuthError("Session expired (map endpoint returned 401/403)")

        if not response.is_success:
            raise ChargePointError(f"Map endpoint HTTP {response.status_code}")

        polled_at = datetime.now(timezone.utc)

        try:
            data = response.json()
        except ValueError as e:
            raise ChargePointError(f"Map endpoint returned non-JSON: {e}") from e

        return self._parse_map_response(data, station_id, polled_at)

    def _parse_map_response(self, data: dict, station_id: int, polled_at: datetime) -> StationData:
        """
        Parse the mapcache response into a StationData.
        Expected shape: { station_list: { summaries: [{ port_count: { available: N, total: N } }] } }
        Falls back to searching for the station_id in the response if the structure differs.
        """
        # Try standard shape
        try:
            summaries = data["station_list"]["summaries"]
            for summary in summaries:
                pc = summary.get("port_count", {})
                available_count = int(pc.get("available", 0))
                total_count = int(pc.get("total", 2))
                return self._make_aggregate_station_data(station_id, available_count, total_count, polled_at)
        except (KeyError, TypeError, ValueError):
            pass

        # Fallback: look for any dict in the response that has port_count
        def _search(obj: object) -> dict | None:
            if isinstance(obj, dict):
                if "port_count" in obj:
                    return obj
                for v in obj.values():
                    result = _search(v)
                    if result:
                        return result
            elif isinstance(obj, list):
                for item in obj:
                    result = _search(item)
                    if result:
                        return result
            return None

        found = _search(data)
        if found:
            pc = found["port_count"]
            available_count = int(pc.get("available", 0))
            total_count = int(pc.get("total", 2))
            return self._make_aggregate_station_data(station_id, available_count, total_count, polled_at)

        logger.error("Cannot parse map response for station %s. Data: %s", station_id, str(data)[:500])
        raise ChargePointError("Map endpoint: could not extract port availability from response")

    def _make_aggregate_station_data(
        self,
        station_id: int,
        available_count: int,
        total_count: int,
        polled_at: datetime,
    ) -> StationData:
        """
        Convert aggregate counts into PortData entries with status_source='aggregate'.
        When available_count is ambiguous (e.g., 1 of 2), both ports are created but
        we can't identify which specific port is free.
        """
        ports: list[PortData] = []
        for i in range(1, total_count + 1):
            is_available = i <= available_count
            ports.append(PortData(port_number=i, is_available=is_available, status_source="aggregate"))
        logger.info(
            "Station %s (aggregate): %d/%d ports available", station_id, available_count, total_count
        )
        return StationData(station_id=station_id, ports=ports, polled_at=polled_at)

    async def aclose(self) -> None:
        await self._client.aclose()
