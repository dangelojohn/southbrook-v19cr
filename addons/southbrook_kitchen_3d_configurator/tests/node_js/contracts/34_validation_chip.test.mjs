/**
 * Task 19 (corner-remediation 2026-07-30, SBK-032 + C9 UX) — validation
 * surfaced in the configurator: the persistent status chip (Ready / N
 * warnings / N blockers), its severity-grouped issue panel, click-to-
 * select, and the one wired fix action (Assign walk-in customer).
 *
 * Part 1 exercises the pure reducer (static/src/js/validation/
 * validation_store.esm.js) directly — dedupeIssues, groupBySeverity,
 * deriveChipState — importable head­lessly with no jsdom/mount
 * involved, exactly like 33_undo_redo.test.mjs's Part 1 does for
 * UndoStore.
 *
 * Part 2 exercises the real component (via the shared mount harness):
 *   - the chip is hidden until the design is actually persisted
 *     (state.designId) — nothing to validate before the first save;
 *   - `_refreshValidation()` populates state.validation from the new
 *     /validate endpoint, with a client-side defensive dedupe pass on
 *     top of whatever the server already sent;
 *   - the chip's tone/label track the server's {blocking, warning}
 *     counts (blocking always wins);
 *   - the panel groups issues by severity, blocking first;
 *   - clicking an issue that names a still-placed cabinet selects +
 *     reveals it via the EXACT Task 22 mechanism (state.selected —
 *     never a second selection path) and closes the panel;
 *   - an issue with no resolvable line renders as inert text, not a
 *     button;
 *   - MISSING_CUSTOMER carries the one wired fix action (Task 18's
 *     existing get_or_create_walkin_partner flow), which now ALSO
 *     closes the panel and re-fetches /validate once it resolves (fix
 *     round 1, Minor);
 *   - /validate is re-fetched after a successful save, auto-save, and
 *     arrange (the brief: "refreshed after save/arrange").
 *
 * Fix round 1 (review) — dedupeIssues MUST key on (code, line_ids), not
 * (code, layout_keys). A backend/manual-origin design line (the record
 * form's editable list; `_sb_quote_line_key`'s fallback in
 * kitchen_design.py exists precisely for this real, supported case)
 * carries no `layout_key` at all, so two DIFFERENT such lines firing
 * the SAME issue code both carry `layout_keys: []` — keying on
 * layout_keys collapsed them into one panel entry while the server-
 * truth `counts` (which the chip renders from) still counted both: chip
 * said "2 warnings", panel showed 1, a real issue silently hidden.
 * `line_ids` is always populated and is the exact field the server's
 * own dedupe already keys on. Part 1 below is rewritten to key on
 * line_ids throughout, plus one new test reproducing the exact bug
 * shape (two same-code issues, distinct line_ids, BOTH empty
 * layout_keys) end to end through the mounted panel.
 */
import test from "node:test";
import assert from "node:assert/strict";
import {
    dedupeIssues, groupBySeverity, deriveChipState,
} from "../../../static/src/js/validation/validation_store.esm.js";
import { mountConfigurator } from "../harness/mount_configurator.mjs";
import { tick } from "../harness/tick.mjs";

// ─── Part 1 — pure reducer (no DOM) ─────────────────────────────────────────

test("dedupeIssues: an identical (code, line_ids) pair collapses to one entry, order-insensitive", () => {
    const issues = [
        { code: "CABINET_COLLISION", line_ids: [11, 12], layout_keys: ["k1", "k2"], message: "a" },
        { code: "CABINET_COLLISION", line_ids: [12, 11], layout_keys: ["k2", "k1"], message: "a" },
    ];
    assert.equal(dedupeIssues(issues).length, 1,
        "same code + same line-id-set (any order) is one issue");
});

test("dedupeIssues: the SAME code on a DIFFERENT line-id-set is kept as a distinct issue", () => {
    const issues = [
        { code: "CABINET_COLLISION", line_ids: [11, 12], layout_keys: ["k1", "k2"], message: "a" },
        { code: "CABINET_COLLISION", line_ids: [13, 14], layout_keys: ["k3", "k4"], message: "b" },
    ];
    assert.equal(dedupeIssues(issues).length, 2,
        "a different pair of cabinets is a different, real issue — must not be swallowed");
});

