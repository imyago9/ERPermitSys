from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


_PERMIT_TYPES: tuple[str, ...] = ("building", "demolition", "remodeling")
_PERMIT_TYPE_ALIASES: dict[str, str] = {
    "build": "building",
    "building": "building",
    "demo": "demolition",
    "demolition": "demolition",
    "remodel": "remodeling",
    "remodeling": "remodeling",
    "remodelled": "remodeling",
    "remodeled": "remodeling",
}

PERMIT_EVENT_TYPES: tuple[str, ...] = (
    "requested",
    "intake_prepared",
    "submitted",
    "intake_accepted",
    "fee_paid",
    "plan_review_started",
    "comments_received",
    "resubmitted",
    "approved",
    "issued",
    "inspection_scheduled",
    "inspection_passed",
    "inspection_failed",
    "finaled",
    "closed",
    "canceled",
    "note",
)
_MAJOR_EVENT_TYPES: tuple[str, ...] = tuple(
    value for value in PERMIT_EVENT_TYPES if value != "note"
)

_SLOT_STATUSES: tuple[str, ...] = (
    "missing",
    "uploaded",
    "accepted",
    "rejected",
    "superseded",
)
_DOCUMENT_REVIEW_STATUSES: tuple[str, ...] = (
    "uploaded",
    "accepted",
    "rejected",
    "superseded",
)

_PARCEL_NORMALIZE_PATTERN = re.compile(r"[^a-z0-9]+")
_SLOT_ID_NORMALIZE_PATTERN = re.compile(r"[^a-z0-9_]+")
_CYCLE_SEGMENT_PATTERN = re.compile(r"(?:^|[\\\\/])cycle[-_ ]?(\\d+)(?:$|[\\\\/])", re.IGNORECASE)
_HEX_COLOR_PATTERN = re.compile(r"^#?([0-9a-fA-F]{6})$")
_SHORT_HEX_COLOR_PATTERN = re.compile(r"^#?([0-9a-fA-F]{3})$")


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _as_non_negative_int(value: Any) -> int:
    try:
        parsed = int(value)
    except Exception:
        return 0
    return max(0, parsed)


def _as_positive_int(value: Any, *, default: int = 1) -> int:
    try:
        parsed = int(value)
    except Exception:
        return max(1, int(default))
    return max(1, parsed)


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = _as_text(value).casefold()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off", ""}:
        return False
    return bool(value)


def _parse_text_list(value: Any, *, fallback: Any = None) -> list[str]:
    source = value
    if source is None or source == "":
        source = fallback

    if isinstance(source, list):
        candidates = source
    elif isinstance(source, tuple):
        candidates = list(source)
    else:
        candidates = [source]

    rows: list[str] = []
    for candidate in candidates:
        text = _as_text(candidate)
        if not text:
            continue
        for chunk in text.replace(";", "\n").replace(",", "\n").splitlines():
            normalized = chunk.strip()
            if normalized:
                rows.append(normalized)

    deduped: list[str] = []
    seen: set[str] = set()
    for row in rows:
        key = row.casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def _safe_uuid(value: Any) -> str:
    normalized = _as_text(value)
    return normalized or uuid4().hex


def normalize_parcel_id(value: Any) -> str:
    text = _as_text(value).casefold()
    if not text:
        return ""
    return _PARCEL_NORMALIZE_PATTERN.sub("", text)


def normalize_list_color(value: Any) -> str:
    text = _as_text(value)
    if not text:
        return ""
    full_match = _HEX_COLOR_PATTERN.fullmatch(text)
    if full_match is not None:
        return f"#{full_match.group(1).upper()}"
    short_match = _SHORT_HEX_COLOR_PATTERN.fullmatch(text)
    if short_match is not None:
        expanded = "".join(ch * 2 for ch in short_match.group(1))
        return f"#{expanded.upper()}"
    return ""


def normalize_permit_type(value: Any) -> str:
    raw = _as_text(value).replace("-", "_").replace(" ", "_").casefold()
    normalized = _PERMIT_TYPE_ALIASES.get(raw, raw)
    if normalized in _PERMIT_TYPES:
        return normalized
    return "building"


def normalize_event_type(value: Any) -> str:
    raw = _as_text(value).replace("-", "_").replace(" ", "_").casefold()
    if raw in PERMIT_EVENT_TYPES:
        return raw
    return "note"


def event_type_label(value: Any) -> str:
    normalized = normalize_event_type(value)
    return normalized.replace("_", " ").title()


def normalize_slot_status(value: Any) -> str:
    normalized = _as_text(value).replace("-", "_").replace(" ", "_").casefold()
    if normalized in _SLOT_STATUSES:
        return normalized
    return "missing"


def normalize_document_review_status(value: Any) -> str:
    normalized = _as_text(value).replace("-", "_").replace(" ", "_").casefold()
    if normalized in _DOCUMENT_REVIEW_STATUSES:
        return normalized
    if normalized == "missing":
        return "uploaded"
    return "uploaded"


def normalize_slot_id(value: Any) -> str:
    normalized = _as_text(value).replace("-", "_").replace(" ", "_").casefold()
    if not normalized:
        return ""
    normalized = _SLOT_ID_NORMALIZE_PATTERN.sub("_", normalized)
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    return normalized


def _infer_cycle_index_from_relative_path(value: Any) -> int:
    relative_path = _as_text(value)
    if not relative_path:
        return 1
    match = _CYCLE_SEGMENT_PATTERN.search(relative_path)
    if match is None:
        return 1
    try:
        parsed = int(match.group(1))
    except Exception:
        return 1
    return max(1, parsed)


def _parse_iso_datetime(value: Any) -> datetime | None:
    text = _as_text(value)
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


