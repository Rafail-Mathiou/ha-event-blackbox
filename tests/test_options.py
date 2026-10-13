"""Pure option parsing and validation (no Home Assistant runtime needed)."""

from __future__ import annotations

from typing import Any

import pytest

from custom_components.event_blackbox.options import (
    CONF_COLLECTION_ENABLED,
    CONF_DOMAINS,
    CONF_ENTITIES,
    CONF_HISTORY_DAYS,
    CONF_MAX_AGE_MINUTES,
    CONF_MAX_EVENTS,
    CONF_MAX_KIB,
    LIMITS,
    MAX_ALLOWLIST_ENTITIES,
    SELECTABLE_DOMAINS,
    SENSITIVE_DOMAINS,
    BlackboxOptions,
    parse_options,
    validate_user_input,
)

from .conftest import SYNTHETIC_SECRETS

VALID: dict[str, Any] = {
    CONF_COLLECTION_ENABLED: True,
    CONF_ENTITIES: ["light.demo_hall"],
    CONF_DOMAINS: ["binary_sensor"],
    CONF_MAX_EVENTS: 2000,
    CONF_MAX_KIB: 2048,
    CONF_MAX_AGE_MINUTES: 30,
    CONF_HISTORY_DAYS: 0,
}


def test_defaults_are_safe() -> None:
    """Nothing is enabled or selected by default; limits match the defaults."""
    options = BlackboxOptions()
    assert options.collection_enabled is False
    assert options.entities == ()
    assert options.domains == ()
    assert options.active is False
    assert (options.max_events, options.max_kib, options.max_age_minutes) == (
        2000,
        2048,
        30,
    )
    assert options.history_days == 0
    assert options.history is False


def test_sensitive_domains_are_selectable_only_explicitly() -> None:
    """All sensitive domains exist and none is preselected."""
    assert {
        "person",
        "device_tracker",
        "camera",
        "alarm_control_panel",
        "lock",
        "media_player",
    } == SENSITIVE_DOMAINS
    assert set(SELECTABLE_DOMAINS) >= SENSITIVE_DOMAINS
    assert not set(BlackboxOptions().domains) & SENSITIVE_DOMAINS


def test_round_trip() -> None:
    """Stored options parse back to the same values."""
    options = parse_options(VALID)
    assert options.as_dict() == VALID
    assert options.active is True


@pytest.mark.parametrize(
    "raw",
    [
        {},
        SYNTHETIC_SECRETS,
        {**VALID, CONF_COLLECTION_ENABLED: "yes"},
        {**VALID, CONF_COLLECTION_ENABLED: 1},
        {**VALID, CONF_ENTITIES: "light.demo_hall"},
        {**VALID, CONF_ENTITIES: ["light.demo_hall", 7]},
        {**VALID, CONF_ENTITIES: ["not an entity"]},
        {**VALID, CONF_ENTITIES: ["light.demo_" + "x" * 300]},
        {**VALID, CONF_ENTITIES: [f"light.demo_{i}" for i in range(201)]},
        {**VALID, CONF_DOMAINS: ["unknown_domain"]},
        {**VALID, CONF_MAX_EVENTS: 10**9},
        {**VALID, CONF_MAX_EVENTS: True},
        {**VALID, CONF_MAX_KIB: -1},
        {**VALID, CONF_MAX_AGE_MINUTES: 1.5},
    ],
)
def test_invalid_stored_options_fail_safe(raw: dict[str, Any]) -> None:
    """Manipulated or corrupt stored options never enable collection."""
    options = parse_options(raw)
    assert options == BlackboxOptions()
    assert options.active is False


def test_unknown_keys_are_dropped() -> None:
    """Extra keys are ignored and never carried along."""
    options = parse_options({**VALID, "token": "synthetic-token-0000-not-real"})
    assert "token" not in options.as_dict()
    assert options.active is True


def test_opt_in_without_allowlist_is_inactive() -> None:
    """Opt-in alone never means 'observe everything': it falls back to off."""
    options = parse_options({**VALID, CONF_ENTITIES: [], CONF_DOMAINS: []})
    assert options == BlackboxOptions()
    assert options.active is False
    # Constructed directly (not via parsing), the property still refuses.
    assert BlackboxOptions(collection_enabled=True).active is False


def test_allowlist_without_opt_in_is_inactive() -> None:
    """Selected sources without opt-in stay inactive."""
    assert parse_options({**VALID, CONF_COLLECTION_ENABLED: False}).active is False


def test_sensitive_selection_from_entities_and_domains() -> None:
    """Sensitive domains are detected in both entity and domain selections."""
    options = parse_options(
        {**VALID, CONF_ENTITIES: ["lock.demo_door"], CONF_DOMAINS: ["camera"]}
    )
    assert options.sensitive_domains == ("camera", "lock")
    assert parse_options(VALID).sensitive_domains == ()


def test_validate_user_input_coerces_numbers_and_dedupes() -> None:
    """Number selectors return floats; duplicates are removed, order is stable."""
    options, errors = validate_user_input(
        {
            **VALID,
            CONF_ENTITIES: ["light.demo_hall", "light.demo_hall", "light.demo_desk"],
            CONF_MAX_EVENTS: 500.0,
        }
    )
    assert errors == {}
    assert options is not None
    assert options.entities == ("light.demo_desk", "light.demo_hall")
    assert options.max_events == 500


@pytest.mark.parametrize(
    ("change", "error_key", "error"),
    [
        (
            {CONF_ENTITIES: [], CONF_DOMAINS: []},
            "base",
            "allowlist_required",
        ),
        (
            {CONF_ENTITIES: [f"light.demo_{i}" for i in range(201)]},
            CONF_ENTITIES,
            "too_many_entities",
        ),
        ({CONF_ENTITIES: ["Light.Demo Hall"]}, CONF_ENTITIES, "invalid_entity_id"),
        ({CONF_DOMAINS: ["unknown_domain"]}, CONF_DOMAINS, "invalid_domain"),
        ({CONF_MAX_EVENTS: 99}, CONF_MAX_EVENTS, "out_of_range"),
        ({CONF_MAX_KIB: 10**6}, CONF_MAX_KIB, "out_of_range"),
        ({CONF_MAX_AGE_MINUTES: 0.5}, CONF_MAX_AGE_MINUTES, "out_of_range"),
        ({CONF_HISTORY_DAYS: 8}, CONF_HISTORY_DAYS, "out_of_range"),
        ({CONF_HISTORY_DAYS: -1}, CONF_HISTORY_DAYS, "out_of_range"),
    ],
)
def test_validate_user_input_errors(
    change: dict[str, Any], error_key: str, error: str
) -> None:
    """Invalid input is reported per field and produces no options."""
    options, errors = validate_user_input({**VALID, **change})
    assert options is None
    assert errors == {error_key: error}


def test_disabled_collection_may_have_empty_allowlist() -> None:
    """Turning collection off never requires an allowlist."""
    options, errors = validate_user_input(
        {**VALID, CONF_COLLECTION_ENABLED: False, CONF_ENTITIES: [], CONF_DOMAINS: []}
    )
    assert errors == {}
    assert options == BlackboxOptions()


def test_limits_bound_the_allowlist_and_buffer() -> None:
    """Bounds exist for every limit and the defaults lie inside them."""
    assert MAX_ALLOWLIST_ENTITIES == 200
    for limit in LIMITS.values():
        assert limit.minimum <= limit.default <= limit.maximum
