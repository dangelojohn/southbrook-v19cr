# Dev harnesses — L-shape / corner-engine verification (2026-07-27)

Odoo-shell scripts used to diagnose and then gate the L-shaped-kitchen
corner workflow fixes (C1-C9) and the M1-M6 rules-as-data engine work.
They drive the REAL controller routes (`save_design`, `load_design_lines`)
with a stubbed `request`, and ROLL BACK everything — safe on any DB copy.

Run pattern (local sami-odoo container, repo mounted live):

    docker cp scripts/dev-harness/e2e_verify_fix.py sami-odoo:/tmp/
    docker exec -i sami-odoo sh -lc "odoo shell -c /etc/odoo/odoo.conf \
      --db_host=db --db_user=odoo --db_password=change-me-strong-password \
      -d southbrook --http-port=8999 --max-cron-threads=0 \
      --log-level=critical < /tmp/e2e_verify_fix.py >/dev/null 2>&1; \
      cat /tmp/verify_out.txt"

- `e2e_verify_fix.py` — THE regression gate: T1 L-shape 4+4/4+4 → exactly
  SB-CORNER + SB-WALL-CORNER, correct zones/elevations, zero same-layer
  overlaps (anchor-convention footprints), originals archived; T2 genuinely
  over-full room → human layout_warning, never a 500; T3 re-save
  idempotence; T4 single-back-wall legacy byte-parity. ALL PASS required.
- `e2e_lshape.py` — the original workflow walk that measured the C1-C9
  defects (kept for archaeology; its DEFECT output reflects pre-fix code).
- `repro_corner.py` — pure-python3 engine repros (no Odoo needed).

See also: docs/research/corner-engine/ (221-entry knowledge corpus +
09-rule-engine-spec.md), and the node contract suite under
addons/southbrook_kitchen_3d_configurator/tests/node_js/.
