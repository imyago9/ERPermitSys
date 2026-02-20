from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
from uuid import uuid4

from erpermitsys.app.data_store import BACKEND_LOCAL_SQLITE, BACKEND_SUPABASE
from erpermitsys.app.tracker_models import (
    PermitDocumentFolder,
    PermitDocumentRecord,
    PermitRecord,
)


_PERMITS_ROOT = "permits"
_DOCUMENTS_ROOT = "documents"
_SAFE_SEGMENT_PATTERN = re.compile(r"[^a-z0-9]+")
_SAFE_FILE_PATTERN = re.compile(r"[\\/:*?\"<>|]+")
_SUPABASE_URI_SCHEME = "supabase://"
_DEFAULT_SUPABASE_BUCKET = "erpermitsys-documents"
_DEFAULT_SUPABASE_PREFIX = "tracker"
_DEFAULT_SUPABASE_TIMEOUT_SECONDS = 8.0


@dataclass(frozen=True, slots=True)
class SupabaseDocumentStoreConfig:
    url: str = ""
    api_key: str = ""
    bucket: str = _DEFAULT_SUPABASE_BUCKET
    prefix: str = _DEFAULT_SUPABASE_PREFIX
    timeout_seconds: float = _DEFAULT_SUPABASE_TIMEOUT_SECONDS

    @property
    def configured(self) -> bool:
        return bool(self.url and self.api_key and self.bucket)

    @classmethod
    def from_mapping(cls, value: dict[str, Any] | None) -> SupabaseDocumentStoreConfig:
        raw = value or {}
        url = str(raw.get("url", "") or "").strip().rstrip("/")
        api_key = str(raw.get("api_key", "") or "").strip()
        bucket = str(raw.get("bucket", "") or "").strip() or _DEFAULT_SUPABASE_BUCKET
        prefix = str(raw.get("prefix", "") or "").strip().strip("/") or _DEFAULT_SUPABASE_PREFIX
        timeout_raw = raw.get("timeout_seconds", _DEFAULT_SUPABASE_TIMEOUT_SECONDS)
        try:
            timeout_seconds = float(timeout_raw)
        except Exception:
            timeout_seconds = _DEFAULT_SUPABASE_TIMEOUT_SECONDS
        timeout_seconds = max(1.0, timeout_seconds)
        return cls(
            url=url,
            api_key=api_key,
            bucket=bucket,
            prefix=prefix,
            timeout_seconds=timeout_seconds,
        )


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
    backend = BACKEND_LOCAL_SQLITE

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
        lineage = _folder_lineage(permit, folder)
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


