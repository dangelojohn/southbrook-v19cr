# SPDX-License-Identifier: LGPL-3.0-only
{
    "name": "Southbrook Integrations",
    "summary": "Homag simulator + 3PL ASN + MCP-into-Odoo scaffold + "
               "IoT label printers (no EDI, no SSO - descoped to v2)",
    "version": "19.0.2.0.1",
    "license": "LGPL-3",
    "author": "Southbrook Cabinetry / OdooIQ",
    "category": "Manufacturing/Integrations",
    "depends": [
        "base", "mail", "mrp", "stock", "delivery",
        "southbrook_kitchen_mrp", "southbrook_manufacturing_intelligence",
        "southbrook_api",
        # Required for seed MCP tool refs (ncr / payroll.run / finance.asset).
        # All three are in the prod Makefile MODULES list, so adding them
        # doesn't expand the install surface, but it does pin install order.
        "southbrook_quality",
        "southbrook_payroll_ca",
        "southbrook_finance_pack",
    ],
    "data": [
        "security/groups.xml",
        "security/ir.model.access.csv",
        "data/ir_sequence.xml",
        "data/iot_label_printers.xml",
        "data/mi_tiles.xml",
        # Seed MCP tools — was omitted, so the /integrations/mcp/v1/invoke
        # endpoint 404'd for every documented tool (headline feature shipped
        # empty). The referenced sibling models are all in the dep list.
        "data/mcp_tools.xml",
        "views/homag_session_views.xml",
        "views/asn_3pl_views.xml",
        "views/mcp_tool_views.xml",
        "views/mcp_call_log_views.xml",
        "views/iot_label_printer_views.xml",
        "views/iot_label_log_views.xml",
        "views/mi_engine_ext_views.xml",
        "views/menus.xml",
    ],
    "installable": True,
    "application": False,
}
