"""Diagnostics must contain only allowlisted aggregate values."""

from __future__ import annotations

import json

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.event_blackbox.const import (
    DATA_SCHEMA_VERSION,
    DOMAIN,
)

from .conftest import SYNTHETIC_SECRETS

ALLOWED_KEYS = {
    "integration_version",
    "config_entry_version",
    "data_schema_version",
    "option_count",
    "collection_enabled",
    "allowlist_entity_count",
    "allowlist_domain_count",
    "sensitive_domain_count",
    "collection_status",
    "history_days",
    "buffer",
}


async def test_diagnostics_are_allowlisted(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    mock_config_entry: MockConfigEntry,
) -> None:
    """The diagnostics platform is registered and returns only safe keys."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    result = await get_diagnostics_for_config_entry(
        hass, hass_client, mock_config_entry
    )

    assert set(result) == ALLOWED_KEYS
    assert result["data_schema_version"] == DATA_SCHEMA_VERSION
    assert result["option_count"] == 0
    assert result["collection_enabled"] is False
    assert result["allowlist_entity_count"] == 0
    assert result["collection_status"] == "OFF"
    assert result["buffer"] is None


async def test_diagnostics_count_allowlist_without_naming_it(
    hass: HomeAssistant, hass_client: ClientSessionGenerator
) -> None:
    """Allowlisted entity IDs and domains appear only as counts."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        options={
            "collection_enabled": True,
            "entities": ["light.demo_hall", "lock.demo_door"],
            "domains": ["camera", "binary_sensor"],
            "max_events": 2000,
            "max_kib": 2048,
            "max_age_minutes": 30,
        },
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await get_diagnostics_for_config_entry(hass, hass_client, entry)
    dumped = json.dumps(result)

    assert result["collection_enabled"] is True
    assert result["allowlist_entity_count"] == 2
    assert result["allowlist_domain_count"] == 2
    assert result["sensitive_domain_count"] == 2
    for value in ("demo_hall", "demo_door", "camera", "binary_sensor", "lock"):
        assert value not in dumped, value


async def test_diagnostics_never_contain_entry_data(
    hass: HomeAssistant, hass_client: ClientSessionGenerator
) -> None:
    """Secrets, hosts, URLs and entity IDs in entry data never leak."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="synthetic-title-not-real",
        data=SYNTHETIC_SECRETS,
        options=SYNTHETIC_SECRETS,
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await get_diagnostics_for_config_entry(hass, hass_client, entry)
    dumped = json.dumps(result)

    assert set(result) == ALLOWED_KEYS
    for key, value in SYNTHETIC_SECRETS.items():
        assert value not in dumped, key
    assert "synthetic-title-not-real" not in dumped
    assert entry.entry_id not in dumped
