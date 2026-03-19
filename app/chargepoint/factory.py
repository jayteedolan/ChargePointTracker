"""
Selects and returns a working ChargePointProvider at startup.

Order of preference:
  1. HttpProvider  — direct consumer API (most likely to work for shared stations)
  2. LibraryProvider — python-chargepoint library (unlikely to work, kept as fallback)

The factory runs a probe GET (authentication only, no status call) to verify credentials.
If HttpProvider auth succeeds, it is used for the lifetime of the process.
If it fails, LibraryProvider is tried. If both fail, a clear error is raised.
"""

import logging

from .base import ChargePointError, ChargePointProvider
from .http_provider import HttpProvider
from .library_provider import LibraryProvider

logger = logging.getLogger(__name__)


async def get_provider(username: str, password: str) -> ChargePointProvider:
    """
    Authenticate with ChargePoint and return a ready-to-use provider.
    Raises ChargePointError if all providers fail.
    """
    providers: list[tuple[str, ChargePointProvider]] = [
        ("HttpProvider", HttpProvider(username, password)),
        ("LibraryProvider", LibraryProvider(username, password)),
    ]

    last_error: Exception | None = None
    for name, provider in providers:
        try:
            logger.info("Trying %s...", name)
            await provider.authenticate()
            logger.info("%s authenticated successfully — using as active provider", name)
            return provider
        except ChargePointError as e:
            logger.warning("%s failed: %s", name, e)
            last_error = e

    raise ChargePointError(
        f"All ChargePoint providers failed. Last error: {last_error}. "
        "Check CHARGEPOINT_USERNAME and CHARGEPOINT_PASSWORD in your .env file."
    )
