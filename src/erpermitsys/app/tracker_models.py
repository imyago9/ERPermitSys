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


@dataclass(slots=True)
class ContactRecord:
    name: str
    number: str = ""
    email: str = ""

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "ContactRecord":
        if not isinstance(value, Mapping):
            return cls(name="", number="", email="")
        return cls(
            name=_as_text(value.get("name")),
            number=_as_text(value.get("number")),
            email=_as_text(value.get("email")),
        )

    def to_mapping(self) -> dict[str, str]:
        return {
            "name": _as_text(self.name),
            "number": _as_text(self.number),
            "email": _as_text(self.email),
        }


@dataclass(slots=True)
class PermitRecord:
    parcel_id: str
    address: str
    request_date: str = ""
    application_date: str = ""
    completion_date: str = ""
    client_name: str = ""
    contractor_name: str = ""

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "PermitRecord":
        if not isinstance(value, Mapping):
            return cls(parcel_id="", address="")
        return cls(
            parcel_id=_as_text(value.get("parcel_id")),
            address=_as_text(value.get("address")),
            request_date=_as_text(value.get("request_date")),
            application_date=_as_text(value.get("application_date")),
            completion_date=_as_text(value.get("completion_date")),
            client_name=_as_text(value.get("client_name")),
            contractor_name=_as_text(value.get("contractor_name")),
        )

    def to_mapping(self) -> dict[str, str]:
        return {
            "parcel_id": _as_text(self.parcel_id),
            "address": _as_text(self.address),
            "request_date": _as_text(self.request_date),
            "application_date": _as_text(self.application_date),
            "completion_date": _as_text(self.completion_date),
            "client_name": _as_text(self.client_name),
            "contractor_name": _as_text(self.contractor_name),
        }


@dataclass(slots=True)
class TrackerDataBundle:
    clients: list[ContactRecord] = field(default_factory=list)
    contractors: list[ContactRecord] = field(default_factory=list)
    permits: list[PermitRecord] = field(default_factory=list)

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any] | None) -> "TrackerDataBundle":
        if not isinstance(payload, Mapping):
            return cls()
        return cls(
            clients=_parse_contacts(payload.get("clients")),
            contractors=_parse_contacts(payload.get("contractors")),
            permits=_parse_permits(payload.get("permits")),
        )

    def to_payload(self) -> dict[str, list[dict[str, str]]]:
        return {
            "clients": [record.to_mapping() for record in self.clients],
            "contractors": [record.to_mapping() for record in self.contractors],
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
                record.parcel_id,
                record.address,
                record.request_date,
                record.application_date,
                record.completion_date,
                record.client_name,
                record.contractor_name,
            )
        ):
            continue
        rows.append(record)
    return rows
