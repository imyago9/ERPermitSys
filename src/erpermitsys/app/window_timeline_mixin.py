from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QLabel, QSizePolicy, QWidget

from erpermitsys.app.permit_workspace_helpers import (
    extract_due_from_next_action_detail as _extract_due_from_next_action_detail,
    next_action_detail_text as _next_action_detail_text,
    parse_iso_date as _parse_iso_date,
    today_iso as _today_iso,
)
from erpermitsys.app.timeline_rows import (
    default_business_rows_for_permit as _timeline_default_business_rows_for_permit_logic,
    event_sort_key as _event_sort_key_logic,
    latest_note_event_id_for_permit as _latest_note_event_id_for_permit_logic,
    next_action_rows_for_permit as _timeline_next_action_rows_for_permit_logic,
)
from erpermitsys.app.tracker_models import (
    PERMIT_EVENT_TYPES,
    PermitEventRecord,
    PermitRecord,
    compute_permit_status,
    event_affects_status,
    event_type_label,
    normalize_event_type,
)
from erpermitsys.ui.assets import asset_path
from erpermitsys.ui.dialogs import (
    NextActionDialog,
    NextActionTimelineEntryDialog,
    PermitEventDialog,
    TimelineEventEditDialog,
)
from erpermitsys.ui.widgets import TimelineEventBubble

_TimelineRenderRow = tuple[str, str, str, tuple[str, ...], str]