def compute_permit_status(events: list["PermitEventRecord"], *, fallback: str = "") -> str:
    latest_event: PermitEventRecord | None = None
    latest_key: tuple[datetime, int] | None = None
    for index, event in enumerate(events):
        event_type = normalize_event_type(event.event_type)
        if event_type not in _MAJOR_EVENT_TYPES:
            continue
        parsed_date = _parse_iso_datetime(event.event_date) or datetime.min.replace(tzinfo=timezone.utc)
        candidate_key = (parsed_date, index)
        if latest_key is None or candidate_key > latest_key:
            latest_key = candidate_key
            latest_event = event

    if latest_event is not None:
        return normalize_event_type(latest_event.event_type)

    if fallback:
        normalized_fallback = normalize_event_type(fallback)
        if normalized_fallback in _MAJOR_EVENT_TYPES:
            return normalized_fallback
    return "requested"


def event_affects_status(event_type: Any) -> bool:
    return normalize_event_type(event_type) in _MAJOR_EVENT_TYPES


@dataclass(slots=True)
class ContactMethodRecord:
    label: str = ""
    emails: list[str] = field(default_factory=list)
    numbers: list[str] = field(default_factory=list)
    note: str = ""

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "ContactMethodRecord":
        if not isinstance(value, Mapping):
            return cls()
        return cls(
            label=_as_text(value.get("label") or value.get("name") or value.get("title")),
            emails=_parse_text_list(value.get("emails"), fallback=value.get("email")),
            numbers=_parse_text_list(value.get("numbers"), fallback=value.get("number")),
            note=_as_text(value.get("note") or value.get("details")),
        )

    def to_mapping(self) -> dict[str, str | list[str]]:
        return {
            "label": _as_text(self.label),
            "emails": _parse_text_list(self.emails),
            "numbers": _parse_text_list(self.numbers),
            "note": _as_text(self.note),
        }


def _normalize_contact_methods(methods: list[ContactMethodRecord]) -> list[ContactMethodRecord]:
    rows: list[ContactMethodRecord] = []
    seen: set[str] = set()
    for entry in methods:
        label = _as_text(entry.label)
        emails = _parse_text_list(entry.emails)
        numbers = _parse_text_list(entry.numbers)
        note = _as_text(entry.note)
        if not any((label, emails, numbers, note)):
            continue
        key = "|".join(
            (
                label.casefold(),
                ";".join(value.casefold() for value in emails),
                ";".join(value.casefold() for value in numbers),
                note.casefold(),
            )
        )
        if key in seen:
            continue
        seen.add(key)
        rows.append(ContactMethodRecord(label=label, emails=emails, numbers=numbers, note=note))
    return rows


def _aggregate_contact_method_values(
    methods: list[ContactMethodRecord],
    *,
    field_name: str,
) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for method in methods:
        source = method.emails if field_name == "emails" else method.numbers
        for value in _parse_text_list(source):
            key = value.casefold()
            if key in seen:
                continue
            seen.add(key)
            values.append(value)
    return values


def _parse_contact_methods(value: Any) -> list[ContactMethodRecord]:
    if not isinstance(value, list):
        return []
    rows: list[ContactMethodRecord] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        rows.append(ContactMethodRecord.from_mapping(item))
    return _normalize_contact_methods(rows)


@dataclass(slots=True)
class ContactRecord:
    contact_id: str
    name: str
    numbers: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    roles: list[str] = field(default_factory=list)
    contact_methods: list[ContactMethodRecord] = field(default_factory=list)
    list_color: str = ""

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "ContactRecord":
        if not isinstance(value, Mapping):
            return cls(contact_id=uuid4().hex, name="")

        legacy_numbers = _parse_text_list(value.get("numbers"), fallback=value.get("number"))
        legacy_emails = _parse_text_list(value.get("emails"), fallback=value.get("email"))
        legacy_note = _as_text(value.get("note") or value.get("contact_note"))

        contact_methods = _parse_contact_methods(value.get("contact_methods"))
        if not contact_methods and any((legacy_emails, legacy_numbers, legacy_note)):
            contact_methods = _normalize_contact_methods(
                [ContactMethodRecord(emails=legacy_emails, numbers=legacy_numbers, note=legacy_note)]
            )

        numbers = _aggregate_contact_method_values(contact_methods, field_name="numbers")
        emails = _aggregate_contact_method_values(contact_methods, field_name="emails")
        if not numbers:
            numbers = legacy_numbers
        if not emails:
            emails = legacy_emails

        return cls(
            contact_id=_safe_uuid(value.get("contact_id") or value.get("id")),
            name=_as_text(value.get("name")),
            numbers=numbers,
            emails=emails,
            roles=_parse_text_list(value.get("roles"), fallback=value.get("role")),
            contact_methods=contact_methods,
            list_color=normalize_list_color(
                value.get("list_color") or value.get("accent_color") or value.get("color")
            ),
        )

    def to_mapping(self) -> dict[str, Any]:
        contact_methods = _normalize_contact_methods(self.contact_methods)
        emails = _aggregate_contact_method_values(contact_methods, field_name="emails")
        numbers = _aggregate_contact_method_values(contact_methods, field_name="numbers")
        if not contact_methods and any((self.emails, self.numbers)):
            contact_methods = _normalize_contact_methods(
                [ContactMethodRecord(emails=self.emails, numbers=self.numbers, note="")]
            )
            emails = _aggregate_contact_method_values(contact_methods, field_name="emails")
            numbers = _aggregate_contact_method_values(contact_methods, field_name="numbers")
        if not emails:
            emails = _parse_text_list(self.emails)
        if not numbers:
            numbers = _parse_text_list(self.numbers)
        return {
            "contact_id": _safe_uuid(self.contact_id),
            "name": _as_text(self.name),
            "numbers": numbers,
            "emails": emails,
            "roles": _parse_text_list(self.roles),
            "contact_methods": [entry.to_mapping() for entry in contact_methods],
            "list_color": normalize_list_color(self.list_color),
        }


