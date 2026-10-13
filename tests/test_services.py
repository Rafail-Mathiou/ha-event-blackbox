"""The admin-only, read-only action event_blackbox.explain."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

from homeassistant.core import Context, HomeAssistant, ServiceResponse
from homeassistant.exceptions import ServiceValidationError, Unauthorized
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, MockUser

from custom_components.event_blackbox.const import DOMAIN
from custom_components.event_blackbox.services import SERVICE_EXPLAIN

MOTION = "input_boolean.demo_motion"
HALL = "input_boolean.demo_hall"


def _options(**overrides: Any) -> dict[str, Any]:  # noqa: ANN401 - option values
    return {
        "collection_enabled": True,
        "entities": [MOTION, HALL],
        "domains": [],
        "max_events": 2000,
        "max_kib": 2048,
        "max_age_minutes": 30,
        **overrides,
    }


async def _setup(hass: HomeAssistant, options: dict[str, Any]) -> MockConfigEntry:
    assert await async_setup_component(
        hass, "input_boolean", {"input_boolean": {"demo_motion": {}, "demo_hall": {}}}
    )
    entry = MockConfigEntry(domain=DOMAIN, options=options)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _explain(
    hass: HomeAssistant, entity_id: str, user: MockUser | None = None
) -> ServiceResponse:
    return await hass.services.async_call(
        DOMAIN,
        SERVICE_EXPLAIN,
        {"entity_id": entity_id},
        blocking=True,
        return_response=True,
        context=Context(user_id=user.id) if user else None,
    )


@pytest.fixture
def nested_calls() -> Iterator[list[tuple[str, str]]]:
    """Record every service call while still executing it."""
    from homeassistant.core import ServiceRegistry  # noqa: PLC0415

    original = ServiceRegistry.async_call
    calls: list[tuple[str, str]] = []

    async def _spy(
        self: ServiceRegistry,
        domain: str,
        service: str,
        *args: Any,  # noqa: ANN401 - passed through unchanged
        **kwargs: Any,  # noqa: ANN401 - passed through unchanged
    ) -> ServiceResponse:
        calls.append((domain, service))
        return await original(self, domain, service, *args, **kwargs)

    with patch.object(ServiceRegistry, "async_call", _spy):
        yield calls


async def test_action_is_registered_and_admin_only(
    hass: HomeAssistant, hass_read_only_user: MockUser
) -> None:
    """Non-admin users are rejected."""
    await _setup(hass, _options())
    assert hass.services.has_service(DOMAIN, SERVICE_EXPLAIN)
    with pytest.raises(Unauthorized):
        await _explain(hass, HALL, hass_read_only_user)


async def test_admin_gets_a_confirmed_explanation(
    hass: HomeAssistant, hass_admin_user: MockUser
) -> None:
    """A direct change by a user is explained as a confirmed direct action."""
    await _setup(hass, _options())
    await hass.services.async_call(
        "input_boolean", "turn_on", {"entity_id": HALL}, blocking=True
    )
    await hass.async_block_till_done()

    report = await _explain(hass, HALL, hass_admin_user)
    assert report is not None
    assert report["status"] == "explained"
    assert report["evidence"] == "CONFIRMED_BY_CONTEXT"
    assert "DIRECT_ACTION" in report["notes"]  # type: ignore[operator]


async def test_explain_makes_no_service_calls_and_no_network(
    hass: HomeAssistant, hass_admin_user: MockUser, nested_calls: list[tuple[str, str]]
) -> None:
    """Explaining only reads: the only service call is the action itself."""
    await _setup(hass, _options())
    nested_calls.clear()
    with patch(
        "homeassistant.helpers.aiohttp_client.async_get_clientsession"
    ) as session:
        await _explain(hass, HALL, hass_admin_user)
    assert nested_calls == [(DOMAIN, SERVICE_EXPLAIN)]
    session.assert_not_called()


async def test_not_recording_is_a_clear_error(
    hass: HomeAssistant, hass_admin_user: MockUser
) -> None:
    """Without active recording the action explains why it cannot answer."""
    await _setup(hass, _options(collection_enabled=False))
    with pytest.raises(ServiceValidationError) as err:
        await _explain(hass, HALL, hass_admin_user)
    assert err.value.translation_key == "not_recording"


async def test_entity_outside_allowlist_is_refused(
    hass: HomeAssistant, hass_admin_user: MockUser
) -> None:
    """Only allowlisted entities can be explained."""
    await _setup(hass, _options())
    with pytest.raises(ServiceValidationError) as err:
        await _explain(hass, "light.demo_unlisted", hass_admin_user)
    assert err.value.translation_key == "not_on_allowlist"


async def test_without_config_entry_the_action_refuses(
    hass: HomeAssistant, hass_admin_user: MockUser
) -> None:
    """The action exists with the integration, but needs a loaded entry."""
    assert await async_setup_component(hass, DOMAIN, {})
    with pytest.raises(ServiceValidationError) as err:
        await _explain(hass, HALL, hass_admin_user)
    assert err.value.translation_key == "not_recording"


async def test_invalid_entity_id_is_rejected(
    hass: HomeAssistant, hass_admin_user: MockUser
) -> None:
    """The schema accepts only entity IDs."""
    import probatio  # noqa: PLC0415

    await _setup(hass, _options())
    with pytest.raises(probatio.Invalid):
        await _explain(hass, "not an entity", hass_admin_user)
