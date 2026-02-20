from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QSizePolicy,
    QStackedLayout,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from erpermitsys.app.admin_layout_service import WindowAdminLayoutService
from erpermitsys.ui.widgets import EdgeLockedScrollArea


class WindowAdminLayoutMixin:
    def _build_contacts_and_jurisdictions_view(self, parent: QWidget) -> QWidget:
        view = QWidget(parent)
        view.setObjectName("ContactsJurisdictionsView")
        layout = QVBoxLayout(view)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        header = QFrame(view)
        header.setObjectName("TrackerPanel")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(14, 12, 14, 12)
        header_layout.setSpacing(8)

        title = QLabel("Admin Panel", header)
        title.setObjectName("TrackerPanelTitle")
        header_layout.addWidget(title, 0)

        hint = QLabel(
            "Select an existing record on the left to edit it, or use the Add New button to create a new one.",
            header,
        )
        hint.setObjectName("TrackerPanelMeta")
        hint.setWordWrap(True)
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header_layout.addWidget(hint, 1)

        back_button = QPushButton("Back to Tracker", header)
        back_button.setObjectName("TrackerPanelActionButton")
        back_button.setProperty("adminBackButton", "true")
        back_button.setMinimumHeight(34)
        back_button.setMinimumWidth(150)
        back_button.clicked.connect(self._close_contacts_and_jurisdictions_view)
        header_layout.addWidget(back_button, 0)
        layout.addWidget(header, 0)

        content = QFrame(view)
        content.setObjectName("TrackerPanel")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(14, 12, 14, 14)
        content_layout.setSpacing(10)
        layout.addWidget(content, 1)

        tabs = QTabWidget(content)
        tabs.setObjectName("ContactsJurisdictionsTabs")
        tabs.tabBar().setExpanding(False)
        self._admin_tabs = tabs
        content_layout.addWidget(tabs, 1)

        contact_tab = QWidget(tabs)
        contact_layout = QHBoxLayout(contact_tab)
        contact_layout.setContentsMargins(0, 0, 0, 0)
        contact_layout.setSpacing(14)

        contact_left_card = QFrame(contact_tab)
        contact_left_card.setObjectName("AdminListPane")
        contact_left = QVBoxLayout(contact_left_card)
        contact_left.setContentsMargins(12, 12, 12, 12)
        contact_left.setSpacing(10)

        contacts_title = QLabel("Contacts Directory", contact_left_card)
        contacts_title.setObjectName("AdminListTitleChip")
        contact_left.addWidget(contacts_title, 0)

        contacts_hint = QLabel(
            "Find contacts quickly, then edit details and communication bundles on the right.",
            contact_left_card,
        )
        contacts_hint.setObjectName("AdminSectionHint")
        contacts_hint.setWordWrap(True)
        contact_left.addWidget(contacts_hint, 0)

        contacts_search = QLineEdit(contact_left_card)
        contacts_search.setObjectName("TrackerPanelSearch")
        contacts_search.setPlaceholderText("Search contacts (name, role, email, number, note)")
        contacts_search.setClearButtonEnabled(True)
        contacts_search.textChanged.connect(self._on_admin_contacts_search_changed)
        contact_left.addWidget(contacts_search, 0)
        self._admin_contacts_search_input = contacts_search

        add_contact_button = QPushButton("Add New Contact", contact_left_card)
        add_contact_button.setObjectName("TrackerPanelActionButton")
        add_contact_button.setProperty("adminPrimaryCta", "true")
        add_contact_button.setMinimumHeight(34)
        add_contact_button.clicked.connect(self._on_admin_add_new_contact_clicked)
        contact_left.addWidget(add_contact_button, 0)

        contacts_count_label = QLabel("0 contacts", contact_left_card)
        contacts_count_label.setObjectName("TrackerPanelMeta")
        contact_left.addWidget(contacts_count_label, 0)
        self._admin_contacts_count_label = contacts_count_label

        contacts_list_host = QWidget(contact_left_card)
        contacts_list_stack = QStackedLayout(contacts_list_host)
        contacts_list_stack.setContentsMargins(0, 0, 0, 0)
        contacts_list_stack.setSpacing(0)

        contacts_list = QListWidget(contacts_list_host)
        contacts_list.setObjectName("TrackerPanelList")
        contacts_list.setWordWrap(True)
        contacts_list.setSpacing(6)
        contacts_list.itemSelectionChanged.connect(self._on_admin_contact_selected)
        contacts_list_stack.addWidget(contacts_list)
        self._admin_contacts_list_widget = contacts_list

        contacts_empty_label = QLabel(
            "No contacts yet.\nUse Add New Contact to get started.",
            contacts_list_host,
        )
        contacts_empty_label.setObjectName("AdminListEmptyState")
        contacts_empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        contacts_empty_label.setWordWrap(True)
        contacts_list_stack.addWidget(contacts_empty_label)
        self._admin_contacts_empty_label = contacts_empty_label
        self._admin_contacts_list_stack = contacts_list_stack

        contact_left.addWidget(contacts_list_host, 1)

        contact_layout.addWidget(contact_left_card, 1)

        contact_right_host = QWidget(contact_tab)
        contact_right_layout = QHBoxLayout(contact_right_host)
        contact_right_layout.setContentsMargins(0, 0, 0, 0)
        contact_right_layout.setSpacing(0)

        contact_scroll = EdgeLockedScrollArea(contact_right_host)
        contact_scroll.setObjectName("AdminEditorScroll")
        contact_scroll.setWidgetResizable(True)
        contact_scroll.setFrameShape(QFrame.Shape.NoFrame)
        contact_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        contact_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        contact_scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        contact_scroll.viewport().setAutoFillBackground(False)
        contact_scroll.viewport().setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)

        contact_form = QFrame(contact_scroll)
        contact_form.setObjectName("PermitFormCard")
        contact_form.setProperty("adminForm", "true")
        contact_form.setMinimumWidth(500)
        self._admin_contact_form_widget = contact_form
        contact_form_layout = QVBoxLayout(contact_form)
        contact_form_layout.setContentsMargins(14, 14, 14, 14)
        contact_form_layout.setSpacing(11)

        contact_header_bar = QFrame(contact_form)
        contact_header_bar.setObjectName("AdminHeaderBar")
        contact_header_row = QHBoxLayout(contact_header_bar)
        contact_header_row.setContentsMargins(10, 8, 10, 8)
        contact_header_row.setSpacing(8)

        contact_mode_label = QLabel("Adding New Contact", contact_form)
        contact_mode_label.setObjectName("AdminModeTitle")
        contact_header_row.addWidget(contact_mode_label, 0)
        self._admin_contact_mode_label = contact_mode_label

        contact_header_row.addStretch(1)

        save_contact_button = QPushButton("Create Contact", contact_form)
        save_contact_button.setObjectName("TrackerPanelActionButton")
        save_contact_button.setProperty("adminPrimaryCta", "true")
        save_contact_button.setMinimumHeight(32)
        save_contact_button.clicked.connect(self._admin_save_contact)
        contact_header_row.addWidget(save_contact_button, 0)
        self._admin_contact_save_button = save_contact_button

        delete_contact_button = QPushButton("Delete Contact", contact_form)
        delete_contact_button.setObjectName("PermitFormDangerButton")
        delete_contact_button.setMinimumHeight(32)
        delete_contact_button.clicked.connect(self._admin_delete_contact)
        contact_header_row.addWidget(delete_contact_button, 0)
        self._admin_contact_delete_button = delete_contact_button

        contact_form_layout.addWidget(contact_header_bar, 0)

        contact_details_row = QHBoxLayout()
        contact_details_row.setContentsMargins(0, 0, 0, 0)
        contact_details_row.setSpacing(8)

        contact_details_label = QLabel("Contact Details", contact_form)
        contact_details_label.setObjectName("AdminSectionTitle")
        contact_details_row.addWidget(contact_details_label, 0)

        contact_dirty_bubble = QLabel("Empty", contact_form)
        contact_dirty_bubble.setObjectName("AdminDirtyBubble")
        contact_dirty_bubble.setProperty("dirtyState", "empty")
        contact_dirty_bubble.setAlignment(Qt.AlignmentFlag.AlignCenter)
        contact_dirty_bubble.setMinimumWidth(92)
        contact_dirty_bubble.setMinimumHeight(24)
        contact_details_row.addWidget(contact_dirty_bubble, 0, Qt.AlignmentFlag.AlignVCenter)
        self._admin_contact_dirty_bubble = contact_dirty_bubble

        contact_details_row.addStretch(1)
        contact_form_layout.addLayout(contact_details_row)

        self._admin_contact_field_shells = []
        contact_fields = QFormLayout()
        contact_fields.setContentsMargins(0, 0, 0, 0)
        contact_fields.setHorizontalSpacing(10)
        contact_fields.setVerticalSpacing(8)

        self._admin_contact_name_input = QLineEdit(contact_form)
        self._admin_contact_name_input.setObjectName("PermitFormInput")
        self._admin_contact_name_input.setPlaceholderText("Full name")
        self._admin_contact_name_input.textChanged.connect(self._on_admin_contact_form_changed)
        self._admin_contact_name_input.returnPressed.connect(self._admin_save_contact)
        contact_fields.addRow(
            self._build_admin_input_shell(
                label_text="Name",
                field_widget=self._admin_contact_name_input,
                parent=contact_form,
                shell_bucket=self._admin_contact_field_shells,
            ),
        )

        self._admin_contact_roles_input = QLineEdit(contact_form)
        self._admin_contact_roles_input.setObjectName("PermitFormInput")
        self._admin_contact_roles_input.setPlaceholderText("client, contractor, owner...")
        self._admin_contact_roles_input.textChanged.connect(self._on_admin_contact_form_changed)
        self._admin_contact_roles_input.returnPressed.connect(self._admin_save_contact)
        contact_fields.addRow(
            self._build_admin_input_shell(
                label_text="Roles",
                field_widget=self._admin_contact_roles_input,
                parent=contact_form,
                shell_bucket=self._admin_contact_field_shells,
            ),
        )
        contact_form_layout.addLayout(contact_fields)

        methods_label = QLabel("Email + Number Bundles (0)", contact_form)
        methods_label.setObjectName("AdminSectionTitle")
        contact_form_layout.addWidget(methods_label, 0)
        self._admin_contact_methods_label = methods_label

        methods_hint = QLabel(
            "Group emails and numbers by context so teams know which channel to use.",
            contact_form,
        )
        methods_hint.setObjectName("AdminSectionHint")
        methods_hint.setWordWrap(True)
        contact_form_layout.addWidget(methods_hint, 0)

        contact_method_actions = QHBoxLayout()
        contact_method_actions.setContentsMargins(0, 0, 0, 0)
        contact_method_actions.setSpacing(8)

        add_method_button = QPushButton("Add Bundle", contact_form)
        add_method_button.setObjectName("TrackerPanelActionButton")
        add_method_button.setProperty("bundleAction", "update")
        add_method_button.setProperty("bundleEditing", "false")
        add_method_button.setMinimumHeight(30)
        add_method_button.setMinimumWidth(108)
        add_method_button.clicked.connect(self._admin_add_contact_method_bundle)
        contact_method_actions.addWidget(add_method_button, 0)
        self._admin_contact_add_method_button = add_method_button

        cancel_edit_bundle_button = QPushButton("Cancel Edit", contact_form)
        cancel_edit_bundle_button.setObjectName("TrackerPanelActionButton")
        cancel_edit_bundle_button.setProperty("bundleAction", "cancel")
        cancel_edit_bundle_button.setProperty("bundleEditing", "false")
        cancel_edit_bundle_button.setMinimumHeight(30)
        cancel_edit_bundle_button.setMinimumWidth(108)
        cancel_edit_bundle_button.clicked.connect(self._admin_cancel_contact_method_edit)
        cancel_edit_bundle_button.hide()
        contact_method_actions.addWidget(cancel_edit_bundle_button, 0)
        self._admin_contact_cancel_method_button = cancel_edit_bundle_button

        bundle_toggle_button = QPushButton(">", contact_form)
        bundle_toggle_button.setObjectName("TrackerPanelActionButton")
        bundle_toggle_button.setFixedSize(34, 30)
        bundle_toggle_button.clicked.connect(self._toggle_admin_contact_bundle_fields)
        contact_method_actions.addWidget(bundle_toggle_button, 0)
        self._admin_contact_bundle_toggle_button = bundle_toggle_button

        contact_method_actions.addStretch(1)
        contact_form_layout.addLayout(contact_method_actions)

        contact_bundle_fields_host = QWidget(contact_form)
        contact_bundle_fields_host.setMaximumHeight(0)
        contact_form_layout.addWidget(contact_bundle_fields_host, 0)
        self._admin_contact_bundle_fields_host = contact_bundle_fields_host

        contact_bundle_fields = QFormLayout(contact_bundle_fields_host)
        contact_bundle_fields.setContentsMargins(0, 0, 0, 0)
        contact_bundle_fields.setHorizontalSpacing(10)
        contact_bundle_fields.setVerticalSpacing(8)

        self._admin_contact_bundle_name_input = QLineEdit(contact_bundle_fields_host)
        self._admin_contact_bundle_name_input.setObjectName("PermitFormInput")
        self._admin_contact_bundle_name_input.setPlaceholderText("Office, permit desk, after-hours...")
        self._admin_contact_bundle_name_input.textChanged.connect(self._on_admin_contact_form_changed)
        self._admin_contact_bundle_name_input.returnPressed.connect(self._admin_add_contact_method_bundle)
        contact_bundle_fields.addRow(
            self._build_admin_input_shell(
                label_text="Bundle Name",
                field_widget=self._admin_contact_bundle_name_input,
                parent=contact_bundle_fields_host,
                shell_bucket=self._admin_contact_field_shells,
            ),
        )

        self._admin_contact_numbers_input = QLineEdit(contact_bundle_fields_host)
        self._admin_contact_numbers_input.setObjectName("PermitFormInput")
        self._admin_contact_numbers_input.setPlaceholderText("comma/semicolon-separated")
        self._admin_contact_numbers_input.textChanged.connect(self._on_admin_contact_form_changed)
        self._admin_contact_numbers_input.returnPressed.connect(self._admin_add_contact_method_bundle)
        contact_bundle_fields.addRow(
            self._build_admin_input_shell(
                label_text="Bundle Number(s)",
                field_widget=self._admin_contact_numbers_input,
                parent=contact_bundle_fields_host,
                shell_bucket=self._admin_contact_field_shells,
            ),
        )

        self._admin_contact_emails_input = QLineEdit(contact_bundle_fields_host)
        self._admin_contact_emails_input.setObjectName("PermitFormInput")
        self._admin_contact_emails_input.setPlaceholderText("comma/semicolon-separated")
        self._admin_contact_emails_input.textChanged.connect(self._on_admin_contact_form_changed)
        self._admin_contact_emails_input.returnPressed.connect(self._admin_add_contact_method_bundle)
        contact_bundle_fields.addRow(
            self._build_admin_input_shell(
                label_text="Bundle Email(s)",
                field_widget=self._admin_contact_emails_input,
                parent=contact_bundle_fields_host,
                shell_bucket=self._admin_contact_field_shells,
            ),
        )

        self._admin_contact_note_input = QLineEdit(contact_bundle_fields_host)
        self._admin_contact_note_input.setObjectName("PermitFormInput")
        self._admin_contact_note_input.setPlaceholderText("note for this bundle (office, permit desk, after-hours...)")
        self._admin_contact_note_input.textChanged.connect(self._on_admin_contact_form_changed)
        self._admin_contact_note_input.returnPressed.connect(self._admin_add_contact_method_bundle)
        contact_bundle_fields.addRow(
            self._build_admin_input_shell(
                label_text="Bundle Note",
                field_widget=self._admin_contact_note_input,
                parent=contact_bundle_fields_host,
                shell_bucket=self._admin_contact_field_shells,
            ),
        )

        bundle_animation = QPropertyAnimation(contact_bundle_fields_host, b"maximumHeight", contact_bundle_fields_host)
        bundle_animation.setDuration(170)
        bundle_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._admin_contact_bundle_fields_animation = bundle_animation
        self._set_admin_contact_bundle_fields_open(False, animate=False)
        self._sync_admin_contact_bundle_action_state()

        contact_methods_host = QWidget(contact_form)
        contact_methods_host.setObjectName("AttachedContactsHost")
        contact_methods_layout = QVBoxLayout(contact_methods_host)
        contact_methods_layout.setContentsMargins(0, 0, 0, 0)
        contact_methods_layout.setSpacing(6)
        self._admin_contact_methods_host = contact_methods_host
        contact_form_layout.addWidget(contact_methods_host, 1)

        contact_color_picker = self._build_admin_color_picker_widget(
            parent=contact_form,
            entity_kind="contact",
        )
        self._admin_contact_color_picker_host = contact_color_picker
        contact_color_shell = self._build_admin_input_shell(
            label_text="List Color Picker",
            field_widget=contact_color_picker,
            parent=contact_form,
            shell_bucket=None,
            field_stretch=0,
            left_align_field=True,
        )
        self._admin_contact_color_shell = contact_color_shell
        contact_form_layout.addWidget(contact_color_shell, 0, Qt.AlignmentFlag.AlignLeft)

        contact_right_layout.addStretch(15)
        contact_scroll.setWidget(contact_form)
        contact_right_layout.addWidget(contact_scroll, 70)
        contact_right_layout.addStretch(15)
        contact_layout.addWidget(contact_right_host, 3)

        jurisdiction_tab = QWidget(tabs)
        jurisdiction_layout = QHBoxLayout(jurisdiction_tab)
        jurisdiction_layout.setContentsMargins(0, 0, 0, 0)
        jurisdiction_layout.setSpacing(14)

        jurisdiction_left_card = QFrame(jurisdiction_tab)
        jurisdiction_left_card.setObjectName("AdminListPane")
        jurisdiction_left = QVBoxLayout(jurisdiction_left_card)
        jurisdiction_left.setContentsMargins(12, 12, 12, 12)
        jurisdiction_left.setSpacing(10)

        jurisdictions_title = QLabel("Jurisdictions Directory", jurisdiction_left_card)
        jurisdictions_title.setObjectName("AdminListTitleChip")
        jurisdiction_left.addWidget(jurisdictions_title, 0)

        jurisdictions_hint = QLabel(
            "Keep permitting authorities organized and attach the right contacts to each one.",
            jurisdiction_left_card,
        )
        jurisdictions_hint.setObjectName("AdminSectionHint")
        jurisdictions_hint.setWordWrap(True)
        jurisdiction_left.addWidget(jurisdictions_hint, 0)

        jurisdictions_search = QLineEdit(jurisdiction_left_card)
        jurisdictions_search.setObjectName("TrackerPanelSearch")
        jurisdictions_search.setPlaceholderText("Search jurisdictions (name, type, portal, contact)")
        jurisdictions_search.setClearButtonEnabled(True)
        jurisdictions_search.textChanged.connect(self._on_admin_jurisdictions_search_changed)
        jurisdiction_left.addWidget(jurisdictions_search, 0)
        self._admin_jurisdictions_search_input = jurisdictions_search

        add_jurisdiction_button = QPushButton("Add New Jurisdiction", jurisdiction_left_card)
        add_jurisdiction_button.setObjectName("TrackerPanelActionButton")
        add_jurisdiction_button.setProperty("adminPrimaryCta", "true")
        add_jurisdiction_button.setMinimumHeight(34)
        add_jurisdiction_button.clicked.connect(self._on_admin_add_new_jurisdiction_clicked)
        jurisdiction_left.addWidget(add_jurisdiction_button, 0)

        jurisdictions_count_label = QLabel("0 jurisdictions", jurisdiction_left_card)
        jurisdictions_count_label.setObjectName("TrackerPanelMeta")
        jurisdiction_left.addWidget(jurisdictions_count_label, 0)
        self._admin_jurisdictions_count_label = jurisdictions_count_label

        jurisdictions_list_host = QWidget(jurisdiction_left_card)
        jurisdictions_list_stack = QStackedLayout(jurisdictions_list_host)
        jurisdictions_list_stack.setContentsMargins(0, 0, 0, 0)
        jurisdictions_list_stack.setSpacing(0)

        jurisdictions_list = QListWidget(jurisdictions_list_host)
        jurisdictions_list.setObjectName("TrackerPanelList")
        jurisdictions_list.setWordWrap(True)
        jurisdictions_list.setSpacing(6)
        jurisdictions_list.itemSelectionChanged.connect(self._on_admin_jurisdiction_selected)
        jurisdictions_list_stack.addWidget(jurisdictions_list)
        self._admin_jurisdictions_list_widget = jurisdictions_list

        jurisdictions_empty_label = QLabel(
            "No jurisdictions yet.\nUse Add New Jurisdiction to get started.",
            jurisdictions_list_host,
        )
        jurisdictions_empty_label.setObjectName("AdminListEmptyState")
        jurisdictions_empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        jurisdictions_empty_label.setWordWrap(True)
        jurisdictions_list_stack.addWidget(jurisdictions_empty_label)
        self._admin_jurisdictions_empty_label = jurisdictions_empty_label
        self._admin_jurisdictions_list_stack = jurisdictions_list_stack

        jurisdiction_left.addWidget(jurisdictions_list_host, 1)
        jurisdiction_layout.addWidget(jurisdiction_left_card, 1)

        jurisdiction_right_host = QWidget(jurisdiction_tab)
        jurisdiction_right_layout = QHBoxLayout(jurisdiction_right_host)
        jurisdiction_right_layout.setContentsMargins(0, 0, 0, 0)
        jurisdiction_right_layout.setSpacing(0)

        jurisdiction_scroll = EdgeLockedScrollArea(jurisdiction_right_host)
        jurisdiction_scroll.setObjectName("AdminEditorScroll")
        jurisdiction_scroll.setWidgetResizable(True)
        jurisdiction_scroll.setFrameShape(QFrame.Shape.NoFrame)
        jurisdiction_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        jurisdiction_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        jurisdiction_scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        jurisdiction_scroll.viewport().setAutoFillBackground(False)
        jurisdiction_scroll.viewport().setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)

        jurisdiction_form = QFrame(jurisdiction_scroll)
        jurisdiction_form.setObjectName("PermitFormCard")
        jurisdiction_form.setProperty("adminForm", "true")
        jurisdiction_form.setMinimumWidth(540)
        self._admin_jurisdiction_form_widget = jurisdiction_form
        jurisdiction_form_layout = QVBoxLayout(jurisdiction_form)
        jurisdiction_form_layout.setContentsMargins(14, 14, 14, 14)
        jurisdiction_form_layout.setSpacing(11)

        jurisdiction_header_bar = QFrame(jurisdiction_form)
        jurisdiction_header_bar.setObjectName("AdminHeaderBar")
        jurisdiction_header_row = QHBoxLayout(jurisdiction_header_bar)
        jurisdiction_header_row.setContentsMargins(10, 8, 10, 8)
        jurisdiction_header_row.setSpacing(8)

        jurisdiction_mode_label = QLabel("Adding New Jurisdiction", jurisdiction_form)
        jurisdiction_mode_label.setObjectName("AdminModeTitle")
        jurisdiction_header_row.addWidget(jurisdiction_mode_label, 0)
        self._admin_jurisdiction_mode_label = jurisdiction_mode_label

        jurisdiction_header_row.addStretch(1)

        save_jurisdiction_button = QPushButton("Create Jurisdiction", jurisdiction_form)
        save_jurisdiction_button.setObjectName("TrackerPanelActionButton")
        save_jurisdiction_button.setProperty("adminPrimaryCta", "true")
        save_jurisdiction_button.setMinimumHeight(32)
        save_jurisdiction_button.clicked.connect(self._admin_save_jurisdiction)
        jurisdiction_header_row.addWidget(save_jurisdiction_button, 0)
        self._admin_jurisdiction_save_button = save_jurisdiction_button

        delete_jurisdiction_button = QPushButton("Delete Jurisdiction", jurisdiction_form)
        delete_jurisdiction_button.setObjectName("PermitFormDangerButton")
        delete_jurisdiction_button.setMinimumHeight(32)
        delete_jurisdiction_button.clicked.connect(self._admin_delete_jurisdiction)
        jurisdiction_header_row.addWidget(delete_jurisdiction_button, 0)
        self._admin_jurisdiction_delete_button = delete_jurisdiction_button

        jurisdiction_form_layout.addWidget(jurisdiction_header_bar, 0)

        jurisdiction_details_row = QHBoxLayout()
        jurisdiction_details_row.setContentsMargins(0, 0, 0, 0)
        jurisdiction_details_row.setSpacing(8)

        jurisdiction_details_label = QLabel("Jurisdiction Details", jurisdiction_form)
        jurisdiction_details_label.setObjectName("AdminSectionTitle")
        jurisdiction_details_row.addWidget(jurisdiction_details_label, 0)

        jurisdiction_dirty_bubble = QLabel("Empty", jurisdiction_form)
        jurisdiction_dirty_bubble.setObjectName("AdminDirtyBubble")
        jurisdiction_dirty_bubble.setProperty("dirtyState", "empty")
        jurisdiction_dirty_bubble.setAlignment(Qt.AlignmentFlag.AlignCenter)
        jurisdiction_dirty_bubble.setMinimumWidth(92)
        jurisdiction_dirty_bubble.setMinimumHeight(24)
        jurisdiction_details_row.addWidget(jurisdiction_dirty_bubble, 0, Qt.AlignmentFlag.AlignVCenter)
        self._admin_jurisdiction_dirty_bubble = jurisdiction_dirty_bubble

        jurisdiction_details_row.addStretch(1)
        jurisdiction_form_layout.addLayout(jurisdiction_details_row)

        self._admin_jurisdiction_field_shells = []
        jurisdiction_details_split_row = QHBoxLayout()
        jurisdiction_details_split_row.setContentsMargins(0, 0, 0, 0)
        jurisdiction_details_split_row.setSpacing(12)

        jurisdiction_fields_host = QWidget(jurisdiction_form)
        jurisdiction_fields_host_layout = QVBoxLayout(jurisdiction_fields_host)
        jurisdiction_fields_host_layout.setContentsMargins(0, 0, 0, 0)
        jurisdiction_fields_host_layout.setSpacing(0)
        self._admin_jurisdiction_fields_host = jurisdiction_fields_host

        jurisdiction_fields = QFormLayout()
        jurisdiction_fields.setContentsMargins(0, 0, 0, 0)
        jurisdiction_fields.setHorizontalSpacing(10)
        jurisdiction_fields.setVerticalSpacing(8)

        self._admin_jurisdiction_name_input = QLineEdit(jurisdiction_form)
        self._admin_jurisdiction_name_input.setObjectName("PermitFormInput")
        self._admin_jurisdiction_name_input.setPlaceholderText("City of ... / County of ...")
        self._admin_jurisdiction_name_input.textChanged.connect(self._on_admin_jurisdiction_form_changed)
        self._admin_jurisdiction_name_input.returnPressed.connect(self._admin_save_jurisdiction)
        jurisdiction_fields.addRow(
            self._build_admin_input_shell(
                label_text="Name",
                field_widget=self._admin_jurisdiction_name_input,
                parent=jurisdiction_form,
                shell_bucket=self._admin_jurisdiction_field_shells,
            ),
        )

        self._admin_jurisdiction_type_combo = QComboBox(jurisdiction_form)
        self._admin_jurisdiction_type_combo.setObjectName("PermitFormCombo")
        self._admin_jurisdiction_type_combo.addItem("City", "city")
        self._admin_jurisdiction_type_combo.addItem("County", "county")
        self._admin_jurisdiction_type_combo.currentIndexChanged.connect(
            self._on_admin_jurisdiction_form_changed
        )
        jurisdiction_fields.addRow(
            self._build_admin_input_shell(
                label_text="Type",
                field_widget=self._admin_jurisdiction_type_combo,
                parent=jurisdiction_form,
                shell_bucket=self._admin_jurisdiction_field_shells,
            ),
        )

        self._admin_jurisdiction_parent_input = QLineEdit(jurisdiction_form)
        self._admin_jurisdiction_parent_input.setObjectName("PermitFormInput")
        self._admin_jurisdiction_parent_input.setPlaceholderText("Optional (usually for city jurisdictions)")
        self._admin_jurisdiction_parent_input.textChanged.connect(self._on_admin_jurisdiction_form_changed)
        jurisdiction_fields.addRow(
            self._build_admin_input_shell(
                label_text="Parent County (Optional)",
                field_widget=self._admin_jurisdiction_parent_input,
                parent=jurisdiction_form,
                shell_bucket=self._admin_jurisdiction_field_shells,
            ),
        )

        self._admin_jurisdiction_portals_input = QLineEdit(jurisdiction_form)
        self._admin_jurisdiction_portals_input.setObjectName("PermitFormInput")
        self._admin_jurisdiction_portals_input.setPlaceholderText("comma-separated URLs")
        self._admin_jurisdiction_portals_input.textChanged.connect(self._on_admin_jurisdiction_form_changed)
        jurisdiction_fields.addRow(
            self._build_admin_input_shell(
                label_text="Portal URLs",
                field_widget=self._admin_jurisdiction_portals_input,
                parent=jurisdiction_form,
                shell_bucket=self._admin_jurisdiction_field_shells,
            ),
        )

        self._admin_jurisdiction_vendor_input = QLineEdit(jurisdiction_form)
        self._admin_jurisdiction_vendor_input.setObjectName("PermitFormInput")
        self._admin_jurisdiction_vendor_input.setPlaceholderText("accela, click2gov, other")
        self._admin_jurisdiction_vendor_input.textChanged.connect(self._on_admin_jurisdiction_form_changed)
        jurisdiction_fields.addRow(
            self._build_admin_input_shell(
                label_text="Portal Vendor",
                field_widget=self._admin_jurisdiction_vendor_input,
                parent=jurisdiction_form,
                shell_bucket=self._admin_jurisdiction_field_shells,
            ),
        )

        self._admin_jurisdiction_notes_input = QLineEdit(jurisdiction_form)
        self._admin_jurisdiction_notes_input.setObjectName("PermitFormInput")
        self._admin_jurisdiction_notes_input.setPlaceholderText("Internal notes for this jurisdiction")
        self._admin_jurisdiction_notes_input.textChanged.connect(self._on_admin_jurisdiction_form_changed)
        jurisdiction_fields.addRow(
            self._build_admin_input_shell(
                label_text="Notes",
                field_widget=self._admin_jurisdiction_notes_input,
                parent=jurisdiction_form,
                shell_bucket=self._admin_jurisdiction_field_shells,
            ),
        )
        jurisdiction_fields_host_layout.addLayout(jurisdiction_fields)
        jurisdiction_fields_host_layout.addStretch(1)
        jurisdiction_details_split_row.addWidget(jurisdiction_fields_host, 1)

        attached_panel = QFrame(jurisdiction_form)
        attached_panel.setObjectName("AdminAttachedContactsPane")
        attached_panel_layout = QVBoxLayout(attached_panel)
        attached_panel_layout.setContentsMargins(10, 10, 10, 10)
        attached_panel_layout.setSpacing(8)
        self._admin_jurisdiction_attached_panel = attached_panel

        attached_label = QLabel("Attached Contacts (0)", attached_panel)
        attached_label.setObjectName("AdminSectionTitle")
        attached_panel_layout.addWidget(attached_label, 0)
        self._admin_jurisdiction_attached_label = attached_label

        attached_hint = QLabel(
            "Attach as many contacts as needed. Each card shows every saved bundle for quick lookup.",
            attached_panel,
        )
        attached_hint.setObjectName("AdminSectionHint")
        attached_hint.setWordWrap(True)
        attached_panel_layout.addWidget(attached_hint, 0)

        attached_picker_host = QWidget(attached_panel)
        attached_picker_host.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        attached_picker_row = QHBoxLayout(attached_picker_host)
        attached_picker_row.setContentsMargins(0, 0, 0, 0)
        attached_picker_row.setSpacing(8)
        attached_picker_row.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        add_attached_contact_button = QPushButton("Add", attached_picker_host)
        add_attached_contact_button.setObjectName("TrackerPanelActionButton")
        add_attached_contact_button.setFixedSize(108, 30)
        add_attached_contact_button.clicked.connect(self._admin_add_jurisdiction_contact)
        attached_picker_row.addWidget(add_attached_contact_button, 0)
        self._admin_jurisdiction_contact_add_button = add_attached_contact_button

        contact_picker_combo = QComboBox(attached_picker_host)
        contact_picker_combo.setObjectName("PermitFormCombo")
        contact_picker_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        contact_picker_combo.addItem("Select contact to attach...", "")
        attached_picker_row.addWidget(contact_picker_combo, 1)
        self._admin_jurisdiction_contact_picker_combo = contact_picker_combo

        attached_picker_row.addStretch(1)
        self._admin_jurisdiction_attached_picker_host = attached_picker_host
        attached_panel_layout.addWidget(attached_picker_host, 0, Qt.AlignmentFlag.AlignLeft)

        attached_contacts_host = QWidget(attached_panel)
        attached_contacts_host.setObjectName("AttachedContactsHost")
        attached_contacts_layout = QVBoxLayout(attached_contacts_host)
        attached_contacts_layout.setContentsMargins(0, 0, 0, 0)
        attached_contacts_layout.setSpacing(6)
        self._admin_jurisdiction_attached_contacts_host = attached_contacts_host
        attached_panel_layout.addWidget(attached_contacts_host, 1)

        jurisdiction_details_split_row.addWidget(attached_panel, 1)
        jurisdiction_form_layout.addLayout(jurisdiction_details_split_row, 1)

        jurisdiction_color_picker = self._build_admin_color_picker_widget(
            parent=jurisdiction_form,
            entity_kind="jurisdiction",
        )
        self._admin_jurisdiction_color_picker_host = jurisdiction_color_picker
        jurisdiction_color_shell = self._build_admin_input_shell(
            label_text="List Color Picker",
            field_widget=jurisdiction_color_picker,
            parent=jurisdiction_form,
            shell_bucket=None,
            field_stretch=0,
            left_align_field=True,
        )
        self._admin_jurisdiction_color_shell = jurisdiction_color_shell
        jurisdiction_form_layout.addWidget(jurisdiction_color_shell, 0, Qt.AlignmentFlag.AlignLeft)

        jurisdiction_right_layout.addStretch(15)
        jurisdiction_scroll.setWidget(jurisdiction_form)
        jurisdiction_right_layout.addWidget(jurisdiction_scroll, 70)
        jurisdiction_right_layout.addStretch(15)
        jurisdiction_layout.addWidget(jurisdiction_right_host, 3)

        tabs.addTab(contact_tab, "Contacts")
        tabs.addTab(jurisdiction_tab, "Jurisdictions")
        templates_tab = self._build_document_templates_view(tabs, as_tab=True)
        self._admin_templates_tab_index = tabs.addTab(templates_tab, "Document Templates")
        self._admin_active_tab_index = max(0, int(tabs.currentIndex()))
        tabs.currentChanged.connect(self._on_admin_tab_changed)
        return view

    def _admin_layout_service(self) -> WindowAdminLayoutService:
        service = self.__dict__.get("_admin_layout_service_instance")
        if service is None:
            service = WindowAdminLayoutService(self)
            self.__dict__["_admin_layout_service_instance"] = service
        return service

    def __getattr__(self, name: str):
        if getattr(WindowAdminLayoutService, name, None) is not None:
            return getattr(self._admin_layout_service(), name)
        super_getattr = getattr(super(), "__getattr__", None)
        if callable(super_getattr):
            return super_getattr(name)
        raise AttributeError(f"{self.__class__.__name__!s} object has no attribute {name!r}")