@dataclass(slots=True)
class JurisdictionRecord:
    jurisdiction_id: str
    name: str
    jurisdiction_type: str = "county"
    parent_county: str = ""
    portal_urls: list[str] = field(default_factory=list)
    contact_ids: list[str] = field(default_factory=list)
    portal_vendor: str = ""
    notes: str = ""
    list_color: str = ""

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "JurisdictionRecord":
        if not isinstance(value, Mapping):
            return cls(jurisdiction_id=uuid4().hex, name="")
        jurisdiction_type = _as_text(
            value.get("jurisdiction_type")
            or value.get("type")
        ).casefold()
        if jurisdiction_type not in {"city", "county"}:
            jurisdiction_type = "county"
        return cls(
            jurisdiction_id=_safe_uuid(value.get("jurisdiction_id") or value.get("id")),
            name=_as_text(value.get("name")),
            jurisdiction_type=jurisdiction_type,
            parent_county=_as_text(value.get("parent_county") or value.get("parent_jurisdiction_id")),
            portal_urls=_parse_text_list(
                value.get("portal_urls"),
                fallback=value.get("portal_url"),
            ),
            contact_ids=_parse_text_list(value.get("contact_ids")),
            portal_vendor=_as_text(value.get("portal_vendor")),
            notes=_as_text(value.get("notes")),
            list_color=normalize_list_color(
                value.get("list_color") or value.get("accent_color") or value.get("color")
            ),
        )

    def to_mapping(self) -> dict[str, str | list[str]]:
        return {
            "jurisdiction_id": _safe_uuid(self.jurisdiction_id),
            "name": _as_text(self.name),
            "jurisdiction_type": "city" if _as_text(self.jurisdiction_type).casefold() == "city" else "county",
            "parent_county": _as_text(self.parent_county),
            "portal_urls": _parse_text_list(self.portal_urls),
            "contact_ids": _parse_text_list(self.contact_ids),
            "portal_vendor": _as_text(self.portal_vendor),
            "notes": _as_text(self.notes),
            "list_color": normalize_list_color(self.list_color),
        }


@dataclass(slots=True)
class PropertyRecord:
    property_id: str
    display_address: str
    parcel_id: str
    parcel_id_norm: str = ""
    jurisdiction_id: str = ""
    contact_ids: list[str] = field(default_factory=list)
    list_color: str = ""
    tags: list[str] = field(default_factory=list)
    notes: str = ""

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "PropertyRecord":
        if not isinstance(value, Mapping):
            return cls(property_id=uuid4().hex, display_address="", parcel_id="")

        parcel_id = _as_text(value.get("parcel_id") or value.get("parcel"))
        parcel_id_norm = _as_text(value.get("parcel_id_norm"))
        if not parcel_id_norm:
            parcel_id_norm = normalize_parcel_id(parcel_id)

        return cls(
            property_id=_safe_uuid(value.get("property_id") or value.get("id")),
            display_address=_as_text(value.get("display_address") or value.get("address")),
            parcel_id=parcel_id,
            parcel_id_norm=parcel_id_norm,
            jurisdiction_id=_as_text(value.get("jurisdiction_id")),
            contact_ids=_parse_text_list(value.get("contact_ids")),
            list_color=normalize_list_color(
                value.get("list_color") or value.get("accent_color") or value.get("color")
            ),
            tags=_parse_text_list(value.get("tags")),
            notes=_as_text(value.get("notes")),
        )

    def to_mapping(self) -> dict[str, str | list[str]]:
        parcel = _as_text(self.parcel_id)
        normalized = _as_text(self.parcel_id_norm) or normalize_parcel_id(parcel)
        return {
            "property_id": _safe_uuid(self.property_id),
            "display_address": _as_text(self.display_address),
            "parcel_id": parcel,
            "parcel_id_norm": normalized,
            "jurisdiction_id": _as_text(self.jurisdiction_id),
            "contact_ids": _parse_text_list(self.contact_ids),
            "list_color": normalize_list_color(self.list_color),
            "tags": _parse_text_list(self.tags),
            "notes": _as_text(self.notes),
        }


@dataclass(slots=True)
class PermitDocumentFolder:
    folder_id: str
    name: str
    parent_folder_id: str = ""

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "PermitDocumentFolder":
        if not isinstance(value, Mapping):
            return cls(folder_id="", name="")
        return cls(
            folder_id=_as_text(value.get("folder_id")),
            name=_as_text(value.get("name")),
            parent_folder_id=_as_text(value.get("parent_folder_id")),
        )

    def to_mapping(self) -> dict[str, str]:
        return {
            "folder_id": _as_text(self.folder_id),
            "name": _as_text(self.name),
            "parent_folder_id": _as_text(self.parent_folder_id),
        }


