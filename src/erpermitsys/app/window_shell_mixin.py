from __future__ import annotations

from PySide6.QtCore import QEvent, QTimer, Qt, QUrl
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import QApplication, QGraphicsDropShadowEffect

from erpermitsys.app.command_runtime import AppCommandContext, CommandRuntime
from erpermitsys.app.settings_store import (
    DEFAULT_PALETTE_SHORTCUT,
    load_active_plugin_ids,
    save_active_plugin_ids,
    save_dark_mode,
    save_palette_shortcut_settings,
)
from erpermitsys.ui.assets import icon_asset_path
from erpermitsys.ui.settings_dialog import SettingsDialog
from erpermitsys.ui.theme import apply_app_theme


class WindowShellMixin:
    def set_command_runtime(self, runtime: CommandRuntime) -> None:
        self._command_runtime = runtime
        runtime.configure_shortcut(
            enabled=self._palette_shortcut_enabled,
            shortcut_text=self._palette_shortcut_keybind,
        )
        self._palette_shortcut_enabled = runtime.shortcut_enabled
        self._palette_shortcut_keybind = runtime.shortcut_text

    def build_command_context(self) -> AppCommandContext:
        return AppCommandContext(
            open_settings_dialog=self.open_settings_dialog,
            close_settings_dialog=self.close_settings_dialog,
            is_settings_dialog_open=self.is_settings_dialog_open,
            minimize_window=self.showMinimized,
            close_app=self.close,
            expand_window=self.expand_window,
            shrink_window=self.shrink_window,
            open_home_view=self._open_home_tracker_view,
            open_admin_contacts=lambda: self._open_contacts_and_jurisdictions_dialog(
                preferred_tab="contacts"
            ),
            open_admin_templates=self._open_document_templates_view,
            open_add_property=self._open_add_property_view,
            open_add_permit=self._open_add_permit_view,
            upload_documents=self._upload_documents_to_slot,
            focus_property_search=self._focus_property_search_input,
            focus_permit_search=self._focus_permit_search_input,
            check_updates=self._on_check_updates_requested,
            switch_storage_backend=self._on_data_storage_backend_changed,
        )

    def open_settings_dialog(self) -> None:
        dialog = self._settings_dialog
        if dialog is None:
            dialog = SettingsDialog(
                self._plugin_manager,
                parent=self,
                dark_mode_enabled=self._dark_mode_enabled,
                on_dark_mode_changed=self._on_dark_mode_changed,
                palette_shortcut_enabled=self._palette_shortcut_enabled,
                palette_shortcut_keybind=self._palette_shortcut_keybind,
                on_palette_shortcut_changed=self._on_palette_shortcut_changed,
                data_storage_folder=str(self._data_storage_folder),
                on_data_storage_folder_changed=self._on_data_storage_folder_changed,
                data_storage_backend=str(self._data_storage_backend),
                on_data_storage_backend_changed=self._on_data_storage_backend_changed,
                on_export_json_backup_requested=self._on_export_json_backup_requested,
                on_import_json_backup_requested=self._on_import_json_backup_requested,
                supabase_settings=self._supabase_settings.to_mapping(redact_api_key=False),
                on_supabase_settings_changed=self._on_supabase_settings_changed,
                supabase_merge_on_switch=self._supabase_merge_on_switch,
                on_supabase_merge_on_switch_changed=self._on_supabase_merge_on_switch_changed,
                app_version=self._app_version,
                on_check_updates_requested=self._on_check_updates_requested,
            )
            dialog.setModal(False)
            dialog.setWindowModality(Qt.WindowModality.NonModal)
            dialog.plugins_changed.connect(self._sync_background_from_plugins)
            dialog.finished.connect(self._on_settings_dialog_finished)
            self._settings_dialog = dialog

        mode = "dark" if self._dark_mode_enabled else "light"
        dialog.set_theme_mode(mode)
        dialog.set_update_check_running(False)
        source = self._auto_update_github_repo or "not configured"
        dialog.set_update_status(f"Current version: {self._app_version} • Source: {source}")
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        self._state_streamer.record("settings.opened", source="main_window", payload={})

    def _open_home_tracker_view(self) -> None:
        if self._panel_stack is None or self._panel_home_view is None:
            return
        current = self._panel_stack.currentWidget()
        if current is self._panel_admin_view:
            self._close_contacts_and_jurisdictions_view()
            return
        if self._active_inline_form_view:
            self._close_inline_form_view(require_confirm=True, action_label="Open Home")
            return
        self._panel_stack.setCurrentWidget(self._panel_home_view)
        self._sync_foreground_layout()

    def _focus_property_search_input(self) -> None:
        widget = self._property_search_input
        if widget is None:
            return
        widget.setFocus()
        widget.selectAll()

    def _focus_permit_search_input(self) -> None:
        widget = self._permit_search_input
        if widget is None:
            return
        widget.setFocus()
        widget.selectAll()

    def close_settings_dialog(self) -> bool:
        dialog = self._settings_dialog
        if dialog is None or not dialog.isVisible():
            return False
        dialog.close()
        return True

    def is_settings_dialog_open(self) -> bool:
        dialog = self._settings_dialog
        return dialog is not None and dialog.isVisible()

    def expand_window(self) -> None:
        if self.isMinimized():
            self.showNormal()
        if self.isFullScreen():
            self.showNormal()
        if not self.isMaximized():
            self.showMaximized()

    def shrink_window(self) -> None:
        if self.isMinimized():
            self.showNormal()
            return
        if self.isMaximized() or self.isFullScreen():
            self.showNormal()
            return

        current_w = max(1, self.width())
        current_h = max(1, self.height())
        target_w = max(self.minimumWidth(), int(round(current_w * 0.9)))
        target_h = max(self.minimumHeight(), int(round(current_h * 0.9)))
        if target_w == current_w and target_h == current_h:
            return
        self.resize(target_w, target_h)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._sync_foreground_layout()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        QTimer.singleShot(0, self._sync_foreground_layout)

    def closeEvent(self, event) -> None:
        skip_dirty_confirmations = bool(getattr(self, "_close_requested_for_update", False))
        if not skip_dirty_confirmations:
            if not self._confirm_discard_inline_form_changes(action_label="Exit App"):
                event.ignore()
                return
            if not self._confirm_discard_admin_view_changes(action_label="Exit App"):
                event.ignore()
                return
            if not self._confirm_discard_template_changes(action_label="Exit App"):
                event.ignore()
                return
        self._shutdown_supabase_realtime_subscription()
        self._persist_tracker_data(show_error_dialog=False)
        dialog = self._settings_dialog
        if dialog is not None:
            dialog.close()
        self._plugin_bridge.shutdown()
        self._plugin_api.shutdown()
        self._plugin_manager.shutdown()
        self._state_streamer.record("window.closed", source="main_window", payload={})
        super().closeEvent(event)

    def eventFilter(self, watched, event) -> bool:
        if watched is self._scene_widget and event.type() in (
            QEvent.Type.Resize,
            QEvent.Type.Show,
            QEvent.Type.LayoutRequest,
        ):
            QTimer.singleShot(0, self._sync_foreground_layout)
        return super().eventFilter(watched, event)

    def _on_settings_dialog_finished(self, _result: int) -> None:
        dialog = self._settings_dialog
        self._settings_dialog = None
        if dialog is not None:
            dialog.deleteLater()
        self._state_streamer.record("settings.closed", source="main_window", payload={})
        self._sync_background_from_plugins()

    def _on_dark_mode_changed(self, enabled: bool) -> None:
        self._dark_mode_enabled = bool(enabled)
        save_dark_mode(self._dark_mode_enabled)
        mode = "dark" if self._dark_mode_enabled else "light"
        app = QApplication.instance()
        if app is not None:
            apply_app_theme(app, mode=mode)
        self.set_theme_mode(mode)
        self.setWindowIcon(QIcon(icon_asset_path("resize_handle.png", mode=mode)))
        if self._settings_dialog is not None:
            self._settings_dialog.set_theme_mode(mode)
        # Rebuild permit workspace visuals so timeline connector arrows swap
        # between black/white assets immediately on theme change.
        self._refresh_selected_permit_view()
        self._refresh_document_templates_view()
        self._apply_settings_button_effect()
        self._sync_foreground_layout()
        self._state_streamer.record("theme.changed", source="main_window", payload={"mode": mode})

    def _on_palette_shortcut_changed(self, enabled: bool, keybind: str) -> None:
        self._palette_shortcut_enabled = bool(enabled)
        self._palette_shortcut_keybind = (
            keybind.strip() if isinstance(keybind, str) else ""
        ) or DEFAULT_PALETTE_SHORTCUT

        runtime = self._command_runtime
        if runtime is not None:
            runtime.configure_shortcut(
                enabled=self._palette_shortcut_enabled,
                shortcut_text=self._palette_shortcut_keybind,
            )
            self._palette_shortcut_enabled = runtime.shortcut_enabled
            self._palette_shortcut_keybind = runtime.shortcut_text

        save_palette_shortcut_settings(
            self._palette_shortcut_enabled,
            self._palette_shortcut_keybind,
        )
        self._state_streamer.record(
            "palette.shortcut_settings_changed",
            source="main_window",
            payload={
                "enabled": self._palette_shortcut_enabled,
                "keybind": self._palette_shortcut_keybind,
            },
        )

    def _sync_background_from_plugins(self) -> None:
        self._persist_active_plugins()
        background_url = self._plugin_manager.active_background_url()

        if self._settings_button is not None:
            active_count = len(self._plugin_manager.active_plugin_ids)
            self._settings_button.setToolTip(f"{active_count} plugin(s) active")

        if self._stack is None or self._fallback_widget is None:
            return

        if background_url and self._background_view is not None:
            self._stack.setCurrentWidget(self._background_view)
            self._sync_foreground_layout()
            self._schedule_background_url_load(background_url)
            QTimer.singleShot(0, self._restore_settings_dialog_z_order)
            return

        self._current_background_url = None
        self._stack.setCurrentWidget(self._fallback_widget)
        self._sync_foreground_layout()
        QTimer.singleShot(0, self._restore_settings_dialog_z_order)

    def _restore_active_plugins(self) -> None:
        for plugin_id in load_active_plugin_ids(default=()):
            if self._plugin_manager.get_plugin(plugin_id) is None:
                continue
            try:
                self._plugin_manager.activate(plugin_id)
            except Exception:
                continue

    def _persist_active_plugins(self) -> None:
        save_active_plugin_ids(self._plugin_manager.active_plugin_ids)

    def _schedule_background_url_load(self, background_url: str) -> None:
        if self._background_view is None:
            return
        if self._current_background_url == background_url:
            return
        QTimer.singleShot(0, lambda url=background_url: self._load_background_url(url))

    def _load_background_url(self, background_url: str) -> None:
        if self._background_view is None:
            return
        if self._current_background_url == background_url:
            return
        if self._plugin_manager.active_background_url() != background_url:
            return
        self._background_view.setUrl(QUrl(background_url))
        self._current_background_url = background_url

    def _on_background_load_finished(self, _ok: bool) -> None:
        self._raise_foreground_widgets()
        QTimer.singleShot(0, self._restore_settings_dialog_z_order)

    def _restore_settings_dialog_z_order(self) -> None:
        dialog = self._settings_dialog
        if dialog is None or not dialog.isVisible():
            return
        try:
            dialog.raise_()
            dialog.activateWindow()
        except Exception:
            return

    def _position_settings_button(self) -> None:
        if not self._settings_button or not self._scene_widget:
            return
        if self._scene_widget.width() <= 0 or self._scene_widget.height() <= 0:
            return
        margin = 16
        self._settings_button.adjustSize()
        x = margin
        y = max(margin, self._scene_widget.height() - self._settings_button.height() - margin)
        self._settings_button.move(x, y)
        if not self._settings_button.isVisible():
            self._settings_button.show()

    def _position_tracker_panels(self) -> None:
        if self._panel_host is None or self._scene_widget is None:
            return

        scene_width = self._scene_widget.width()
        scene_height = self._scene_widget.height()
        if scene_width <= 0 or scene_height <= 0:
            return

        desired_width = max(760, int(scene_width * 0.95))
        desired_height = max(520, int(scene_height * 0.86))

        content_width = min(desired_width, max(1, scene_width - 12))
        content_height = min(desired_height, max(1, scene_height - 12))

        x = max(0, int((scene_width - content_width) / 2))
        y = max(0, int((scene_height - content_height) / 2))
        self._panel_host.setGeometry(x, y, int(content_width), int(content_height))
        if not self._panel_host.isVisible():
            self._panel_host.show()

    def _raise_foreground_widgets(self) -> None:
        if self._panel_host is not None:
            self._panel_host.raise_()
        if self._settings_button is not None:
            self._settings_button.raise_()
        resize_handle = getattr(self, "_resize_handle", None)
        if resize_handle is not None:
            try:
                resize_handle.setAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop, True)
                resize_handle.raise_()
            except Exception:
                pass

    def _sync_foreground_layout(self) -> None:
        self._position_tracker_panels()
        self._sync_admin_editor_field_widths()
        self._sync_inline_form_card_widths()
        self._sync_permit_workspace_blur_overlay()
        self._position_settings_button()
        self._raise_foreground_widgets()

    def _sync_inline_form_card_widths(self) -> None:
        host = self._panel_host
        if host is None:
            return
        host_width = max(1, host.width())
        max_allowed = max(320, host_width - 56)
        target_width = max(360, int(round(host_width * 0.35)))
        target_width = min(target_width, 720, max_allowed)
        for card in self._inline_form_cards:
            if card is None:
                continue
            if card.width() == target_width and card.minimumWidth() == target_width:
                continue
            card.setMinimumWidth(target_width)
            card.setMaximumWidth(target_width)
        self._set_admin_entity_color_picker_open(
            "property",
            self._add_property_color_picker_open,
            animate=False,
        )

    def _apply_settings_button_effect(self) -> None:
        if self._settings_button is None:
            return
        shadow = self._settings_button_shadow
        if shadow is None:
            shadow = QGraphicsDropShadowEffect(self._settings_button)
            self._settings_button.setGraphicsEffect(shadow)
            self._settings_button_shadow = shadow

        if self._dark_mode_enabled:
            shadow.setBlurRadius(34.0)
            shadow.setOffset(0.0, 7.0)
            shadow.setColor(QColor(33, 109, 185, 138))
            return

        shadow.setBlurRadius(28.0)
        shadow.setOffset(0.0, 5.0)
        shadow.setColor(QColor(86, 150, 208, 112))
