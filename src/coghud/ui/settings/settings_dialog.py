from __future__ import annotations

from functools import partial
from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QKeySequenceEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from coghud.plugins import DiscoveredPlugin, PluginManager
from coghud.ui.window.frameless_dialog import FramelessDialog


class SettingsDialog(FramelessDialog):
    plugins_changed = Signal()
    dark_mode_changed = Signal(bool)
    palette_shortcut_changed = Signal(bool, str)

    def __init__(
        self,
        plugin_manager: PluginManager,
        parent: QWidget | None = None,
        *,
        dark_mode_enabled: bool = False,
        on_dark_mode_changed: Callable[[bool], None] | None = None,
        palette_shortcut_enabled: bool = True,
        palette_shortcut_keybind: str = "Ctrl+Space",
        on_palette_shortcut_changed: Callable[[bool, str], None] | None = None,
    ) -> None:
        super().__init__(
            title="Settings",
            parent=parent,
            theme_mode=("dark" if dark_mode_enabled else "light"),
        )
        self._plugin_manager = plugin_manager
        self._dark_mode_enabled = bool(dark_mode_enabled)
        self._on_dark_mode_changed = on_dark_mode_changed
        self._palette_shortcut_enabled = bool(palette_shortcut_enabled)
        self._palette_shortcut_keybind = (
            palette_shortcut_keybind.strip() if isinstance(palette_shortcut_keybind, str) else ""
        ) or "Ctrl+Space"
        self._on_palette_shortcut_changed = on_palette_shortcut_changed
        self._refreshing = False
        self._plugin_cache: tuple[DiscoveredPlugin, ...] = ()

        intro = QLabel(
            "Choose one background and combine feature plugins. Changes apply immediately."
        )
        intro.setObjectName("PluginPickerHint")
        intro.setWordWrap(True)

        self._summary_label = QLabel("Active: --")
        self._summary_label.setObjectName("PluginPickerSummary")

        self._status_label = QLabel("Loading plugins...")
        self._status_label.setObjectName("PluginPickerStatus")
        self._status_label.setWordWrap(True)

        self._warning_label = QLabel("")
        self._warning_label.setObjectName("PluginPickerWarning")
        self._warning_label.setWordWrap(True)
        self._warning_label.setVisible(False)

        general_card = QFrame(self.body)
        general_card.setObjectName("PluginGeneralCard")
        general_layout = QVBoxLayout(general_card)
        general_layout.setContentsMargins(10, 10, 10, 10)
        general_layout.setSpacing(8)
        general_title = QLabel("General Settings", general_card)
        general_title.setObjectName("PluginGeneralTitle")
        general_layout.addWidget(general_title)

        theme_row = QHBoxLayout()
        theme_row.setContentsMargins(0, 0, 0, 0)
        theme_row.setSpacing(8)
        theme_label = QLabel("Dark mode", general_card)
        theme_label.setObjectName("PluginGeneralLabel")
        self._dark_mode_toggle = QCheckBox(general_card)
        self._dark_mode_toggle.setObjectName("PluginGeneralToggle")
        self._dark_mode_toggle.setChecked(self._dark_mode_enabled)
        self._dark_mode_toggle.toggled.connect(self._on_dark_mode_toggled)
        theme_row.addWidget(theme_label)
        theme_row.addStretch(1)
        theme_row.addWidget(self._dark_mode_toggle, 0, Qt.AlignmentFlag.AlignRight)
        general_layout.addLayout(theme_row)

        palette_toggle_row = QHBoxLayout()
        palette_toggle_row.setContentsMargins(0, 0, 0, 0)
        palette_toggle_row.setSpacing(8)
        palette_toggle_label = QLabel("Command palette", general_card)
        palette_toggle_label.setObjectName("PluginGeneralLabel")
        self._palette_shortcut_toggle = QCheckBox(general_card)
        self._palette_shortcut_toggle.setObjectName("PluginGeneralToggle")
        self._palette_shortcut_toggle.setChecked(self._palette_shortcut_enabled)
        self._palette_shortcut_toggle.toggled.connect(self._on_palette_shortcut_toggled)
        palette_toggle_row.addWidget(palette_toggle_label)
        palette_toggle_row.addStretch(1)
        palette_toggle_row.addWidget(self._palette_shortcut_toggle, 0, Qt.AlignmentFlag.AlignRight)
        general_layout.addLayout(palette_toggle_row)

        palette_keybind_row = QHBoxLayout()
        palette_keybind_row.setContentsMargins(0, 0, 0, 0)
        palette_keybind_row.setSpacing(8)
        self._palette_shortcut_label = QLabel("Palette keybind", general_card)
        self._palette_shortcut_label.setObjectName("PluginGeneralLabel")
        self._palette_shortcut_keybind_edit = QKeySequenceEdit(general_card)
        self._palette_shortcut_keybind_edit.setObjectName("PaletteShortcutEdit")
        self._palette_shortcut_keybind_edit.setClearButtonEnabled(True)
        self._palette_shortcut_keybind_edit.setKeySequence(
            QKeySequence.fromString(
                self._palette_shortcut_keybind,
                QKeySequence.SequenceFormat.PortableText,
            )
        )
        self._palette_shortcut_keybind_edit.keySequenceChanged.connect(
            self._on_palette_keybind_changed
        )
        palette_keybind_row.addWidget(self._palette_shortcut_label)
        palette_keybind_row.addStretch(1)
        palette_keybind_row.addWidget(
            self._palette_shortcut_keybind_edit,
            0,
            Qt.AlignmentFlag.AlignRight,
        )
        general_layout.addLayout(palette_keybind_row)
        self._sync_palette_shortcut_controls()

        top_controls = QHBoxLayout()
        top_controls.setContentsMargins(0, 0, 0, 0)
        top_controls.setSpacing(8)
        self._kind_filter = QComboBox(self.body)
        self._kind_filter.setObjectName("PluginKindFilter")
        self._kind_filter.currentIndexChanged.connect(self._on_filter_changed)
        top_controls.addWidget(self._kind_filter, 0)
        self._search_input = QLineEdit(self.body)
        self._search_input.setObjectName("PluginPickerSearch")
        self._search_input.setPlaceholderText("Search plugin, id, kind, tag, or description")
        top_controls.addWidget(self._search_input, 1)

        self._scroll = QScrollArea(self.body)
        self._scroll.setObjectName("PluginPickerScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)

        self._scroll_host = QWidget(self._scroll)
        self._scroll_layout = QVBoxLayout(self._scroll_host)
        self._scroll_layout.setContentsMargins(4, 4, 4, 4)
        self._scroll_layout.setSpacing(10)
        self._scroll.setWidget(self._scroll_host)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(8)
        self._reload_button = QPushButton("Reload Plugins", self.body)
        self._reload_button.setObjectName("PluginPickerButton")
        self._clear_background_button = QPushButton("Clear Background", self.body)
        self._clear_background_button.setObjectName("PluginPickerButton")
        self._close_button = QPushButton("Close", self.body)
        self._close_button.setObjectName("PluginPickerButton")
        self._close_button.setProperty("primary", "true")
        footer.addWidget(self._reload_button)
        footer.addWidget(self._clear_background_button)
        footer.addStretch(1)
        footer.addWidget(self._close_button)

        self.body_layout.addWidget(intro)
        self.body_layout.addWidget(general_card)
        self.body_layout.addLayout(top_controls)
        self.body_layout.addWidget(self._summary_label)
        self.body_layout.addWidget(self._status_label)
        self.body_layout.addWidget(self._warning_label)
        self.body_layout.addWidget(self._scroll, 1)
        self.body_layout.addLayout(footer)

        self._reload_button.clicked.connect(self.reload_plugins)
        self._clear_background_button.clicked.connect(self._clear_background_selection)
        self._close_button.clicked.connect(self.accept)
        self._search_input.textChanged.connect(self._on_filter_changed)

        self.resize(760, 760)
        self.reload_plugins()

    def reload_plugins(self) -> None:
        plugins = self._plugin_manager.discover(auto_activate_background=False)
        self._plugin_cache = plugins
        self._refresh_kind_filter_options()
        self._rebuild_sections(self._filtered_plugins())

    def _on_filter_changed(self, *_unused) -> None:
        self._rebuild_sections(self._filtered_plugins())

    def _rebuild_sections(self, plugins: tuple[DiscoveredPlugin, ...]) -> None:
        self._refreshing = True
        scroll_value = self._scroll.verticalScrollBar().value()
        try:
            self._clear_scroll_layout()
            self._set_summary()

            if not self._plugin_cache:
                empty = QLabel("No plugins found in rewrite/plugins.")
                empty.setObjectName("PluginPickerEmpty")
                self._scroll_layout.addWidget(empty)
                self._set_status("No plugins discovered.")
                self._set_warning("")
                return

            if not plugins:
                empty = QLabel("No plugins match your current search.")
                empty.setObjectName("PluginPickerEmpty")
                self._scroll_layout.addWidget(empty)
                self._set_status(
                    self._build_discovery_message(
                        display_count=0,
                        total_count=len(self._plugin_cache),
                        warning_count=len(self._plugin_manager.discovery_errors),
                    )
                )
                self._set_warning(self._format_discovery_warnings())
                return

            grouped: dict[str, list[DiscoveredPlugin]] = {}
            for plugin in plugins:
                grouped.setdefault(plugin.manifest.kind, []).append(plugin)

            for kind in sorted(grouped):
                kind_plugins = sorted(grouped[kind], key=lambda item: item.manifest.name.lower())
                policy = self._plugin_manager.kind_policy(kind)
                section = QFrame(self._scroll_host)
                section.setObjectName("PluginKindCard")
                section_layout = QVBoxLayout(section)
                section_layout.setContentsMargins(12, 10, 12, 10)
                section_layout.setSpacing(8)

                mode = "single" if policy.activation_mode == "single" else "multi"
                title_text = f"{kind} ({mode} active)"
                title = QLabel(title_text, section)
                title.setObjectName("PluginKindTitle")
                section_layout.addWidget(title)

                button_group: QButtonGroup | None = None
                if policy.activation_mode == "single":
                    button_group = QButtonGroup(section)  # keep radios exclusive in section only
                    button_group.setExclusive(True)
                    off_row = QFrame(section)
                    off_row.setObjectName("PluginOptionRow")
                    off_layout = QHBoxLayout(off_row)
                    off_layout.setContentsMargins(8, 6, 8, 6)
                    off_layout.setSpacing(10)
                    off_radio = QRadioButton(off_row)
                    off_radio.setObjectName("PluginOptionToggle")
                    off_radio.setChecked(len(self._plugin_manager.active_plugins(kind=kind)) == 0)
                    off_radio.toggled.connect(partial(self._on_single_kind_selected, kind, None))
                    button_group.addButton(off_radio)
                    off_label = QLabel("Off", off_row)
                    off_label.setObjectName("PluginOptionTitle")
                    off_meta = QLabel("No plugin active for this category", off_row)
                    off_meta.setObjectName("PluginOptionMeta")
                    off_info = QVBoxLayout()
                    off_info.setContentsMargins(0, 0, 0, 0)
                    off_info.setSpacing(2)
                    off_info.addWidget(off_label)
                    off_info.addWidget(off_meta)
                    off_layout.addWidget(off_radio, 0, Qt.AlignmentFlag.AlignTop)
                    off_layout.addLayout(off_info, 1)
                    section_layout.addWidget(off_row)

                for plugin in kind_plugins:
                    row = QFrame(section)
                    row.setObjectName("PluginOptionRow")
                    row_layout = QHBoxLayout(row)
                    row_layout.setContentsMargins(8, 6, 8, 6)
                    row_layout.setSpacing(10)

                    info_layout = QVBoxLayout()
                    info_layout.setContentsMargins(0, 0, 0, 0)
                    info_layout.setSpacing(3)

                    title_line = QLabel(
                        f"{plugin.manifest.name}",
                        row,
                    )
                    title_line.setObjectName("PluginOptionTitle")

                    metadata = [plugin.plugin_id, plugin.manifest.version, plugin.manifest.kind]
                    if plugin.manifest.tags:
                        metadata.append(" ".join(f"#{tag}" for tag in plugin.manifest.tags))
                    meta_line = QLabel(" • ".join(metadata), row)
                    meta_line.setObjectName("PluginOptionMeta")

                    info_layout.addWidget(title_line)
                    info_layout.addWidget(meta_line)
                    if plugin.manifest.description:
                        desc_line = QLabel(plugin.manifest.description, row)
                        desc_line.setObjectName("PluginOptionDescription")
                        desc_line.setWordWrap(True)
                        info_layout.addWidget(desc_line)

                    row.setToolTip(plugin.manifest.description or plugin.plugin_id)

                    if policy.activation_mode == "single":
                        toggle = QRadioButton(row)
                        toggle.setObjectName("PluginOptionToggle")
                        toggle.setChecked(self._plugin_manager.is_active(plugin.plugin_id))
                        toggle.toggled.connect(
                            partial(self._on_single_kind_selected, kind, plugin.plugin_id)
                        )
                        if button_group is not None:
                            button_group.addButton(toggle)
                    else:
                        toggle = QCheckBox(row)
                        toggle.setObjectName("PluginOptionToggle")
                        toggle.setChecked(self._plugin_manager.is_active(plugin.plugin_id))
                        toggle.toggled.connect(partial(self._on_multi_toggled, plugin.plugin_id))

                    row_layout.addWidget(toggle, 0, Qt.AlignmentFlag.AlignTop)
                    row_layout.addLayout(info_layout, 1)
                    section_layout.addWidget(row)

                self._scroll_layout.addWidget(section)

            self._scroll_layout.addStretch(1)
            self._set_status(
                self._build_discovery_message(
                    display_count=len(plugins),
                    total_count=len(self._plugin_cache),
                    warning_count=len(self._plugin_manager.discovery_errors),
                )
            )
            self._set_warning(self._format_discovery_warnings())
        finally:
            self._refreshing = False
            self._scroll.verticalScrollBar().setValue(scroll_value)

    def _on_single_kind_selected(self, kind: str, plugin_id: str | None, checked: bool) -> None:
        if self._refreshing or not checked:
            return
        try:
            if plugin_id is None:
                self._plugin_manager.clear_active(kind=kind)
            else:
                self._plugin_manager.activate(plugin_id)
        except Exception as exc:
            self._set_status(f"Activation failed: {exc}")
            self._rebuild_sections(self._filtered_plugins())
            return
        self.plugins_changed.emit()
        self._rebuild_sections(self._filtered_plugins())

    def _on_multi_toggled(self, plugin_id: str, checked: bool) -> None:
        if self._refreshing:
            return
        try:
            self._plugin_manager.set_enabled(plugin_id, checked)
        except Exception as exc:
            self._set_status(f"Toggle failed: {exc}")
            self._rebuild_sections(self._filtered_plugins())
            return
        self.plugins_changed.emit()
        self._rebuild_sections(self._filtered_plugins())

    def _clear_background_selection(self) -> None:
        self._plugin_manager.clear_active(kind="html-background")
        self.plugins_changed.emit()
        self._rebuild_sections(self._filtered_plugins())

    def _on_dark_mode_toggled(self, enabled: bool) -> None:
        self._dark_mode_enabled = bool(enabled)
        self.set_theme_mode("dark" if self._dark_mode_enabled else "light")
        if callable(self._on_dark_mode_changed):
            self._on_dark_mode_changed(self._dark_mode_enabled)
        self.dark_mode_changed.emit(self._dark_mode_enabled)

    def _on_palette_shortcut_toggled(self, enabled: bool) -> None:
        self._palette_shortcut_enabled = bool(enabled)
        self._sync_palette_shortcut_controls()
        self._emit_palette_shortcut_changed()

    def _on_palette_keybind_changed(self, sequence: QKeySequence) -> None:
        text = sequence.toString(QKeySequence.SequenceFormat.PortableText).strip()
        if not text:
            text = "Ctrl+Space"
            self._palette_shortcut_keybind_edit.blockSignals(True)
            self._palette_shortcut_keybind_edit.setKeySequence(
                QKeySequence.fromString(text, QKeySequence.SequenceFormat.PortableText)
            )
            self._palette_shortcut_keybind_edit.blockSignals(False)
        self._palette_shortcut_keybind = text
        self._emit_palette_shortcut_changed()

    def _sync_palette_shortcut_controls(self) -> None:
        enabled = bool(self._palette_shortcut_enabled)
        self._palette_shortcut_label.setEnabled(enabled)
        self._palette_shortcut_keybind_edit.setEnabled(enabled)

    def _emit_palette_shortcut_changed(self) -> None:
        enabled = bool(self._palette_shortcut_enabled)
        keybind = self._palette_shortcut_keybind or "Ctrl+Space"
        if callable(self._on_palette_shortcut_changed):
            self._on_palette_shortcut_changed(enabled, keybind)
        self.palette_shortcut_changed.emit(enabled, keybind)

    def _refresh_kind_filter_options(self) -> None:
        selected = self._kind_filter.currentData()
        kinds = sorted({plugin.manifest.kind for plugin in self._plugin_cache})

        self._kind_filter.blockSignals(True)
        try:
            self._kind_filter.clear()
            self._kind_filter.addItem("All categories", "__all__")
            for kind in kinds:
                self._kind_filter.addItem(kind, kind)
            target_index = self._kind_filter.findData(selected)
            if target_index < 0:
                target_index = 0
            self._kind_filter.setCurrentIndex(target_index)
        finally:
            self._kind_filter.blockSignals(False)

    def _filtered_plugins(self) -> tuple[DiscoveredPlugin, ...]:
        query = self._search_input.text().strip().lower()
        kind_filter = self._kind_filter.currentData()
        kind_value = kind_filter if isinstance(kind_filter, str) else "__all__"
        if not query:
            if kind_value == "__all__":
                return tuple(self._plugin_cache)
            return tuple(
                plugin for plugin in self._plugin_cache if plugin.manifest.kind == kind_value
            )

        filtered: list[DiscoveredPlugin] = []
        for plugin in self._plugin_cache:
            if kind_value != "__all__" and plugin.manifest.kind != kind_value:
                continue
            haystack = [
                plugin.plugin_id,
                plugin.manifest.name,
                plugin.manifest.kind,
                plugin.manifest.version,
                plugin.manifest.description,
                " ".join(plugin.manifest.tags),
            ]
            combined = " ".join(part.lower() for part in haystack if part)
            if query in combined:
                filtered.append(plugin)
        return tuple(filtered)

    def _set_summary(self) -> None:
        active_ids = self._plugin_manager.active_plugin_ids
        background = self._plugin_manager.active_background_id or "none"
        self._summary_label.setText(
            f"Active plugins: {len(active_ids)} • Active background: {background}"
        )

    def _format_discovery_warnings(self) -> str:
        warnings = self._plugin_manager.discovery_errors
        if not warnings:
            return ""
        preview = "\n".join(f"- {line}" for line in warnings[:3])
        if len(warnings) > 3:
            preview += f"\n- ...and {len(warnings) - 3} more"
        return f"Discovery warnings ({len(warnings)}):\n{preview}"

    def _set_warning(self, text: str) -> None:
        show = bool(text.strip())
        self._warning_label.setText(text)
        self._warning_label.setVisible(show)

    def _clear_scroll_layout(self) -> None:
        while self._scroll_layout.count():
            item = self._scroll_layout.takeAt(0)
            widget = item.widget()
            child_layout = item.layout()
            if widget is not None:
                widget.deleteLater()
            elif child_layout is not None:
                self._clear_layout_recursive(child_layout)

    def _clear_layout_recursive(self, layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            child_layout = item.layout()
            if widget is not None:
                widget.deleteLater()
            elif child_layout is not None:
                self._clear_layout_recursive(child_layout)

    def _build_discovery_message(
        self,
        display_count: int,
        total_count: int,
        warning_count: int,
    ) -> str:
        if display_count != total_count:
            base = f"Showing {display_count} of {total_count} plugin(s)."
        else:
            base = f"Loaded {total_count} plugin(s)."
        if warning_count:
            return f"{base} {warning_count} warning(s)."
        return base

    def _set_status(self, text: str) -> None:
        self._status_label.setText(text)


# Backward compatibility for any in-flight imports.
PluginPickerDialog = SettingsDialog
