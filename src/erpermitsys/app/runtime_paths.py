from __future__ import annotations

import sys
from pathlib import Path


def is_frozen_runtime() -> bool:
    return bool(getattr(sys, "frozen", False))


def source_root() -> Path:
    return Path(__file__).resolve().parents[3]


def bundle_root() -> Path:
    if is_frozen_runtime():
        meipass = getattr(sys, "_MEIPASS", None)
        if isinstance(meipass, str) and meipass.strip():
            return Path(meipass).resolve()
        return Path(sys.executable).resolve().parent
    return source_root()


def app_root() -> Path:
    if is_frozen_runtime():
        return Path(sys.executable).resolve().parent
    return source_root()


def bundled_path(*parts: str) -> Path:
    return bundle_root().joinpath(*parts)


def app_path(*parts: str) -> Path:
    return app_root().joinpath(*parts)