@dataclass(slots=True)
class PermitDocumentRecord:
    document_id: str
    folder_id: str
    original_name: str
    stored_name: str
    relative_path: str
    slot_id: str = ""
    cycle_index: int = 1
    revision_index: int = 1
    review_status: str = "uploaded"
    reviewed_at: str = ""
    review_note: str = ""
    imported_at: str = ""
    byte_size: int = 0
    sha256: str = ""

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "PermitDocumentRecord":
        if not isinstance(value, Mapping):
            return cls(
                document_id="",
                folder_id="",
                original_name="",
                stored_name="",
                relative_path="",
            )
        return cls(
            document_id=_as_text(value.get("document_id")),
            folder_id=_as_text(value.get("folder_id")),
            original_name=_as_text(value.get("original_name")),
            stored_name=_as_text(value.get("stored_name")),
            relative_path=_as_text(value.get("relative_path")),
            slot_id=normalize_slot_id(value.get("slot_id") or value.get("slot")) or _as_text(value.get("slot_id")),
            cycle_index=_as_positive_int(
                value.get("cycle_index") or value.get("cycle") or _infer_cycle_index_from_relative_path(value.get("relative_path"))
            ),
            revision_index=_as_positive_int(value.get("revision_index") or value.get("revision")),
            review_status=normalize_document_review_status(value.get("review_status") or value.get("status")),
            reviewed_at=_as_text(value.get("reviewed_at")),
            review_note=_as_text(value.get("review_note") or value.get("review_notes") or value.get("review_detail")),
            imported_at=_as_text(value.get("imported_at")),
            byte_size=_as_non_negative_int(value.get("byte_size")),
            sha256=_as_text(value.get("sha256")),
        )

    def to_mapping(self) -> dict[str, str | int]:
        return {
            "document_id": _as_text(self.document_id),
            "folder_id": _as_text(self.folder_id),
            "original_name": _as_text(self.original_name),
            "stored_name": _as_text(self.stored_name),
            "relative_path": _as_text(self.relative_path),
            "slot_id": normalize_slot_id(self.slot_id) or _as_text(self.slot_id),
            "cycle_index": _as_positive_int(self.cycle_index),
            "revision_index": _as_positive_int(self.revision_index),
            "review_status": normalize_document_review_status(self.review_status),
            "reviewed_at": _as_text(self.reviewed_at),
            "review_note": _as_text(self.review_note),
            "imported_at": _as_text(self.imported_at),
            "byte_size": _as_non_negative_int(self.byte_size),
            "sha256": _as_text(self.sha256),
        }


@dataclass(slots=True)
class PermitParty:
    contact_id: str
    role: str
    note: str = ""

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "PermitParty":
        if not isinstance(value, Mapping):
            return cls(contact_id="", role="")
        return cls(
            contact_id=_as_text(value.get("contact_id")),
            role=_as_text(value.get("role")),
            note=_as_text(value.get("note")),
        )

    def to_mapping(self) -> dict[str, str]:
        return {
            "contact_id": _as_text(self.contact_id),
            "role": _as_text(self.role),
            "note": _as_text(self.note),
        }


@dataclass(slots=True)
class PermitEventRecord:
    event_id: str
    event_type: str
    event_date: str
    summary: str = ""
    detail: str = ""
    actor_contact_id: str = ""
    attachments: list[str] = field(default_factory=list)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "PermitEventRecord":
        if not isinstance(value, Mapping):
            return cls(event_id=uuid4().hex, event_type="note", event_date="")
        return cls(
            event_id=_safe_uuid(value.get("event_id") or value.get("id")),
            event_type=normalize_event_type(value.get("event_type") or value.get("type")),
            event_date=_as_text(value.get("event_date") or value.get("date")),
            summary=_as_text(value.get("summary")),
            detail=_as_text(value.get("detail") or value.get("notes")),
            actor_contact_id=_as_text(value.get("actor_contact_id")),
            attachments=_parse_text_list(value.get("attachments")),
        )

    def to_mapping(self) -> dict[str, str | list[str]]:
        return {
            "event_id": _safe_uuid(self.event_id),
            "event_type": normalize_event_type(self.event_type),
            "event_date": _as_text(self.event_date),
            "summary": _as_text(self.summary),
            "detail": _as_text(self.detail),
            "actor_contact_id": _as_text(self.actor_contact_id),
            "attachments": _parse_text_list(self.attachments),
        }


@dataclass(slots=True)
class PermitDocumentSlot:
    slot_id: str
    label: str
    required: bool
    status: str = "missing"
    folder_id: str = ""
    active_cycle: int = 1
    notes: str = ""

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "PermitDocumentSlot":
        if not isinstance(value, Mapping):
            return cls(slot_id="", label="", required=False)
        raw_slot_id = _as_text(value.get("slot_id") or value.get("id"))
        slot_id = normalize_slot_id(raw_slot_id) or raw_slot_id
        raw_folder_id = _as_text(value.get("folder_id"))
        folder_id = normalize_slot_id(raw_folder_id) or raw_folder_id or slot_id
        return cls(
            slot_id=slot_id,
            label=_as_text(value.get("label") or slot_id),
            required=_as_bool(value.get("required")),
            status=normalize_slot_status(value.get("status")),
            folder_id=folder_id,
            active_cycle=_as_positive_int(value.get("active_cycle") or value.get("cycle")),
            notes=_as_text(value.get("notes")),
        )

    def to_mapping(self) -> dict[str, str | bool]:
        slot_id = normalize_slot_id(self.slot_id) or _as_text(self.slot_id)
        folder_id = normalize_slot_id(self.folder_id) or _as_text(self.folder_id) or slot_id
        return {
            "slot_id": slot_id,
            "label": _as_text(self.label),
            "required": bool(self.required),
            "status": normalize_slot_status(self.status),
            "folder_id": folder_id,
            "active_cycle": _as_positive_int(self.active_cycle),
            "notes": _as_text(self.notes),
        }


@dataclass(slots=True)
class DocumentChecklistTemplate:
    template_id: str
    name: str
    permit_type: str = "building"
    slots: list[PermitDocumentSlot] = field(default_factory=list)
    notes: str = ""

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "DocumentChecklistTemplate":
        if not isinstance(value, Mapping):
            return cls(template_id=uuid4().hex, name="")
        permit_type = normalize_permit_type(value.get("permit_type") or value.get("type"))
        record = cls(
            template_id=_safe_uuid(value.get("template_id") or value.get("id")),
            name=_as_text(value.get("name")),
            permit_type=permit_type,
            slots=_parse_permit_document_slots(value.get("slots") or value.get("document_slots")),
            notes=_as_text(value.get("notes")),
        )
        record.slots = normalize_template_slots(record.slots, permit_type=permit_type)
        return record

    def to_mapping(self) -> dict[str, Any]:
        permit_type = normalize_permit_type(self.permit_type)
        return {
            "template_id": _safe_uuid(self.template_id),
            "name": _as_text(self.name),
            "permit_type": permit_type,
            "slots": [
                PermitDocumentSlot(
                    slot_id=normalize_slot_id(entry.slot_id) or entry.slot_id,
                    label=_as_text(entry.label),
                    required=bool(entry.required),
                    status="missing",
                    folder_id=normalize_slot_id(entry.folder_id) or entry.folder_id or entry.slot_id,
                    notes=_as_text(entry.notes),
                ).to_mapping()
                for entry in normalize_template_slots(self.slots, permit_type=permit_type)
            ],
            "notes": _as_text(self.notes),
        }


