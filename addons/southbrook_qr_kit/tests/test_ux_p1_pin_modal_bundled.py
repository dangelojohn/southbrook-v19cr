# SPDX-License-Identifier: LGPL-3.0-only
"""UX-P1 (2026-07-31, ux-shopfloor.md #1) — operator PIN modal bundling.

Regression guard for the audit's #1 finding: `operator_pin_modal.js`
and `.scss` were fully built (56px touch targets, verified working
backend endpoints) but referenced in NO asset bundle, so the file
never shipped and every floor scan was attributed anonymously. This
pins down the *behaviour* of the fix, not its prose:

  1. The manifest's `assets` dict actually lists both files in
     `web.assets_qr_floor` — the ONLY bundle the floor shell
     (`views/floor_shell_template.xml`) loads via `t-call-assets`.
     Being present in `assets_backend`/`assets_frontend` alone is NOT
     sufficient — that was the misleading half of the original bug:
     the manifest's own inline comment already *claimed* backend+
     frontend bundling before this fix, while the assets dict carried
     the files in neither bundle, let alone the floor one.
  2. Every asset path this addon lists in ANY bundle actually exists
     on disk. This is the real regression guard the task asked for:
     the original bug wasn't a missing string, it was a build artifact
     with zero bundle references at all. A future typo/rename in the
     manifest must fail this test loudly rather than ship a silently
     dead asset again.
"""
import ast
import os

from odoo.modules import get_module_path
from odoo.tests.common import TransactionCase, tagged

_QR_KIT_ROOT = get_module_path("southbrook_qr_kit")


def _load_manifest():
    manifest_path = os.path.join(_QR_KIT_ROOT, "__manifest__.py")
    with open(manifest_path, "r", encoding="utf-8") as fh:
        src = fh.read()
    # The manifest is a single dict literal (comments only) — safe and
    # exact to parse with ast.literal_eval rather than importing/exec'ing
    # the file or regexing for substrings.
    return ast.literal_eval(src)


@tagged("post_install", "-at_install", "southbrook", "qr", "ux_p1")
class TestUxP1PinModalBundled(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.manifest = _load_manifest()

    def test_10_pin_modal_js_and_scss_in_qr_floor_bundle(self):
        bundle = self.manifest["assets"]["web.assets_qr_floor"]
        self.assertIn(
            "southbrook_qr_kit/static/src/js/operator_pin_modal.js", bundle,
            "PIN modal JS must be in web.assets_qr_floor — it is the "
            "ONLY bundle the floor shell (floor_shell_template.xml) "
            "loads; being in backend/frontend alone leaves the actual "
            "Bin-Scan/Load-Unit tablets without the modal.")
        self.assertIn(
            "southbrook_qr_kit/static/src/scss/operator_pin_modal.scss",
            bundle,
            "PIN modal SCSS must be in web.assets_qr_floor for the "
            "same reason as the JS above.")

    def test_11_pin_modal_also_in_backend_and_frontend(self):
        for bundle_name in ("web.assets_backend", "web.assets_frontend"):
            bundle = self.manifest["assets"][bundle_name]
            self.assertIn(
                "southbrook_qr_kit/static/src/js/operator_pin_modal.js",
                bundle, f"{bundle_name} missing PIN modal JS")
            self.assertIn(
                "southbrook_qr_kit/static/src/scss/operator_pin_modal.scss",
                bundle, f"{bundle_name} missing PIN modal SCSS")

    def test_20_every_listed_asset_path_exists_on_disk(self):
        """The load-bearing regression guard: walk every asset path
        this addon contributes to every bundle and confirm the file
        is actually on disk. The original bug would NOT have been
        caught by a test that only greps the manifest text for the
        filename — the filename was never missing, it just wasn't
        wired into any `assets` list. This asserts the reverse
        direction too: everything the manifest claims to ship, ships."""
        missing = []
        for bundle_name, paths in self.manifest["assets"].items():
            for path in paths:
                if not path.startswith("southbrook_qr_kit/"):
                    continue  # contributed by another addon (n/a here)
                rel = path[len("southbrook_qr_kit/"):]
                full = os.path.join(_QR_KIT_ROOT, rel)
                if not os.path.isfile(full):
                    missing.append("%s: %s" % (bundle_name, path))
        self.assertFalse(
            missing,
            "manifest lists asset path(s) that do not exist on disk "
            "(this is exactly the class of bug this test exists to "
            "catch): %s" % missing)

    def test_21_operator_pin_modal_js_is_a_self_booting_module(self):
        """The modal has no explicit mount call from floor_screens.esm.js
        — it is a self-invoking IIFE marked `@odoo-module` that boots
        itself on DOMContentLoaded. Confirm both properties hold, since
        Task 1's "wire the entry point too" instruction only applies if
        an explicit call is required; if this ever stops being
        self-booting, the bundling fix alone would no longer be enough."""
        js_path = os.path.join(
            _QR_KIT_ROOT, "static/src/js/operator_pin_modal.js")
        with open(js_path, "r", encoding="utf-8") as fh:
            content = fh.read()
        self.assertIn("@odoo-module", content)
        self.assertIn("DOMContentLoaded", content)
        floor_js_path = os.path.join(
            _QR_KIT_ROOT, "static/src/js/floor_screens.esm.js")
        with open(floor_js_path, "r", encoding="utf-8") as fh:
            floor_content = fh.read()
        self.assertNotIn(
            "operator_pin_modal", floor_content,
            "operator_pin_modal.js self-boots; floor_screens.esm.js "
            "must not need to import/call it. If this starts failing "
            "because the modal became an explicit import, that's fine "
            "— just confirms the wiring is real rather than assuming "
            "self-boot forever.")

    def test_22_pin_modal_auto_prompts_on_the_floor_shell_routes(self):
        """UX-P1 companion bug: the auto-prompt path-prefix check used
        to allow /odoo, /web, /sb/ but NOT /southbrook/floor/ — the
        actual Bin-Scan/Load-Unit route prefix — so even a correctly
        bundled modal would only ever appear via a manual badge tap on
        the primary floor screens, never the auto-prompt the feature
        exists for."""
        js_path = os.path.join(
            _QR_KIT_ROOT, "static/src/js/operator_pin_modal.js")
        with open(js_path, "r", encoding="utf-8") as fh:
            content = fh.read()
        self.assertIn('path.startsWith("/southbrook/floor/")', content)
