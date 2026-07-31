# SPDX-License-Identifier: LGPL-3.0-only
"""southbrook.placement.rule — M1 "rules-as-data" layer for the corner
cabinetry rule engine (docs/research/corner-engine/09-rule-engine-spec.md
§6-7). One record per placeable product. The pure
`kitchen_layout_engine` module never reads Odoo directly: this model's
`engine_dicts()` is the ONLY bridge, handing the engine a plain-dict
`corner_rules` list it consumes as a new, optional kwarg (§7 migration
step M1). Nothing here changes `models/kitchen_layout_engine.py`.

Payload contract (§3 of the spec, mm units — the engine is metric-only
per COORDINATE_CONTRACT.md): validated by a hand-rolled python
constraint below rather than a jsonschema dependency, because the
envelope grammar the spec anticipates (arcs, cylinders — §5) is still
growing and a full schema would need to be re-validated every time a
new envelope kind lands. The M1 slice only needs the five junction-
anchor legs the engine's `select_corner_type` step (§4.3) reads today,
so the checklist stays short and explicit; extend it here (not with an
external schema file) when M2 adds asymmetric `wall_consumption_in` or
M4 adds `envelopes`.
"""
from odoo import api, fields, models
from odoo.exceptions import ValidationError

# Junction-anchor payload keys the M1 engine step reads (§3, §4.3).
_JUNCTION_REQUIRED_NUMERIC_KEYS = (
    "leg_x_mm",
    "leg_z_mm",
    "height_mm",
    "min_leg_x_mm",
    "min_leg_z_mm",
)

_HOST_LEG_VALUES = ("x", "z")

# Seed data for the 5 live corner SKUs (spec §6, "Seed data"). Kept as a
# module-level constant so both the install-time seeder
# (_seed_default_rules, invoked from data/placement_rules.xml via
# <function>) and tests can reference the same source of truth.
#
# product_default_code is resolved to a product.template at seed time —
# see _seed_default_rules for why SB-CORNER-BLIND / SB-WALL-CORNER fall
# back to SB-CORNER when unresolvable.
_SEED_RULES = [
    {
        "name": "Diagonal susan base corner 36x36",
        "product_default_code": "SB-CORNER",
        "corner_type_id": "diagonal-corner-lazy-susan",
        "anchor_class": "junction",
        "tier": "base",
        "sequence": 10,
        "payload": {
            # Task 3 (2026-07-30, corner-remediation) — 914.4mm = 36in
            # exactly (25.4 * 36). The prior 914.0 was a round-metric
            # approximation of an inch-native leg dimension; see
            # .superpowers/sdd/corner-verify/V1-geometry.md for the
            # drift math this caused on persisted design lines.
            "leg_x_mm": 914.4,
            "leg_z_mm": 914.4,
            # Task 4 (2026-07-31, height-drift) — 876.3mm = 25.4 * 34.5
            # exactly. The prior 876.0 was a round-metric approximation
            # (34.488", rounds to 34.49" instead of 34.50" like every
            # other base cabinet); see
            # .superpowers/sdd/corner-verify/V1-geometry.md for the
            # drift math this caused on persisted design lines.
            "height_mm": 876.3,
            "min_leg_x_mm": 1143.0,
            "min_leg_z_mm": 1143.0,
            # M4 — bifold door swing / tray access in front of the cell
            # (ESTIMATE per docs 04/06; surfaces as a blocking
            # MOTION_ENVELOPE_COLLISION when violated). Metric spec
            # value — not an inch-native leg dimension, left as-is.
            "clearance_front_mm": 500.0,
        },
    },
    {
        "name": "Blind base corner 45/24 (fallback when a susan leg won't fit)",
        "product_default_code": "SB-CORNER-BLIND",
        "corner_type_id": "blind-corner-basic",
        "anchor_class": "junction",
        "tier": "base",
        "sequence": 20,
        "payload": {
            # Task 3 — 609.6mm = 24in exactly (25.4 * 24).
            "leg_x_mm": 609.6,
            "leg_z_mm": 1143.0,
            # Task 4 — 876.3mm = 25.4 * 34.5 exactly (see the susan
            # rule above for the drift this replaces).
            "height_mm": 876.3,
            # M3 — the 3in blind-corner filler on the X-wall run
            # (docs 02/06 `filler-blind-corner-min`): min_leg_x must
            # cover leg + filler.
            "filler_x_mm": 76.2,
            # Task 3 — 685.8mm = 27in exactly (609.6 leg + 76.2
            # filler); 1371.6mm = 54in exactly (25.4 * 54).
            "min_leg_x_mm": 685.8,
            "min_leg_z_mm": 1371.6,
            "host_leg": "z",
            "clearance_front_mm": 450.0,
        },
    },
    {
        "name": "Pie-cut wall corner 24x24",
        "product_default_code": "SB-WALL-CORNER",
        "corner_type_id": "pie-cut-wall-corner",
        "anchor_class": "junction",
        "tier": "wall",
        "sequence": 10,
        "payload": {
            # Task 3 — 609.6mm = 24in exactly (25.4 * 24).
            "leg_x_mm": 609.6,
            "leg_z_mm": 609.6,
            "height_mm": 762.0,
            "min_leg_x_mm": 762.0,
            "min_leg_z_mm": 762.0,
            "clearance_front_mm": 300.0,
        },
    },
]

