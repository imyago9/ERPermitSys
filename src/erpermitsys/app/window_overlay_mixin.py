from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QSizePolicy,
    QStackedLayout,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from erpermitsys.app.permit_workspace_helpers import (
    PERMIT_TYPE_OPTIONS as _PERMIT_TYPE_OPTIONS,
)
from erpermitsys.app.tracker_models import PERMIT_EVENT_TYPES, event_type_label
from erpermitsys.ui.widgets import EdgeLockedScrollArea

try:
    from PySide6.QtWebEngineWidgets import QWebEngineView
except Exception:  # pragma: no cover - optional runtime dependency
    QWebEngineView = None  # type: ignore[assignment]

try:
    from PySide6.QtWebChannel import QWebChannel
except Exception:  # pragma: no cover - optional runtime dependency
    QWebChannel = None  # type: ignore[assignment]


class WindowOverlayMixin:
    def _build_body(self) -> None:
        page = QWidget(self)
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(0)

        scene = QWidget(page)
        scene.setObjectName("AppScene")
        scene.installEventFilter(self)
        self._scene_widget = scene

        scene_layout = QVBoxLayout(scene)
        scene_layout.setContentsMargins(0, 0, 0, 0)
        scene_layout.setSpacing(0)

        stack_host = QWidget(scene)
        stack = QStackedLayout(stack_host)
        stack.setContentsMargins(0, 0, 0, 0)
        stack.setStackingMode(QStackedLayout.StackingMode.StackOne)
        self._stack = stack

        fallback = QFrame(stack_host)
        fallback.setObjectName("FallbackBackground")
        stack.addWidget(fallback)
        self._fallback_widget = fallback

        if QWebEngineView is not None:
            web = QWebEngineView(stack_host)
            web.setObjectName("BackgroundWebView")
            web.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
            web.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
            web.setAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop, False)
            web.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            try:
                web.page().setBackgroundColor(QColor(6, 10, 16))
            except Exception:
                pass
            try:
                web.loadFinished.connect(self._on_background_load_finished)
            except Exception:
                pass
            if QWebChannel is not None:
                try:
                    channel = QWebChannel(web.page())
                    channel.registerObject("erpermitsysBridge", self._plugin_bridge)
                    web.page().setWebChannel(channel)
                    self._background_web_channel = channel
                except Exception:
                    self._background_web_channel = None
            stack.addWidget(web)
            self._background_view = web
        else:
            self._background_view = None
            self._background_web_channel = None

        scene_layout.addWidget(stack_host, 1)

        button = QPushButton("Settings", scene)
        button.setObjectName("SettingsLauncherButton")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop, True)
        button.clicked.connect(self.open_settings_dialog)
        button.hide()
        self._settings_button = button
        self._apply_settings_button_effect()

        self._build_tracker_overlay(scene)

        page_layout.addWidget(scene, 1)
        self.body_layout.addWidget(page)

    def _create_tracker_panel(
        self,
        parent: QWidget,
        title: str,
        *,
        with_title: bool = True,
    ) -> tuple[QFrame, QVBoxLayout]:
        panel = QFrame(parent)
        panel.setObjectName("TrackerPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        if with_title:
            label = QLabel(title, panel)
            label.setObjectName("TrackerPanelTitle")
            layout.addWidget(label)
        return panel, layout

    def _toggle_left_column_panel(self, panel_key: str) -> None:
        normalized = str(panel_key or "").strip().casefold()
        target = "permit" if normalized == "permit" else "address"
        current = str(self._left_column_expanded_panel or "").strip().casefold() or "address"
        if target == current:
            target = "permit" if current == "address" else "address"
        self._set_left_column_expanded_panel(target)

    def _set_left_column_expanded_panel(self, panel_key: str) -> None:
        normalized = str(panel_key or "").strip().casefold()
        target = "permit" if normalized == "permit" else "address"
        self._left_column_expanded_panel = target
        address_expanded = target == "address"
        permit_expanded = not address_expanded

        left_layout = self._left_column_layout
        left_column_widget = left_layout.parentWidget() if left_layout is not None else None
        if left_column_widget is not None:
            left_column_widget.setUpdatesEnabled(False)

        address_body = self._address_list_panel_body
        if address_body is not None:
            address_body.setEnabled(address_expanded)
            address_body.setMinimumHeight(0)
            address_body.setMaximumHeight(16777215 if address_expanded else 0)
            address_body.setSizePolicy(
                QSizePolicy.Policy.Expanding,
                QSizePolicy.Policy.Expanding if address_expanded else QSizePolicy.Policy.Fixed,
            )
        permit_body = self._permit_list_panel_body
        if permit_body is not None:
            permit_body.setEnabled(permit_expanded)
            permit_body.setMinimumHeight(0)
            permit_body.setMaximumHeight(16777215 if permit_expanded else 0)
            permit_body.setSizePolicy(
                QSizePolicy.Policy.Expanding,
                QSizePolicy.Policy.Expanding if permit_expanded else QSizePolicy.Policy.Fixed,
            )

        address_button = self._address_panel_toggle_button
        if address_button is not None:
            address_button.setText("▲" if address_expanded else "▼")
            address_button.setToolTip(
                "Collapse Address List" if address_expanded else "Expand Address List"
            )
        permit_button = self._permit_panel_toggle_button
        if permit_button is not None:
            permit_button.setText("▼" if permit_expanded else "▲")
            permit_button.setToolTip("Collapse Permits" if permit_expanded else "Expand Permits")

        if left_layout is not None:
            left_layout.setStretch(0, 1 if address_expanded else 0)
            left_layout.setStretch(1, 0 if address_expanded else 1)

        if left_column_widget is not None:
            left_column_widget.setUpdatesEnabled(True)
            left_column_widget.update()

    def _build_tracker_overlay(self, scene: QWidget) -> None:
        panel_host = QWidget(scene)
        panel_host.setObjectName("PermitPanelHost")
        panel_stack = QStackedLayout(panel_host)
        panel_stack.setContentsMargins(0, 0, 0, 0)
        panel_stack.setStackingMode(QStackedLayout.StackingMode.StackOne)
        self._panel_host = panel_host
        self._panel_stack = panel_stack

        home_view = QWidget(panel_host)
        self._panel_home_view = home_view
        root_layout = QVBoxLayout(home_view)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        panels_shell = QFrame(home_view)
        panels_shell.setObjectName("TrackerPanelsShell")
        panels_shell_layout = QHBoxLayout(panels_shell)
        panels_shell_layout.setContentsMargins(16, 16, 16, 16)
        panels_shell_layout.setSpacing(16)
        root_layout.addWidget(panels_shell, 1)

        left_column = QWidget(panels_shell)
        left_column.setObjectName("TrackerPanelsLeftColumn")
        left_column_layout = QVBoxLayout(left_column)
        left_column_layout.setContentsMargins(0, 0, 0, 0)
        left_column_layout.setSpacing(16)
        self._left_column_layout = left_column_layout

        left_panel, left_layout = self._create_tracker_panel(left_column, "Address List", with_title=False)
        left_panel.setProperty("panelRole", "address")
        self._address_list_panel = left_panel

        address_header_row = QHBoxLayout()
        address_header_row.setContentsMargins(0, 0, 0, 0)
        address_header_row.setSpacing(8)
        address_title_label = QLabel("Address List", left_panel)
        address_title_label.setObjectName("TrackerPanelTitle")
        address_header_row.addWidget(address_title_label, 1)
        address_toggle_button = QToolButton(left_panel)
        address_toggle_button.setObjectName("TrackerPanelCollapseButton")
        address_toggle_button.setCursor(Qt.CursorShape.PointingHandCursor)
        address_toggle_button.clicked.connect(
            lambda _checked=False: self._toggle_left_column_panel("address")
        )
        address_header_row.addWidget(
            address_toggle_button,
            0,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )
        self._address_panel_toggle_button = address_toggle_button
        left_layout.addLayout(address_header_row, 0)

        left_panel_body = QWidget(left_panel)
        left_panel_body_layout = QVBoxLayout(left_panel_body)
        left_panel_body_layout.setContentsMargins(0, 0, 0, 0)
        left_panel_body_layout.setSpacing(10)
        self._address_list_panel_body = left_panel_body

        self._property_filter_combo = QComboBox(left_panel)
        self._property_filter_combo.setObjectName("TrackerPanelFilter")
        self._property_filter_combo.currentIndexChanged.connect(self._refresh_property_list)
        left_panel_body_layout.addWidget(self._property_filter_combo)

        self._property_search_input = QLineEdit(left_panel)
        self._property_search_input.setObjectName("TrackerPanelSearch")
        self._property_search_input.setPlaceholderText("Search address or parcel")
        self._property_search_input.textChanged.connect(self._refresh_property_list)
        left_panel_body_layout.addWidget(self._property_search_input)

        self._property_result_label = QLabel("0 addresses", left_panel)
        self._property_result_label.setObjectName("TrackerPanelMeta")
        left_panel_body_layout.addWidget(self._property_result_label)

        properties_list_host = QWidget(left_panel)
        properties_list_stack = QStackedLayout(properties_list_host)
        properties_list_stack.setContentsMargins(0, 0, 0, 0)
        properties_list_stack.setStackingMode(QStackedLayout.StackingMode.StackOne)
        self._properties_list_stack = properties_list_stack

        self._properties_list_widget = QListWidget(properties_list_host)
        self._properties_list_widget.setObjectName("TrackerPanelList")
        self._properties_list_widget.setWordWrap(True)
        self._properties_list_widget.itemSelectionChanged.connect(self._on_property_selection_changed)
        properties_list_stack.addWidget(self._properties_list_widget)

        properties_empty_label = QLabel(
            "No addresses yet.\nClick Add Address to create your first property.",
            properties_list_host,
        )
        properties_empty_label.setObjectName("TrackerPanelEmptyState")
        properties_empty_label.setWordWrap(True)
        properties_empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._properties_empty_label = properties_empty_label
        properties_list_stack.addWidget(properties_empty_label)
        properties_list_stack.setCurrentWidget(properties_empty_label)

        left_panel_body_layout.addWidget(properties_list_host, 1)

        add_property_button = QPushButton("Add Address", left_panel)
        add_property_button.setObjectName("TrackerPanelActionButton")
        add_property_button.clicked.connect(self._add_property)
        left_panel_body_layout.addWidget(add_property_button)

        open_admin_button = QPushButton("Open Admin Panel", left_panel)
        open_admin_button.setObjectName("TrackerPanelActionButton")
        open_admin_button.clicked.connect(self._open_contacts_and_jurisdictions_dialog)
        left_panel_body_layout.addWidget(open_admin_button)

        left_layout.addWidget(left_panel_body, 1)
        left_column_layout.addWidget(left_panel, 1)

        middle_panel, middle_layout = self._create_tracker_panel(left_column, "Permits", with_title=False)
        middle_panel.setProperty("panelRole", "permit")
        middle_panel.setProperty("contextual", "true")
        self._permit_list_panel = middle_panel

        permit_title_row = QHBoxLayout()
        permit_title_row.setContentsMargins(0, 0, 0, 0)
        permit_title_row.setSpacing(8)
        permit_title_label = QLabel("Permits", middle_panel)
        permit_title_label.setObjectName("TrackerPanelTitle")
        permit_title_row.addWidget(permit_title_label, 1)
        permit_toggle_button = QToolButton(middle_panel)
        permit_toggle_button.setObjectName("TrackerPanelCollapseButton")
        permit_toggle_button.setCursor(Qt.CursorShape.PointingHandCursor)
        permit_toggle_button.clicked.connect(
            lambda _checked=False: self._toggle_left_column_panel("permit")
        )
        permit_title_row.addWidget(
            permit_toggle_button,
            0,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )
        self._permit_panel_toggle_button = permit_toggle_button
        middle_layout.addLayout(permit_title_row, 0)

        permit_panel_body = QWidget(middle_panel)
        permit_panel_body_layout = QVBoxLayout(permit_panel_body)
        permit_panel_body_layout.setContentsMargins(0, 0, 0, 0)
        permit_panel_body_layout.setSpacing(10)
        self._permit_list_panel_body = permit_panel_body

        permit_header_row = QHBoxLayout()
        permit_header_row.setContentsMargins(0, 0, 0, 0)
        permit_header_row.setSpacing(8)

        self._permit_header_label = QLabel("Select an address to view permits", middle_panel)
        self._permit_header_label.setObjectName("TrackerPanelMeta")
        self._permit_header_label.setProperty("permitHeader", "true")
        permit_header_row.addWidget(self._permit_header_label, 1)

        add_permit_button = QPushButton("Add Permit", middle_panel)
        add_permit_button.setObjectName("TrackerPanelActionButton")
        add_permit_button.setProperty("addPermitPrimary", "true")
        add_permit_button.clicked.connect(self._add_permit)
        self._add_permit_button = add_permit_button
        permit_header_row.addWidget(
            add_permit_button,
            0,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )
        permit_panel_body_layout.addLayout(permit_header_row)

        permit_controls_host = QWidget(middle_panel)
        permit_controls_host.setObjectName("PermitControlsHost")
        permit_controls_layout = QVBoxLayout(permit_controls_host)
        permit_controls_layout.setContentsMargins(0, 0, 0, 0)
        permit_controls_layout.setSpacing(8)
        self._permit_controls_host = permit_controls_host

        type_picker = QWidget(permit_panel_body)
        type_picker.setObjectName("PermitCategoryPicker")
        self._permit_type_picker_host = type_picker
        type_picker_layout = QHBoxLayout(type_picker)
        type_picker_layout.setContentsMargins(0, 0, 0, 0)
        type_picker_layout.setSpacing(8)
        for permit_type, label in _PERMIT_TYPE_OPTIONS:
            button = QPushButton(label, type_picker)
            button.setObjectName("PermitCategoryPill")
            button.setCheckable(True)
            button.clicked.connect(
                lambda _checked=False, value=permit_type: self._set_active_permit_type_filter(value)
            )
            type_picker_layout.addWidget(button, 1)
            self._permit_type_buttons[permit_type] = button

        filter_row = QHBoxLayout()
        filter_row.setContentsMargins(0, 0, 0, 0)
        filter_row.setSpacing(8)

        self._permit_filter_combo = QComboBox(permit_controls_host)
        self._permit_filter_combo.setObjectName("TrackerPanelFilter")
        self._permit_filter_combo.addItem("All Statuses", "all")
        self._permit_filter_combo.addItem("Open", "open")
        self._permit_filter_combo.addItem("Closed", "closed")
        self._permit_filter_combo.addItem("Overdue", "overdue")
        for event_type in PERMIT_EVENT_TYPES:
            if event_type == "note":
                continue
            self._permit_filter_combo.addItem(event_type_label(event_type), event_type)
        self._permit_filter_combo.currentIndexChanged.connect(self._refresh_permit_list)
        filter_row.addWidget(self._permit_filter_combo, 0)

        self._permit_search_input = QLineEdit(permit_controls_host)
        self._permit_search_input.setObjectName("TrackerPanelSearch")
        self._permit_search_input.setPlaceholderText("Search permit #, status, next action")
        self._permit_search_input.textChanged.connect(self._refresh_permit_list)
        filter_row.addWidget(self._permit_search_input, 1)

        self._permit_result_label = QLabel("0 permits", permit_controls_host)
        self._permit_result_label.setObjectName("TrackerPanelMeta")
        filter_row.addWidget(
            self._permit_result_label,
            0,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )

        permit_controls_layout.addLayout(filter_row)
        permit_panel_body_layout.addWidget(permit_controls_host, 0)

        permits_list_host = QWidget(middle_panel)
        permits_list_stack = QStackedLayout(permits_list_host)
        permits_list_stack.setContentsMargins(0, 0, 0, 0)
        permits_list_stack.setStackingMode(QStackedLayout.StackingMode.StackOne)
        self._permits_list_stack = permits_list_stack

        self._permits_list_widget = QListWidget(permits_list_host)
        self._permits_list_widget.setObjectName("TrackerPanelList")
        self._permits_list_widget.setWordWrap(True)
        self._permits_list_widget.itemSelectionChanged.connect(self._on_permit_selection_changed)
        permits_list_stack.addWidget(self._permits_list_widget)

        permits_empty_label = QLabel(
            "No permits yet.\nSelect an address and click Add Permit to create one.",
            permits_list_host,
        )
        permits_empty_label.setObjectName("TrackerPanelEmptyState")
        permits_empty_label.setWordWrap(True)
        permits_empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._permits_empty_label = permits_empty_label
        permits_list_stack.addWidget(permits_empty_label)
        permits_list_stack.setCurrentWidget(permits_empty_label)

        permit_panel_body_layout.addWidget(permits_list_host, 1)
        permit_panel_body_layout.addWidget(type_picker, 0)
        middle_layout.addWidget(permit_panel_body, 1)
        left_column_layout.addWidget(middle_panel, 1)
        self._set_left_column_expanded_panel("address")
        panels_shell_layout.addWidget(left_column, 1)

        right_panel, right_layout = self._create_tracker_panel(panels_shell, "Permit Workspace")
        right_panel.setProperty("panelRole", "workspace")
        self._permit_workspace_panel = right_panel
        self._workspace_title_label = right_panel.findChild(QLabel, "TrackerPanelTitle")
        if self._workspace_title_label is not None:
            self._workspace_title_label.setText("Permit Workspace - Select Permit")

        next_step_label = QLabel("", right_panel)
        next_step_label.setObjectName("PermitWorkspaceNextStepNote")
        next_step_label.setWordWrap(True)
        next_step_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        next_step_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        next_step_label.setVisible(False)
        self._workspace_next_step_label = next_step_label

        workspace_width_host = QWidget(right_panel)
        workspace_width_layout = QHBoxLayout(workspace_width_host)
        workspace_width_layout.setContentsMargins(0, 0, 0, 0)
        workspace_width_layout.setSpacing(0)

        workspace_content_host = QWidget(workspace_width_host)
        workspace_content_host.setObjectName("PermitWorkspaceContentHost")
        workspace_content_layout = QVBoxLayout(workspace_content_host)
        workspace_content_layout.setContentsMargins(0, 0, 0, 0)
        workspace_content_layout.setSpacing(10)
        self._permit_workspace_content_host = workspace_content_host

        blur_overlay = QFrame(workspace_content_host)
        blur_overlay.setObjectName("PermitWorkspaceBlurOverlay")
        blur_overlay.setVisible(False)
        blur_overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._permit_workspace_blur_overlay = blur_overlay

        summary_width_host = QWidget(workspace_content_host)
        summary_width_layout = QHBoxLayout(summary_width_host)
        summary_width_layout.setContentsMargins(0, 0, 0, 0)
        summary_width_layout.setSpacing(0)

        summary_grid_host = QWidget(summary_width_host)
        summary_grid_host.setObjectName("PermitWorkspaceSummaryGrid")
        summary_grid_layout = QGridLayout(summary_grid_host)
        summary_grid_layout.setContentsMargins(2, 2, 2, 2)
        summary_grid_layout.setHorizontalSpacing(12)
        summary_grid_layout.setVerticalSpacing(12)

        workspace_lower_width_host = QWidget(workspace_content_host)
        workspace_lower_width_layout = QHBoxLayout(workspace_lower_width_host)
        workspace_lower_width_layout.setContentsMargins(0, 0, 0, 0)
        workspace_lower_width_layout.setSpacing(0)

        workspace_lower_scroll = EdgeLockedScrollArea(workspace_lower_width_host)
        workspace_lower_scroll.setObjectName("PermitWorkspaceDetailScroll")
        workspace_lower_scroll.setWidgetResizable(True)
        workspace_lower_scroll.setFrameShape(QFrame.Shape.NoFrame)
        workspace_lower_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        workspace_lower_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        workspace_lower_scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        workspace_lower_scroll.viewport().setAutoFillBackground(False)
        workspace_lower_scroll.viewport().setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)

        workspace_lower_content_host = QWidget(workspace_lower_scroll)
        workspace_lower_content_layout = QVBoxLayout(workspace_lower_content_host)
        workspace_lower_content_layout.setContentsMargins(0, 0, 0, 0)
        workspace_lower_content_layout.setSpacing(10)

        workspace_focus_region = QFrame(workspace_lower_content_host)
        workspace_focus_region.setObjectName("PermitWorkspaceFocusRegion")
        workspace_focus_layout = QVBoxLayout(workspace_focus_region)
        workspace_focus_layout.setContentsMargins(10, 10, 10, 10)
        workspace_focus_layout.setSpacing(8)

        def create_workspace_info_cell(
            *,
            label_text: str,
            row: int,
            column: int,
            key: str,
        ) -> None:
            cell = QFrame(summary_grid_host)
            cell.setObjectName("PermitWorkspaceInfoCell")
            cell_layout = QVBoxLayout(cell)
            cell_layout.setContentsMargins(12, 10, 12, 10)
            cell_layout.setSpacing(3)

            label = QLabel(label_text, cell)
            label.setObjectName("PermitWorkspaceInfoLabel")
            cell_layout.addWidget(label, 0)

            value = QLabel("—", cell)
            value.setObjectName("PermitWorkspaceInfoValue")
            value.setWordWrap(True)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            cell_layout.addWidget(value, 1)

            summary_grid_layout.addWidget(cell, row, column)
            self._workspace_info_values[key] = value

        create_workspace_info_cell(label_text="Address", row=0, column=0, key="address")
        create_workspace_info_cell(label_text="Parcel", row=0, column=1, key="parcel")
        create_workspace_info_cell(label_text="Jurisdiction", row=0, column=2, key="jurisdiction")
        create_workspace_info_cell(label_text="Permit #", row=1, column=0, key="permit_number")
        create_workspace_info_cell(label_text="Status", row=1, column=1, key="status")
        create_workspace_info_cell(label_text="Contacts / Portal", row=1, column=2, key="contacts_portal")

        for column in range(3):
            summary_grid_layout.setColumnStretch(column, 1)
        summary_width_layout.addStretch(5)
        summary_width_layout.addWidget(summary_grid_host, 90)
        summary_width_layout.addStretch(5)
        workspace_content_layout.addWidget(summary_width_host, 0)

        top_actions = QHBoxLayout()
        top_actions.setContentsMargins(0, 0, 0, 0)
        top_actions.setSpacing(8)
        top_actions.addStretch(1)

        self._open_portal_button = QPushButton("Open Portal", workspace_focus_region)
        self._open_portal_button.setObjectName("TrackerPanelActionButton")
        self._open_portal_button.clicked.connect(self._open_selected_portal)
        top_actions.addWidget(self._open_portal_button)

        self._set_next_action_button = QPushButton("Set Next Action", workspace_focus_region)
        self._set_next_action_button.setObjectName("TrackerPanelActionButton")
        self._set_next_action_button.clicked.connect(self._set_next_action)
        top_actions.addWidget(self._set_next_action_button)

        self._add_event_button = QPushButton("Add Event", workspace_focus_region)
        self._add_event_button.setObjectName("TrackerPanelActionButton")
        self._add_event_button.clicked.connect(self._add_event)
        top_actions.addWidget(self._add_event_button)

        top_actions.addStretch(1)
        workspace_focus_layout.addLayout(top_actions)

        next_action_card = QFrame(workspace_focus_region)
        next_action_card.setObjectName("PermitDocumentsSection")
        next_action_card.setProperty("nextActionPanel", "true")
        next_action_layout = QVBoxLayout(next_action_card)
        next_action_layout.setContentsMargins(12, 10, 12, 10)
        next_action_layout.setSpacing(6)

        next_action_title = QLabel("Next Action", next_action_card)
        next_action_title.setObjectName("PermitDocumentsTitle")
        next_action_title.setProperty("nextAction", "true")
        next_action_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        next_action_layout.addWidget(next_action_title)

        self._next_action_label = QLabel("No next action set.", next_action_card)
        self._next_action_label.setObjectName("PermitDocumentStatus")
        self._next_action_label.setProperty("nextAction", "true")
        self._next_action_label.setWordWrap(True)
        self._next_action_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        next_action_layout.addWidget(self._next_action_label)
        workspace_focus_layout.addWidget(next_action_card)

        timeline_card = QFrame(workspace_focus_region)
        timeline_card.setObjectName("PermitDocumentsSection")
        timeline_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        timeline_card.setMaximumHeight(240)
        timeline_layout = QVBoxLayout(timeline_card)
        timeline_layout.setContentsMargins(12, 10, 12, 10)
        timeline_layout.setSpacing(6)

        timeline_title_row = QHBoxLayout()
        timeline_title_row.setContentsMargins(0, 0, 0, 0)
        timeline_title_row.setSpacing(8)

        timeline_title = QLabel("Timeline", timeline_card)
        timeline_title.setObjectName("PermitDocumentsTitle")
        timeline_title.setProperty("timeline", "true")
        timeline_title_row.addWidget(timeline_title, 0, Qt.AlignmentFlag.AlignVCenter)
        self._timeline_title_label = timeline_title

        timeline_title_row.addStretch(1)
        timeline_mode_toggle_button = QPushButton("Show Next Action Timeline", timeline_card)
        timeline_mode_toggle_button.setObjectName("TrackerPanelActionButton")
        timeline_mode_toggle_button.clicked.connect(self._toggle_timeline_mode)
        timeline_title_row.addWidget(
            timeline_mode_toggle_button,
            0,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )
        self._timeline_mode_toggle_button = timeline_mode_toggle_button
        timeline_layout.addLayout(timeline_title_row)

        timeline_hint = QLabel(
            "Oldest on the left, newest on the right. Only saved events appear here.",
            timeline_card,
        )
        timeline_hint.setObjectName("TrackerPanelHint")
        timeline_hint.setProperty("timeline", "true")
        timeline_hint.setWordWrap(True)
        timeline_layout.addWidget(timeline_hint, 0)
        self._timeline_hint_label = timeline_hint

        timeline_scroll = EdgeLockedScrollArea(timeline_card)
        timeline_scroll.setObjectName("PermitTimelineScroll")
        timeline_scroll.setWidgetResizable(False)
        timeline_scroll.setFrameShape(QFrame.Shape.NoFrame)
        timeline_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        timeline_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        timeline_scroll.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        timeline_scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        timeline_scroll.setFixedHeight(132)
        self._timeline_scroll_area = timeline_scroll

        timeline_track = QWidget(timeline_scroll)
        timeline_track.setObjectName("PermitTimelineTrack")
        timeline_track_layout = QHBoxLayout(timeline_track)
        timeline_track_layout.setContentsMargins(2, 2, 2, 2)
        timeline_track_layout.setSpacing(10)
        self._timeline_track_widget = timeline_track
        self._timeline_track_layout = timeline_track_layout
        timeline_scroll.setWidget(timeline_track)
        timeline_layout.addWidget(timeline_scroll, 1)
        workspace_focus_layout.addWidget(timeline_card, 0)

        workspace_lower_content_layout.addWidget(workspace_focus_region, 0)

        docs_card = QFrame(workspace_lower_content_host)
        docs_card.setObjectName("PermitDocumentsSection")
        docs_layout = QVBoxLayout(docs_card)
        docs_layout.setContentsMargins(12, 10, 12, 10)
        docs_layout.setSpacing(6)

        docs_title_row = QHBoxLayout()
        docs_title_row.setContentsMargins(0, 0, 0, 0)
        docs_title_row.setSpacing(8)
        docs_title = QLabel("Documents Checklist", docs_card)
        docs_title.setObjectName("PermitDocumentsTitle")
        docs_title_row.addWidget(docs_title, 0)
        docs_title_row.addStretch(1)

        docs_title_actions = QVBoxLayout()
        docs_title_actions.setContentsMargins(0, 0, 0, 0)
        docs_title_actions.setSpacing(6)

        template_actions_row = QHBoxLayout()
        template_actions_row.setContentsMargins(0, 0, 0, 0)
        template_actions_row.setSpacing(8)

        template_apply_combo = QComboBox(docs_card)
        template_apply_combo.setObjectName("PermitFormCombo")
        template_apply_combo.setMinimumWidth(220)
        template_actions_row.addWidget(
            template_apply_combo,
            0,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )
        self._document_template_apply_combo = template_apply_combo

        template_apply_button = QPushButton("Select", docs_card)
        template_apply_button.setObjectName("TrackerPanelActionButton")
        template_apply_button.clicked.connect(self._apply_selected_document_template_to_permit)
        template_actions_row.addWidget(
            template_apply_button,
            0,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )
        self._document_template_apply_button = template_apply_button
        docs_title_actions.addLayout(template_actions_row)

        docs_title_row.addLayout(docs_title_actions, 0)
        docs_layout.addLayout(docs_title_row, 0)

        self._document_status_label = QLabel("Select a permit to manage documents.", docs_card)
        self._document_status_label.setObjectName("PermitDocumentStatus")
        self._document_status_label.setWordWrap(True)
        docs_layout.addWidget(self._document_status_label)

        docs_hint = QLabel(
            "Each slot tracks version cycles. Select a file to mark it, or leave no file selected to mark the entire slot folder.",
            docs_card,
        )
        docs_hint.setObjectName("TrackerPanelHint")
        docs_hint.setWordWrap(True)
        docs_layout.addWidget(docs_hint, 0)

        docs_slot_tools_row = QHBoxLayout()
        docs_slot_tools_row.setContentsMargins(0, 0, 0, 0)
        docs_slot_tools_row.setSpacing(8)

        self._document_open_folder_button = QPushButton("Open Folder", docs_card)
        self._document_open_folder_button.setObjectName("TrackerPanelActionButton")
        self._document_open_folder_button.clicked.connect(self._open_selected_slot_folder)
        docs_slot_tools_row.addWidget(
            self._document_open_folder_button,
            0,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        )

        self._document_new_cycle_button = QPushButton("New Cycle", docs_card)
        self._document_new_cycle_button.setObjectName("TrackerPanelActionButton")
        self._document_new_cycle_button.clicked.connect(self._start_selected_slot_new_cycle)
        docs_slot_tools_row.addWidget(
            self._document_new_cycle_button,
            0,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        )

        docs_slot_tools_row.addStretch(1)

        self._document_mark_accepted_button = QPushButton("Mark Accepted", docs_card)
        self._document_mark_accepted_button.setObjectName("TrackerPanelActionButton")
        self._document_mark_accepted_button.clicked.connect(
            lambda: self._mark_selected_slot_status("accepted")
        )
        docs_slot_tools_row.addWidget(
            self._document_mark_accepted_button,
            0,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )

        self._document_mark_rejected_button = QPushButton("Mark Rejected", docs_card)
        self._document_mark_rejected_button.setObjectName("TrackerPanelActionButton")
        self._document_mark_rejected_button.clicked.connect(
            lambda: self._mark_selected_slot_status("rejected")
        )
        docs_slot_tools_row.addWidget(
            self._document_mark_rejected_button,
            0,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )

        docs_layout.addLayout(docs_slot_tools_row, 0)

        docs_headers = QHBoxLayout()
        docs_headers.setContentsMargins(2, 0, 2, 0)
        docs_headers.setSpacing(8)
        slots_header = QLabel("Checklist Slots", docs_card)
        slots_header.setObjectName("TrackerPanelSubsectionTitle")
        docs_headers.addWidget(slots_header, 1)
        files_header = QLabel("Files in Active Cycle", docs_card)
        files_header.setObjectName("TrackerPanelSubsectionTitle")
        docs_headers.addWidget(files_header, 1)
        docs_layout.addLayout(docs_headers)

        docs_lists_row = QHBoxLayout()
        docs_lists_row.setContentsMargins(0, 0, 0, 0)
        docs_lists_row.setSpacing(8)

        self._document_slot_list_widget = QListWidget(docs_card)
        self._document_slot_list_widget.setObjectName("TrackerPanelList")
        self._document_slot_list_widget.itemSelectionChanged.connect(self._on_document_slot_selection_changed)
        docs_lists_row.addWidget(self._document_slot_list_widget, 1)

        self._document_file_list_widget = QListWidget(docs_card)
        self._document_file_list_widget.setObjectName("PermitDocumentList")
        self._document_file_list_widget.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self._document_file_list_widget.itemSelectionChanged.connect(self._on_document_file_selection_changed)
        self._document_file_list_widget.itemDoubleClicked.connect(self._open_selected_document)
        docs_lists_row.addWidget(self._document_file_list_widget, 1)

        docs_layout.addLayout(docs_lists_row)

        docs_actions = QHBoxLayout()
        docs_actions.setContentsMargins(0, 0, 0, 0)
        docs_actions.setSpacing(8)

        self._document_upload_button = QPushButton("Upload", docs_card)
        self._document_upload_button.setObjectName("TrackerPanelActionButton")
        self._document_upload_button.clicked.connect(self._upload_documents_to_slot)
        docs_actions.addWidget(self._document_upload_button)

        docs_actions.addStretch(1)

        self._document_open_file_button = QPushButton("Open File", docs_card)
        self._document_open_file_button.setObjectName("TrackerPanelActionButton")
        self._document_open_file_button.clicked.connect(self._open_selected_document)
        docs_actions.addWidget(
            self._document_open_file_button,
            0,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )

        self._document_remove_file_button = QPushButton("Remove File", docs_card)
        self._document_remove_file_button.setObjectName("PermitFormDangerButton")
        self._document_remove_file_button.setProperty("adminHeaderDanger", "true")
        self._document_remove_file_button.clicked.connect(self._remove_selected_document)
        docs_actions.addWidget(
            self._document_remove_file_button,
            0,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )
        docs_layout.addLayout(docs_actions)

        workspace_lower_content_layout.addWidget(docs_card, 1)
        workspace_lower_scroll.setWidget(workspace_lower_content_host)

        workspace_lower_width_layout.addWidget(workspace_lower_scroll, 1)
        workspace_content_layout.addWidget(workspace_lower_width_host, 1)
        workspace_width_layout.addStretch(1)
        workspace_width_layout.addWidget(workspace_content_host, 6)
        workspace_width_layout.addStretch(1)
        right_layout.addWidget(workspace_width_host, 1)

        panels_shell_layout.addWidget(right_panel, 2)

        panel_stack.addWidget(home_view)
        admin_view = self._build_contacts_and_jurisdictions_view(panel_host)
        self._panel_admin_view = admin_view
        panel_stack.addWidget(admin_view)
        add_property_view = self._build_add_property_view(panel_host)
        self._panel_add_property_view = add_property_view
        panel_stack.addWidget(add_property_view)
        add_permit_view = self._build_add_permit_view(panel_host)
        self._panel_add_permit_view = add_permit_view
        panel_stack.addWidget(add_permit_view)
        panel_stack.setCurrentWidget(home_view)
        panel_host.hide()
