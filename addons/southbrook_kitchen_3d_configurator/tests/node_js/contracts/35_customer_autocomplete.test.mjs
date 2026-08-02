/**
 * P3 (2026-08-02, client-approved ticket) — the top-bar customer field
 * becomes a real res.partner autocomplete, replacing the free-text
 * input pre-filled with the "Walk-in Customer" magic string
 * (role="combobox" advertised with no real listbox wiring).
 *
 * Facts this suite pins:
 *   - empty state: a search input with the exact placeholder copy,
 *     no chip.
 *   - typing debounces (~250ms) into a res.partner web_search_read
 *     across name/email/phone/ref, limit 8; the secondary line is
 *     city, falling back to email.
 *   - selecting a result (click or Enter) assigns partnerId/
 *     partnerName and renders a chip — the search box disappears.
 *   - the typed query is NEVER itself persisted as a customer: closing
 *     the picker (blur/Escape) without picking a result leaves
 *     partnerId/partnerName exactly as they were.
 *   - the Walk-in button still resolves via the EXISTING
 *     get_or_create_walkin_partner flow (Task 18/SBK-031), now reading
 *     the resolved partner's REAL name off the response instead of a
 *     hardcoded "Walk-in Customer" label.
 *   - the assigned chip's name opens the contact form via the action
 *     service; its (x) unassigns back to the search state.
 *   - quick-create ("Create '<query>'") is gated on a res.partner
 *     check_access_rights('create') round trip, hidden until it
 *     resolves true.
 */
import test from "node:test";
import assert from "node:assert/strict";
import { mountConfigurator } from "../harness/mount_configurator.mjs";
import { tick } from "../harness/tick.mjs";

// Routes every /web/dataset/call_kw hit in this file by `method`, same
// pattern 34_validation_chip.test.mjs uses for the walk-in RPC — throws
// on anything unrouted so a stray/unexpected call fails loudly instead
// of silently resolving undefined.
function callKw(handlers) {
    return (params) => {
        const h = handlers[params.method];
        if (!h) throw new Error("unexpected call_kw method: " + params.method);
        return h(params);
    };
}

const PARTNER_SEARCH_RESULTS = {
    length: 2,
    records: [
        { id: 501, name: "Jane Doe", email: "jane@example.com", phone: "555-1111", city: "Guelph" },
        { id: 502, name: "Jane Smith", email: "jsmith@example.com", phone: "", city: "" },
    ],
};

test("empty state: search input with the exact placeholder, no chip", async () => {
    const h = await mountConfigurator({});
    const input = h.root.querySelector(".o_sbk_customer_input");
    assert.ok(input, "search input renders when no customer is assigned");
    assert.equal(input.getAttribute("placeholder"), "No customer assigned — search contacts…");
    assert.equal(h.root.querySelector(".o_sbk_customer_chip"), null,
        "no chip while unassigned");
    h.destroy();
});

test("typing debounces (~250ms) into res.partner web_search_read across name/email/phone/ref, limit 8", async () => {
    const calls = [];
    const h = await mountConfigurator({
        rpcRoutes: {
            "/web/dataset/call_kw": callKw({
                web_search_read: (params) => { calls.push(params); return PARTNER_SEARCH_RESULTS; },
                check_access_rights: () => false,
            }),
        },
    });
    h.component._onCustomerTyped({ target: { value: "jane" } });
    assert.equal(h.component.state.pickerLoading, true,
        "loading flips true immediately, before the debounce fires");
    assert.equal(calls.length, 0, "the RPC must not fire before the debounce window elapses");

    await tick(280);

    assert.equal(calls.length, 1, "exactly one search fired after the debounce");
    const params = calls[0];
    assert.equal(params.model, "res.partner");
    assert.equal(params.kwargs.limit, 8);
    const domain = JSON.stringify(params.args[0]);
    assert.match(domain, /name/);
    assert.match(domain, /email/);
    assert.match(domain, /phone/);
    assert.match(domain, /ref/);

    assert.equal(h.component.state.pickerResults.length, 2);
    assert.equal(h.component.state.pickerResults[0].name, "Jane Doe");
    assert.equal(h.component.state.pickerResults[0].secondary, "Guelph",
        "secondary line prefers city");
    assert.equal(h.component.state.pickerResults[1].secondary, "jsmith@example.com",
        "secondary line falls back to email when city is blank");
    h.destroy();
});

