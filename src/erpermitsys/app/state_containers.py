from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from erpermitsys.app.settings_store import SupabaseSettings


@dataclass(slots=True)
class WorkspaceState:
    selected_property_id: str = ""
    selected_permit_id: str = ""
    selected_document_slot_id: str = ""
    selected_document_id: str = ""
    active_permit_type_filter: str = "all"


@dataclass(slots=True)
class AdminState:
    contact_dirty: bool = False
    jurisdiction_dirty: bool = False
    contacts_search_text: str = ""
    jurisdictions_search_text: str = ""
    templates_search_text: str = ""


@dataclass(slots=True)
class StorageState:
    backend: str
    data_storage_folder: Path
    supabase_settings: SupabaseSettings = field(default_factory=SupabaseSettings)
    supabase_merge_on_switch: bool = True
