from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QCursor, QGuiApplication, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMainWindow, QToolButton, QVBoxLayout, QWidget

from coghud.ui.assets import icon_asset_path


class BackgroundFrame(QFrame):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._bg_pixmap: Optional[QPixmap] = None
        self._corner_radius = 14
        self._overlay = QColor(10, 12, 16, 168)
        self._border = QColor(56, 66, 60, 220)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

    def set_background_image(self, path: Optional[str]) -> None:
        if not path:
            self._bg_pixmap = None
            self.update()
            return
        pixmap = QPixmap(path)
        self._bg_pixmap = pixmap if not pixmap.isNull() else None
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self.rect()
        if rect.isNull():
            return

        inner_rect = rect.adjusted(1, 1, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(inner_rect, self._corner_radius, self._corner_radius)
        painter.setClipPath(path)

        gradient = QLinearGradient(0, 0, rect.width(), rect.height())
        gradient.setColorAt(0.0, QColor(12, 15, 19))
        gradient.setColorAt(1.0, QColor(26, 32, 36))
        painter.fillRect(rect, QBrush(gradient))

        if self._bg_pixmap and not self._bg_pixmap.isNull():
            scaled = self._bg_pixmap.scaled(
                rect.size(),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = (rect.width() - scaled.width()) // 2
            y = (rect.height() - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)
            painter.fillRect(rect, self._overlay)

        painter.setClipping(False)
        painter.setPen(QPen(self._border, 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(inner_rect, self._corner_radius, self._corner_radius)


class ApiTitleBar(QWidget):
    minimize_requested = Signal()
    maximize_restore_requested = Signal()
    close_requested = Signal()

    def __init__(self, parent: Optional[QWidget] = None, *, theme_mode: str = "dark") -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._theme_mode = theme_mode if theme_mode in ("light", "dark") else "dark"
        self._icon_label = QLabel(self)
        self._title_label = QLabel(self)
        self._min_button = QToolButton(self)
        self._max_button = QToolButton(self)
        self._close_button = QToolButton(self)
        self._min_icon = QIcon()
        self._expand_icon = QIcon()
        self._shrink_icon = QIcon()
        self._close_icon = QIcon()
        self._load_icons()
        self._configure_window_buttons()
        self.setMinimumHeight(36)
        self._icon_label.setFixedSize(18, 18)
        self._icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title_label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 8, 6)
        layout.setSpacing(8)
        layout.addWidget(self._icon_label)
        layout.addWidget(self._title_label)
        layout.addStretch(1)
        layout.addSpacing(2)
        layout.addWidget(self._min_button)
        layout.addWidget(self._max_button)
        layout.addWidget(self._close_button)

        self._min_button.clicked.connect(self.minimize_requested.emit)
        self._max_button.clicked.connect(self.maximize_restore_requested.emit)
        self._close_button.clicked.connect(self.close_requested.emit)
        self._drag_active = False
        self._double_click_enabled = True

    def set_theme_mode(self, mode: str) -> None:
        if mode not in ("light", "dark"):
            return
        if mode == self._theme_mode:
            return
        self._theme_mode = mode
        self._load_icons()
        self._configure_window_buttons()

    def set_title(self, title: str) -> None:
        self._title_label.setText(title)

    def set_double_click_enabled(self, enabled: bool) -> None:
        self._double_click_enabled = bool(enabled)

    def set_maximized(self, maximized: bool) -> None:
        icon = self._shrink_icon if maximized else self._expand_icon
        fallback = "<>" if maximized else "[]"
        self._apply_button_icon(self._max_button, icon, fallback)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._is_drag_target(event.position().toPoint()):
            self._drag_active = True
            self._dispatch_to_window("begin_titlebar_drag", event)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_active:
            self._dispatch_to_window("continue_titlebar_drag", event)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._drag_active:
            self._drag_active = False
            self._dispatch_to_window("end_titlebar_drag", event)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self._double_click_enabled
            and self._is_drag_target(event.position().toPoint())
        ):
            self.maximize_restore_requested.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def _is_drag_target(self, pos) -> bool:
        child = self.childAt(pos)
        if child is None:
            return True
        controls = (self._min_button, self._max_button, self._close_button)
        for control in controls:
            if child is control or control.isAncestorOf(child):
                return False
        return True

    def _dispatch_to_window(self, method_name: str, event) -> None:
        window = self.window()
        handler = getattr(window, method_name, None)
        if callable(handler):
            handler(event)

    def _configure_window_buttons(self) -> None:
        icon_size = QSize(11, 11)
        button_size = QSize(30, 24)
        for button in (self._min_button, self._max_button, self._close_button):
            button.setAutoRaise(True)
            button.setFixedSize(button_size)
            button.setIconSize(icon_size)

        self._apply_button_icon(self._min_button, self._min_icon, "-")
        self._apply_button_icon(self._close_button, self._close_icon, "x")
        self.set_maximized(False)

    def _load_icons(self) -> None:
        self._min_icon = QIcon(icon_asset_path("minimize_window.png", mode=self._theme_mode))
        self._expand_icon = QIcon(icon_asset_path("expand_icon.png", mode=self._theme_mode))
        self._shrink_icon = QIcon(icon_asset_path("shrink_icon.png", mode=self._theme_mode))
        self._close_icon = QIcon(icon_asset_path("close_window.png", mode=self._theme_mode))

    def _apply_button_icon(self, button: QToolButton, icon: QIcon, fallback_text: str) -> None:
        if icon.isNull():
            button.setIcon(QIcon())
            button.setText(fallback_text)
            return
        button.setText("")
        button.setIcon(icon)


class ResizeCornerHandle(QToolButton):
    def __init__(self, parent: Optional[QWidget] = None, *, theme_mode: str = "dark") -> None:
        super().__init__(parent)
        self._theme_mode = theme_mode if theme_mode in ("light", "dark") else "dark"
        self.setObjectName("ResizeHandle")
        self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        self.setAutoRaise(True)
        self.setFixedSize(18, 18)
        self._apply_icon()
        self._resize_active = False

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._resize_active = True
            self._dispatch_to_window("begin_corner_resize", event)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._resize_active:
            self._dispatch_to_window("continue_corner_resize", event)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._resize_active:
            self._resize_active = False
            self._dispatch_to_window("end_corner_resize", event)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _dispatch_to_window(self, method_name: str, event) -> None:
        window = self.window()
        handler = getattr(window, method_name, None)
        if callable(handler):
            handler(event)

    def set_theme_mode(self, mode: str) -> None:
        if mode not in ("light", "dark"):
            return
        if mode == self._theme_mode:
            return
        self._theme_mode = mode
        self._apply_icon()

    def _apply_icon(self) -> None:
        icon = QIcon(icon_asset_path("resize_handle.png", mode=self._theme_mode))
        if not icon.isNull():
            self.setText("")
            self.setIcon(icon)
            self.setIconSize(QSize(12, 12))
            return
        self.setIcon(QIcon())
        self.setText(">>")


class FramelessWindow(QMainWindow):
    def __init__(
        self,
        *,
        title: str = "",
        icon_path: Optional[str] = None,
        theme_mode: str = "dark",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._theme_mode = theme_mode if theme_mode in ("light", "dark") else "dark"
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowSystemMenuHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._manual_drag_active = False
        self._manual_drag_anchor: Optional[QPointF] = None
        self._manual_drag_origin: Optional[QPoint] = None
        self._corner_resize_active = False
        self._corner_resize_anchor: Optional[QPointF] = None
        self._corner_resize_origin: Optional[QSize] = None
        self._baseline_minimum_physical: Optional[QSize] = None
        self._applying_scaled_minimum = False
        self._drag_size_lock_active = False
        self._drag_locked_size: Optional[QSize] = None
        self._drag_locked_physical_size: Optional[QSize] = None
        self._drag_pointer_offset_physical: Optional[QPointF] = None
        self._lock_resize_guard = False
        self._screen_change_hooked = False
        self._window_icon = QIcon()

        self._frame = BackgroundFrame(self)
        self._frame.setObjectName("WindowContainer")

        self._title_bar = ApiTitleBar(self._frame, theme_mode=self._theme_mode)
        self._title_bar.setObjectName("TitleBar")
        self._title_bar.minimize_requested.connect(self.showMinimized)
        self._title_bar.maximize_restore_requested.connect(self._toggle_maximize_restore)
        self._title_bar.close_requested.connect(self.close)

        self._title_bar._min_button.setObjectName("TitleMinButton")
        self._title_bar._max_button.setObjectName("TitleMaxButton")
        self._title_bar._close_button.setObjectName("TitleCloseButton")
        self._title_bar._icon_label.setObjectName("TitleIcon")
        self._title_bar._title_label.setObjectName("TitleLabel")
        self._title_bar.minBtn = self._title_bar._min_button
        self._title_bar.maxBtn = self._title_bar._max_button
        self._title_bar.closeBtn = self._title_bar._close_button
        self._title_bar.iconLabel = self._title_bar._icon_label
        self._title_bar.titleLabel = self._title_bar._title_label
        self._title_bar.setTitle = self._title_bar.set_title
        self._title_bar.setIcon = self._set_title_icon
        self._title_bar.setDoubleClickEnabled = self._title_bar.set_double_click_enabled

        self.titleBar = self._title_bar
        self._body = QWidget(self._frame)
        self._body.setObjectName("WindowBody")
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(0, 0, 0, 0)
        self._body_layout.setSpacing(0)

        frame_layout = QVBoxLayout(self._frame)
        frame_layout.setContentsMargins(0, 0, 0, 0)
        frame_layout.setSpacing(0)
        frame_layout.addWidget(self._title_bar, 0)
        frame_layout.addWidget(self._body, 1)

        self.setCentralWidget(self._frame)
        self._resize_handle = ResizeCornerHandle(self._frame, theme_mode=self._theme_mode)
        self._position_resize_handle()
        self._sync_title_bar_window_state()

        if title:
            self.setWindowTitle(title)
        if icon_path:
            self.setWindowIcon(QIcon(icon_path))

    @property
    def title_bar(self) -> ApiTitleBar:
        return self._title_bar

    @property
    def body_layout(self) -> QVBoxLayout:
        return self._body_layout

    def set_background_image(self, path: Optional[str]) -> None:
        self._frame.set_background_image(path)

    def set_theme_mode(self, mode: str) -> None:
        if mode not in ("light", "dark"):
            return
        if mode == self._theme_mode:
            return
        self._theme_mode = mode
        self._title_bar.set_theme_mode(mode)
        self._resize_handle.set_theme_mode(mode)
        self._refresh_theme_styles()

    def refresh_mouse_handlers(self) -> None:
        return None

    def setWindowTitle(self, title: str) -> None:
        super().setWindowTitle(title)
        self._title_bar.set_title(title)

    def setWindowIcon(self, icon: QIcon) -> None:
        self._window_icon = QIcon(icon)
        super().setWindowIcon(self._window_icon)
        self._set_title_icon(self._window_icon)

    def setMinimumSize(self, *args) -> None:
        super().setMinimumSize(*args)
        if self._applying_scaled_minimum:
            return
        self._capture_baseline_minimum_physical()

    def begin_titlebar_drag(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self.isFullScreen():
            return

        global_pos = event.globalPosition()
        restored_for_drag = False
        if self.isMaximized():
            self._restore_from_maximized_for_drag(global_pos)
            restored_for_drag = True

        self._ensure_window_handle_hooks()
        self._capture_drag_pointer_offset(global_pos)
        self._start_drag_size_lock()

        if not self._should_force_manual_drag() and self._start_system_move():
            self._manual_drag_active = False
            self._manual_drag_anchor = None
            self._manual_drag_origin = None
            return
        if not self._allow_manual_move_fallback():
            self._end_drag_size_lock()
            if restored_for_drag:
                self.showMaximized()
            return

        self._manual_drag_active = True
        self._manual_drag_anchor = global_pos
        self._manual_drag_origin = self.frameGeometry().topLeft()

    def continue_titlebar_drag(self, event) -> None:
        if not self._manual_drag_active:
            return
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            self._stop_manual_drag()
            return
        if self._manual_drag_anchor is None or self._manual_drag_origin is None:
            self._stop_manual_drag()
            return

        delta = event.globalPosition() - self._manual_drag_anchor
        target = QPoint(
            int(self._manual_drag_origin.x() + delta.x()),
            int(self._manual_drag_origin.y() + delta.y()),
        )
        self.move(self._clamp_point_to_virtual_bounds(target))

    def end_titlebar_drag(self, _event) -> None:
        self._stop_manual_drag()
        QTimer.singleShot(0, self._end_drag_size_lock)

    def begin_corner_resize(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self.isFullScreen() or self.isMaximized():
            return
        self._corner_resize_active = True
        self._corner_resize_anchor = event.globalPosition()
        self._corner_resize_origin = QSize(max(1, self.width()), max(1, self.height()))

    def continue_corner_resize(self, event) -> None:
        if not self._corner_resize_active:
            return
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            self.end_corner_resize(event)
            return
        if self._corner_resize_anchor is None or self._corner_resize_origin is None:
            self.end_corner_resize(event)
            return
        delta = event.globalPosition() - self._corner_resize_anchor
        width = max(self.minimumWidth(), int(round(self._corner_resize_origin.width() + delta.x())))
        height = max(self.minimumHeight(), int(round(self._corner_resize_origin.height() + delta.y())))
        self.resize(width, height)

    def end_corner_resize(self, _event) -> None:
        self._corner_resize_active = False
        self._corner_resize_anchor = None
        self._corner_resize_origin = None

    def _toggle_maximize_restore(self) -> None:
        self._stop_manual_drag()
        self._end_drag_size_lock()
        if self.isFullScreen():
            return
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()
        self._sync_title_bar_window_state()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self._sync_title_bar_window_state()

    def _set_title_icon(self, icon: QIcon) -> None:
        if icon is None or icon.isNull():
            self._title_bar._icon_label.clear()
            return
        pixmap = icon.pixmap(QSize(16, 16))
        if not pixmap.isNull():
            self._title_bar._icon_label.setPixmap(pixmap)
            self._title_bar._icon_label.setText("")

    def _sync_title_bar_window_state(self) -> None:
        self._title_bar.set_maximized(self.isMaximized())

    def _refresh_theme_styles(self) -> None:
        widgets = (self._frame, self._title_bar)
        for widget in widgets:
            style = widget.style()
            if style is None:
                continue
            style.unpolish(widget)
            style.polish(widget)
            widget.update()

    def _restore_from_maximized_for_drag(self, global_pos: QPointF) -> None:
        normal = self.normalGeometry()
        if not normal.isValid() or normal.width() <= 0 or normal.height() <= 0:
            normal = QRect(QPoint(0, 0), self._fallback_restore_size(global_pos.toPoint()))

        frame = self.frameGeometry()
        if frame.width() > 0:
            ratio = (global_pos.x() - frame.x()) / frame.width()
        else:
            ratio = 0.5
        ratio = max(0.0, min(1.0, ratio))

        self.showNormal()

        width = max(self.minimumWidth(), normal.width())
        height = max(self.minimumHeight(), normal.height())
        self.resize(width, height)

        drag_offset_y = max(8, min(int(global_pos.y() - frame.y()), self._title_bar.height() or 32))
        target = QPoint(
            int(round(global_pos.x() - (ratio * width))),
            int(round(global_pos.y() - drag_offset_y)),
        )
        self.move(self._clamp_point_to_active_screen(target, global_pos.toPoint()))

    def _fallback_restore_size(self, cursor_pos: QPoint) -> QSize:
        screen = QGuiApplication.screenAt(cursor_pos) or self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            return QSize(max(900, self.minimumWidth()), max(620, self.minimumHeight()))
        area = screen.availableGeometry()
        width = max(self.minimumWidth(), int(area.width() * 0.78))
        height = max(self.minimumHeight(), int(area.height() * 0.82))
        return QSize(width, height)

    def _start_system_move(self) -> bool:
        handle = self.windowHandle()
        if handle is None:
            return False
        try:
            moved = handle.startSystemMove()
        except Exception:
            return False
        if moved is False:
            return False
        return True

    def _allow_manual_move_fallback(self) -> bool:
        platform = (QGuiApplication.platformName() or "").lower()
        return "wayland" not in platform

    def _should_force_manual_drag(self) -> bool:
        platform = (QGuiApplication.platformName() or "").lower()
        if "windows" not in platform:
            return False
        return self._has_mixed_dpi_screens()

    def _has_mixed_dpi_screens(self) -> bool:
        screens = QGuiApplication.screens()
        if len(screens) < 2:
            return False
        dprs: set[float] = set()
        for screen in screens:
            try:
                dpr = float(screen.devicePixelRatio())
            except Exception:
                continue
            dprs.add(round(dpr, 3))
            if len(dprs) > 1:
                return True
        return False

    def _stop_manual_drag(self) -> None:
        self._manual_drag_active = False
        self._manual_drag_anchor = None
        self._manual_drag_origin = None

    def _start_drag_size_lock(self) -> None:
        if self.isFullScreen() or self.isMaximized():
            return
        self._drag_size_lock_active = True
        self._drag_locked_size = QSize(max(1, self.width()), max(1, self.height()))
        dpr = self._effective_dpr()
        self._drag_locked_physical_size = QSize(
            max(1, int(round(self._drag_locked_size.width() * dpr))),
            max(1, int(round(self._drag_locked_size.height() * dpr))),
        )

    def _end_drag_size_lock(self) -> None:
        self._drag_size_lock_active = False
        self._drag_locked_size = None
        self._drag_locked_physical_size = None
        self._drag_pointer_offset_physical = None

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._position_resize_handle()
        self._enforce_drag_size_lock()

    def _position_resize_handle(self) -> None:
        if not hasattr(self, "_resize_handle"):
            return
        if self.isFullScreen() or self.isMaximized():
            self._resize_handle.hide()
            return
        self._resize_handle.show()
        margin = 4
        x = max(0, self._frame.width() - self._resize_handle.width() - margin)
        y = max(0, self._frame.height() - self._resize_handle.height() - margin)
        self._resize_handle.move(x, y)
        self._resize_handle.raise_()

    def _effective_dpr(self) -> float:
        handle = self.windowHandle()
        if handle is not None:
            try:
                screen = handle.screen()
                if screen is not None:
                    dpr = float(screen.devicePixelRatio())
                    if dpr > 0:
                        return dpr
            except Exception:
                pass
            try:
                dpr = float(handle.devicePixelRatio())
                if dpr > 0:
                    return dpr
            except Exception:
                pass
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is not None:
            try:
                dpr = float(screen.devicePixelRatio())
                if dpr > 0:
                    return dpr
            except Exception:
                pass
        return 1.0

    def _capture_baseline_minimum_physical(self) -> None:
        min_width = max(1, self.minimumWidth())
        min_height = max(1, self.minimumHeight())
        dpr = self._effective_dpr()
        self._baseline_minimum_physical = QSize(
            max(1, int(round(min_width * dpr))),
            max(1, int(round(min_height * dpr))),
        )

    def _apply_scaled_minimum_for_current_dpr(self) -> None:
        baseline = self._baseline_minimum_physical
        if baseline is None or not baseline.isValid():
            return
        dpr = self._effective_dpr()
        target = QSize(
            max(1, int(round(baseline.width() / dpr))),
            max(1, int(round(baseline.height() / dpr))),
        )
        current = QSize(max(1, self.minimumWidth()), max(1, self.minimumHeight()))
        if current == target:
            return
        self._applying_scaled_minimum = True
        try:
            super().setMinimumSize(target)
        finally:
            self._applying_scaled_minimum = False

    def _ensure_window_handle_hooks(self) -> None:
        if self._screen_change_hooked:
            return
        handle = self.windowHandle()
        if handle is None:
            return
        try:
            handle.screenChanged.connect(self._on_screen_changed)
        except Exception:
            return
        self._screen_change_hooked = True

    def _on_screen_changed(self, _screen) -> None:
        self._apply_scaled_minimum_for_current_dpr()
        if not self._drag_size_lock_active:
            return
        QTimer.singleShot(0, self._reconcile_drag_geometry)

    def _capture_drag_pointer_offset(self, global_pos: QPointF) -> None:
        if self.isFullScreen():
            self._drag_pointer_offset_physical = None
            return
        frame = self.frameGeometry()
        offset_x = max(0.0, min(global_pos.x() - frame.x(), float(frame.width())))
        offset_y = max(0.0, min(global_pos.y() - frame.y(), float(frame.height())))
        dpr = self._effective_dpr()
        self._drag_pointer_offset_physical = QPointF(offset_x * dpr, offset_y * dpr)

    def _reconcile_drag_geometry(self) -> None:
        if not self._drag_size_lock_active:
            return
        if self.isFullScreen() or self.isMaximized():
            return
        if self._lock_resize_guard:
            return
        self._enforce_drag_size_lock()
        dpr = self._effective_dpr()

        cursor_pos = QCursor.pos()
        pointer_offset = self._drag_pointer_offset_physical
        if pointer_offset is not None:
            offset_x = pointer_offset.x() / dpr
            offset_y = pointer_offset.y() / dpr
        else:
            frame = self.frameGeometry()
            offset_x = max(0.0, min(cursor_pos.x() - frame.x(), float(frame.width())))
            offset_y = max(0.0, min(cursor_pos.y() - frame.y(), float(frame.height())))

        target_pos = QPoint(
            int(round(cursor_pos.x() - offset_x)),
            int(round(cursor_pos.y() - offset_y)),
        )

        self._lock_resize_guard = True
        try:
            clamped_pos = self._clamp_point_to_active_screen(target_pos, cursor_pos)
            if self.frameGeometry().topLeft() != clamped_pos:
                self.move(clamped_pos)
        finally:
            self._lock_resize_guard = False

        if self._manual_drag_active and self._manual_drag_anchor is not None:
            delta = QPointF(cursor_pos) - self._manual_drag_anchor
            current_top_left = self.frameGeometry().topLeft()
            self._manual_drag_origin = QPoint(
                int(round(current_top_left.x() - delta.x())),
                int(round(current_top_left.y() - delta.y())),
            )

    def _enforce_drag_size_lock(self) -> None:
        if not self._drag_size_lock_active:
            return
        if self.isFullScreen() or self.isMaximized():
            return
        if self._lock_resize_guard:
            return

        locked_physical = self._drag_locked_physical_size
        locked_logical = self._drag_locked_size
        if (locked_physical is None or not locked_physical.isValid()) and (
            locked_logical is None or not locked_logical.isValid()
        ):
            return

        dpr = self._effective_dpr()
        if locked_physical is not None and locked_physical.isValid():
            target_w = max(1, int(round(locked_physical.width() / dpr)))
            target_h = max(1, int(round(locked_physical.height() / dpr)))
        else:
            target_w = max(1, locked_logical.width())
            target_h = max(1, locked_logical.height())

        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            target_w = min(target_w, max(1, available.width()))
            target_h = min(target_h, max(1, available.height()))

        target_w = max(self.minimumWidth(), target_w)
        target_h = max(self.minimumHeight(), target_h)

        # Ignore sub-pixel/dpr rounding noise to reduce resize thrash flicker.
        if abs(self.width() - target_w) <= 1 and abs(self.height() - target_h) <= 1:
            return

        self._lock_resize_guard = True
        try:
            self.resize(target_w, target_h)
        finally:
            self._lock_resize_guard = False

    def _clamp_point_to_active_screen(self, target: QPoint, cursor_pos: QPoint) -> QPoint:
        screen = QGuiApplication.screenAt(cursor_pos) or self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            return target
        return self._clamp_point(target, screen.availableGeometry())

    def _clamp_point_to_virtual_bounds(self, target: QPoint) -> QPoint:
        screens = QGuiApplication.screens()
        if not screens:
            return target
        bounds = screens[0].availableGeometry()
        for screen in screens[1:]:
            bounds = bounds.united(screen.availableGeometry())
        return self._clamp_point(target, bounds)

    def _clamp_point(self, target: QPoint, bounds: QRect) -> QPoint:
        frame = self.frameGeometry()
        width = max(1, frame.width())
        title_height = max(28, self._title_bar.height() or 28)
        min_visible_x = min(140, max(64, width // 3))

        min_x = bounds.left() - width + min_visible_x
        max_x = bounds.right() - min_visible_x + 1
        min_y = bounds.top()
        max_y = bounds.bottom() - title_height + 1

        x = max(min_x, min(target.x(), max_x))
        y = max(min_y, min(target.y(), max_y))
        return QPoint(x, y)
