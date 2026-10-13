"""Read-only "why did this change?" report for one entity.

Pure module without Home Assistant runtime. It turns the causal graph of the
recorded events into plain data for the response of the explain action. The
report names events, times and evidence labels; context IDs stay internal.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Any

from .correlation import CausalGraph, Evidence, Link, Note
from .normalizer import TRANSITION_CHANGED, EventKind, EventRecord

STATUS_EXPLAINED = "explained"
STATUS_NO_CHANGE = "no_change_recorded"
STATUS_NOT_FOUND = "change_not_found"
# The report lists at most this many recent changes to choose from.
MAX_LISTED_CHANGES = 20
# The monitor shows at most this many recent events.
MAX_ACTIVITY = 30


def _step(
    record: EventRecord, started_at: datetime, link: Link | None = None
) -> dict[str, Any]:
    step: dict[str, Any] = {
        "time": (started_at + timedelta(milliseconds=record.relative_ms)).isoformat(),
        "kind": str(record.kind),
    }
    if record.entity_ref is not None:
        step["entity_id"] = record.entity_ref
    if record.transition is not None:
        step["transition"] = record.transition
    if record.service is not None:
        step["service"] = record.service
        step["targets"] = list(record.target_refs)
    if link is not None:
        step["link"] = str(link.reason)
        step["evidence"] = str(link.evidence)
    return step


def _describe(record: EventRecord) -> str:
    if record.kind is EventKind.SERVICE_CALL:
        return f"{record.service} was called"
    if record.kind is EventKind.STATE_CHANGE:
        return f"{record.entity_ref} ({record.transition})"
    return f"{record.entity_ref} ran"


def _summary(
    target: EventRecord, evidence: Evidence, links: list[Link], notes: tuple[Note, ...]
) -> str:
    head = (
        f"{target.entity_ref} changed."
        if target.transition == TRANSITION_CHANGED
        else f"{target.entity_ref} changed ({target.transition})."
    )
    if evidence is Evidence.CONFIRMED_BY_CONTEXT:
        causes = ", then ".join(_describe(link.cause) for link in links)
        return f"{head} Confirmed by context: {causes}."
    if evidence is Evidence.INFERRED_TEMPORAL:
        causes = ", ".join(_describe(link.cause) for link in links)
        return (
            f"{head} No context link was recorded. "
            f"Possible causes, not confirmed: {causes}."
        )
    if Note.ENTITY_APPEARED in notes:
        return (
            f"{target.entity_ref} got its first state, for example after a restart "
            "or a reload. That is not a change with a cause."
        )
    if Note.ENTITY_REMOVED in notes:
        return (
            f"{target.entity_ref} was removed from Home Assistant. "
            "That is not a change with a cause."
        )
    return f"{head} The cause cannot be determined from the recorded events."


def _empty(
    entity_id: str, status: str, summary: str, changes: list[Any]
) -> dict[str, Any]:
    return {
        "entity_id": entity_id,
        "status": status,
        "evidence": str(Evidence.UNRESOLVED),
        "summary": summary,
        "change": None,
        "chain": [],
        "candidates": [],
        "notes": [],
        "changes": changes,
        "recorded_changes": len(changes),
    }


def build_report(
    *,
    entity_id: str,
    records: Iterable[EventRecord],
    started_at: datetime,
    event_id: int | None = None,
) -> dict[str, Any]:
    """Explain a recorded change of an entity, by default the latest one."""
    graph = CausalGraph(records)
    changes = graph.changes(entity_id)
    if event_id is None:
        target = changes[-1] if changes else None
    else:
        target = next((r for r in changes if r.event_id == event_id), None)
    recent = [
        {
            "event_id": record.event_id,
            "time": _step(record, started_at)["time"],
            "transition": record.transition,
            "selected": target is not None and record.event_id == target.event_id,
        }
        for record in reversed(changes[-MAX_LISTED_CHANGES:])
    ]
    if target is None:
        if event_id is not None:
            return _empty(
                entity_id,
                STATUS_NOT_FOUND,
                f"This change of {entity_id} is no longer recorded.",
                recent,
            )
        return _empty(
            entity_id,
            STATUS_NO_CHANGE,
            f"No change of {entity_id} has been recorded since recording started.",
            recent,
        )
    explanation = graph.explain_record(target)
    links = list(explanation.chain or explanation.candidates)
    summary = _summary(target, explanation.evidence, links, explanation.notes)
    if Note.DIRECT_ACTION in explanation.notes:
        summary += (
            " It was a direct action, for example from the UI, an app or the API."
        )
    return {
        "entity_id": entity_id,
        "status": STATUS_EXPLAINED,
        "evidence": str(explanation.evidence),
        "summary": summary,
        "change": _step(target, started_at),
        "chain": [_step(link.cause, started_at, link) for link in explanation.chain],
        "candidates": [
            _step(link.cause, started_at, link) for link in explanation.candidates
        ],
        "notes": [str(note) for note in explanation.notes],
        "changes": recent,
        "recorded_changes": len(changes),
    }


def build_overview(
    *, records: Iterable[EventRecord], started_at: datetime
) -> dict[str, Any]:
    """Summarise the buffer for the monitor view.

    Per entity: number of changes and the evidence of its latest change. A tape
    of the most recent events, newest first. Totals per evidence label.
    """
    graph = CausalGraph(records)
    evidence = {str(label): 0 for label in Evidence}
    latest: dict[str, EventRecord] = {}
    counts: dict[str, int] = {}
    for record in graph.records:
        if record.kind is EventKind.STATE_CHANGE and record.entity_ref is not None:
            latest[record.entity_ref] = record
            counts[record.entity_ref] = counts.get(record.entity_ref, 0) + 1
    entities = []
    initial = 0
    for entity_id, record in latest.items():
        explanation = graph.explain_record(record)
        label = str(explanation.evidence)
        lifecycle = bool(
            {Note.ENTITY_APPEARED, Note.ENTITY_REMOVED} & set(explanation.notes)
        )
        if lifecycle:
            initial += 1
        else:
            evidence[label] += 1
        entities.append(
            {
                "entity_id": entity_id,
                "changes": counts[entity_id],
                "last_time": _step(record, started_at)["time"],
                "last_transition": record.transition,
                "last_evidence": label,
                "initial": lifecycle,
                "_order": (record.relative_ms, record.event_id),
            }
        )
    entities.sort(key=lambda entry: entry["_order"], reverse=True)
    for entry in entities:
        del entry["_order"]
    return {
        "entities": entities,
        "activity": [
            _step(record, started_at)
            for record in reversed(graph.records[-MAX_ACTIVITY:])
        ],
        "evidence": evidence,
        "initial": initial,
    }
