from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Sequence
from uuid import uuid4

from erpermitsys.app.tracker_models import PermitEventRecord

PERMIT_TYPE_OPTIONS: tuple[tuple[str, str], ...] = (
    ("all", "All"),
    ("building", "Building"),
    ("demolition", "Demolition"),
    ("remodeling", "Remodeling"),
)


def today_iso() -> str:
    return date.today().isoformat()


def parse_iso_date(value: str) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        parsed_dt = datetime.fromisoformat(normalized)
        return parsed_dt.date()
    except Exception:
        pass
    try:
        return date.fromisoformat(text[:10])
    except Exception:
        return None


def parse_iso_datetime(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except Exception:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def next_action_detail_text(due_value: str) -> str:
    due_text = str(due_value or "").strip()
    return f"Due: {due_text}" if due_text else ""


def extract_due_from_next_action_detail(detail_value: str) -> str:
    detail_text = str(detail_value or "").strip()
    if not detail_text:
        return ""
    prefix = "due:"
    if detail_text.casefold().startswith(prefix):
        return detail_text[len(prefix):].strip()
    return ""


def parse_multi_values(raw_value: str) -> list[str]:
    chunks = (
        str(raw_value or "")
        .replace("\r", "\n")
        .replace(";", "\n")
        .replace(",", "\n")
        .splitlines()
    )
    values: list[str] = []
    seen: set[str] = set()
    for chunk in chunks:
        text = chunk.strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        values.append(text)
    return values


def join_multi_values(values: Sequence[str]) -> str:
    return ", ".join(parse_multi_values("\n".join(str(value) for value in values)))


def prefill_permit_events_from_milestones(
    *,
    request_date: str,
    application_date: str,
    issued_date: str,
    final_date: str,
    completion_date: str,
    next_action_text: str = "",
    next_action_due: str = "",
) -> list[PermitEventRecord]:
    rows: list[tuple[int, PermitEventRecord]] = []
    field_rows = (
        (request_date, "requested", "Permit requested"),
        (application_date, "submitted", "Application submitted"),
        (issued_date, "issued", "Permit issued"),
        (final_date, "finaled", "Permit finaled"),
        (completion_date, "closed", "Permit closed"),
    )
    for lifecycle_index, (raw_date, event_type, summary) in enumerate(field_rows):
        event_date = str(raw_date or "").strip()
        if not event_date:
            continue
        rows.append(
            (
                lifecycle_index,
                PermitEventRecord(
                    event_id=uuid4().hex,
                    event_type=event_type,
                    event_date=event_date,
                    summary=summary,
                    detail="",
                    actor_contact_id="",
                    attachments=[],
                ),
            )
        )

    next_action_summary = str(next_action_text or "").strip()
    next_action_due_text = str(next_action_due or "").strip()
    if next_action_summary or next_action_due_text:
        timeline_date = (
            next_action_due_text
            if parse_iso_date(next_action_due_text) is not None
            else today_iso()
        )
        rows.append(
            (
                len(field_rows),
                PermitEventRecord(
                    event_id=uuid4().hex,
                    event_type="note",
                    event_date=timeline_date,
                    summary=next_action_summary or "Next Action",
                    detail=next_action_detail_text(next_action_due_text),
                    actor_contact_id="",
                    attachments=[],
                ),
            )
        )

    rows.sort(
        key=lambda pair: (
            parse_iso_datetime(pair[1].event_date) or datetime.min.replace(tzinfo=timezone.utc),
            pair[0],
        )
    )
    return [event for _index, event in rows]

