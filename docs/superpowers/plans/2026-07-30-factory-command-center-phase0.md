# Factory Command Center — Phase 0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop `southbrook_command_center` manufacturing 2,600 duplicate exceptions a day, stop its health score inventing points from metrics with no observations, and make its numbers identical to Process Explorer's — before any visual rebuild.

**Architecture:** Add a stable `dedupe_key` to `southbrook.command.exception` and key the upsert on it instead of the churning `southbrook.mi.check` row id. A post-migration elects one survivor per live MI condition and archives the rest with a NULL key (NULLs don't collide in a Postgres unique index). A new soft-detect bridge model reads `mrp.process.explorer.get_data()` so on-time delivery and work-center counts come from the same query Process Explorer renders. The health score is rewritten to score out of *available* points, marking components with no observations "no data" rather than 0 or 100.

**Tech Stack:** Odoo 19.0 CE, Python 3.11, OWL 2, `TransactionCase` tests, Postgres 16.

## Global Constraints

- Odoo **19.0 CE** only. No Enterprise models.
- `models.Constraint`, never the legacy `_sql_constraints` (Odoo 19 silently ignores it).
- Never add `@api.depends` against a foreign model in this addon — a wrong field name silently breaks the registry on this deployment.
- `res.users` group field is `group_ids`, never the removed `groups_id`.
- SPDX header `# SPDX-License-Identifier: LGPL-3.0-only` on every new Python file.
- Module version goes **19.0.2.0.0 → 19.0.3.0.0** in this plan; the migration directory must match exactly.
- Every panel/query stays wrapped so one failure degrades that panel, never 500s the dashboard.
- Read-only against business objects. This addon writes only `southbrook.command.exception`.
- **Never run tests against the live `southbrook` database on the QNAP.**

## Preconditions

- [ ] **Start the local harness**

```bash
open -a OrbStack && sleep 20 && docker ps --format '{{.Names}}' | grep sami-odoo
```

Expected: `sami-odoo` listed. `sami-odoo` mounts `~/southbrook-v19cr/addons → /mnt/extra-addons`, so the working tree is what runs.

- [ ] **Record the pre-change test baseline**

```bash
docker exec sami-odoo sh -lc "odoo -c /etc/odoo/odoo.conf \
  --db_host=db --db_user=odoo --db_password=change-me-strong-password \
  -d southbrook -u southbrook_command_center --test-enable --stop-after-init \
  --http-port=8999 --max-cron-threads=0" 2>&1 | grep -E "odoo.tests.result"
```

Expected: a line like `N failed, M error(s) of T tests`. **Write that line down.** Exit codes are meaningless here — a run with failures still exits 0, and an exit-0 run has twice executed zero tests. Always compare the failing-test *set* against this baseline.

---

### Task 1: Stable dedupe key on the exception model

The keying bug in one sentence: the uniqueness rule is `unique(source_model, source_res_id, exception_type)` where `source_res_id` is a `southbrook.mi.check` row id, and the MI sweep cron deletes and recreates those rows every 15 minutes, so the key never matches twice.

**Files:**
- Modify: `addons/southbrook_command_center/models/southbrook_command_exception.py:231-234` (replace the constraint), plus new field and helper
- Modify: `addons/southbrook_command_center/models/command_center_materialize.py:59-92` (`_upsert_exception`)
- Test: `addons/southbrook_command_center/tests/test_dedupe_key.py`

**Interfaces:**
- Produces: `southbrook.command.exception.dedupe_key` (Char, indexed, nullable); `_dedupe_key_for(exception_type, source_model, source_res_id, source_record=None) -> str`; `_upsert_exception(..., dedupe_key=None)` — when `dedupe_key` is omitted it falls back to the legacy triple so existing callers keep working.

- [ ] **Step 1: Write the failing test**

Create `addons/southbrook_command_center/tests/test_dedupe_key.py`:

```python
# SPDX-License-Identifier: LGPL-3.0-only
"""The keying contract: one exception per live business condition, no matter
how many times the source row is deleted and recreated underneath it."""
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "southbrook_command_center")
class TestDedupeKey(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Exc = cls.env["southbrook.command.exception"]
        cls.owner = cls.env.ref("base.user_admin")

    def _vals(self, **kw):
        v = {
            "severity": "critical", "severity_rank": 0,
            "owner_id": self.owner.id,
            "impact_summary": "Missing cutlist",
            "recommended_action": "Link the cutlist",
            "why_text": "no cutlist on the MO",
        }
        v.update(kw)
        return v

    def test_recreated_source_rows_do_not_duplicate(self):
        """The sweep deletes and recreates mi.check rows; ids churn. The same
        business condition must resolve to ONE exception row."""
        key = "mi:42:cut:missing cutlist:cutlist_divergence"
        first = self.Exc._upsert_exception(
            "cutlist_divergence", "southbrook.mi.check", 900001,
            self._vals(), dedupe_key=key)
        second = self.Exc._upsert_exception(
            "cutlist_divergence", "southbrook.mi.check", 900002,
            self._vals(impact_summary="Missing cutlist"), dedupe_key=key)
        self.assertEqual(first.id, second.id,
                         "a recreated source row must upsert, not duplicate")

    def test_source_res_id_tracks_the_live_row(self):
        """Click-through must open a row that still exists."""
        key = "mi:43:cut:missing cutlist:cutlist_divergence"
        exc = self.Exc._upsert_exception(
            "cutlist_divergence", "southbrook.mi.check", 900010,
            self._vals(), dedupe_key=key)
        self.Exc._upsert_exception(
            "cutlist_divergence", "southbrook.mi.check", 900011,
            self._vals(), dedupe_key=key)
        exc.invalidate_recordset()
        self.assertEqual(exc.source_res_id, 900011,
                         "source_res_id must point at the live source row")

    def test_distinct_conditions_stay_distinct(self):
        a = self.Exc._upsert_exception(
            "cutlist_divergence", "southbrook.mi.check", 900020,
            self._vals(), dedupe_key="mi:50:cut:missing cutlist:cutlist_divergence")
        b = self.Exc._upsert_exception(
            "cutlist_divergence", "southbrook.mi.check", 900021,
            self._vals(), dedupe_key="mi:51:cut:missing cutlist:cutlist_divergence")
        self.assertNotEqual(a.id, b.id, "different MOs are different conditions")

    def test_legacy_callers_without_key_still_work(self):
        a = self.Exc._upsert_exception(
            "breakdown_alert", "southbrook.cmms.breakdown_alert", 900030,
            self._vals())
        b = self.Exc._upsert_exception(
            "breakdown_alert", "southbrook.cmms.breakdown_alert", 900030,
            self._vals(impact_summary="updated"))
        self.assertEqual(a.id, b.id)
        self.assertEqual(a.impact_summary, "updated")

    def test_dedupe_key_for_mi_check_is_stable_across_ids(self):
        Check = self.env["southbrook.mi.check"]
        rec = Check.new({"name": "Missing cutlist", "category": "cut"})
        k1 = self.Exc._dedupe_key_for(
            "cutlist_divergence", "southbrook.mi.check", 111, rec)
        k2 = self.Exc._dedupe_key_for(
            "cutlist_divergence", "southbrook.mi.check", 222, rec)
        self.assertEqual(k1, k2, "the key must not contain the row id")
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
docker exec sami-odoo sh -lc "odoo -c /etc/odoo/odoo.conf \
  --db_host=db --db_user=odoo --db_password=change-me-strong-password \
  -d southbrook -u southbrook_command_center --test-enable --stop-after-init \
  --http-port=8999 --max-cron-threads=0 \
  --test-tags /southbrook_command_center:TestDedupeKey" 2>&1 | grep -E "odoo.tests.result|TypeError|FAIL"
```

Expected: failures — `_upsert_exception() got an unexpected keyword argument 'dedupe_key'` and `_dedupe_key_for` not defined.

- [ ] **Step 3: Add the field, the key helper, and swap the constraint**

In `models/southbrook_command_exception.py`, add near `alert_notified` (around line 228):

```python
    # ------------------------------------------------------------------
    # Stable business identity (2026-07-30).
    #
    # The original uniqueness rule keyed on (source_model, source_res_id,
    # exception_type). source_res_id is a southbrook.mi.check row id, and the
    # MI sweep cron DELETES AND RECREATES those rows every 15 minutes — so the
    # ids churn, the key never matches twice, and every sweep minted a fresh
    # exception. Measured 2026-07-30: 38,910 open rows tracing to 9 MOs and 17
    # live checks, growing ~2,600/day.
    #
    # dedupe_key holds what is actually stable about the condition (which MO,
    # which category, which check), so a re-fire updates one row. Archived
    # historical duplicates carry NULL, and Postgres does not collide NULLs in
    # a unique index — which is what lets them stay in the table for audit.
    # ------------------------------------------------------------------
    dedupe_key = fields.Char(
        index=True,
        copy=False,
        help="Stable identity of the underlying business condition. One open "
             "exception per key. NULL on archived historical duplicates.",
    )
```

Replace the constraint at lines 231-234:

```python
    _dedupe_key_uniq = models.Constraint(
        "unique(dedupe_key)",
        "An exception already exists for this business condition.",
    )
```

Add the key helper as a method on the same class:

```python
    @api.model
    def _dedupe_key_for(self, exception_type, source_model, source_res_id,
                        source_record=None):
        """Stable identity for the condition this exception describes.

        For southbrook.mi.check the row id is worthless as an identity (the
        sweep recreates rows), so the key is built from what survives a sweep:
        the manufacturing order, the check category, and the check name.
        Every other source has stable ids and keeps the legacy triple.
        """
        if source_model == "southbrook.mi.check" and source_record is not None:
            mo = getattr(source_record, "production_id", False)
            mo_part = str(mo.id) if mo else "0"
            category = (getattr(source_record, "category", "") or "").strip().lower()
            name = (getattr(source_record, "name", "") or "").strip().lower()[:120]
            return "mi:%s:%s:%s:%s" % (mo_part, category, name, exception_type)
        return "%s:%s:%s" % (source_model, source_res_id, exception_type)
```

- [ ] **Step 4: Key the upsert on it**

Replace `_upsert_exception` in `models/command_center_materialize.py` (lines 59-92):

```python
    def _upsert_exception(self, exception_type, source_model, source_res_id, vals,
                          reactivate=False, dedupe_key=None):
        """Idempotent create-or-update keyed on the stable business identity.

        On an EXISTING OPEN row only the narrative (severity/rank/impact/
        action/why) is refreshed — owner and workflow state set by a human are
        preserved. A resolved/dismissed row is reactivated ONLY when
        ``reactivate=True`` (event hooks: the source defect genuinely
        re-fired). The cron scan passes ``reactivate=False`` so it never undoes
        a human's resolution of a still-present blocker.

        ``source_res_id`` is refreshed to the live source row on every upsert,
        so click-through always opens a record that still exists — before this,
        18,240 rows pointed at deleted mi.check ids.
        """
        Exc = self.sudo().with_context(active_test=False)
        key = dedupe_key or self._dedupe_key_for(
            exception_type, source_model, source_res_id)
        existing = Exc.search([("dedupe_key", "=", key)], limit=1)
        if not existing:
            # Pre-migration rows have no key yet; fall back to the legacy triple
            # so an upgrade mid-flight never duplicates.
            existing = Exc.search([
                ("source_model", "=", source_model),
                ("source_res_id", "=", source_res_id),
                ("exception_type", "=", exception_type),
                ("dedupe_key", "=", False),
            ], limit=1)
        narrative = {k: vals[k] for k in
                     ("severity", "severity_rank", "impact_summary",
                      "recommended_action", "why_text") if k in vals}
        if existing:
            narrative["dedupe_key"] = key
            narrative["source_res_id"] = source_res_id
            if not existing.active and reactivate:
                existing.write({**narrative, "active": True, "state": "new"})
            else:
                existing.write(narrative)
            return existing
        create_vals = dict(vals)
        create_vals.update({
            "exception_type": exception_type,
            "source_model": source_model,
            "source_res_id": source_res_id,
            "dedupe_key": key,
        })
        return Exc.create(create_vals)
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
docker exec sami-odoo sh -lc "odoo -c /etc/odoo/odoo.conf \
  --db_host=db --db_user=odoo --db_password=change-me-strong-password \
  -d southbrook -u southbrook_command_center --test-enable --stop-after-init \
  --http-port=8999 --max-cron-threads=0 \
  --test-tags /southbrook_command_center:TestDedupeKey" 2>&1 | grep -E "odoo.tests.result"
```

Expected: `0 failed, 0 error(s) of 5 tests`.

- [ ] **Step 6: Confirm the existing suite still passes**

```bash
docker exec sami-odoo sh -lc "odoo -c /etc/odoo/odoo.conf \
  --db_host=db --db_user=odoo --db_password=change-me-strong-password \
  -d southbrook -u southbrook_command_center --test-enable --stop-after-init \
  --http-port=8999 --max-cron-threads=0" 2>&1 | grep -E "odoo.tests.result"
```

Expected: the same failing-test *set* as the baseline. `test_phase2.py` calls `_upsert_exception` without a key and must still pass.

- [ ] **Step 7: Commit**

```bash
git add addons/southbrook_command_center/models/southbrook_command_exception.py \
        addons/southbrook_command_center/models/command_center_materialize.py \
        addons/southbrook_command_center/tests/test_dedupe_key.py
git commit -m "fix(command_center): key exceptions on stable identity, not churning row ids

The MI sweep cron deletes and recreates southbrook.mi.check rows every
15 min, so the unique(source_model, source_res_id, exception_type) rule
never matched twice and every sweep minted a fresh exception. Measured
2026-07-30: 38,910 open rows from 9 MOs and 17 live checks, +2,600/day.

dedupe_key holds what survives a sweep (MO + category + check name).
source_res_id is now refreshed to the live row so click-through opens a
record that exists."
```

---

### Task 2: Materializer and hook emit the stable key

**Files:**
- Modify: `addons/southbrook_command_center/models/command_center_materialize.py:145-170` (`_scan_mi_blockers`)
- Modify: `addons/southbrook_command_center/models/mi_check.py:82-84` (`_sb_cc_materialize`)
- Test: `addons/southbrook_command_center/tests/test_dedupe_key.py` (append)

**Interfaces:**
- Consumes: `_dedupe_key_for` and `_upsert_exception(..., dedupe_key=)` from Task 1.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_dedupe_key.py`:

```python
    def test_scan_is_idempotent_across_recreated_checks(self):
        """Two sweeps over the same condition, with the mi.check row deleted
        and recreated between them, must leave ONE exception."""
        Check = self.env["southbrook.mi.check"]
        Exc = self.Exc.with_context(active_test=False)

        def _count(mo_id):
            return Exc.search_count([
                ("source_model", "=", "southbrook.mi.check"),
                ("mo_id", "=", mo_id),
                ("active", "=", True),
            ])

        mo = self.env["mrp.production"].search([], limit=1)
        self.assertTrue(mo, "needs at least one MO in the database")

        first = Check.create({
            "name": "Missing cutlist", "category": "cut",
            "severity": "blocker", "production_id": mo.id,
        })
        self.Exc._scan_and_materialize()
        after_first = _count(mo.id)

        first.unlink()                      # the sweep drops the old row
        Check.create({                      # and recreates the same condition
            "name": "Missing cutlist", "category": "cut",
            "severity": "blocker", "production_id": mo.id,
        })
        self.Exc._scan_and_materialize()

        self.assertEqual(_count(mo.id), after_first,
                         "a recreated check must not add a second exception")
```

- [ ] **Step 2: Run it to verify it fails**

```bash
docker exec sami-odoo sh -lc "odoo -c /etc/odoo/odoo.conf \
  --db_host=db --db_user=odoo --db_password=change-me-strong-password \
  -d southbrook -u southbrook_command_center --test-enable --stop-after-init \
  --http-port=8999 --max-cron-threads=0 \
  --test-tags /southbrook_command_center:TestDedupeKey.test_scan_is_idempotent_across_recreated_checks" 2>&1 | grep -E "odoo.tests.result|AssertionError"
```

Expected: FAIL — the count doubles, because the scan still keys on the row id.

- [ ] **Step 3: Pass the key from the cron scan**

In `models/command_center_materialize.py`, inside `_scan_mi_blockers`, replace the `self._upsert_exception(etype, ...)` call (lines 154-166) with:

```python
                key = self._dedupe_key_for(
                    etype, "southbrook.mi.check", c.id, c)
                self._upsert_exception(etype, "southbrook.mi.check", c.id, {
                    "exception_type": etype,
                    "source_model": "southbrook.mi.check",
                    "source_res_id": c.id,
                    "severity": "critical", "severity_rank": 0,
                    "owner_id": self._default_owner_id(mo or c),
                    "mo_id": mo.id if mo else False,
                    "impact_summary": (c.name or "MI blocker")[:280],
                    "recommended_action": getattr(c, "recommendation", False) or
                        "Review the MI check and clear the blocker.",
                    "why_text": getattr(c, "message", False) or c.name or "",
                    "company_id": self.env.company.id,
                }, dedupe_key=key)
```

- [ ] **Step 4: Pass the key from the create hook**

In `models/mi_check.py`, replace lines 82-84:

```python
        key = Exception._dedupe_key_for(
            exception_type, "southbrook.mi.check", self.id, self)
        exc = Exception._upsert_exception(
            exception_type, "southbrook.mi.check", self.id, vals,
            dedupe_key=key
        )
```

- [ ] **Step 5: Run it to verify it passes**

```bash
docker exec sami-odoo sh -lc "odoo -c /etc/odoo/odoo.conf \
  --db_host=db --db_user=odoo --db_password=change-me-strong-password \
  -d southbrook -u southbrook_command_center --test-enable --stop-after-init \
  --http-port=8999 --max-cron-threads=0 \
  --test-tags /southbrook_command_center:TestDedupeKey" 2>&1 | grep -E "odoo.tests.result"
```

Expected: `0 failed, 0 error(s) of 6 tests`.

- [ ] **Step 6: Commit**

```bash
git add addons/southbrook_command_center/models/command_center_materialize.py \
        addons/southbrook_command_center/models/mi_check.py \
        addons/southbrook_command_center/tests/test_dedupe_key.py
git commit -m "fix(command_center): scan and create hook emit the stable dedupe key"
```

---

### Task 3: Migration — elect survivors, archive the duplicates

**Files:**
- Create: `addons/southbrook_command_center/migrations/19.0.3.0.0/post-dedupe-exceptions.py`
- Modify: `addons/southbrook_command_center/__manifest__.py:32` (version bump)

**Interfaces:**
- Consumes: the `dedupe_key` column from Task 1, which exists by the time a `post-` script runs.

Runs `post-`, not `pre-`: the column must exist, and at that moment every row has `dedupe_key = NULL`, so the new unique index creates cleanly (Postgres does not collide NULLs).

Election rule, per live `southbrook.mi.check`: among exceptions matching its `(exception_type, mo_id)`, prefer a row a human has touched (`state != 'new'`), then the oldest — which preserves first-seen for the cluster card. Every other MI-sourced exception is archived with a NULL key. **An MI-sourced exception with no live check is archived**: if no check exists, the condition has cleared and nothing should stay open.

- [ ] **Step 1: Write the migration**

Create `addons/southbrook_command_center/migrations/19.0.3.0.0/post-dedupe-exceptions.py`:

```python
# SPDX-License-Identifier: LGPL-3.0-only
"""Collapse the duplicate-exception backlog created by the keying bug.

Measured on production 2026-07-30: 38,910 open southbrook.command.exception
rows tracing to 9 manufacturing orders and 17 live southbrook.mi.check rows,
growing ~2,600/day. Cause: the uniqueness rule keyed on the mi.check row id,
which the MI sweep cron recreates every 15 minutes.

This elects one survivor per live MI condition, keys it, and archives the rest
with a NULL dedupe_key (NULLs do not collide in a unique index, so archived
rows stay in the table and stay auditable). Reversible: nothing is deleted.
"""
import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    Exc = env["southbrook.command.exception"].with_context(active_test=False)

    before = Exc.search_count([("source_model", "=", "southbrook.mi.check"),
                               ("active", "=", True)])

    survivors = set()
    for check in env["southbrook.mi.check"].search([]):
        mo = check.production_id
        etype = "cutlist_divergence" if check.category == "cut" else "mi_blocker"
        key = Exc._dedupe_key_for(etype, "southbrook.mi.check", check.id, check)
        if key in {s[1] for s in survivors}:
            continue  # two live checks share an identity — keep the first
        candidates = Exc.search([
            ("source_model", "=", "southbrook.mi.check"),
            ("exception_type", "=", etype),
            ("mo_id", "=", mo.id if mo else False),
        ], order="id asc")
        if not candidates:
            continue
        touched = candidates.filtered(lambda r: r.state != "new")
        winner = touched[0] if touched else candidates[0]
        winner.write({
            "dedupe_key": key,
            "source_res_id": check.id,
            "active": True if winner.state not in ("resolved", "dismissed") else winner.active,
        })
        survivors.add((winner.id, key))

    keep_ids = [s[0] for s in survivors]
    doomed = Exc.search([
        ("source_model", "=", "southbrook.mi.check"),
        ("id", "not in", keep_ids),
    ])
    archived = len(doomed)
    # Written in slices: a single write over ~39k rows on the QNAP is a long
    # transaction against a live database.
    for i in range(0, archived, 2000):
        doomed[i:i + 2000].write({"active": False, "dedupe_key": False})

    # Non-MI sources have stable ids; key them on the legacy triple.
    others = Exc.search([("source_model", "!=", "southbrook.mi.check"),
                         ("dedupe_key", "=", False)])
    for rec in others:
        rec.dedupe_key = Exc._dedupe_key_for(
            rec.exception_type, rec.source_model, rec.source_res_id)

    after = Exc.search_count([("active", "=", True)])
    _logger.warning(
        "command_center dedupe migration: %s MI rows open before, %s survivors "
        "keyed, %s archived, %s non-MI rows keyed, %s open after.",
        before, len(survivors), archived, len(others), after)
```

- [ ] **Step 2: Bump the module version**

In `__manifest__.py` line 32, change `"version": "19.0.2.0.0",` to `"version": "19.0.3.0.0",`.

- [ ] **Step 3: Run the migration against a disposable copy of production**

Never against live. Take a copy on the QNAP first.

**Do not use `WITH TEMPLATE southbrook`** — Postgres refuses to copy a template
that has active sessions, and `southbrook` is a live Odoo with a connection
pool. It fails with `source database "southbrook" is being accessed by other
users`. Dump and restore instead:

```bash
ssh qnap-tunnel 'SD=/share/CACHEDEV3_DATA/.qpkg/container-station/bin/system-docker; \
  $SD exec -i southbrook-postgres psql -U odoo -d postgres -c \
  "CREATE DATABASE southbrook_dedupe_test OWNER odoo;" && \
  $SD exec -i southbrook-postgres sh -c \
  "pg_dump -U odoo -d southbrook | psql -U odoo -q -d southbrook_dedupe_test" && \
  $SD exec -i southbrook-postgres psql -U odoo -d southbrook_dedupe_test -tAc \
  "SELECT count(*) FROM southbrook_command_exception;"'
```

Expected: the restore prints no errors and the count matches production
(~38,917). The copy is ~250 MB; drop it in Step 6 rather than leaving it on the
QNAP, which runs close to its resource ceiling.

Neutralise the copy's crons before upgrading it, so the MI sweep cannot fire
against the test database and re-seed the very duplicates being measured:

```bash
ssh qnap-tunnel 'SD=/share/CACHEDEV3_DATA/.qpkg/container-station/bin/system-docker; \
  $SD exec -i southbrook-postgres psql -U odoo -d southbrook_dedupe_test -c \
  "UPDATE ir_cron SET active = false;"'
```

Then upgrade the copy and read the migration's own log line:

```bash
ssh qnap-tunnel 'SD=/share/CACHEDEV3_DATA/.qpkg/container-station/bin/system-docker; \
  $SD exec southbrook-odoo odoo -c /etc/odoo/odoo.conf -d southbrook_dedupe_test \
  -u southbrook_command_center --stop-after-init --http-port=8999 \
  --max-cron-threads=0 2>&1 | grep "dedupe migration"'
```

Expected: `... MI rows open before, 17 survivors keyed, ~38890 archived, 3 non-MI rows keyed, ~20 open after.`

- [ ] **Step 4: Verify the collapse and that nothing was destroyed**

```bash
ssh qnap-tunnel 'SD=/share/CACHEDEV3_DATA/.qpkg/container-station/bin/system-docker; \
  $SD exec -i southbrook-postgres psql -U odoo -d southbrook_dedupe_test -c \
  "SELECT count(*) AS total, count(*) FILTER (WHERE active) AS open, \
          count(dedupe_key) AS keyed FROM southbrook_command_exception;"'
```

Expected: `total` unchanged from production (~38,917 — nothing deleted), `open` ~20, `keyed` ~20.

- [ ] **Step 5: Verify a sweep on the migrated copy adds nothing**

```bash
ssh qnap-tunnel 'SD=/share/CACHEDEV3_DATA/.qpkg/container-station/bin/system-docker; \
  echo "env[\"southbrook.command.exception\"]._scan_and_materialize()
print(\"OPEN\", env[\"southbrook.command.exception\"].search_count([]))" | \
  $SD exec -i southbrook-odoo odoo shell -c /etc/odoo/odoo.conf \
  -d southbrook_dedupe_test --http-port=8999 2>/dev/null | grep OPEN'
```

Expected: the same ~20. Run it twice; the number must not move.

- [ ] **Step 6: Drop the test copy**

```bash
ssh qnap-tunnel 'SD=/share/CACHEDEV3_DATA/.qpkg/container-station/bin/system-docker; \
  $SD exec -i southbrook-postgres psql -U odoo -d postgres -c \
  "DROP DATABASE southbrook_dedupe_test;"'
```

- [ ] **Step 7: Commit**

```bash
git add addons/southbrook_command_center/migrations/19.0.3.0.0/post-dedupe-exceptions.py \
        addons/southbrook_command_center/__manifest__.py
git commit -m "feat(command_center): 19.0.3.0.0 migration collapsing the duplicate backlog

Elects one survivor per live MI condition, archives the rest with a NULL
dedupe_key. Nothing deleted; archived rows stay auditable and reversible."
```

---

### Task 4: Process Explorer bridge

**Files:**
- Create: `addons/southbrook_command_center/models/explorer_bridge.py`
- Modify: `addons/southbrook_command_center/models/__init__.py`
- Test: `addons/southbrook_command_center/tests/test_explorer_bridge.py`

**Interfaces:**
- Produces: `southbrook.command.explorer_bridge.fetch() -> (payload: dict, provenance: str)`. `payload` is `mrp.process.explorer.get_data()`'s return value, or `{}` when unavailable. `provenance` is a human-readable string naming the actual source, always non-empty.

- [ ] **Step 1: Write the failing test**

Create `addons/southbrook_command_center/tests/test_explorer_bridge.py`:

```python
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
```

- [ ] **Step 2: Run it to verify it fails**

```bash
docker exec sami-odoo sh -lc "odoo -c /etc/odoo/odoo.conf \
  --db_host=db --db_user=odoo --db_password=change-me-strong-password \
  -d southbrook -u southbrook_command_center --test-enable --stop-after-init \
  --http-port=8999 --max-cron-threads=0 \
  --test-tags /southbrook_command_center:TestExplorerBridge" 2>&1 | grep -E "odoo.tests.result|KeyError"
```

Expected: FAIL — `southbrook.command.explorer_bridge` is not in the registry.

- [ ] **Step 3: Write the bridge**

Create `addons/southbrook_command_center/models/explorer_bridge.py`:

```python
# SPDX-License-Identifier: LGPL-3.0-only
"""Single source of factory truth, shared with the MRP Process Explorer.

Command Center and Process Explorer used to compute the same quantities from
different queries and disagree: on 2026-07-30 the dashboard reported 100%
on-time delivery while Process Explorer showed 10 of 13 orders past deadline,
and they counted 14 vs 8 work centers.

Reading Process Explorer's own payload makes agreement structural rather than
tested. The dependency is deliberately SOFT — mrp_process_explorer lives in a
different repository with no settled home, so this must not appear in the
manifest. When it is absent the caller degrades and says so; the screen never
reports numbers from a source other than the one it names.
"""
import logging

from odoo import api, models

_logger = logging.getLogger(__name__)

_PROVENANCE_LIVE = "mrp.process.explorer.get_data() — shared with Process Explorer"


class SouthbrookCommandExplorerBridge(models.AbstractModel):
    _name = "southbrook.command.explorer_bridge"
    _description = "Central Command — shared factory-truth reader"

    @api.model
    def fetch(self):
        """Return ``(payload, provenance)``.

        ``payload`` is Process Explorer's ``get_data()`` result, or ``{}`` when
        it cannot be trusted. ``provenance`` always names the real source.
        """
        Explorer = self.env.get("mrp.process.explorer")
        if Explorer is None:
            return {}, "fallback: mrp_process_explorer is not installed"
        try:
            data = Explorer.sudo().get_data()
        except Exception:  # noqa: BLE001 — degrade, never 500 the dashboard
            _logger.exception("command_center: explorer bridge query failed")
            return {}, "fallback: Process Explorer query failed"
        if not isinstance(data, dict) or not data.get("ok"):
            # `(data or {}).get(...)` would look right here and be wrong: once
            # isinstance() has already failed, a TRUTHY non-dict (a non-empty
            # string, a list, True) makes `data or {}` evaluate to `data`, and
            # .get() on it raises AttributeError OUTSIDE the try above — a 500
            # on an endpoint that runs on every dashboard open.
            reason = (data.get("reason", "unknown") if isinstance(data, dict)
                      else "invalid response type")
            return {}, "fallback: Process Explorer returned no data (%s)" % reason
        return data, _PROVENANCE_LIVE
```

- [ ] **Step 4: Register the model**

Add to `models/__init__.py`, after the existing imports:

```python
from . import explorer_bridge
```

- [ ] **Step 5: Run it to verify it passes**

```bash
docker exec sami-odoo sh -lc "odoo -c /etc/odoo/odoo.conf \
  --db_host=db --db_user=odoo --db_password=change-me-strong-password \
  -d southbrook -u southbrook_command_center --test-enable --stop-after-init \
  --http-port=8999 --max-cron-threads=0 \
  --test-tags /southbrook_command_center:TestExplorerBridge" 2>&1 | grep -E "odoo.tests.result"
```

Expected: `0 failed, 0 error(s) of 3 tests`.

- [ ] **Step 6: Commit**

```bash
git add addons/southbrook_command_center/models/explorer_bridge.py \
        addons/southbrook_command_center/models/__init__.py \
        addons/southbrook_command_center/tests/test_explorer_bridge.py
git commit -m "feat(command_center): soft-detect bridge to mrp.process.explorer

Makes agreement between the two screens structural rather than tested."
```

---

### Task 5: Health score out of available points

Today the score reads 55/100 red: OEE banks 0 of 30 with no snapshots behind it (absent data scored zero) and on-time delivery banks 25 of 25 with **zero** outgoing pickings ever completed (absent data scored perfect — `exec_snapshot.py:197` sets `otd = 100.0` before its guard). Honest read: 10 of 25 available points.

**Files:**
- Modify: `addons/southbrook_command_center/models/command_center.py:64-185` (`factory_health_score`)
- Test: `addons/southbrook_command_center/tests/test_health_available_points.py`

**Interfaces:**
- Consumes: `southbrook.command.explorer_bridge.fetch()` from Task 4.
- Produces: `factory_health_score()` returns `{"score", "available", "coverage_pct", "band", "factors", "explanation", "provenance"}`. Each factor gains `"data_state"` (`"live"` or `"no_data"`) and `"no_data_reason"` (str or `None`). `band` is computed from `score/available`, or `"insufficient"` when `available == 0`.

- [ ] **Step 1: Write the failing test**

Create `addons/southbrook_command_center/tests/test_health_available_points.py`:

```python
# SPDX-License-Identifier: LGPL-3.0-only
"""Absence of data is never a score — in either direction.

A component with no observations must not bank zero (which deflates and is at
least visible) and must not bank full marks (which inflates and is invisible).
"""
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "southbrook_command_center")
class TestHealthAvailablePoints(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.CC = cls.env["southbrook.command.center"]

    def test_shape(self):
        h = self.CC.factory_health_score()
        for key in ("score", "available", "coverage_pct", "band",
                    "factors", "provenance"):
            self.assertIn(key, h)

    def test_every_factor_declares_its_data_state(self):
        h = self.CC.factory_health_score()
        for f in h["factors"]:
            self.assertIn(f["data_state"], ("live", "no_data"), f["label"])
            if f["data_state"] == "no_data":
                self.assertTrue(f["no_data_reason"],
                                "a no-data factor must say why: %s" % f["label"])

    def test_no_data_factors_are_excluded_from_the_denominator(self):
        h = self.CC.factory_health_score()
        expected = sum(f["of"] for f in h["factors"] if f["data_state"] == "live")
        self.assertEqual(h["available"], expected,
                         "no-data components must not sit in the denominator")

    def test_no_data_factors_score_neither_zero_nor_full(self):
        h = self.CC.factory_health_score()
        for f in h["factors"]:
            if f["data_state"] == "no_data":
                self.assertIsNone(f["points"],
                                  "no-data must be None, not 0 and not full marks")

    def test_score_never_exceeds_available(self):
        h = self.CC.factory_health_score()
        self.assertLessEqual(h["score"], h["available"])

    def test_band_is_insufficient_when_nothing_is_measurable(self):
        h = self.CC.factory_health_score()
        if h["available"] == 0:
            self.assertEqual(h["band"], "insufficient")
        else:
            self.assertIn(h["band"], ("green", "amber", "red"))

    def test_on_time_delivery_is_not_perfect_without_observations(self):
        """The specific production bug: 0 outgoing pickings scored 25 of 25."""
        otd = next((f for f in self.CC.factory_health_score()["factors"]
                    if "On-Time" in f["label"]), None)
        self.assertIsNotNone(otd)
        picks = self.env["stock.picking"].search_count([
            ("state", "=", "done"), ("picking_type_code", "=", "outgoing")])
        if picks == 0:
            self.assertEqual(otd["data_state"], "no_data")
```

- [ ] **Step 2: Run it to verify it fails**

```bash
docker exec sami-odoo sh -lc "odoo -c /etc/odoo/odoo.conf \
  --db_host=db --db_user=odoo --db_password=change-me-strong-password \
  -d southbrook -u southbrook_command_center --test-enable --stop-after-init \
  --http-port=8999 --max-cron-threads=0 \
  --test-tags /southbrook_command_center:TestHealthAvailablePoints" 2>&1 | grep -E "odoo.tests.result|KeyError"
```

Expected: FAIL — `KeyError: 'available'` and missing `data_state`.

- [ ] **Step 3: Rewrite the scorer**

Replace `factory_health_score` in `models/command_center.py` (lines 64-185):

```python
    def factory_health_score(self):
        """Score out of AVAILABLE points.

        A component with no observations behind it is marked "no data" and
        excluded from the denominator. Scoring absent data as zero deflates
        (visible); scoring it as perfect inflates (invisible). On 2026-07-30
        this screen did both at once: OEE banked 0 of 30 with no snapshots,
        on-time delivery banked 25 of 25 with zero completed shipments.
        """
        env = self.env
        get = lambda key, default: _get_param(env, f"factory_health.{key}", default)

        w_oee = get("weight_oee", 0.30)
        w_otd = get("weight_otd", 0.25)
        w_fpy = get("weight_fpy", 0.20)
        w_blocker = get("weight_blocker", 0.15)
        w_capacity = get("weight_capacity", 0.10)
        pts_per_blocker = get("blocker_penalty_per_blocker", 15.0)
        band_green_min = get("band_green_min", 80)
        band_amber_min = get("band_amber_min", 60)

        payload, provenance = env["southbrook.command.explorer_bridge"].fetch()

        factors = []

        def _factor(label, of, value, component, source,
                    no_data_reason=None):
            live = no_data_reason is None
            factors.append({
                "label": label,
                "value": value if live else None,
                "points": round(of / 100.0 * component, 1) if live else None,
                "of": of,
                "source": source,
                "data_state": "live" if live else "no_data",
                "no_data_reason": no_data_reason,
            })

        # --- OEE: no data unless a snapshot actually exists ---
        oee_reason = None
        oee_component = 0.0
        Snap = env.get("southbrook.mes_mps.oee_snapshot")
        if Snap is None or Snap.sudo().search_count([]) == 0:
            oee_reason = "no OEE snapshots recorded"
        else:
            try:
                tiles = env["southbrook.mes_mps.mi_tiles"].new({})
                oee_component = _clamp((tiles.avg_oee_last_7d or 0.0) * 100)
            except Exception:  # noqa: BLE001
                oee_reason = "OEE tiles unavailable"
        _factor("OEE (7d avg)", w_oee * 100,
                f"{oee_component:.0f}%", oee_component,
                "southbrook.mes_mps.oee_snapshot", oee_reason)

        # --- On-time delivery: read Process Explorer's own measure ---
        ontime = (payload.get("ontime") or {}) if payload else {}
        otd_reason = None
        otd_component = 0.0
        if not payload:
            otd_reason = "Process Explorer payload unavailable"
        elif not ontime.get("total"):
            otd_reason = "no closed orders in the window to measure"
        else:
            otd_component = _clamp(ontime.get("pct") or 0.0)
        _factor("On-Time Delivery", w_otd * 100,
                f"{otd_component:.0f}% of {ontime.get('total') or 0} closed orders",
                otd_component, "mrp.process.explorer.get_data().ontime",
                otd_reason)

        # --- First-pass yield: needs closed orders to have yielded anything ---
        fpy_reason = None
        fpy_component = 0.0
        closed = env["mrp.production"].search_count([("state", "=", "done")])
        if not closed:
            fpy_reason = "no closed manufacturing orders to yield from"
        else:
            try:
                snapshot = env["southbrook.exec_dashboard.snapshot"].get_or_create_today()
                fpy_component = _clamp(snapshot.fpy_pct_7d or 0.0)
            except Exception:  # noqa: BLE001
                fpy_reason = "exec dashboard snapshot unavailable"
        _factor("First-Pass Yield (7d)", w_fpy * 100,
                f"{fpy_component:.0f}%", fpy_component,
                "southbrook.exec_dashboard.snapshot.fpy_pct_7d", fpy_reason)

        # --- Open MI blockers: always measurable ---
        open_blocker_count = env["southbrook.mi.check"].search_count(
            [("severity", "=", "blocker")])
        _factor("Open MI blockers", w_blocker * 100,
                open_blocker_count,
                _clamp(100 - pts_per_blocker * open_blocker_count),
                "southbrook.mi.check (severity=blocker)")

        # --- Capacity: work centers CARRYING WORK, matching Process Explorer.
        # The old denominator was every active work center (14); Process
        # Explorer counts those with open work (8). Same query now.
        stations = (payload.get("workcenters") or []) if payload else []
        cap_reason = None
        cap_component = 0.0
        cap_value = None
        if not payload:
            cap_reason = "Process Explorer payload unavailable"
        elif not stations:
            cap_reason = "no work centers carrying open work"
        else:
            over_days = _get_param(env, "chips.capacity_over_days", 1.0)
            overloaded = sum(1 for s in stations
                             if (s.get("queued_days") or 0) > over_days)
            cap_component = _clamp(100 * (1 - overloaded / len(stations)))
            cap_value = f"{overloaded}/{len(stations)} carrying work"
        _factor("Work centers overloaded", w_capacity * 100,
                cap_value, cap_component,
                "mrp.process.explorer.get_data().workcenters", cap_reason)

        score = round(sum(f["points"] for f in factors if f["points"] is not None))
        available = round(sum(f["of"] for f in factors
                              if f["data_state"] == "live"))
        no_data_points = round(sum(f["of"] for f in factors
                                   if f["data_state"] == "no_data"))

        if not available:
            band = "insufficient"
            coverage_pct = 0
        else:
            pct = score / available * 100
            coverage_pct = round(available)
            band = ("green" if pct >= band_green_min
                    else "amber" if pct >= band_amber_min else "red")

        missing = ", ".join(f["label"] for f in factors
                            if f["data_state"] == "no_data") or "none"
        explanation = (
            f"Factory Health {score} of {available} available points ({band}).\n"
            f" {no_data_points} points are unscored — no data for: {missing}.\n"
            " A component with no observations is never scored 0 and never "
            "scored full marks.\n"
            " Click any line to open the records that produced it."
        )

        return {
            "score": score,
            "available": available,
            "coverage_pct": coverage_pct,
            "band": band,
            "factors": factors,
            "explanation": explanation,
            "provenance": provenance,
        }
```

- [ ] **Step 4: Run it to verify it passes**

```bash
docker exec sami-odoo sh -lc "odoo -c /etc/odoo/odoo.conf \
  --db_host=db --db_user=odoo --db_password=change-me-strong-password \
  -d southbrook -u southbrook_command_center --test-enable --stop-after-init \
  --http-port=8999 --max-cron-threads=0 \
  --test-tags /southbrook_command_center:TestHealthAvailablePoints" 2>&1 | grep -E "odoo.tests.result"
```

Expected: `0 failed, 0 error(s) of 7 tests`.

- [ ] **Step 5: Confirm the older health test still passes or update it deliberately**

`tests/test_command_center.py` asserts the old return shape. Run it:

```bash
docker exec sami-odoo sh -lc "odoo -c /etc/odoo/odoo.conf \
  --db_host=db --db_user=odoo --db_password=change-me-strong-password \
  -d southbrook -u southbrook_command_center --test-enable --stop-after-init \
  --http-port=8999 --max-cron-threads=0 \
  --test-tags /southbrook_command_center:TestCommandCenter" 2>&1 | grep -E "odoo.tests.result|AssertionError"
```

If it asserts `score` is an int 0-100 out of a fixed 100, update those assertions to the new contract — the contract changed on purpose. Do not weaken a test to make it pass; change it to assert the new intent.

- [ ] **Step 6: Commit**

```bash
git add addons/southbrook_command_center/models/command_center.py \
        addons/southbrook_command_center/tests/test_health_available_points.py \
        addons/southbrook_command_center/tests/test_command_center.py
git commit -m "fix(command_center): score health out of available points

OEE banked 0 of 30 with no snapshots (absent data scored zero); on-time
delivery banked 25 of 25 with zero completed shipments (absent data
scored perfect). Components without observations are now marked no-data
and excluded from the denominator. OTD and the work-center denominator
now read Process Explorer's payload, which also closes the 100%-on-time
vs 10-of-13-late disagreement."
```

---

### Task 6: Serve the reconciled numbers to the client

**Files:**
- Modify: `addons/southbrook_command_center/controllers/main.py:74-88` (`bootstrap`), `:161-182` (`_flow`)
- Test: `addons/southbrook_command_center/tests/test_bootstrap_chips.py`

**Interfaces:**
- Consumes: `explorer_bridge.fetch()` (Task 4), `factory_health_score()` (Task 5).
- Produces: bootstrap payload gains `"chips"` — a list of `{"key", "label", "value", "unit"}` for the eight truth chips — and `"provenance"` (str).

Chip units are explicit because `orders` counts manufacturing orders while `running`/`ready`/`blocked` count work orders. In one row without units they read as parts of one total, and 1 + 36 + 57 ≠ 13.

- [ ] **Step 1: Write the failing test**

Create `addons/southbrook_command_center/tests/test_bootstrap_chips.py`:

```python
# SPDX-License-Identifier: LGPL-3.0-only
"""The chips must be Process Explorer's numbers, and must carry their units."""
from odoo.tests import TransactionCase, tagged

CHIP_KEYS = ["orders", "running", "ready", "blocked", "late", "at_risk",
             "material_short", "capacity_over"]


@tagged("post_install", "-at_install", "southbrook_command_center")
class TestBootstrapChips(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Bridge = cls.env["southbrook.command.explorer_bridge"]
        cls.CC = cls.env["southbrook.command.center"]

    def test_eight_chips_with_units(self):
        chips = self.CC.truth_chips()
        self.assertEqual([c["key"] for c in chips], CHIP_KEYS)
        for c in chips:
            self.assertTrue(c["unit"], "every chip states its unit: %s" % c["key"])

    def test_chip_values_equal_the_explorer_payload(self):
        payload, _prov = self.Bridge.fetch()
        if not payload:
            self.skipTest("Process Explorer payload unavailable here")
        workload = payload["workload"]
        chips = {c["key"]: c["value"] for c in self.CC.truth_chips()}
        self.assertEqual(chips["orders"], workload["orders"])
        self.assertEqual(chips["running"], workload["progress"])
        self.assertEqual(chips["ready"], workload["ready"])
        self.assertEqual(chips["blocked"], workload["blocked"])
        self.assertEqual(chips["late"], workload["late"])
        self.assertEqual(chips["at_risk"], workload["at_risk"])

    def test_chips_degrade_to_none_not_zero(self):
        """With no payload a chip is unknown, not zero — zero is a claim."""
        chips = self.CC.truth_chips(payload={})
        for c in chips:
            self.assertIsNone(c["value"])
```

- [ ] **Step 2: Run it to verify it fails**

```bash
docker exec sami-odoo sh -lc "odoo -c /etc/odoo/odoo.conf \
  --db_host=db --db_user=odoo --db_password=change-me-strong-password \
  -d southbrook -u southbrook_command_center --test-enable --stop-after-init \
  --http-port=8999 --max-cron-threads=0 \
  --test-tags /southbrook_command_center:TestBootstrapChips" 2>&1 | grep -E "odoo.tests.result|AttributeError"
```

Expected: FAIL — `truth_chips` not defined.

- [ ] **Step 3: Add the chip builder**

Append to the `SouthbrookCommandCenter` class in `models/command_center.py`:

```python
    # ------------------------------------------------------------------
    # 5. Truth chips — the pinned context bar
    # ------------------------------------------------------------------
    def truth_chips(self, payload=None):
        """The eight click-to-filter counts, read from Process Explorer.

        Units are explicit: ``orders`` counts manufacturing orders while
        ``running``/``ready``/``blocked`` count work orders. Rendered in one
        row without units they imply a total that does not exist.

        A chip with no payload behind it is ``None``, never 0 — zero is a
        claim about the factory, and we would not have grounds for it.
        """
        if payload is None:
            payload, _prov = self.env["southbrook.command.explorer_bridge"].fetch()
        workload = (payload or {}).get("workload") or {}
        rows = (payload or {}).get("rows") or []
        stations = (payload or {}).get("workcenters") or []
        have = bool(payload)
        over_days = _get_param(self.env, "chips.capacity_over_days", 1.0)

        def _v(value):
            return value if have else None

        return [
            {"key": "orders", "label": "orders", "unit": "orders",
             "value": _v(workload.get("orders", 0))},
            {"key": "running", "label": "running", "unit": "steps",
             "value": _v(workload.get("progress", 0))},
            {"key": "ready", "label": "ready", "unit": "steps",
             "value": _v(workload.get("ready", 0))},
            {"key": "blocked", "label": "blocked", "unit": "steps",
             "value": _v(workload.get("blocked", 0))},
            {"key": "late", "label": "late", "unit": "orders",
             "value": _v(workload.get("late", 0))},
            {"key": "at_risk", "label": "at-risk", "unit": "orders",
             "value": _v(workload.get("at_risk", 0))},
            {"key": "material_short", "label": "material-short", "unit": "orders",
             "value": _v(sum(1 for r in rows if r.get("mat_short")))},
            {"key": "capacity_over", "label": "capacity-over", "unit": "stations",
             "value": _v(sum(1 for s in stations
                             if (s.get("queued_days") or 0) > over_days))},
        ]
```

- [ ] **Step 4: Serve chips and provenance from bootstrap**

In `controllers/main.py`, replace the `bootstrap` return dict (lines 79-88):

```python
        payload, provenance = env["southbrook.command.explorer_bridge"].fetch()
        return {
            "company_id": cid,
            "role": role,
            "provenance": provenance,
            "chips": self._chips(env, payload),
            "factory_health": self._factory_health(env),
            "flow": self._flow(env, cid),
            "exceptions": self._exceptions(env, cid, role),
            "exceptions_total": self._exceptions_total(env, cid, role),
            "recommendations": self._recommendations(env, cid),
            "alerts": self._alerts(env, cid),
        }
```

And add the panel method beside the other `_panel` helpers:

```python
    # -- context bar ---------------------------------------------------
    def _chips(self, env, payload):
        try:
            return env["southbrook.command.center"].truth_chips(payload=payload)
        except Exception:  # noqa: BLE001
            _logger.exception("command_center: chips panel failed")
            return []
```

- [ ] **Step 5: Run it to verify it passes**

```bash
docker exec sami-odoo sh -lc "odoo -c /etc/odoo/odoo.conf \
  --db_host=db --db_user=odoo --db_password=change-me-strong-password \
  -d southbrook -u southbrook_command_center --test-enable --stop-after-init \
  --http-port=8999 --max-cron-threads=0 \
  --test-tags /southbrook_command_center:TestBootstrapChips" 2>&1 | grep -E "odoo.tests.result"
```

Expected: `0 failed, 0 error(s) of 3 tests`.

- [ ] **Step 6: Commit**

```bash
git add addons/southbrook_command_center/models/command_center.py \
        addons/southbrook_command_center/controllers/main.py \
        addons/southbrook_command_center/tests/test_bootstrap_chips.py
git commit -m "feat(command_center): serve the eight truth chips from the shared payload

Chips carry explicit units — orders count MOs, running/ready/blocked
count work orders, and one unlabelled row implies a total that does not
exist. A chip without a payload is null, never zero."
```

---

### Task 7: Rename to Factory Command Center

**Files:**
- Modify: `addons/southbrook_command_center/views/command_center_menus.xml`
- Modify: `addons/southbrook_command_center/__manifest__.py` (name/summary)

- [ ] **Step 1: Rename the action and menu**

In `views/command_center_menus.xml`, change the `ir.actions.client` record's `name` field from `Central Command` to `Factory Command Center`, and the menu item that points at it from `Dashboard` to `Factory Command Center`.

Leave the action **tag** (`southbrook_command_center.central_command`) and the XML ids untouched — the tag is the JS registry key and the xmlid is referenced by `ir.model.data`; renaming either breaks the client action and any saved link.

- [ ] **Step 2: Update the module name**

In `__manifest__.py`, change `"name"` to `"Factory Command Center"` and refresh `"summary"` to describe the four-zone lens screen.

- [ ] **Step 3: Verify the rename applied and the action still resolves**

```bash
docker exec sami-odoo sh -lc "odoo -c /etc/odoo/odoo.conf \
  --db_host=db --db_user=odoo --db_password=change-me-strong-password \
  -d southbrook -u southbrook_command_center --stop-after-init \
  --http-port=8999 --max-cron-threads=0" 2>&1 | tail -3
docker exec sami-odoo sh -lc "PGPASSWORD=change-me-strong-password psql -h db -U odoo \
  -d southbrook -tAc \"SELECT id, name->>'en_US', tag FROM ir_act_client WHERE id = 1512;\""
```

Expected: `1512|Factory Command Center|southbrook_command_center.central_command`.

- [ ] **Step 4: Commit**

```bash
git add addons/southbrook_command_center/views/command_center_menus.xml \
        addons/southbrook_command_center/__manifest__.py
git commit -m "feat(command_center): rename Central Command to Factory Command Center

Action tag and xmlids deliberately unchanged — the tag is the JS registry
key and the xmlid is referenced by ir.model.data."
```

---

### Task 8: Display quick wins

**Files:**
- Modify: `addons/southbrook_command_center/static/src/js/central_command.esm.js`
- Modify: `addons/southbrook_command_center/static/src/xml/central_command.xml`
- Modify: `addons/southbrook_command_center/controllers/main.py` (`_recommendations`)

Four changes, each independently visible:

1. **Grouping on by default** — `groupBy: "exception_type"` in the initial `useState` instead of `""`.
2. **Bulk actions** — a selection checkbox per group header that acknowledges or resolves every row in the group in one ORM call.
3. **Technical detail behind a disclosure** — `why_text` currently renders the raw `southbrook.mi.check <name>: category=… severity=… message=…` string inline. Move it inside a `<details><summary>Technical detail</summary>` block.
4. **Smoke-test recommendation out of the production feed** — filter it in `_recommendations`.

- [ ] **Step 1: Default the grouping on**

In `central_command.esm.js`, in the `useState` call, change `groupBy: ""` to `groupBy: "exception_type"`.

- [ ] **Step 2: Add the bulk action handler**

Add to the `CentralCommand` class:

```javascript
    // Bulk workflow over a whole group — the issue list was 20k rows of the
    // same condition, and acknowledging them one at a time was never viable.
    bulkAction(group, action) {
        const ids = group.items.map((e) => e.id);
        if (!ids.length) {
            return;
        }
        const method = action === "resolve" ? "action_resolve" : "action_acknowledge";
        const verb = action === "resolve" ? "resolved" : "acknowledged";
        this.orm.call("southbrook.command.exception", method, [ids])
            .then(() => {
                this._loadAll(true);
                this.notification.add(`${ids.length} exceptions ${verb}.`, {
                    type: "success",
                });
            })
            .catch(() => {
                this.notification.add("Bulk action failed.", { type: "warning" });
            });
    }
```

- [ ] **Step 3: Wire the group header buttons and the disclosure**

In `static/src/xml/central_command.xml`, inside the group-header element, add:

```xml
<span class="sb_cc_bulk" t-if="group.key">
    <button class="btn btn-sm btn-secondary"
            t-on-click="() => this.bulkAction(group, 'acknowledge')">
        Acknowledge all <t t-esc="group.count"/>
    </button>
    <button class="btn btn-sm btn-secondary"
            t-on-click="() => this.bulkAction(group, 'resolve')">
        Resolve all <t t-esc="group.count"/>
    </button>
</span>
```

And replace the inline `why_text` rendering with:

```xml
<details class="sb_cc_tech" t-if="exc.why_text">
    <summary>Technical detail</summary>
    <span t-esc="exc.why_text"/>
</details>
```

- [ ] **Step 4: Keep the smoke-test recommendation out of the production feed**

In `controllers/main.py`, in `_recommendations`, add to the domain before the search:

```python
            domain.append(("summary", "not ilike", "smoke test"))
```

- [ ] **Step 5: Verify in the browser**

Restart the container (JS and XML changed — SIGHUP does not reload assets) and hard-reload:

```bash
docker restart sami-odoo && sleep 25
```

Open the action, confirm: issues arrive grouped by type, each group header offers both bulk buttons, `why_text` is collapsed behind "Technical detail", and no smoke-test entry appears in Recommendations.

- [ ] **Step 6: Commit**

```bash
git add addons/southbrook_command_center/static/src/js/central_command.esm.js \
        addons/southbrook_command_center/static/src/xml/central_command.xml \
        addons/southbrook_command_center/controllers/main.py
git commit -m "feat(command_center): grouping on by default, bulk actions, tech detail disclosure"
```

---

### Task 9: Deploy Phase 0 to production

**Files:** none — deployment only.

Traps this stack has already burned, carried from the Process Explorer remediation:

- `flock` is **not installed** on the QNAP host. `ssh qnap "flock … system-docker exec …"` fails with `flock: command not found`, and a trailing `|| true` swallows it so the upgrade **never runs** — an unchanged version is the tell.
- Python changed, so SIGHUP is insufficient: the container must be stopped and started.
- `southbrook-odoo` has no host-published port; probe from inside the container.

- [ ] **Step 1: Back up the exception table before the migration touches it**

```bash
ssh qnap-tunnel 'SD=/share/CACHEDEV3_DATA/.qpkg/container-station/bin/system-docker; \
  $SD exec -i southbrook-postgres pg_dump -U odoo -d southbrook \
  -t southbrook_command_exception' > ~/sb_cc_exception_backup_2026-07-30.sql
wc -l ~/sb_cc_exception_backup_2026-07-30.sql
```

Expected: a non-trivial line count (tens of thousands). Do not proceed on an empty file.

- [ ] **Step 2: Record the before-state**

```bash
ssh qnap-tunnel 'SD=/share/CACHEDEV3_DATA/.qpkg/container-station/bin/system-docker; \
  $SD exec -i southbrook-postgres psql -U odoo -d southbrook -c \
  "SELECT count(*) total, count(*) FILTER (WHERE active) open FROM southbrook_command_exception;"'
```

- [ ] **Step 3: Ship the module**

```bash
rsync -az --delete \
  ~/southbrook-v19cr/addons/southbrook_command_center/ \
  qnap-tunnel:/share/CACHEDEV3_DATA/Container/southbrook/addons/southbrook_command_center/
```

- [ ] **Step 4: Cold upgrade — no host flock**

```bash
ssh qnap-tunnel 'SD=/share/CACHEDEV3_DATA/.qpkg/container-station/bin/system-docker; \
  $SD exec southbrook-odoo odoo -c /etc/odoo/odoo.conf -d southbrook \
  -u southbrook_command_center --stop-after-init --http-port=8999 \
  --max-cron-threads=0 2>&1 | grep -E "dedupe migration|ERROR|CRITICAL"'
```

Expected: the migration's summary line; no ERROR or CRITICAL.

- [ ] **Step 5: Verify the version actually bumped**

```bash
ssh qnap-tunnel 'SD=/share/CACHEDEV3_DATA/.qpkg/container-station/bin/system-docker; \
  $SD exec -i southbrook-postgres psql -U odoo -d southbrook -tAc \
  "SELECT latest_version FROM ir_module_module WHERE name='"'"'southbrook_command_center'"'"';"'
```

Expected: `19.0.3.0.0`. **If it still reads 19.0.2.0.0 the upgrade did not run** — do not continue.

- [ ] **Step 6: Restart and purge assets**

```bash
ssh qnap-tunnel 'SD=/share/CACHEDEV3_DATA/.qpkg/container-station/bin/system-docker; \
  $SD exec -i southbrook-postgres psql -U odoo -d southbrook -c \
  "DELETE FROM ir_attachment WHERE name LIKE '"'"'%assets_backend%'"'"' OR name LIKE '"'"'%web.assets_web%'"'"';"; \
  $SD stop southbrook-odoo && $SD start southbrook-odoo'
sleep 30
```

- [ ] **Step 7: Verify the collapse and that the screen loads**

```bash
ssh qnap-tunnel 'SD=/share/CACHEDEV3_DATA/.qpkg/container-station/bin/system-docker; \
  $SD exec -i southbrook-postgres psql -U odoo -d southbrook -c \
  "SELECT count(*) total, count(*) FILTER (WHERE active) open, count(dedupe_key) keyed \
   FROM southbrook_command_exception;"; \
  $SD exec southbrook-odoo curl -s -o /dev/null -w "login %{http_code}\n" localhost:8069/web/login'
```

Expected: `total` equal to the before-state (nothing deleted), `open` ~20, `keyed` ~20, `login 200`.

- [ ] **Step 8: Confirm the leak is closed**

Wait for two sweep cycles (~30 minutes), then re-count:

```bash
ssh qnap-tunnel 'SD=/share/CACHEDEV3_DATA/.qpkg/container-station/bin/system-docker; \
  $SD exec -i southbrook-postgres psql -U odoo -d southbrook -tAc \
  "SELECT count(*) FILTER (WHERE active) FROM southbrook_command_exception;"'
```

Expected: unchanged. Before this fix it would have grown by ~50 in that window.

- [ ] **Step 9: Commit and push the branch**

```bash
git push -u origin feat/factory-command-center
```

---

## Self-review

**Spec coverage.** Spec §10 lists nine Phase-0 items. Mapping: item 1 → Task 1+2; item 2 → Task 3; item 3 → Task 5; item 4 → Task 5 (OTD, work-center denominator) and Task 6 (chips); items 5, 6, 7, 8 → Task 8. **Item 9 ("right column put to use") is deliberately not in this plan** — the right column's use *is* the detail drawer (spec §7), and building an interim panel there would be work the rebuild immediately deletes. It moves to the rebuild plan. Spec §§3–9 and §11 are the rebuild plan's scope, not Phase 0's. Task 7 (rename) covers the spec title change.

**Placeholder scan.** No TBD/TODO; every code step carries real code; no "similar to Task N".

**Type consistency.** `_dedupe_key_for` and `_upsert_exception(..., dedupe_key=)` defined in Task 1, consumed in Tasks 2 and 3. `fetch() -> (dict, str)` defined in Task 4, consumed in Tasks 5 and 6. `truth_chips(payload=None)` defined in Task 6, consumed by the Task 6 controller. `factory_health_score()` keys (`score`, `available`, `coverage_pct`, `band`, `factors`, `provenance`) are consistent between Task 5's implementation and its tests.
