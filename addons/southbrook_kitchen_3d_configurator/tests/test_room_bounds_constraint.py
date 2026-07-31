# SPDX-License-Identifier: LGPL-3.0-only
"""Task 12 (corner-remediation 2026-07-30, SBK-021/022) — room-dimension
bounds as a model-level `@api.constrains` (Odoo 19: the current
constraint API, NOT `_sql_constraints`, which is deprecated for this
kind of check per V19CR strategy).

Live evidence (V5-partc §SBK-021/022): `room_width_in`/`room_depth_in`/
`room_height_in` were plain `fields.Float(required=True)` with ZERO
range checks anywhere — a client posting room_width_in=99999 generated
8,332 lines / ~$1.77M and persisted it verbatim. This locks the model
half of the fix: `SouthbrookKitchenDesign._check_room_bounds` (models/
kitchen_design.py), using the single-source-of-truth bounds constants
`ROOM_WIDTH_MIN_IN`/`ROOM_WIDTH_MAX_IN`/`ROOM_DEPTH_MIN_IN`/
`ROOM_DEPTH_MAX_IN`/`ROOM_HEIGHT_MIN_IN`/`ROOM_HEIGHT_MAX_IN` also
imported by controllers/main.py's save_design pre-check (see
test_room_bounds_save_and_autofill_cap.py for that half).

Deliberately write-time-only: an already-persisted design ABOVE these
bounds (e.g. a legacy 99999in room saved before this fix existed) must
keep LOADING/READING fine — the binding constraint explicitly calls
this out ("constraint on write only, not a registry-breaking check").
`test_legacy_oversized_design_still_loads_and_reads` locks that by
writing the oversized value directly via SQL (bypassing the ORM/
constraint, exactly mirroring how such a row would already exist from
before this fix shipped) and confirming a plain read/browse — and an
unrelated field write — never raises.
"""
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged

from odoo.addons.southbrook_kitchen_3d_configurator.models.kitchen_design import (
    ROOM_WIDTH_MIN_IN, ROOM_WIDTH_MAX_IN,
    ROOM_DEPTH_MIN_IN, ROOM_DEPTH_MAX_IN,
    ROOM_HEIGHT_MIN_IN, ROOM_HEIGHT_MAX_IN,
)


@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator", "corner_remediation")
class TestRoomBoundsConstraint(TransactionCase):

    def _vals(self, **overrides):
        vals = {
            "name": "T12 room bounds",
            "room_width_in": 120.0,
            "room_depth_in": 96.0,
            "room_height_in": 96.0,
        }
        vals.update(overrides)
        return vals

    # ── create() ────────────────────────────────────────────────────────
    def test_create_rejects_width_above_max(self):
        with self.assertRaises(ValidationError):
            self.env["southbrook.kitchen.design"].create(
                self._vals(room_width_in=99999.0))

    def test_create_rejects_width_below_min(self):
        with self.assertRaises(ValidationError):
            self.env["southbrook.kitchen.design"].create(
                self._vals(room_width_in=1.0))

    def test_create_rejects_depth_out_of_range(self):
        with self.assertRaises(ValidationError):
            self.env["southbrook.kitchen.design"].create(
                self._vals(room_depth_in=99999.0))
        with self.assertRaises(ValidationError):
            self.env["southbrook.kitchen.design"].create(
                self._vals(room_depth_in=1.0))

    def test_create_rejects_height_out_of_range(self):
        with self.assertRaises(ValidationError):
            self.env["southbrook.kitchen.design"].create(
                self._vals(room_height_in=200.0))
        with self.assertRaises(ValidationError):
            self.env["southbrook.kitchen.design"].create(
                self._vals(room_height_in=10.0))

    def test_create_accepts_inclusive_boundary_values(self):
        design = self.env["southbrook.kitchen.design"].create(self._vals(
            room_width_in=ROOM_WIDTH_MIN_IN,
            room_depth_in=ROOM_DEPTH_MAX_IN,
            room_height_in=ROOM_HEIGHT_MIN_IN,
        ))
        self.assertEqual(design.room_width_in, ROOM_WIDTH_MIN_IN)
        design2 = self.env["southbrook.kitchen.design"].create(self._vals(
            room_width_in=ROOM_WIDTH_MAX_IN,
            room_depth_in=ROOM_DEPTH_MIN_IN,
            room_height_in=ROOM_HEIGHT_MAX_IN,
        ))
        self.assertEqual(design2.room_width_in, ROOM_WIDTH_MAX_IN)

    def test_create_accepts_normal_values(self):
        # Non-regression: the overwhelming majority of shipped fixtures/
        # demo data use ordinary dims (96-200in width, 96in height) —
        # must never be caught by this constraint.
        design = self.env["southbrook.kitchen.design"].create(self._vals())
        self.assertEqual(design.room_width_in, 120.0)

    # ── write() ─────────────────────────────────────────────────────────
    def test_write_rejects_out_of_range_width(self):
        design = self.env["southbrook.kitchen.design"].create(self._vals())
        with self.assertRaises(ValidationError):
            design.write({"room_width_in": 99999.0})

    def test_write_rejects_out_of_range_height(self):
        design = self.env["southbrook.kitchen.design"].create(self._vals())
        with self.assertRaises(ValidationError):
            design.write({"room_height_in": 500.0})

    def test_write_of_unrelated_field_is_unaffected(self):
        # Sanity: the constraint must not fire on every write — only
        # when one of the three room-dimension fields is touched.
        design = self.env["southbrook.kitchen.design"].create(self._vals())
        design.write({"name": "T12 renamed"})
        self.assertEqual(design.name, "T12 renamed")

    # ── the binding requirement: legacy oversized rows still LOAD ──────
    def test_legacy_oversized_design_still_loads_and_reads(self):
        """Simulates a design persisted BEFORE this constraint existed
        (e.g. the live-evidence 99999in room) by writing the value
        directly via SQL — bypassing the ORM/constraint entirely, the
        only way such a row could legitimately carry an out-of-bounds
        value today. A plain read must never raise; this is the
        "constraint on write only, not a registry-breaking check"
        requirement from the task brief."""
        design = self.env["southbrook.kitchen.design"].create(self._vals())
        self.env.cr.execute(
            "UPDATE southbrook_kitchen_design SET room_width_in = %s "
            "WHERE id = %s",
            (99999.0, design.id),
        )
        design.invalidate_recordset()

        # Reading/browsing must not raise — this is the whole point.
        self.assertEqual(design.room_width_in, 99999.0)
        design.read(["room_width_in", "name"])

        # A write that does NOT touch any of the three room-dimension
        # fields must also succeed untouched (other actions on a legacy
        # oversized design — e.g. renaming it — must keep working).
        design.write({"name": "T12 legacy still workable"})
        self.assertEqual(design.name, "T12 legacy still workable")

        # But the moment a NEW write touches room_width_in again (even
        # to try to keep the same bad value), it's rejected — future
        # saves of the room dims are gated, per the brief.
        with self.assertRaises(ValidationError):
            design.write({"room_width_in": 99999.0})
