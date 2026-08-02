# SPDX-License-Identifier: LGPL-3.0-only
"""PLM data repair — southbrook.eco / southbrook.cut.spec / mrp.bom cleanup.

Context: the southbrook_plm module (Engineering Change Orders + parametric
cut specs + BoM versioning) accumulated production data debt during the
build/demo/test cycle: two duplicate ACTIVE BoM heads at southbrook_version
2 (ids 129/130, code 'SB-BASE-1DR Canonical'), three exact-duplicate v1 BoMs
sharing code 'CatalogAutoBOM-Variant-SB-BASE-1DR', a handful of test/chatter
ECOs that leaked into the applied audit trail, one saved bom-kind ECO draft
(ECO/2026/0115) with an empty Target BoM that traps the form, and demo
records that should not ride along into the production DB. This script
reconciles all of it in one governed, auditable pass.

Run via odoo shell inside the prod container:

    docker exec -i southbrook-odoo odoo shell -d southbrook --no-http \\
        --http-port=8899 --gevent-port=8902 \\
        --workers=0 --max-cron-threads=0 \\
        < scripts/plm_data_repair_20260801.py

Mode:
    APPLY = False (default) -> DRY-RUN. Every mutation below is executed
        against the ORM (so constraints/handlers run for real and any
        failure surfaces immediately), but the whole transaction is rolled
        back at the very end via env.cr.rollback(). Nothing persists.
    APPLY = True  -> the same mutations are executed and then committed
        once at the very end via env.cr.commit().

The snapshot (step 2) is written to disk unconditionally, dry-run or not,
because it is a read-only export, not a DB mutation.

Idempotent: every step re-derives its target set from current DB state
(never from hardcoded ids), so running this script twice in APPLY mode is a
no-op the second time — already-archived BoMs stay out of the active-only
groupings, already-deleted ECOs/demo records are no longer found, and the
demo-flag UPDATE affects 0 rows once already flipped.
"""
import json
import re
from collections import defaultdict
from datetime import datetime

# ---------------------------------------------------------------------
# 1. Mode flag
# ---------------------------------------------------------------------
APPLY = False  # <-- flip to True to persist changes, then re-run this file.

VERB = "DID" if APPLY else "WOULD"

SNAPSHOT_PATH = "/tmp/plm_repair_snapshot.json"

# Titles of test/experimentation ECOs that leaked into the applied history.
# Exact match only — do not touch any other applied ECO.
TEST_ECO_TITLES = [
    "M20 chatter test",
    "M20 Markup test",
    "Provenance verify",
]

# xmlids as actually present in addons/southbrook_plm/demo/southbrook_plm_demo.xml
# (read 2026-08-01). ECOs first so no FK from an ECO to a demo cut spec ever
# blocks the cut-spec unlink; the cut spec follows.
DEMO_ECO_XMLIDS = [
    "southbrook_plm.demo_eco_bom",
    "southbrook_plm.demo_eco_cut_spec",
    "southbrook_plm.demo_eco_rule",
    "southbrook_plm.demo_eco_document",
]
DEMO_CUT_SPEC_XMLIDS = [
    "southbrook_plm.demo_cut_spec_wider_reveal",
]

TESTISH_RE = re.compile(r"test|verify|demo|chatter", re.IGNORECASE)

RECONCILIATION_NOTE_STAMP = "2026-08-01"

ledger = {
    "archived_boms": [],        # [{id, code, reason}]
    "deleted_ecos": [],         # [{id, name, title}]
    "deleted_demo_xmlids": [],  # [xmlid, ...]
    "demo_flag_rowcount": None,
    "needs_human": [],          # [str, ...]
    "errors": [],               # [str, ...] non-fatal, explicitly-allowed fallbacks only
}


def hr(title):
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def archive_bom(bom, version, survivor):
    """Archive one duplicate BoM head, chatter-noting why, guarded."""
    note = (
        "Archived by PLM data reconciliation %s — duplicate active head "
        "at version %s; surviving head is %s (id %s)."
        % (RECONCILIATION_NOTE_STAMP, version, survivor.display_name, survivor.id)
    )
    try:
        bom.message_post(body=note)
        bom.write({"active": False})
    except Exception as exc:  # noqa: BLE001
        print("  [ERROR] archiving BoM id=%s (code=%r) failed: %s"
              % (bom.id, bom.code, exc))
        raise
    print("  [%s] archived BoM id=%s code=%r -> kept id=%s (%s)"
          % (VERB, bom.id, bom.code, survivor.id, survivor.display_name))
    ledger["archived_boms"].append(
        {"id": bom.id, "code": bom.code, "reason": note}
    )


