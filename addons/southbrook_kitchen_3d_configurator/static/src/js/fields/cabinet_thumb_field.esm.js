/** @odoo-module **/
/**
 * Southbrook cabinet product thumbnail field widget ("sb_cabinet_thumb").
 *
 * Design-system rule ("Cabinet iconography — one source",
 * `.claude/skills/southbrook-design-system/SKILL.md`): every surface that
 * shows a cabinet icon must render it through
 * `cabinet_thumb.esm.js`'s `cabinetThumbSVG()` — never a second,
 * hand-drawn variant. This widget applies that rule to the backend
 * Product views (Cabinet Products kanban card + list column) so a
 * cabinet product shows the SAME axonometric icon the 3D configurator's
 * own inventory panel already renders for it
 * (`kitchen_configurator.js`'s `_rowThumbMarkup`), instead of the
 * generic Odoo product-image placeholder.
 *
 * Bind this widget to `image_128` (or any binary image field) — it
 * fully replaces that field's own rendering:
 *   - `southbrook_is_cabinet` true  → the axonometric SVG icon.
 *   - `southbrook_is_cabinet` false → falls back to the bound image
 *     field's own standard photo (same URL-based lookup `ImageField`
 *     uses), so non-cabinet products are visually untouched.
 * Never writes to any image field — this is read-only rendering, so
 * `image_1920`/PIL binary constraints never enter into it.
 */
import {Component, markup, xml} from "@odoo/owl";
import {registry} from "@web/core/registry";
import {standardFieldProps} from "@web/views/fields/standard_field_props";
import {imageUrl} from "@web/core/utils/urls";
import {cabinetThumbSVG} from "@southbrook_kitchen_3d_configurator/js/canvas/cabinet_thumb.esm";

export class CabinetThumbField extends Component {
    static template = xml`
        <t t-if="isCabinet">
            <span class="o_sbk_cabinet_thumb_field"
                  t-att-title="titleText"
                  t-out="thumbMarkup"/>
        </t>
        <t t-else="">
            <img t-if="fallbackUrl"
                 class="o_sbk_cabinet_thumb_fallback_img"
                 t-att-src="fallbackUrl"
                 t-att-alt="titleText"/>
        </t>
    `;
    static props = {
        ...standardFieldProps,
        sizePx: {type: Number, optional: true},
    };
    static defaultProps = {
        sizePx: 46,
    };

    get isCabinet() {
        return Boolean(this.props.record.data.southbrook_is_cabinet);
    }

    // cabinetThumbSVG's own contract (see cabinet_thumb.esm.js header):
    // `cabinet_type` is a fixed Selection vocabulary and `width_in` is
    // coerced to a Number inside that module — both numeric-only /
    // controlled-vocabulary inputs, never free text. `name`/`sku` feed
    // ONLY the family-inference keyword matching (thumbFamily) and are
    // never interpolated into the returned SVG string — the markup's
    // aria-label is always drawn from cabinet_thumb.esm.js's own fixed
    // FAMILY_LABEL table. `thumb_family` is likewise a fixed Selection
    // vocabulary (southbrook_thumb_family on product.template) — when
    // set it short-circuits thumbFamily()'s inference outright, but
    // never touches the markup string directly either. Same trust
    // boundary `kitchen_configurator.js`'s `_rowThumbMarkup` already
    // documents and relies on, so trusting this as markup() here is safe.
    get thumbMarkup() {
        const data = this.props.record.data;
        const rawWidth = Number(data.southbrook_width_in);
        const product = {
            cabinet_type: String(data.southbrook_cabinet_type || ""),
            width_in: Number.isFinite(rawWidth) ? rawWidth : 0,
            name: String(data.name || ""),
            sku: String(data.default_code || ""),
            // Debt fix (2026-08-02) — explicit override wins outright in
            // thumbFamily(); reading it here means editing the field on
            // this very form updates the preview immediately, before save,
            // instead of only taking effect after a reload.
            thumb_family: data.southbrook_thumb_family || false,
        };
        return markup(cabinetThumbSVG(product, this.props.sizePx));
    }

    // Non-cabinet fallback — mirrors ImageField.getUrl()'s URL-based
    // path (kanban/list reads only carry a size marker for binary
    // fields, not the real bytes, so a data-URI here would be wrong;
    // the browser fetches the real image from this route instead).
    get fallbackUrl() {
        const record = this.props.record;
        if (!record.resId || !record.data[this.props.name]) {
            return null;
        }
        return imageUrl(record.resModel, record.resId, this.props.name, {
            unique: record.data.write_date,
        });
    }

    // Rendered via t-att (auto-escaped by Owl), never concatenated
    // into the markup()-trusted SVG string above — the one place this
    // widget touches free-text product data (name), it goes through
    // the framework's normal attribute escaping.
    get titleText() {
        return this.props.record.data.name || "";
    }
}

export const cabinetThumbField = {
    component: CabinetThumbField,
    supportedTypes: ["binary"],
    fieldDependencies: [
        {name: "southbrook_is_cabinet", type: "boolean"},
        {name: "southbrook_cabinet_type", type: "selection"},
        {name: "southbrook_thumb_family", type: "selection"},
        {name: "southbrook_width_in", type: "float"},
        {name: "default_code", type: "char"},
        {name: "name", type: "char"},
        {name: "write_date", type: "datetime"},
    ],
    extractProps: ({options}) => ({
        sizePx: (options && options.size_px) || undefined,
    }),
};

registry.category("fields").add("sb_cabinet_thumb", cabinetThumbField);
