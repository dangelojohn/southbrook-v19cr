# SPDX-License-Identifier: LGPL-3.0-only
"""Touch editing v1 (2026-08-02 spec §4 item 2) — per-surface kill switch.

Two ir.config_parameter keys (TOUCH_ENABLED_PARAM_BACKEND / _PORTAL on
southbrook.kitchen.design, kitchen_design.py) gate touch independently
per surface, modeled on southbrook_qr_kit/controllers/qr_scan.py:
906-925's `pin_modal_enabled`. This pins:

  1. Default is enabled ("1") when the parameter row doesn't exist —
     the spec's explicit "default '1'" ask.
  2. Each surface's key is read independently — flipping backend never
     touches portal and vice versa.
  3. The full "0/false/no/off" falsy-string vocabulary is honoured
     (matching qr_scan.py's own vocabulary), not just literal "0".
  4. The backend configurator's one unconditional boot RPC
     (/southbrook_kitchen/configurator/products, called from
     kitchen_configurator.js's onWillStart regardless of new-design or
     existing-design branch) actually carries the flag end to end.
"""
from contextlib import contextmanager
from unittest.mock import MagicMock

from odoo.tests import TransactionCase, tagged

from odoo.addons.southbrook_kitchen_3d_configurator.controllers import (
    main as ctrl_main,
)
from odoo.addons.southbrook_kitchen_3d_configurator.models.kitchen_design import (
    TOUCH_ENABLED_PARAM_BACKEND,
    TOUCH_ENABLED_PARAM_PORTAL,
)


@contextmanager
def stubbed_request(env):
    saved = ctrl_main.request
    mock = MagicMock()
    mock.env = env
    mock.session = {}
    mock.params = {}
    ctrl_main.request = mock
    try:
        yield mock
    finally:
        ctrl_main.request = saved


@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator", "touch_editing")
class TestTouchKillSwitch(TransactionCase):

    def _set_param(self, key, value):
        self.env["ir.config_parameter"].sudo().set_param(key, value)

    # ------------------------------------------------------------------
    def test_default_is_enabled_when_param_row_absent(self):
        """No row at all (fresh DB) -> both surfaces default ON."""
        Design = self.env["southbrook.kitchen.design"]
        self.assertTrue(Design._sb_touch_enabled("backend"))
        self.assertTrue(Design._sb_touch_enabled("portal"))

    def test_backend_disable_does_not_affect_portal(self):
        Design = self.env["southbrook.kitchen.design"]
        self._set_param(TOUCH_ENABLED_PARAM_BACKEND, "0")
        self.assertFalse(Design._sb_touch_enabled("backend"))
        self.assertTrue(Design._sb_touch_enabled("portal"),
                        "portal key must be independent of backend's")

    def test_portal_disable_does_not_affect_backend(self):
        Design = self.env["southbrook.kitchen.design"]
        self._set_param(TOUCH_ENABLED_PARAM_PORTAL, "0")
        self.assertFalse(Design._sb_touch_enabled("portal"))
        self.assertTrue(Design._sb_touch_enabled("backend"),
                        "backend key must be independent of portal's")

    def test_falsy_vocabulary(self):
        Design = self.env["southbrook.kitchen.design"]
        for falsy in ("0", "false", "False", "no", "NO", "off", "OFF"):
            self._set_param(TOUCH_ENABLED_PARAM_BACKEND, falsy)
            self.assertFalse(
                Design._sb_touch_enabled("backend"),
                f"{falsy!r} must be read as disabled",
            )

    def test_whitespace_only_stored_value_is_falsy(self):
        """`set_param(key, "")` deletes the row entirely (Odoo's own
        set_param semantics — an empty value is not the same as a
        stored empty string), so the "" branch of the falsy vocabulary
        can't be exercised through set_param with a literal "" at all:
        it would just fall back to the absent-row default
        ('1'/enabled), silently passing for the wrong reason. A
        whitespace-only value ("   ") is NOT falsy to set_param (it's
        a non-empty string, so the row is genuinely written and its
        ormcache genuinely invalidated) but IS falsy to
        `_sb_touch_enabled`'s own `.strip()` — the defense-in-depth
        case where a UI field gets cleared to blank rather than the
        row being deleted."""
        Design = self.env["southbrook.kitchen.design"]
        self._set_param(TOUCH_ENABLED_PARAM_BACKEND, "   ")
        self.assertFalse(Design._sb_touch_enabled("backend"))

    def test_truthy_values_stay_enabled(self):
        Design = self.env["southbrook.kitchen.design"]
        for truthy in ("1", "true", "yes", "on", "anything-else"):
            self._set_param(TOUCH_ENABLED_PARAM_BACKEND, truthy)
            self.assertTrue(
                Design._sb_touch_enabled("backend"),
                f"{truthy!r} must be read as enabled",
            )

    def test_unknown_surface_falls_back_to_backend(self):
        Design = self.env["southbrook.kitchen.design"]
        self._set_param(TOUCH_ENABLED_PARAM_BACKEND, "0")
        self.assertFalse(Design._sb_touch_enabled("bogus-surface"))

    # ------------------------------------------------------------------
    def test_products_endpoint_carries_touch_enabled_flag(self):
        """The backend's unconditional boot RPC includes touch_enabled,
        mirroring the live ICP value."""
        controller = ctrl_main.SouthbrookKitchenConfiguratorController()
        with stubbed_request(self.env):
            resp = controller.products()
        self.assertIn("touch_enabled", resp)
        self.assertTrue(resp["touch_enabled"])

        self._set_param(TOUCH_ENABLED_PARAM_BACKEND, "0")
        with stubbed_request(self.env):
            resp = controller.products()
        self.assertFalse(resp["touch_enabled"])
        # Existing keys must still be present — additive, not a
        # replacement of the D3 channel/products contract.
        self.assertIn("channel", resp)
        self.assertIn("products", resp)