# ---------------------------------------------------------------------
# 2. SNAPSHOT (always taken, even in dry-run, before any mutation)
# ---------------------------------------------------------------------
hr("STEP 2 -- SNAPSHOT (pre-repair state) -> %s" % SNAPSHOT_PATH)

snapshot = {
    "generated_at": datetime.now().isoformat(),
    "apply_mode": APPLY,
}

ecos_snap = env["southbrook.eco"].search([])
snapshot["southbrook_eco"] = [
    {
        "id": e.id,
        "name": e.name,
        "title": e.title,
        "state": e.state,
        "stage": e.stage_id.name if e.stage_id else None,
        "approver_id": e.approver_id.id or None,
        "approver_name": e.approver_id.name or None,
        "user_id": e.user_id.id or None,
        "user_name": e.user_id.name or None,
        "create_uid": e.create_uid.id or None,
        "create_uid_name": e.create_uid.name or None,
        "bom_id": e.bom_id.id or None,
        "new_bom_id": e.new_bom_id.id or None,
        "cut_spec_id": e.cut_spec_id.id or None,
        "applied_date": e.applied_date,
    }
    for e in ecos_snap
]

cut_specs_snap = env["southbrook.cut.spec"].with_context(active_test=False).search([])
snapshot["southbrook_cut_spec"] = [
    {
        "id": c.id,
        "name": c.name,
        "state": c.state,
        "active": c.active,
        "version": c.version,
        "box_th": c.box_th,
        "back_th": c.back_th,
        "rabbet": c.rabbet,
        "door_th": c.door_th,
        "door_reveal": c.door_reveal,
        "shelf_tol": c.shelf_tol,
        "shelf_vent_gap": c.shelf_vent_gap,
        "toekick_h": c.toekick_h,
    }
    for c in cut_specs_snap
]

# mrp.bom: every BoM whose product_tmpl_id has more than one BoM record,
# counting archived ones too (active_test=False needed to find those).
all_boms_snap = env["mrp.bom"].with_context(active_test=False).search([])
by_tmpl = defaultdict(list)
for b in all_boms_snap:
    by_tmpl[b.product_tmpl_id.id].append(b.id)

multi_head_ids = []
for tmpl_id, ids in by_tmpl.items():
    if len(ids) > 1:
        multi_head_ids.extend(ids)

multi_head_boms = env["mrp.bom"].with_context(active_test=False).browse(
    sorted(set(multi_head_ids))
)
snapshot["mrp_bom_multi_head_templates"] = [
    {
        "id": b.id,
        "code": b.code,
        "display_name": b.display_name,
        "product_tmpl_id": b.product_tmpl_id.id,
        "product_tmpl_name": b.product_tmpl_id.display_name,
        "product_id": b.product_id.id or None,
        "type": b.type,
        "southbrook_version": b.southbrook_version,
        "active": b.active,
    }
    for b in multi_head_boms.sorted(key=lambda r: (r.product_tmpl_id.id, r.id))
]

with open(SNAPSHOT_PATH, "w") as f:
    json.dump(snapshot, f, indent=2, default=str)

print("  %d ECO(s), %d cut spec(s), %d BoM(s) across multi-head templates written."
      % (
          len(snapshot["southbrook_eco"]),
          len(snapshot["southbrook_cut_spec"]),
          len(snapshot["mrp_bom_multi_head_templates"]),
      ))


# ---------------------------------------------------------------------
# 3. DUPLICATE ACTIVE BoM HEADS (southbrook_version > 1)
# ---------------------------------------------------------------------
hr("STEP 3 -- DUPLICATE ACTIVE BoM HEADS (southbrook_version > 1)")

versioned_boms = env["mrp.bom"].search([("southbrook_version", ">", 1)])
groups_v = defaultdict(lambda: env["mrp.bom"])
for b in versioned_boms:
    key = (b.product_tmpl_id.id, b.product_id.id or False, b.type, b.southbrook_version)
    groups_v[key] |= b