class WindowTimelineMixin:
    def _timeline_debug(self, event: str, **payload: object) -> None:
        if not self._timeline_debug_enabled:
            return
        self._timeline_debug_sequence += 1
        record = {
            "seq": self._timeline_debug_sequence,
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": str(event or "").strip() or "unknown",
            "selected_property_id": str(self._selected_property_id or "").strip(),
            "selected_permit_id": str(self._selected_permit_id or "").strip(),
            "active_type_filter": str(self._active_permit_type_filter or "").strip(),
            "data": payload,
        }
        line = json.dumps(record, ensure_ascii=True, default=str)
        target_path = str(self._timeline_debug_log_path or "").strip()
        if target_path:
            try:
                destination = Path(target_path).expanduser()
                destination.parent.mkdir(parents=True, exist_ok=True)
                with destination.open("a", encoding="utf-8") as handle:
                    handle.write(f"{line}\n")
                return
            except Exception:
                pass
        try:
            sys.stderr.write(f"[timeline-debug] {line}\n")
            sys.stderr.flush()
        except Exception:
            pass

    def _timeline_debug_capture_widget_widths(self, *, stage: str, permit_id: str) -> None:
        if not self._timeline_debug_enabled:
            return
        track_widget = self._timeline_track_widget
        track_layout = self._timeline_track_layout
        timeline_scroll = self._timeline_scroll_area
        if track_widget is None or track_layout is None:
            self._timeline_debug(
                "timeline_widths_unavailable",
                stage=stage,
                permit_id=permit_id,
                has_track_widget=bool(track_widget is not None),
                has_track_layout=bool(track_layout is not None),
            )
            return
        rows: list[dict[str, object]] = []
        for index in range(track_layout.count()):
            item = track_layout.itemAt(index)
            child = item.widget() if item is not None else None
            if child is None:
                continue
            rows.append(
                {
                    "index": index,
                    "class": child.__class__.__name__,
                    "object_name": child.objectName(),
                    "hidden": child.isHidden(),
                    "size_hint_w": child.sizeHint().width(),
                    "min_hint_w": child.minimumSizeHint().width(),
                    "width": child.width(),
                    "min_width": child.minimumWidth(),
                    "max_width": child.maximumWidth(),
                }
            )
        self._timeline_debug(
            "timeline_widths_snapshot",
            stage=stage,
            permit_id=permit_id,
            track_width=track_widget.width(),
            track_fixed_width=track_widget.minimumWidth(),
            viewport_width=(
                timeline_scroll.viewport().width()
                if timeline_scroll is not None
                else 0
            ),
            child_count=track_layout.count(),
            rows=rows,
        )

    def _event_sort_key(self, event: PermitEventRecord, index: int) -> tuple[datetime, int]:
        return _event_sort_key_logic(event, index)

    def _timeline_default_business_rows_for_permit(self, permit: PermitRecord) -> list[_TimelineRenderRow]:
        return _timeline_default_business_rows_for_permit_logic(permit, self._contacts)

    def _timeline_next_action_rows_for_permit(self, permit: PermitRecord) -> list[_TimelineRenderRow]:
        return _timeline_next_action_rows_for_permit_logic(permit, self._contacts)

    def _sync_timeline_mode_chrome(self, permit: PermitRecord | None) -> None:
        show_next_action_mode = bool(self._timeline_show_next_action_mode)
        title_label = self._timeline_title_label
        if title_label is not None:
            title_label.setText("Next Action Timeline" if show_next_action_mode else "Timeline")
        hint_label = self._timeline_hint_label
        if hint_label is not None:
            if show_next_action_mode:
                hint_label.setText(
                    "Saved next-action notes only. Use Set Next Action to add informal timeline updates."
                )
            else:
                hint_label.setText(
                    "Oldest on the left, newest on the right. Only saved events appear here."
                )
        toggle_button = self._timeline_mode_toggle_button
        if toggle_button is not None:
            toggle_button.setText(
                "Show Business Timeline" if show_next_action_mode else "Show Next Action Timeline"
            )
            toggle_button.setEnabled(permit is not None)

    def _toggle_timeline_mode(self) -> None:
        permit = self._selected_permit()
        if permit is None:
            self._show_info_dialog("Select Permit", "Select a permit first.")
            return
        self._timeline_show_next_action_mode = not self._timeline_show_next_action_mode
        self._refresh_selected_permit_view()

    def _timeline_rows_for_permit(self, permit: PermitRecord) -> list[_TimelineRenderRow]:
        mode_name = "next_action_saved" if self._timeline_show_next_action_mode else "default_business"
        self._timeline_debug(
            "timeline_rows_build_start",
            permit_id=permit.permit_id,
            raw_event_count=len(permit.events),
            mode=mode_name,
        )
        if self._timeline_show_next_action_mode:
            timeline_rows = self._timeline_next_action_rows_for_permit(permit)
        else:
            timeline_rows = self._timeline_default_business_rows_for_permit(permit)
        self._timeline_debug(
            "timeline_rows_build_done",
            permit_id=permit.permit_id,
            row_count=len(timeline_rows),
            mode=mode_name,
            rows_preview=[
                {
                    "event_date": row[0],
                    "event_type": row[1],
                    "summary": row[2],
                    "detail_lines_count": len(row[3]),
                    "event_id": row[4],
                }
                for row in timeline_rows[:20]
            ],
        )
        return timeline_rows

    def _timeline_connector_icon_path(self) -> str:
        if self._dark_mode_enabled:
            white_arrow = Path(asset_path("icons", "white-arrow-right.png"))
            if white_arrow.exists():
                return str(white_arrow)
        return asset_path("icons", "black-arrow-right.png")

    def _refresh_timeline_list(self, permit: PermitRecord | None) -> None:
        track_widget = self._timeline_track_widget
        track_layout = self._timeline_track_layout
        timeline_scroll = self._timeline_scroll_area
        if track_widget is None or track_layout is None:
            self._timeline_debug(
                "timeline_refresh_skipped",
                reason="missing_track_widget_or_layout",
                has_track_widget=bool(track_widget is not None),
                has_track_layout=bool(track_layout is not None),
            )
            return
        self._timeline_render_sequence += 1
        render_id = self._timeline_render_sequence
        permit_id = str(permit.permit_id or "").strip() if permit is not None else ""
        self._timeline_debug(
            "timeline_refresh_start",
            render_id=render_id,
            permit_id=permit_id,
            event_count=len(permit.events) if permit is not None else 0,
            existing_layout_count=track_layout.count(),
        )

        def sync_track_width(*, stage: str) -> None:
            track_layout.activate()
            margins = track_layout.contentsMargins()
            measured_widgets: list[QWidget] = []
            hidden_widget_count = 0
            for index in range(track_layout.count()):
                item = track_layout.itemAt(index)
                child = item.widget() if item is not None else None
                if child is None:
                    continue
                measured_widgets.append(child)
                if child.isHidden():
                    hidden_widget_count += 1
            content_width = margins.left() + margins.right()
            if measured_widgets:
                content_width += sum(
                    max(
                        child.sizeHint().width(),
                        child.minimumSizeHint().width(),
                        child.minimumWidth(),
                    )
                    for child in measured_widgets
                )
                content_width += max(0, len(measured_widgets) - 1) * max(0, track_layout.spacing())
            viewport_width = timeline_scroll.viewport().width() if timeline_scroll is not None else 0
            target_width = max(1, viewport_width, content_width)
            track_widget.setFixedWidth(target_width)
            self._timeline_debug(
                "timeline_sync_track_width",
                stage=stage,
                render_id=render_id,
                permit_id=permit_id,
                measured_widget_count=len(measured_widgets),
                hidden_widget_count=hidden_widget_count,
                measured_widget_widths=[child.sizeHint().width() for child in measured_widgets],
                layout_spacing=track_layout.spacing(),
                margins=[margins.left(), margins.right()],
                content_width=content_width,
                viewport_width=viewport_width,
                target_width=target_width,
            )

        def render_empty_state(message: str) -> None:
            empty_label = QLabel(message, track_widget)
            empty_label.setObjectName("TrackerPanelEmptyState")
            empty_label.setProperty("timeline", "true")
            empty_label.setWordWrap(True)
            empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_label.setMinimumWidth(360)
            empty_label.setMaximumWidth(420)
            empty_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
            track_layout.addWidget(empty_label, 0, Qt.AlignmentFlag.AlignVCenter)
            empty_label.show()
            track_layout.addStretch(1)
            track_widget.adjustSize()
            sync_track_width(stage="empty_immediate")
            if timeline_scroll is not None:
                timeline_scroll.horizontalScrollBar().setValue(0)
                timeline_scroll.viewport().update()
            self._timeline_debug(
                "timeline_render_empty_state",
                render_id=render_id,
                permit_id=permit_id,
                message=message,
                layout_count=track_layout.count(),
            )
            self._timeline_debug_capture_widget_widths(
                stage=f"render_empty_state:render_id={render_id}",
                permit_id=permit_id,
            )

        removed_count = 0
        while track_layout.count():
            item = track_layout.takeAt(0)
            child = item.widget()
            if child is not None:
                # Keep widget parented while deleting so it does not briefly
                # become a top-level window during timeline rebuilds.
                child.hide()
                child.deleteLater()
                removed_count += 1
        self._timeline_debug(
            "timeline_refresh_cleared_layout",
            render_id=render_id,
            permit_id=permit_id,
            removed_widget_count=removed_count,
            remaining_layout_count=track_layout.count(),
        )

        if permit is None:
            render_empty_state(
                "No permit selected.\nChoose a permit from the list to view and manage timeline events.",
            )
            return

        timeline_rows = self._timeline_rows_for_permit(permit)
        if not timeline_rows:
            if self._timeline_show_next_action_mode:
                render_empty_state(
                    "No saved next-action entries yet.\nUse Set Next Action to add the first one.",
                )
            else:
                render_empty_state(
                    "No timeline events yet.\nClick Add Event to record the first permitting milestone.",
                )
            return

        connector_icon_path = self._timeline_connector_icon_path()
        timeline_total = len(timeline_rows)
        for row_index, (event_date, event_type, summary, detail_lines, event_id) in enumerate(timeline_rows):
            normalized_event_id = str(event_id or "").strip()
            bubble = TimelineEventBubble(
                date_text=event_date or "(no date)",
                event_type_text=event_type_label(event_type),
                summary=summary or "(no summary)",
                detail_lines=list(detail_lines),
                on_edit=(
                    (lambda pid=permit.permit_id, eid=normalized_event_id: self._edit_timeline_event(
                        permit_id=pid,
                        event_id=eid,
                    ))
                    if normalized_event_id
                    else None
                ),
                on_remove=(
                    (lambda pid=permit.permit_id, eid=normalized_event_id: self._delete_timeline_event(
                        permit_id=pid,
                        event_id=eid,
                    ))
                    if normalized_event_id
                    else None
                ),
                parent=track_widget,
            )
            track_layout.addWidget(bubble, 0, Qt.AlignmentFlag.AlignVCenter)
            bubble.show()
            self._timeline_debug(
                "timeline_render_bubble",
                render_id=render_id,
                permit_id=permit_id,
                event_date=event_date,
                event_type=event_type,
                summary=summary,
                detail_lines_count=len(detail_lines),
                bubble_size_hint_width=bubble.sizeHint().width(),
                bubble_minimum_width=bubble.minimumWidth(),
                bubble_maximum_width=bubble.maximumWidth(),
            )
            if row_index < (timeline_total - 1):
                connector = QLabel(track_widget)
                connector.setObjectName("PermitTimelineConnector")
                connector.setFixedSize(20, 20)
                connector.setAlignment(Qt.AlignmentFlag.AlignCenter)
                connector_icon = QPixmap(connector_icon_path)
                if connector_icon.isNull():
                    connector.setText(">")
                else:
                    connector.setPixmap(
                        connector_icon.scaled(
                            12,
                            12,
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation,
                        )
                    )
                track_layout.addWidget(connector, 0, Qt.AlignmentFlag.AlignVCenter)
                connector.show()
                self._timeline_debug(
                    "timeline_render_connector",
                    render_id=render_id,
                    permit_id=permit_id,
                    row_index=row_index,
                    icon_path=connector_icon_path,
                    icon_loaded=not connector_icon.isNull(),
                )
        bubble_widgets: list[TimelineEventBubble] = []
        for index in range(track_layout.count()):
            item = track_layout.itemAt(index)
            widget = item.widget() if item is not None else None
            if isinstance(widget, TimelineEventBubble):
                bubble_widgets.append(widget)

        unified_badge_width = 0
        unified_badge_height = 0
        if bubble_widgets:
            for widget in bubble_widgets:
                badge_width, badge_height = widget.event_type_badge_size_hint()
                unified_badge_width = max(unified_badge_width, badge_width)
                unified_badge_height = max(unified_badge_height, badge_height)
            for widget in bubble_widgets:
                widget.set_event_type_badge_size(unified_badge_width, unified_badge_height)
        self._timeline_debug(
            "timeline_unify_event_type_badge_size",
            render_id=render_id,
            permit_id=permit_id,
            bubble_count=len(bubble_widgets),
            unified_badge_width=unified_badge_width,
            unified_badge_height=unified_badge_height,
        )

        unified_height = 0
        if bubble_widgets:
            unified_height = max(
                max(widget.sizeHint().height(), widget.minimumSizeHint().height(), widget.minimumHeight())
                for widget in bubble_widgets
            )
            for widget in bubble_widgets:
                widget.setFixedHeight(unified_height)
        self._timeline_debug(
            "timeline_unify_bubble_height",
            render_id=render_id,
            permit_id=permit_id,
            bubble_count=len(bubble_widgets),
            unified_height=unified_height,
        )
        track_layout.addStretch(1)
        track_widget.adjustSize()
        sync_track_width(stage="events_immediate")
        if timeline_scroll is not None:
            timeline_scroll.horizontalScrollBar().setValue(0)
            timeline_scroll.viewport().update()
        self._timeline_debug(
            "timeline_refresh_done",
            render_id=render_id,
            permit_id=permit_id,
            timeline_row_count=len(timeline_rows),
            layout_count=track_layout.count(),
            scroll_value=(
                timeline_scroll.horizontalScrollBar().value()
                if timeline_scroll is not None
                else 0
            ),
        )
        self._timeline_debug_capture_widget_widths(
            stage=f"render_done_immediate:render_id={render_id}",
            permit_id=permit_id,
        )
        QTimer.singleShot(
            0,
            lambda rid=render_id, pid=permit_id: (
                sync_track_width(stage=f"deferred:render_id={rid}"),
                self._timeline_debug_capture_widget_widths(
                    stage=f"render_done_deferred:render_id={rid}",
                    permit_id=pid,
                ),
            ),
        )


    def _set_next_action(self) -> None:
        permit = self._selected_permit()
        self._timeline_debug(
            "set_next_action_start",
            permit_id=permit.permit_id if permit is not None else "",
            permit_status=permit.status if permit is not None else "",
        )
        if permit is None:
            self._show_info_dialog("Select Permit", "Select a permit first.")
            return

        dialog = NextActionDialog(
            current_text=permit.next_action_text,
            current_due=permit.next_action_due,
            parent=self,
            theme_mode=self._dialog_theme_mode(),
        )
        if dialog.exec() != dialog.DialogCode.Accepted:
            self._timeline_debug(
                "set_next_action_cancelled",
                permit_id=permit.permit_id,
            )
            return

        previous_text = str(permit.next_action_text or "").strip()
        previous_due = str(permit.next_action_due or "").strip()
        action_text, action_due = dialog.values()
        permit.next_action_text = action_text
        permit.next_action_due = action_due
        appended_next_action_note = False
        if (
            (action_text or action_due)
            and (action_text != previous_text or action_due != previous_due)
        ):
            note_event_date = action_due if _parse_iso_date(action_due) is not None else _today_iso()
            permit.events.append(
                PermitEventRecord(
                    event_id=uuid4().hex,
                    event_type="note",
                    event_date=note_event_date,
                    summary=action_text or "Next Action Updated",
                    detail=_next_action_detail_text(action_due),
                    actor_contact_id="",
                    attachments=[],
                )
            )
            appended_next_action_note = True
        self._selected_property_id = permit.property_id
        self._selected_permit_id = permit.permit_id
        persisted = self._persist_tracker_data()
        self._timeline_debug(
            "set_next_action_saved",
            permit_id=permit.permit_id,
            next_action_text=permit.next_action_text,
            next_action_due=permit.next_action_due,
            appended_next_action_note=appended_next_action_note,
            persisted=persisted,
        )
        self._refresh_permit_list(keep_selected_visible=True)

    def _latest_note_event_id_for_permit(self, permit: PermitRecord) -> str:
        return _latest_note_event_id_for_permit_logic(permit)

    def _edit_timeline_event(self, *, permit_id: str, event_id: str) -> None:
        normalized_permit_id = str(permit_id or "").strip()
        normalized_event_id = str(event_id or "").strip()
        if not normalized_permit_id or not normalized_event_id:
            return

        permit = self._permit_by_id(normalized_permit_id)
        if permit is None:
            self._show_info_dialog("Permit Missing", "The selected permit could not be found.")
            return

        target_event = next(
            (
                row
                for row in permit.events
                if str(row.event_id or "").strip() == normalized_event_id
            ),
            None,
        )
        if target_event is None:
            self._show_info_dialog("Event Missing", "The selected timeline event could not be found.")
            return

        event_type = normalize_event_type(target_event.event_type)
        if event_type == "note":
            previous_latest_note_event_id = self._latest_note_event_id_for_permit(permit)
            dialog = NextActionTimelineEntryDialog(
                current_date=str(target_event.event_date or "").strip() or _today_iso(),
                current_text=str(target_event.summary or "").strip(),
                current_due=_extract_due_from_next_action_detail(target_event.detail),
                parent=self,
                theme_mode=self._dialog_theme_mode(),
            )
            if dialog.exec() != dialog.DialogCode.Accepted:
                return
            event_date, action_text, action_due = dialog.values()
            target_event.event_date = event_date
            target_event.summary = action_text
            target_event.detail = _next_action_detail_text(action_due)

            if normalized_event_id == previous_latest_note_event_id:
                permit.next_action_text = action_text
                permit.next_action_due = action_due
        else:
            dialog = TimelineEventEditDialog(
                event=target_event,
                contacts=self._contacts,
                parent=self,
                theme_mode=self._dialog_theme_mode(),
            )
            if dialog.exec() != dialog.DialogCode.Accepted:
                return
            updated_type, updated_date, updated_summary, updated_detail, updated_actor = dialog.values()
            target_event.event_type = updated_type
            target_event.event_date = updated_date
            target_event.summary = updated_summary
            target_event.detail = updated_detail
            target_event.actor_contact_id = updated_actor

        permit.status = compute_permit_status(permit.events, fallback=permit.status)
        self._selected_property_id = permit.property_id
        self._selected_permit_id = permit.permit_id
        self._persist_tracker_data()
        self._refresh_permit_list(keep_selected_visible=True)

    def _delete_timeline_event(self, *, permit_id: str, event_id: str) -> None:
        normalized_permit_id = str(permit_id or "").strip()
        normalized_event_id = str(event_id or "").strip()
        if not normalized_permit_id or not normalized_event_id:
            return

        permit = self._permit_by_id(normalized_permit_id)
        if permit is None:
            self._show_info_dialog("Permit Missing", "The selected permit could not be found.")
            return

        target_event = next(
            (
                row
                for row in permit.events
                if str(row.event_id or "").strip() == normalized_event_id
            ),
            None,
        )
        if target_event is None:
            self._show_info_dialog("Event Missing", "The selected timeline event could not be found.")
            return

        event_title = str(target_event.summary or "").strip() or event_type_label(target_event.event_type)
        event_date = str(target_event.event_date or "").strip() or "no date"
        if not self._confirm_dialog(
            "Delete Timeline Event",
            f"Delete '{event_title}' dated {event_date} from this timeline?",
            confirm_text="Delete",
            cancel_text="Cancel",
            danger=True,
        ):
            return

        remaining_events = [
            row
            for row in permit.events
            if str(row.event_id or "").strip() != normalized_event_id
        ]
        if len(remaining_events) == len(permit.events):
            return
        permit.events = remaining_events
        permit.status = compute_permit_status(permit.events, fallback=permit.status)
        self._selected_property_id = permit.property_id
        self._selected_permit_id = permit.permit_id
        self._persist_tracker_data()
        self._refresh_permit_list(keep_selected_visible=True)

    def _add_event(self) -> None:
        permit = self._selected_permit()
        self._timeline_debug(
            "add_event_start",
            permit_id=permit.permit_id if permit is not None else "",
            permit_event_count=len(permit.events) if permit is not None else 0,
        )
        if permit is None:
            self._show_info_dialog("Select Permit", "Select a permit first.")
            return

        existing_event_types: set[str] = {
            normalize_event_type(event.event_type)
            for event in permit.events
            if normalize_event_type(event.event_type) in PERMIT_EVENT_TYPES
        }
        available_event_types = [
            event_type
            for event_type in PERMIT_EVENT_TYPES
            if event_type not in existing_event_types
        ]
        if not available_event_types:
            self._show_info_dialog(
                "No Event Types Available",
                "All event types are already present on this timeline.",
            )
            return

        dialog = PermitEventDialog(
            contacts=self._contacts,
            available_event_types=available_event_types,
            parent=self,
            theme_mode=self._dialog_theme_mode(),
        )
        if dialog.exec() != dialog.DialogCode.Accepted:
            self._timeline_debug(
                "add_event_cancelled",
                permit_id=permit.permit_id,
                permit_event_count=len(permit.events),
            )
            return

        event = dialog.build_event()
        self._timeline_debug(
            "add_event_built",
            permit_id=permit.permit_id,
            event_id=event.event_id,
            event_type=event.event_type,
            event_date=event.event_date,
            summary=event.summary,
            detail=event.detail,
            actor_contact_id=event.actor_contact_id,
        )
        permit.events.append(event)
        if event_affects_status(event.event_type):
            permit.status = compute_permit_status(permit.events, fallback=permit.status)
        self._selected_property_id = permit.property_id
        self._selected_permit_id = permit.permit_id
        persisted = self._persist_tracker_data()
        self._timeline_debug(
            "add_event_appended",
            permit_id=permit.permit_id,
            permit_event_count=len(permit.events),
            permit_status=permit.status,
            persisted=persisted,
        )
        self._refresh_permit_list(keep_selected_visible=True)
        selected_after_refresh = self._selected_permit()
        self._timeline_debug(
            "add_event_after_refresh",
            selected_permit_id=self._selected_permit_id,
            selected_property_id=self._selected_property_id,
            selected_permit_event_count=(
                len(selected_after_refresh.events)
                if selected_after_refresh is not None
                else 0
            ),
            permits_list_count=(
                self._permits_list_widget.count()
                if self._permits_list_widget is not None
                else 0
            ),
        )
