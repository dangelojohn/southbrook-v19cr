# SPDX-License-Identifier: LGPL-3.0-only
{
    "name": "Southbrook Training Hub",
    "summary": "Discovery + retrieval layer over Southbrook's internal "
               "e-learning catalogue — in-app Help panel, Hermes "
               "find_training tool, JTBD verb-phrase search, "
               "menu-anchored recommendations.",
    "description": """
Southbrook Training Hub
=======================

Companion to ``southbrook_elearning_internal``. The eLearning addon
authors the courses; this addon makes them findable in the moment a
user needs them.

Surfaces added
--------------

1. **Unified training index** — ``southbrook.training.item`` mirrors
   every ``slide.slide`` plus optional external assets (runbooks,
   step-card PDFs, embedded videos) so search hits all of them at once.

2. **JTBD verb-phrase tagging** — ``southbrook.training.tag`` with
   typed tag groups (``role``, ``module``, ``jtbd``, ``department``).
   Authors annotate slides with a short "I want to..." verb phrase that
   becomes the primary search axis.

3. **In-app Help systray button** — a ``?`` icon in the top-right
   systray opens a side panel listing training items relevant to the
   current menu, scoped by the user's groups + tags. v19 OWL.

4. **Hermes ``find_training`` tool** — registered via
   ``@hermes_tool``; trade partners and internal staff can ask
   "how do I create a kitchen quote?" and get back the matching
   eLearning URLs + summaries.

5. **Public JSON endpoint** — ``GET /training/recommended`` returns
   the per-user recommendation list, consumed by the IQ-Deck intranet
   tile.

6. **Menu → item map** — ``southbrook.training.menu_link`` lets an
   admin pin specific lessons to ``ir.ui.menu`` records so the Help
   panel knows what to surface when a user is on, say,
   *Kitchen Ops → Production Release Queue*.

Why a separate addon from ``southbrook_elearning_internal``
----------------------------------------------------------

The eLearning addon's job is content. Its install profile is "drop in
markdown, regenerate XML, upgrade addon, courses appear." This
addon's job is integration: it depends on ``website_slides``,
``southbrook_hermes`` (for tool registration), and a JS asset bundle.
Mixing those concerns would make content edits hostage to the OWL
build, and make discovery hostage to content shipping. Two addons,
clean separation of concerns.

Install order
-------------

``southbrook_elearning_internal`` should be installed before this
addon so the seed pass finds populated ``slide.slide`` records.
Re-running ``odoo -u southbrook_training_hub`` after new courses ship
refreshes the index. There is no destructive sync — manual entries
are preserved.
""",
    "version": "19.0.2.0.1",
    "license": "LGPL-3",
    "author": "Southbrook Cabinetry",
    "website": "https://southbrookcabinetry.space",
    "category": "Website/eLearning",
    "depends": [
        "base",
        "mail",
        "website",
        "website_slides",
        "southbrook_hermes",
    ],
    "data": [
        "security/training_security.xml",
        "security/ir.model.access.csv",
        "data/training_tag_seed.xml",
        "views/training_tag_views.xml",
        "views/training_item_views.xml",
        "views/training_menu_link_views.xml",
        "views/menu.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "southbrook_training_hub/static/src/help/help_systray.js",
            "southbrook_training_hub/static/src/help/help_systray.xml",
            "southbrook_training_hub/static/src/help/help_panel.js",
            "southbrook_training_hub/static/src/help/help_panel.xml",
            "southbrook_training_hub/static/src/help/help.scss",
        ],
    },
    "post_init_hook": "_seed_training_items_from_slides",
    "installable": True,
    "application": False,
    "auto_install": False,
}