found_any_v = False
for key, group in groups_v.items():
    if len(group) <= 1:
        continue
    found_any_v = True
    tmpl_id, product_id, btype, version = key
    print("  group tmpl=%s product=%s type=%s version=%s -> %d active heads: %s"
          % (tmpl_id, product_id, btype, version, len(group), group.ids))

    # Survivor = the head referenced by the most recently applied ECO's
    # new_bom_id, falling back to the highest id.
    winning_ecos = env["southbrook.eco"].search(
        [("new_bom_id", "in", group.ids), ("state", "=", "applied")],
        order="applied_date desc, id desc",
        limit=1,
    )
    survivor = winning_ecos.new_bom_id if winning_ecos else env["mrp.bom"]
    if not survivor or survivor not in group:
        survivor = group.sorted(key=lambda r: r.id, reverse=True)[0]
        print("    survivor chosen by fallback (highest id): id=%s (%s)"
              % (survivor.id, survivor.display_name))
    else:
        print("    survivor chosen via applied ECO %s.new_bom_id: id=%s (%s)"
              % (winning_ecos.name, survivor.id, survivor.display_name))

    for bom in (group - survivor):
        archive_bom(bom, version, survivor)

if not found_any_v:
    print("  no duplicate active heads at southbrook_version > 1 found.")


# ---------------------------------------------------------------------
# 4. EXACT-DUPLICATE v1 BoMs (identical non-empty code strings)
# ---------------------------------------------------------------------
hr("STEP 4 -- EXACT-DUPLICATE v1 BoMs (identical code)")

v1_boms = env["mrp.bom"].search([("southbrook_version", "=", 1)])
groups_c = defaultdict(lambda: env["mrp.bom"])
for b in v1_boms:
    if not b.code:
        continue
    key = (b.product_tmpl_id.id, b.product_id.id or False, b.type, b.code)
    groups_c[key] |= b

found_any_c = False
for key, group in groups_c.items():
    if len(group) <= 1:
        continue
    found_any_c = True
    tmpl_id, product_id, btype, code = key
    survivor = group.sorted(key=lambda r: r.id, reverse=True)[0]
    print("  group tmpl=%s product=%s type=%s code=%r -> %d identical-code heads: %s"
          " -> keep highest id=%s"
          % (tmpl_id, product_id, btype, code, len(group), group.ids, survivor.id))
    for bom in (group - survivor):
        archive_bom(bom, 1, survivor)

if not found_any_c:
    print("  no exact-duplicate-code v1 BoMs found.")


# ---------------------------------------------------------------------
# 5. TEST ECOs IN APPLIED HISTORY
# ---------------------------------------------------------------------
hr("STEP 5 -- TEST ECOs IN APPLIED HISTORY (%s)" % ", ".join(TEST_ECO_TITLES))

test_ecos = env["southbrook.eco"].search([("title", "in", TEST_ECO_TITLES)])
if not test_ecos:
    print("  none found.")
for eco in test_ecos:
    print("  identity: id=%s name=%s title=%r state=%s stage=%s create_uid=%s"
          " applied_date=%s"
          % (
              eco.id, eco.name, eco.title, eco.state,
              eco.stage_id.name if eco.stage_id else None,
              eco.create_uid.name if eco.create_uid else None,
              eco.applied_date,
          ))
    # Defensive fallback (explicitly required): this script runs BEFORE the
    # hardening module upgrade adds unlink guards on southbrook.eco, but if
    # an unlink still raises for any reason, note it and move on rather than
    # aborting the whole reconciliation run.
    try:
        eco_id, eco_name, eco_title = eco.id, eco.name, eco.title
        eco.unlink()
    except Exception as exc:  # noqa: BLE001
        print("  [WARN] unlink failed for ECO id=%s (%s): %s -- noting in "
              "ledger, record left in place." % (eco.id, eco.name, exc))
        ledger["errors"].append(
            "ECO id=%s (%s) unlink failed: %s" % (eco.id, eco.name, exc)
        )
        continue
    print("  [%s] unlinked ECO id=%s (%s) title=%r"
          % (VERB, eco_id, eco_name, eco_title))
    ledger["deleted_ecos"].append({"id": eco_id, "name": eco_name, "title": eco_title})


# ---------------------------------------------------------------------
# 6. ECO/2026/0115 -- saved bom-kind draft with EMPTY bom_id
# ---------------------------------------------------------------------
hr("STEP 6 -- ECO/2026/0115 (empty Target BoM draft)")

