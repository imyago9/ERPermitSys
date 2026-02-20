from __future__ import annotations

from erpermitsys.app.window_admin_data_mixin import WindowAdminDataMixin
from erpermitsys.app.window_admin_layout_mixin import WindowAdminLayoutMixin


class WindowAdminMixin(
    WindowAdminLayoutMixin,
    WindowAdminDataMixin,
):
    """Compatibility facade that groups all admin panel mixins."""