@dataclass(slots=True)
class PermitRecord:
    permit_id: str
    property_id: str
    permit_type: str = "building"
    permit_number: str = ""
    status: str = "requested"
    next_action_text: str = ""
    next_action_due: str = ""
    request_date: str = ""
    application_date: str = ""
    issued_date: str = ""
    final_date: str = ""
    completion_date: str = ""
    parties: list[PermitParty] = field(default_factory=list)
    events: list[PermitEventRecord] = field(default_factory=list)
    document_slots: list[PermitDocumentSlot] = field(default_factory=list)
    document_folders: list[PermitDocumentFolder] = field(default_factory=list)
    documents: list[PermitDocumentRecord] = field(default_factory=list)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "PermitRecord":
        if not isinstance(value, Mapping):
            record = cls(permit_id=uuid4().hex, property_id="")
            ensure_default_document_structure(record)
            record.status = compute_permit_status(record.events, fallback=record.status)
            return record

        permit_type = normalize_permit_type(value.get("permit_type") or value.get("category"))
        record = cls(
            permit_id=_safe_uuid(value.get("permit_id") or value.get("id")),
            property_id=_as_text(value.get("property_id")),
            permit_type=permit_type,
            permit_number=_as_text(value.get("permit_number")),
            status=normalize_event_type(value.get("status") or "requested"),
            next_action_text=_as_text(value.get("next_action_text")),
            next_action_due=_as_text(value.get("next_action_due")),
            request_date=_as_text(value.get("request_date")),
            application_date=_as_text(value.get("application_date")),
            issued_date=_as_text(value.get("issued_date")),
            final_date=_as_text(value.get("final_date")),
            completion_date=_as_text(value.get("completion_date")),
            parties=_parse_permit_parties(value.get("parties")),
            events=_parse_permit_events(value.get("events")),
            document_slots=_parse_permit_document_slots(value.get("document_slots")),
            document_folders=_parse_permit_document_folders(value.get("document_folders")),
            documents=_parse_permit_documents(value.get("documents")),
        )
        ensure_default_document_structure(record)
        refresh_slot_status_from_documents(record)
        record.status = compute_permit_status(record.events, fallback=record.status)
        return record

    def to_mapping(self) -> dict[str, Any]:
        refresh_slot_status_from_documents(self)
        self.status = compute_permit_status(self.events, fallback=self.status)
        return {
            "permit_id": _safe_uuid(self.permit_id),
            "property_id": _as_text(self.property_id),
            "permit_type": normalize_permit_type(self.permit_type),
            "permit_number": _as_text(self.permit_number),
            "status": normalize_event_type(self.status),
            "next_action_text": _as_text(self.next_action_text),
            "next_action_due": _as_text(self.next_action_due),
            "request_date": _as_text(self.request_date),
            "application_date": _as_text(self.application_date),
            "issued_date": _as_text(self.issued_date),
            "final_date": _as_text(self.final_date),
            "completion_date": _as_text(self.completion_date),
            "parties": [entry.to_mapping() for entry in self.parties],
            "events": [entry.to_mapping() for entry in self.events],
            "document_slots": [entry.to_mapping() for entry in self.document_slots],
            "document_folders": [entry.to_mapping() for entry in self.document_folders],
            "documents": [entry.to_mapping() for entry in self.documents],
        }


_DOCUMENT_SLOT_TEMPLATE_BUILDING: tuple[tuple[str, str, bool], ...] = (
    ("application", "Application", True),
    ("plans", "Plans", True),
    ("owner_authorization", "Owner Authorization", True),
    ("contractor_license", "Contractor License", True),
    ("engineering", "Engineering", False),
    ("invoices_fees", "Invoices / Fees", False),
    ("inspection_reports", "Inspection Reports", False),
    ("photos", "Photos", False),
    ("other", "Other", False),
)

_DOCUMENT_SLOT_TEMPLATE_DEMOLITION: tuple[tuple[str, str, bool], ...] = (
    ("application", "Application", True),
    ("demo_plan_or_scope", "Demo Plan or Scope", True),
    ("utility_disconnect_letters", "Utility Disconnect Letters", True),
    ("contractor_license", "Contractor License", True),
    ("disposal_manifest", "Disposal Manifest", False),
    ("asbestos_statement", "Asbestos Statement", False),
    ("inspection_reports", "Inspection Reports", False),
    ("photos", "Photos", False),
    ("other", "Other", False),
)


def _document_slot_template_rows(permit_type: Any) -> tuple[tuple[str, str, bool], ...]:
    normalized_type = normalize_permit_type(permit_type)
    if normalized_type == "demolition":
        return _DOCUMENT_SLOT_TEMPLATE_DEMOLITION
    return _DOCUMENT_SLOT_TEMPLATE_BUILDING


