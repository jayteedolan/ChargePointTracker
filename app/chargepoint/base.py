from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class PortData:
    port_number: int        # 1-indexed
    is_available: bool
    status_source: str      # "per_port" | "aggregate"


@dataclass
class StationData:
    station_id: int
    ports: list[PortData]   # Ordered by port_number
    polled_at: datetime     # UTC


class ChargePointError(Exception):
    pass


class ChargePointAuthError(ChargePointError):
    pass


class ChargePointProvider(ABC):
    @abstractmethod
    async def authenticate(self) -> None:
        """Establish or refresh authentication."""
        ...

    @abstractmethod
    async def get_station_status(self, station_id: int) -> StationData:
        """
        Fetch current port availability for the given station.
        Raises ChargePointAuthError on auth failure.
        Raises ChargePointError on other API errors.
        """
        ...
