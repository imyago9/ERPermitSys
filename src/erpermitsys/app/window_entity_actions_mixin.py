from __future__ import annotations

from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl

from erpermitsys.app.tracker_models import PropertyRecord


class WindowEntityActionsMixin:
    def _confirm_duplicate_property_parcel(self, property_record: PropertyRecord) -> bool:
        if not property_record.parcel_id_norm:
            return True
        duplicate = next(
            (
                row
                for row in self._properties
                if row.parcel_id_norm
                and row.parcel_id_norm == property_record.parcel_id_norm
                and row.property_id != property_record.property_id
            ),
            None,
        )
        if duplicate is None:
            return True
        return self._confirm_dialog(
            "Duplicate Parcel",
            "Another address already uses this normalized parcel id.\n\n"
            f"Existing: {duplicate.display_address or '(no address)'}\n"
            f"New: {property_record.display_address or '(no address)'}\n\n"
            "Save anyway?",
            confirm_text="Save Anyway",
            cancel_text="Cancel",
        )

    def _edit_property_record(self, property_id: str) -> None:
        property_record = self._property_by_id(property_id)
        if property_record is None:
            return
        self._selected_property_id = property_record.property_id
        self._open_edit_property_view(property_record)

    def _delete_property_record(self, property_id: str) -> None:
        property_record = self._property_by_id(property_id)
        if property_record is None:
            return
        permits_for_property = [
            permit for permit in self._permits if permit.property_id == property_record.property_id
        ]
        delete_message = f"Delete address '{property_record.display_address or '(no address)'}'?"
        if permits_for_property:
            delete_message = (
                f"{delete_message}\n\n"
                f"This will also delete {len(permits_for_property)} permit(s) under this address."
            )
        confirmed = self._confirm_dialog(
            "Delete Address",
            delete_message,
            confirm_text="Delete",
            cancel_text="Cancel",
            danger=True,
        )
        if not confirmed:
            return
        for permit in permits_for_property:
            try:
                self._document_store.delete_permit_tree(permit)
            except Exception:
                pass
        self._properties = [row for row in self._properties if row.property_id != property_record.property_id]
        self._permits = [row for row in self._permits if row.property_id != property_record.property_id]
        if self._selected_property_id == property_record.property_id:
            self._selected_property_id = ""
        if self._selected_permit_id and self._permit_by_id(self._selected_permit_id) is None:
            self._selected_permit_id = ""
        self._selected_document_slot_id = ""
        self._selected_document_id = ""
        self._persist_tracker_data()
        self._refresh_all_views()

    def _edit_permit_record(self, permit_id: str) -> None:
        permit = self._permit_by_id(permit_id)
        if permit is None:
            return
        property_record = self._property_by_id(permit.property_id)
        if property_record is None:
            self._show_warning_dialog(
                "Missing Address",
                "This permit is not linked to a valid address record.",
            )
            return
        self._selected_property_id = property_record.property_id
        self._selected_permit_id = permit.permit_id
        self._open_edit_permit_view(property_record=property_record, permit=permit)

    def _delete_permit_record(self, permit_id: str) -> None:
        permit = self._permit_by_id(permit_id)
        if permit is None:
            return
        permit_label = permit.permit_number.strip() or "(no permit # yet)"
        confirmed = self._confirm_dialog(
            "Delete Permit",
            f"Delete permit '{permit_label}'?",
            confirm_text="Delete",
            cancel_text="Cancel",
            danger=True,
        )
        if not confirmed:
            return
        try:
            self._document_store.delete_permit_tree(permit)
        except Exception:
            pass
        self._permits = [row for row in self._permits if row.permit_id != permit.permit_id]
        if self._selected_permit_id == permit.permit_id:
            self._selected_permit_id = ""
        self._selected_document_slot_id = ""
        self._selected_document_id = ""
        self._persist_tracker_data()
        self._refresh_all_views()

    def _add_property(self) -> None:
        self._open_add_property_view()

    def _add_permit(self) -> None:
        self._open_add_permit_view()

    def _open_selected_portal(self) -> None:
        property_record = self._selected_property()
        permit = self._selected_permit()
        if permit is not None:
            property_record = self._property_by_id(permit.property_id) or property_record
        portal_url = self._portal_url_for_property(property_record)
        if not portal_url:
            self._show_info_dialog("Portal Missing", "No jurisdiction portal URL is configured.")
            return
        QDesktopServices.openUrl(QUrl(portal_url))

