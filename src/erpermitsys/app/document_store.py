from __future__ import annotations

import hashlib
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from erpermitsys.app.data_store import BACKEND_LOCAL_JSON, BACKEND_SUPABASE
from erpermitsys.app.tracker_models import (
    PermitDocumentFolder,
    PermitDocumentRecord,
    PermitRecord,
)


_PERMITS_ROOT = "permits"
_DOCUMENTS_ROOT = "documents"
_SAFE_SEGMENT_PATTERN = re.compile(r"[^a-z0-9]+")
_SAFE_FILE_PATTERN = re.compile(r"[\\/:*?\"<>|]+")


class PermitDocumentStore(Protocol):
    backend: str
    data_root: Path

    def update_data_root(self, data_root: Path | str) -> None:
        raise NotImplementedError

    def ensure_folder_structure(self, permit: PermitRecord) -> None:
        raise NotImplementedError

    def import_document(
        self,
        *,
        permit: PermitRecord,
        folder: PermitDocumentFolder,
        source_path: Path | str,
        cycle_folder: str = "",
    ) -> PermitDocumentRecord:
        raise NotImplementedError

    def delete_document_file(self, document: PermitDocumentRecord) -> None:
        raise NotImplementedError

    def delete_folder_tree(self, permit: PermitRecord, folder: PermitDocumentFolder) -> None:
        raise NotImplementedError

    def delete_permit_tree(self, permit: PermitRecord) -> None:
        raise NotImplementedError

    def resolve_document_path(self, relative_path: str) -> Path | None:
        raise NotImplementedError

    def folder_path(self, permit: PermitRecord, folder: PermitDocumentFolder) -> Path:
        raise NotImplementedError


class LocalPermitDocumentStore:
    backend = BACKEND_LOCAL_JSON

    def __init__(self, data_root: Path | str) -> None:
        self.data_root = _normalize_path(Path(data_root))

    def update_data_root(self, data_root: Path | str) -> None:
        self.data_root = _normalize_path(Path(data_root))

    @property
    def permits_root(self) -> Path:
        return self.data_root / _PERMITS_ROOT

    def permit_path(self, permit: PermitRecord) -> Path:
        permit_type = _safe_segment(permit.permit_type or "building") or "building"
        permit_id = _safe_segment(permit.permit_id)
        if not permit_id:
            raise ValueError("Permit must have a permit_id before storing documents.")
        return self.permits_root / permit_type / permit_id

    def folder_path(self, permit: PermitRecord, folder: PermitDocumentFolder) -> Path:
        lineage = self._folder_lineage(permit, folder)
        if not lineage:
            raise ValueError("Document folder must be part of the permit folder tree.")

        current_path = self.permit_path(permit) / _DOCUMENTS_ROOT
        for entry in lineage:
            folder_id = _safe_segment(entry.folder_id)
            if not folder_id:
                raise ValueError("Document folder must have a folder_id.")
            folder_name = _safe_segment(entry.name) or "folder"
            folder_segment = f"{folder_id}__{folder_name}"
            current_path = current_path / folder_segment
        return current_path

    def ensure_folder_structure(self, permit: PermitRecord) -> None:
        permit_root = self.permit_path(permit)
        documents_root = permit_root / _DOCUMENTS_ROOT
        documents_root.mkdir(parents=True, exist_ok=True)
        for folder in permit.document_folders:
            self.folder_path(permit, folder).mkdir(parents=True, exist_ok=True)

    def import_document(
        self,
        *,
        permit: PermitRecord,
        folder: PermitDocumentFolder,
        source_path: Path | str,
        cycle_folder: str = "",
    ) -> PermitDocumentRecord:
        source_file = Path(source_path).expanduser()
        if not source_file.exists() or not source_file.is_file():
            raise FileNotFoundError(f"File not found: {source_file}")

        destination_dir = self.folder_path(permit, folder)
        cycle_segment = _safe_segment(cycle_folder)
        if cycle_segment:
            destination_dir = destination_dir / cycle_segment
        destination_dir.mkdir(parents=True, exist_ok=True)

        requested_name = _safe_file_name(source_file.name)
        destination_file = _next_available_path(destination_dir / requested_name)
        shutil.copy2(source_file, destination_file)

        try:
            relative_path = destination_file.relative_to(self.data_root).as_posix()
        except Exception as exc:
            raise RuntimeError(f"Could not build relative document path: {destination_file}") from exc

        return PermitDocumentRecord(
            document_id=uuid4().hex,
            folder_id=folder.folder_id,
            original_name=source_file.name,
            stored_name=destination_file.name,
            relative_path=relative_path,
            imported_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            byte_size=max(0, int(destination_file.stat().st_size)),
            sha256=_sha256_file(destination_file),
        )

    def delete_document_file(self, document: PermitDocumentRecord) -> None:
        target = self.resolve_document_path(document.relative_path)
        if target is None or not target.exists() or not target.is_file():
            return
        try:
            target.unlink()
        except Exception:
            return
        self._prune_empty_directories(target.parent)

    def delete_folder_tree(self, permit: PermitRecord, folder: PermitDocumentFolder) -> None:
        try:
            folder_dir = self.folder_path(permit, folder)
        except Exception:
            return
        if folder_dir.exists() and folder_dir.is_dir():
            shutil.rmtree(folder_dir, ignore_errors=True)
            self._prune_empty_directories(folder_dir.parent)

    def delete_permit_tree(self, permit: PermitRecord) -> None:
        permit_root = self.permit_path(permit)
        if permit_root.exists() and permit_root.is_dir():
            shutil.rmtree(permit_root, ignore_errors=True)
        category_root = permit_root.parent
        self._prune_empty_directories(category_root)

    def resolve_document_path(self, relative_path: str) -> Path | None:
        normalized_input = str(relative_path or "").strip()
        if not normalized_input:
            return None

        candidate = Path(normalized_input).expanduser()
        if not candidate.is_absolute():
            candidate = self.data_root / candidate
        normalized_candidate = _normalize_path(candidate)
        try:
            normalized_candidate.relative_to(self.data_root)
        except Exception:
            return None
        return normalized_candidate

    def _prune_empty_directories(self, start_path: Path) -> None:
        current = start_path
        permits_root = self.permits_root
        while True:
            if current == permits_root or current == self.data_root:
                break
            if not current.exists() or not current.is_dir():
                break
            try:
                next(current.iterdir())
                break
            except StopIteration:
                try:
                    current.rmdir()
                except Exception:
                    break
                parent = current.parent
                if parent == current:
                    break
                current = parent
            except Exception:
                break

    def _folder_lineage(
        self,
        permit: PermitRecord,
        folder: PermitDocumentFolder,
    ) -> list[PermitDocumentFolder]:
        by_id: dict[str, PermitDocumentFolder] = {
            row.folder_id: row for row in permit.document_folders if row.folder_id.strip()
        }
        current = by_id.get(folder.folder_id)
        if current is None:
            return []

        chain: list[PermitDocumentFolder] = []
        visited: set[str] = set()
        max_hops = max(1, len(by_id) + 1)
        hops = 0
        while current is not None and hops < max_hops:
            hops += 1
            folder_id = str(current.folder_id).strip()
            if not folder_id or folder_id in visited:
                break
            visited.add(folder_id)
            chain.append(current)
            parent_id = str(current.parent_folder_id).strip()
            if not parent_id:
                break
            current = by_id.get(parent_id)
        chain.reverse()
        return chain


