# SPDX-License-Identifier: LGPL-3.0-only
"""UX-P1 (2026-07-31, ux-shopfloor.md #2) — Floor Traveler scan gets a
real human-facing response.

Before this change, `southbrook_floor_traveler`'s only shop-floor
touchpoint was a printed QR hitting a JSON-only endpoint: no HTML, no
toast, nothing. The audit called this the WORST finding across the
shop-floor surfaces. This suite pins the FIX's behaviour, not its
prose:

  * The GET route (type="http") renders an HTML page; the POST route
    (type="jsonrpc") keeps returning JSON — same URL, both contracts
    alive side by side.
  * Every failure case (forbidden / invalid_payload / not_found /
    wrong_step / expired) resolves to its OWN distinct outcome, not a
    generic catch-all — and the pre-existing JSON contract (tested by
    test_p8_floor_traveler.py's TestP8ScanEndpointAuth) is unbroken.
  * The wrong-station guard actually blocks the finish action (this
    is a genuine behaviour fix: the endpoint used to finish whatever
    workorder was next regardless of which station scanned it).
  * The rendered HTML page never leaks a raw traceback/JSON blob and
    escapes user-controlled text (package/job names are free-text
    fields).
"""
import ast
import json
import os
import time
from contextlib import contextmanager
from unittest.mock import patch

from odoo.modules import get_module_path
from odoo.tests.common import HttpCase, TransactionCase, tagged

from odoo.addons.southbrook_floor_traveler.controllers import (
    scan_endpoint as scan_endpoint_mod,
)


class _FakeHttpRequest:
    def __init__(self, ua="pytest-uxp1", ip="127.0.0.1"):
        self.remote_addr = ip
        self.headers = {"User-Agent": ua}


class _FakeRequest:
    """Stub of `odoo.http.request` carrying only the surface
    `_log_unified` touches (env + httprequest). Mirrors the pattern
    already used by southbrook_qr_kit/tests/test_w035_pin_operator.py
    for exactly the same reason: exercise controller Python methods
    directly without spinning up a real HTTP thread for every case."""

    def __init__(self, env):
        self.env = env
        self.httprequest = _FakeHttpRequest()
        self.uid = env.uid


@contextmanager
def _patched_request(fake):
    with patch.object(scan_endpoint_mod, "request", fake):
        yield


@tagged("post_install", "-at_install", "southbrook", "floor_traveler",
        "ux_p1")