test("dedupeIssues: issues with no line_ids at all dedupe on code alone", () => {
    const issues = [
        { code: "MISSING_CUSTOMER", line_ids: [], layout_keys: [], message: "a" },
        { code: "MISSING_CUSTOMER", line_ids: [], layout_keys: [], message: "a" },
    ];
    assert.equal(dedupeIssues(issues).length, 1,
        "a design has at most one active customer-missing state");
});

test("dedupeIssues: an empty/undefined list never throws", () => {
    assert.deepEqual(dedupeIssues([]), []);
    assert.deepEqual(dedupeIssues(undefined), []);
});

test("fix round 1 (Important) regression: two same-code issues with DISTINCT line_ids but BOTH empty layout_keys must remain two entries", () => {
    // The exact shape a backend/manual-origin design line produces (no
    // layout_key at all — kitchen_design.py's _sb_quote_line_key
    // fallback exists precisely for this case): keying dedupe on
    // layout_keys collapsed these into one, silently hiding a real
    // second issue behind server-truth counts that still said two.
    const issues = [
        { code: "NON_STANDARD_WIDTH", line_ids: [901], layout_keys: [], message: "Cabinet A is 37\" wide." },
        { code: "NON_STANDARD_WIDTH", line_ids: [902], layout_keys: [], message: "Cabinet B is 41\" wide." },
    ];
    const out = dedupeIssues(issues);
    assert.equal(out.length, 2,
        "two distinct backend-origin lines firing the same code must both survive dedupe");
    assert.deepEqual(out.map((i) => i.line_ids), [[901], [902]]);
});

test("groupBySeverity: splits blocking from warning, preserving each group's own order", () => {
    const issues = [
        { code: "A", severity: "warning" },
        { code: "B", severity: "blocking" },
        { code: "C", severity: "warning" },
        { code: "D", severity: "blocking" },
    ];
    const { blocking, warning } = groupBySeverity(issues);
    assert.deepEqual(blocking.map((i) => i.code), ["B", "D"]);
    assert.deepEqual(warning.map((i) => i.code), ["A", "C"]);
});

test("groupBySeverity: a missing/unrecognised severity lands in warning, never silently dropped", () => {
    const { blocking, warning } = groupBySeverity(
        [{ code: "X" }, { code: "Y", severity: "weird" }]);
    assert.equal(blocking.length, 0);
    assert.equal(warning.length, 2);
});

test("deriveChipState: no issues at all -> Ready / ok tone", () => {
    assert.deepEqual(deriveChipState({ blocking: 0, warning: 0 }), { tone: "ok", label: "Ready" });
    assert.deepEqual(deriveChipState({}), { tone: "ok", label: "Ready" });
    assert.deepEqual(deriveChipState(undefined), { tone: "ok", label: "Ready" });
});

test("deriveChipState: blocking always wins over warning; singular/plural label", () => {
    assert.deepEqual(deriveChipState({ blocking: 1, warning: 3 }), { tone: "blocked", label: "1 blocker" });
    assert.deepEqual(deriveChipState({ blocking: 2, warning: 0 }), { tone: "blocked", label: "2 blockers" });
});

test("deriveChipState: warning only -> warn tone; singular/plural label", () => {
    assert.deepEqual(deriveChipState({ blocking: 0, warning: 1 }), { tone: "warn", label: "1 warning" });
    assert.deepEqual(deriveChipState({ blocking: 0, warning: 5 }), { tone: "warn", label: "5 warnings" });
});

// ─── Part 2 — component integration ────────────────────────────────────────

const LINES = [
    { id: 501, layout_key: "k1", cabinet_type: "base", product_name: "Base 24",
      width_in: 24, height_in: 34.5, depth_in: 24,
      x_position_in: 0, y_position_in: 0, z_position_in: 0, rotation_deg: 0 },
    { id: 502, layout_key: "k2", cabinet_type: "wall", product_name: "Wall 24",
      width_in: 24, height_in: 30, depth_in: 12,
      x_position_in: 0, y_position_in: 54, z_position_in: 0, rotation_deg: 0 },
];

function mountHydrated(rpcRoutes = {}) {
    return mountConfigurator({
        actionParams: { design_id: 1900 },
        rpcRoutes: {
            "/southbrook_kitchen/configurator/load_design_lines": () => ({
                room: { width_in: 96, depth_in: 96, height_in: 96 },
                design_name: "T19 design", lines: LINES,
            }),
            ...rpcRoutes,
        },
    });
}

