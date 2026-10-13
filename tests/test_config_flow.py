"""Tests for the config and options flow."""

from __future__ import annotations

from typing import Any

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.event_blackbox.config_flow import (
    CONF_ACKNOWLEDGE_SENSITIVE,
    EventBlackboxOptionsFlow,
)
from custom_components.event_blackbox.const import (
    DOMAIN,
    NAME,
)
from custom_components.event_blackbox.options import (
    CONF_COLLECTION_ENABLED,
    CONF_DOMAINS,
    CONF_ENTITIES,
    CONF_MAX_AGE_MINUTES,
    CONF_MAX_EVENTS,
    CONF_MAX_KIB,
)


async def test_user_flow_creates_entry_without_data(hass: HomeAssistant) -> None:
    """The user step asks for nothing and stores nothing."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == NAME
    assert result["data"] == {}
    assert result["options"] == {}


async def test_user_flow_single_instance(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """A second setup is rejected."""
    mock_config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


@pytest.mark.parametrize(
    "user_input",
    [
        {"unexpected": "value"},
        {"collection_enabled": True},
        {"x" * 10_000: "y" * 1_000_000},
    ],
)
async def test_user_flow_rejects_unexpected_input(
    hass: HomeAssistant, user_input: dict[str, object]
) -> None:
    """Injected or oversized fields are rejected instead of being stored."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with pytest.raises(InvalidData):
        await hass.config_entries.flow.async_configure(result["flow_id"], user_input)
    assert hass.config_entries.async_entries(DOMAIN) == []


# --- options flow --------------------------------------------------

ENABLED_INPUT: dict[str, Any] = {
    CONF_COLLECTION_ENABLED: True,
    CONF_ENTITIES: ["light.demo_hall"],
    CONF_DOMAINS: ["binary_sensor"],
    CONF_MAX_EVENTS: 2000,
    CONF_MAX_KIB: 2048,
    CONF_MAX_AGE_MINUTES: 30,
    "history_days": 0,
}


async def _open_options(
    hass: HomeAssistant, entry: MockConfigEntry
) -> ConfigFlowResult:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return await hass.config_entries.options.async_init(entry.entry_id)


def _default(result: ConfigFlowResult, key: str) -> Any:  # noqa: ANN401 - schema default
    schema = result["data_schema"]
    assert schema is not None
    for marker in schema.schema:
        if str(marker) == key:
            return marker.default()
    raise AssertionError(key)


async def test_options_form_defaults_are_off(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """The form opens with opt-in off, nothing selected, default limits."""
    result = await _open_options(hass, mock_config_entry)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    assert _default(result, CONF_COLLECTION_ENABLED) is False
    assert _default(result, CONF_ENTITIES) == []
    assert _default(result, CONF_DOMAINS) == []
    assert _default(result, CONF_MAX_EVENTS) == 2000


async def test_options_flow_stores_validated_options(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """A valid, non-sensitive selection is stored directly and reloads."""
    result = await _open_options(hass, mock_config_entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**ENABLED_INPUT, CONF_MAX_EVENTS: 500.0}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert mock_config_entry.options == {**ENABLED_INPUT, CONF_MAX_EVENTS: 500}
    assert mock_config_entry.runtime_data.options.active is True
    assert mock_config_entry.runtime_data.recorder is not None


async def test_options_form_shows_current_values(hass: HomeAssistant) -> None:
    """Reopening the form starts from the stored options."""
    entry = MockConfigEntry(domain=DOMAIN, options=ENABLED_INPUT)
    result = await _open_options(hass, entry)
    assert _default(result, CONF_COLLECTION_ENABLED) is True
    assert _default(result, CONF_ENTITIES) == ["light.demo_hall"]


async def test_opt_in_requires_allowlist(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Opt-in without any selected source is rejected."""
    result = await _open_options(hass, mock_config_entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**ENABLED_INPUT, CONF_ENTITIES: [], CONF_DOMAINS: []}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "allowlist_required"}
    assert mock_config_entry.options == {}


async def test_too_many_entities_rejected(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """An oversized allowlist is rejected with a field error."""
    result = await _open_options(hass, mock_config_entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {**ENABLED_INPUT, CONF_ENTITIES: [f"light.demo_{i}" for i in range(201)]},
    )
    assert result["errors"] == {CONF_ENTITIES: "too_many_entities"}
    assert mock_config_entry.options == {}


@pytest.mark.parametrize(
    "selection",
    [
        {CONF_DOMAINS: ["camera"]},
        {CONF_ENTITIES: ["lock.demo_door"]},
        {CONF_ENTITIES: ["person.demo_resident"], CONF_DOMAINS: ["media_player"]},
    ],
)
async def test_sensitive_selection_needs_acknowledgement(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    selection: dict[str, list[str]],
) -> None:
    """Sensitive domains are only stored after an explicit extra warning."""
    result = await _open_options(hass, mock_config_entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**ENABLED_INPUT, **selection}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "sensitive"
    placeholders = result["description_placeholders"]
    assert placeholders is not None
    assert placeholders["domains"]
    assert mock_config_entry.options == {}

    # Declining keeps the warning up and stores nothing.
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_ACKNOWLEDGE_SENSITIVE: False}
    )
    assert result["step_id"] == "sensitive"
    assert result["errors"] == {"base": "acknowledgement_required"}
    assert mock_config_entry.options == {}

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_ACKNOWLEDGE_SENSITIVE: True}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert mock_config_entry.runtime_data.options.sensitive_domains


async def test_sensitive_warning_also_without_opt_in(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """A preselected sensitive domain is warned about even with opt-in off."""
    result = await _open_options(hass, mock_config_entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {**ENABLED_INPUT, CONF_COLLECTION_ENABLED: False, CONF_DOMAINS: ["person"]},
    )
    assert result["step_id"] == "sensitive"


@pytest.mark.parametrize(
    "user_input",
    [
        {**ENABLED_INPUT, "unexpected": "value"},
        {**ENABLED_INPUT, CONF_DOMAINS: ["unknown_domain"]},
        {**ENABLED_INPUT, CONF_MAX_EVENTS: 10**9},
        {**ENABLED_INPUT, CONF_ENTITIES: ["not an entity"]},
        {**ENABLED_INPUT, "x" * 10_000: "y" * 1_000_000},
    ],
)
async def test_options_flow_rejects_unexpected_input(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    user_input: dict[str, object],
) -> None:
    """Injected, unknown or out-of-range values are rejected by the schema."""
    result = await _open_options(hass, mock_config_entry)
    with pytest.raises(InvalidData):
        await hass.config_entries.options.async_configure(result["flow_id"], user_input)
    assert mock_config_entry.options == {}


async def test_sensitive_step_without_pending_selection_restarts(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Entering the warning step directly falls back to the form."""
    await _open_options(hass, mock_config_entry)
    flow = EventBlackboxOptionsFlow()
    flow.hass = hass
    flow.handler = mock_config_entry.entry_id
    result = await flow.async_step_sensitive()
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