class TestUxP1ScanOutcomes(TransactionCase):
    """Direct-call tests against `_resolve_scan` / `_outcome_to_json` /
    `_render_scan_result_html` — no HTTP layer, so no risk of a
    cross-thread mock-patching flake for the harder-to-set-up outcomes
    (wrong_step needs button_finish to NOT fire, expired needs a
    monkeypatched clock)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Same bypass test_p8_floor_traveler.py uses: these packages
        # are built from a bare BoM/MO with no staged stock, which the
        # premium_orchestration availability gate would otherwise
        # reject on action_confirm().
        cls.env["ir.config_parameter"].sudo().set_param(
            "southbrook.mo_availability_gate.enabled", "0")
        cls.Package = cls.env["sb.production.package"]
        cls.MO = cls.env["mrp.production"]
        cls.Cutlist = cls.env["sb.cutlist"]
        cls.Hardware = cls.env["sb.hardware.package"]
        cls.Product = cls.env["product.product"]
        cls.Controller = scan_endpoint_mod.FloorTravelerScanAPI()
        cls.mrp_user = cls.env["res.users"].create({
            "name": "UXP1 MRP Operator",
            "login": "uxp1_mrp_operator",
            "group_ids": [(6, 0, [
                cls.env.ref("base.group_user").id,
                cls.env.ref("mrp.group_mrp_user").id,
            ])],
        })
        cls.plain_user = cls.env["res.users"].create({
            "name": "UXP1 Plain User",
            "login": "uxp1_plain_user",
            "group_ids": [(6, 0, [cls.env.ref("base.group_user").id])],
        })

    def _fake(self, user=None):
        return _FakeRequest(self.env(user=user or self.mrp_user))

    def _make_package(self):
        product = self.Product.create({
            "name": "UXP1 test product",
            "type": "consu", "is_storable": True,
        })
        mo = self.MO.create({"product_id": product.id, "product_qty": 1.0})
        cutlist = self.Cutlist.create({"mo_id": mo.id})
        hardware = self.Hardware.create({"mo_id": mo.id})
        return self.Package.create({
            "mo_id": mo.id,
            "cutlist_id": cutlist.id,
            "hardware_package_id": hardware.id,
            "state": "ready",
        })

    def _make_package_with_workorder(self, wc_code="UXP1-SAW",
                                     wc_name="UXP1 Panel Saw"):
        wc = self.env["mrp.workcenter"].create({
            "name": wc_name, "code": wc_code,
        })
        pkg = self._make_package()
        wo = self.env["mrp.workorder"].create({
            "name": "UXP1 test wo",
            "workcenter_id": wc.id,
            "production_id": pkg.mo_id.id,
            "product_uom_id": pkg.mo_id.product_uom_id.id,
        })
        wo.state = "progress"
        return pkg, wo, wc

    # ------------------------------------------------------------------
    # Each failure case is its own outcome, not a generic catch-all
    # ------------------------------------------------------------------
    def test_10_forbidden_is_its_own_outcome(self):
        fake = self._fake(user=self.plain_user)
        with _patched_request(fake):
            outcome = self.Controller._resolve_scan(
                fake.env, "sb-package:1", None)
        self.assertEqual(outcome["outcome"], "forbidden")
        self.assertFalse(outcome["ok"])

    def test_11_malformed_payload_is_invalid_payload(self):
        fake = self._fake()
        with _patched_request(fake):
            outcome = self.Controller._resolve_scan(
                fake.env, "not-a-real-payload", None)
        self.assertEqual(outcome["outcome"], "invalid_payload")
        self.assertNotEqual(outcome["outcome"], "not_found",
                            "malformed code must not be confused with "
                            "a well-formed-but-missing record")

    def test_12_empty_payload_is_invalid_payload(self):
        fake = self._fake()
        with _patched_request(fake):
            outcome = self.Controller._resolve_scan(fake.env, "", None)
        self.assertEqual(outcome["outcome"], "invalid_payload")

    def test_13_missing_record_is_not_found_not_invalid(self):
        fake = self._fake()
        with _patched_request(fake):
            outcome = self.Controller._resolve_scan(
                fake.env, "sb-package:999999999", None)
        self.assertEqual(outcome["outcome"], "not_found")
        self.assertNotEqual(outcome["outcome"], "invalid_payload",
                            "well-formed code for a missing record must "
                            "not collapse into the malformed-code bucket")

    def test_14_wrong_station_blocks_and_never_calls_finish(self):
        """Load-bearing behaviour fix: before this, the endpoint
        finished whatever workorder was next regardless of which
        station scanned it. Now a station mismatch is a hard stop —
        button_finish must never fire, and no scan event is appended."""
        pkg, wo, wc = self._make_package_with_workorder(wc_code="UXP1-SAW")
        fake = self._fake()
        with _patched_request(fake), \
                patch.object(type(wo), "button_finish", autospec=True,
                             return_value=True) as finish_mock:
            outcome = self.Controller._resolve_scan(
                fake.env, "sb-package:%s" % pkg.id, "WRONG-STATION")
        self.assertEqual(outcome["outcome"], "wrong_step")
        self.assertFalse(outcome["ok"])
        self.assertEqual(
            outcome["extra"]["expected_workcenter"], wc.display_name)
        finish_mock.assert_not_called()
        log = json.loads(pkg.x_scan_log_json or "[]")
        self.assertEqual(
            log, [], "the guard must fire BEFORE record_scan() — no "
            "scan event should be appended for a wrong-station scan")

    def test_15_matching_station_succeeds(self):
        pkg, wo, wc = self._make_package_with_workorder(wc_code="UXP1-SAW")
        fake = self._fake()
        with _patched_request(fake), \
                patch.object(type(wo), "button_finish", autospec=True,
                             return_value=True) as finish_mock:
            outcome = self.Controller._resolve_scan(
                fake.env, "sb-package:%s" % pkg.id, "UXP1-SAW")
        self.assertEqual(outcome["outcome"], "ok")
        self.assertTrue(outcome["ok"])
        finish_mock.assert_called_once()

    def test_16_station_match_is_case_insensitive_by_name_or_code(self):
        pkg, wo, wc = self._make_package_with_workorder(
            wc_code="UXP1-SAW", wc_name="UXP1 Panel Saw")
        fake = self._fake()
        with _patched_request(fake), \
                patch.object(type(wo), "button_finish", autospec=True,
                             return_value=True):
            outcome = self.Controller._resolve_scan(
                fake.env, "sb-package:%s" % pkg.id, "uxp1 panel saw")
        self.assertEqual(outcome["outcome"], "ok",
                         "matching by workcenter NAME (case-insensitive) "
                         "must also be accepted, not just by code")

    def test_17_no_declared_station_keeps_legacy_permissive_behaviour(self):
        """A caller that never sends workcenter_code (legacy scanners,
        and every existing test in test_p8_floor_traveler.py) must
        keep working exactly as before — the new guard only engages
        when a station IS declared."""
        pkg, wo, wc = self._make_package_with_workorder(wc_code="UXP1-SAW")
        fake = self._fake()
        with _patched_request(fake), \
                patch.object(type(wo), "button_finish", autospec=True,
                             return_value=True) as finish_mock:
            outcome = self.Controller._resolve_scan(
                fake.env, "sb-package:%s" % pkg.id, None)
        self.assertEqual(outcome["outcome"], "ok")
        finish_mock.assert_called_once()

    def test_18_already_done_is_distinct_from_ok_and_stays_ok_true(self):
        """No workorder to advance at all (never had one) — this is
        informational, not a rejection: mirrors the pre-existing JSON
        contract where this case already returned ok:true, wo_id:null."""
        pkg = self._make_package()
        fake = self._fake()
        with _patched_request(fake):
            outcome = self.Controller._resolve_scan(
                fake.env, "sb-package:%s" % pkg.id, None)
        self.assertEqual(outcome["outcome"], "already_done")
        self.assertTrue(outcome["ok"])
        self.assertNotEqual(outcome["outcome"], "ok",
                            "'nothing to advance' must be visibly "
                            "different from 'this scan just finished "
                            "a step', even though both are ok:true")

    def test_19_expired_signed_payload_is_distinct_outcome(self):
        if "southbrook.qr.payload" not in self.env:
            self.skipTest("southbrook_qr_kit not installed in this DB")
        from odoo.addons.southbrook_qr_kit.models import (
            qr_payload as qr_payload_mod,
        )
        pkg = self._make_package()
        self.env["ir.config_parameter"].sudo().set_param(
            "southbrook_floor_traveler.scan_qr_ttl_hours", "1")
        old_ts = int(time.time()) - (3600 * 24 * 40)  # 40 days ago
        Payload = self.env["southbrook.qr.payload"]
        with patch.object(qr_payload_mod.time, "time", return_value=old_ts):
            signed = Payload.build("pkg", pkg.id)
        fake = self._fake()
        with _patched_request(fake):
            outcome = self.Controller._resolve_scan(fake.env, signed, None)
        self.assertEqual(outcome["outcome"], "expired")
        self.assertFalse(outcome["ok"])

    def test_20_fresh_signed_payload_within_ttl_is_not_expired(self):
        if "southbrook.qr.payload" not in self.env:
            self.skipTest("southbrook_qr_kit not installed in this DB")
        pkg = self._make_package()
        self.env["ir.config_parameter"].sudo().set_param(
            "southbrook_floor_traveler.scan_qr_ttl_hours", "720")
        Payload = self.env["southbrook.qr.payload"]
        signed = Payload.build("pkg", pkg.id)
        fake = self._fake()
        with _patched_request(fake):
            outcome = self.Controller._resolve_scan(fake.env, signed, None)
        self.assertNotEqual(outcome["outcome"], "expired")
        self.assertEqual(outcome["outcome"], "already_done")

    # ------------------------------------------------------------------
    # JSON contract — additive only, never breaking
    # ------------------------------------------------------------------
    def test_30_json_shape_unchanged_for_forbidden(self):
        outcome = {"outcome": "forbidden", "ok": False, "error": "forbidden"}
        result = self.Controller._outcome_to_json(outcome)
        self.assertEqual(result, {"ok": False, "error": "forbidden"},
                         "the legacy forbidden shape must stay exactly "
                         "{ok: false, error: 'forbidden'} — no new keys")

    def test_31_json_shape_for_ok_matches_legacy_keys(self):
        pkg, wo, wc = self._make_package_with_workorder(wc_code="UXP1-SAW")
        with patch.object(type(wo), "button_finish", autospec=True,
                          return_value=True):
            fake = self._fake()
            with _patched_request(fake):
                outcome = self.Controller._resolve_scan(
                    fake.env, "sb-package:%s" % pkg.id, "UXP1-SAW")
        result = self.Controller._outcome_to_json(outcome)
        self.assertEqual(
            set(result.keys()),
            {"ok", "package_id", "wo_id", "wo_state", "scan_count"},
            "success JSON shape must be EXACTLY the pre-existing keys")
        self.assertTrue(result["ok"])
        self.assertEqual(result["package_id"], pkg.id)

    def test_32_json_shape_for_wrong_step_adds_new_key_only(self):
        pkg, wo, wc = self._make_package_with_workorder(wc_code="UXP1-SAW")
        fake = self._fake()
        with _patched_request(fake), \
                patch.object(type(wo), "button_finish", autospec=True,
                             return_value=True):
            outcome = self.Controller._resolve_scan(
                fake.env, "sb-package:%s" % pkg.id, "WRONG-STATION")
        result = self.Controller._outcome_to_json(outcome)
        self.assertFalse(result["ok"])
        self.assertIn("error", result)
        self.assertEqual(result["expected_workcenter"], wc.display_name)

    def test_33_json_shape_for_expired_adds_new_key_only(self):
        if "southbrook.qr.payload" not in self.env:
            self.skipTest("southbrook_qr_kit not installed in this DB")
        outcome = {"outcome": "expired", "ok": False, "error": "expired",
                  "extra": {"ttl_hours": 720}}
        result = self.Controller._outcome_to_json(outcome)
        self.assertEqual(result["error"], "expired")
        self.assertEqual(result["ttl_hours"], 720)

    # ------------------------------------------------------------------
    # HTML rendering — the actual "real response" this task delivers.
    # Rendered directly (no HTTP layer) so every outcome, including
    # the harder-to-reach ones, gets checked without cross-thread
    # mock fragility.
    # ------------------------------------------------------------------
    def _render(self, outcome):
        html = self.Controller._render_scan_result_html(self.env, outcome)
        self.assertTrue(
            html.lower().startswith("<!doctype"),
            "rendered page must be a full HTML document, never a bare "
            "JSON blob or a fragment")
        self.assertNotIn("Traceback", html)
        self.assertNotIn("File \"", html,
                         "a raw Python traceback reaching the operator "
                         "is a defect regardless of which outcome hit it")
        return html

    def test_40_render_ok(self):
        pkg, wo, wc = self._make_package_with_workorder(wc_code="UXP1-SAW")
        outcome = {"outcome": "ok", "ok": True, "package": pkg, "wo": wo}
        html = self._render(outcome)
        self.assertIn("sb-scan-token-ok", html)
        self.assertIn("Scan confirmed", html)
        self.assertIn(pkg.name, html)

    def test_41_render_already_done_uses_info_token_not_ok_or_blocked(self):
        pkg = self._make_package()
        outcome = {"outcome": "already_done", "ok": True, "package": pkg,
                  "wo": self.env["mrp.workorder"]}
        html = self._render(outcome)
        self.assertIn("sb-scan-token-info", html)
        self.assertNotIn("sb-scan-token-ok", html)
        self.assertNotIn("sb-scan-token-blocked", html)

    def test_42_render_wrong_step_names_expected_and_actual_station(self):
        pkg, wo, wc = self._make_package_with_workorder(wc_code="UXP1-SAW")
        outcome = {
            "outcome": "wrong_step", "ok": False, "error": "wrong station",
            "package": pkg,
            "extra": {"expected_workcenter": wc.display_name,
                     "given_workcenter": "WRONG-STATION"},
        }
        html = self._render(outcome)
        self.assertIn("sb-scan-token-blocked", html)
        self.assertIn(wc.display_name, html)
        self.assertIn("WRONG-STATION", html)
        # Copy standard: says what to do next, not just what's wrong.
        self.assertIn("Bring the package to", html)

    def test_43_render_expired_explains_what_to_do_next(self):
        outcome = {"outcome": "expired", "ok": False, "error": "expired",
                  "extra": {"ttl_hours": 720}}
        html = self._render(outcome)
        self.assertIn("sb-scan-token-blocked", html)
        self.assertIn("Print a fresh traveler", html)

    def test_44_render_not_found_explains_what_to_do_next(self):
        outcome = {"outcome": "not_found", "ok": False,
                  "error": "package not found",
                  "extra": {"package_id": 999999999}}
        html = self._render(outcome)
        self.assertIn("sb-scan-token-blocked", html)
        self.assertIn("999999999", html)
        self.assertIn("supervisor", html.lower())

    def test_45_render_forbidden_explains_what_to_do_next(self):
        outcome = {"outcome": "forbidden", "ok": False, "error": "forbidden"}
        html = self._render(outcome)
        self.assertIn("sb-scan-token-blocked", html)
        self.assertIn("supervisor", html.lower())

    def test_46_render_invalid_payload_explains_what_to_do_next(self):
        outcome = {"outcome": "invalid_payload", "ok": False,
                  "error": "invalid qr_payload"}
        html = self._render(outcome)
        self.assertIn("sb-scan-token-blocked", html)
        self.assertIn("Reprint the traveler", html)

    def test_47_render_escapes_user_controlled_package_name(self):
        """Package names are free-text; a name containing HTML must
        never be interpreted as markup on the confirmation page."""
        pkg, wo, wc = self._make_package_with_workorder(wc_code="UXP1-SAW")
        pkg.name = "<script>alert(1)</script>"
        outcome = {"outcome": "ok", "ok": True, "package": pkg, "wo": wo}
        html = self._render(outcome)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_48_render_shows_next_step_when_more_steps_remain(self):
        pkg, wo, wc = self._make_package_with_workorder(wc_code="UXP1-SAW")
        wc2 = self.env["mrp.workcenter"].create({
            "name": "UXP1 Edge Bander", "code": "UXP1-EDGE",
        })
        wo2 = self.env["mrp.workorder"].create({
            "name": "UXP1 test wo 2",
            "workcenter_id": wc2.id,
            "production_id": pkg.mo_id.id,
            "product_uom_id": pkg.mo_id.product_uom_id.id,
            "sequence": 20,
        })
        wo2.state = "ready"
        wo.sequence = 10
        # Simulate "this step was just finished" so _sbk_next_workorder()
        # (called by the renderer to compute the upcoming-step line)
        # correctly skips it and surfaces wo2 instead.
        wo.state = "done"
        outcome = {"outcome": "ok", "ok": True, "package": pkg, "wo": wo}
        html = self._render(outcome)
        self.assertIn("Next step", html)
        self.assertIn("UXP1 Edge Bander", html)


@tagged("post_install", "-at_install", "southbrook", "floor_traveler",
        "ux_p1")
class TestUxP1ScanRoutesOverHttp(HttpCase):
    """Thin, real-HTTP smoke test: the two routes at the SAME URL
    genuinely dispatch to different handlers by method, exactly like
    southbrook_qr_kit's `/sb/qr/scan`. Kept to cases that don't need
    button_finish patched across the request thread, so there's no
    cross-thread mock fragility here — the harder outcomes are already
    pinned above via direct calls."""

    _URL = "/southbrook/api/floor-traveler/scan"

    def setUp(self):
        super().setUp()
        self.mrp_user = self.env["res.users"].create({
            "name": "UXP1 HTTP Operator",
            "login": "uxp1_http_operator",
            "password": "uxp1_http_operator",
            "group_ids": [(6, 0, [
                self.env.ref("base.group_user").id,
                self.env.ref("mrp.group_mrp_user").id,
            ])],
        })

    def test_10_get_returns_html_for_invalid_payload(self):
        self.authenticate("uxp1_http_operator", "uxp1_http_operator")
        resp = self.url_open(
            self._URL + "?qr_payload=not-a-real-payload")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.headers.get("Content-Type", ""))
        self.assertIn("CODE NOT RECOGNIZED", resp.text)
        self.assertNotIn("Traceback", resp.text)

    def test_11_get_returns_html_for_not_found(self):
        self.authenticate("uxp1_http_operator", "uxp1_http_operator")
        resp = self.url_open(
            self._URL + "?qr_payload=sb-package:999999999")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.headers.get("Content-Type", ""))
        self.assertIn("NOT FOUND", resp.text)

    def test_12_post_json_contract_unaffected_by_new_get_route(self):
        """Regression proof: adding the GET route at the same URL must
        not disturb the pre-existing POST/jsonrpc contract."""
        self.authenticate("uxp1_http_operator", "uxp1_http_operator")
        resp = self.url_open(
            self._URL,
            data=json.dumps({
                "jsonrpc": "2.0", "method": "call",
                "params": {"qr_payload": "sb-package:999999999"},
            }),
            headers={"Content-Type": "application/json"},
        )
        body = json.loads(resp.text)
        result = body.get("result", body)
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error"), "package not found")

    def test_13_get_without_auth_group_renders_forbidden_html_not_500(self):
        plain = self.env["res.users"].create({
            "name": "UXP1 HTTP Plain",
            "login": "uxp1_http_plain",
            "password": "uxp1_http_plain",
            "group_ids": [(6, 0, [self.env.ref("base.group_user").id])],
        })
        self.authenticate("uxp1_http_plain", "uxp1_http_plain")
        resp = self.url_open(self._URL + "?qr_payload=sb-package:1")
        self.assertEqual(
            resp.status_code, 200,
            "a forbidden scan must still render a page, not a 500/redirect")
        self.assertIn("NOT SIGNED IN", resp.text)


@tagged("post_install", "-at_install", "southbrook", "floor_traveler",
        "ux_p1")
class TestUxP1ManifestAndAssets(TransactionCase):
    """File-level invariants for the new scan-confirmation surface.

    Mirrors southbrook_qr_kit/tests/test_ux_p1_pin_modal_bundled.py's
    approach: parse the manifest as data (ast.literal_eval), not text
    matching, and confirm every path it claims to ship actually exists
    on disk — the exact class of bug (a real file, zero bundle
    references) this whole task exists to fix."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ft_root = get_module_path("southbrook_floor_traveler")
        cls.ui_root = get_module_path("southbrook_ui")
        with open(os.path.join(cls.ft_root, "__manifest__.py"),
                  encoding="utf-8") as fh:
            cls.manifest = ast.literal_eval(fh.read())

    def test_10_manifest_depends_on_southbrook_ui(self):
        self.assertIn(
            "southbrook_ui", self.manifest["depends"],
            "the scan-result page colours itself via southbrook_ui's "
            "--sb-status-* tokens and needs the dependency declared, "
            "per the southbrook-design-system skill")

    def test_11_scan_result_bundle_wires_tokens_and_own_scss(self):
        bundle = self.manifest["assets"]["web.assets_floor_traveler_scan"]
        self.assertIn(
            "southbrook_ui/static/src/scss/_sb_tokens.scss", bundle)
        self.assertIn(
            "southbrook_floor_traveler/static/src/scss/scan_result.scss",
            bundle)

    def test_12_every_listed_asset_path_exists_on_disk(self):
        roots = {
            "southbrook_floor_traveler": self.ft_root,
            "southbrook_ui": self.ui_root,
        }
        missing = []
        for bundle_name, paths in self.manifest.get("assets", {}).items():
            for path in paths:
                addon, _, rel = path.partition("/")
                root = roots.get(addon)
                if not root:
                    continue  # asset contributed by an addon we don't
                              # need to independently verify here
                if not os.path.isfile(os.path.join(root, rel)):
                    missing.append("%s: %s" % (bundle_name, path))
        self.assertFalse(
            missing,
            "manifest lists asset path(s) that do not exist on disk: "
            "%s" % missing)

    def test_13_scan_result_view_and_config_data_registered(self):
        self.assertIn("views/scan_result_template.xml", self.manifest["data"])
        self.assertIn(
            "data/floor_traveler_config_parameters.xml",
            self.manifest["data"])

    def test_14_scan_result_scss_never_hardcodes_status_hex(self):
        scss_path = os.path.join(
            self.ft_root, "static/src/scss/scan_result.scss")
        with open(scss_path, encoding="utf-8") as fh:
            content = fh.read()
        for token_class in ("sb-scan-token-ok", "sb-scan-token-info",
                            "sb-scan-token-blocked"):
            self.assertIn(token_class, content)
        marker = "Status tokens"
        self.assertIn(marker, content)
        tail = content[content.index(marker):]
        self.assertNotIn(
            "#", tail,
            "the status-token section must colour ONLY via "
            "var(--sb-status-*) — no hardcoded hex, per the "
            "southbrook-design-system skill")

    def test_15_scan_result_template_exists_and_pulls_the_lean_bundle(self):
        tmpl_path = os.path.join(
            self.ft_root, "views/scan_result_template.xml")
        self.assertTrue(os.path.isfile(tmpl_path))
        with open(tmpl_path, encoding="utf-8") as fh:
            content = fh.read()
        self.assertIn("web.assets_floor_traveler_scan", content)
        # Must NOT drag in the full backend/frontend bundle — same
        # "lean bundle" discipline as southbrook_qr_kit's floor shell.
        self.assertNotIn("web.assets_backend", content)
        self.assertNotIn("web.assets_frontend", content)
