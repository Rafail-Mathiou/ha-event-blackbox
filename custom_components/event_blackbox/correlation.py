"""Deterministic causal graph over recorded events.

Pure module without Home Assistant runtime. It explains a recorded event with
explicitly labelled evidence:

- CONFIRMED_BY_CONTEXT: linked through the same context or a documented parent
  context.
- INFERRED_TEMPORAL: no context link, but other events happened shortly
  before. These are hypotheses only; all of them are listed.
- UNRESOLVED: no link and nothing nearby. A cause is never invented.

Ordering is stable by relative time and sequence number, so the same records
always give the same explanation, whatever order they arrive in.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from .normalizer import EventKind, EventRecord, is_appearance, is_removal

# Longest confirmed chain that is followed back from the target.
MAX_DEPTH: Final = 16
# Hypotheses only come from this window before the target.
TEMPORAL_WINDOW_MS: Final = 2_000
MAX_CANDIDATES: Final = 10

_RUN_KINDS: Final = frozenset(
    {EventKind.AUTOMATION_TRIGGERED, EventKind.SCRIPT_STARTED}
)
_CANDIDATE_KINDS: Final = frozenset({EventKind.STATE_CHANGE, *_RUN_KINDS})


class Evidence(StrEnum):
    """How well a link or an explanation is supported."""

    CONFIRMED_BY_CONTEXT = "CONFIRMED_BY_CONTEXT"
    INFERRED_TEMPORAL = "INFERRED_TEMPORAL"
    UNRESOLVED = "UNRESOLVED"


class LinkReason(StrEnum):
    """Why two records are linked."""

    SAME_CONTEXT = "SAME_CONTEXT"
    PARENT_CONTEXT = "PARENT_CONTEXT"
    TIME_WINDOW = "TIME_WINDOW"


class Note(StrEnum):
    """Additional facts about an explanation, as codes for the report."""

    DIRECT_ACTION = "DIRECT_ACTION"
    PARENT_NOT_RECORDED = "PARENT_NOT_RECORDED"
    NO_CONTEXT_LINK = "NO_CONTEXT_LINK"
    MULTIPLE_CANDIDATES = "MULTIPLE_CANDIDATES"
    CHAIN_TRUNCATED = "CHAIN_TRUNCATED"
    CANDIDATES_TRUNCATED = "CANDIDATES_TRUNCATED"
    ENTITY_APPEARED = "ENTITY_APPEARED"
    ENTITY_REMOVED = "ENTITY_REMOVED"


@dataclass(frozen=True, slots=True)
class Link:
    """A cause and its effect, with the evidence for the connection."""

    cause: EventRecord
    effect: EventRecord
    evidence: Evidence
    reason: LinkReason


@dataclass(frozen=True, slots=True)
class Explanation:
    """Why a recorded event happened, as far as the records tell."""

    target: EventRecord
    evidence: Evidence
    chain: tuple[Link, ...]
    candidates: tuple[Link, ...]
    notes: tuple[Note, ...]


def _key(record: EventRecord) -> tuple[int, int]:
    return (record.relative_ms, record.event_id)


def ordered(records: Iterable[EventRecord]) -> tuple[EventRecord, ...]:
    """Sort records by relative time, then sequence number (stable, total)."""
    return tuple(sorted(records, key=_key))


class CausalGraph:
    """Indexes a set of records and explains each of them."""

    def __init__(self, records: Iterable[EventRecord]) -> None:
        """Build the indexes. Records with duplicate sequence numbers keep the last."""
        self._ordered = ordered({r.event_id: r for r in records}.values())
        self._times = [r.relative_ms for r in self._ordered]
        self._by_id = {r.event_id: r for r in self._ordered}
        self._by_context: dict[str, list[EventRecord]] = defaultdict(list)
        for record in self._ordered:
            self._by_context[record.context_ref].append(record)

    @property
    def records(self) -> tuple[EventRecord, ...]:
        """All records in their stable order."""
        return self._ordered

    def changes(self, entity_id: str) -> tuple[EventRecord, ...]:
        """Return the recorded state changes of an entity, oldest first."""
        return tuple(
            record
            for record in self._ordered
            if record.kind is EventKind.STATE_CHANGE and record.entity_ref == entity_id
        )

    def explain(self, event_id: int) -> Explanation | None:
        """Explain one recorded event, or return None if it is unknown."""
        target = self._by_id.get(event_id)
        return None if target is None else self.explain_record(target)

    def explain_record(self, target: EventRecord) -> Explanation:
        """Explain a record of this graph."""
        notes: set[Note] = set()
        chain = self._confirmed_chain(target, notes)
        candidates: tuple[Link, ...] = ()
        if not chain:
            # A first state or a removal (startup, reload) has no cause in the
            # household; nearby events are not offered as guesses.
            if is_appearance(target):
                notes.add(Note.ENTITY_APPEARED)
            elif is_removal(target):
                notes.add(Note.ENTITY_REMOVED)
            else:
                notes.add(Note.NO_CONTEXT_LINK)
                candidates = self._temporal_candidates(target, notes)
        if chain:
            evidence = Evidence.CONFIRMED_BY_CONTEXT
        elif candidates:
            evidence = Evidence.INFERRED_TEMPORAL
        else:
            evidence = Evidence.UNRESOLVED
        return Explanation(
            target=target,
            evidence=evidence,
            chain=chain,
            candidates=candidates,
            notes=tuple(sorted(notes)),
        )

    def _earlier(self, context: str, before: EventRecord) -> list[EventRecord]:
        limit = _key(before)
        return [r for r in self._by_context.get(context, ()) if _key(r) < limit]

    def _same_context_cause(self, current: EventRecord) -> EventRecord | None:
        earlier = self._earlier(current.context_ref, current)
        if current.kind is EventKind.STATE_CHANGE:
            calls = [
                r
                for r in earlier
                if r.kind is EventKind.SERVICE_CALL
                and current.entity_ref in r.target_refs
            ]
            if calls:
                return calls[-1]
        runs = [r for r in earlier if r.kind in _RUN_KINDS]
        return runs[-1] if runs else None

    def _confirmed_chain(
        self, target: EventRecord, notes: set[Note]
    ) -> tuple[Link, ...]:
        links: list[Link] = []
        visited: set[str] = set()
        current = target
        while True:
            if len(links) >= MAX_DEPTH:
                notes.add(Note.CHAIN_TRUNCATED)
                break
            cause = self._same_context_cause(current)
            reason = LinkReason.SAME_CONTEXT
            if cause is None:
                visited.add(current.context_ref)
                parent = current.parent_context_ref
                if parent is None:
                    if current.kind is EventKind.SERVICE_CALL:
                        notes.add(Note.DIRECT_ACTION)
                    break
                if parent in visited:
                    break
                earlier = self._earlier(parent, current)
                if not earlier:
                    notes.add(Note.PARENT_NOT_RECORDED)
                    break
                cause, reason = earlier[-1], LinkReason.PARENT_CONTEXT
            links.append(
                Link(
                    cause=cause,
                    effect=current,
                    evidence=Evidence.CONFIRMED_BY_CONTEXT,
                    reason=reason,
                )
            )
            current = cause
        return tuple(reversed(links))

    def _temporal_candidates(
        self, target: EventRecord, notes: set[Note]
    ) -> tuple[Link, ...]:
        start = bisect_left(self._times, target.relative_ms - TEMPORAL_WINDOW_MS)
        end = bisect_right(self._times, target.relative_ms)
        limit = _key(target)
        found = [
            r
            for r in self._ordered[start:end]
            if _key(r) < limit
            and r.kind in _CANDIDATE_KINDS
            and r.entity_ref != target.entity_ref
            and not is_appearance(r)
            and not is_removal(r)
        ]
        found.sort(key=lambda r: (target.relative_ms - r.relative_ms, r.event_id))
        if len(found) > 1:
            notes.add(Note.MULTIPLE_CANDIDATES)
        if len(found) > MAX_CANDIDATES:
            notes.add(Note.CANDIDATES_TRUNCATED)
        return tuple(
            Link(
                cause=r,
                effect=target,
                evidence=Evidence.INFERRED_TEMPORAL,
                reason=LinkReason.TIME_WINDOW,
            )
            for r in found[:MAX_CANDIDATES]
        )
