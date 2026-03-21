from typing import Optional

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    chargepoint_username: str
    chargepoint_password: str
    chargepoint_station_ids: list[int] = []
    chargepoint_station_id: Optional[int] = None  # legacy — migrated to chargepoint_station_ids
    ntfy_topic: str
    ntfy_url: str = "https://ntfy.sh"
    app_port: int = 8080
    poll_interval_seconds: int = 120
    pi_host: str = "localhost"  # LAN IP or DDNS hostname; used in ntfy action button URLs

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )

    @model_validator(mode="after")
    def migrate_legacy_station_id(self) -> "Settings":
        if not self.chargepoint_station_ids and self.chargepoint_station_id is not None:
            self.chargepoint_station_ids = [self.chargepoint_station_id]
        return self


settings = Settings()  # type: ignore[call-arg]
