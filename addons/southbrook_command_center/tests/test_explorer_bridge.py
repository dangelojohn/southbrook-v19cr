# SPDX-License-Identifier: LGPL-3.0-only
"""The bridge must never lie about where its numbers came from."""
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "southbrook_command_center")
class TestExplorerBridge(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Bridge = cls.env["southbrook.command.explorer_bridge"]

    def test_returns_payload_and_names_its_source(self):
        payload, provenance = self.Bridge.fetch()
        self.assertIsInstance(payload, dict)
        self.assertTrue(provenance, "provenance must never be empty")

    def test_degrades_when_explorer_raises(self):
        Explorer = self.env.get("mrp.process.explorer")
        if Explorer is None:
            self.skipTest("mrp_process_explorer not installed here")
        with patch.object(type(Explorer), "get_data",
                          side_effect=ValueError("boom")):
            payload, provenance = self.Bridge.fetch()
        self.assertEqual(payload, {})
        self.assertIn("fallback", provenance)

    def test_degrades_when_explorer_returns_not_ok(self):
        Explorer = self.env.get("mrp.process.explorer")
        if Explorer is None:
            self.skipTest("mrp_process_explorer not installed here")
        with patch.object(type(Explorer), "get_data",
                          return_value={"ok": False, "reason": "no routing"}):
            payload, provenance = self.Bridge.fetch()
        self.assertEqual(payload, {})
        self.assertIn("no routing", provenance)
