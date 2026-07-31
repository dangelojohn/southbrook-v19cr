/** @odoo-module **/
/**
 * Southbrook Kitchen 3D Configurator — shared color-math helper.
 *
 * Task 22 review fix (SBK-036, 2026-07-30) — `mixHex` was duplicated
 * byte-for-byte in corner_cabinet.esm.js (the CORNER_TINT blend) and
 * selection.esm.js (the selection-tint blend). Extracted here so both
 * consumers share one implementation instead of two copies that could
 * silently drift apart.
 */

/**
 * Linear-blend two 0xRRGGBB hex colors by `amount` (0 = all base,
 * 1 = all tint).
 *
 * @param {number} baseHex
 * @param {number} tintHex
 * @param {number} amount — 0..1
 * @returns {number} 0xRRGGBB
 */
export function mixHex(baseHex, tintHex, amount) {
    const br = (baseHex >> 16) & 0xff, bg = (baseHex >> 8) & 0xff, bb = baseHex & 0xff;
    const tr = (tintHex >> 16) & 0xff, tg = (tintHex >> 8) & 0xff, tb = tintHex & 0xff;
    const r = Math.round(br + (tr - br) * amount);
    const g = Math.round(bg + (tg - bg) * amount);
    const b = Math.round(bb + (tb - bb) * amount);
    return (r << 16) | (g << 8) | b;
}