class SupabasePermitDocumentStore:
    backend = BACKEND_SUPABASE

    def __init__(self, data_root: Path | str) -> None:
        self.data_root = _normalize_path(Path(data_root))

    def update_data_root(self, data_root: Path | str) -> None:
        self.data_root = _normalize_path(Path(data_root))

    def ensure_folder_structure(self, permit: PermitRecord) -> None:
        _ = permit
        raise NotImplementedError("Supabase document storage is not implemented yet.")

    def import_document(
        self,
        *,
        permit: PermitRecord,
        folder: PermitDocumentFolder,
        source_path: Path | str,
        cycle_folder: str = "",
    ) -> PermitDocumentRecord:
        _ = (permit, folder, source_path, cycle_folder)
        raise NotImplementedError("Supabase document storage is not implemented yet.")

    def delete_document_file(self, document: PermitDocumentRecord) -> None:
        _ = document
        raise NotImplementedError("Supabase document storage is not implemented yet.")

    def delete_folder_tree(self, permit: PermitRecord, folder: PermitDocumentFolder) -> None:
        _ = (permit, folder)
        raise NotImplementedError("Supabase document storage is not implemented yet.")

    def delete_permit_tree(self, permit: PermitRecord) -> None:
        _ = permit
        raise NotImplementedError("Supabase document storage is not implemented yet.")

    def resolve_document_path(self, relative_path: str) -> Path | None:
        _ = relative_path
        raise NotImplementedError("Supabase document storage is not implemented yet.")

    def folder_path(self, permit: PermitRecord, folder: PermitDocumentFolder) -> Path:
        _ = (permit, folder)
        raise NotImplementedError("Supabase document storage is not implemented yet.")


def _normalize_path(path: Path) -> Path:
    expanded = path.expanduser()
    try:
        return expanded.resolve()
    except Exception:
        return expanded


def _safe_segment(value: str) -> str:
    normalized = str(value or "").strip().lower()
    normalized = _SAFE_SEGMENT_PATTERN.sub("-", normalized)
    normalized = normalized.strip("-")
    if not normalized:
        return ""
    return normalized[:80]


def _safe_file_name(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        normalized = f"document-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}.bin"
    normalized = _SAFE_FILE_PATTERN.sub("-", normalized)
    normalized = normalized.replace("\n", " ").replace("\r", " ")
    normalized = normalized.strip().strip(".")
    if not normalized:
        normalized = f"document-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}.bin"
    return normalized[:180]


def _next_available_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    index = 2
    while True:
        candidate = parent / f"{stem}-{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()