# Prior seed payload versions, keyed by (corner_type_id, tier). When the
# seeder finds an existing rule whose payload EXACTLY matches one of these
# historical versions, the rule is still factory-state and is upgraded to
# the current _SEED_RULES payload in place. A payload the user has edited
# matches none of them and is left untouched.
_SEED_PAYLOADS_LEGACY = {
    ("diagonal-corner-lazy-susan", "base"): [
        {"leg_x_mm": 914.0, "leg_z_mm": 914.0, "height_mm": 876.0,
         "min_leg_x_mm": 1143.0, "min_leg_z_mm": 1143.0},
        # M4 factory state, pre-Task-3 (round-metric 914.0 legs).
        {"leg_x_mm": 914.0, "leg_z_mm": 914.0, "height_mm": 876.0,
         "min_leg_x_mm": 1143.0, "min_leg_z_mm": 1143.0,
         "clearance_front_mm": 500.0},
        # Task 4 (2026-07-31, height-drift) — post-Task-3 production
        # state: inch-exact 914.4mm legs already landed, but height_mm
        # still carried the round-metric 876.0 (34.488", rounds to
        # 34.49"). Recognise it so live rows self-heal to 876.3.
        {"leg_x_mm": 914.4, "leg_z_mm": 914.4, "height_mm": 876.0,
         "min_leg_x_mm": 1143.0, "min_leg_z_mm": 1143.0,
         "clearance_front_mm": 500.0},
    ],
    ("blind-corner-basic", "base"): [
        {"leg_x_mm": 610.0, "leg_z_mm": 1143.0, "height_mm": 876.0,
         "min_leg_x_mm": 610.0, "min_leg_z_mm": 1372.0, "host_leg": "z"},
        # M3/M4 factory state, pre-Task-3 (round-metric 610.0/686.2/
        # 1372.0 legs).
        {"leg_x_mm": 610.0, "leg_z_mm": 1143.0, "height_mm": 876.0,
         "filler_x_mm": 76.2, "min_leg_x_mm": 686.2,
         "min_leg_z_mm": 1372.0, "host_leg": "z",
         "clearance_front_mm": 450.0},
        # Task 4 — post-Task-3 production state: inch-exact 609.6/685.8/
        # 1371.6mm legs already landed, height_mm still 876.0.
        {"leg_x_mm": 609.6, "leg_z_mm": 1143.0, "height_mm": 876.0,
         "filler_x_mm": 76.2, "min_leg_x_mm": 685.8,
         "min_leg_z_mm": 1371.6, "host_leg": "z",
         "clearance_front_mm": 450.0},
    ],
    ("pie-cut-wall-corner", "wall"): [
        {"leg_x_mm": 610.0, "leg_z_mm": 610.0, "height_mm": 762.0,
         "min_leg_x_mm": 762.0, "min_leg_z_mm": 762.0},
        # M4 factory state, pre-Task-3 (round-metric 610.0 legs).
        {"leg_x_mm": 610.0, "leg_z_mm": 610.0, "height_mm": 762.0,
         "min_leg_x_mm": 762.0, "min_leg_z_mm": 762.0,
         "clearance_front_mm": 300.0},
    ],
}

