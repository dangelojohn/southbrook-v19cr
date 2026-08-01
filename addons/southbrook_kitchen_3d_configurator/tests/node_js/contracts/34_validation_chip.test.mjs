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
 *     existing get_or_create_walkin_partner flow);
 *   - /validate is re-fetched after a successful save, auto-save, and
 *     arrange (the brief: "refreshed after save/arrange").
 */
import test from "node:test";
import assert from "node:assert/strict";
import {
    dedupeIssues, groupBySeverity, deriveChipState,
} from "../../../static/src/js/validation/validation_store.esm.js";
import { mountConfigurator } from "../harness/mount_configurator.mjs";
import { tick } from "../harness/tick.mjs";

// ─── Part 1 — pure reducer (no DOM) ─────────────────────────────────────────

test("dedupeIssues: an identical (code, layout_keys) pair collapses to one entry, order-insensitive", () => {
    const issues = [
        { code: "CABINET_COLLISION", layout_keys: ["k1", "k2"], message: "a" },
        { code: "CABINET_COLLISION", layout_keys: ["k2", "k1"], message: "a" },
    ];
    assert.equal(dedupeIssues(issues).length, 1,
        "same code + same line-set (any order) is one issue");
});

test("dedupeIssues: the SAME code on a DIFFERENT line-set is kept as a distinct issue", () => {
    const issues = [
        { code: "CABINET_COLLISION", layout_keys: ["k1", "k2"], message: "a" },
        { code: "CABINET_COLLISION", layout_keys: ["k3", "k4"], message: "b" },
    ];
    assert.equal(dedupeIssues(issues).length, 2,
        "a different pair of cabinets is a different, real issue — must not be swallowed");
});

test("dedupeIssues: issues with no layout_keys at all dedupe on code alone", () => {
    const issues = [
        { code: "MISSING_CUSTOMER", message: "a" },
        { code: "MISSING_CUSTOMER", message: "a" },
    ];
    assert.equal(dedupeIssues(issues).length, 1,
        "a design has at most one active customer-missing state");
});

test("dedupeIssues: an empty/undefined list never throws", () => {
    assert.deepEqual(dedupeIssues([]), []);
    assert.deepEqual(dedupeIssues(undefined), []);
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

test("MISSING_CUSTOMER carries the wired 'Assign walk-in customer' fix action (Task 18's existing flow)", async () => {
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
    fixBtn.click();
    await tick(30);   // _useWalkInCustomer chains rpc -> _setCustomer -> _loadProducts/_refreshLayout

    assert.equal(walkinCalls, 1, "the SAME get_or_create_walkin_partner call the topbar picker already uses");
    assert.equal(h.component.state.partnerId, 4242);
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