test("selecting a search result assigns the customer and swaps the search box for a chip", async () => {
    const h = await mountConfigurator({
        rpcRoutes: {
            "/southbrook_kitchen/configurator/save": () => ({ id: 1, name: "saved" }),
        },
    });
    await h.component._setCustomer({ id: 501, name: "Jane Doe" });
    await tick();
    clearTimeout(h.component._autoSaveTimer);

    assert.equal(h.component.state.partnerId, 501);
    assert.equal(h.component.state.partnerName, "Jane Doe");
    assert.equal(h.component.state.pickerOpen, false);
    assert.equal(h.component.state.customerQuery, "",
        "the search query is cleared, never carried over as the assigned label");

    const chip = h.root.querySelector(".o_sbk_customer_chip");
    assert.ok(chip, "the chip renders once a customer is assigned");
    assert.equal(h.root.querySelector(".o_sbk_customer_chip_name").textContent.trim(), "Jane Doe");
    assert.equal(h.root.querySelector(".o_sbk_customer_input"), null,
        "the search input is gone while a customer is assigned");
    h.destroy();
});

test("autocomplete never writes free text: typing without selecting leaves partnerId/partnerName untouched", async () => {
    const h = await mountConfigurator({
        rpcRoutes: {
            "/web/dataset/call_kw": callKw({
                web_search_read: () => PARTNER_SEARCH_RESULTS,
                check_access_rights: () => false,
            }),
        },
    });
    assert.equal(h.component.state.partnerId, null);
    assert.equal(h.component.state.partnerName, "");

    await h.component._openCustomerPicker();
    h.component._onCustomerTyped({ target: { value: "some random typed text" } });
    await tick(280);
    assert.equal(h.component.state.pickerResults.length, 2, "the search did run");

    // Closing WITHOUT clicking/Enter-selecting a result — the typed text
    // must never become the assigned customer.
    h.component._closeCustomerPicker();
    await tick(200);   // past the 150ms close delay

    assert.equal(h.component.state.pickerOpen, false);
    assert.equal(h.component.state.partnerId, null,
        "no partner was ever assigned from typing alone");
    assert.equal(h.component.state.partnerName, "",
        "the assigned-customer label was never set from typed text");
    assert.equal(h.root.querySelector(".o_sbk_customer_chip"), null,
        "no chip renders — nothing was actually assigned");
    h.destroy();
});

test("Escape closes the picker without touching partnerId/partnerName either", async () => {
    const h = await mountConfigurator({});
    h.component.state.pickerOpen = true;
    h.component.state.customerQuery = "abandoned search";
    h.component._onCustomerInputKeydown({
        key: "Escape", preventDefault() {}, stopPropagation() {},
    });
    assert.equal(h.component.state.pickerOpen, false);
    assert.equal(h.component.state.partnerId, null);
    assert.equal(h.component.state.partnerName, "");
    h.destroy();
});

test("ArrowDown highlights the first result and Enter selects it", async () => {
    const h = await mountConfigurator({
        rpcRoutes: {
            "/southbrook_kitchen/configurator/save": () => ({ id: 1, name: "saved" }),
        },
    });
    h.component.state.pickerOpen = true;
    h.component.state.pickerResults = PARTNER_SEARCH_RESULTS.records.map((r) => ({
        id: r.id, name: r.name, secondary: r.city,
    }));

    h.component._onCustomerInputKeydown({ key: "ArrowDown", preventDefault() {} });
    assert.equal(h.component.state.pickerActiveIndex, 0);
    assert.equal(h.component._customerActiveOptionId(), "sbkCustomerOpt501");

    h.component._onCustomerInputKeydown({ key: "Enter", preventDefault() {} });
    await tick();
    clearTimeout(h.component._autoSaveTimer);

    assert.equal(h.component.state.partnerId, 501);
    assert.equal(h.component.state.partnerName, "Jane Doe");
    h.destroy();
});

test("Walk-in button still resolves: assigns the SERVER's real name, not a hardcoded label", async () => {
    let walkinCalls = 0;
    const h = await mountConfigurator({
        rpcRoutes: {
            "/web/dataset/call_kw": callKw({
                get_or_create_walkin_partner: () => {
                    walkinCalls++;
                    // The real contract post-P3: {id, name}, and the
                    // resolved record can legitimately be the legacy
                    // name — proving the client never hardcodes it.
                    return { id: 4242, name: "Walk-in Retail" };
                },
            }),
        },
    });
    await h.component._useWalkInCustomer();
    await tick();
    clearTimeout(h.component._autoSaveTimer);

    assert.equal(walkinCalls, 1);
    assert.equal(h.component.state.partnerId, 4242);
    assert.equal(h.component.state.partnerName, "Walk-in Retail",
        "the chip shows the server's real resolved name, never a hardcoded 'Walk-in Customer'");
    h.destroy();
});

