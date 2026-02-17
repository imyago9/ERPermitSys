from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QApplication


_REWRITE_ROOT = Path(__file__).resolve().parents[3]
_ASSETS_DIR = _REWRITE_ROOT / "assets"


def asset_path(*parts: str) -> str:
    return str(_ASSETS_DIR.joinpath(*parts))


def current_theme_mode(default: str = "dark") -> str:
    app = QApplication.instance()
    if app is None:
        return default
    value = app.property("erpermitsys.theme_mode")
    if isinstance(value, str) and value in ("light", "dark"):
        return value
    return default


def icon_asset_path(file_name: str, *, mode: str | None = None) -> str:
    selected_mode = mode if mode in ("light", "dark") else current_theme_mode()
    icon_dir = _ASSETS_DIR / "icons"
    if selected_mode == "light":
        source = Path(file_name)
        tinted = icon_dir / f"{source.stem}_black{source.suffix}"
        if tinted.exists():
            return str(tinted)
    return str(icon_dir / file_name)
