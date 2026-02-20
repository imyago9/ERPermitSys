from __future__ import annotations

from datetime import date
from urllib.parse import urlsplit

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QListWidgetItem

from erpermitsys.app.permit_workspace_helpers import parse_iso_date as _parse_iso_date
from erpermitsys.app.tracker_models import (
    JurisdictionRecord,
    PermitRecord,
    PropertyRecord,
    compute_permit_status,
    ensure_default_document_structure,
    event_type_label,
    normalize_event_type,
    normalize_permit_type,
    normalize_slot_status,
    refresh_slot_status_from_documents,
)


def _permit_type_label(permit_type: str) -> str:
    normalized = normalize_permit_type(permit_type)
    if normalized == "demolition":
        return "Demolition"
    if normalized == "remodeling":
        return "Remodeling"
    return "Building"


class WindowWorkspaceListMixin:
    def _refresh_property_filters(self) -> None:
        combo = self._property_filter_combo
        if combo is None:
            return
        current = self._current_filter_value(combo)
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("All", "all")
        combo.addItem("Has Overdue", "overdue")
        combo.addItem("Has Missing Docs", "missing_docs")
        for jurisdiction in sorted(self._jurisdictions, key=lambda row: row.name.casefold()):
            combo.addItem(f"By Jurisdiction: {jurisdiction.name}", f"jurisdiction:{jurisdiction.jurisdiction_id}")
        index = combo.findData(current)
        if index < 0:
            index = 0
        combo.setCurrentIndex(index)
        combo.blockSignals(False)

    def _property_overdue_count(self, property_record: PropertyRecord) -> int:
        count = 0
        today = date.today()
        for permit in self._permits_for_property(property_record.property_id):
            due_date = _parse_iso_date(permit.next_action_due)
            if due_date is None:
                continue
            if permit.status in {"closed", "canceled", "finaled"}:
                continue
            if due_date < today:
                count += 1
        return count

    def _permit_missing_required_docs_count(self, permit: PermitRecord) -> int:
        ensure_default_document_structure(permit)
        refresh_slot_status_from_documents(permit)
        count = 0
        for slot in permit.document_slots:
            if not slot.required:
                continue
            status = normalize_slot_status(slot.status)
            if status in {"missing", "rejected", "superseded"}:
                count += 1
        return count

    def _property_missing_docs_count(self, property_record: PropertyRecord) -> int:
        count = 0
        for permit in self._permits_for_property(property_record.property_id):
            count += self._permit_missing_required_docs_count(permit)
        return count

    def _property_matches_filter(self, property_record: PropertyRecord, filter_mode: str) -> bool:
        if filter_mode == "overdue":
            return self._property_overdue_count(property_record) > 0
        if filter_mode == "missing_docs":
            return self._property_missing_docs_count(property_record) > 0
        if filter_mode.startswith("jurisdiction:"):
            target_jurisdiction = filter_mode.split(":", 1)[1].strip()
            return target_jurisdiction and property_record.jurisdiction_id == target_jurisdiction
        return True

    def _refresh_property_list(self) -> None:
        widget = self._properties_list_widget
        if widget is None:
            return
        list_stack = self._properties_list_stack
        empty_label = self._properties_empty_label

        search = self._current_search(self._property_search_input)
        filter_mode = self._current_filter_value(self._property_filter_combo)

        candidates = sorted(
            self._properties,
            key=lambda row: (
                row.display_address.casefold(),
                row.parcel_id.casefold(),
                row.property_id,
            ),
        )
        filtered: list[PropertyRecord] = []
        for record in candidates:
            if search:
                haystack = " ".join((
                    record.display_address,
                    record.parcel_id,
                    record.parcel_id_norm,
                    record.notes,
                )).casefold()
                if search not in haystack:
                    continue
            if not self._property_matches_filter(record, filter_mode):
                continue
            filtered.append(record)

        selected_id = self._selected_property_id

        widget.blockSignals(True)
        widget.clear()
        for property_record in filtered:
            jurisdiction = self._jurisdiction_by_id(property_record.jurisdiction_id)
            jurisdiction_name = (
                jurisdiction.name
                if jurisdiction is not None and jurisdiction.name.strip()
                else "Unassigned"
            )
            overdue_count = self._property_overdue_count(property_record)
            missing_docs = self._property_missing_docs_count(property_record)
            subtitle = f"{property_record.parcel_id or '(no parcel)'}  •  {jurisdiction_name}"
            badges = f"{overdue_count} overdue  •  {missing_docs} missing docs"
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, property_record.property_id)
            widget.addItem(item)
            card = self._build_tracker_entity_card(
                title=property_record.display_address or "(no address)",
                title_field="address",
                subtitle=subtitle,
                subtitle_field="parcel",
                meta=badges,
                meta_field="request",
                accent_color=property_record.list_color,
                on_edit=lambda property_id=property_record.property_id: self._edit_property_record(property_id),
                on_remove=lambda property_id=property_record.property_id: self._delete_property_record(property_id),
            )
            item_hint = card.sizeHint()
            row_height = max(
                item_hint.height(),
                card.minimumSizeHint().height(),
                card.minimumHeight(),
            )
            item_hint.setHeight(row_height + 6)
            item.setSizeHint(item_hint)
            widget.setItemWidget(item, card)

        if selected_id and any(row.property_id == selected_id for row in filtered):
            self._select_property_item(selected_id)
        else:
            self._selected_property_id = filtered[0].property_id if filtered else ""
            if self._selected_property_id:
                self._select_property_item(self._selected_property_id)

        widget.blockSignals(False)
        self._set_admin_list_card_selection(widget)
        self._set_result_label(self._property_result_label, shown=len(filtered), total=len(candidates), noun="addresses")

        if list_stack is not None and empty_label is not None:
            if filtered:
                list_stack.setCurrentWidget(widget)
            else:
                if not candidates:
                    message = "No addresses yet.\nClick Add Address to create your first property."
                elif search:
                    message = "No addresses match this search.\nTry another search or clear filters."
                elif filter_mode == "overdue":
                    message = "No addresses with overdue next actions."
                elif filter_mode == "missing_docs":
                    message = "No addresses with missing required documents."
                elif filter_mode.startswith("jurisdiction:"):
                    message = "No addresses found for this jurisdiction filter."
                else:
                    message = "No addresses available for the current filters."
                empty_label.setText(message)
                list_stack.setCurrentWidget(empty_label)

        self._refresh_permit_list()

    def _select_property_item(self, property_id: str) -> None:
        widget = self._properties_list_widget
        if widget is None:
            return
        target = str(property_id or "").strip()
        for index in range(widget.count()):
            item = widget.item(index)
            if str(item.data(Qt.ItemDataRole.UserRole) or "").strip() != target:
                continue
            widget.setCurrentItem(item)
            self._set_admin_list_card_selection(widget)
            return

    def _on_property_selection_changed(self) -> None:
        widget = self._properties_list_widget
        self._set_admin_list_card_selection(widget)
        current_item = widget.currentItem() if widget is not None else None
        selected_id = str(current_item.data(Qt.ItemDataRole.UserRole) or "").strip() if current_item else ""
        if selected_id:
            self._set_left_column_expanded_panel("permit")
        self._timeline_debug(
            "property_selection_changed",
            selected_property_id=selected_id,
            previous_property_id=self._selected_property_id,
        )
        if selected_id == self._selected_property_id:
            return
        self._selected_property_id = selected_id
        self._selected_permit_id = ""
        self._selected_document_slot_id = ""
        self._selected_document_id = ""
        self._refresh_permit_list()
        self._refresh_selected_permit_view()

    def _sync_permit_type_buttons(self) -> None:
        for permit_type, button in self._permit_type_buttons.items():
            button.setChecked(self._active_permit_type_filter == permit_type)

    def _set_active_permit_type_filter(self, permit_type: str) -> None:
        normalized = str(permit_type or "").strip().lower()
        if normalized not in {"all", "building", "demolition", "remodeling"}:
            normalized = "all"
        if self._active_permit_type_filter == normalized:
            self._sync_permit_type_buttons()
            return
        self._active_permit_type_filter = normalized
        self._sync_permit_type_buttons()
        self._refresh_permit_list()

    def _permit_matches_filter(self, permit: PermitRecord, filter_mode: str) -> bool:
        status = normalize_event_type(permit.status)
        if filter_mode == "open":
            return status not in {"closed", "canceled", "finaled"}
        if filter_mode == "closed":
            return status in {"closed", "canceled", "finaled"}
        if filter_mode == "overdue":
            due_date = _parse_iso_date(permit.next_action_due)
            if due_date is None:
                return False
            return due_date < date.today() and status not in {"closed", "canceled", "finaled"}
        if filter_mode != "all":
            return status == filter_mode
        return True

    def _refresh_permit_list(self, *, keep_selected_visible: bool = False) -> None:
        widget = self._permits_list_widget
        if widget is None:
            return
        list_stack = self._permits_list_stack
        empty_label = self._permits_empty_label

        selected_property = self._selected_property()
        selected_id = str(self._selected_permit_id or "").strip()
        selected_permit = self._permit_by_id(selected_id) if selected_id else None
        if keep_selected_visible and selected_permit is not None:
            if selected_property is None or selected_property.property_id != selected_permit.property_id:
                selected_property = self._property_by_id(selected_permit.property_id)
                if selected_property is not None:
                    self._selected_property_id = selected_property.property_id
        properties_count = len(self._properties)
        permits_for_property = (
            self._permits_for_property(selected_property.property_id)
            if selected_property is not None
            else []
        )
        if (
            keep_selected_visible
            and selected_permit is not None
            and all(row.permit_id != selected_permit.permit_id for row in permits_for_property)
        ):
            permits_for_property.append(selected_permit)

        if self._permit_header_label is not None:
            if properties_count <= 0:
                self._permit_header_label.setText("Add an address to start tracking permits")
            elif selected_property is None:
                self._permit_header_label.setText("Select an address to view permits")
            else:
                self._permit_header_label.setText(
                    f"For: {selected_property.display_address or '(no address)'}"
                )
        has_selected_property = selected_property is not None
        if self._permit_controls_host is not None:
            self._permit_controls_host.setVisible(has_selected_property)
        if self._permit_type_picker_host is not None:
            self._permit_type_picker_host.setVisible(has_selected_property)
        if self._add_permit_button is not None:
            self._add_permit_button.setEnabled(has_selected_property)
            self._add_permit_button.setToolTip(
                "" if has_selected_property else "Select an address first"
            )

        filter_mode = self._current_filter_value(self._permit_filter_combo)
        search = self._current_search(self._permit_search_input)
        type_filter = self._active_permit_type_filter
        self._timeline_debug(
            "refresh_permit_list_start",
            keep_selected_visible=keep_selected_visible,
            selected_property_id=selected_property.property_id if selected_property is not None else "",
            selected_permit_id=selected_id,
            filter_mode=filter_mode,
            type_filter=type_filter,
            search=search,
            permits_for_property_count=len(permits_for_property),
        )

        filtered: list[PermitRecord] = []
        selected_permit_filter_details: dict[str, object] = {}
        for permit in permits_for_property:
            ensure_default_document_structure(permit)
            refresh_slot_status_from_documents(permit)
            permit.status = compute_permit_status(permit.events, fallback=permit.status)

            matches_filters = True
            reasons: list[str] = []
            if type_filter != "all" and normalize_permit_type(permit.permit_type) != type_filter:
                matches_filters = False
                reasons.append("type_filter")
            if matches_filters and not self._permit_matches_filter(permit, filter_mode):
                matches_filters = False
                reasons.append("status_filter")
            if search:
                haystack = " ".join(
                    (
                        permit.permit_number,
                        permit.status,
                        permit.next_action_text,
                        permit.next_action_due,
                        permit.request_date,
                    )
                ).casefold()
                if search not in haystack:
                    matches_filters = False
                    reasons.append("search")
            if selected_id and permit.permit_id == selected_id:
                selected_permit_filter_details = {
                    "permit_id": permit.permit_id,
                    "status": permit.status,
                    "permit_type": permit.permit_type,
                    "matches_filters": matches_filters,
                    "reasons": reasons,
                    "event_count": len(permit.events),
                }
            if not matches_filters:
                if not (
                    keep_selected_visible
                    and selected_id
                    and permit.permit_id == selected_id
                ):
                    continue
            filtered.append(permit)

        filtered.sort(
            key=lambda row: (
                row.next_action_due or "9999-12-31",
                row.permit_number.casefold(),
                row.permit_id,
            )
        )

        widget.blockSignals(True)
        widget.clear()
        for permit in filtered:
            permit_number = permit.permit_number or "(no permit # yet)"
            status_text = event_type_label(permit.status)
            due_text = permit.next_action_due or "No due date"
            missing_docs_count = self._permit_missing_required_docs_count(permit)
            subtitle = f"{_permit_type_label(permit.permit_type)}  •  {status_text}"
            meta_parts = [f"Due {due_text}"]
            if missing_docs_count > 0:
                meta_parts.append(f"{missing_docs_count} missing docs")
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, permit.permit_id)
            widget.addItem(item)
            card = self._build_tracker_entity_card(
                title=permit_number,
                title_field="document",
                subtitle=subtitle,
                subtitle_field="parcel",
                meta=" | ".join(meta_parts),
                meta_field="request",
                on_edit=lambda permit_id=permit.permit_id: self._edit_permit_record(permit_id),
                on_remove=lambda permit_id=permit.permit_id: self._delete_permit_record(permit_id),
            )
            next_action_text = str(permit.next_action_text or "").strip()
            if next_action_text:
                card.setToolTip(f"Next action: {next_action_text}")
            item_hint = card.sizeHint()
            row_height = max(
                item_hint.height(),
                card.minimumSizeHint().height(),
                card.minimumHeight(),
            )
            item_hint.setHeight(row_height + 6)
            item.setSizeHint(item_hint)
            widget.setItemWidget(item, card)

        if selected_id and any(row.permit_id == selected_id for row in filtered):
            self._select_permit_item(selected_id)
        else:
            self._selected_permit_id = filtered[0].permit_id if filtered else ""
            if self._selected_permit_id:
                self._select_permit_item(self._selected_permit_id)

        widget.blockSignals(False)
        self._set_admin_list_card_selection(widget)
        self._set_result_label(self._permit_result_label, shown=len(filtered), total=len(permits_for_property), noun="permits")

        if list_stack is not None and empty_label is not None:
            if filtered:
                list_stack.setCurrentWidget(widget)
            else:
                if properties_count <= 0:
                    message = "No permits yet.\nCreate your first address above to unlock permits."
                elif selected_property is None:
                    message = "Select an address above to see permits for that property."
                elif not permits_for_property:
                    message = (
                        f"No permits for:\n{selected_property.display_address or '(no address)'}\n"
                        "Click Add Permit to create the first one."
                    )
                elif search:
                    message = "No permits match this search.\nTry another search or clear filters."
                elif filter_mode != "all":
                    message = "No permits match the selected status filter."
                elif type_filter != "all":
                    message = f"No {_permit_type_label(type_filter)} permits for this address."
                else:
                    message = "No permits available for the current filters."
                empty_label.setText(message)
                list_stack.setCurrentWidget(empty_label)

        self._timeline_debug(
            "refresh_permit_list_done",
            keep_selected_visible=keep_selected_visible,
            selected_permit_before=selected_id,
            selected_permit_after=self._selected_permit_id,
            selected_permit_filter_details=selected_permit_filter_details,
            filtered_count=len(filtered),
            filtered_ids=[row.permit_id for row in filtered[:20]],
            list_widget_count=widget.count(),
        )
        self._refresh_selected_permit_view()

    def _select_permit_item(self, permit_id: str) -> None:
        widget = self._permits_list_widget
        if widget is None:
            return
        target = str(permit_id or "").strip()
        for index in range(widget.count()):
            item = widget.item(index)
            if str(item.data(Qt.ItemDataRole.UserRole) or "").strip() != target:
                continue
            widget.setCurrentItem(item)
            self._set_admin_list_card_selection(widget)
            self._timeline_debug(
                "select_permit_item_found",
                permit_id=target,
                index=index,
                widget_count=widget.count(),
            )
            return
        self._timeline_debug(
            "select_permit_item_missing",
            permit_id=target,
            widget_count=widget.count(),
        )

    def _on_permit_selection_changed(self) -> None:
        widget = self._permits_list_widget
        self._set_admin_list_card_selection(widget)
        current_item = widget.currentItem() if widget is not None else None
        selected_id = str(current_item.data(Qt.ItemDataRole.UserRole) or "").strip() if current_item else ""
        self._timeline_debug(
            "permit_selection_changed",
            widget_count=widget.count() if widget is not None else 0,
            selected_id=selected_id,
            previously_selected_id=self._selected_permit_id,
        )
        if selected_id == self._selected_permit_id:
            self._refresh_selected_permit_view()
            return
        self._selected_permit_id = selected_id
        self._selected_document_slot_id = ""
        self._selected_document_id = ""
        self._refresh_selected_permit_view()

    def _portal_url_for_property(self, property_record: PropertyRecord | None) -> str:
        if property_record is None:
            return ""
        jurisdiction = self._jurisdiction_by_id(property_record.jurisdiction_id)
        if jurisdiction is None or not jurisdiction.portal_urls:
            return ""
        return jurisdiction.portal_urls[0].strip()

    def _workspace_contacts_portal_text(
        self,
        *,
        property_record: PropertyRecord | None,
        permit: PermitRecord | None,
        jurisdiction: JurisdictionRecord | None,
        portal_url: str,
    ) -> str:
        contact_preview = ""
        ordered_contact_ids: list[str] = []
        seen_contact_ids: set[str] = set()

        if permit is not None:
            for party in permit.parties:
                contact_id = str(party.contact_id or "").strip()
                if not contact_id or contact_id in seen_contact_ids:
                    continue
                seen_contact_ids.add(contact_id)
                ordered_contact_ids.append(contact_id)
        if property_record is not None:
            for contact_id in property_record.contact_ids:
                normalized_id = str(contact_id or "").strip()
                if not normalized_id or normalized_id in seen_contact_ids:
                    continue
                seen_contact_ids.add(normalized_id)
                ordered_contact_ids.append(normalized_id)
        if jurisdiction is not None:
            for contact_id in jurisdiction.contact_ids:
                normalized_id = str(contact_id or "").strip()
                if not normalized_id or normalized_id in seen_contact_ids:
                    continue
                seen_contact_ids.add(normalized_id)
                ordered_contact_ids.append(normalized_id)

        if ordered_contact_ids:
            contact_names: list[str] = []
            for contact_id in ordered_contact_ids:
                contact = self._contact_by_id(contact_id)
                if contact is None:
                    continue
                name = contact.name.strip()
                if not name:
                    continue
                contact_names.append(name)
            if contact_names:
                contact_preview = ", ".join(contact_names[:2])
                if len(contact_names) > 2:
                    contact_preview = f"{contact_preview}, +{len(contact_names) - 2}"
        portal_preview = ""
        if portal_url:
            try:
                parsed = urlsplit(portal_url)
                portal_preview = parsed.netloc.strip() or parsed.path.strip() or portal_url
            except Exception:
                portal_preview = portal_url
        if contact_preview and portal_preview:
            return f"{contact_preview} | {portal_preview}"
        if contact_preview:
            return contact_preview
        if portal_preview:
            return portal_preview
        return "No contacts or portal"

    def _refresh_selected_permit_view(self) -> None:
        permit = self._selected_permit()
        self._refresh_document_template_apply_options()
        property_record = self._selected_property()
        if permit is not None:
            property_record = self._property_by_id(permit.property_id) or property_record
        property_has_permits = bool(
            property_record is not None and self._permits_for_property(property_record.property_id)
        )
        if property_record is None:
            self._set_workspace_next_step_hint(
                "Next Action: Select an address in Address List, then choose an existing permit or click Add Permit."
            )
        elif not property_has_permits:
            self._set_workspace_next_step_hint(
                "Next Action: Click Add Permit to create the first permit for this address."
            )
        else:
            self._set_workspace_next_step_hint("")
        self._timeline_debug(
            "refresh_selected_permit_view_start",
            permit_id=permit.permit_id if permit is not None else "",
            property_id=property_record.property_id if property_record is not None else "",
            permit_event_count=len(permit.events) if permit is not None else 0,
        )
        self._set_permit_workspace_blur(permit is None)
        self._sync_timeline_mode_chrome(permit)
        any_properties = bool(self._properties)
        any_permits = bool(self._permits)

        if permit is None:
            selected_jurisdiction = (
                self._jurisdiction_by_id(property_record.jurisdiction_id)
                if property_record is not None
                else None
            )
            selected_portal = self._portal_url_for_property(property_record)
            contacts_portal_text = self._workspace_contacts_portal_text(
                property_record=property_record,
                permit=None,
                jurisdiction=selected_jurisdiction,
                portal_url=selected_portal,
            )
            if not any_properties:
                self._set_workspace_title_state("Add Your First Address")
            elif property_record is None:
                self._set_workspace_title_state("Select Address")
            elif not any_permits:
                self._set_workspace_title_state("Add Permit")
            else:
                self._set_workspace_title_state("Select Permit")
            if not any_properties:
                self._set_workspace_info_values(
                    address="No address yet",
                    parcel="—",
                    jurisdiction="—",
                    permit_number="—",
                    status="Not Started",
                    contacts_portal="Add jurisdiction portal in Admin",
                )
            elif property_record is None:
                self._set_workspace_info_values(
                    address="Select an address",
                    parcel="—",
                    jurisdiction="—",
                    permit_number="—",
                    status="Awaiting Selection",
                    contacts_portal="—",
                )
            elif not self._permits_for_property(property_record.property_id):
                self._set_workspace_info_values(
                    address=property_record.display_address or "(no address)",
                    parcel=property_record.parcel_id or "(no parcel)",
                    jurisdiction=(
                        selected_jurisdiction.name
                        if selected_jurisdiction is not None and selected_jurisdiction.name.strip()
                        else "Unassigned"
                    ),
                    permit_number="No permits yet",
                    status="Awaiting Permit",
                    contacts_portal=contacts_portal_text,
                )
            else:
                self._set_workspace_info_values(
                    address=property_record.display_address or "(no address)",
                    parcel=property_record.parcel_id or "(no parcel)",
                    jurisdiction=(
                        selected_jurisdiction.name
                        if selected_jurisdiction is not None and selected_jurisdiction.name.strip()
                        else "Unassigned"
                    ),
                    permit_number="Select permit",
                    status="Awaiting Selection",
                    contacts_portal=contacts_portal_text,
                )
            if self._next_action_label is not None:
                self._next_action_label.setText(
                    "No permit selected.\nSelect a permit, then set the next action and due date."
                )
            if self._open_portal_button is not None:
                self._open_portal_button.setEnabled(False)
            if self._set_next_action_button is not None:
                self._set_next_action_button.setEnabled(False)
            if self._add_event_button is not None:
                self._add_event_button.setEnabled(False)
            if self._document_status_label is not None:
                self._document_status_label.setText(
                    "Select a permit to manage its checklist and files."
                )
            self._refresh_timeline_list(None)
            self._refresh_document_slots(None)
            self._timeline_debug(
                "refresh_selected_permit_view_done",
                permit_id="",
                mode="empty_state",
            )
            return

        permit.status = compute_permit_status(permit.events, fallback=permit.status)

        property_record = self._property_by_id(permit.property_id)
        parcel_id = property_record.parcel_id if property_record is not None else ""
        permit_number_text = permit.permit_number.strip() or "(no permit # yet)"
        self._set_workspace_title_state(f"Viewing: {permit_number_text}")

        jurisdiction = self._jurisdiction_by_id(property_record.jurisdiction_id) if property_record else None
        jurisdiction_name = jurisdiction.name if jurisdiction is not None else "Unassigned"
        portal_url = self._portal_url_for_property(property_record)
        contacts_portal_text = self._workspace_contacts_portal_text(
            property_record=property_record,
            permit=permit,
            jurisdiction=jurisdiction,
            portal_url=portal_url,
        )

        self._set_workspace_info_values(
            address=property_record.display_address if property_record is not None else "(unknown)",
            parcel=parcel_id or "(none)",
            jurisdiction=jurisdiction_name,
            permit_number=permit.permit_number or "(no permit # yet)",
            status=event_type_label(permit.status),
            contacts_portal=contacts_portal_text,
        )

        if self._open_portal_button is not None:
            self._open_portal_button.setEnabled(bool(portal_url))
        if self._set_next_action_button is not None:
            self._set_next_action_button.setEnabled(True)
        if self._add_event_button is not None:
            self._add_event_button.setEnabled(True)

        next_action_text = permit.next_action_text or "No next action set."
        due_text = permit.next_action_due or "No due date"
        if self._next_action_label is not None:
            self._next_action_label.setText(f"{next_action_text}\nDue: {due_text}")

        self._refresh_timeline_list(permit)
        self._refresh_document_slots(permit)
        self._timeline_debug(
            "refresh_selected_permit_view_done",
            permit_id=permit.permit_id,
            permit_status=permit.status,
            permit_event_count=len(permit.events),
        )
