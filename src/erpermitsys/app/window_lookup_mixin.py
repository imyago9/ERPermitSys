from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QLineEdit

from erpermitsys.app.tracker_models import ContactRecord, JurisdictionRecord, PermitRecord, PropertyRecord


class WindowLookupMixin:
    def _current_search(self, widget: QLineEdit | None) -> str:
        if widget is None:
            return ""
        return widget.text().strip().casefold()

    def _current_filter_value(self, combo: QComboBox | None) -> str:
        if combo is None:
            return "all"
        value = str(combo.currentData() or "").strip().lower()
        return value or "all"

    def _contact_by_id(self, contact_id: str) -> ContactRecord | None:
        target = str(contact_id or "").strip()
        if not target:
            return None
        for record in self._contacts:
            if record.contact_id == target:
                return record
        return None

    def _property_by_id(self, property_id: str) -> PropertyRecord | None:
        target = str(property_id or "").strip()
        if not target:
            return None
        for record in self._properties:
            if record.property_id == target:
                return record
        return None

    def _jurisdiction_by_id(self, jurisdiction_id: str) -> JurisdictionRecord | None:
        target = str(jurisdiction_id or "").strip()
        if not target:
            return None
        for record in self._jurisdictions:
            if record.jurisdiction_id == target:
                return record
        return None

    def _permit_by_id(self, permit_id: str) -> PermitRecord | None:
        target = str(permit_id or "").strip()
        if not target:
            return None
        for record in self._permits:
            if record.permit_id == target:
                return record
        return None

    def _selected_property(self) -> PropertyRecord | None:
        return self._property_by_id(self._selected_property_id)

    def _selected_permit(self) -> PermitRecord | None:
        return self._permit_by_id(self._selected_permit_id)

    def _permits_for_property(self, property_id: str) -> list[PermitRecord]:
        target = str(property_id or "").strip()
        if not target:
            return []
        return [record for record in self._permits if record.property_id == target]

