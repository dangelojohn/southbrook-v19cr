# SPDX-License-Identifier: LGPL-3.0-only
# plm_lifecycle first: it defines the PLM_LIFECYCLE sentinel (FIX 1, 2026-08-02
# blindspot pass) that southbrook_cut_spec and southbrook_eco both import at
# module load time. No model classes live in it, so Odoo registry ordering
# doesn't strictly require this — Python resolves `from .plm_lifecycle import
# ...` on demand regardless of __init__ order — but importing it first here
# keeps the dependency direction explicit and matches how the module is
# actually consumed (a plain singleton, not a model).
from . import plm_lifecycle
from . import southbrook_cut_spec
from . import southbrook_eco_stage
from . import southbrook_eco_type
from . import southbrook_eco
from . import mrp_bom
from . import sale_order_line
from . import sale_order
