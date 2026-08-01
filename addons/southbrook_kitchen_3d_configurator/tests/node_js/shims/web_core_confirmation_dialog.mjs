/**
 * Boundary shim for "@web/core/confirmation_dialog/confirmation_dialog"
 * (real module lives in Odoo's web addon; not an npm package). Task 35
 * imports the real `ConfirmationDialog` class only as an OPAQUE token
 * to pass to the `dialog` service's `add(Component, props)` call — this
 * harness never actually renders a dialog (no real modal/backdrop
 * exists under jsdom for this suite), so a minimal placeholder class is
 * enough: it just needs to be importable and usable as an identity
 * (tests can assert `dialogCalls[0].component === ConfirmationDialog`),
 * exactly like harness/kitchen_canvas_mock.mjs substitutes for the real
 * <KitchenCanvas> at the Three.js/WebGL boundary.
 *
 * The real dialog SERVICE itself is mocked separately, per-test, via
 * `mountConfigurator({ services: { dialog: {...} } })` (see
 * harness/mount_configurator.mjs's `defaultServices.dialog`) — that
 * mock is what actually invokes `props.confirm()`/`props.cancel()` to
 * simulate the rep's choice.
 */
export class ConfirmationDialog {}
