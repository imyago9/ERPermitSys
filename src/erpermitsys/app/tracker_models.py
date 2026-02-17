from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


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


def _parse_text_list(value: Any, *, fallback: Any = None) -> list[str]:
    source = value
    if source is None or source == "":
        source = fallback
    rows: list[str] = []
    if isinstance(source, list):
        candidates = source
    elif isinstance(source, tuple):
        candidates = list(source)
    else:
        candidates = [source]
    for candidate in candidates:
        text = _as_text(candidate)
        if not text:
            continue
        for chunk in text.replace(";", "\n").replace(",", "\n").splitlines():
            normalized = chunk.strip()
            if not normalized:
                continue
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


@dataclass(slots=True)
class ContactRecord:
    name: str
    numbers: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "ContactRecord":
        if not isinstance(value, Mapping):
            return cls(name="", numbers=[], emails=[])
        return cls(
            name=_as_text(value.get("name")),
            numbers=_parse_text_list(value.get("numbers"), fallback=value.get("number")),
            emails=_parse_text_list(value.get("emails"), fallback=value.get("email")),
        )

    def to_mapping(self) -> dict[str, str | list[str]]:
        first_number = self.numbers[0] if self.numbers else ""
        first_email = self.emails[0] if self.emails else ""
        return {
            "name": _as_text(self.name),
            "numbers": [value for value in _parse_text_list(self.numbers)],
            "emails": [value for value in _parse_text_list(self.emails)],
            "number": _as_text(first_number),
            "email": _as_text(first_email),
        }


@dataclass(slots=True)
class CountyRecord:
    county_name: str
    portal_urls: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    numbers: list[str] = field(default_factory=list)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "CountyRecord":
        if not isinstance(value, Mapping):
            return cls(county_name="", portal_urls=[], emails=[], numbers=[])
        return cls(
            county_name=_as_text(value.get("county_name") or value.get("name")),
            portal_urls=_parse_text_list(
                value.get("portal_urls"),
                fallback=value.get("county_portal_url") or value.get("portal_url"),
            ),
            emails=_parse_text_list(
                value.get("emails"),
                fallback=value.get("county_email") or value.get("email"),
            ),
            numbers=_parse_text_list(
                value.get("numbers"),
                fallback=value.get("county_number") or value.get("number"),
            ),
        )

    def to_mapping(self) -> dict[str, str | list[str]]:
        first_url = self.portal_urls[0] if self.portal_urls else ""
        first_email = self.emails[0] if self.emails else ""
        first_number = self.numbers[0] if self.numbers else ""
        return {
            "county_name": _as_text(self.county_name),
            "portal_urls": [value for value in _parse_text_list(self.portal_urls)],
            "emails": [value for value in _parse_text_list(self.emails)],
            "numbers": [value for value in _parse_text_list(self.numbers)],
            "county_portal_url": _as_text(first_url),
            "county_email": _as_text(first_email),
            "county_number": _as_text(first_number),
            "portal_url": _as_text(first_url),
            "email": _as_text(first_email),
            "number": _as_text(first_number),
        }


@dataclass(slots=True)
class PermitDocumentFolder:
    folder_id: str
    name: str
    parent_folder_id: str = ""

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "PermitDocumentFolder":
        if not isinstance(value, Mapping):
            return cls(folder_id="", name="", parent_folder_id="")
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
            "imported_at": _as_text(self.imported_at),
            "byte_size": _as_non_negative_int(self.byte_size),
            "sha256": _as_text(self.sha256),
        }


@dataclass(slots=True)
class PermitRecord:
    parcel_id: str
    address: str
    permit_id: str = ""
    category: str = "building"
    request_date: str = ""
    application_date: str = ""
    completion_date: str = ""
    client_name: str = ""
    contractor_name: str = ""
    document_folders: list[PermitDocumentFolder] = field(default_factory=list)
    documents: list[PermitDocumentRecord] = field(default_factory=list)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "PermitRecord":
        if not isinstance(value, Mapping):
            return cls(parcel_id="", address="")
        return cls(
            permit_id=_as_text(value.get("permit_id")),
            parcel_id=_as_text(value.get("parcel_id")),
            address=_as_text(value.get("address")),
            category=_as_text(value.get("category")) or "building",
            request_date=_as_text(value.get("request_date")),
            application_date=_as_text(value.get("application_date")),
            completion_date=_as_text(value.get("completion_date")),
            client_name=_as_text(value.get("client_name")),
            contractor_name=_as_text(value.get("contractor_name")),
            document_folders=_parse_permit_document_folders(value.get("document_folders")),
            documents=_parse_permit_documents(value.get("documents")),
        )

    def to_mapping(self) -> dict[str, str | list[dict[str, str | int]]]:
        return {
            "permit_id": _as_text(self.permit_id),
            "parcel_id": _as_text(self.parcel_id),
            "address": _as_text(self.address),
            "category": _as_text(self.category) or "building",
            "request_date": _as_text(self.request_date),
            "application_date": _as_text(self.application_date),
            "completion_date": _as_text(self.completion_date),
            "client_name": _as_text(self.client_name),
            "contractor_name": _as_text(self.contractor_name),
            "document_folders": [entry.to_mapping() for entry in self.document_folders],
            "documents": [entry.to_mapping() for entry in self.documents],
        }


@dataclass(slots=True)
class TrackerDataBundle:
    clients: list[ContactRecord] = field(default_factory=list)
    contractors: list[ContactRecord] = field(default_factory=list)
    counties: list[CountyRecord] = field(default_factory=list)
    permits: list[PermitRecord] = field(default_factory=list)

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any] | None) -> "TrackerDataBundle":
        if not isinstance(payload, Mapping):
            return cls()
        return cls(
            clients=_parse_contacts(payload.get("clients")),
            contractors=_parse_contacts(payload.get("contractors")),
            counties=_parse_counties(payload.get("counties")),
            permits=_parse_permits(payload.get("permits")),
        )

    def to_payload(self) -> dict[str, list[dict[str, Any]]]:
        return {
            "clients": [record.to_mapping() for record in self.clients],
            "contractors": [record.to_mapping() for record in self.contractors],
            "counties": [record.to_mapping() for record in self.counties],
            "permits": [record.to_mapping() for record in self.permits],
        }

    def clone(self) -> "TrackerDataBundle":
        return TrackerDataBundle.from_payload(self.to_payload())


def _parse_contacts(value: Any) -> list[ContactRecord]:
    if not isinstance(value, list):
        return []
    rows: list[ContactRecord] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        record = ContactRecord.from_mapping(item)
        if not record.name:
            continue
        rows.append(record)
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
                record.parcel_id,
                record.address,
                record.request_date,
                record.application_date,
                record.completion_date,
                record.client_name,
                record.contractor_name,
                record.document_folders,
                record.documents,
            )
        ):
            continue
        rows.append(record)
    return rows


def _parse_counties(value: Any) -> list[CountyRecord]:
    if not isinstance(value, list):
        return []
    rows: list[CountyRecord] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        record = CountyRecord.from_mapping(item)
        if not any((record.county_name, record.portal_urls, record.emails, record.numbers)):
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
