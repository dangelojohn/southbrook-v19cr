# SPDX-License-Identifier: LGPL-3.0-only
"""
Public download routes for the Southbrook documentation PDFs.

Four QWeb PDF reports (reports/southbrook_docs.xml) — Features, Brochure,
Quick Start Guide, User Manual — are rendered on demand and returned as a
download to any website visitor (no login). Linked from the homepage
Resources section. Read-only; renders static content, writes nothing.
"""
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class SouthbrookDocs(http.Controller):

    # slug -> (report xml_id, download filename)
    _DOCS = {
        "features": (
            "southbrook_estimating_website.report_sb_doc_features",
            "Southbrook-Cabinetry-Platform-Features.pdf"),
        "brochure": (
            "southbrook_estimating_website.report_sb_doc_brochure",
            "Southbrook-Cabinetry-Sales-Brochure.pdf"),
        "quick-guide": (
            "southbrook_estimating_website.report_sb_doc_quickguide",
            "Southbrook-Cabinetry-Quick-Start-Guide.pdf"),
        "user-manual": (
            "southbrook_estimating_website.report_sb_doc_manual",
            "Southbrook-Cabinetry-User-Manual.pdf"),
    }

    # 2026-08-02 — UX Phase 2 vocab gate: sitemap=False. The user-manual
    # PDF's later chapters are written for reps/dealers ("check the pricing
    # for your channel... see exactly what your margin is") and search
    # indexing surfaced that to anonymous homeowners. The PDFs stay public
    # and directly linkable (homepage links are unaffected) — this only
    # stops the crawler from independently indexing/ranking them.
    @http.route(
        "/southbrook/docs/<string:slug>.pdf",
        type="http", auth="public", website=True, sitemap=False, methods=["GET"],
    )
    def download_doc(self, slug, **kw):
        entry = self._DOCS.get(slug)
        if not entry:
            return request.not_found()
        report_ref, filename = entry
        # sudo(): render as system so the public user needs no report/company
        # ACL. The report is static marketing/documentation content.
        Report = request.env["ir.actions.report"].sudo()
        try:
            pdf_content, _ct = Report._render_qweb_pdf(
                report_ref, res_ids=request.env.company.sudo().ids)
        except Exception:                                   # noqa: BLE001
            _logger.exception("Failed to render doc PDF %s", report_ref)
            return request.not_found()
        return request.make_response(
            pdf_content,
            headers=[
                ("Content-Type", "application/pdf"),
                ("Content-Length", len(pdf_content)),
                ("Content-Disposition", 'attachment; filename="%s"' % filename),
                # Public marketing docs — safe to cache at the edge for a day.
                ("Cache-Control", "public, max-age=86400"),
            ],
        )
