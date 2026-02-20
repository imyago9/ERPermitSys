from __future__ import annotations

from typing import Callable, Sequence

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QColorDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from erpermitsys.app.permit_workspace_helpers import (
    join_multi_values as _join_multi_values,
    parse_multi_values as _parse_multi_values,
)
from erpermitsys.app.tracker_models import ContactMethodRecord, normalize_list_color
from erpermitsys.app.window_admin_shared import (
    _ADMIN_LIST_COLOR_PRESETS,
    _dot_ring_color,
    _hex_color_channels,
    _mix_color_channels,
    _normalize_card_tint_channels,
    _rgba_text,
)
from erpermitsys.app.window_bound_service import WindowBoundService
from erpermitsys.ui.widgets import TrackerHoverEntityCard


class WindowAdminLayoutService(WindowBoundService):
    def _open_contacts_and_jurisdictions_dialog(self, *, preferred_tab: str = "contacts") -> None:
        if self._panel_stack is None or self._panel_admin_view is None:
            return
        currently_admin = self._panel_stack.currentWidget() is self._panel_admin_view
        tab_key = str(preferred_tab or "").strip().casefold()
        action_label = "Open Document Templates" if tab_key == "templates" else "Open Admin Panel"
        if not currently_admin:
            if not self._confirm_discard_template_changes(action_label=action_label):
                return
            if not self._confirm_discard_inline_form_changes(action_label=action_label):
                return
        self._refresh_admin_views()
        self._refresh_document_templates_view()
        tabs = self._admin_tabs
        if tabs is not None:
            if tab_key == "jurisdictions":
                target_index = 1 if tabs.count() > 1 else 0
            elif tab_key == "templates" and self._admin_templates_tab_index >= 0:
                target_index = self._admin_templates_tab_index
            else:
                target_index = 0
            self._set_admin_tab_index(target_index, skip_confirmation=not currently_admin)
        self._panel_stack.setCurrentWidget(self._panel_admin_view)
        self._sync_foreground_layout()

    def _set_admin_tab_index(self, index: int, *, skip_confirmation: bool) -> None:
        tabs = self._admin_tabs
        if tabs is None:
            return
        tab_count = max(0, int(tabs.count()))
        if tab_count <= 0:
            self._admin_active_tab_index = 0
            return
        target = max(0, min(int(index), tab_count - 1))
        self._admin_tab_change_guard = bool(skip_confirmation)
        try:
            tabs.setCurrentIndex(target)
        finally:
            self._admin_tab_change_guard = False
        self._admin_active_tab_index = max(0, int(tabs.currentIndex()))

    def _on_admin_tab_changed(self, index: int) -> None:
        tabs = self._admin_tabs
        if tabs is None:
            return
        tab_count = max(0, int(tabs.count()))
        if tab_count <= 0:
            self._admin_active_tab_index = 0
            return
        target_index = max(0, min(int(index), tab_count - 1))
        previous_index = max(0, min(int(self._admin_active_tab_index), tab_count - 1))
        if self._admin_tab_change_guard:
            self._admin_active_tab_index = target_index
            return
        if target_index == previous_index:
            self._admin_active_tab_index = target_index
            return

        target_title = str(tabs.tabText(target_index) or "selected tab").strip() or "selected tab"
        if not self._confirm_discard_admin_view_changes(action_label=f"Switch to {target_title}"):
            self._admin_tab_change_guard = True
            try:
                tabs.setCurrentIndex(previous_index)
            finally:
                self._admin_tab_change_guard = False
            self._admin_active_tab_index = previous_index
            return
        self._admin_active_tab_index = target_index

    def _close_contacts_and_jurisdictions_view(self) -> None:
        if self._panel_stack is None or self._panel_home_view is None:
            return
        if not self._confirm_discard_admin_view_changes(action_label="Back to Tracker"):
            return
        self._panel_stack.setCurrentWidget(self._panel_home_view)
        self._refresh_all_views()
        self._sync_foreground_layout()

    def _refresh_admin_views(self) -> None:
        if self._admin_contacts_list_widget is None or self._admin_jurisdictions_list_widget is None:
            return
        self._refresh_admin_contacts_list(select_id=self._admin_selected_contact_id)
        self._refresh_admin_jurisdictions_list(select_id=self._admin_selected_jurisdiction_id)
        selected_jurisdiction = self._jurisdiction_by_id(self._admin_selected_jurisdiction_id)
        if selected_jurisdiction is None:
            self._refresh_admin_jurisdiction_contacts_picker(
                selected_ids=self._admin_jurisdiction_attached_contact_ids
            )
        else:
            self._refresh_admin_jurisdiction_contacts_picker(selected_ids=selected_jurisdiction.contact_ids)
        self._admin_set_contact_form_mode(
            editing=self._contact_by_id(self._admin_selected_contact_id) is not None
        )
        self._admin_set_jurisdiction_form_mode(
            editing=self._jurisdiction_by_id(self._admin_selected_jurisdiction_id) is not None
        )
        self._set_admin_contact_bundle_fields_open(
            self._admin_contact_bundle_fields_open,
            animate=False,
        )
        self._refresh_admin_contact_methods_list()
        self._sync_admin_contact_dirty_state()
        self._sync_admin_jurisdiction_dirty_state()
        self._sync_admin_editor_field_widths()

    def _set_widget_max_width(self, widget: QWidget | None, width: int, *, min_width: int = 220) -> None:
        if widget is None:
            return
        widget.setMaximumWidth(max(int(min_width), int(width)))

    def _list_item_size_hint_for_card(self, card: QWidget) -> object:
        hint = card.sizeHint()

        min_width = max(0, int(card.minimumWidth()))
        if min_width > 0:
            hint.setWidth(max(hint.width(), min_width))
        max_width = int(card.maximumWidth())
        if 0 < max_width < 16777215:
            hint.setWidth(min(hint.width(), max_width))

        min_height = max(0, int(card.minimumHeight()))
        if min_height > 0:
            hint.setHeight(max(hint.height(), min_height))
        max_height = int(card.maximumHeight())
        if 0 < max_height < 16777215:
            hint.setHeight(min(hint.height(), max_height))

        return hint

    def _sync_admin_editor_field_widths(self) -> None:
        contact_form = self._admin_contact_form_widget
        if contact_form is not None and contact_form.width() > 0:
            contact_half = max(280, int(contact_form.width() * 0.75))
            contact_quarter = max(96, int(contact_form.width() * 0.25))
            contact_color_shell_width = max(240, contact_quarter + 156)
            for shell in self._admin_contact_field_shells:
                self._set_widget_max_width(shell, contact_half)
            self._set_widget_max_width(self._admin_contact_name_input, contact_half)
            self._set_widget_max_width(self._admin_contact_roles_input, contact_half)
            self._set_widget_max_width(self._admin_contact_bundle_fields_host, contact_half)
            self._set_widget_max_width(self._admin_contact_methods_host, contact_half)
            self._set_widget_max_width(
                self._admin_contact_color_shell,
                contact_color_shell_width,
                min_width=240,
            )
            self._set_widget_max_width(
                self._admin_contact_color_picker_host,
                contact_quarter,
                min_width=96,
            )
            self._set_admin_entity_color_picker_open(
                "contact",
                self._admin_contact_color_picker_open,
                animate=False,
            )

        jurisdiction_form = self._admin_jurisdiction_form_widget
        if jurisdiction_form is not None and jurisdiction_form.width() > 0:
            jurisdiction_half = max(260, int(jurisdiction_form.width() * 0.5))
            jurisdiction_quarter = max(96, int(jurisdiction_form.width() * 0.25))
            jurisdiction_color_shell_width = max(240, jurisdiction_quarter + 156)
            for shell in self._admin_jurisdiction_field_shells:
                self._set_widget_max_width(shell, jurisdiction_half)
            self._set_widget_max_width(self._admin_jurisdiction_name_input, jurisdiction_half)
            self._set_widget_max_width(self._admin_jurisdiction_type_combo, jurisdiction_half)
            self._set_widget_max_width(self._admin_jurisdiction_parent_input, jurisdiction_half)
            self._set_widget_max_width(self._admin_jurisdiction_portals_input, jurisdiction_half)
            self._set_widget_max_width(self._admin_jurisdiction_vendor_input, jurisdiction_half)
            self._set_widget_max_width(self._admin_jurisdiction_notes_input, jurisdiction_half)
            self._set_widget_max_width(self._admin_jurisdiction_fields_host, jurisdiction_half)
            self._set_widget_max_width(self._admin_jurisdiction_attached_panel, jurisdiction_half)
            add_button_width = 0
            if self._admin_jurisdiction_contact_add_button is not None:
                add_button_width = self._admin_jurisdiction_contact_add_button.width() or 48
            picker_width = max(180, jurisdiction_half - add_button_width - 12)
            self._set_widget_max_width(self._admin_jurisdiction_contact_picker_combo, picker_width)
            self._set_widget_max_width(self._admin_jurisdiction_attached_picker_host, jurisdiction_half)
            self._set_widget_max_width(self._admin_jurisdiction_attached_contacts_host, jurisdiction_half)
            self._set_widget_max_width(
                self._admin_jurisdiction_color_shell,
                jurisdiction_color_shell_width,
                min_width=240,
            )
            self._set_widget_max_width(
                self._admin_jurisdiction_color_picker_host,
                jurisdiction_quarter,
                min_width=96,
            )
            self._set_admin_entity_color_picker_open(
                "jurisdiction",
                self._admin_jurisdiction_color_picker_open,
                animate=False,
            )

        template_form = self._template_form_widget
        if template_form is not None and template_form.width() > 0:
            template_half = max(280, int(template_form.width() * 0.75))
            for shell in self._template_field_shells:
                self._set_widget_max_width(shell, template_half)
            self._set_widget_max_width(self._template_name_input, template_half)
            self._set_widget_max_width(self._template_type_combo, template_half)
            self._set_widget_max_width(self._template_notes_input, template_half)
            self._set_widget_max_width(self._template_slot_label_input, template_half)
            self._set_widget_max_width(self._template_slot_required_combo, template_half)
            self._set_widget_max_width(self._template_slots_list_widget, template_half)

    def _contact_method_rows_snapshot(
        self, rows: Sequence[ContactMethodRecord]
    ) -> tuple[tuple[str, tuple[str, ...], tuple[str, ...], str], ...]:
        snapshot_rows: list[tuple[str, tuple[str, ...], tuple[str, ...], str]] = []
        for row in rows:
            label = str(row.label or "").strip()
            emails = tuple(_parse_multi_values(_join_multi_values(row.emails)))
            numbers = tuple(_parse_multi_values(_join_multi_values(row.numbers)))
            note = str(row.note or "").strip()
            if not any((label, emails, numbers, note)):
                continue
            snapshot_rows.append((label, emails, numbers, note))
        return tuple(snapshot_rows)

    def _admin_contact_form_snapshot(self) -> tuple[object, ...]:
        name = self._admin_contact_name_input.text().strip() if self._admin_contact_name_input else ""
        roles = (
            tuple(_parse_multi_values(self._admin_contact_roles_input.text()))
            if self._admin_contact_roles_input
            else tuple()
        )
        pending_numbers = (
            tuple(_parse_multi_values(self._admin_contact_numbers_input.text()))
            if self._admin_contact_numbers_input
            else tuple()
        )
        pending_emails = (
            tuple(_parse_multi_values(self._admin_contact_emails_input.text()))
            if self._admin_contact_emails_input
            else tuple()
        )
        pending_label = (
            self._admin_contact_bundle_name_input.text().strip()
            if self._admin_contact_bundle_name_input
            else ""
        )
        pending_note = self._admin_contact_note_input.text().strip() if self._admin_contact_note_input else ""
        methods = self._contact_method_rows_snapshot(self._admin_contact_method_rows)
        list_color = normalize_list_color(self._admin_contact_list_color)
        return (
            name,
            roles,
            methods,
            pending_label,
            pending_numbers,
            pending_emails,
            pending_note,
            self._admin_contact_editing_bundle_index,
            list_color,
        )

    def _admin_jurisdiction_form_snapshot(self) -> tuple[object, ...]:
        name = (
            self._admin_jurisdiction_name_input.text().strip()
            if self._admin_jurisdiction_name_input is not None
            else ""
        )
        jurisdiction_type = (
            str(self._admin_jurisdiction_type_combo.currentData() or "city")
            if self._admin_jurisdiction_type_combo is not None
            else "city"
        )
        parent_county = (
            self._admin_jurisdiction_parent_input.text().strip()
            if self._admin_jurisdiction_parent_input is not None
            else ""
        )
        portal_urls = (
            tuple(_parse_multi_values(self._admin_jurisdiction_portals_input.text()))
            if self._admin_jurisdiction_portals_input is not None
            else tuple()
        )
        portal_vendor = (
            self._admin_jurisdiction_vendor_input.text().strip()
            if self._admin_jurisdiction_vendor_input is not None
            else ""
        )
        notes = (
            self._admin_jurisdiction_notes_input.text().strip()
            if self._admin_jurisdiction_notes_input is not None
            else ""
        )
        contact_ids = tuple(sorted(self._admin_selected_jurisdiction_contact_ids()))
        list_color = normalize_list_color(self._admin_jurisdiction_list_color)
        return (
            name,
            jurisdiction_type,
            parent_county,
            portal_urls,
            portal_vendor,
            notes,
            contact_ids,
            list_color,
        )

    def _set_admin_contact_dirty(self, dirty: bool) -> None:
        self._admin_contact_dirty = bool(dirty)
        if hasattr(self, "_admin_state"):
            self._admin_state.contact_dirty = self._admin_contact_dirty
        self._set_admin_dirty_bubble_state(
            self._admin_contact_dirty_bubble,
            state=self._admin_contact_dirty_bubble_state(),
        )
        self._admin_set_contact_form_mode(editing=self._admin_contact_editing_mode)

    def _set_admin_jurisdiction_dirty(self, dirty: bool) -> None:
        self._admin_jurisdiction_dirty = bool(dirty)
        if hasattr(self, "_admin_state"):
            self._admin_state.jurisdiction_dirty = self._admin_jurisdiction_dirty
        self._set_admin_dirty_bubble_state(
            self._admin_jurisdiction_dirty_bubble,
            state=self._admin_jurisdiction_dirty_bubble_state(),
        )
        self._admin_set_jurisdiction_form_mode(editing=self._admin_jurisdiction_editing_mode)

    def _admin_contact_bubble_is_empty(self) -> bool:
        snapshot = self._admin_contact_form_snapshot()
        if len(snapshot) < 9:
            return False
        name = str(snapshot[0] or "").strip()
        roles = tuple(snapshot[1]) if isinstance(snapshot[1], tuple) else tuple()
        methods = tuple(snapshot[2]) if isinstance(snapshot[2], tuple) else tuple()
        pending_label = str(snapshot[3] or "").strip()
        pending_numbers = tuple(snapshot[4]) if isinstance(snapshot[4], tuple) else tuple()
        pending_emails = tuple(snapshot[5]) if isinstance(snapshot[5], tuple) else tuple()
        pending_note = str(snapshot[6] or "").strip()
        editing_bundle_index = int(snapshot[7]) if isinstance(snapshot[7], int) else -1
        list_color = normalize_list_color(snapshot[8] if len(snapshot) >= 9 else "")
        return not any(
            (
                name,
                roles,
                methods,
                pending_label,
                pending_numbers,
                pending_emails,
                pending_note,
                editing_bundle_index >= 0,
                list_color,
            )
        )

    def _admin_jurisdiction_bubble_is_empty(self) -> bool:
        snapshot = self._admin_jurisdiction_form_snapshot()
        if len(snapshot) < 8:
            return False
        name = str(snapshot[0] or "").strip()
        jurisdiction_type = str(snapshot[1] or "").strip().casefold()
        parent_county = str(snapshot[2] or "").strip()
        portal_urls = tuple(snapshot[3]) if isinstance(snapshot[3], tuple) else tuple()
        portal_vendor = str(snapshot[4] or "").strip()
        notes = str(snapshot[5] or "").strip()
        contact_ids = tuple(snapshot[6]) if isinstance(snapshot[6], tuple) else tuple()
        list_color = normalize_list_color(snapshot[7] if len(snapshot) >= 8 else "")
        return not any(
            (
                name,
                jurisdiction_type not in {"", "city"},
                parent_county,
                portal_urls,
                portal_vendor,
                notes,
                contact_ids,
                list_color,
            )
        )

    def _admin_contact_dirty_bubble_state(self) -> str:
        if self._admin_contact_dirty:
            return "dirty"
        if not self._admin_contact_editing_mode and self._admin_contact_bubble_is_empty():
            return "empty"
        return "clean"

    def _admin_jurisdiction_dirty_bubble_state(self) -> str:
        if self._admin_jurisdiction_dirty:
            return "dirty"
        if not self._admin_jurisdiction_editing_mode and self._admin_jurisdiction_bubble_is_empty():
            return "empty"
        return "clean"

    def _set_admin_dirty_bubble_state(self, bubble: QLabel | None, *, state: str) -> None:
        if bubble is None:
            return
        normalized_state = str(state or "").strip().casefold()
        if normalized_state not in {"dirty", "clean", "empty"}:
            normalized_state = "clean"
        bubble_text = {
            "dirty": "Unsaved",
            "clean": "Saved",
            "empty": "Empty",
        }.get(normalized_state, "Saved")
        bubble.setText(bubble_text)
        bubble.setProperty("dirtyState", normalized_state)
        style = bubble.style()
        style.unpolish(bubble)
        style.polish(bubble)
        bubble.update()

    def _rebase_admin_contact_dirty_tracking(self) -> None:
        self._admin_contact_baseline_snapshot = self._admin_contact_form_snapshot()
        self._set_admin_contact_dirty(False)

    def _rebase_admin_jurisdiction_dirty_tracking(self) -> None:
        self._admin_jurisdiction_baseline_snapshot = self._admin_jurisdiction_form_snapshot()
        self._set_admin_jurisdiction_dirty(False)

    def _sync_admin_contact_dirty_state(self) -> None:
        if self._admin_contact_form_loading:
            return
        if not self._admin_contact_baseline_snapshot:
            self._rebase_admin_contact_dirty_tracking()
            return
        self._set_admin_contact_dirty(
            self._admin_contact_form_snapshot() != self._admin_contact_baseline_snapshot
        )

    def _sync_admin_jurisdiction_dirty_state(self) -> None:
        if self._admin_jurisdiction_form_loading:
            return
        if not self._admin_jurisdiction_baseline_snapshot:
            self._rebase_admin_jurisdiction_dirty_tracking()
            return
        self._set_admin_jurisdiction_dirty(
            self._admin_jurisdiction_form_snapshot() != self._admin_jurisdiction_baseline_snapshot
        )

    def _on_admin_contact_form_changed(self, *_args: object) -> None:
        self._sync_admin_contact_dirty_state()

    def _on_admin_jurisdiction_form_changed(self, *_args: object) -> None:
        self._sync_admin_jurisdiction_dirty_state()

    def _on_admin_contacts_search_changed(self, *_args: object) -> None:
        if hasattr(self, "_admin_state") and self._admin_contacts_search_input is not None:
            self._admin_state.contacts_search_text = self._admin_contacts_search_input.text().strip()
        self._refresh_admin_contacts_list(select_id=self._admin_selected_contact_id)

    def _on_admin_jurisdictions_search_changed(self, *_args: object) -> None:
        if hasattr(self, "_admin_state") and self._admin_jurisdictions_search_input is not None:
            self._admin_state.jurisdictions_search_text = (
                self._admin_jurisdictions_search_input.text().strip()
            )
        self._refresh_admin_jurisdictions_list(select_id=self._admin_selected_jurisdiction_id)

    def _on_templates_search_changed(self, *_args: object) -> None:
        if hasattr(self, "_admin_state") and self._templates_search_input is not None:
            self._admin_state.templates_search_text = self._templates_search_input.text().strip()
        self._refresh_templates_list(select_id=self._template_selected_id)

    def _admin_contact_panel_dirty_excluding_bundle_edit(self) -> bool:
        baseline = self._admin_contact_baseline_snapshot
        if not baseline:
            return False
        current = self._admin_contact_form_snapshot()
        if not isinstance(current, tuple) or not isinstance(baseline, tuple):
            return self._admin_contact_dirty and not self._admin_contact_bundle_edit_is_dirty()
        if len(current) != len(baseline):
            return self._admin_contact_dirty and not self._admin_contact_bundle_edit_is_dirty()
        # Ignore bundle-edit buffer fields while editing an existing bundle so the panel
        # check only tracks true form changes outside that temporary edit state.
        if self._admin_contact_editing_bundle_index >= 0:
            normalized_current = list(current)
            for index in (3, 4, 5, 6, 7):
                normalized_current[index] = baseline[index]
            return tuple(normalized_current) != baseline
        return current != baseline

    def _confirm_discard_admin_contact_panel_changes(self, *, action_label: str) -> bool:
        if not self._admin_contact_panel_dirty_excluding_bundle_edit():
            return True
        return self._confirm_dialog(
            "Unsaved Contact Changes",
            (
                "You have unsaved changes in the contact form. "
                f"Discard them and continue with '{action_label}'?"
            ),
            confirm_text="Discard Changes",
            cancel_text="Keep Editing",
            danger=True,
        )

    def _confirm_discard_admin_view_changes(self, *, action_label: str) -> bool:
        if not self._confirm_discard_admin_contact_editor_changes(action_label=action_label):
            return False
        if not self._confirm_discard_admin_jurisdiction_changes(action_label=action_label):
            return False
        if not self._confirm_discard_template_changes(action_label=action_label):
            return False
        return True

    def _confirm_discard_admin_contact_editor_changes(self, *, action_label: str) -> bool:
        if self._admin_contact_bundle_edit_is_dirty():
            if not self._confirm_discard_admin_contact_bundle_changes(action_label=action_label):
                return False
        if not self._confirm_discard_admin_contact_panel_changes(action_label=action_label):
            return False
        return True

    def _confirm_discard_admin_contact_changes(self, *, action_label: str) -> bool:
        if not self._admin_contact_dirty:
            return True
        return self._confirm_dialog(
            "Unsaved Contact Changes",
            (
                "You have unsaved changes in the contact form. "
                f"Discard them and continue with '{action_label}'?"
            ),
            confirm_text="Discard Changes",
            cancel_text="Keep Editing",
            danger=True,
        )

    def _confirm_discard_admin_jurisdiction_changes(self, *, action_label: str) -> bool:
        if not self._admin_jurisdiction_dirty:
            return True
        return self._confirm_dialog(
            "Unsaved Jurisdiction Changes",
            (
                "You have unsaved changes in the jurisdiction form. "
                f"Discard them and continue with '{action_label}'?"
            ),
            confirm_text="Discard Changes",
            cancel_text="Keep Editing",
            danger=True,
        )

    def _on_admin_add_new_contact_clicked(self) -> None:
        self._admin_new_contact(require_confirm=True, action_label="Add New Contact")

    def _on_admin_add_new_jurisdiction_clicked(self) -> None:
        self._admin_new_jurisdiction(require_confirm=True, action_label="Add New Jurisdiction")

    def _admin_entity_color_values(self, entity_kind: str) -> tuple[str, str]:
        normalized_entity = str(entity_kind or "").strip().casefold()
        if normalized_entity == "contact":
            return (
                normalize_list_color(self._admin_contact_list_color),
                normalize_list_color(self._admin_contact_custom_list_color),
            )
        if normalized_entity == "property":
            return (
                normalize_list_color(self._add_property_list_color),
                normalize_list_color(self._add_property_custom_list_color),
            )
        return (
            normalize_list_color(self._admin_jurisdiction_list_color),
            normalize_list_color(self._admin_jurisdiction_custom_list_color),
        )

    def _admin_set_entity_color_values(self, entity_kind: str, *, selected: str, custom: str) -> None:
        normalized_entity = str(entity_kind or "").strip().casefold()
        if normalized_entity == "contact":
            self._admin_contact_list_color = normalize_list_color(selected)
            self._admin_contact_custom_list_color = normalize_list_color(custom)
            return
        if normalized_entity == "property":
            self._add_property_list_color = normalize_list_color(selected)
            self._add_property_custom_list_color = normalize_list_color(custom)
            return
        self._admin_jurisdiction_list_color = normalize_list_color(selected)
        self._admin_jurisdiction_custom_list_color = normalize_list_color(custom)

    def _admin_entity_color_controls(
        self, entity_kind: str
    ) -> tuple[dict[str, QPushButton], QPushButton | None, QLabel | None]:
        normalized_entity = str(entity_kind or "").strip().casefold()
        if normalized_entity == "contact":
            return (
                self._admin_contact_color_buttons,
                self._admin_contact_custom_color_dot,
                self._admin_contact_color_selected_label,
            )
        if normalized_entity == "property":
            return (
                self._add_property_color_buttons,
                self._add_property_custom_color_dot,
                self._add_property_color_selected_label,
            )
        return (
            self._admin_jurisdiction_color_buttons,
            self._admin_jurisdiction_custom_color_dot,
            self._admin_jurisdiction_color_selected_label,
        )

    def _admin_entity_color_picker_controls(
        self, entity_kind: str
    ) -> tuple[QPushButton | None, QWidget | None, QPropertyAnimation | None]:
        normalized_entity = str(entity_kind or "").strip().casefold()
        if normalized_entity == "contact":
            return (
                self._admin_contact_color_toggle_button,
                self._admin_contact_color_content_host,
                self._admin_contact_color_animation,
            )
        if normalized_entity == "property":
            return (
                self._add_property_color_toggle_button,
                self._add_property_color_content_host,
                self._add_property_color_animation,
            )
        return (
            self._admin_jurisdiction_color_toggle_button,
            self._admin_jurisdiction_color_content_host,
            self._admin_jurisdiction_color_animation,
        )

    def _admin_entity_color_picker_is_open(self, entity_kind: str) -> bool:
        normalized_entity = str(entity_kind or "").strip().casefold()
        if normalized_entity == "contact":
            return bool(self._admin_contact_color_picker_open)
        if normalized_entity == "property":
            return bool(self._add_property_color_picker_open)
        return bool(self._admin_jurisdiction_color_picker_open)

    def _admin_color_picker_content_target_height(self, host: QWidget | None) -> int:
        if host is None:
            return 0
        hint = host.sizeHint().height()
        if hint <= 0 and host.layout() is not None:
            hint = host.layout().sizeHint().height()
        return max(0, hint)

    def _set_admin_entity_color_picker_open(
        self,
        entity_kind: str,
        expanded: bool,
        *,
        animate: bool,
    ) -> None:
        normalized_entity = str(entity_kind or "").strip().casefold()
        toggle_button, content_host, animation = self._admin_entity_color_picker_controls(normalized_entity)
        if content_host is None or toggle_button is None:
            return
        target_height = self._admin_color_picker_content_target_height(content_host) if expanded else 0
        if animate and animation is not None:
            animation.stop()
            animation.setStartValue(content_host.maximumHeight())
            animation.setEndValue(target_height)
            animation.start()
        else:
            content_host.setMaximumHeight(target_height)

        if normalized_entity == "contact":
            self._admin_contact_color_picker_open = bool(expanded)
        elif normalized_entity == "property":
            self._add_property_color_picker_open = bool(expanded)
        else:
            self._admin_jurisdiction_color_picker_open = bool(expanded)
        toggle_button.setText("v" if expanded else "^")
        toggle_button.setToolTip("Collapse list color picker" if expanded else "Expand list color picker")

    def _toggle_admin_entity_color_picker(self, entity_kind: str) -> None:
        self._set_admin_entity_color_picker_open(
            entity_kind,
            not self._admin_entity_color_picker_is_open(entity_kind),
            animate=True,
        )

    def _set_admin_color_dot_button_style(
        self,
        button: QPushButton,
        *,
        color_hex: str,
        selected: bool,
        placeholder: bool = False,
    ) -> None:
        normalized = normalize_list_color(color_hex)
        if placeholder or not normalized:
            border = "rgba(99, 124, 150, 186)"
            if selected:
                border = "rgba(214, 230, 247, 234)"
            button.setStyleSheet(
                (
                    "QPushButton {"
                    "border-radius: 10px;"
                    f"border: 2px dashed {border};"
                    "background: rgba(0, 0, 0, 0);"
                    "padding: 0px;"
                    "}"
                    "QPushButton:hover {"
                    f"border: 2px dashed {border};"
                    "background: rgba(0, 0, 0, 0);"
                    "}"
                )
            )
            button.setText("")
            return

        channels = _normalize_card_tint_channels(_hex_color_channels(normalized))
        border = _dot_ring_color(channels, selected=selected)
        hover_border = _dot_ring_color(channels, selected=True)
        border_width = 3 if selected else 1
        button.setStyleSheet(
            (
                "QPushButton {"
                "border-radius: 10px;"
                f"border: {border_width}px solid {border};"
                f"background: {_rgba_text(channels, 255)};"
                "padding: 0px;"
                "}"
                "QPushButton:hover {"
                f"border: {max(2, border_width)}px solid {hover_border};"
                f"background: {_rgba_text(channels, 255)};"
                "}"
                "QPushButton:disabled {"
                "border: 1px dashed rgba(121, 146, 173, 186);"
                f"background: {_rgba_text(channels, 104)};"
                "}"
            )
        )
        button.setText("")

    def _refresh_admin_color_picker_controls(self, entity_kind: str) -> None:
        selected, custom = self._admin_entity_color_values(entity_kind)
        color_buttons, custom_dot, selected_label = self._admin_entity_color_controls(entity_kind)
        preset_set = set(_ADMIN_LIST_COLOR_PRESETS)
        for color_hex, button in color_buttons.items():
            self._set_admin_color_dot_button_style(
                button,
                color_hex=color_hex,
                selected=(selected == color_hex),
            )
            button.setToolTip(f"Use {color_hex}")

        if custom_dot is not None:
            if custom:
                custom_dot.setEnabled(True)
                self._set_admin_color_dot_button_style(
                    custom_dot,
                    color_hex=custom,
                    selected=(selected == custom),
                )
                custom_dot.setToolTip(f"Use custom color {custom}")
            else:
                custom_dot.setEnabled(False)
                self._set_admin_color_dot_button_style(
                    custom_dot,
                    color_hex="",
                    selected=False,
                    placeholder=True,
                )
                custom_dot.setToolTip("Pick a custom color first.")

        if selected_label is not None:
            if selected:
                selected_suffix = " (custom)" if selected == custom and selected not in preset_set else ""
                selected_label.setText(f"Selected: {selected}{selected_suffix}")
            else:
                selected_label.setText("Selected: Theme default")
        self._set_admin_entity_color_picker_open(
            entity_kind,
            self._admin_entity_color_picker_is_open(entity_kind),
            animate=False,
        )

    def _set_admin_entity_list_color(
        self,
        *,
        entity_kind: str,
        color_hex: str,
        custom_color: str | None = None,
        notify: bool,
    ) -> None:
        selected_current, custom_current = self._admin_entity_color_values(entity_kind)
        selected = normalize_list_color(color_hex)
        if custom_color is None:
            custom = custom_current
        else:
            custom = normalize_list_color(custom_color)

        if selected and selected not in _ADMIN_LIST_COLOR_PRESETS and not custom:
            custom = selected
        if not selected and custom_color == "":
            custom = ""

        if selected == selected_current and custom == custom_current:
            self._refresh_admin_color_picker_controls(entity_kind)
            return

        self._admin_set_entity_color_values(entity_kind, selected=selected, custom=custom)
        self._refresh_admin_color_picker_controls(entity_kind)
        if not notify:
            return
        normalized_entity = str(entity_kind or "").strip().casefold()
        if normalized_entity == "contact":
            self._on_admin_contact_form_changed()
        elif normalized_entity == "property":
            self._on_inline_property_form_changed()
        else:
            self._on_admin_jurisdiction_form_changed()

    def _on_admin_color_preset_clicked(self, entity_kind: str, color_hex: str) -> None:
        selected, _custom = self._admin_entity_color_values(entity_kind)
        normalized = normalize_list_color(color_hex)
        if not normalized:
            return
        next_color = "" if selected == normalized else normalized
        self._set_admin_entity_list_color(
            entity_kind=entity_kind,
            color_hex=next_color,
            notify=True,
        )

    def _on_admin_custom_color_dot_clicked(self, entity_kind: str) -> None:
        selected, custom = self._admin_entity_color_values(entity_kind)
        if not custom:
            return
        next_color = "" if selected == custom else custom
        self._set_admin_entity_list_color(
            entity_kind=entity_kind,
            color_hex=next_color,
            notify=True,
        )

    def _open_admin_custom_color_picker(self, entity_kind: str) -> None:
        selected, custom = self._admin_entity_color_values(entity_kind)
        initial_hex = selected or custom or _ADMIN_LIST_COLOR_PRESETS[0]
        initial_color = QColor(initial_hex)
        normalized_entity = str(entity_kind or "").strip().casefold()
        if normalized_entity == "contact":
            title_prefix = "Contact"
        elif normalized_entity == "property":
            title_prefix = "Address"
        else:
            title_prefix = "Jurisdiction"
        picked = QColorDialog.getColor(initial_color, self, f"Pick {title_prefix} List Color")
        if not picked.isValid():
            return
        picked_hex = normalize_list_color(picked.name(QColor.NameFormat.HexRgb))
        if not picked_hex:
            return
        self._set_admin_entity_list_color(
            entity_kind=entity_kind,
            color_hex=picked_hex,
            custom_color=picked_hex,
            notify=True,
        )

    def _build_admin_color_picker_widget(self, *, parent: QWidget, entity_kind: str) -> QWidget:
        normalized_entity = str(entity_kind or "").strip().casefold()
        host = QWidget(parent)
        if normalized_entity == "property":
            host.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        else:
            host.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        host_layout = QVBoxLayout(host)
        host_layout.setContentsMargins(0, 0, 0, 0)
        host_layout.setSpacing(6)

        toggle_host = QWidget(host)
        toggle_host.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        toggle_row = QHBoxLayout(toggle_host)
        toggle_row.setContentsMargins(0, 0, 0, 0)
        toggle_row.setSpacing(6)
        toggle_row.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        toggle_button = QPushButton("^", toggle_host)
        toggle_button.setObjectName("TrackerPanelActionButton")
        toggle_button.setFixedSize(34, 30)
        toggle_button.clicked.connect(
            lambda _checked=False, kind=normalized_entity: self._toggle_admin_entity_color_picker(kind)
        )
        toggle_row.addStretch(1)
        toggle_row.addWidget(toggle_button, 0)
        host_layout.addWidget(toggle_host, 0)

        content_host = QWidget(host)
        if normalized_entity == "property":
            content_host.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        else:
            content_host.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        content_layout = QVBoxLayout(content_host)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(7)

        presets_host = QWidget(content_host)
        presets_layout = QGridLayout(presets_host)
        presets_layout.setContentsMargins(0, 0, 0, 0)
        presets_layout.setHorizontalSpacing(6)
        presets_layout.setVerticalSpacing(6)

        color_buttons: dict[str, QPushButton] = {}
        for index, color_hex in enumerate(_ADMIN_LIST_COLOR_PRESETS):
            dot_button = QPushButton("", presets_host)
            dot_button.setFixedSize(22, 22)
            dot_button.setCursor(Qt.CursorShape.PointingHandCursor)
            dot_button.clicked.connect(
                lambda _checked=False, kind=normalized_entity, value=color_hex: self._on_admin_color_preset_clicked(
                    kind, value
                )
            )
            color_buttons[color_hex] = dot_button
            presets_layout.addWidget(dot_button, index // 6, index % 6)
        content_layout.addWidget(presets_host, 0)

        custom_host = QWidget(content_host)
        custom_row = QHBoxLayout(custom_host)
        custom_row.setContentsMargins(0, 0, 0, 0)
        custom_row.setSpacing(8)

        custom_button = QPushButton("Custom Color...", custom_host)
        custom_button.setObjectName("TrackerPanelActionButton")
        custom_button.setMinimumHeight(30)
        custom_button.clicked.connect(
            lambda _checked=False, kind=normalized_entity: self._open_admin_custom_color_picker(kind)
        )
        custom_row.addWidget(custom_button, 0)

        custom_dot = QPushButton("", custom_host)
        custom_dot.setFixedSize(22, 22)
        custom_dot.setCursor(Qt.CursorShape.PointingHandCursor)
        custom_dot.clicked.connect(
            lambda _checked=False, kind=normalized_entity: self._on_admin_custom_color_dot_clicked(kind)
        )
        custom_row.addWidget(custom_dot, 0)

        selected_label = QLabel("Selected: Theme default", custom_host)
        selected_label.setObjectName("TrackerPanelMeta")
        selected_label.setWordWrap(True)
        custom_row.addWidget(selected_label, 1)
        content_layout.addWidget(custom_host, 0)
        host_layout.addWidget(content_host, 0)

        picker_animation = QPropertyAnimation(content_host, b"maximumHeight", content_host)
        picker_animation.setDuration(170)
        picker_animation.setEasingCurve(QEasingCurve.Type.OutCubic)

        if normalized_entity == "contact":
            self._admin_contact_color_buttons = color_buttons
            self._admin_contact_custom_color_button = custom_button
            self._admin_contact_custom_color_dot = custom_dot
            self._admin_contact_color_selected_label = selected_label
            self._admin_contact_color_toggle_button = toggle_button
            self._admin_contact_color_content_host = content_host
            self._admin_contact_color_animation = picker_animation
        elif normalized_entity == "jurisdiction":
            self._admin_jurisdiction_color_buttons = color_buttons
            self._admin_jurisdiction_custom_color_button = custom_button
            self._admin_jurisdiction_custom_color_dot = custom_dot
            self._admin_jurisdiction_color_selected_label = selected_label
            self._admin_jurisdiction_color_toggle_button = toggle_button
            self._admin_jurisdiction_color_content_host = content_host
            self._admin_jurisdiction_color_animation = picker_animation
        else:
            self._add_property_color_buttons = color_buttons
            self._add_property_custom_color_button = custom_button
            self._add_property_custom_color_dot = custom_dot
            self._add_property_color_selected_label = selected_label
            self._add_property_color_toggle_button = toggle_button
            self._add_property_color_content_host = content_host
            self._add_property_color_animation = picker_animation
        self._set_admin_entity_color_picker_open(normalized_entity, False, animate=False)
        self._refresh_admin_color_picker_controls(normalized_entity)
        return host

    def _apply_admin_entity_card_color_style(self, card: QFrame, *, color_hex: str) -> None:
        normalized = normalize_list_color(color_hex)
        if not normalized:
            card.setStyleSheet("")
            return

        tint = _normalize_card_tint_channels(_hex_color_channels(normalized))
        border_idle = _mix_color_channels(tint, (26, 38, 52), 0.22)
        border_hover = _mix_color_channels(tint, (26, 38, 52), 0.12)
        border_selected = _mix_color_channels(tint, (244, 250, 255), 0.18)

        card.setStyleSheet(
            (
                f"QFrame#TrackerListCard {{"
                f"border: 1px solid {_rgba_text(border_idle, 188)};"
                f"background: {_rgba_text(tint, 42)};"
                "}"
                f"QFrame#TrackerListCard:hover {{"
                f"border: 1px solid {_rgba_text(border_hover, 214)};"
                f"background: {_rgba_text(tint, 60)};"
                "}"
                f"QFrame#TrackerListCard[selected=\"true\"] {{"
                f"border: 1px solid {_rgba_text(border_selected, 238)};"
                f"background: {_rgba_text(tint, 88)};"
                "}"
            )
        )

    def _build_admin_entity_card(
        self,
        *,
        title: str,
        title_field: str,
        subtitle: str,
        subtitle_field: str,
        meta: str,
        meta_field: str,
        accent_color: str = "",
    ) -> QFrame:
        card = QFrame()
        card.setObjectName("TrackerListCard")
        card.setProperty("selected", "false")
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        card.setFixedHeight(106)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(3)

        title_label = QLabel(str(title or "").replace("\n", " "), card)
        title_label.setObjectName("TrackerListFieldValue")
        title_label.setProperty("field", title_field)
        title_label.setWordWrap(False)
        layout.addWidget(title_label, 0)

        if subtitle.strip():
            subtitle_label = QLabel(str(subtitle or "").replace("\n", " "), card)
            subtitle_label.setObjectName("TrackerListFieldValue")
            subtitle_label.setProperty("field", subtitle_field)
            subtitle_label.setWordWrap(False)
            layout.addWidget(subtitle_label, 0)

        if meta.strip():
            meta_label = QLabel(str(meta or "").replace("\n", " "), card)
            meta_label.setObjectName("TrackerListFieldValue")
            meta_label.setProperty("field", meta_field)
            meta_label.setWordWrap(False)
            layout.addWidget(meta_label, 0)

        self._apply_admin_entity_card_color_style(card, color_hex=accent_color)
        return card

    def _build_tracker_entity_card(
        self,
        *,
        title: str,
        title_field: str,
        subtitle: str,
        subtitle_field: str,
        meta: str,
        meta_field: str,
        accent_color: str = "",
        on_edit: Callable[[], None] | None = None,
        on_remove: Callable[[], None] | None = None,
    ) -> QFrame:
        card = TrackerHoverEntityCard(
            title=title,
            title_field=title_field,
            subtitle=subtitle,
            subtitle_field=subtitle_field,
            meta=meta,
            meta_field=meta_field,
            on_edit=on_edit,
            on_remove=on_remove,
        )
        self._apply_admin_entity_card_color_style(card, color_hex=accent_color)
        return card

    def _build_admin_input_shell(
        self,
        *,
        label_text: str,
        field_widget: QWidget,
        parent: QWidget,
        shell_bucket: list[QFrame] | None = None,
        field_stretch: int = 1,
        left_align_field: bool = False,
    ) -> QFrame:
        shell = QFrame(parent)
        shell.setObjectName("AdminInputShell")
        shell_layout = QHBoxLayout(shell)
        shell_layout.setContentsMargins(10, 6, 10, 6)
        shell_layout.setSpacing(10)
        label = QLabel(str(label_text or "").strip(), shell)
        label.setObjectName("AdminInputShellLabel")
        label.setMinimumWidth(136)
        shell_layout.addWidget(label, 0)
        if left_align_field:
            shell_layout.addWidget(
                field_widget,
                max(0, int(field_stretch)),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            )
            shell_layout.addStretch(1)
        else:
            shell_layout.addWidget(field_widget, max(0, int(field_stretch)))
        if shell_bucket is not None:
            shell_bucket.append(shell)
        return shell

    def _set_admin_list_card_selection(self, widget: QListWidget | None) -> None:
        if widget is None:
            return
        for index in range(widget.count()):
            item = widget.item(index)
            card = widget.itemWidget(item)
            if card is None:
                continue
            selected_value = "true" if item.isSelected() else "false"
            if str(card.property("selected") or "") == selected_value:
                continue
            card.setProperty("selected", selected_value)
            style = card.style()
            style.unpolish(card)
            style.polish(card)
            card.update()
