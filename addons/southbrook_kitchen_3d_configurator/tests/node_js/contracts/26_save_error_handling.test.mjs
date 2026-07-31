/**
 * Fix-round (coordinator review, corner-remediation 2026-07-30, Task 12
 * follow-up) — CRITICAL finding: `_saveDesign`/`_autoSave` resend the
 * ENTIRE `state.room` on every save (kitchen_configurator.js), and
 * `save_design` (controllers/main.py) can reject with a normal
 * (non-thrown) JSON body in three shapes: {error, detail}
 * (invalid_wall), {error, error_code} (e.g. ROOM_OUT_OF_BOUNDS), or
 * {ok:false, reason} (not_found/forbidden). Before this fix, NEITHER
 * save path inspected `result.error`/`result.ok` at all — unlike every
 * other RPC site in this file (`_hydrateFromDesign`'s NOT_FOUND/
 * ACCESS_DENIED check, `_refreshLayout`'s error/error_code mirror) — so
 * a structured rejection was silently treated as success: `_saveDesign`
 * showed a FALSE "Design saved" toast, and `_autoSave` failed with zero
 * visible signal (its catch block never even ran — the RPC itself
 * succeeded; only the JSON body says "no").
 *
 * This locks the fix: `_saveErrorMessage(result)` is the ONE place that
 * interprets all three failure shapes (plus the null/success case), and
 * both `_saveDesign`/`_autoSave` route through it before touching any
 * success-path state. `_autoSave`'s failure toast is deduped (shown
 * once per ongoing failure condition, reset by the next successful
 * save) so a persistently-failing debounced auto-save doesn't spam.
 */
import test from "node:test";
import assert from "node:assert/strict";
import { mountConfigurator } from "../harness/mount_configurator.mjs";

function messagesOf(h) {
    return h.component._toastStore.toasts.map(t => t.message);
}

// ─── _saveErrorMessage — pure interpretation of the three shapes ──────────

test("_saveErrorMessage: null/success-shaped results return null", async () => {
    const h = await mountConfigurator({});
    const c = h.component;
    assert.equal(c._saveErrorMessage(null), null);
    assert.equal(c._saveErrorMessage(undefined), null);
    assert.equal(c._saveErrorMessage({ id: 1, name: "Kitchen" }), null);
});

test("_saveErrorMessage: {error} without detail uses error as the message", async () => {
    const h = await mountConfigurator({});
    assert.equal(
        h.component._saveErrorMessage({ error: "boom", error_code: "X" }),
        "boom");
});

test("_saveErrorMessage: {error, detail} (invalid_wall shape) prefers detail", async () => {
    const h = await mountConfigurator({});
    assert.equal(
        h.component._saveErrorMessage({
            error: "invalid_wall",
            detail: "wall='banana' is not a recognised room wall.",
        }),
        "wall='banana' is not a recognised room wall.");
});

test("_saveErrorMessage: {ok:false, reason:'not_found'|'forbidden'|other}", async () => {
    const h = await mountConfigurator({});
    const c = h.component;
    assert.match(c._saveErrorMessage({ ok: false, reason: "not_found" }),
        /no longer exists/i);
    assert.match(c._saveErrorMessage({ ok: false, reason: "forbidden" }),
        /write access/i);
    assert.equal(c._saveErrorMessage({ ok: false, reason: "whatever" }),
        "Save failed.");
});

// ─── _saveDesign — must not show a false success toast on rejection ───────

test("_saveDesign on {error, error_code: ROOM_OUT_OF_BOUNDS} surfaces the error, never 'Design saved'", async () => {
    const h = await mountConfigurator({
        rpcRoutes: {
            "/southbrook_kitchen/configurator/save": () => ({
                error: 'Room size is out of range: width 99999" (must be 12"-480").',
                error_code: "ROOM_OUT_OF_BOUNDS",
            }),
        },
    });
    await h.component._saveDesign();
    const messages = messagesOf(h);
    assert.ok(!messages.includes("Design saved"),
        "a rejected save must never show the success toast");
    const errToast = h.component._toastStore.toasts.find(
        t => t.message.includes("out of range"));
    assert.ok(errToast, "the readable server message must be surfaced");
    assert.equal(errToast.type, "danger");
    assert.equal(h.component.state.saving, false,
        "the saving flag must still be cleared (finally still runs)");
});

test("_saveDesign on {ok:false, reason:'not_found'} surfaces a readable message, not success", async () => {
    const h = await mountConfigurator({
        rpcRoutes: {
            "/southbrook_kitchen/configurator/save": () => (
                { ok: false, reason: "not_found" }),
        },
    });
    await h.component._saveDesign();
    const messages = messagesOf(h);
    assert.ok(!messages.includes("Design saved"));
    assert.ok(messages.some(m => /no longer exists/i.test(m)));
});

test("_saveDesign on {ok:false, reason:'forbidden'} surfaces a readable message, not success", async () => {
    const h = await mountConfigurator({
        rpcRoutes: {
            "/southbrook_kitchen/configurator/save": () => (
                { ok: false, reason: "forbidden" }),
        },
    });
    await h.component._saveDesign();
    const messages = messagesOf(h);
    assert.ok(!messages.includes("Design saved"));
    assert.ok(messages.some(m => /write access/i.test(m)));
});