def normalize_template_slots(
    slots: list[PermitDocumentSlot],
    *,
    permit_type: Any,
) -> list[PermitDocumentSlot]:
    rows: list[PermitDocumentSlot] = []
    seen_slot_ids: set[str] = set()
    for slot in slots:
        slot_id = normalize_slot_id(slot.slot_id) or normalize_slot_id(slot.label)
        if not slot_id:
            continue
        key = slot_id.casefold()
        if key in seen_slot_ids:
            continue
        seen_slot_ids.add(key)
        label = _as_text(slot.label) or slot_id.replace("_", " ").title()
        folder_id = normalize_slot_id(slot.folder_id) or slot_id
        rows.append(
            PermitDocumentSlot(
                slot_id=slot_id,
                label=label,
                required=bool(slot.required),
                status="missing",
                folder_id=folder_id,
                notes=_as_text(slot.notes),
            )
        )

    if rows:
        return rows

    normalized_type = normalize_permit_type(permit_type)
    defaults: list[PermitDocumentSlot] = []
    for slot_id, label, required in _document_slot_template_rows(normalized_type):
        defaults.append(
            PermitDocumentSlot(
                slot_id=slot_id,
                label=label,
                required=required,
                status="missing",
                folder_id=slot_id,
                notes="",
            )
        )
    return defaults


def build_default_document_slots(permit_type: Any) -> list[PermitDocumentSlot]:
    rows: list[PermitDocumentSlot] = []
    for slot_id, label, required in _document_slot_template_rows(permit_type):
        rows.append(
            PermitDocumentSlot(
                slot_id=slot_id,
                label=label,
                required=required,
                status="missing",
                folder_id=slot_id,
                notes="",
            )
        )
    return rows


def build_document_slots_from_template(
    template: DocumentChecklistTemplate | None,
    *,
    permit_type: Any,
) -> list[PermitDocumentSlot]:
    normalized_type = normalize_permit_type(permit_type)
    if template is None:
        return build_default_document_slots(normalized_type)
    source_rows = normalize_template_slots(template.slots, permit_type=normalized_type)
    rows: list[PermitDocumentSlot] = []
    for slot in source_rows:
        slot_id = normalize_slot_id(slot.slot_id) or normalize_slot_id(slot.label)
        if not slot_id:
            continue
        folder_id = normalize_slot_id(slot.folder_id) or slot_id
        rows.append(
            PermitDocumentSlot(
                slot_id=slot_id,
                label=_as_text(slot.label) or slot_id.replace("_", " ").title(),
                required=bool(slot.required),
                status="missing",
                folder_id=folder_id,
                notes=_as_text(slot.notes),
            )
        )
    if rows:
        return rows
    return build_default_document_slots(normalized_type)


def build_document_folders_from_slots(
    slots: list[PermitDocumentSlot],
) -> list[PermitDocumentFolder]:
    rows: list[PermitDocumentFolder] = []
    seen: set[str] = set()
    for slot in slots:
        folder_id = normalize_slot_id(slot.folder_id) or normalize_slot_id(slot.slot_id)
        if not folder_id:
            continue
        key = folder_id.casefold()
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            PermitDocumentFolder(
                folder_id=folder_id,
                name=_as_text(slot.label) or folder_id,
                parent_folder_id="",
            )
        )
    return rows


def ensure_default_document_structure(permit: PermitRecord) -> bool:
    changed = False

    if not permit.document_slots:
        permit.document_slots = build_default_document_slots(permit.permit_type)
        changed = True

    normalized_slots: list[PermitDocumentSlot] = []
    seen_slot_ids: set[str] = set()
    for slot in permit.document_slots:
        slot_id = normalize_slot_id(slot.slot_id) or normalize_slot_id(slot.label)
        if not slot_id:
            continue
        key = slot_id.casefold()
        if key in seen_slot_ids:
            continue
        seen_slot_ids.add(key)
        label = _as_text(slot.label) or slot_id.replace("_", " ").title()
        folder_id = normalize_slot_id(slot.folder_id) or slot_id
        status = normalize_slot_status(slot.status)
        normalized_slots.append(
            PermitDocumentSlot(
                slot_id=slot_id,
                label=label,
                required=bool(slot.required),
                status=status,
                folder_id=folder_id,
                active_cycle=_as_positive_int(slot.active_cycle),
                notes=_as_text(slot.notes),
            )
        )

    if normalized_slots != permit.document_slots:
        permit.document_slots = normalized_slots
        changed = True

    slot_folder_to_slot_id: dict[str, str] = {}
    for slot in permit.document_slots:
        folder_id = normalize_slot_id(slot.folder_id) or normalize_slot_id(slot.slot_id)
        if folder_id:
            slot_folder_to_slot_id[folder_id] = slot.slot_id

    max_revision_by_key: dict[tuple[str, int], int] = {}
    normalized_documents: list[PermitDocumentRecord] = []
    for document in sorted(
        permit.documents,
        key=lambda row: (
            normalize_slot_id(row.folder_id),
            _as_positive_int(row.cycle_index or _infer_cycle_index_from_relative_path(row.relative_path)),
            row.imported_at,
            row.document_id,
        ),
    ):
        folder_id = normalize_slot_id(document.folder_id)
        if not folder_id:
            continue
        slot_id = normalize_slot_id(document.slot_id) or slot_folder_to_slot_id.get(folder_id, "")
        cycle_index = _as_positive_int(
            document.cycle_index or _infer_cycle_index_from_relative_path(document.relative_path)
        )
        revision_key = (folder_id, cycle_index)
        revision_index = _as_positive_int(document.revision_index, default=0)
        next_revision = max_revision_by_key.get(revision_key, 0) + 1
        if revision_index < next_revision:
            revision_index = next_revision
        max_revision_by_key[revision_key] = max(max_revision_by_key.get(revision_key, 0), revision_index)
        normalized_documents.append(
            PermitDocumentRecord(
                document_id=_safe_uuid(document.document_id),
                folder_id=folder_id,
                original_name=_as_text(document.original_name),
                stored_name=_as_text(document.stored_name),
                relative_path=_as_text(document.relative_path),
                slot_id=slot_id,
                cycle_index=cycle_index,
                revision_index=revision_index,
                review_status=normalize_document_review_status(document.review_status),
                reviewed_at=_as_text(document.reviewed_at),
                review_note=_as_text(document.review_note),
                imported_at=_as_text(document.imported_at),
                byte_size=_as_non_negative_int(document.byte_size),
                sha256=_as_text(document.sha256),
            )
        )

    if normalized_documents != permit.documents:
        permit.documents = normalized_documents
        changed = True

    max_cycle_by_folder: dict[str, int] = {}
    for document in permit.documents:
        folder_id = normalize_slot_id(document.folder_id)
        if not folder_id:
            continue
        cycle_index = _as_positive_int(document.cycle_index)
        max_cycle_by_folder[folder_id] = max(max_cycle_by_folder.get(folder_id, 0), cycle_index)

    normalized_slots_with_cycle: list[PermitDocumentSlot] = []
    for slot in permit.document_slots:
        folder_id = normalize_slot_id(slot.folder_id) or normalize_slot_id(slot.slot_id)
        derived_cycle = max(_as_positive_int(slot.active_cycle), max_cycle_by_folder.get(folder_id, 0), 1)
        normalized_slot = PermitDocumentSlot(
            slot_id=slot.slot_id,
            label=slot.label,
            required=bool(slot.required),
            status=normalize_slot_status(slot.status),
            folder_id=folder_id or slot.slot_id,
            active_cycle=derived_cycle,
            notes=_as_text(slot.notes),
        )
        normalized_slots_with_cycle.append(normalized_slot)

    if normalized_slots_with_cycle != permit.document_slots:
        permit.document_slots = normalized_slots_with_cycle
        changed = True

    expected_folders = build_document_folders_from_slots(permit.document_slots)
    existing_by_id: dict[str, PermitDocumentFolder] = {
        folder.folder_id: folder
        for folder in permit.document_folders
        if _as_text(folder.folder_id)
    }

    merged_folders: list[PermitDocumentFolder] = []
    for expected in expected_folders:
        existing = existing_by_id.get(expected.folder_id)
        if existing is None:
            merged_folders.append(expected)
            changed = True
            continue
        desired_name = _as_text(existing.name) or expected.name
        merged = PermitDocumentFolder(
            folder_id=expected.folder_id,
            name=desired_name,
            parent_folder_id="",
        )
        if merged != existing:
            changed = True
        merged_folders.append(merged)

    if merged_folders != permit.document_folders:
        permit.document_folders = merged_folders
        changed = True

    return changed


