"""Create vectorless Engine instances from settings."""

from __future__ import annotations

from typing import TYPE_CHECKING

from vectorless_code.settings import UserSettings, load_user_settings

if TYPE_CHECKING:
    from vectorless import Engine


def create_engine(user_settings: UserSettings | None = None) -> Engine:
    """Create a vectorless Engine from settings.

    Raises ``RuntimeError`` if no API key is configured.
    """
    from vectorless import Engine as _Engine

    settings = user_settings or load_user_settings()

    if not settings.api_key:
        raise RuntimeError(
            "VECTORLESS_API_KEY is not set. "
            "Run `vcc init` or set the VECTORLESS_API_KEY environment variable."
        )

    return _Engine(
        api_key=settings.api_key,
        model=settings.model,
        endpoint=settings.endpoint,
    )