# Fallback product when a seed rule's product_default_code has no
# resolvable product.template in THIS module (see _seed_default_rules).
_SEED_FALLBACK_XMLID = "southbrook_estimating.corner"


class SouthbrookPlacementRule(models.Model):
    _name = "southbrook.placement.rule"
    _description = "Cabinet placement rule (rules-as-data for the pure layout engine)"
    _order = "sequence, id"

    name = fields.Char(required=True)
    product_tmpl_id = fields.Many2one(
        "product.template", required=True, ondelete="cascade", index=True)
    corner_type_id = fields.Char(
        help="Taxonomy type_id — see docs/research/corner-engine/"
             "01-base-corner-taxonomy.md and 02-*.md (wall types) for "
             "the canonical registry (e.g. 'diagonal-corner-lazy-susan', "
             "'blind-corner-basic', 'pie-cut-wall-corner').")
    anchor_class = fields.Selection(
        [("run", "Run"), ("junction", "Junction"), ("free", "Free")],
        default="junction", required=True,
        help="§2.2 of the rule-engine spec: run-anchored (along-wall "
             "cursor packing), junction-anchored (consumes both "
             "adjacent wall legs), or free-anchored (explicit pose, "
             "islands/peninsulas).")
    tier = fields.Selection(
        [("base", "Base"), ("wall", "Wall")], required=True)
    sequence = fields.Integer(
        default=10,
        help="Lower = preferred when several rules fit (§4.3 "
             "select_corner_type ranking step).")
    active = fields.Boolean(default=True)
    payload = fields.Json(
        required=True,
        help="Engine-facing rule record (§3). All lengths are mm "
             "floats. See the module-level docstring for the "
             "validated key set.")

    @api.constrains("payload", "tier", "anchor_class")
    def _check_payload(self):
        for rule in self:
            payload = rule.payload or {}
            if not isinstance(payload, dict):
                raise ValidationError(
                    self.env._("Placement rule '%s': payload must be a "
                                "JSON object.", rule.name))

            if rule.anchor_class != "junction":
                # M1 only validates the junction-anchor shape (§3/§4.3);
                # run/free rules aren't consumed by select_corner_type
                # yet and carry no required keys here.
                continue

            for key in _JUNCTION_REQUIRED_NUMERIC_KEYS:
                if key not in payload:
                    raise ValidationError(
                        self.env._(
                            "Placement rule '%(name)s': payload is "
                            "missing required key '%(key)s' "
                            "(anchor_class=junction needs leg_x_mm, "
                            "leg_z_mm, height_mm, min_leg_x_mm, "
                            "min_leg_z_mm).",
                            name=rule.name, key=key))
                value = payload[key]
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ValidationError(
                        self.env._(
                            "Placement rule '%(name)s': payload key "
                            "'%(key)s' must be numeric, got %(value)r.",
                            name=rule.name, key=key, value=value))
                if value <= 0:
                    raise ValidationError(
                        self.env._(
                            "Placement rule '%(name)s': payload key "
                            "'%(key)s' must be > 0, got %(value)r.",
                            name=rule.name, key=key, value=value))

            if payload["min_leg_x_mm"] < payload["leg_x_mm"]:
                raise ValidationError(
                    self.env._(
                        "Placement rule '%(name)s': min_leg_x_mm "
                        "(%(min)r) must be >= leg_x_mm (%(leg)r).",
                        name=rule.name, min=payload["min_leg_x_mm"],
                        leg=payload["leg_x_mm"]))
            if payload["min_leg_z_mm"] < payload["leg_z_mm"]:
                raise ValidationError(
                    self.env._(
                        "Placement rule '%(name)s': min_leg_z_mm "
                        "(%(min)r) must be >= leg_z_mm (%(leg)r).",
                        name=rule.name, min=payload["min_leg_z_mm"],
                        leg=payload["leg_z_mm"]))

            host_leg = payload.get("host_leg")
            if host_leg is not None and host_leg not in _HOST_LEG_VALUES:
                raise ValidationError(
                    self.env._(
                        "Placement rule '%(name)s': payload key "
                        "'host_leg' must be 'x' or 'z' if present, got "
                        "%(value)r.",
                        name=rule.name, value=host_leg))

            # M3/M4 — optional numeric keys: corner filler strips per leg
            # and the front motion-envelope clearance. All must be
            # numeric >= 0 when present; a filler additionally extends
            # the leg's minimum wall-length requirement.
            for opt_key in ("filler_x_mm", "filler_z_mm",
                            "clearance_front_mm"):
                if opt_key not in payload:
                    continue
                value = payload[opt_key]
                if isinstance(value, bool) or not isinstance(
                        value, (int, float)) or value < 0:
                    raise ValidationError(
                        self.env._(
                            "Placement rule '%(name)s': payload key "
                            "'%(key)s' must be numeric >= 0 if present, "
                            "got %(value)r.",
                            name=rule.name, key=opt_key, value=value))
            for leg, fill, mn in (("leg_x_mm", "filler_x_mm",
                                   "min_leg_x_mm"),
                                  ("leg_z_mm", "filler_z_mm",
                                   "min_leg_z_mm")):
                fw = payload.get(fill) or 0
                # Task 3 (corner-remediation) — 1e-6mm epsilon: inch-
                # exact mm values (e.g. 609.6 + 76.2 = 685.8) are not
                # exactly representable in IEEE-754 double, so a bare
                # `<` here false-positives on a payload that is exactly
                # at the boundary (609.6 + 76.2 == 685.8000000000001).
                if fw and payload[mn] < payload[leg] + fw - 1e-6:
                    raise ValidationError(
                        self.env._(
                            "Placement rule '%(name)s': %(mn)s (%(m)r) "
                            "must cover %(leg)s + %(fill)s "
                            "(%(l)r + %(f)r).",
                            name=rule.name, mn=mn,
                            m=payload[mn], leg=leg, fill=fill,
                            l=payload[leg], f=fw))

    def engine_dicts(self):
        """Plain-dict form the pure kitchen_layout_engine consumes as
        `corner_rules`. sku comes from the linked template's default_code;
        sequence rides along for select ranking.

        Task 28 (corner-remediation 2026-07-30) fix — `product_tmpl_id.
        default_code` is Odoo's own related field, and Odoo only
        populates it for a SINGLE-variant template
        (`_compute_default_code`: `len(product_variant_ids) != 1` ->
        forced False, no matter what any individual variant's own code
        is). The corner-remediation Task 6 work made multi-variant LH/RH
        corner templates the EXPECTED shape (see kitchen_design.py's T5
        `_sb_resolve_corner_variant` docstring), so a template going
        multi-variant silently blanks `sku` here -> the caller
        (kitchen_design.py action_auto_arrange) falls back to its
        hardcoded legacy `_CORNER_SKU` default ("SB-CORNER", the susan)
        for EVERY rule whose template has 2+ variants, regardless of
        which rule the leg-length gate actually picked. Live evidence:
        prod's "Blind base corner 45/24" rule (id 2, corner_type_id
        blind-corner-basic) resolved to template 223 "Corner Base ·
        Blind" with `default_code=False` (variant 1104=SB-CORNER-BLIND,
        variant 1422=no code) — a narrow room correctly SELECTED the
        blind rule (min-leg gate passed) but PLACED SB-CORNER (susan)
        because `sku` came back "". Falling back to a variant's own
        default_code — preferring one that actually HAS a code, same
        precedence `_sb_resolve_corner_variant`'s own last-resort
        fallback uses — fixes this without touching the ORM's
        single-variant-only `default_code` semantics."""
        out = []
        for r in self:
            d = dict(r.payload or {})
            tmpl = r.product_tmpl_id
            sku = tmpl.default_code
            if not sku:
                variant = tmpl.product_variant_ids.filtered(
                    lambda v: v.default_code)[:1]
                if not variant:
                    variant = tmpl.product_variant_id or tmpl.product_variant_ids[:1]
                sku = variant.default_code or ""
            d.update({"rule_id": "spr-%d" % r.id, "sku": sku or "",
                      "tier": r.tier, "sequence": r.sequence})
            out.append(d)
        return out

    @api.model
    def _seed_default_rules(self):
        """Idempotent install-time seed for the 3 M1 corner rules
        (invoked by data/placement_rules.xml via <function>).

        Product resolution choice (documented per the M1 task brief):
        southbrook_estimating ships only ONE corner template in its own
        data/product_templates.xml — xml_id 'corner' / default_code
        SB-CORNER (see that file, ~line 1120). SB-CORNER-BLIND and
        SB-WALL-CORNER are minted at runtime by the SIBLING module
        southbrook_configurator_ux (models/catalog_expansion.py) — a
        module this addon does NOT depend on, so those templates have
        no stable xml_id reachable from here and may not even exist in
        every database this module installs into (e.g. this module's
        own unit tests).
        A hardcoded ref() to a foreign xml_id would either raise
        (module not installed) or be fragile (relies on load order
        across two independently-versioned addons). Instead we search
        by default_code at seed time — the same lookup convention the
        rest of the codebase already uses for these SKUs (see
        kitchen_design.py's `_CORNER_SKU` table) — and fall back to the
        in-module SB-CORNER template when the sibling module hasn't
        (yet) created its templates. This keeps the seed install-safe
        in every dependency configuration while resolving to the
        richer template automatically once southbrook_configurator_ux
        is installed alongside it.
        """
        Tmpl = self.env["product.template"]
        created = skipped = 0
        for spec in _SEED_RULES:
            existing = self.search([
                ("corner_type_id", "=", spec["corner_type_id"]),
                ("tier", "=", spec["tier"]),
            ], limit=1)
            if existing:
                # M3/M4 upgrade path — a rule still carrying a prior
                # seed payload verbatim is factory-state: bring it up to
                # the current seed (adds filler/clearance keys). Any
                # user-edited payload matches no legacy version and is
                # preserved.
                legacy = _SEED_PAYLOADS_LEGACY.get(
                    (spec["corner_type_id"], spec["tier"]), [])
                if (existing.payload or {}) in legacy \
                        and existing.payload != spec["payload"]:
                    existing.payload = spec["payload"]
                skipped += 1
                continue

            tmpl = Tmpl.search(
                [("default_code", "=", spec["product_default_code"])],
                limit=1)
            if not tmpl and spec["product_default_code"] == "SB-CORNER":
                # Only SB-CORNER may resolve via the in-module xml_id —
                # its default_code can lag data loading order.
                tmpl = self.env.ref(_SEED_FALLBACK_XMLID,
                                    raise_if_not_found=False)
            if not tmpl:
                # The intended template doesn't exist in this DB (e.g.
                # southbrook_configurator_ux not installed). SKIP the
                # rule rather than substitute a different product: the
                # rule's SKU drives which PRODUCT the engine inserts at
                # that corner, and substituting SB-CORNER (a base
                # cabinet) into e.g. the WALL-tier rule would silently
                # put a base product at upper level. With the rule
                # absent, the engine's legacy per-tier _CORNER_SKU
                # fallback picks the correct product family instead.
                skipped += 1
                continue

            self.create({
                "name": spec["name"],
                "product_tmpl_id": tmpl.id,
                "corner_type_id": spec["corner_type_id"],
                "anchor_class": spec["anchor_class"],
                "tier": spec["tier"],
                "sequence": spec["sequence"],
                "payload": spec["payload"],
            })
            created += 1
        return (created, skipped)
