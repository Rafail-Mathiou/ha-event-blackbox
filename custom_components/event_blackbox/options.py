"""Options of Event Blackbox: opt-in, allowlist and buffer limits.

Pure module without Home Assistant runtime. Stored options are untrusted
(they can be edited by hand or come from an older version), so parsing fails
safe: any invalid value yields the defaults, which collect nothing.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import re
from types import MappingProxyType
from typing import Any, Final

CONF_COLLECTION_ENABLED: Final = "collection_enabled"
CONF_ENTITIES: Final = "entities"
CONF_DOMAINS: Final = "domains"
CONF_MAX_EVENTS: Final = "max_events"
CONF_MAX_KIB: Final = "max_kib"
CONF_MAX_AGE_MINUTES: Final = "max_age_minutes"
CONF_HISTORY_DAYS: Final = "history_days"

# Excluded by default; selecting them needs an extra acknowledgement.
SENSITIVE_DOMAINS: Final = frozenset(
    {
        "alarm_control_panel",
        "camera",
        "device_tracker",
        "lock",
        "media_player",
        "person",
    }
)
# Domains offered in the domain allowlist (fixed list: no registry access).
SELECTABLE_DOMAINS: Final = tuple(
    sorted(
        {
            "automation",
            "binary_sensor",
            "button",
            "climate",
            "cover",
            "event",
            "fan",
            "humidifier",
            "input_boolean",
            "input_button",
            "input_number",
            "input_select",
            "light",
            "number",
            "scene",
            "script",
            "select",
            "sensor",
            "siren",
            "switch",
            "vacuum",
            "valve",
            "water_heater",
            *SENSITIVE_DOMAINS,
        }
    )
)
MAX_ALLOWLIST_ENTITIES: Final = 200
MAX_ENTITY_ID_LENGTH: Final = 255
# Same shape Home Assistant accepts for entity IDs.
_ENTITY_ID = re.compile(r"^(?!.+__)(?!_)[\da-z_]+(?<!_)\.(?!_)[\da-z_]+(?<!_)$")


@dataclass(frozen=True, slots=True)
class Limit:
    """Inclusive bounds and default of an integer option."""

    minimum: int
    default: int
    maximum: int

    def accepts(self, value: int) -> bool:
        """Return whether the value lies within the bounds."""
        return self.minimum <= value <= self.maximum


# Defaults: 2,000 events, 2 MiB, 30 minutes.
LIMITS: Final[Mapping[str, Limit]] = MappingProxyType(
    {
        CONF_MAX_EVENTS: Limit(100, 2000, 10_000),
        CONF_MAX_KIB: Limit(256, 2048, 8192),
        CONF_MAX_AGE_MINUTES: Limit(1, 30, 120),
        # Optional history across restarts: 0 means off.
        CONF_HISTORY_DAYS: Limit(0, 0, 7),
    }
)


@dataclass(frozen=True, slots=True)
class BlackboxOptions:
    """Validated options. The defaults collect nothing."""

    collection_enabled: bool = False
    entities: tuple[str, ...] = ()
    domains: tuple[str, ...] = ()
    max_events: int = LIMITS[CONF_MAX_EVENTS].default
    max_kib: int = LIMITS[CONF_MAX_KIB].default
    max_age_minutes: int = LIMITS[CONF_MAX_AGE_MINUTES].default
    history_days: int = LIMITS[CONF_HISTORY_DAYS].default

    @property
    def active(self) -> bool:
        """Collect only with opt-in AND a non-empty allowlist."""
        return self.collection_enabled and bool(self.entities or self.domains)

    @property
    def history(self) -> bool:
        """Keep recorded events across restarts (only while collection is active)."""
        return self.active and self.history_days > 0

    @property
    def sensitive_domains(self) -> tuple[str, ...]:
        """Sensitive domains touched by the allowlist, sorted."""
        touched = set(self.domains) | {e.split(".", 1)[0] for e in self.entities}
        return tuple(sorted(touched & SENSITIVE_DOMAINS))

    def as_dict(self) -> dict[str, Any]:
        """Return the representation stored in ConfigEntry.options."""
        return {
            CONF_COLLECTION_ENABLED: self.collection_enabled,
            CONF_ENTITIES: list(self.entities),
            CONF_DOMAINS: list(self.domains),
            CONF_MAX_EVENTS: self.max_events,
            CONF_MAX_KIB: self.max_kib,
            CONF_MAX_AGE_MINUTES: self.max_age_minutes,
            CONF_HISTORY_DAYS: self.history_days,
        }


def _as_int(value: object) -> int | None:
    """Accept ints and integral floats (number selectors return floats)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _as_str_list(value: object) -> list[str] | None:
    if not isinstance(value, list | tuple):
        return None
    if not all(isinstance(item, str) for item in value):
        return None
    return list(value)


def _unique_sorted(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted(set(values)))


def _entity_error(entities: list[str] | None) -> str | None:
    if entities is None:
        return "invalid_entity_id"
    if len(set(entities)) > MAX_ALLOWLIST_ENTITIES:
        return "too_many_entities"
    if not all(
        len(e) <= MAX_ENTITY_ID_LENGTH and _ENTITY_ID.match(e) for e in entities
    ):
        return "invalid_entity_id"
    return None


def validate_user_input(
    user_input: Mapping[str, Any],
) -> tuple[BlackboxOptions | None, dict[str, str]]:
    """Validate options flow input. Returns (options, {}) or (None, errors)."""
    errors: dict[str, str] = {}

    enabled = user_input.get(CONF_COLLECTION_ENABLED, False)
    if not isinstance(enabled, bool):
        errors[CONF_COLLECTION_ENABLED] = "invalid_value"

    entities = _as_str_list(user_input.get(CONF_ENTITIES, []))
    if error := _entity_error(entities):
        errors[CONF_ENTITIES] = error

    domains = _as_str_list(user_input.get(CONF_DOMAINS, []))
    if domains is None or not set(domains) <= set(SELECTABLE_DOMAINS):
        errors[CONF_DOMAINS] = "invalid_domain"

    numbers: dict[str, int] = {}
    for key, limit in LIMITS.items():
        number = _as_int(user_input.get(key, limit.default))
        if number is None or not limit.accepts(number):
            errors[key] = "out_of_range"
        else:
            numbers[key] = number

    if errors or entities is None or domains is None:
        return None, errors
    if enabled and not (entities or domains):
        return None, {"base": "allowlist_required"}

    return (
        BlackboxOptions(
            collection_enabled=bool(enabled),
            entities=_unique_sorted(entities),
            domains=_unique_sorted(domains),
            max_events=numbers[CONF_MAX_EVENTS],
            max_kib=numbers[CONF_MAX_KIB],
            max_age_minutes=numbers[CONF_MAX_AGE_MINUTES],
            history_days=numbers[CONF_HISTORY_DAYS],
        ),
        {},
    )


def parse_options(raw: Mapping[str, Any]) -> BlackboxOptions:
    """Parse stored options. Anything invalid falls back to the safe defaults."""
    options, errors = validate_user_input(raw)
    if errors or options is None:
        return BlackboxOptions()
    return options