test("Walk-in button tolerates a legacy bare-id response shape (defensive fallback only)", async () => {
    const h = await mountConfigurator({
        rpcRoutes: {
            "/web/dataset/call_kw": callKw({
                get_or_create_walkin_partner: () => 4242,   // pre-P3 shape
            }),
        },
    });
    await h.component._useWalkInCustomer();
    await tick();
    clearTimeout(h.component._autoSaveTimer);

    assert.equal(h.component.state.partnerId, 4242);
    assert.equal(h.component.state.partnerName, "Walk-in Customer");
    h.destroy();
});

test("the chip's (x) unassigns back to the search state", async () => {
    const h = await mountConfigurator({
        rpcRoutes: {
            "/southbrook_kitchen/configurator/save": () => ({ id: 1, name: "saved" }),
        },
    });
    await h.component._setCustomer({ id: 501, name: "Jane Doe" });
    await tick();
    clearTimeout(h.component._autoSaveTimer);
    assert.ok(h.root.querySelector(".o_sbk_customer_chip"));

    await h.component._unassignCustomer();
    await tick();
    clearTimeout(h.component._autoSaveTimer);

    assert.equal(h.component.state.partnerId, null);
    assert.equal(h.component.state.partnerName, "");
    assert.equal(h.root.querySelector(".o_sbk_customer_chip"), null);
    assert.ok(h.root.querySelector(".o_sbk_customer_input"),
        "the search box comes back once unassigned");
    h.destroy();
});

test("the chip's name opens the contact form via the action service", async () => {
    const h = await mountConfigurator({
        rpcRoutes: {
            "/southbrook_kitchen/configurator/save": () => ({ id: 1, name: "saved" }),
        },
    });
    await h.component._setCustomer({ id: 501, name: "Jane Doe" });
    await tick();
    clearTimeout(h.component._autoSaveTimer);

    h.component._openCustomerForm();
    assert.equal(h.actionCalls.length, 1);
    const action = h.actionCalls[0];
    assert.equal(action.res_model, "res.partner");
    assert.equal(action.res_id, 501);
    assert.equal(action.type, "ir.actions.act_window");
    h.destroy();
});

test("quick-create is hidden until check_access_rights resolves true, then creates + assigns", async () => {
    const h = await mountConfigurator({
        rpcRoutes: {
            "/web/dataset/call_kw": callKw({
                web_search_read: () => ({ length: 0, records: [] }),
                check_access_rights: () => true,
                create: (params) => {
                    assert.deepEqual(params.args, [{ name: "Brand New Co" }]);
                    return 9001;
                },
            }),
            "/southbrook_kitchen/configurator/save": () => ({ id: 1, name: "saved" }),
        },
    });
    // Before the permission check resolves, the row must not render/act.
    assert.equal(h.component.state.customerCreateAllowed, null);

    await h.component._openCustomerPicker();
    await tick();   // _loadCustomerCreatePermission resolves
    assert.equal(h.component.state.customerCreateAllowed, true);

    h.component._onCustomerTyped({ target: { value: "Brand New Co" } });
    await tick(280);
    assert.equal(h.component.state.pickerResults.length, 0);
    assert.equal(h.component._customerExactMatch(), false);

    await h.component._createCustomerFromQuery();
    await tick();
    clearTimeout(h.component._autoSaveTimer);

    assert.equal(h.component.state.partnerId, 9001);
    assert.equal(h.component.state.partnerName, "Brand New Co");
    h.destroy();
});

test("quick-create stays hidden (never creates) when check_access_rights denies", async () => {
    const h = await mountConfigurator({
        rpcRoutes: {
            "/web/dataset/call_kw": callKw({
                check_access_rights: () => false,
            }),
        },
    });
    await h.component._openCustomerPicker();
    await tick();
    assert.equal(h.component.state.customerCreateAllowed, false);

    h.component.state.customerQuery = "Anything";
    await h.component._createCustomerFromQuery();   // must no-op, not throw
    assert.equal(h.component.state.partnerId, null);
    h.destroy();
});
