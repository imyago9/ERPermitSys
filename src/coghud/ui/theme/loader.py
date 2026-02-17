from __future__ import annotations

from pathlib import Path
from typing import Iterable, Literal

from PySide6.QtWidgets import QApplication


_THEME_DIR = Path(__file__).resolve().parent
ThemeMode = Literal["light", "dark"]
_DEFAULT_MODE: ThemeMode = "light"
_MODE_QSS_FILES: dict[ThemeMode, tuple[str, ...]] = {
    "light": ("window.qss", "window_light.qss"),
    "dark": ("window.qss", "window_dark.qss"),
}


def load_stylesheet(
    file_names: Iterable[str] | None = None,
    *,
    mode: ThemeMode = _DEFAULT_MODE,
) -> str:
    selected_files = tuple(file_names) if file_names is not None else _MODE_QSS_FILES.get(
        mode,
        _MODE_QSS_FILES[_DEFAULT_MODE],
    )

    parts: list[str] = []
    for file_name in selected_files:
        qss_path = _THEME_DIR / file_name
        if not qss_path.exists():
            continue
        stylesheet = qss_path.read_text(encoding="utf-8").strip()
        if stylesheet:
            parts.append(stylesheet)
    return "\n\n".join(parts)


def apply_app_theme(
    app: QApplication,
    *,
    mode: ThemeMode = _DEFAULT_MODE,
    file_names: Iterable[str] | None = None,
) -> None:
    app.setProperty("coghud.theme_mode", mode)
    app.setStyleSheet(load_stylesheet(file_names=file_names, mode=mode))
