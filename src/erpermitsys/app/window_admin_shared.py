from __future__ import annotations

from erpermitsys.app.tracker_models import normalize_list_color

_ADMIN_LIST_COLOR_PRESETS: tuple[str, ...] = (
    "#2563EB",
    "#0D9488",
    "#16A34A",
    "#65A30D",
    "#CA8A04",
    "#EA580C",
    "#DC2626",
    "#BE185D",
    "#7C3AED",
    "#4338CA",
    "#0369A1",
    "#4B5563",
)


def _hex_color_channels(value: str) -> tuple[int, int, int]:
    normalized = normalize_list_color(value)
    if not normalized:
        return (99, 116, 137)
    return (
        int(normalized[1:3], 16),
        int(normalized[3:5], 16),
        int(normalized[5:7], 16),
    )


def _mix_color_channels(
    source: tuple[int, int, int],
    target: tuple[int, int, int],
    target_weight: float,
) -> tuple[int, int, int]:
    weight = max(0.0, min(1.0, float(target_weight)))
    keep = 1.0 - weight
    return tuple(
        max(0, min(255, int(round((src * keep) + (dst * weight)))))
        for src, dst in zip(source, target)
    )


def _normalize_card_tint_channels(channels: tuple[int, int, int]) -> tuple[int, int, int]:
    clamped = tuple(max(22, min(232, int(value))) for value in channels)
    darkest = min(clamped)
    brightest = max(clamped)
    if brightest < 56:
        return _mix_color_channels(clamped, (118, 136, 156), 0.46)
    if darkest > 224:
        return _mix_color_channels(clamped, (108, 126, 148), 0.52)
    return clamped


def _rgba_text(channels: tuple[int, int, int], alpha: int) -> str:
    r, g, b = channels
    return f"rgba({r}, {g}, {b}, {max(0, min(255, int(alpha)))})"


def _dot_ring_color(channels: tuple[int, int, int], *, selected: bool) -> str:
    luminance = (
        (0.2126 * channels[0]) + (0.7152 * channels[1]) + (0.0722 * channels[2])
    ) / 255.0
    if selected:
        return "rgba(241, 248, 255, 244)" if luminance < 0.52 else "rgba(22, 33, 46, 242)"
    return "rgba(204, 221, 240, 180)" if luminance < 0.52 else "rgba(40, 56, 72, 170)"