class SupabasePermitDocumentStore:
    backend = BACKEND_SUPABASE

    def __init__(
        self,
        data_root: Path | str,
        *,
        config: SupabaseDocumentStoreConfig | None = None,
    ) -> None:
        self.data_root = _normalize_path(Path(data_root))
        self._config = config or SupabaseDocumentStoreConfig()
        self._cache_root = self.data_root / ".supabase-cache"

    def update_data_root(self, data_root: Path | str) -> None:
        self.data_root = _normalize_path(Path(data_root))
        self._cache_root = self.data_root / ".supabase-cache"

    def ensure_folder_structure(self, permit: PermitRecord) -> None:
        _ = permit
        self._require_config()
        self._cache_root.mkdir(parents=True, exist_ok=True)

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

        config = self._require_config()
        object_dir = self._remote_folder_prefix(permit, folder, cycle_folder=cycle_folder)
        requested_name = _safe_file_name(source_file.name)
        stored_name = f"{uuid4().hex[:12]}-{requested_name}"
        object_path = f"{object_dir}/{stored_name}" if object_dir else stored_name

        payload = source_file.read_bytes()
        self._upload_object(
            bucket=config.bucket,
            object_path=object_path,
            payload=payload,
        )
        return PermitDocumentRecord(
            document_id=uuid4().hex,
            folder_id=folder.folder_id,
            original_name=source_file.name,
            stored_name=stored_name,
            relative_path=_build_supabase_uri(config.bucket, object_path),
            imported_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            byte_size=max(0, int(source_file.stat().st_size)),
            sha256=_sha256_file(source_file),
        )

    def delete_document_file(self, document: PermitDocumentRecord) -> None:
        parsed = _parse_supabase_uri(document.relative_path)
        if parsed is None:
            return
        bucket, object_path = parsed
        try:
            self._delete_object(bucket=bucket, object_path=object_path)
        except Exception:
            return
        cached_path = self._cache_root / bucket / object_path
        try:
            if cached_path.exists() and cached_path.is_file():
                cached_path.unlink()
        except Exception:
            return

    def delete_folder_tree(self, permit: PermitRecord, folder: PermitDocumentFolder) -> None:
        prefix = self._remote_folder_prefix(permit, folder, cycle_folder="")
        self._delete_prefix(prefix)

    def delete_permit_tree(self, permit: PermitRecord) -> None:
        prefix = self._permit_prefix(permit)
        self._delete_prefix(prefix)

    def resolve_document_path(self, relative_path: str) -> Path | None:
        normalized_input = str(relative_path or "").strip()
        if not normalized_input:
            return None

        parsed = _parse_supabase_uri(normalized_input)
        if parsed is None:
            fallback = Path(normalized_input).expanduser()
            if not fallback.is_absolute():
                fallback = self.data_root / fallback
            resolved = _normalize_path(fallback)
            return resolved if resolved.exists() else None

        bucket, object_path = parsed
        cache_path = self._cache_root / bucket / object_path
        if cache_path.exists() and cache_path.is_file():
            return cache_path

        cache_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            payload = self._download_object(bucket=bucket, object_path=object_path)
        except Exception:
            return None
        try:
            cache_path.write_bytes(payload)
        except Exception:
            return None
        return cache_path

    def folder_path(self, permit: PermitRecord, folder: PermitDocumentFolder) -> Path:
        prefix = self._remote_folder_prefix(permit, folder, cycle_folder="")
        return self._cache_root / "folders" / prefix

    def _permit_prefix(self, permit: PermitRecord) -> str:
        config = self._require_config()
        permit_type = _safe_segment(permit.permit_type or "building") or "building"
        permit_id = _safe_segment(permit.permit_id)
        if not permit_id:
            raise ValueError("Permit must have a permit_id before storing documents.")
        return f"{config.prefix}/{_PERMITS_ROOT}/{permit_type}/{permit_id}/{_DOCUMENTS_ROOT}"

    def _remote_folder_prefix(
        self,
        permit: PermitRecord,
        folder: PermitDocumentFolder,
        *,
        cycle_folder: str,
    ) -> str:
        base = self._permit_prefix(permit)
        lineage = _folder_lineage(permit, folder)
        if not lineage:
            raise ValueError("Document folder must be part of the permit folder tree.")

        segments: list[str] = [base]
        for entry in lineage:
            folder_id = _safe_segment(entry.folder_id)
            if not folder_id:
                raise ValueError("Document folder must have a folder_id.")
            folder_name = _safe_segment(entry.name) or "folder"
            segments.append(f"{folder_id}__{folder_name}")
        cycle_segment = _safe_segment(cycle_folder)
        if cycle_segment:
            segments.append(cycle_segment)
        return "/".join(segment.strip("/") for segment in segments if segment.strip("/"))

    def _delete_prefix(self, prefix: str) -> None:
        config = self._require_config()
        objects = self._list_objects(bucket=config.bucket, prefix=prefix)
        for object_path in objects:
            try:
                self._delete_object(bucket=config.bucket, object_path=object_path)
            except Exception:
                continue

    def _upload_object(self, *, bucket: str, object_path: str, payload: bytes) -> None:
        safe_bucket = quote(bucket, safe="")
        safe_object_path = quote(object_path, safe="/")
        self._request_bytes(
            method="POST",
            path=f"/storage/v1/object/{safe_bucket}/{safe_object_path}",
            payload=payload,
            content_type="application/octet-stream",
            headers={"x-upsert": "false"},
        )

    def _delete_object(self, *, bucket: str, object_path: str) -> None:
        safe_bucket = quote(bucket, safe="")
        safe_object_path = quote(object_path, safe="/")
        self._request_bytes(
            method="DELETE",
            path=f"/storage/v1/object/{safe_bucket}/{safe_object_path}",
            payload=None,
            content_type="",
            headers={},
        )

    def _download_object(self, *, bucket: str, object_path: str) -> bytes:
        safe_bucket = quote(bucket, safe="")
        safe_object_path = quote(object_path, safe="/")
        try:
            return self._request_bytes(
                method="GET",
                path=f"/storage/v1/object/authenticated/{safe_bucket}/{safe_object_path}",
                payload=None,
                content_type="",
                headers={},
            )
        except Exception:
            return self._request_bytes(
                method="GET",
                path=f"/storage/v1/object/{safe_bucket}/{safe_object_path}",
                payload=None,
                content_type="",
                headers={},
            )

    def _list_objects(self, *, bucket: str, prefix: str) -> list[str]:
        safe_bucket = quote(bucket, safe="")
        normalized_prefix = str(prefix or "").strip().strip("/")
        objects: list[str] = []
        offset = 0
        limit = 500
        while True:
            rows = self._request_json(
                method="POST",
                path=f"/storage/v1/object/list/{safe_bucket}",
                payload={
                    "prefix": normalized_prefix,
                    "limit": limit,
                    "offset": offset,
                    "sortBy": {"column": "name", "order": "asc"},
                },
            )
            if not isinstance(rows, list) or not rows:
                break
            for row in rows:
                if not isinstance(row, dict):
                    continue
                name = str(row.get("name", "") or "").strip().lstrip("/")
                if not name:
                    continue
                object_path = f"{normalized_prefix}/{name}" if normalized_prefix else name
                objects.append(object_path)
            if len(rows) < limit:
                break
            offset += limit
        return objects

    def _require_config(self) -> SupabaseDocumentStoreConfig:
        if self._config.configured:
            return self._config
        raise RuntimeError(
            "Supabase backend is selected, but Supabase URL, API key, or storage bucket is missing. "
            "Set them in Settings > General Settings > Data backend."
        )

    def _request_json(self, *, method: str, path: str, payload: Any | None) -> Any:
        body = self._request_bytes(
            method=method,
            path=path,
            payload=(
                json.dumps(payload, ensure_ascii=False).encode("utf-8")
                if payload is not None
                else None
            ),
            content_type="application/json" if payload is not None else "",
            headers={},
        )
        if not body:
            return None
        try:
            return json.loads(body.decode("utf-8"))
        except Exception as exc:
            raise RuntimeError(
                f"Supabase storage endpoint returned non-JSON payload for {path} ({len(body)} bytes)."
            ) from exc

    def _request_bytes(
        self,
        *,
        method: str,
        path: str,
        payload: bytes | None,
        content_type: str,
        headers: dict[str, str],
    ) -> bytes:
        config = self._require_config()
        request_headers = {
            "apikey": config.api_key,
            "Authorization": f"Bearer {config.api_key}",
        }
        if content_type:
            request_headers["Content-Type"] = content_type
        request_headers.update(headers)
        request_url = f"{config.url.rstrip('/')}{path}"
        request = Request(
            request_url,
            data=payload,
            headers=request_headers,
            method=method.upper(),
        )
        try:
            with urlopen(request, timeout=config.timeout_seconds) as response:
                return response.read()
        except HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", errors="replace").strip()
            except Exception:
                body = ""
            detail = f"{exc.code} {exc.reason}"
            if body:
                detail = f"{detail}: {body}"
            raise RuntimeError(f"Supabase storage request failed for {path}: {detail}") from exc
        except URLError as exc:
            raise RuntimeError(f"Supabase storage request failed for {path}: {exc}") from exc


def create_document_store(
    backend: str,
    data_root: Path | str,
    *,
    supabase_config: SupabaseDocumentStoreConfig | None = None,
) -> PermitDocumentStore:
    normalized_backend = str(backend or "").strip().lower()
    if normalized_backend == BACKEND_SUPABASE:
        return SupabasePermitDocumentStore(data_root, config=supabase_config)
    return LocalPermitDocumentStore(data_root)


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


def _folder_lineage(
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


def _parse_supabase_uri(value: str) -> tuple[str, str] | None:
    text = str(value or "").strip()
    if not text or not text.startswith(_SUPABASE_URI_SCHEME):
        return None
    body = text[len(_SUPABASE_URI_SCHEME) :]
    bucket, sep, object_path = body.partition("/")
    if not sep:
        return None
    normalized_bucket = bucket.strip()
    normalized_path = object_path.strip().lstrip("/")
    if not normalized_bucket or not normalized_path:
        return None
    return normalized_bucket, normalized_path


def _build_supabase_uri(bucket: str, object_path: str) -> str:
    normalized_bucket = str(bucket or "").strip()
    normalized_path = str(object_path or "").strip().lstrip("/")
    return f"{_SUPABASE_URI_SCHEME}{normalized_bucket}/{normalized_path}"
