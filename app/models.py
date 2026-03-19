from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class PortStatus(BaseModel):
    port_number: int
    is_available: bool
    since: datetime                 # UTC: when this status last changed
    duration_seconds: int           # Computed server-side: seconds in current state
    status_source: str              # "per_port" | "aggregate"
    last_polled_at: Optional[datetime] = None
    last_poll_error: Optional[str] = None


class StatusResponse(BaseModel):
    station_id: int
    ports: list[PortStatus]
    watch_mode_active: bool
    watch_mode_since: Optional[datetime] = None
    last_poll_error: Optional[str] = None


class WatchRequest(BaseModel):
    enabled: bool


class WatchResponse(BaseModel):
    active: bool
    since: Optional[datetime] = None