target_eco = env["southbrook.eco"].search([("name", "=", "ECO/2026/0115")], limit=1)
# 2026-08-02 — John's verdict on the NEEDS-HUMAN flag: DELETE. The record
# carries a realistic title ("Add lazy-susan exclusion to Corner BoM") but was
# created by __system__ on 2026-06-01, the same demo-walkthrough batch that
# produced the other fixtures — it is a walkthrough prop, not real engineering
# work. If the lazy-susan exclusion is still wanted, it gets re-raised cleanly
# under the new governance with the Corner BoM actually set as target.
ECO_0115_VERDICT_DELETE = True
if not target_eco:
    print("  ECO/2026/0115 not found (already handled, or never existed). Nothing to do.")
else:
    title = target_eco.title or ""
    description = target_eco.description or ""
    combined = "%s %s" % (title, description)
    if ECO_0115_VERDICT_DELETE or TESTISH_RE.search(combined):
        print("  ECO %s (id=%s) title=%r matches a test-ish pattern -> unlinking."
              % (target_eco.name, target_eco.id, target_eco.title))
        try:
            eco_id, eco_name, eco_title = target_eco.id, target_eco.name, target_eco.title
            target_eco.unlink()
        except Exception as exc:  # noqa: BLE001
            print("  [ERROR] unlink failed for ECO %s (id=%s): %s"
                  % (target_eco.name, target_eco.id, exc))
            raise
        print("  [%s] unlinked ECO id=%s (%s) title=%r"
              % (VERB, eco_id, eco_name, eco_title))
        ledger["deleted_ecos"].append({"id": eco_id, "name": eco_name, "title": eco_title})
    else:
        create_uid_name = target_eco.create_uid.name if target_eco.create_uid else None
        print("  ECO %s (id=%s) does NOT match test/verify/demo/chatter patterns."
              " Leaving it in place -- flagged NEEDS-HUMAN." % (target_eco.name, target_eco.id))
        print("    title      : %r" % target_eco.title)
        print("    description: %r" % description[:500])
        print("    create_uid : %s" % create_uid_name)
        ledger["needs_human"].append(
            "ECO %s (id=%s), title=%r, created by %s -- saved bom-kind draft "
            "with an EMPTY Target BoM that traps the form. John must either "
            "set its Target BoM or delete it; this script will not guess."
            % (target_eco.name, target_eco.id, target_eco.title, create_uid_name)
        )


# ---------------------------------------------------------------------
# 7. DEMO RECORDS
# ---------------------------------------------------------------------
hr("STEP 7 -- DEMO RECORDS")


def delete_demo_record(xmlid, is_cut_spec=False):
    rec = env.ref(xmlid, raise_if_not_found=False)
    if not rec:
        print("  %s -> not found (already removed, or demo data not loaded). Skipping."
              % xmlid)
        return
    if is_cut_spec and rec.state == "active":
        print("  %s (id=%s) is an ACTIVE cut spec -- NOT unlinking." % (xmlid, rec.id))
        ledger["needs_human"].append(
            "Demo cut spec %s (id=%s) is state=active -- left in place. "
            "Deleting the live geometry source would change cut output; "
            "needs a human decision (activate a real spec via ECO first, "
            "then re-run this step)." % (xmlid, rec.id)
        )
        return
    label = getattr(rec, "title", None) or getattr(rec, "name", None)
    try:
        rec_id, rec_model = rec.id, rec._name
        rec.unlink()
    except Exception as exc:  # noqa: BLE001
        print("  [ERROR] unlink failed for %s (id=%s): %s" % (xmlid, rec.id, exc))
        raise
    print("  [%s] unlinked %s -> %s id=%s (%r)" % (VERB, xmlid, rec_model, rec_id, label))

    module, _, name = xmlid.partition(".")
    imd = env["ir.model.data"].search(
        [("module", "=", module), ("name", "=", name)], limit=1
    )
    if imd:
        try:
            imd.unlink()
        except Exception as exc:  # noqa: BLE001
            print("    [ERROR] failed removing ir.model.data row for %s: %s"
                  % (xmlid, exc))
            raise
        print("    + removed ir.model.data row for %s" % xmlid)
    else:
        print("    (no ir.model.data row found for %s -- already gone)" % xmlid)

    ledger["deleted_demo_xmlids"].append(xmlid)