test("_saveDesign on {error:'invalid_wall', detail} uses detail as the message, not success", async () => {
    const h = await mountConfigurator({
        rpcRoutes: {
            "/southbrook_kitchen/configurator/save": () => ({
                error: "invalid_wall",
                detail: "wall='banana' is not a recognised room wall (must be one of back, front, left, right).",
            }),
        },
    });
    await h.component._saveDesign();
    const messages = messagesOf(h);
    assert.ok(!messages.includes("Design saved"));
    assert.ok(messages.some(m => m.includes("banana")),
        "must use `detail`, not the bare 'invalid_wall' code, as the message");
});

test("_saveDesign on a normal success response still shows 'Design saved' (regression guard)", async () => {
    const h = await mountConfigurator({
        rpcRoutes: {
            "/southbrook_kitchen/configurator/save": () => (
                { id: 999, name: "Fine Kitchen" }),
        },
    });
    await h.component._saveDesign();
    assert.ok(messagesOf(h).includes("Design saved"));
    assert.equal(h.component.state.designId, 999);
});

// ─── _autoSave — must not fail silently, and must dedup ────────────────────

test("_autoSave on a structured rejection shows exactly one toast (not silent)", async () => {
    const h = await mountConfigurator({
        rpcRoutes: {
            "/southbrook_kitchen/configurator/save": () => ({
                error: 'Room size is out of range: width 99999" (must be 12"-480").',
                error_code: "ROOM_OUT_OF_BOUNDS",
            }),
        },
    });
    let toastCalls = 0;
    const origToast = h.component._toast.bind(h.component);
    h.component._toast = (msg, opts) => { toastCalls += 1; return origToast(msg, opts); };

    await h.component._autoSave();
    assert.equal(toastCalls, 1,
        "SBK-021/022 fix-round — a rejected auto-save must be visible, not silent");
    assert.equal(h.component._autoSaveErrorShown, true);
});

test("_autoSave dedups the failure toast across repeated ongoing failures", async () => {
    const h = await mountConfigurator({
        rpcRoutes: {
            "/southbrook_kitchen/configurator/save": () => ({
                error: 'Room size is out of range: width 99999" (must be 12"-480").',
                error_code: "ROOM_OUT_OF_BOUNDS",
            }),
        },
    });
    let toastCalls = 0;
    const origToast = h.component._toast.bind(h.component);
    h.component._toast = (msg, opts) => { toastCalls += 1; return origToast(msg, opts); };

    await h.component._autoSave();
    await h.component._autoSave();
    await h.component._autoSave();
    assert.equal(toastCalls, 1,
        "the SAME ongoing failure must not re-toast on every debounced retry");
});

test("_autoSave's failure toast reappears after an intervening success", async () => {
    let shouldFail = true;
    const h = await mountConfigurator({
        rpcRoutes: {
            "/southbrook_kitchen/configurator/save": () => shouldFail
                ? { error: "bad", error_code: "ROOM_OUT_OF_BOUNDS" }
                : { id: 42, name: "Toggle Kitchen" },
        },
    });
    let toastCalls = 0;
    const origToast = h.component._toast.bind(h.component);
    h.component._toast = (msg, opts) => { toastCalls += 1; return origToast(msg, opts); };

    await h.component._autoSave();
    assert.equal(toastCalls, 1, "first failure toasts");

    shouldFail = false;
    await h.component._autoSave();
    assert.equal(toastCalls, 1, "a success must not itself toast an error");
    assert.equal(h.component._autoSaveErrorShown, false,
        "the dedup flag must reset once a save succeeds");

    shouldFail = true;
    await h.component._autoSave();
    assert.equal(toastCalls, 2,
        "a NEW failure after an intervening success must surface again");
});

test("_saveDesign succeeding also resets _autoSave's dedup flag", async () => {
    const h = await mountConfigurator({
        rpcRoutes: {
            "/southbrook_kitchen/configurator/save": () => (
                { id: 7, name: "Manual Save Kitchen" }),
        },
    });
    h.component._autoSaveErrorShown = true;   // simulate a prior auto-save failure
    await h.component._saveDesign();
    assert.equal(h.component._autoSaveErrorShown, false,
        "a successful MANUAL save should also clear a stale auto-save failure flag");
});

test("_autoSave on a normal success response is unaffected (regression guard)", async () => {
    const h = await mountConfigurator({
        rpcRoutes: {
            "/southbrook_kitchen/configurator/save": () => (
                { id: 55, name: "Auto Kitchen" }),
        },
    });
    let toastCalls = 0;
    const origToast = h.component._toast.bind(h.component);
    h.component._toast = (msg, opts) => { toastCalls += 1; return origToast(msg, opts); };

    await h.component._autoSave();
    assert.equal(toastCalls, 0, "auto-save success stays silent, same as before this fix");
    assert.equal(h.component.state.designId, 55);
});
