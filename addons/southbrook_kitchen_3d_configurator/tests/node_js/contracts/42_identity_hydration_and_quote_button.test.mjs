/**
 * H-4/H-01 fix (2026-08-05) — consume the new identity fields
 * `/southbrook_kitchen/configurator/load_design_lines` now sends:
 *
 *   "partner_id":      design.partner_id.id or False,
 *   "partner_name":    design.partner_id.display_name or False,
 *   "design_state":    design.state,
 *   "sale_order_id":   design.sale_order_id.id or False,
 *   "sale_order_name": design.sale_order_id.name or False,
 *
 * `_hydrateFromDesign` backfills `state.partnerId`/`state.partnerName`
 * (additive — only when not already set locally, same precedence rule
 * every other field in that function follows) and always stores
 * `state.designState`/`state.saleOrderId`/`state.saleOrderName`.
 *
 * H-13 fix (2026-08-05, half) — the "Create & Confirm" button reads
 * `state.saleOrderName` and relabels to "Open quotation …" once a
 * linked quotation exists, instead of always claiming "Create &
 * Confirm" for a design that already has one.
 */
import test from "node:test";
import assert from "node:assert/strict";
import { mountConfigurator } from "../harness/mount_configurator.mjs";
import { tick } from "../harness/tick.mjs";

const LINES = [
    { id: 501, layout_key: "k1", cabinet_type: "base", product_name: "Base 24",
      width_in: 24, height_in: 34.5, depth_in: 24,
      x_position_in: 0, y_position_in: 0, z_position_in: 0, rotation_deg: 0,
      wall: "back", price: 500 },
];

function confirmButton(root) {
    return root.querySelector(".o_sbk_btn_confirm");
}

// ─── Fix 3 — identity fields backfilled on hydrate ─────────────────────────

test("partner_id AND partner_name are BOTH backfilled from load_design_lines when not already set locally", async () => {
    const h = await mountConfigurator({
        actionParams: { design_id: 6001 },   // no partner_id/partner_name in params
        rpcRoutes: {
            "/southbrook_kitchen/configurator/load_design_lines": () => ({
                room: { width_in: 96, depth_in: 96, height_in: 96 },
                design_name: "identity test", lines: LINES,
                partner_id: 77, partner_name: "Acme Kitchens",
                design_state: "draft", sale_order_id: false, sale_order_name: false,
            }),
        },
    });
    assert.equal(h.component.state.partnerId, 77,
        "partner_id must be backfilled — this was dead code before the fix (only partner_name was)");
    assert.equal(h.component.state.partnerName, "Acme Kitchens");
    h.destroy();
});

test("an explicitly-passed partner_id/partner_name in params is NEVER clobbered by the hydration backfill", async () => {
    const h = await mountConfigurator({
        actionParams: { design_id: 6002, partner_id: 5, partner_name: "Explicit Partner" },
        rpcRoutes: {
            "/southbrook_kitchen/configurator/load_design_lines": () => ({
                room: { width_in: 96, depth_in: 96, height_in: 96 },
                design_name: "identity test 2", lines: LINES,
                partner_id: 999, partner_name: "Server Says Someone Else",
            }),
        },
    });
    assert.equal(h.component.state.partnerId, 5, "params precedence must hold, same as room/design_name");
    assert.equal(h.component.state.partnerName, "Explicit Partner");
    h.destroy();
});

test("design_state / sale_order_id / sale_order_name are stored in state from the new contract fields", async () => {
    const h = await mountConfigurator({
        actionParams: { design_id: 6003 },
        rpcRoutes: {
            "/southbrook_kitchen/configurator/load_design_lines": () => ({
                room: { width_in: 96, depth_in: 96, height_in: 96 },
                design_name: "quoted design", lines: LINES,
                design_state: "confirmed",
                sale_order_id: 4242,
                sale_order_name: "S01434",
            }),
        },
    });
    assert.equal(h.component.state.designState, "confirmed");
    assert.equal(h.component.state.saleOrderId, 4242);
    assert.equal(h.component.state.saleOrderName, "S01434");
    h.destroy();
});

test("older/cached responses that omit the new fields entirely leave safe, falsy defaults (guarded against undefined)", async () => {
    const h = await mountConfigurator({
        actionParams: { design_id: 6004 },
        rpcRoutes: {
            "/southbrook_kitchen/configurator/load_design_lines": () => ({
                room: { width_in: 96, depth_in: 96, height_in: 96 },
                design_name: "pre-contract design", lines: LINES,
                // No design_state/sale_order_id/sale_order_name/partner_id at all.
            }),
        },
    });
    assert.equal(h.component.state.designState, "");
    assert.equal(h.component.state.saleOrderId, false);
    assert.equal(h.component.state.saleOrderName, "");
    assert.notEqual(h.component.state.saleOrderName, undefined,
        "must be a real falsy value, never undefined, for the button's defensive guard");
    h.destroy();
});

