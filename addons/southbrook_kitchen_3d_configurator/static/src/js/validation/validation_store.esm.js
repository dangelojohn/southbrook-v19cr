/** @odoo-module **/
/**
 * Southbrook Kitchen 3D Configurator — production-readiness status chip
 * (Task 19, corner-remediation 2026-07-30, SBK-032 + C9 UX).
 *
 * Pure, DOM/OWL-agnostic helpers — exactly like toast_store.esm.js
 * (SBK-030) and undo_store.esm.js (Task 35): kitchen_configurator.js is
 * the only caller and owns every "when do we fetch" / "what does a
 * click do" decision; this module only shapes data, so it's
 * exercisable headlessly in Node with plain object literals (no jsdom,
 * no mounted component required).
 *
 * The server (`southbrook.kitchen.design._normalize_production_issues`,
 * consumed by controllers/main.py's `/validate` endpoint) already
 * dedupes by (code, line_ids) and never emits raw coordinates/enum
 * literals in `message` (Task 15's copy hygiene, kept intact here —
 * this module never rewrites `message`). `dedupeIssues` below is a
 * defensive SECOND pass on the same contract — collapsing on (code,
 * sorted layout_keys) — so a client-side re-render racing a fresh
 * fetch (e.g. two /validate responses landing out of order) can never
 * show a duplicate row either. It can never disagree with the server's
 * own dedupe because it uses the identical two-part key shape.
 */

/**
 * Dedupe an issue list by (code, sorted layout_keys) — the brief's
 * "dedupe by rule+line". Issues with no `layout_keys` at all (a design-
 * level issue with nothing to point at, e.g. MISSING_CUSTOMER) dedupe
 * on `code` alone, which is correct: a design has at most one active
 * customer-missing state.
 * @param {{code?:string, layout_keys?:string[]}[]} issues
 * @returns {object[]}
 */
export function dedupeIssues(issues) {
    const seen = new Set();
    const out = [];
    for (const issue of issues || []) {
        const keys = [...(issue.layout_keys || [])].sort().join(",");
        const dedupeKey = (issue.code || "") + "|" + keys;
        if (seen.has(dedupeKey)) continue;
        seen.add(dedupeKey);
        out.push(issue);
    }
    return out;
}

/**
 * Groups a (deduped) issue list by severity — the panel's own render
 * order (brief: "panel grouped by severity"), blocking first. A
 * missing/unrecognised severity is treated as a warning — conservative
 * (never silently dropped, never mistaken for a hard block it isn't).
 * @param {{severity?:string}[]} issues
 * @returns {{blocking: object[], warning: object[]}}
 */
export function groupBySeverity(issues) {
    const blocking = [];
    const warning = [];
    for (const issue of issues || []) {
        (issue.severity === "blocking" ? blocking : warning).push(issue);
    }
    return { blocking, warning };
}

/**
 * Pure chip-state derivation (brief: "chip state derivation (pure
 * function)"). `counts` is always the server's OWN {blocking, warning}
 * tally (controllers/main.py `/validate`) — never independently
 * recomputed here from the issue list, so the chip can never disagree
 * with what the panel underneath it actually shows.
 * @param {{blocking?:number, warning?:number}} counts
 * @returns {{tone: "ok"|"warn"|"blocked", label: string}}
 */
export function deriveChipState(counts) {
    const blocking = (counts && counts.blocking) || 0;
    const warning = (counts && counts.warning) || 0;
    if (blocking > 0) {
        return {
            tone:  "blocked",
            label: blocking === 1 ? "1 blocker" : `${blocking} blockers`,
        };
    }
    if (warning > 0) {
        return {
            tone:  "warn",
            label: warning === 1 ? "1 warning" : `${warning} warnings`,
        };
    }
    return { tone: "ok", label: "Ready" };
}
