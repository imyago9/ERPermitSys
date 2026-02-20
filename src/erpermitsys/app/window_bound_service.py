from __future__ import annotations


class WindowBoundService:
    """Forwards attribute reads/writes to a host window instance."""

    __slots__ = ("_window",)

    def __init__(self, window: object) -> None:
        object.__setattr__(self, "_window", window)

    @property
    def window(self) -> object:
        return object.__getattribute__(self, "_window")

    def __getattr__(self, name: str):
        return getattr(object.__getattribute__(self, "_window"), name)

    def __setattr__(self, name: str, value: object) -> None:
        if name == "_window":
            object.__setattr__(self, name, value)
            return
        setattr(object.__getattribute__(self, "_window"), name, value)
