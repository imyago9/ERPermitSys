from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QFileInfo, QSize, QTimer, Qt, QUrl
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QFileDialog,
    QFileIconProvider,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QStyle,
)

from erpermitsys.app.permit_workspace_helpers import today_iso as _today_iso
from erpermitsys.app.tracker_models import (
    PermitDocumentFolder,
    PermitDocumentRecord,
    PermitDocumentSlot,
    PermitEventRecord,
    PermitRecord,
    document_file_count_by_slot,
    ensure_default_document_structure,
    normalize_document_review_status,
    normalize_slot_id,
    normalize_slot_status,
    refresh_slot_status_from_documents,
)
from erpermitsys.ui.widgets import DocumentChecklistSlotCard, PermitDocumentFileCard


class WindowDocumentsMixin:
    def _slot_by_id(self, permit: PermitRecord, slot_id: str) -> PermitDocumentSlot | None:
        target = str(slot_id or "").strip()
        if not target:
            return None
        for slot in permit.document_slots:
            if slot.slot_id == target:
                return slot
        return None

    def _folder_by_id(self, permit: PermitRecord, folder_id: str) -> PermitDocumentFolder | None:
        target = str(folder_id or "").strip()
        if not target:
            return None
        for folder in permit.document_folders:
            if folder.folder_id == target:
                return folder
        return None

    def _document_by_id(self, permit: PermitRecord, document_id: str) -> PermitDocumentRecord | None:
        target = str(document_id or "").strip()
        if not target:
            return None
        for document in permit.documents:
            if document.document_id == target:
                return document
        return None

    def _ensure_slot_folder(self, permit: PermitRecord, slot: PermitDocumentSlot) -> PermitDocumentFolder:
        folder_id = normalize_slot_id(slot.folder_id) or normalize_slot_id(slot.slot_id)
        existing = self._folder_by_id(permit, folder_id)
        if existing is not None:
            return existing
        created = PermitDocumentFolder(folder_id=folder_id, name=slot.label or folder_id, parent_folder_id="")
        permit.document_folders.append(created)
        return created

    def _slot_active_cycle(self, slot: PermitDocumentSlot) -> int:
        return self._safe_positive_int(slot.active_cycle, default=1)

    def _safe_positive_int(self, value: object, *, default: int = 1) -> int:
        try:
            parsed = int(value)  # type: ignore[arg-type]
        except Exception:
            parsed = int(default)
        return max(1, parsed)

    def _documents_for_slot(
        self,
        permit: PermitRecord,
        slot: PermitDocumentSlot,
        *,
        active_cycle_only: bool,
    ) -> list[PermitDocumentRecord]:
        folder_id = normalize_slot_id(slot.folder_id) or normalize_slot_id(slot.slot_id)
        if not folder_id:
            return []
        active_cycle = self._slot_active_cycle(slot)
        rows: list[PermitDocumentRecord] = []
        for record in permit.documents:
            if normalize_slot_id(record.folder_id) != folder_id:
                continue
            record_cycle = self._safe_positive_int(record.cycle_index, default=1)
            if active_cycle_only and record_cycle != active_cycle:
                continue
            rows.append(record)
        return rows

    def _next_slot_revision_index(
        self,
        permit: PermitRecord,
        slot: PermitDocumentSlot,
        *,
        cycle_index: int,
    ) -> int:
        target_cycle = self._safe_positive_int(cycle_index, default=1)
        max_revision = 0
        for record in self._documents_for_slot(permit, slot, active_cycle_only=False):
            if self._safe_positive_int(record.cycle_index, default=1) != target_cycle:
                continue
            max_revision = max(
                max_revision,
                self._safe_positive_int(record.revision_index, default=1),
            )
        return max_revision + 1

    def _cycle_folder_segment(self, cycle_index: int) -> str:
        return f"cycle-{self._safe_positive_int(cycle_index, default=1):02d}"

    def _cycle_label(self, cycle_index: int) -> str:
        return f"Cycle {self._safe_positive_int(cycle_index, default=1):02d}"

    def _active_cycle_status_counts_for_slot(
        self,
        permit: PermitRecord,
        slot: PermitDocumentSlot,
    ) -> dict[str, int]:
        counts: dict[str, int] = {}
        for record in self._documents_for_slot(permit, slot, active_cycle_only=True):
            review_status = normalize_document_review_status(record.review_status)
            counts[review_status] = counts.get(review_status, 0) + 1
        return counts

    def _add_centered_list_empty_state(
        self,
        widget: QListWidget,
        message: str,
    ) -> None:
        text = str(message or "").strip()
        item = QListWidgetItem()
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        widget.addItem(item)

        label = QLabel(text, widget)
        label.setObjectName("TrackerPanelEmptyState")
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setMargin(8)
        widget.setItemWidget(item, label)

        def sync_empty_state_size() -> None:
            try:
                viewport = widget.viewport()
                if viewport is None:
                    return
                if widget.itemWidget(item) is not label:
                    return
                viewport_size = viewport.size()
                target_width = max(120, viewport_size.width() - 2)
                target_height = max(108, viewport_size.height() - 2)
                item.setSizeHint(QSize(target_width, target_height))
                label.setMinimumHeight(max(96, target_height - 4))
            except RuntimeError:
                return

        sync_empty_state_size()
        QTimer.singleShot(0, sync_empty_state_size)

    def _refresh_document_slots(self, permit: PermitRecord | None) -> None:
        slot_widget = self._document_slot_list_widget
        file_widget = self._document_file_list_widget
        if slot_widget is None or file_widget is None:
            return

        slot_widget.blockSignals(True)
        slot_widget.clear()
        self._document_slot_cards = {}
        file_widget.blockSignals(True)
        file_widget.clear()
        self._selected_document_id = ""

        if permit is None:
            self._selected_document_slot_id = ""
            self._add_centered_list_empty_state(
                slot_widget,
                "No permit selected.\nChoose a permit to view its checklist slots.",
            )
            self._add_centered_list_empty_state(
                file_widget,
                "No permit selected.\nChoose a permit, then select a slot to view files.",
            )
            slot_widget.blockSignals(False)
            file_widget.blockSignals(False)
            self._sync_document_action_buttons(enabled=False)
            return

        changed = ensure_default_document_structure(permit)
        changed = refresh_slot_status_from_documents(permit) or changed
        if changed:
            self._persist_tracker_data(show_error_dialog=False)

        file_counts = document_file_count_by_slot(permit)

        for slot in permit.document_slots:
            status = normalize_slot_status(slot.status)
            count = file_counts.get(slot.slot_id, 0)
            status_counts = self._active_cycle_status_counts_for_slot(permit, slot)
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, slot.slot_id)
            slot_widget.addItem(item)
            card = DocumentChecklistSlotCard(
                slot_label=slot.label,
                slot_id=slot.folder_id or slot.slot_id,
                required=bool(slot.required),
                status=status,
                file_count=count,
                status_counts=status_counts,
                parent=slot_widget,
            )
            item.setSizeHint(self._list_item_size_hint_for_card(card))
            slot_widget.setItemWidget(item, card)
            self._document_slot_cards[slot.slot_id] = card

        if not permit.document_slots:
            self._add_centered_list_empty_state(
                slot_widget,
                "No checklist slots are configured for this permit.",
            )
            self._add_centered_list_empty_state(
                file_widget,
                "No checklist slots are configured for this permit.",
            )
            self._selected_document_slot_id = ""
            slot_widget.blockSignals(False)
            file_widget.blockSignals(False)
            self._sync_document_action_buttons(enabled=False)
            return

        if self._selected_document_slot_id and self._slot_by_id(permit, self._selected_document_slot_id) is not None:
            self._select_document_slot_item(self._selected_document_slot_id)
        else:
            self._selected_document_slot_id = permit.document_slots[0].slot_id if permit.document_slots else ""
            if self._selected_document_slot_id:
                self._select_document_slot_item(self._selected_document_slot_id)

        slot_widget.blockSignals(False)
        file_widget.blockSignals(False)
        self._set_admin_list_card_selection(slot_widget)
        self._refresh_document_files(permit)

    def _select_document_slot_item(self, slot_id: str) -> None:
        widget = self._document_slot_list_widget
        if widget is None:
            return
        target = str(slot_id or "").strip()
        for index in range(widget.count()):
            item = widget.item(index)
            if str(item.data(Qt.ItemDataRole.UserRole) or "").strip() != target:
                continue
            widget.setCurrentItem(item)
            return

    def _document_file_icon_for_record(self, document: PermitDocumentRecord, display_name: str) -> QIcon:
        extension = Path(str(display_name or "").strip()).suffix.casefold()
        cache_key = extension or "__default__"
        cached_icon = self._document_file_icon_cache.get(cache_key)
        if cached_icon is not None and not cached_icon.isNull():
            return cached_icon

        provider = self._document_file_icon_provider
        if provider is None:
            provider = QFileIconProvider()
            self._document_file_icon_provider = provider

        resolved_path: Path | None = None
        try:
            resolved_path = self._document_store.resolve_document_path(document.relative_path)
        except Exception:
            resolved_path = None

        icon = QIcon()
        if resolved_path is not None:
            icon = provider.icon(QFileInfo(str(resolved_path)))
        if icon.isNull():
            probe_name = f"sample{extension}" if extension else "sample.txt"
            icon = provider.icon(QFileInfo(probe_name))
        if icon.isNull():
            icon = self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon)

        self._document_file_icon_cache[cache_key] = icon
        return icon

    def _refresh_document_files(self, permit: PermitRecord | None) -> None:
        file_widget = self._document_file_list_widget
        if file_widget is None:
            return

        previous_selected_document_id = str(self._selected_document_id or "").strip()
        file_widget.blockSignals(True)
        file_widget.clear()
        self._selected_document_id = ""

        if permit is None:
            self._add_centered_list_empty_state(
                file_widget,
                "No permit selected.\nChoose a permit, then select a checklist slot.",
            )
            file_widget.blockSignals(False)
            self._sync_document_action_buttons(enabled=False)
            return

        slot = self._slot_by_id(permit, self._selected_document_slot_id)
        if slot is None:
            self._add_centered_list_empty_state(
                file_widget,
                "Select a checklist slot to view or upload documents.",
            )
            file_widget.blockSignals(False)
            self._sync_document_action_buttons(enabled=False)
            return

        active_cycle = self._slot_active_cycle(slot)
        documents = self._documents_for_slot(
            permit,
            slot,
            active_cycle_only=True,
        )
        documents.sort(
            key=lambda row: (
                self._safe_positive_int(row.revision_index, default=1),
                row.imported_at,
                row.original_name,
                row.document_id,
            ),
            reverse=True,
        )

        if not documents:
            self._add_centered_list_empty_state(
                file_widget,
                "No files in the active cycle yet.\nUse Upload to add documents to this checklist item.",
            )
            if self._document_status_label is not None:
                self._document_status_label.setText(
                    f"{slot.label} | {self._cycle_label(active_cycle)} | {normalize_slot_status(slot.status).title()} | 0 files"
                )
            file_widget.blockSignals(False)
            self._sync_document_action_buttons(enabled=True, has_file=False)
            return

        for document in documents:
            size_text = self._format_byte_size(document.byte_size)
            date_text = self._format_imported_value(document.imported_at)
            display_name = (
                str(document.original_name or "").strip()
                or str(document.stored_name or "").strip()
                or Path(str(document.relative_path or "").strip()).name
                or "Unnamed"
            )
            extension = Path(display_name).suffix.lstrip(".").upper() or "FILE"
            icon = self._document_file_icon_for_record(document, display_name)
            review_status = normalize_document_review_status(document.review_status)
            document_cycle = self._safe_positive_int(document.cycle_index, default=1)
            document_revision = self._safe_positive_int(document.revision_index, default=1)
            version_text = (
                f"{self._cycle_label(document_cycle)}  "
                f"Rev {document_revision:02d}"
            )
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, document.document_id)
            file_widget.addItem(item)
            card = PermitDocumentFileCard(
                file_name=display_name,
                extension_label=extension,
                meta_text=f"{size_text} | {date_text}",
                version_text=version_text,
                review_status=review_status,
                icon=icon,
                parent=file_widget,
            )
            file_widget.setItemWidget(item, card)
            item.setSizeHint(self._list_item_size_hint_for_card(card))

        selected_document_id = previous_selected_document_id
        if not any(row.document_id == selected_document_id for row in documents):
            selected_document_id = ""
        self._selected_document_id = selected_document_id
        if self._selected_document_id:
            self._select_document_file_item(self._selected_document_id)

        if self._document_status_label is not None:
            self._document_status_label.setText(
                f"{slot.label} | {self._cycle_label(active_cycle)} | {normalize_slot_status(slot.status).title()} | {len(documents)} files"
            )

        file_widget.blockSignals(False)
        self._set_admin_list_card_selection(file_widget)
        self._sync_document_action_buttons(
            enabled=True,
            has_file=bool(self._selected_document_id),
        )

    def _select_document_file_item(self, document_id: str) -> None:
        widget = self._document_file_list_widget
        if widget is None:
            return
        target = str(document_id or "").strip()
        for index in range(widget.count()):
            item = widget.item(index)
            if str(item.data(Qt.ItemDataRole.UserRole) or "").strip() != target:
                continue
            widget.setCurrentItem(item)
            return

    def _sync_document_action_buttons(self, *, enabled: bool, has_file: bool = False) -> None:
        for control in (
            self._document_upload_button,
            self._document_open_folder_button,
        ):
            if control is not None:
                control.setEnabled(enabled)
        if self._document_new_cycle_button is not None:
            self._document_new_cycle_button.setEnabled(enabled and has_file)
        if self._document_open_file_button is not None:
            self._document_open_file_button.setEnabled(enabled and has_file)
        if self._document_remove_file_button is not None:
            self._document_remove_file_button.setEnabled(enabled and has_file)
        for control in (self._document_mark_accepted_button, self._document_mark_rejected_button):
            if control is not None:
                control.setEnabled(enabled)


    def _on_document_slot_selection_changed(self) -> None:
        widget = self._document_slot_list_widget
        current_item = widget.currentItem() if widget is not None else None
        self._selected_document_slot_id = (
            str(current_item.data(Qt.ItemDataRole.UserRole) or "").strip() if current_item else ""
        )
        self._set_admin_list_card_selection(widget)
        self._selected_document_id = ""
        self._refresh_document_files(self._selected_permit())

    def _on_document_file_selection_changed(self) -> None:
        widget = self._document_file_list_widget
        current_item = widget.currentItem() if widget is not None else None
        self._selected_document_id = (
            str(current_item.data(Qt.ItemDataRole.UserRole) or "").strip() if current_item else ""
        )
        self._set_admin_list_card_selection(widget)
        self._sync_document_action_buttons(
            enabled=bool(self._selected_permit() and self._selected_document_slot_id),
            has_file=bool(self._selected_document_id),
        )


    def _upload_documents_to_slot(self) -> None:
        permit = self._selected_permit()
        if permit is None:
            return

        slot = self._slot_by_id(permit, self._selected_document_slot_id)
        if slot is None:
            self._show_info_dialog("Select Slot", "Select a document checklist slot first.")
            return

        folder = self._ensure_slot_folder(permit, slot)
        try:
            self._document_store.ensure_folder_structure(permit)
        except Exception as exc:
            self._show_warning_dialog("Storage Error", f"Could not prepare permit folders.\n\n{exc}")
            return

        file_paths, _filter_used = QFileDialog.getOpenFileNames(
            self,
            "Upload Documents",
            "",
            "All Files (*)",
        )
        if not file_paths:
            return

        active_cycle = self._slot_active_cycle(slot)
        cycle_segment = self._cycle_folder_segment(active_cycle)
        failures: list[str] = []
        imported_count = 0
        for raw_path in file_paths:
            source_path = Path(raw_path)
            try:
                document = self._document_store.import_document(
                    permit=permit,
                    folder=folder,
                    source_path=source_path,
                    cycle_folder=cycle_segment,
                )
            except Exception as exc:
                failures.append(f"{source_path.name}: {exc}")
                continue
            document.folder_id = normalize_slot_id(slot.folder_id) or normalize_slot_id(slot.slot_id)
            document.slot_id = slot.slot_id
            document.cycle_index = active_cycle
            document.revision_index = self._next_slot_revision_index(
                permit,
                slot,
                cycle_index=active_cycle,
            )
            document.review_status = "uploaded"
            document.reviewed_at = ""
            document.review_note = ""
            permit.documents.append(document)
            imported_count += 1
            self._selected_document_id = document.document_id

        refresh_slot_status_from_documents(permit)
        self._persist_tracker_data()
        self._refresh_selected_permit_view()

        if failures:
            preview = "\n".join(failures[:6])
            suffix = ""
            if len(failures) > 6:
                suffix = f"\n...and {len(failures) - 6} more."
            self._show_warning_dialog("Some Uploads Failed", f"{preview}{suffix}")

    def _open_selected_slot_folder(self) -> None:
        permit = self._selected_permit()
        if permit is None:
            return
        slot = self._slot_by_id(permit, self._selected_document_slot_id)
        if slot is None:
            return

        folder = self._ensure_slot_folder(permit, slot)
        try:
            folder_path = self._document_store.folder_path(permit, folder)
            folder_path.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            self._show_warning_dialog("Folder Error", f"Could not open folder.\n\n{exc}")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder_path)))

    def _start_selected_slot_new_cycle(self) -> None:
        permit = self._selected_permit()
        if permit is None:
            return
        slot = self._slot_by_id(permit, self._selected_document_slot_id)
        if slot is None:
            self._show_info_dialog("Select Slot", "Select a checklist slot first.")
            return

        current_cycle = self._slot_active_cycle(slot)
        existing_cycle_docs = self._documents_for_slot(
            permit,
            slot,
            active_cycle_only=True,
        )
        if not existing_cycle_docs:
            self._show_info_dialog(
                "Cycle Already Empty",
                "The active cycle has no files yet. Upload files to this cycle first.",
            )
            return

        next_cycle = current_cycle + 1
        if not self._confirm_dialog(
            "Start New Cycle",
            (
                f"Start {self._cycle_label(next_cycle)} for '{slot.label or slot.slot_id}'?\n\n"
                "This keeps existing files in previous cycles and sets this slot back to Missing "
                "until new files are uploaded."
            ),
            confirm_text="Start New Cycle",
            cancel_text="Cancel",
        ):
            return

        slot.active_cycle = next_cycle
        slot.status = "missing"
        permit.events.append(
            PermitEventRecord(
                event_id=uuid4().hex,
                event_type="note",
                event_date=_today_iso(),
                summary=f"Started {self._cycle_label(next_cycle)} for {slot.label or slot.slot_id}",
                detail="Document resubmission cycle advanced.",
            )
        )
        refresh_slot_status_from_documents(permit)
        self._selected_document_id = ""
        self._persist_tracker_data()
        self._refresh_selected_permit_view()

    def _open_selected_document(self, _item: QListWidgetItem | None = None) -> None:
        permit = self._selected_permit()
        if permit is None:
            return

        widget = self._document_file_list_widget
        if _item is None and widget is not None:
            _item = widget.currentItem()
        if _item is not None:
            self._selected_document_id = str(_item.data(Qt.ItemDataRole.UserRole) or "").strip()

        document = self._document_by_id(permit, self._selected_document_id)
        if document is None:
            return

        file_path = self._document_store.resolve_document_path(document.relative_path)
        if file_path is None or not file_path.exists():
            self._show_warning_dialog(
                "Missing File",
                "The selected file could not be found in local storage.",
            )
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(file_path)))

    def _remove_selected_document(self) -> None:
        permit = self._selected_permit()
        if permit is None:
            return
        document = self._document_by_id(permit, self._selected_document_id)
        if document is None:
            return

        document_name = document.original_name or document.stored_name or "selected document"
        confirmed = self._confirm_dialog(
            "Remove File",
            f"Remove '{document_name}' from this permit?",
            confirm_text="Remove",
            cancel_text="Cancel",
            danger=True,
        )
        if not confirmed:
            return

        self._document_store.delete_document_file(document)
        permit.documents = [row for row in permit.documents if row.document_id != document.document_id]
        refresh_slot_status_from_documents(permit)
        self._persist_tracker_data()
        self._refresh_selected_permit_view()

    def _mark_selected_slot_status(self, status: str) -> None:
        permit = self._selected_permit()
        if permit is None:
            return
        slot = self._slot_by_id(permit, self._selected_document_slot_id)
        if slot is None:
            return

        target_status = normalize_document_review_status(status)
        if target_status not in {"accepted", "rejected"}:
            return

        slot_folder_id = normalize_slot_id(slot.folder_id) or normalize_slot_id(slot.slot_id)
        slot_documents = [
            record
            for record in permit.documents
            if normalize_slot_id(record.folder_id) == slot_folder_id
        ]
        if not slot_documents:
            self._show_info_dialog(
                "No Files",
                "This checklist slot has no files to mark yet.",
            )
            return

        widget = self._document_file_list_widget
        if widget is not None:
            current_item = widget.currentItem()
            if current_item is not None:
                self._selected_document_id = str(
                    current_item.data(Qt.ItemDataRole.UserRole) or ""
                ).strip()

        document = self._document_by_id(permit, self._selected_document_id)
        use_bulk_slot_mark = (
            document is None
            or normalize_slot_id(document.folder_id) != slot_folder_id
        )

        if use_bulk_slot_mark:
            action_text = "Accepted" if target_status == "accepted" else "Rejected"
            if not self._confirm_dialog(
                f"Mark {action_text}",
                (
                    f"No file is selected.\n\n"
                    f"Mark all files in '{slot.label or slot.slot_id}' as {action_text.lower()}?\n"
                    "This will update the status of every file in this folder."
                ),
                confirm_text=f"Mark All {action_text}",
                cancel_text="Cancel",
                danger=(target_status == "rejected"),
            ):
                return
            changed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            for record in slot_documents:
                record.review_status = target_status
                record.reviewed_at = changed_at
                record.review_note = ""
        else:
            document_cycle = self._safe_positive_int(document.cycle_index, default=1)
            document.review_status = target_status
            document.reviewed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            document.review_note = ""

            if target_status == "accepted":
                for record in permit.documents:
                    if record.document_id == document.document_id:
                        continue
                    if normalize_slot_id(record.folder_id) != slot_folder_id:
                        continue
                    if self._safe_positive_int(record.cycle_index, default=1) != document_cycle:
                        continue
                    peer_status = normalize_document_review_status(record.review_status)
                    if peer_status in {"uploaded", "accepted"}:
                        record.review_status = "superseded"
                        record.reviewed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

        refresh_slot_status_from_documents(permit)
        self._persist_tracker_data()
        self._refresh_selected_permit_view()

    def _format_byte_size(self, raw_size: int) -> str:
        size = max(0, int(raw_size or 0))
        units = ("B", "KB", "MB", "GB")
        scaled = float(size)
        unit = units[0]
        for candidate in units:
            unit = candidate
            if scaled < 1024.0 or candidate == units[-1]:
                break
            scaled = scaled / 1024.0
        if unit == "B":
            return f"{int(scaled)} {unit}"
        return f"{scaled:.1f} {unit}"

    def _format_imported_value(self, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            return "Unknown import date"
        if len(text) >= 10:
            return text[:10]
        return text