def refresh_slot_status_from_documents(permit: PermitRecord) -> bool:
    changed = False
    documents_by_folder: dict[str, list[PermitDocumentRecord]] = {}
    max_cycle_by_folder: dict[str, int] = {}
    for document in permit.documents:
        folder_id = normalize_slot_id(document.folder_id)
        if not folder_id:
            continue

        normalized_cycle = _as_positive_int(document.cycle_index or _infer_cycle_index_from_relative_path(document.relative_path))
        normalized_revision = _as_positive_int(document.revision_index)
        normalized_review_status = normalize_document_review_status(document.review_status)
        if (
            normalized_cycle != document.cycle_index
            or normalized_revision != document.revision_index
            or normalized_review_status != document.review_status
        ):
            document.cycle_index = normalized_cycle
            document.revision_index = normalized_revision
            document.review_status = normalized_review_status
            changed = True

        documents_by_folder.setdefault(folder_id, []).append(document)
        max_cycle_by_folder[folder_id] = max(max_cycle_by_folder.get(folder_id, 0), normalized_cycle)

    for slot in permit.document_slots:
        folder_id = normalize_slot_id(slot.folder_id) or normalize_slot_id(slot.slot_id)
        if not folder_id:
            continue

        active_cycle = _as_positive_int(slot.active_cycle)
        max_cycle = max_cycle_by_folder.get(folder_id, 0)
        if max_cycle > active_cycle:
            active_cycle = max_cycle
            slot.active_cycle = active_cycle
            changed = True
        elif slot.active_cycle != active_cycle:
            slot.active_cycle = active_cycle
            changed = True

        slot_documents = documents_by_folder.get(folder_id, [])
        normalized_status = normalize_slot_status(slot.status)
        if not slot_documents:
            target_status = "missing"
        else:
            active_documents = [
                document
                for document in slot_documents
                if _as_positive_int(document.cycle_index) == active_cycle
            ]
            if not active_documents:
                target_status = "superseded"
            else:
                active_statuses = {
                    normalize_document_review_status(document.review_status)
                    for document in active_documents
                }
                if "accepted" in active_statuses:
                    target_status = "accepted"
                elif "uploaded" in active_statuses:
                    target_status = "uploaded"
                elif "rejected" in active_statuses:
                    target_status = "rejected"
                elif "superseded" in active_statuses:
                    target_status = "superseded"
                else:
                    target_status = "uploaded"

        if normalized_status != target_status:
            slot.status = target_status
            changed = True
        else:
            slot.status = normalized_status

    return changed


def document_file_count_by_slot(permit: PermitRecord) -> dict[str, int]:
    counts: dict[str, int] = {}
    folder_meta: dict[str, tuple[str, int]] = {}
    for slot in permit.document_slots:
        folder_id = normalize_slot_id(slot.folder_id) or normalize_slot_id(slot.slot_id)
        if folder_id:
            folder_meta[folder_id] = (slot.slot_id, _as_positive_int(slot.active_cycle))
            counts.setdefault(slot.slot_id, 0)
    for document in permit.documents:
        folder_id = normalize_slot_id(document.folder_id)
        meta = folder_meta.get(folder_id)
        if meta is None:
            continue
        slot_id, active_cycle = meta
        if _as_positive_int(document.cycle_index) != active_cycle:
            continue
        counts[slot_id] = counts.get(slot_id, 0) + 1
    return counts


