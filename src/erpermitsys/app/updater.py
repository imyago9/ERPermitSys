from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

_GITHUB_API_BASE = "https://api.github.com/repos"
_GITHUB_ACCEPT_HEADER = "application/vnd.github+json"
_DEFAULT_USER_AGENT = "erpermitsys-updater"
_DEFAULT_TIMEOUT_SECONDS = 8.0


@dataclass(frozen=True)
class GitHubReleaseAsset:
    name: str
    download_url: str
    size_bytes: int = 0


@dataclass(frozen=True)
class GitHubUpdateInfo:
    repo: str
    current_version: str
    latest_version: str
    tag_name: str
    release_name: str
    release_url: str
    published_at: str
    notes: str
    asset: GitHubReleaseAsset | None


@dataclass(frozen=True)
class GitHubUpdateCheckResult:
    status: str
    message: str
    info: GitHubUpdateInfo | None = None


class GitHubReleaseUpdater:
    def __init__(self, *, timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS) -> None:
        self._timeout_seconds = float(timeout_seconds)

    def check_for_update(
        self,
        *,
        repo: str,
        current_version: str,
        asset_name: str = "",
    ) -> GitHubUpdateCheckResult:
        normalized_repo = normalize_github_repo(repo)
        if not normalized_repo:
            return GitHubUpdateCheckResult(
                status="not_configured",
                message="GitHub repository is not configured.",
            )

        release_url = f"{_GITHUB_API_BASE}/{normalized_repo}/releases/latest"
        try:
            payload = self._fetch_json(release_url)
        except HTTPError as exc:
            if exc.code == 404:
                return GitHubUpdateCheckResult(
                    status="no_release",
                    message=(
                        f"No published release was found for {normalized_repo}. "
                        "Create a GitHub Release first."
                    ),
                )
            return GitHubUpdateCheckResult(
                status="error",
                message=f"Update check failed with HTTP {exc.code}: {exc.reason}",
            )
        except URLError as exc:
            return GitHubUpdateCheckResult(
                status="error",
                message=f"Update check failed: {exc.reason}",
            )
        except Exception as exc:
            return GitHubUpdateCheckResult(
                status="error",
                message=f"Update check failed: {exc}",
            )

        tag_name = str(payload.get("tag_name") or "").strip()
        latest_version = normalize_version_text(tag_name)
        release_name = str(payload.get("name") or "").strip()
        html_url = str(payload.get("html_url") or "").strip()
        published_at = str(payload.get("published_at") or "").strip()
        notes = str(payload.get("body") or "").strip()

        if not latest_version:
            return GitHubUpdateCheckResult(
                status="error",
                message="Release metadata is missing tag_name.",
            )

        normalized_current = normalize_version_text(current_version)
        release_assets = self._parse_assets(payload.get("assets"))
        selected_asset = self._select_asset(release_assets, asset_name)
        info = GitHubUpdateInfo(
            repo=normalized_repo,
            current_version=normalized_current,
            latest_version=latest_version,
            tag_name=tag_name,
            release_name=release_name,
            release_url=html_url,
            published_at=published_at,
            notes=notes,
            asset=selected_asset,
        )

        if not is_version_newer(latest_version, normalized_current):
            return GitHubUpdateCheckResult(
                status="up_to_date",
                message=f"You are on the latest version ({normalized_current}).",
                info=info,
            )

        if selected_asset is None:
            requested_name = asset_name.strip()
            if requested_name:
                message = (
                    f"New version {latest_version} is available, but asset "
                    f"'{requested_name}' was not found in the release."
                )
            else:
                message = (
                    f"New version {latest_version} is available, but the release "
                    "does not include a downloadable asset."
                )
            return GitHubUpdateCheckResult(
                status="no_compatible_asset",
                message=message,
                info=info,
            )

        return GitHubUpdateCheckResult(
            status="update_available",
            message=f"Version {latest_version} is available.",
            info=info,
        )

    def download_asset(self, *, asset: GitHubReleaseAsset, destination: Path) -> Path:
        target = Path(destination).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)

        request = Request(
            asset.download_url,
            headers={
                "Accept": _GITHUB_ACCEPT_HEADER,
                "User-Agent": _DEFAULT_USER_AGENT,
            },
        )

        with urlopen(request, timeout=self._timeout_seconds) as response:
            with target.open("wb") as handle:
                while True:
                    chunk = response.read(1024 * 128)
                    if not chunk:
                        break
                    handle.write(chunk)

        return target

    def _fetch_json(self, url: str) -> dict:
        request = Request(
            url,
            headers={
                "Accept": _GITHUB_ACCEPT_HEADER,
                "User-Agent": _DEFAULT_USER_AGENT,
            },
        )
        with urlopen(request, timeout=self._timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise RuntimeError("Invalid release payload.")
        return payload

    def _parse_assets(self, payload: object) -> list[GitHubReleaseAsset]:
        if not isinstance(payload, list):
            return []
        assets: list[GitHubReleaseAsset] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            url = str(item.get("browser_download_url") or "").strip()
            size = item.get("size")
            if not name or not url:
                continue
            size_bytes = int(size) if isinstance(size, int) and size >= 0 else 0
            assets.append(
                GitHubReleaseAsset(
                    name=name,
                    download_url=url,
                    size_bytes=size_bytes,
                )
            )
        return assets

    def _select_asset(
        self,
        assets: list[GitHubReleaseAsset],
        requested_name: str,
    ) -> GitHubReleaseAsset | None:
        if not assets:
            return None

        requested = requested_name.strip()
        if requested:
            lowered = requested.lower()
            for asset in assets:
                if asset.name.lower() == lowered:
                    return asset
            return None

        zip_assets = [asset for asset in assets if asset.name.lower().endswith(".zip")]
        if zip_assets:
            return zip_assets[0]

        exe_assets = [asset for asset in assets if asset.name.lower().endswith(".exe")]
        if exe_assets:
            return exe_assets[0]

        return assets[0]


def normalize_github_repo(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    prefixes = (
        "https://github.com/",
        "http://github.com/",
        "github.com/",
    )
    for prefix in prefixes:
        if lowered.startswith(prefix):
            text = text[len(prefix):]
            break
    text = text.strip().strip("/")
    if text.lower().endswith(".git"):
        text = text[:-4]
    parts = [part for part in text.split("/") if part]
    if len(parts) != 2:
        return ""
    return f"{parts[0]}/{parts[1]}"


def normalize_version_text(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.lower().startswith("v") and len(text) > 1 and text[1].isdigit():
        return text[1:]
    return text


def is_version_newer(candidate: str, baseline: str) -> bool:
    candidate_value = normalize_version_text(candidate)
    baseline_value = normalize_version_text(baseline)
    if not candidate_value:
        return False
    if not baseline_value:
        return True

    candidate_tokens = tuple(int(token) for token in re.findall(r"\d+", candidate_value))
    baseline_tokens = tuple(int(token) for token in re.findall(r"\d+", baseline_value))

    if candidate_tokens and baseline_tokens:
        target_len = max(len(candidate_tokens), len(baseline_tokens))
        padded_candidate = candidate_tokens + (0,) * (target_len - len(candidate_tokens))
        padded_baseline = baseline_tokens + (0,) * (target_len - len(baseline_tokens))
        if padded_candidate != padded_baseline:
            return padded_candidate > padded_baseline

    return candidate_value.lower() != baseline_value.lower()


def is_packaged_runtime() -> bool:
    return bool(getattr(sys, "frozen", False))


def can_self_update_windows() -> bool:
    return os.name == "nt" and is_packaged_runtime()


_WINDOWS_UPDATE_SCRIPT = """param(
    [int]$AppPid,
    [string]$ZipPath,
    [string]$TargetDir,
    [string]$ExecutablePath
)

$ErrorActionPreference = "Stop"

for ($attempt = 0; $attempt -lt 180; $attempt++) {
    if (-not (Get-Process -Id $AppPid -ErrorAction SilentlyContinue)) {
        break
    }
    Start-Sleep -Milliseconds 500
}

$stageRoot = Join-Path $env:TEMP ("erpermitsys_update_" + [guid]::NewGuid().ToString("N"))
New-Item -Path $stageRoot -ItemType Directory -Force | Out-Null

try {
    Expand-Archive -LiteralPath $ZipPath -DestinationPath $stageRoot -Force
    $children = Get-ChildItem -LiteralPath $stageRoot -Force
    $sourceRoot = $stageRoot
    if ($children.Count -eq 1 -and $children[0].PSIsContainer) {
        $sourceRoot = $children[0].FullName
    }

    Copy-Item -Path (Join-Path $sourceRoot "*") -Destination $TargetDir -Recurse -Force
} catch {
    # Keep going so we can relaunch even if copy fails.
}
Start-Process -FilePath $ExecutablePath
"""


def launch_windows_zip_updater(
    *,
    archive_path: Path,
    app_pid: int,
    target_dir: Path,
    executable_path: Path,
) -> tuple[bool, str]:
    if os.name != "nt":
        return False, "Self-update launcher currently supports Windows only."

    archive = Path(archive_path).expanduser().resolve()
    target = Path(target_dir).expanduser().resolve()
    executable = Path(executable_path).expanduser().resolve()

    if not archive.exists():
        return False, f"Downloaded update file was not found: {archive}"
    if not target.exists() or not target.is_dir():
        return False, f"Install folder was not found: {target}"
    if not executable.exists():
        return False, f"Executable was not found: {executable}"

    try:
        work_dir = Path(tempfile.mkdtemp(prefix="erpermitsys_updater_"))
        script_path = work_dir / "apply_update.ps1"
        script_path.write_text(_WINDOWS_UPDATE_SCRIPT, encoding="utf-8")

        command = [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script_path),
            "-AppPid",
            str(int(app_pid)),
            "-ZipPath",
            str(archive),
            "-TargetDir",
            str(target),
            "-ExecutablePath",
            str(executable),
        ]

        creationflags = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        creationflags |= int(getattr(subprocess, "DETACHED_PROCESS", 0))

        subprocess.Popen(
            command,
            close_fds=True,
            creationflags=creationflags,
            cwd=str(work_dir),
        )
    except Exception as exc:
        return False, f"Could not launch update installer: {exc}"

    return True, ""