print("-- ECOs first --")
for xmlid in DEMO_ECO_XMLIDS:
    delete_demo_record(xmlid, is_cut_spec=False)

print("-- then cut specs --")
for xmlid in DEMO_CUT_SPEC_XMLIDS:
    delete_demo_record(xmlid, is_cut_spec=True)

print("-- prevent demo reload on future -u --")
try:
    env.cr.execute(
        "UPDATE ir_module_module SET demo = false"
        " WHERE name = 'southbrook_plm' AND demo = true"
    )
    demo_rowcount = env.cr.rowcount
except Exception as exc:  # noqa: BLE001
    print("  [ERROR] UPDATE ir_module_module failed: %s" % exc)
    raise
print("  [%s] UPDATE ir_module_module SET demo=false WHERE name='southbrook_plm'"
      " -> %d row(s) affected" % (VERB, demo_rowcount))
ledger["demo_flag_rowcount"] = demo_rowcount


# ---------------------------------------------------------------------
# 8. CONSISTENCY REPORT (read-only, reflects post-repair in-transaction state)
# ---------------------------------------------------------------------
hr("STEP 8 -- CONSISTENCY REPORT (read-only)")

active_spec_count = env["southbrook.cut.spec"].search_count([("state", "=", "active")])
print("  active cut specs: %d%s"
      % (active_spec_count,
         "" if active_spec_count == 1 else "   <-- WARNING: expected exactly 1"))

applied_no_approver = env["southbrook.eco"].search(
    [("state", "=", "applied"), ("approver_id", "=", False)]
)
print("  applied ECOs with empty approver_id: %d" % len(applied_no_approver))
for eco in applied_no_approver:
    print("    - id=%s name=%s title=%r applied_date=%s"
          % (eco.id, eco.name, eco.title, eco.applied_date))

remaining_boms = env["mrp.bom"].search([])
coll = defaultdict(lambda: env["mrp.bom"])
for b in remaining_boms:
    key = (b.product_tmpl_id.id, b.product_id.id or False, b.type, b.southbrook_version)
    coll[key] |= b
collisions = {k: v for k, v in coll.items() if len(v) > 1}
print("  remaining active-BoM (tmpl, product, type, version) collisions: %d"
      % len(collisions))
for key, group in collisions.items():
    print("    - tmpl=%s product=%s type=%s version=%s ids=%s"
          % (key[0], key[1], key[2], key[3], group.ids))
    ledger["needs_human"].append(
        "Residual active-BoM collision after reconciliation: tmpl=%s "
        "product=%s type=%s version=%s ids=%s -- needs manual review."
        % (key[0], key[1], key[2], key[3], group.ids)
    )


# ---------------------------------------------------------------------
# 9. ACTION LEDGER SUMMARY + commit/rollback
# ---------------------------------------------------------------------
hr("STEP 9 -- ACTION LEDGER SUMMARY")

print("Archived BoM ids (%d):" % len(ledger["archived_boms"]))
for b in ledger["archived_boms"]:
    print("  - id=%s code=%r" % (b["id"], b["code"]))

print("\nDeleted ECO ids/names (%d):" % len(ledger["deleted_ecos"]))
for e in ledger["deleted_ecos"]:
    print("  - id=%s name=%s title=%r" % (e["id"], e["name"], e["title"]))

print("\nDeleted demo xmlids (%d):" % len(ledger["deleted_demo_xmlids"]))
for x in ledger["deleted_demo_xmlids"]:
    print("  - %s" % x)

print("\nDemo flag change: ir_module_module.demo -> false, %s row(s)"
      % ledger["demo_flag_rowcount"])

print("\nNEEDS-HUMAN items (%d):" % len(ledger["needs_human"]))
for item in ledger["needs_human"]:
    print("  ! %s" % item)

if ledger["errors"]:
    print("\nNon-fatal errors recorded (%d):" % len(ledger["errors"]))
    for err in ledger["errors"]:
        print("  ! %s" % err)

print("\nSnapshot file: %s" % SNAPSHOT_PATH)

hr("MODE: %s" % ("APPLY -- committing" if APPLY else "DRY-RUN -- rolling back"))
if APPLY:
    env.cr.commit()
    print("Committed.")
else:
    env.cr.rollback()
    print("Rolled back -- no changes persisted. Set APPLY = True and re-run to commit.")
