"""Reduce Home Assistant events to data contract version 1.

Pure module without Home Assistant runtime. Event payloads are untrusted: every
value is validated, bounded and reduced to the few fields the correlation
needs. Full state objects, attributes, service data and user IDs are never
kept.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Any, Final

from .const import DATA_SCHEMA_VERSION

MAX_ENTITY_ID_LENGTH: Final = 255
MAX_TARGETS: Final = 32
TRANSITION_OTHER: Final = "other"
TRANSITION_UNCHANGED: Final = "unchanged"
# Numeric states (temperatures, energy, health values) are never kept.
TRANSITION_VALUE: Final = "value"
# Sensitive domains keep only the fact that something changed.
TRANSITION_CHANGED: Final = "changed"
# Sensitive entities that got their first state, or were removed.
TRANSITION_APPEARED: Final = "appeared"
TRANSITION_REMOVED: Final = "removed"
# Fixed overhead per record in the size estimate (object, references, counters).
RECORD_OVERHEAD_BYTES: Final = 160

_ENTITY_ID = re.compile(r"^(?!.+__)(?!_)[\da-z_]+(?<!_)\.(?!_)[\da-z_]+(?<!_)$")
_CONTEXT_ID = re.compile(r"^[0-9A-Za-z_-]{1,64}$")
_NAME = re.compile(r"^[a-z0-9_]{1,64}$")
_STATE_TOKEN = re.compile(r"^[a-z0-9_.:+-]{1,32}$")


class EventKind(StrEnum):
    """What a record describes."""

    STATE_CHANGE = "state_change"
    AUTOMATION_TRIGGERED = "automation_triggered"
    SCRIPT_STARTED = "script_started"
    SERVICE_CALL = "service_call"


@dataclass(frozen=True, slots=True)
class EventRecord:
    """One observed event, reduced to data contract version 1."""

    event_id: int
    relative_ms: int
    kind: EventKind
    entity_ref: str | None
    transition: str | None
    context_ref: str
    parent_context_ref: str | None
    service: str | None = None
    target_refs: tuple[str, ...] = ()
    schema: int = DATA_SCHEMA_VERSION

    def as_dict(self) -> dict[str, Any]:
        """Return the record in contract field order (JSON compatible)."""
        return {
            "schema": self.schema,
            "event_id": self.event_id,
            "relative_ms": self.relative_ms,
            "kind": str(self.kind),
            "entity_ref": self.entity_ref,
            "transition": self.transition,
            "context_ref": self.context_ref,
            "parent_context_ref": self.parent_context_ref,
            "service": self.service,
            "target_refs": list(self.target_refs),
        }


def record_size(record: EventRecord) -> int:
    """Estimate the memory a record takes, for the byte limit."""
    strings = (
        record.entity_ref,
        record.transition,
        record.context_ref,
        record.parent_context_ref,
        record.service,
        *record.target_refs,
    )
    return RECORD_OVERHEAD_BYTES + sum(len(s) for s in strings if s)


def is_appearance(record: EventRecord) -> bool:
    """Whether a state change is an entity getting its first state.

    This happens when Home Assistant starts or an integration reloads. It is
    not a change with a cause in the household.
    """
    transition = record.transition or ""
    return record.kind is EventKind.STATE_CHANGE and (
        transition == TRANSITION_APPEARED or transition.startswith("none_to_")
    )


def is_removal(record: EventRecord) -> bool:
    """Whether a state change is an entity being removed."""
    transition = record.transition or ""
    return record.kind is EventKind.STATE_CHANGE and (
        transition == TRANSITION_REMOVED or transition.endswith("_to_none")
    )


def valid_entity_id(value: object) -> bool:
    """Return whether a value is a plain, bounded entity ID."""
    return (
        isinstance(value, str)
        and len(value) <= MAX_ENTITY_ID_LENGTH
        and _ENTITY_ID.match(value) is not None
    )


def _context(value: object) -> str | None:
    if isinstance(value, str) and _CONTEXT_ID.match(value):
        return value
    return None


def _is_number(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return False
    return True


def _state_token(value: str | None) -> str:
    if value is None:
        return "none"
    if _is_number(value):
        return TRANSITION_VALUE
    token = value.lower()
    return token if _STATE_TOKEN.match(token) else TRANSITION_OTHER


def _transition(
    old_state: str | None, new_state: str | None, *, minimal: bool = False
) -> str:
    if minimal:
        if old_state is None:
            return TRANSITION_APPEARED
        if new_state is None:
            return TRANSITION_REMOVED
        return TRANSITION_UNCHANGED if old_state == new_state else TRANSITION_CHANGED
    old, new = _state_token(old_state), _state_token(new_state)
    if (
        old_state is not None
        and old_state == new_state
        and old not in (TRANSITION_OTHER, TRANSITION_VALUE)
    ):
        return TRANSITION_UNCHANGED
    return f"{old}_to_{new}"


def _entity_record(  # noqa: PLR0913 - one argument per contract field
    kind: EventKind,
    *,
    seq: int,
    relative_ms: int,
    entity_id: str,
    context_id: str,
    parent_id: str | None,
    transition: str | None = None,
) -> EventRecord | None:
    context = _context(context_id)
    if context is None or not valid_entity_id(entity_id):
        return None
    return EventRecord(
        event_id=seq,
        relative_ms=relative_ms,
        kind=kind,
        entity_ref=entity_id,
        transition=transition,
        context_ref=context,
        parent_context_ref=_context(parent_id),
    )


def normalize_state_change(  # noqa: PLR0913 - one argument per contract field
    *,
    seq: int,
    relative_ms: int,
    entity_id: str,
    old_state: str | None,
    new_state: str | None,
    context_id: str,
    parent_id: str | None,
    minimal: bool = False,
) -> EventRecord | None:
    """Reduce a state change. Only the transition between states is kept.

    Numeric states become "value". With minimal=True (sensitive domains) only
    "changed" or "unchanged" is kept, never a state.
    """
    return _entity_record(
        EventKind.STATE_CHANGE,
        seq=seq,
        relative_ms=relative_ms,
        entity_id=entity_id,
        context_id=context_id,
        parent_id=parent_id,
        transition=_transition(old_state, new_state, minimal=minimal),
    )


def normalize_automation_triggered(
    *,
    seq: int,
    relative_ms: int,
    entity_id: str,
    context_id: str,
    parent_id: str | None,
) -> EventRecord | None:
    """Reduce an automation run to its entity and contexts."""
    return _entity_record(
        EventKind.AUTOMATION_TRIGGERED,
        seq=seq,
        relative_ms=relative_ms,
        entity_id=entity_id,
        context_id=context_id,
        parent_id=parent_id,
    )


def normalize_script_started(
    *,
    seq: int,
    relative_ms: int,
    entity_id: str,
    context_id: str,
    parent_id: str | None,
) -> EventRecord | None:
    """Reduce a script run to its entity and contexts."""
    return _entity_record(
        EventKind.SCRIPT_STARTED,
        seq=seq,
        relative_ms=relative_ms,
        entity_id=entity_id,
        context_id=context_id,
        parent_id=parent_id,
    )


def _targets(
    service_data: Mapping[str, Any], allowed: Callable[[str], bool]
) -> tuple[str, ...]:
    raw = service_data.get("entity_id")
    candidates = [raw] if isinstance(raw, str) else raw
    if not isinstance(candidates, list | tuple):
        return ()
    targets = {
        entity for entity in candidates if valid_entity_id(entity) and allowed(entity)
    }
    return tuple(sorted(targets)[:MAX_TARGETS])


def normalize_service_call(  # noqa: PLR0913 - one argument per contract field
    *,
    seq: int,
    relative_ms: int,
    domain: str,
    service: str,
    service_data: Mapping[str, Any],
    context_id: str,
    parent_id: str | None,
    allowed: Callable[[str], bool],
) -> EventRecord | None:
    """Reduce a service call to its name and allowlisted entity targets.

    The service data itself is never kept. A call that targets no allowlisted
    entity is not recorded at all.
    """
    context = _context(context_id)
    if context is None or not (_NAME.match(domain) and _NAME.match(service)):
        return None
    targets = _targets(service_data, allowed)
    if not targets:
        return None
    return EventRecord(
        event_id=seq,
        relative_ms=relative_ms,
        kind=EventKind.SERVICE_CALL,
        entity_ref=None,
        transition=None,
        context_ref=context,
        parent_context_ref=_context(parent_id),
        service=f"{domain}.{service}",
        target_refs=targets,
    )


_TRANSITION = re.compile(r"^[a-z0-9_.:+-]{1,72}$")
_SERVICE = re.compile(r"^[a-z0-9_]{1,64}\.[a-z0-9_]{1,64}$")


def restore_record(
    data: object, *, event_id: int, relative_ms: int
) -> EventRecord | None:
    """Rebuild a record from stored history. The stored data is untrusted.

    Returns None when anything does not match data contract version 1.
    """
    if not isinstance(data, dict) or data.get("schema") != DATA_SCHEMA_VERSION:
        return None
    kind_value = data.get("kind")
    targets = data.get("target_refs")
    if kind_value not in set(EventKind) or not isinstance(targets, list):
        return None
    kind = EventKind(kind_value)
    context = _context(data.get("context_ref"))
    parent = data.get("parent_context_ref")
    entity = data.get("entity_ref")
    transition = data.get("transition")
    service = data.get("service")
    valid = (
        (parent is None or _context(parent) is not None)
        and (entity is None or valid_entity_id(entity))
        and (
            transition is None
            or (isinstance(transition, str) and _TRANSITION.match(transition))
        )
        and (service is None or (isinstance(service, str) and _SERVICE.match(service)))
        and len(targets) <= MAX_TARGETS
        and all(valid_entity_id(target) for target in targets)
        and (entity is not None) == (kind is not EventKind.SERVICE_CALL)
    )
    if not valid or context is None:
        return None
    return EventRecord(
        event_id=event_id,
        relative_ms=relative_ms,
        kind=kind,
        entity_ref=entity,
        transition=transition,
        context_ref=context,
        parent_context_ref=parent,
        service=service,
        target_refs=tuple(targets),
    )
