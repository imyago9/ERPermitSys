from __future__ import annotations

from functools import partial
from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
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

from erpermitsys.app.data_store import (
    BACKEND_LOCAL_SQLITE,
    BACKEND_SUPABASE,
    DEFAULT_DATA_FILE_NAME,
)
from erpermitsys.app.settings_store import (
    DEFAULT_SUPABASE_SCHEMA,
    DEFAULT_SUPABASE_STORAGE_BUCKET,
    DEFAULT_SUPABASE_STORAGE_PREFIX,
    DEFAULT_SUPABASE_TRACKER_TABLE,
)
from erpermitsys.plugins import DiscoveredPlugin, PluginManager
from erpermitsys.ui.widgets.edge_locked_scroll_area import EdgeLockedScrollArea
from erpermitsys.ui.window.frameless_dialog import FramelessDialog


class SettingsDialog(FramelessDialog):
    plugins_changed = Signal()
    dark_mode_changed = Signal(bool)
    palette_shortcut_changed = Signal(bool, str)
    data_storage_folder_changed = Signal(str)
    data_storage_backend_changed = Signal(str)
    supabase_settings_changed = Signal(dict)
    supabase_merge_on_switch_changed = Signal(bool)
    check_updates_requested = Signal()

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
        data_storage_folder: str = "",
        on_data_storage_folder_changed: Callable[[str], str] | None = None,
        data_storage_backend: str = BACKEND_LOCAL_SQLITE,
        on_data_storage_backend_changed: Callable[[str], str] | None = None,
        on_export_json_backup_requested: Callable[[str], str] | None = None,
        on_import_json_backup_requested: Callable[[str], bool] | None = None,
        supabase_settings: dict[str, str] | None = None,
        on_supabase_settings_changed: Callable[[dict[str, object]], dict[str, str] | None] | None = None,
        supabase_merge_on_switch: bool = True,
        on_supabase_merge_on_switch_changed: Callable[[bool], bool] | None = None,
        app_version: str = "",
        on_check_updates_requested: Callable[[], None] | None = None,
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
        self._data_storage_folder = (
            data_storage_folder.strip()
            if isinstance(data_storage_folder, str)
            else ""
        )
        self._on_data_storage_folder_changed = on_data_storage_folder_changed
        self._data_storage_backend = self._normalize_backend(data_storage_backend)
        self._on_data_storage_backend_changed = on_data_storage_backend_changed
        self._on_export_json_backup_requested = on_export_json_backup_requested
        self._on_import_json_backup_requested = on_import_json_backup_requested
        initial_supabase = supabase_settings if isinstance(supabase_settings, dict) else {}
        self._supabase_url = str(initial_supabase.get("url", "") or "").strip()
        self._supabase_api_key = str(initial_supabase.get("api_key", "") or "").strip()
        self._supabase_schema = (
            str(initial_supabase.get("schema", "") or "").strip() or DEFAULT_SUPABASE_SCHEMA
        )
        self._supabase_table = (
            str(initial_supabase.get("tracker_table", "") or "").strip()
            or DEFAULT_SUPABASE_TRACKER_TABLE
        )
        self._supabase_bucket = (
            str(initial_supabase.get("storage_bucket", "") or "").strip()
            or DEFAULT_SUPABASE_STORAGE_BUCKET
        )
        self._supabase_prefix = (
            str(initial_supabase.get("storage_prefix", "") or "").strip()
            or DEFAULT_SUPABASE_STORAGE_PREFIX
        )
        self._on_supabase_settings_changed = on_supabase_settings_changed
        self._supabase_merge_on_switch = bool(supabase_merge_on_switch)
        self._on_supabase_merge_on_switch_changed = on_supabase_merge_on_switch_changed
        self._app_version = app_version.strip() if isinstance(app_version, str) else ""
        self._on_check_updates_requested = on_check_updates_requested
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

        backend_row = QHBoxLayout()
        backend_row.setContentsMargins(0, 0, 0, 0)
        backend_row.setSpacing(8)
        backend_label = QLabel("Data backend", general_card)
        backend_label.setObjectName("PluginGeneralLabel")
        self._data_storage_backend_combo = QComboBox(general_card)
        self._data_storage_backend_combo.setObjectName("PluginKindFilter")
        self._data_storage_backend_combo.addItem("Local SQLite", BACKEND_LOCAL_SQLITE)
        self._data_storage_backend_combo.addItem("Supabase", BACKEND_SUPABASE)
        self._data_storage_backend_combo.currentIndexChanged.connect(
            self._on_data_storage_backend_selected
        )
        backend_row.addWidget(backend_label)
        backend_row.addStretch(1)
        backend_row.addWidget(self._data_storage_backend_combo, 0, Qt.AlignmentFlag.AlignRight)
        general_layout.addLayout(backend_row)

        supabase_merge_row = QHBoxLayout()
        supabase_merge_row.setContentsMargins(0, 0, 0, 0)
        supabase_merge_row.setSpacing(8)
        supabase_merge_label = QLabel("On Supabase connect", general_card)
        supabase_merge_label.setObjectName("PluginGeneralLabel")
        self._supabase_merge_toggle = QCheckBox(general_card)
        self._supabase_merge_toggle.setObjectName("PluginGeneralToggle")
        self._supabase_merge_toggle.setChecked(self._supabase_merge_on_switch)
        self._supabase_merge_toggle.toggled.connect(self._on_supabase_merge_on_switch_toggled)
        supabase_merge_row.addWidget(supabase_merge_label)
        supabase_merge_row.addStretch(1)
        supabase_merge_row.addWidget(self._supabase_merge_toggle, 0, Qt.AlignmentFlag.AlignRight)
        general_layout.addLayout(supabase_merge_row)

        self._supabase_merge_hint = QLabel("", general_card)
        self._supabase_merge_hint.setObjectName("PluginPickerStatus")
        self._supabase_merge_hint.setWordWrap(True)
        general_layout.addWidget(self._supabase_merge_hint)

        data_folder_label = QLabel("Data folder / cache", general_card)
        data_folder_label.setObjectName("PluginGeneralLabel")
        general_layout.addWidget(data_folder_label)

        data_folder_row = QHBoxLayout()
        data_folder_row.setContentsMargins(0, 0, 0, 0)
        data_folder_row.setSpacing(8)

        self._data_storage_folder_input = QLineEdit(general_card)
        self._data_storage_folder_input.setObjectName("PluginPickerSearch")
        self._data_storage_folder_input.setReadOnly(True)
        self._data_storage_folder_input.setPlaceholderText("Choose local data folder")
        data_folder_row.addWidget(self._data_storage_folder_input, 1)

        self._browse_data_folder_button = QPushButton("Browse...", general_card)
        self._browse_data_folder_button.setObjectName("PluginPickerButton")
        self._browse_data_folder_button.clicked.connect(self._on_browse_data_folder_clicked)
        data_folder_row.addWidget(self._browse_data_folder_button, 0)

        self._default_data_folder_button = QPushButton("Default", general_card)
        self._default_data_folder_button.setObjectName("PluginPickerButton")
        self._default_data_folder_button.clicked.connect(self._on_default_data_folder_clicked)
        data_folder_row.addWidget(self._default_data_folder_button, 0)

        general_layout.addLayout(data_folder_row)

        json_transfer_row = QHBoxLayout()
        json_transfer_row.setContentsMargins(0, 0, 0, 0)
        json_transfer_row.setSpacing(8)

        self._export_json_button = QPushButton("Export JSON...", general_card)
        self._export_json_button.setObjectName("PluginPickerButton")
        self._export_json_button.clicked.connect(self._on_export_json_clicked)
        json_transfer_row.addWidget(self._export_json_button, 0)

        self._import_json_button = QPushButton("Import JSON...", general_card)
        self._import_json_button.setObjectName("PluginPickerButton")
        self._import_json_button.clicked.connect(self._on_import_json_clicked)
        json_transfer_row.addWidget(self._import_json_button, 0)

        json_transfer_row.addStretch(1)
        general_layout.addLayout(json_transfer_row)

        self._supabase_card = QFrame(general_card)
        self._supabase_card.setObjectName("PluginGeneralCard")
        supabase_layout = QVBoxLayout(self._supabase_card)
        supabase_layout.setContentsMargins(8, 8, 8, 8)
        supabase_layout.setSpacing(7)
        supabase_title = QLabel("Supabase Settings", self._supabase_card)
        supabase_title.setObjectName("PluginGeneralTitle")
        supabase_layout.addWidget(supabase_title)

        self._supabase_url_input = QLineEdit(self._supabase_card)
        self._supabase_url_input.setObjectName("PluginPickerSearch")
        self._supabase_url_input.setPlaceholderText("https://<project-ref>.supabase.co")
        supabase_layout.addWidget(self._labeled_setting("Project URL", self._supabase_url_input))

        self._supabase_api_key_input = QLineEdit(self._supabase_card)
        self._supabase_api_key_input.setObjectName("PluginPickerSearch")
        self._supabase_api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._supabase_api_key_input.setPlaceholderText("Service role key or anon key")
        supabase_layout.addWidget(self._labeled_setting("API key", self._supabase_api_key_input))

        self._supabase_schema_input = QLineEdit(self._supabase_card)
        self._supabase_schema_input.setObjectName("PluginPickerSearch")
        self._supabase_schema_input.setPlaceholderText(DEFAULT_SUPABASE_SCHEMA)
        supabase_layout.addWidget(self._labeled_setting("Schema", self._supabase_schema_input))

        self._supabase_table_input = QLineEdit(self._supabase_card)
        self._supabase_table_input.setObjectName("PluginPickerSearch")
        self._supabase_table_input.setPlaceholderText(DEFAULT_SUPABASE_TRACKER_TABLE)
        supabase_layout.addWidget(self._labeled_setting("State table", self._supabase_table_input))

        self._supabase_bucket_input = QLineEdit(self._supabase_card)
        self._supabase_bucket_input.setObjectName("PluginPickerSearch")
        self._supabase_bucket_input.setPlaceholderText(DEFAULT_SUPABASE_STORAGE_BUCKET)
        supabase_layout.addWidget(self._labeled_setting("Storage bucket", self._supabase_bucket_input))

        self._supabase_prefix_input = QLineEdit(self._supabase_card)
        self._supabase_prefix_input.setObjectName("PluginPickerSearch")
        self._supabase_prefix_input.setPlaceholderText(DEFAULT_SUPABASE_STORAGE_PREFIX)
        supabase_layout.addWidget(self._labeled_setting("Storage prefix", self._supabase_prefix_input))

        apply_supabase_row = QHBoxLayout()
        apply_supabase_row.setContentsMargins(0, 0, 0, 0)
        apply_supabase_row.setSpacing(8)
        self._apply_supabase_button = QPushButton("Apply Supabase Settings", self._supabase_card)
        self._apply_supabase_button.setObjectName("PluginPickerButton")
        self._apply_supabase_button.clicked.connect(self._on_apply_supabase_settings_clicked)
        apply_supabase_row.addWidget(self._apply_supabase_button, 0)
        apply_supabase_row.addStretch(1)
        supabase_layout.addLayout(apply_supabase_row)

        general_layout.addWidget(self._supabase_card)

        updates_title = QLabel("App Updates", general_card)
        updates_title.setObjectName("PluginGeneralTitle")
        general_layout.addWidget(updates_title)

        update_hint = QLabel(
            "Startup checks are always enabled for this build.",
            general_card,
        )
        update_hint.setObjectName("PluginGeneralLabel")
        update_hint.setWordWrap(True)
        general_layout.addWidget(update_hint)

        update_actions_row = QHBoxLayout()
        update_actions_row.setContentsMargins(0, 0, 0, 0)
        update_actions_row.setSpacing(8)
        self._check_updates_button = QPushButton("Check for Updates", general_card)
        self._check_updates_button.setObjectName("PluginPickerButton")
        self._check_updates_button.clicked.connect(self._on_check_updates_clicked)
        update_actions_row.addWidget(self._check_updates_button, 0)
        update_actions_row.addStretch(1)
        general_layout.addLayout(update_actions_row)

        self._auto_update_status_label = QLabel("", general_card)
        self._auto_update_status_label.setObjectName("PluginPickerStatus")
        self._auto_update_status_label.setWordWrap(True)
        general_layout.addWidget(self._auto_update_status_label)

        self._backend_hint = QLabel("", general_card)
        self._backend_hint.setObjectName("PluginPickerStatus")
        self._backend_hint.setWordWrap(True)
        general_layout.addWidget(self._backend_hint)
        self._set_backend_combo(self._data_storage_backend)
        self._apply_supabase_settings_mapping(self._current_supabase_settings())
        self._sync_backend_controls()
        self._sync_supabase_merge_on_switch_hint()
        self._set_data_storage_folder_display(self._data_storage_folder)
        self._set_update_status(
            f"Current version: {self._app_version}" if self._app_version else "Current version: unknown"
        )

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

        self._scroll = EdgeLockedScrollArea(self.body)
        self._scroll.setObjectName("PluginPickerScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)

        self._scroll_host = QWidget(self._scroll)
        self._scroll_layout = QVBoxLayout(self._scroll_host)
        self._scroll_layout.setContentsMargins(0, 0, 0, 0)
        self._scroll_layout.setSpacing(10)

        self._plugin_sections_host = QWidget(self._scroll_host)
        self._plugin_sections_layout = QVBoxLayout(self._plugin_sections_host)
        self._plugin_sections_layout.setContentsMargins(4, 4, 4, 4)
        self._plugin_sections_layout.setSpacing(10)
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

        self._scroll_layout.addWidget(intro)
        self._scroll_layout.addWidget(general_card)
        self._scroll_layout.addLayout(top_controls)
        self._scroll_layout.addWidget(self._summary_label)
        self._scroll_layout.addWidget(self._status_label)
        self._scroll_layout.addWidget(self._warning_label)
        self._scroll_layout.addWidget(self._plugin_sections_host)
        self._scroll_layout.addLayout(footer)
        self.body_layout.addWidget(self._scroll, 1)

        self._reload_button.clicked.connect(self.reload_plugins)
        self._clear_background_button.clicked.connect(self._clear_background_selection)
        self._close_button.clicked.connect(self.accept)
        self._search_input.textChanged.connect(self._on_filter_changed)

        self.resize(760, 840)
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
                self._plugin_sections_layout.addWidget(empty)
                self._set_status("No plugins discovered.")
                self._set_warning("")
                return

            if not plugins:
                empty = QLabel("No plugins match your current search.")
                empty.setObjectName("PluginPickerEmpty")
                self._plugin_sections_layout.addWidget(empty)
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

                self._plugin_sections_layout.addWidget(section)

            self._plugin_sections_layout.addStretch(1)
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

    def _sync_supabase_merge_on_switch_hint(self) -> None:
        if self._supabase_merge_on_switch:
            self._supabase_merge_hint.setText(
                "On Local -> Supabase switch, merge local data into Supabase "
                "(new records only; duplicate addresses are skipped)."
            )
        else:
            self._supabase_merge_hint.setText(
                "On Local -> Supabase switch, load Supabase data only "
                "(do not copy or merge local records)."
            )

    def _on_supabase_merge_on_switch_toggled(self, enabled: bool) -> None:
        applied = bool(enabled)
        if callable(self._on_supabase_merge_on_switch_changed):
            try:
                applied = bool(self._on_supabase_merge_on_switch_changed(applied))
            except Exception as exc:
                self._set_status(f"Supabase switch behavior update failed: {exc}")
                self._supabase_merge_toggle.blockSignals(True)
                self._supabase_merge_toggle.setChecked(self._supabase_merge_on_switch)
                self._supabase_merge_toggle.blockSignals(False)
                return
        self._supabase_merge_on_switch = applied
        if self._supabase_merge_toggle.isChecked() != applied:
            self._supabase_merge_toggle.blockSignals(True)
            self._supabase_merge_toggle.setChecked(applied)
            self._supabase_merge_toggle.blockSignals(False)
        self._sync_supabase_merge_on_switch_hint()
        if applied:
            self._set_status("Supabase connect behavior: merge local data.")
        else:
            self._set_status("Supabase connect behavior: load remote only.")
        self.supabase_merge_on_switch_changed.emit(applied)

    def _emit_palette_shortcut_changed(self) -> None:
        enabled = bool(self._palette_shortcut_enabled)
        keybind = self._palette_shortcut_keybind or "Ctrl+Space"
        if callable(self._on_palette_shortcut_changed):
            self._on_palette_shortcut_changed(enabled, keybind)
        self.palette_shortcut_changed.emit(enabled, keybind)

    def _on_check_updates_clicked(self) -> None:
        self._set_update_check_running(True)
        self._set_update_status("Checking for updates...")
        if callable(self._on_check_updates_requested):
            self._on_check_updates_requested()
        else:
            self._set_update_check_running(False)
        self.check_updates_requested.emit()

    def _set_update_status(self, text: str) -> None:
        message = text.strip() if isinstance(text, str) else ""
        self._auto_update_status_label.setText(message)

    def set_update_status(self, text: str) -> None:
        self._set_update_status(text)

    def _set_update_check_running(self, running: bool) -> None:
        self._check_updates_button.setEnabled(not bool(running))

    def set_update_check_running(self, running: bool) -> None:
        self._set_update_check_running(running)

    def _set_data_storage_folder_display(self, folder: str) -> None:
        text = folder.strip() if isinstance(folder, str) else ""
        self._data_storage_folder = text
        self._data_storage_folder_input.setText(text)
        self._data_storage_folder_input.setCursorPosition(0)

    def _normalize_backend(self, backend: str) -> str:
        normalized = str(backend or "").strip().lower()
        if normalized == BACKEND_SUPABASE:
            return BACKEND_SUPABASE
        return BACKEND_LOCAL_SQLITE

    def _set_backend_combo(self, backend: str) -> None:
        target = self._normalize_backend(backend)
        combo = self._data_storage_backend_combo
        combo.blockSignals(True)
        try:
            index = combo.findData(target)
            combo.setCurrentIndex(index if index >= 0 else 0)
        finally:
            combo.blockSignals(False)
        self._data_storage_backend = target

    def _sync_backend_controls(self) -> None:
        is_supabase = self._data_storage_backend == BACKEND_SUPABASE
        self._supabase_card.setVisible(is_supabase)
        source = "Supabase" if is_supabase else "Local SQLite"
        self._backend_hint.setText(f"Data backend: {source}")

    def _on_data_storage_backend_selected(self, *_args: object) -> None:
        backend = self._data_storage_backend_combo.currentData()
        requested_backend = backend if isinstance(backend, str) else BACKEND_LOCAL_SQLITE
        self._apply_data_storage_backend_change(requested_backend)

    def _apply_data_storage_backend_change(self, requested_backend: str) -> None:
        requested = self._normalize_backend(requested_backend)
        if requested == BACKEND_SUPABASE and self._data_storage_backend != BACKEND_SUPABASE:
            if not self._ensure_supabase_settings_for_switch():
                self._set_backend_combo(self._data_storage_backend)
                self._sync_backend_controls()
                return
        applied = requested
        if callable(self._on_data_storage_backend_changed):
            try:
                callback_value = self._on_data_storage_backend_changed(requested)
            except Exception as exc:
                self._set_status(f"Data backend update failed: {exc}")
                self._set_backend_combo(self._data_storage_backend)
                self._sync_backend_controls()
                return
            if isinstance(callback_value, str) and callback_value.strip():
                applied = self._normalize_backend(callback_value)
        self._set_backend_combo(applied)
        self._sync_backend_controls()
        self._set_status(f"Data backend: {'Supabase' if applied == BACKEND_SUPABASE else 'Local SQLite'}")
        self.data_storage_backend_changed.emit(applied)

    def _collect_supabase_input_values(self) -> dict[str, str]:
        return {
            "url": self._supabase_url_input.text().strip(),
            "api_key": self._supabase_api_key_input.text().strip(),
            "schema": self._supabase_schema_input.text().strip() or DEFAULT_SUPABASE_SCHEMA,
            "tracker_table": self._supabase_table_input.text().strip() or DEFAULT_SUPABASE_TRACKER_TABLE,
            "storage_bucket": self._supabase_bucket_input.text().strip()
            or DEFAULT_SUPABASE_STORAGE_BUCKET,
            "storage_prefix": self._supabase_prefix_input.text().strip()
            or DEFAULT_SUPABASE_STORAGE_PREFIX,
        }

    def _apply_supabase_settings_values(
        self,
        values: dict[str, str],
        *,
        success_status: str | None = None,
    ) -> bool:
        applied = dict(values)
        if callable(self._on_supabase_settings_changed):
            try:
                callback_value = self._on_supabase_settings_changed(dict(values))
            except Exception as exc:
                self._set_status(f"Supabase settings update failed: {exc}")
                return False
            if isinstance(callback_value, dict):
                applied = {
                    "url": str(callback_value.get("url", values["url"]) or "").strip(),
                    "api_key": str(callback_value.get("api_key", values["api_key"]) or "").strip(),
                    "schema": str(callback_value.get("schema", values["schema"]) or "").strip(),
                    "tracker_table": str(
                        callback_value.get("tracker_table", values["tracker_table"]) or ""
                    ).strip(),
                    "storage_bucket": str(
                        callback_value.get("storage_bucket", values["storage_bucket"]) or ""
                    ).strip(),
                    "storage_prefix": str(
                        callback_value.get("storage_prefix", values["storage_prefix"]) or ""
                    ).strip(),
                }
        self._apply_supabase_settings_mapping(applied)
        if isinstance(success_status, str) and success_status.strip():
            self._set_status(success_status)
        self.supabase_settings_changed.emit(dict(self._current_supabase_settings()))
        return True

    def _prompt_for_supabase_credentials(
        self,
        *,
        url: str,
        api_key: str,
    ) -> tuple[str, str] | None:
        dialog = FramelessDialog(
            title="Configure Supabase",
            parent=self,
            theme_mode=("dark" if self._dark_mode_enabled else "light"),
        )
        dialog.setMinimumSize(560, 280)
        dialog.resize(620, 320)

        prompt = QLabel(
            "Enter your Supabase project URL and API key to use Supabase storage. "
            "These values are saved locally on this device and reused automatically.",
            dialog.body,
        )
        prompt.setWordWrap(True)
        prompt.setObjectName("PluginPickerHint")
        dialog.body_layout.addWidget(prompt)

        url_input = QLineEdit(dialog.body)
        url_input.setObjectName("PluginPickerSearch")
        url_input.setPlaceholderText("https://<project-ref>.supabase.co")
        url_input.setText(url)
        dialog.body_layout.addWidget(self._labeled_setting("Project URL", url_input, parent=dialog.body))

        api_key_input = QLineEdit(dialog.body)
        api_key_input.setObjectName("PluginPickerSearch")
        api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        api_key_input.setPlaceholderText("Service role key or anon key")
        api_key_input.setText(api_key)
        dialog.body_layout.addWidget(self._labeled_setting("API key", api_key_input, parent=dialog.body))

        validation_label = QLabel("", dialog.body)
        validation_label.setObjectName("PluginPickerWarning")
        validation_label.setWordWrap(True)
        validation_label.setVisible(False)
        dialog.body_layout.addWidget(validation_label)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(8)
        footer.addStretch(1)

        cancel_button = QPushButton("Cancel", dialog.body)
        cancel_button.setObjectName("PluginPickerButton")
        cancel_button.clicked.connect(dialog.reject)
        footer.addWidget(cancel_button)

        save_button = QPushButton("Save & Use Supabase", dialog.body)
        save_button.setObjectName("PluginPickerButton")
        save_button.setProperty("primary", "true")
        footer.addWidget(save_button)
        dialog.body_layout.addLayout(footer)

        def _accept_if_valid() -> None:
            entered_url = url_input.text().strip()
            entered_api_key = api_key_input.text().strip()
            if not entered_url or not entered_api_key:
                validation_label.setText("Project URL and API key are required.")
                validation_label.setVisible(True)
                if not entered_url:
                    url_input.setFocus()
                else:
                    api_key_input.setFocus()
                return
            validation_label.setText("")
            validation_label.setVisible(False)
            dialog.accept()

        save_button.clicked.connect(_accept_if_valid)
        url_input.returnPressed.connect(_accept_if_valid)
        api_key_input.returnPressed.connect(_accept_if_valid)
        if url_input.text().strip():
            api_key_input.setFocus()
        else:
            url_input.setFocus()

        if dialog.exec() != dialog.DialogCode.Accepted:
            return None
        return url_input.text().strip(), api_key_input.text().strip()

    def _ensure_supabase_settings_for_switch(self) -> bool:
        values = self._collect_supabase_input_values()
        if not values["url"] or not values["api_key"]:
            prompted = self._prompt_for_supabase_credentials(
                url=values["url"],
                api_key=values["api_key"],
            )
            if prompted is None:
                self._set_status("Supabase setup canceled.")
                return False
            values["url"], values["api_key"] = prompted

        if not self._apply_supabase_settings_values(values):
            return False

        configured = self._current_supabase_settings()
        if configured["url"] and configured["api_key"]:
            return True
        self._set_status("Supabase backend requires both Project URL and API key.")
        return False

    def _current_supabase_settings(self) -> dict[str, str]:
        return {
            "url": self._supabase_url,
            "api_key": self._supabase_api_key,
            "schema": self._supabase_schema or DEFAULT_SUPABASE_SCHEMA,
            "tracker_table": self._supabase_table or DEFAULT_SUPABASE_TRACKER_TABLE,
            "storage_bucket": self._supabase_bucket or DEFAULT_SUPABASE_STORAGE_BUCKET,
            "storage_prefix": self._supabase_prefix or DEFAULT_SUPABASE_STORAGE_PREFIX,
        }

    def _apply_supabase_settings_mapping(self, values: dict[str, str]) -> None:
        self._supabase_url = str(values.get("url", "") or "").strip()
        self._supabase_api_key = str(values.get("api_key", "") or "").strip()
        self._supabase_schema = (
            str(values.get("schema", "") or "").strip() or DEFAULT_SUPABASE_SCHEMA
        )
        self._supabase_table = (
            str(values.get("tracker_table", "") or "").strip() or DEFAULT_SUPABASE_TRACKER_TABLE
        )
        self._supabase_bucket = (
            str(values.get("storage_bucket", "") or "").strip() or DEFAULT_SUPABASE_STORAGE_BUCKET
        )
        self._supabase_prefix = (
            str(values.get("storage_prefix", "") or "").strip() or DEFAULT_SUPABASE_STORAGE_PREFIX
        )

        self._supabase_url_input.setText(self._supabase_url)
        self._supabase_api_key_input.setText(self._supabase_api_key)
        self._supabase_schema_input.setText(self._supabase_schema)
        self._supabase_table_input.setText(self._supabase_table)
        self._supabase_bucket_input.setText(self._supabase_bucket)
        self._supabase_prefix_input.setText(self._supabase_prefix)

    def _on_apply_supabase_settings_clicked(self) -> None:
        values = self._collect_supabase_input_values()
        self._apply_supabase_settings_values(values, success_status="Supabase settings updated.")

    def _on_export_json_clicked(self) -> None:
        start_dir = self._data_storage_folder or ""
        default_name = DEFAULT_DATA_FILE_NAME
        if start_dir:
            normalized_start_dir = start_dir.rstrip("/\\")
            initial_path = f"{normalized_start_dir}/{default_name}"
        else:
            initial_path = default_name
        selected, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "Export Tracker Data as JSON",
            initial_path,
            "JSON Files (*.json);;All Files (*)",
        )
        requested = selected.strip() if isinstance(selected, str) else ""
        if not requested:
            return

        applied = requested
        if callable(self._on_export_json_backup_requested):
            try:
                callback_value = self._on_export_json_backup_requested(requested)
            except Exception as exc:
                self._set_status(f"JSON export failed: {exc}")
                return
            if isinstance(callback_value, str) and callback_value.strip():
                applied = callback_value.strip()
        self._set_status(f"JSON export saved: {applied}")

    def _on_import_json_clicked(self) -> None:
        start_dir = self._data_storage_folder or ""
        selected, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "Import Tracker Data from JSON",
            start_dir,
            "JSON Files (*.json);;All Files (*)",
        )
        requested = selected.strip() if isinstance(selected, str) else ""
        if not requested:
            return

        applied = True
        if callable(self._on_import_json_backup_requested):
            try:
                applied = bool(self._on_import_json_backup_requested(requested))
            except Exception as exc:
                self._set_status(f"JSON import failed: {exc}")
                return
        if applied:
            self._set_status(f"JSON imported: {requested}")
        else:
            self._set_status("JSON import canceled.")

    def _on_browse_data_folder_clicked(self) -> None:
        start_dir = self._data_storage_folder or ""
        selected = QFileDialog.getExistingDirectory(
            self,
            "Choose Local Data Folder",
            start_dir,
            QFileDialog.Option.ShowDirsOnly,
        )
        if not selected:
            return
        self._apply_data_storage_folder_change(selected)

    def _on_default_data_folder_clicked(self) -> None:
        self._apply_data_storage_folder_change("")

    def _apply_data_storage_folder_change(self, requested_folder: str) -> None:
        requested = requested_folder.strip() if isinstance(requested_folder, str) else ""
        applied = requested
        if callable(self._on_data_storage_folder_changed):
            try:
                callback_value = self._on_data_storage_folder_changed(requested)
            except Exception as exc:
                self._set_status(f"Data folder update failed: {exc}")
                return
            if isinstance(callback_value, str) and callback_value.strip():
                applied = callback_value.strip()
        self._set_data_storage_folder_display(applied)
        if applied:
            self._set_status(f"Data folder: {applied}")
        else:
            self._set_status("Data folder updated.")
        self.data_storage_folder_changed.emit(applied)

    def _labeled_setting(
        self,
        label_text: str,
        widget: QWidget,
        *,
        parent: QWidget | None = None,
    ) -> QWidget:
        host_parent = parent if isinstance(parent, QWidget) else self._supabase_card
        host = QWidget(host_parent)
        row = QHBoxLayout(host)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        label = QLabel(label_text, host)
        label.setObjectName("PluginGeneralLabel")
        row.addWidget(label, 0)
        row.addStretch(1)
        row.addWidget(widget, 1)
        return host

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
        while self._plugin_sections_layout.count():
            item = self._plugin_sections_layout.takeAt(0)
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
