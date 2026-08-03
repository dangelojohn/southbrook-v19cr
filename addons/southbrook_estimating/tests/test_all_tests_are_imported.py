# SPDX-License-Identifier: LGPL-3.0-only
"""SBK-049 — every test_*.py in this package must be imported by __init__.py.

Odoo discovers tests by importing the `tests` package and walking what that
import produced. A test file that nobody adds to `tests/__init__.py` is not
"skipped" — it is invisible. No selector finds it, no run reports it, and it
contributes nothing while looking, to anyone reading the directory, exactly
like a test that passes.

This has now happened twice in this package:

  * `test_filler_remainder_gate.py` — fixed 2026-07-30.
  * `test_sale_order_line_bom_rollup.py` — authored 2026-06-10, found
    2026-08-03. Ten tests that had never executed once in nearly two months.

Both were found by accident while chasing something else. This test makes the
next one fail loudly on the first run instead.
"""
import os
import re

from odoo.tests import TransactionCase, tagged

_IMPORT_RE = re.compile(r"^\s*from\s+\.\s+import\s+(\w+)", re.M)


@tagged("post_install", "-at_install", "southbrook",
        "southbrook_estimating", "sb_data_ratchet")
class TestAllTestsAreImported(TransactionCase):

    def test_every_test_module_is_imported_in_init(self):
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, "__init__.py"), encoding="utf-8") as fh:
            imported = set(_IMPORT_RE.findall(fh.read()))

        on_disk = {
            name[:-3]
            for name in os.listdir(here)
            if name.startswith("test_") and name.endswith(".py")
        }

        orphans = sorted(on_disk - imported)
        self.assertFalse(
            orphans,
            "These test modules exist on disk but are not imported in "
            "tests/__init__.py, so Odoo will never collect them and they "
            "will silently contribute nothing:\n"
            + "\n".join(f"  - {name}.py" for name in orphans)
            + "\n\nAdd 'from . import <name>' to tests/__init__.py."
        )
