from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from erpermitsys.app.tracker_models import TrackerDataBundle


BACKEND_LOCAL_JSON = "local_json"
BACKEND_SUPABASE = "supabase"
DEFAULT_DATA_FILE_NAME = "permit_tracker_data.json"
_SCHEMA_VERSION = 2
_APP_ID = "erpermitsys"


@dataclass(frozen=True, slots=True)
class DataLoadResult:
    bundle: TrackerDataBundle
    source: str = "primary"
    warning: str = ""


class TrackerDataStore(Protocol):
    backend: str
    data_root: Path

    @property
    def storage_file_path(self) -> Path:
        raise NotImplementedError

    def has_saved_data(self) -> bool:
        raise NotImplementedError

    def load_bundle(self) -> DataLoadResult:
        raise NotImplementedError

    def save_bundle(self, bundle: TrackerDataBundle) -> None:
        raise NotImplementedError


class LocalJsonDataStore:
    backend = BACKEND_LOCAL_JSON

    def __init__(
        self,
        data_root: Path | str,
        *,
        data_file_name: str = DEFAULT_DATA_FILE_NAME,
    ) -> None:
        self.data_root = _normalize_path(Path(data_root))
        self._data_file_name = data_file_name

    @property
    def storage_file_path(self) -> Path:
        return self.data_root / self._data_file_name

    @property
    def backup_file_path(self) -> Path:
        storage_file = self.storage_file_path
        return storage_file.with_suffix(f"{storage_file.suffix}.bak")

    def has_saved_data(self) -> bool:
        return self.storage_file_path.exists() and self.storage_file_path.is_file()

    def load_bundle(self) -> DataLoadResult:
        primary_path = self.storage_file_path
        if not primary_path.exists():
            return DataLoadResult(bundle=TrackerDataBundle(), source="empty")

        try:
            bundle = self._read_bundle(primary_path)
            return DataLoadResult(bundle=bundle, source="primary")
        except Exception as primary_error:
            backup_path = self.backup_file_path
            if backup_path.exists() and backup_path.is_file():
                try:
                    bundle = self._read_bundle(backup_path)
                    warning = (
                        "Primary data file could not be read; recovered from backup copy."
                    )
                    return DataLoadResult(bundle=bundle, source="backup", warning=warning)
                except Exception as backup_error:
                    warning = (
                        "Primary and backup data files could not be read. "
                        f"Primary error: {primary_error}. Backup error: {backup_error}."
                    )
                    return DataLoadResult(
                        bundle=TrackerDataBundle(),
                        source="empty",
                        warning=warning,
                    )
            warning = f"Primary data file could not be read: {primary_error}."
            return DataLoadResult(bundle=TrackerDataBundle(), source="empty", warning=warning)

    def save_bundle(self, bundle: TrackerDataBundle) -> None:
        self.data_root.mkdir(parents=True, exist_ok=True)
        payload = {
            "app": _APP_ID,
            "schemaVersion": _SCHEMA_VERSION,
            "backend": self.backend,
            "savedAtUtc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "data": bundle.to_payload(),
        }
        self._write_atomic_json(payload)

    def _read_bundle(self, path: Path) -> TrackerDataBundle:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("Storage payload must be a JSON object")
        data_payload = raw.get("data")
        if isinstance(data_payload, dict):
            return TrackerDataBundle.from_payload(data_payload)
        return TrackerDataBundle.from_payload(raw)

    def _write_atomic_json(self, payload: dict[str, object]) -> None:
        target_path = self.storage_file_path
        backup_path = self.backup_file_path

        if target_path.exists():
            try:
                shutil.copy2(target_path, backup_path)
            except Exception:
                pass

        fd, temp_path = tempfile.mkstemp(
            prefix=f"{target_path.stem}.",
            suffix=".tmp",
            dir=str(self.data_root),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, target_path)
        except Exception:
            try:
                os.unlink(temp_path)
            except Exception:
                pass
            raise


class SupabaseDataStore:
    backend = BACKEND_SUPABASE

    def __init__(self, data_root: Path | str) -> None:
        self.data_root = _normalize_path(Path(data_root))

    @property
    def storage_file_path(self) -> Path:
        return self.data_root / ".supabase-placeholder"

    def has_saved_data(self) -> bool:
        return False

    def load_bundle(self) -> DataLoadResult:
        raise NotImplementedError(
            "Supabase data backend is not implemented yet. Use local_json for now."
        )

    def save_bundle(self, bundle: TrackerDataBundle) -> None:
        _ = bundle
        raise NotImplementedError(
            "Supabase data backend is not implemented yet. Use local_json for now."
        )


def create_data_store(backend: str, data_root: Path | str) -> TrackerDataStore:
    normalized_backend = str(backend or "").strip().lower()
    if normalized_backend == BACKEND_SUPABASE:
        return SupabaseDataStore(data_root)
    return LocalJsonDataStore(data_root)


def _normalize_path(path: Path) -> Path:
    expanded = path.expanduser()
    try:
        return expanded.resolve()
    except Exception:
        return expanded