test("the status chip is hidden until the design is persisted (no designId yet)", async () => {
    const h = await mountConfigurator({});   // landing -> _startNewDesign; never saved
    assert.equal(h.component.state.designId, null);
    assert.equal(h.root.querySelector(".o_sbk_validation"), null,
        "nothing to validate before the first save");
    h.destroy();
});

test("_refreshValidation populates state.validation from /validate, deduped client-side too", async () => {
    const h = await mountHydrated({
        "/southbrook_kitchen/configurator/validate": () => ({
            issues: [
                { code: "MISSING_CUSTOMER", severity: "blocking",
                  message: "This design needs a customer before quoting.",
                  line_ids: [], layout_keys: [] },
                { code: "MISSING_CUSTOMER", severity: "blocking",
                  message: "This design needs a customer before quoting.",
                  line_ids: [], layout_keys: [] },
                { code: "NON_STANDARD_WIDTH", severity: "warning",
                  message: "Base 24 is 24\" wide.",
                  line_ids: [501], layout_keys: ["k1"] },
            ],
            counts: { blocking: 1, warning: 1 },
            ready: false,
        }),
    });
    await h.component._refreshValidation();
    assert.equal(h.component.state.validation.issues.length, 2,
        "the exact-duplicate MISSING_CUSTOMER pair collapses to one");
    assert.deepEqual(h.component.state.validation.counts, { blocking: 1, warning: 1 });
    assert.equal(h.component.state.validation.ready, false);
    h.destroy();
});

test("fix round 1 (Important) regression: two backend-origin lines (no layout_key) firing the SAME code both render as distinct panel rows, matching the chip's count", async () => {
    const h = await mountHydrated({
        "/southbrook_kitchen/configurator/validate": () => ({
            issues: [
                { code: "NON_STANDARD_WIDTH", severity: "warning",
                  message: "Cabinet A is 37\" wide.",
                  line_ids: [901], layout_keys: [] },
                { code: "NON_STANDARD_WIDTH", severity: "warning",
                  message: "Cabinet B is 41\" wide.",
                  line_ids: [902], layout_keys: [] },
            ],
            counts: { blocking: 0, warning: 2 },
            ready: true,
        }),
    });
    await h.component._refreshValidation();
    assert.equal(h.component.state.validation.issues.length, 2,
        "distinct backend-origin lines must NOT collapse just because both carry empty layout_keys");
    await tick();

    const chip = h.root.querySelector(".o_sbk_validation_chip");
    assert.equal(chip.textContent.trim(), "2 warnings",
        "chip count (server truth) must match what the panel is about to show");
    chip.click();
    await tick();

    const rows = h.root.querySelectorAll(".o_sbk_validation_issue");
    assert.equal(rows.length, 2, "the panel must render both rows, not one");
    assert.ok(h.root.textContent.includes("Cabinet A is 37\" wide."));
    assert.ok(h.root.textContent.includes("Cabinet B is 41\" wide."));
    // Neither carries a resolvable layout_key -> both are inert text,
    // never a button (see the separate "inert text" test for that
    // contract in isolation).
    assert.equal(h.root.querySelector(".o_sbk_validation_issue_btn"), null);
    h.destroy();
});

test("chip renders Ready/ok when there are no issues", async () => {
    const h = await mountHydrated();   // DEFAULT_ROUTES' benign /validate default
    await h.component._refreshValidation();
    await tick();
    const chip = h.root.querySelector(".o_sbk_validation_chip");
    assert.ok(chip, "chip renders once a design id exists");
    assert.ok(chip.classList.contains("o_sbk_vc_ok"));
    assert.equal(chip.textContent.trim(), "Ready");
    h.destroy();
});

test("chip renders the blocked tone + blocker count when any issue is blocking, even alongside warnings", async () => {
    const h = await mountHydrated({
        "/southbrook_kitchen/configurator/validate": () => ({
            issues: [
                { code: "EMPTY_DESIGN", severity: "blocking", message: "x", line_ids: [], layout_keys: [] },
                { code: "NON_STANDARD_WIDTH", severity: "warning", message: "y", line_ids: [], layout_keys: [] },
            ],
            counts: { blocking: 1, warning: 1 },
            ready: false,
        }),
    });
    await h.component._refreshValidation();
    await tick();
    const chip = h.root.querySelector(".o_sbk_validation_chip");
    assert.ok(chip.classList.contains("o_sbk_vc_blocked"));
    assert.equal(chip.textContent.trim(), "1 blocker");
    h.destroy();
});

