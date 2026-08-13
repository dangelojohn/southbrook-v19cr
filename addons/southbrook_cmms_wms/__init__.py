# SPDX-License-Identifier: LGPL-3.0-only
from . import models


def post_init_hook(env):
    """Write the first KPI snapshot at install time.

    The Snapshot screen used to be seeded with a static record so it would not
    open empty (plain users have perm_create=0 and cannot make one). That seed
    was renamed "Smoke" during testing in June and, because it carried
    noupdate="1", survived every upgrade to sit in production as the first
    point of every trend the KPI history draws.

    A real snapshot is a better answer than a placeholder: this writes one now,
    and the nightly cron appends one per day thereafter.
    """
    env["southbrook.cmms.mi_tiles"]._cron_snapshot()