// ─── Fix 4 — the Create & Confirm button tells the truth ──────────────────

test("no linked quotation: the button reads 'Create & Confirm'", async () => {
    const h = await mountConfigurator({
        actionParams: { design_id: 6005 },
        rpcRoutes: {
            "/southbrook_kitchen/configurator/load_design_lines": () => ({
                room: { width_in: 96, depth_in: 96, height_in: 96 },
                design_name: "no quote yet", lines: LINES,
                sale_order_name: false,
            }),
        },
    });
    await tick();
    const btn = confirmButton(h.root);
    assert.ok(btn, "button renders for an internal user");
    assert.equal(btn.textContent.trim(), "Create & Confirm");
    h.destroy();
});

test("a linked quotation exists: the button reads 'Open quotation <name>' and its title is honest too", async () => {
    const h = await mountConfigurator({
        actionParams: { design_id: 6006 },
        rpcRoutes: {
            "/southbrook_kitchen/configurator/load_design_lines": () => ({
                room: { width_in: 96, depth_in: 96, height_in: 96 },
                design_name: "already quoted", lines: LINES,
                sale_order_id: 4242, sale_order_name: "S01434",
            }),
        },
    });
    await tick();
    const btn = confirmButton(h.root);
    assert.ok(btn);
    assert.equal(btn.textContent.trim(), "Open quotation S01434");
    assert.match(btn.getAttribute("title"), /S01434/);
    h.destroy();
});

test("_createAndConfirm's click handler is unchanged: it still fires action_create_quotation regardless of an existing sale order (server-side routes to the existing draft)", async () => {
    let quotationCalls = 0;
    const h = await mountConfigurator({
        actionParams: { design_id: 6007 },
        rpcRoutes: {
            "/southbrook_kitchen/configurator/load_design_lines": () => ({
                room: { width_in: 96, depth_in: 96, height_in: 96 },
                design_name: "already quoted 2", lines: LINES,
                sale_order_id: 4242, sale_order_name: "S01434",
            }),
            "/web/dataset/call_kw": (params) => {
                if (params.method === "action_create_quotation") {
                    quotationCalls++;
                    return { order_name: "S01434", order_id: 4242 };
                }
                throw new Error("unexpected call_kw: " + params.method);
            },
        },
    });
    await h.component._createAndConfirm();
    assert.equal(quotationCalls, 1,
        "the SAME server method still runs — no client-side branching added, per the fix's own scope");
    h.destroy();
});

test("a fresh, never-quoted design's successful Create & Confirm relabels the button immediately, without waiting for a reload", async () => {
    const h = await mountConfigurator({
        actionParams: { design_id: 6008 },
        rpcRoutes: {
            "/southbrook_kitchen/configurator/load_design_lines": () => ({
                room: { width_in: 96, depth_in: 96, height_in: 96 },
                design_name: "about to be quoted", lines: LINES,
            }),
            "/web/dataset/call_kw": (params) => {
                if (params.method === "action_create_quotation") {
                    // Bare payload shape — no act_window dispatch, rep stays here.
                    return { order_name: "S02000", order_id: 5150 };
                }
                throw new Error("unexpected call_kw: " + params.method);
            },
        },
    });
    await tick();
    assert.equal(confirmButton(h.root).textContent.trim(), "Create & Confirm");

    await h.component._createAndConfirm();
    await tick();

    assert.equal(h.component.state.saleOrderName, "S02000");
    assert.equal(h.component.state.saleOrderId, 5150);
    assert.equal(confirmButton(h.root).textContent.trim(), "Open quotation S02000",
        "the button must relabel immediately on success, not only after the next hydrate");
    h.destroy();
});

test("switching to a brand-new session (_resetForNewSession) clears a stale saleOrderName from a PREVIOUS design", async () => {
    const h = await mountConfigurator({
        actionParams: { design_id: 6009 },
        rpcRoutes: {
            "/southbrook_kitchen/configurator/load_design_lines": () => ({
                room: { width_in: 96, depth_in: 96, height_in: 96 },
                design_name: "old quoted design", lines: LINES,
                sale_order_id: 4242, sale_order_name: "S01434",
            }),
        },
    }, );
    assert.equal(h.component.state.saleOrderName, "S01434");

    // The landing screen's "New Design" action — must not leak the OLD
    // design's quotation label onto a brand-new, unrelated session.
    await h.component._startNewDesign();
    await tick();

    assert.equal(h.component.state.saleOrderName, "");
    assert.equal(h.component.state.saleOrderId, false);
    assert.equal(h.component.state.designState, "");
    assert.equal(confirmButton(h.root).textContent.trim(), "Create & Confirm");
    h.destroy();
});
