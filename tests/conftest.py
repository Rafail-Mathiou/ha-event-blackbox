"""Shared fixtures. All test data is synthetic (see docs/PRIVACY.md)."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import patch

from homeassistant.const import (
    EVENT_HOMEASSISTANT_CLOSE,
    EVENT_HOMEASSISTANT_FINAL_WRITE,
    EVENT_HOMEASSISTANT_START,
    EVENT_HOMEASSISTANT_STARTED,
    EVENT_HOMEASSISTANT_STOP,
)
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.event_blackbox.const import DOMAIN, NAME

# Obviously fake values that must never surface in diagnostics or logs. They use
# documentation-only IP ranges and the reserved .invalid TLD.
SYNTHETIC_SECRETS = {
    "token": "synthetic-token-0000-not-real",
    "password": "synthetic-password-not-real",
    "host": "192.0.2.10",
    "url": "https://hub.example.invalid/api",
    "entity_id": "light.demo_hall",
    "latitude": "0.0001",
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Allow Home Assistant to load the integration from custom_components."""


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return a config entry as created by the config flow."""
    return MockConfigEntry(domain=DOMAIN, title=NAME, data={}, options={})


@pytest.fixture
def service_call_spy() -> Iterator[list[tuple[str, str]]]:
    """Record every service call made through the service registry."""
    calls: list[tuple[str, str]] = []

    async def _record(
        _self: object, domain: str, service: str, *_: object, **__: object
    ) -> None:
        calls.append((domain, service))

    with patch("homeassistant.core.ServiceRegistry.async_call", _record):
        yield calls


# Lifecycle events of Home Assistant itself. Components the integration depends
# on (http, frontend) listen to them; Event Blackbox never does (only events.py
# subscribes, and only to the events of its allowlist).
CORE_LIFECYCLE_EVENTS = frozenset(
    {
        EVENT_HOMEASSISTANT_CLOSE,
        EVENT_HOMEASSISTANT_FINAL_WRITE,
        EVENT_HOMEASSISTANT_START,
        EVENT_HOMEASSISTANT_STARTED,
        EVENT_HOMEASSISTANT_STOP,
    }
)


def bus_listeners(hass: HomeAssistant) -> dict[str, int]:
    """Return bus listener counts without Home Assistant's lifecycle events."""
    return {
        str(event): count
        for event, count in hass.bus.async_listeners().items()
        if event not in CORE_LIFECYCLE_EVENTS
    }
