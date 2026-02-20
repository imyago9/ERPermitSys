from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QScrollArea


class EdgeLockedScrollArea(QScrollArea):
    """Prevents touchpad overscroll events from bubbling at scroll boundaries."""

    def wheelEvent(self, event) -> None:
        angle_x = 0
        angle_y = 0
        try:
            angle_delta = event.angleDelta()
            if angle_delta is not None:
                angle_x = int(angle_delta.x())
                angle_y = int(angle_delta.y())
        except Exception:
            angle_x = 0
            angle_y = 0

        pixel_x = 0
        pixel_y = 0
        try:
            pixel_delta = event.pixelDelta()
            if pixel_delta is not None:
                pixel_x = int(pixel_delta.x())
                pixel_y = int(pixel_delta.y())
        except Exception:
            pixel_x = 0
            pixel_y = 0

        # Keep wheel-mouse behavior native; only intercept touchpad-style gestures.
        is_touchpad = (pixel_x != 0 or pixel_y != 0)
        if not is_touchpad:
            try:
                is_touchpad = event.phase() != Qt.ScrollPhase.NoScrollPhase
            except Exception:
                is_touchpad = False
        if not is_touchpad:
            super().wheelEvent(event)
            return

        requested_vertical = (pixel_y != 0) or (angle_y != 0)
        requested_horizontal = (pixel_x != 0) or (angle_x != 0)

        vertical_bar = self.verticalScrollBar()
        horizontal_bar = self.horizontalScrollBar()

        v_can_scroll = (
            vertical_bar is not None and int(vertical_bar.maximum()) > int(vertical_bar.minimum())
        )
        h_can_scroll = (
            horizontal_bar is not None and int(horizontal_bar.maximum()) > int(horizontal_bar.minimum())
        )

        v_before = int(vertical_bar.value()) if vertical_bar is not None else 0
        h_before = int(horizontal_bar.value()) if horizontal_bar is not None else 0

        super().wheelEvent(event)

        v_after = int(vertical_bar.value()) if vertical_bar is not None else 0
        h_after = int(horizontal_bar.value()) if horizontal_bar is not None else 0
        moved = (v_after != v_before) or (h_after != h_before)
        if moved:
            return

        # If gesture asked to scroll an axis this area supports but nothing moved,
        # consume it so boundary overscroll doesn't bubble and repaint/flicker.
        if (requested_vertical and v_can_scroll) or (requested_horizontal and h_can_scroll):
            event.accept()