test("clicking the chip opens the panel grouped by severity (Blocking before Warnings); clicking again closes it", async () => {
    const h = await mountHydrated({
        "/southbrook_kitchen/configurator/validate": () => ({
            issues: [
                { code: "NON_STANDARD_WIDTH", severity: "warning", message: "warn message",
                  line_ids: [501], layout_keys: ["k1"] },
                { code: "EMPTY_DESIGN", severity: "blocking", message: "block message",
                  line_ids: [], layout_keys: [] },
            ],
            counts: { blocking: 1, warning: 1 },
            ready: false,
        }),
    });
    await h.component._refreshValidation();
    await tick();
    const chip = h.root.querySelector(".o_sbk_validation_chip");
    assert.equal(chip.getAttribute("aria-expanded"), "false");

    chip.click();
    await tick();
    assert.equal(chip.getAttribute("aria-expanded"), "true");
    const groupTitles = [...h.root.querySelectorAll(".o_sbk_validation_group_title")]
        .map((n) => n.textContent.trim());
    assert.deepEqual(groupTitles, ["Blocking", "Warnings"],
        "blocking group renders before warnings, per the brief");
    assert.ok(h.root.textContent.includes("block message"));
    assert.ok(h.root.textContent.includes("warn message"));

    chip.click();
    await tick();
    assert.equal(h.root.querySelector(".o_sbk_validation_panel"), null);
    h.destroy();
});

test("AC: clicking a blocking issue that names a still-placed cabinet selects it — reuses state.selected (Task 22), no second selection path", async () => {
    const h = await mountHydrated({
        "/southbrook_kitchen/configurator/validate": () => ({
            issues: [
                { code: "NON_STANDARD_WIDTH", severity: "warning",
                  message: "Wall 24 is an odd width.",
                  line_ids: [502], layout_keys: ["k2"] },
            ],
            counts: { blocking: 0, warning: 1 },
            ready: true,
        }),
    });
    await h.component._refreshValidation();
    await tick();
    h.component.state.selected = null;   // start from nothing selected

    h.root.querySelector(".o_sbk_validation_chip").click();
    await tick();
    const issueBtn = h.root.querySelector(".o_sbk_validation_issue_btn");
    assert.ok(issueBtn, "an issue with a resolvable line renders as a clickable button");
    issueBtn.click();
    await tick();

    assert.ok(h.component.state.selected, "selection landed");
    assert.equal(h.component.state.selected.layout_key, "k2",
        "the SAME item object the canvas already renders — Task 22's reveal picks this up for free");
    assert.equal(h.component.state.validation.panelOpen, false,
        "selecting an issue closes the panel so the reveal is visible");
    h.destroy();
});

test("an issue with no resolvable line renders as inert text, not a button", async () => {
    const h = await mountHydrated({
        "/southbrook_kitchen/configurator/validate": () => ({
            issues: [
                { code: "MISSING_CUSTOMER", severity: "blocking",
                  message: "This design needs a customer before quoting.",
                  line_ids: [], layout_keys: [] },
            ],
            counts: { blocking: 1, warning: 0 },
            ready: false,
        }),
    });
    await h.component._refreshValidation();
    await tick();
    h.root.querySelector(".o_sbk_validation_chip").click();
    await tick();

    assert.equal(h.root.querySelector(".o_sbk_validation_issue_btn"), null,
        "no line to select -> never rendered as a button");
    const textEl = h.root.querySelector(".o_sbk_validation_issue_text");
    assert.ok(textEl);
    assert.ok(textEl.textContent.includes("needs a customer"));
    h.destroy();
});

