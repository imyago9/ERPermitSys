from __future__ import annotations

from PySide6.QtCore import QPoint, QRect
from PySide6.QtWidgets import QGraphicsBlurEffect, QLabel


class WindowWorkspaceStateMixin:
    def _refresh_all_views(self) -> None:
        self._timeline_debug(
            "refresh_all_views_start",
            contacts=len(self._contacts),
            jurisdictions=len(self._jurisdictions),
            properties=len(self._properties),
            permits=len(self._permits),
        )
        self._refresh_property_filters()
        self._refresh_property_list()
        self._sync_permit_type_buttons()
        self._refresh_permit_list()
        self._refresh_selected_permit_view()
        self._refresh_add_property_contacts_picker(
            selected_ids=self._add_property_attached_contact_ids
        )
        self._refresh_add_permit_contacts_picker(
            selected_ids=self._add_permit_attached_contact_ids
        )
        self._refresh_admin_views()
        self._refresh_document_templates_view()
        self._timeline_debug("refresh_all_views_done")

    def _set_result_label(self, label: QLabel | None, *, shown: int, total: int, noun: str) -> None:
        if label is None:
            return
        if total <= 0:
            label.setText(f"0 {noun}")
            return
        label.setText(f"{shown} of {total} {noun}")

    def _set_workspace_title_state(self, state_text: str) -> None:
        if self._workspace_title_label is None:
            return
        text = str(state_text or "").strip()
        if not text:
            text = "Select Permit"
        self._workspace_title_label.setText(f"Permit Workspace - {text}")

    def _set_workspace_info_values(self, **values: str) -> None:
        for key, label in self._workspace_info_values.items():
            text = str(values.get(key, "") or "").strip()
            if not text:
                text = "—"
            label.setText(text)
            label.setToolTip("" if text == "—" else text)

    def _set_workspace_next_step_hint(self, hint_text: str) -> None:
        label = self._workspace_next_step_label
        if label is None:
            return
        text = str(hint_text or "").strip()
        label.setText(text)
        label.setToolTip(text)
        self._sync_permit_workspace_blur_overlay()

    def _set_permit_workspace_blur(self, enabled: bool) -> None:
        panel = self._permit_workspace_panel
        target = self._permit_workspace_content_host or panel
        if target is None:
            return
        should_enable = bool(enabled)
        if self._permit_workspace_blurred == should_enable:
            return

        if should_enable:
            # Do not reuse cached effects: Qt may delete the previous effect
            # when the widget graphics effect is cleared.
            effect = QGraphicsBlurEffect(target)
            effect.setBlurHints(QGraphicsBlurEffect.BlurHint.QualityHint)
            effect.setBlurRadius(10.0)
            target.setGraphicsEffect(effect)
            self._permit_workspace_blur_effect = effect
        else:
            target.setGraphicsEffect(None)
            self._permit_workspace_blur_effect = None

        self._permit_workspace_blurred = should_enable
        if panel is not None:
            panel.setProperty("workspaceBlurred", "true" if should_enable else "false")
        target.setProperty("workspaceBlurred", "true" if should_enable else "false")
        style = target.style()
        style.unpolish(target)
        style.polish(target)
        target.update()
        self._sync_permit_workspace_blur_overlay()

    def _sync_permit_workspace_blur_overlay(self) -> None:
        panel = self._permit_workspace_content_host or self._permit_workspace_panel
        overlay = self._permit_workspace_blur_overlay
        workspace_panel = self._permit_workspace_panel
        next_step_label = self._workspace_next_step_label
        if panel is None or overlay is None:
            return
        if overlay.parentWidget() is not panel:
            overlay.setParent(panel)
        overlay.setGeometry(panel.rect())
        overlay.setVisible(self._permit_workspace_blurred)
        if self._permit_workspace_blurred:
            overlay.raise_()

        if workspace_panel is None or next_step_label is None:
            return

        hint_text = str(next_step_label.text() or "").strip()
        if not hint_text:
            next_step_label.setVisible(False)
            return

        content_host = self._permit_workspace_content_host
        if content_host is not None:
            try:
                target_top_left = content_host.mapTo(workspace_panel, QPoint(0, 0))
                target_rect = QRect(target_top_left, content_host.size())
            except Exception:
                target_rect = workspace_panel.rect()
        else:
            target_rect = workspace_panel.rect()
        if target_rect.width() <= 0 or target_rect.height() <= 0:
            next_step_label.setVisible(False)
            return

        margin = 18
        available_width = max(220, target_rect.width() - (margin * 2))
        max_width = max(240, min(int(round(target_rect.width() * 0.74)), available_width))
        next_step_label.setMaximumWidth(max_width)
        height_hint = next_step_label.heightForWidth(max_width)
        if height_hint <= 0:
            height_hint = next_step_label.sizeHint().height()
        target_height = max(56, min(height_hint + 8, max(72, target_rect.height() - (margin * 2))))
        target_width = min(max_width, available_width)
        x = target_rect.x() + max(0, int((target_rect.width() - target_width) / 2))
        y = target_rect.y() + max(0, int((target_rect.height() - target_height) / 2))
        next_step_label.setGeometry(x, y, target_width, target_height)
        next_step_label.setVisible(True)
        next_step_label.raise_()
