/**
 * Task 38 (client hardening batch, corner-remediation 2026-07-30) —
 * `_refreshValidation()` request-sequencing guard.
 *
 * `_refreshValidation()` is fired-and-forgotten from five call sites
 * (hydrate, manual save, auto-save, arrange, the validation panel's
 * walk-in fix action — see kitchen_configurator.js's own comment on the
 * method). Before this fix, each call unconditionally overwrote
 * `state.validation` with whatever its own `/validate` RPC resolved,
 * with no memory of whether a NEWER `_refreshValidation()` call had
 * been issued (or had already landed) in the meantime. Under real-world
 * latency two of those five requests can resolve out of order — e.g. a
 * save's post-save refresh lands, then an earlier hydrate-triggered
 * refresh's slower response arrives and clobbers it with stale data.
 *
 * The fix: a monotonic `this._validationReqSeq` captured before each
 * RPC; a response is only applied if no newer request has been issued
 * since. This test drives that race directly — two overlapping
 * `_refreshValidation()` calls, resolved deliberately OUT OF issue
 * order (the newer one first) — using the rpc shim's documented
 * direct-import path (`shims/web_core_rpc.mjs`'s own header comment:
 * "Import this shim directly ... to configure routes") to re-point the
 * `/validate` route AFTER mount, so the mount-time hydrate's own
 * (already-resolved, benign) `_refreshValidation()` call doesn't
 * consume one of this test's two controlled resolvers.
 */
import test from "node:test";
import assert from "node:assert/strict";
import { mountConfigurator } from "../harness/mount_configurator.mjs";
import { __onRoute } from "../shims/web_core_rpc.mjs";

function mountHydrated() {
    return mountConfigurator({
        actionParams: { design_id: 3801 },
        rpcRoutes: {
            "/southbrook_kitchen/configurator/load_design_lines": () => ({
                room: { width_in: 96, depth_in: 96, height_in: 96 },
                design_name: "T38 design", lines: [],
            }),
        },
    });
}

test("_refreshValidation discards a stale response that resolves AFTER a newer request already landed", async () => {
    const h = await mountHydrated();

    // Re-point /validate, post-mount, to a controllable resolver queue.
    // The mount-time hydrate already fired-and-resolved its OWN
    // _refreshValidation() against the benign DEFAULT_ROUTES handler
    // before this line runs, so it never touches `resolvers` below.
    const resolvers = [];
    __onRoute("/southbrook_kitchen/configurator/validate", () => new Promise((resolve) => {
        resolvers.push(resolve);
    }));

    const pOlder = h.component._refreshValidation();   // issued first  (seq N)
    const pNewer = h.component._refreshValidation();   // issued second (seq N+1, supersedes)
    assert.equal(resolvers.length, 2, "both overlapping calls each fired their own RPC");

    // The NEWER request's RPC resolves FIRST — a realistic race (e.g.
    // the older request's round-trip was the slower one).
    resolvers[1]({
        issues: [{ code: "EMPTY_DESIGN", severity: "blocking",
                   message: "fresh response", line_ids: [], layout_keys: [] }],
        counts: { blocking: 1, warning: 0 },
        ready: false,
    });
    await pNewer;
    assert.equal(h.component.state.validation.counts.blocking, 1,
        "the fresher (newer-issued) response is applied");
    assert.equal(h.component.state.validation.ready, false);

    // The STALE (older) request's RPC resolves AFTER the fresher one
    // already landed — this must be discarded, not allowed to clobber
    // the state the fresher response just wrote.
    resolvers[0]({
        issues: [],
        counts: { blocking: 0, warning: 0 },
        ready: true,
    });
    await pOlder;

    assert.equal(h.component.state.validation.counts.blocking, 1,
        "a stale response landing after a fresher one must NOT overwrite it");
    assert.equal(h.component.state.validation.issues.length, 1,
        "the stale response's empty issues list must not erase the fresher issue");
    assert.equal(h.component.state.validation.ready, false,
        "ready must still reflect the fresher (still-blocking) response, not the stale all-clear");
    h.destroy();
});

test("_refreshValidation still applies the response normally with no interleaving (no regression in the common case)", async () => {
    const h = await mountHydrated();
    __onRoute("/southbrook_kitchen/configurator/validate", () => ({
        issues: [{ code: "EMPTY_DESIGN", severity: "blocking",
                   message: "x", line_ids: [], layout_keys: [] }],
        counts: { blocking: 1, warning: 0 },
        ready: false,
    }));
    await h.component._refreshValidation();
    assert.equal(h.component.state.validation.counts.blocking, 1);
    assert.equal(h.component.state.validation.issues.length, 1);
    assert.equal(h.component.state.validation.ready, false);
    h.destroy();
});

test("a second, later call still updates state normally once the first has already resolved (sequential, not interleaved)", async () => {
    const h = await mountHydrated();
    let call = 0;
    __onRoute("/southbrook_kitchen/configurator/validate", () => {
        call += 1;
        return call === 1
            ? { issues: [], counts: { blocking: 0, warning: 0 }, ready: true }
            : { issues: [{ code: "EMPTY_DESIGN", severity: "blocking", message: "y",
                           line_ids: [], layout_keys: [] }],
                counts: { blocking: 1, warning: 0 }, ready: false };
    });
    await h.component._refreshValidation();
    assert.equal(h.component.state.validation.ready, true);
    await h.component._refreshValidation();
    assert.equal(h.component.state.validation.ready, false,
        "a later, non-overlapping call must still update state normally");
    h.destroy();
});