test("MISSING_CUSTOMER carries the wired 'Assign walk-in customer' fix action (Task 18's existing flow); fixing it closes the panel and re-fetches /validate", async () => {
    let walkinCalls = 0;
    const h = await mountHydrated({
        "/southbrook_kitchen/configurator/validate": () => ({
            issues: [
                { code: "MISSING_CUSTOMER", severity: "blocking",
                  message: "This design needs a customer before quoting.",
                  line_ids: [], layout_keys: [] },
            ],
            counts: { blocking: 1, warning: 0 },
            ready: false,
        }),
        "/web/dataset/call_kw": (params) => {
            if (params.method === "get_or_create_walkin_partner") {
                walkinCalls++;
                return 4242;
            }
            throw new Error("unexpected call_kw method: " + params.method);
        },
    });
    await h.component._refreshValidation();
    await tick();
    h.root.querySelector(".o_sbk_validation_chip").click();
    await tick();

    const fixBtn = [...h.root.querySelectorAll(".o_sbk_validation_fix")]
        .find((b) => b.textContent.trim() === "Assign walk-in customer");
    assert.ok(fixBtn, "MISSING_CUSTOMER renders the one wired fix action");
    const validateCallsBefore = h.rpcCalls("/southbrook_kitchen/configurator/validate").length;

    fixBtn.click();
    await tick(30);   // _fixMissingCustomerFromPanel chains _useWalkInCustomer -> _setCustomer -> _refreshValidation

    assert.equal(walkinCalls, 1, "the SAME get_or_create_walkin_partner call the topbar picker already uses");
    assert.equal(h.component.state.partnerId, 4242);
    // Fix round 1 (Minor) — the panel must not keep showing the now-
    // stale MISSING_CUSTOMER row; it closes immediately, and a fresh
    // /validate fetch is fired rather than waiting for the next save/
    // arrange to happen to refresh it.
    assert.equal(h.component.state.validation.panelOpen, false,
        "fixing the issue from the panel closes it — no stale row left showing");
    assert.ok(
        h.rpcCalls("/southbrook_kitchen/configurator/validate").length > validateCallsBefore,
        "the fix action must trigger a fresh /validate fetch, not wait for the next save/arrange",
    );
    h.destroy();
});

test("no other issue code renders a fix action (brief: 'others...inert')", async () => {
    const h = await mountHydrated({
        "/southbrook_kitchen/configurator/validate": () => ({
            issues: [
                { code: "NON_STANDARD_WIDTH", severity: "warning", message: "x",
                  line_ids: [501], layout_keys: ["k1"] },
            ],
            counts: { blocking: 0, warning: 1 },
            ready: true,
        }),
    });
    await h.component._refreshValidation();
    await tick();
    h.root.querySelector(".o_sbk_validation_chip").click();
    await tick();
    assert.equal(h.root.querySelector(".o_sbk_validation_fix"), null);
    h.destroy();
});

test("brief: status chip is refreshed after a successful manual save", async () => {
    const h = await mountHydrated({
        "/southbrook_kitchen/configurator/save": () => ({ id: 1900, name: "T19 design" }),
    });
    const before = h.rpcCalls("/southbrook_kitchen/configurator/validate").length;
    h.component.state.items = [{ ...LINES[0], name: "Base 24" }];
    await h.component._saveDesign();
    assert.ok(
        h.rpcCalls("/southbrook_kitchen/configurator/validate").length > before,
        "a successful save must trigger a fresh /validate fetch",
    );
    h.destroy();
});

test("brief: status chip is refreshed after a successful arrange", async () => {
    const h = await mountHydrated({
        "/southbrook_kitchen/configurator/rearrange": () => ({
            lines: LINES,
            room: { width_in: 96, depth_in: 96, height_in: 96 },
        }),
    });
    const before = h.rpcCalls("/southbrook_kitchen/configurator/validate").length;
    const ok = await h.component._rearrangeOnServer();
    assert.equal(ok, true);
    assert.ok(
        h.rpcCalls("/southbrook_kitchen/configurator/validate").length > before,
        "a successful arrange must trigger a fresh /validate fetch",
    );
    h.destroy();
});

test("brief: status chip is refreshed after a successful auto-save", async () => {
    const h = await mountHydrated({
        "/southbrook_kitchen/configurator/save": () => ({ id: 1900, name: "T19 design" }),
    });
    h.component._userActed = true;   // T7 gate — auto-save no-ops otherwise
    h.component.state.items = [{ ...LINES[0], name: "Base 24" }];
    const before = h.rpcCalls("/southbrook_kitchen/configurator/validate").length;
    await h.component._autoSave();
    assert.ok(
        h.rpcCalls("/southbrook_kitchen/configurator/validate").length > before,
        "a successful auto-save must trigger a fresh /validate fetch",
    );
    h.destroy();
});
