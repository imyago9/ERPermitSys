from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence

from erpermitsys.app.permit_workspace_helpers import parse_iso_datetime
from erpermitsys.app.tracker_models import (
    ContactRecord,
    PermitEventRecord,
    PermitRecord,
    normalize_event_type,
)

TimelineRenderRow = tuple[str, str, str, tuple[str, ...], str]


def event_sort_key(event: PermitEventRecord, index: int) -> tuple[datetime, int]:
    parsed = parse_iso_datetime(event.event_date)
    if parsed is None:
        parsed = datetime.min.replace(tzinfo=timezone.utc)
    return parsed, index


def default_business_rows_for_permit(
    permit: PermitRecord,
    contacts: Sequence[ContactRecord],
) -> list[TimelineRenderRow]:
    ordered_events = list(enumerate(permit.events))
    ordered_events.sort(key=lambda pair: event_sort_key(pair[1], pair[0]))

    contacts_by_id = {row.contact_id: row for row in contacts}
    timeline_rows: list[TimelineRenderRow] = []
    for _index, event in ordered_events:
        event_type = normalize_event_type(event.event_type)
        if event_type == "note":
            continue

        detail_lines: list[str] = []
        detail_text = str(event.detail or "").strip()
        if detail_text:
            detail_lines.append(detail_text)

        actor = contacts_by_id.get(str(event.actor_contact_id or "").strip())
        if actor is not None and actor.name.strip():
            detail_lines.append(f"Actor: {actor.name.strip()}")

        attachment_count = len(
            [
                str(document_id or "").strip()
                for document_id in event.attachments
                if str(document_id or "").strip()
            ]
        )
        if attachment_count > 0:
            detail_lines.append(f"Attachments: {attachment_count}")

        timeline_rows.append(
            (
                str(event.event_date or "").strip(),
                event_type,
                str(event.summary or "").strip(),
                tuple(detail_lines),
                str(event.event_id or "").strip(),
            )
        )
    return timeline_rows


def next_action_rows_for_permit(
    permit: PermitRecord,
    contacts: Sequence[ContactRecord],
) -> list[TimelineRenderRow]:
    ordered_events = list(enumerate(permit.events))
    ordered_events.sort(key=lambda pair: event_sort_key(pair[1], pair[0]))

    contacts_by_id = {row.contact_id: row for row in contacts}
    timeline_rows: list[TimelineRenderRow] = []
    for _index, event in ordered_events:
        event_type = normalize_event_type(event.event_type)
        if event_type != "note":
            continue

        detail_lines: list[str] = []
        detail_text = str(event.detail or "").strip()
        if detail_text:
            detail_lines.append(detail_text)

        actor = contacts_by_id.get(str(event.actor_contact_id or "").strip())
        if actor is not None and actor.name.strip():
            detail_lines.append(f"Actor: {actor.name.strip()}")

        attachment_count = len(
            [
                str(document_id or "").strip()
                for document_id in event.attachments
                if str(document_id or "").strip()
            ]
        )
        if attachment_count > 0:
            detail_lines.append(f"Attachments: {attachment_count}")

        summary_text = str(event.summary or "").strip() or "Next Action"
        timeline_rows.append(
            (
                str(event.event_date or "").strip(),
                event_type,
                summary_text,
                tuple(detail_lines),
                str(event.event_id or "").strip(),
            )
        )
    return timeline_rows


def latest_note_event_id_for_permit(permit: PermitRecord) -> str:
    latest_note_event_id = ""
    latest_note_key: tuple[datetime, int] | None = None
    for index, event in enumerate(permit.events):
        if normalize_event_type(event.event_type) != "note":
            continue
        key = event_sort_key(event, index)
        if latest_note_key is None or key > latest_note_key:
            latest_note_key = key
            latest_note_event_id = str(event.event_id or "").strip()
    return latest_note_event_id

