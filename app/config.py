from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    chargepoint_username: str
    chargepoint_password: str
    chargepoint_station_id: int
    ntfy_topic: str
    ntfy_url: str = "https://ntfy.sh"
    app_port: int = 8080
    poll_interval_seconds: int = 120
    pi_host: str = "localhost"  # LAN IP or DDNS hostname; used in ntfy action button URLs

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )


settings = Settings()  # type: ignore[call-arg]