@dataclass(slots=True)
class TrackerDataBundleV3:
    contacts: list[ContactRecord] = field(default_factory=list)
    jurisdictions: list[JurisdictionRecord] = field(default_factory=list)
    properties: list[PropertyRecord] = field(default_factory=list)
    permits: list[PermitRecord] = field(default_factory=list)
    document_templates: list[DocumentChecklistTemplate] = field(default_factory=list)
    active_document_template_ids: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any] | None) -> "TrackerDataBundleV3":
        if not isinstance(payload, Mapping):
            return cls()
        return cls(
            contacts=_parse_contacts(payload.get("contacts")),
            jurisdictions=_parse_jurisdictions(payload.get("jurisdictions")),
            properties=_parse_properties(payload.get("properties")),
            permits=_parse_permits(payload.get("permits")),
            document_templates=_parse_document_templates(
                payload.get("document_templates") or payload.get("checklist_templates")
            ),
            active_document_template_ids=_parse_active_document_template_ids(
                payload.get("active_document_template_ids") or payload.get("default_template_ids")
            ),
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "contacts": [record.to_mapping() for record in self.contacts],
            "jurisdictions": [record.to_mapping() for record in self.jurisdictions],
            "properties": [record.to_mapping() for record in self.properties],
            "permits": [record.to_mapping() for record in self.permits],
            "document_templates": [record.to_mapping() for record in self.document_templates],
            "active_document_template_ids": dict(self.active_document_template_ids),
        }

    def clone(self) -> "TrackerDataBundleV3":
        return TrackerDataBundleV3.from_payload(self.to_payload())


TrackerDataBundle = TrackerDataBundleV3


def _parse_contacts(value: Any) -> list[ContactRecord]:
    if not isinstance(value, list):
        return []
    rows: list[ContactRecord] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        record = ContactRecord.from_mapping(item)
        if not any((record.name, record.contact_methods, record.numbers, record.emails, record.roles)):
            continue
        rows.append(record)
    return rows


def _parse_jurisdictions(value: Any) -> list[JurisdictionRecord]:
    if not isinstance(value, list):
        return []
    rows: list[JurisdictionRecord] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        record = JurisdictionRecord.from_mapping(item)
        if not any((record.name, record.portal_urls, record.contact_ids, record.notes)):
            continue
        rows.append(record)
    return rows


def _parse_properties(value: Any) -> list[PropertyRecord]:
    if not isinstance(value, list):
        return []
    rows: list[PropertyRecord] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        record = PropertyRecord.from_mapping(item)
        if not any(
            (
                record.display_address,
                record.parcel_id,
                record.jurisdiction_id,
                record.contact_ids,
                record.list_color,
                record.tags,
                record.notes,
            )
        ):
            continue
        rows.append(record)
    return rows


def _parse_document_templates(value: Any) -> list[DocumentChecklistTemplate]:
    if not isinstance(value, list):
        return []
    rows: list[DocumentChecklistTemplate] = []
    seen_ids: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping):
            continue
        has_explicit_slots = isinstance(
            item.get("slots") or item.get("document_slots"),
            list,
        ) and bool(item.get("slots") or item.get("document_slots"))
        if not any((_as_text(item.get("name")), _as_text(item.get("notes")), has_explicit_slots)):
            continue
        record = DocumentChecklistTemplate.from_mapping(item)
        key = record.template_id.casefold()
        if key in seen_ids:
            continue
        seen_ids.add(key)
        rows.append(record)
    return rows


def _parse_active_document_template_ids(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    rows: dict[str, str] = {}
    for raw_permit_type, raw_template_id in value.items():
        permit_type = normalize_permit_type(raw_permit_type)
        template_id = _as_text(raw_template_id)
        if not template_id:
            continue
        rows[permit_type] = template_id
    return rows


def _parse_permit_parties(value: Any) -> list[PermitParty]:
    if not isinstance(value, list):
        return []
    rows: list[PermitParty] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        record = PermitParty.from_mapping(item)
        if not any((record.contact_id, record.role, record.note)):
            continue
        rows.append(record)
    return rows


def _parse_permit_events(value: Any) -> list[PermitEventRecord]:
    if not isinstance(value, list):
        return []
    rows: list[PermitEventRecord] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        record = PermitEventRecord.from_mapping(item)
        if not any((record.event_type, record.event_date, record.summary, record.detail)):
            continue
        rows.append(record)
    return rows


def _parse_permit_document_slots(value: Any) -> list[PermitDocumentSlot]:
    if not isinstance(value, list):
        return []
    rows: list[PermitDocumentSlot] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        record = PermitDocumentSlot.from_mapping(item)
        if not any((record.slot_id, record.label, record.folder_id)):
            continue
        rows.append(record)
    return rows


def _parse_permit_document_folders(value: Any) -> list[PermitDocumentFolder]:
    if not isinstance(value, list):
        return []
    rows: list[PermitDocumentFolder] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        entry = PermitDocumentFolder.from_mapping(item)
        if not entry.folder_id and not entry.name:
            continue
        rows.append(entry)
    return rows


def _parse_permit_documents(value: Any) -> list[PermitDocumentRecord]:
    if not isinstance(value, list):
        return []
    rows: list[PermitDocumentRecord] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        entry = PermitDocumentRecord.from_mapping(item)
        if not any(
            (
                entry.document_id,
                entry.folder_id,
                entry.original_name,
                entry.stored_name,
                entry.relative_path,
            )
        ):
            continue
        rows.append(entry)
    return rows


def _parse_permits(value: Any) -> list[PermitRecord]:
    if not isinstance(value, list):
        return []
    rows: list[PermitRecord] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        record = PermitRecord.from_mapping(item)
        if not any(
            (
                record.permit_id,
                record.property_id,
                record.permit_number,
                record.next_action_text,
                record.events,
                record.document_slots,
                record.documents,
            )
        ):
            continue
        rows.append(record)
    return rows
