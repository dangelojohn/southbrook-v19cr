# SPDX-License-Identifier: LGPL-3.0-only
"""Pure parsing logic for supplier catalogue feeds.

Deliberately free of ORM access so it can be unit-tested without a database
and reasoned about on its own. Everything here is a function of its inputs.

The shapes handled are Shopify's public ``/products.json``, which is what
eurofitcanada.com serves. Measured on that feed 2026-07-27: 548 products,
3743 variant rows, 2274 distinct SKUs.
"""

import re

# ---------------------------------------------------------------------------
# Volume price tiers
# ---------------------------------------------------------------------------
# Shopify has no native tiered pricing, so suppliers encode volume breaks as
# a variant option named "Quantity" or "Qty". On the Eurofit feed this affects
# 234 of 548 products and generates 2511 of the 3743 variant rows — 67%.
#
# Treating these as product attributes would create ~1900 phantom variants of
# the same physical part, each with its own SKU, cost and stock record, and
# would corrupt BoM arithmetic (mrp.bom assumes one variant is one physical
# unit, not a box of 100). They belong on product.supplierinfo.min_qty, which
# is exactly what Odoo's _select_seller(quantity=...) reads.
TIER_OPTION_NAMES = {"quantity", "qty"}

# Observed tier label forms, in the order they must be tried:
#   "1-24pcs"        -> 1        (range: lower bound)
#   "25pcs or more"  -> 25
#   "60+ Sets"       -> 60
#   "Less than 1 Case" -> 1
#   "3 Cases or more"  -> 3
_TIER_RANGE = re.compile(r"^\s*(\d+)\s*[-–]\s*\d+")
_TIER_ATLEAST = re.compile(r"(\d+)\s*(?:\+|or\s+more|and\s+(?:up|over))", re.I)
_TIER_ANY_INT = re.compile(r"(\d+)")


def parse_price_tier(label):
    """Return the minimum quantity a tier label describes.

    Returns 1 for anything unrecognised — the safe default, since a
    supplierinfo row with min_qty=1 is always applicable and simply loses the
    volume discount rather than mispricing an order.

    The unit is deliberately NOT interpreted. "3 Cases or more" yields 3, not
    3 x case-size, because the case size is not stated anywhere in the feed.
    The raw label is preserved alongside so a buyer can see what the number
    meant.
    """
    if not label:
        return 1
    text = str(label).strip()
    m = _TIER_RANGE.match(text)
    if m:
        return max(int(m.group(1)), 1)
    m = _TIER_ATLEAST.search(text)
    if m:
        return max(int(m.group(1)), 1)
    if re.match(r"^\s*less\s+than", text, re.I):
        return 1
    m = _TIER_ANY_INT.search(text)
    if m:
        return max(int(m.group(1)), 1)
    return 1


# ---------------------------------------------------------------------------
# SKU hygiene
# ---------------------------------------------------------------------------
# 457 of Eurofit's 2274 distinct SKUs (20%) are not part numbers at all but
# assembly formulas:
#     BLUM-22F2500+BLUM-22F3500+BLUM-78Z5500T+BLUM-22L8000
#     (HTT-9156338 x 2) + HTT-9227245
# They cannot serve as default_code — but they encode a bill of materials,
# which is worth more than a part number. They are flagged, not discarded.
_KIT_MARKERS = ("+", "(", ")")


def is_kit_formula(sku):
    """True when a SKU is an assembly expression rather than a part number."""
    if not sku:
        return False
    s = str(sku)
    if any(ch in s for ch in _KIT_MARKERS):
        return True
    return bool(re.search(r"\s+x\s*\d", s, re.I))


def parse_kit_components(sku):
    """Best-effort decomposition of a kit formula into (part, qty) pairs.

    Handles the two observed shapes: '+'-joined part lists, and 'PART x N'
    multipliers. Returns [] when nothing sensible can be extracted — the
    caller should then fall back to human review rather than guessing.
    """
    if not sku or not is_kit_formula(sku):
        return []
    text = re.sub(r"[()]", " ", str(sku))
    parts = []
    for chunk in text.split("+"):
        chunk = chunk.strip()
        if not chunk:
            continue
        m = re.match(r"^(.*?)\s*x\s*(\d+)\s*$", chunk, re.I)
        if m:
            code, qty = m.group(1).strip(), int(m.group(2))
        else:
            code, qty = chunk, 1
        code = code.strip()
        if code:
            parts.append((code, qty))
    return parts if len(parts) > 1 or (parts and parts[0][1] > 1) else []


# ---------------------------------------------------------------------------
# Category normalisation
# ---------------------------------------------------------------------------
# The feed carries 70 distinct product_type values containing case duplicates
# ("push open piston" / "Push Open Piston"), a typo ("Contact Adhersive") and
# eleven separate hinge spellings. They collapse to 13 functional branches.
#
# Rules are ordered: the FIRST match wins, so put specific before general
# ("hinge plate" must precede "hinge").
CATEGORY_RULES = [
    ("hinge plate", ("Hinges", "Mounting Plates")),
    ("hinge cover", ("Hinges", "Covers & Trim")),
    ("hinge machine", ("Tools & Jigs", "Machines")),
    ("punching", ("Tools & Jigs", "Machines")),
    ("hinge", ("Hinges", "Hinges")),
    ("undermount", ("Drawer Systems", "Slides")),
    ("side mounted slide", ("Drawer Systems", "Slides")),
    ("ball bearing slide", ("Drawer Systems", "Slides")),
    ("base mounted slide", ("Drawer Systems", "Slides")),
    ("slide accessories", ("Drawer Systems", "Slides")),
    ("metal drawer system", ("Drawer Systems", "Metal Drawer Boxes")),
    ("drawer organizer", ("Drawer Systems", "Organisers")),
    ("cutlery", ("Drawer Systems", "Organisers")),
    ("anti-slip", ("Drawer Systems", "Organisers")),
    ("drawer lock", ("Drawer Systems", "Accessories")),
    ("push", ("Opening Systems", "Push-to-Open")),
    ("opening system", ("Opening Systems", "Push-to-Open")),
    ("piston", ("Opening Systems", "Push-to-Open")),
    ("handle", ("Handles & Knobs", "Handles")),
    ("knob", ("Handles & Knobs", "Knobs")),
    ("waste bin", ("Storage Solutions", "Waste Bins")),
    ("spice", ("Storage Solutions", "Pull-Outs & Racks")),
    ("lazy susan", ("Storage Solutions", "Pull-Outs & Racks")),
    ("blind corner", ("Storage Solutions", "Pull-Outs & Racks")),
    ("pantry", ("Storage Solutions", "Pull-Outs & Racks")),
    ("shoe rack", ("Storage Solutions", "Pull-Outs & Racks")),
    ("laundry", ("Storage Solutions", "Pull-Outs & Racks")),
    ("draining", ("Storage Solutions", "Pull-Outs & Racks")),
    ("door rack", ("Storage Solutions", "Pull-Outs & Racks")),
    ("sink", ("Storage Solutions", "Pull-Outs & Racks")),
    ("soap", ("Storage Solutions", "Pull-Outs & Racks")),
    ("iron board", ("Storage Solutions", "Pull-Outs & Racks")),
    ("closet hanger", ("Storage Solutions", "Pull-Outs & Racks")),
    ("glass hanger", ("Storage Solutions", "Pull-Outs & Racks")),
    ("shelf support", ("Storage Solutions", "Pull-Outs & Racks")),
    ("closet rod", ("Closet Systems", "Rods")),
    ("leg", ("Cabinet Structure", "Legs & Levellers")),
    ("castor", ("Cabinet Structure", "Legs & Levellers")),
    ("suspension", ("Cabinet Structure", "Legs & Levellers")),
    ("corbel", ("Cabinet Structure", "Decorative Woodwork")),
    ("wood turning", ("Cabinet Structure", "Decorative Woodwork")),
    ("sliding door", ("Sliding & Folding", "Systems")),
    # Both spellings: the feed contains "Glue & Adhesive" and the misspelled
    # "Contact Adhersive" (r before s), which a plain "adhes" rule misses.
    ("adhes", ("Adhesives & Sealants", "Adhesives")),
    ("adher", ("Adhesives & Sealants", "Adhesives")),
    ("glue", ("Adhesives & Sealants", "Adhesives")),
    ("edge banding", ("Consumables", "Edging & Wrap")),
    ("plastic wrap", ("Consumables", "Edging & Wrap")),
    ("screw", ("Fasteners & Fittings", "Fasteners")),
    ("staple", ("Fasteners & Fittings", "Fasteners")),
    ("nail", ("Fasteners & Fittings", "Fasteners")),
    ("spacer", ("Fasteners & Fittings", "Fasteners")),
    ("grommet", ("Fasteners & Fittings", "Fasteners")),
    ("bamper", ("Fasteners & Fittings", "Fasteners")),
    ("bumper", ("Fasteners & Fittings", "Fasteners")),
    ("jig", ("Tools & Jigs", "Jigs & Bits")),
    ("drill bit", ("Tools & Jigs", "Jigs & Bits")),
    ("hardware", ("General Hardware", "General Hardware")),
]

UNCLASSIFIED = ("Unclassified", "Needs Triage")


def normalise_category(product_type):
    """Map a raw product_type to a (branch, leaf) pair.

    An empty product_type returns UNCLASSIFIED rather than being guessed at.
    On the Eurofit feed 43 of 548 products land there, and they should be
    triaged by a human, not filed somewhere plausible-looking.
    """
    text = (product_type or "").strip().lower()
    if not text:
        return UNCLASSIFIED
    for needle, target in CATEGORY_RULES:
        if needle in text:
            return target
    return ("General Hardware", "Other")


# ---------------------------------------------------------------------------
# Feed parsing
# ---------------------------------------------------------------------------
def parse_shopify_products(products):
    """Turn raw Shopify product dicts into staged import rows.

    Returns a list of dicts, one per *sellable item* — that is, per distinct
    combination of SKU and genuine attributes. Volume price tiers are folded
    into a ``tiers`` list on the item rather than becoming separate items,
    which is the whole point of this parser.
    """
    items = []
    for prod in products or []:
        option_names = [o.get("name") for o in (prod.get("options") or [])]
        tier_slots = {
            i for i, name in enumerate(option_names)
            if (name or "").strip().lower() in TIER_OPTION_NAMES
        }
        branch, leaf = normalise_category(prod.get("product_type"))
        by_key = {}
        for var in prod.get("variants") or []:
            sku = (var.get("sku") or "").strip()
            tier_label = None
            attrs = {}
            for idx, name in enumerate(option_names):
                value = var.get("option%d" % (idx + 1))
                if not value or value == "Default Title":
                    continue
                if idx in tier_slots:
                    tier_label = value
                else:
                    attrs[name] = value
            key = (sku, tuple(sorted(attrs.items())))
            item = by_key.get(key)
            if item is None:
                item = {
                    "sku": sku,
                    "title": prod.get("title") or "",
                    "handle": prod.get("handle") or "",
                    "brand": (prod.get("vendor") or "").strip(),
                    "product_type": (prod.get("product_type") or "").strip(),
                    "category_branch": branch,
                    "category_leaf": leaf,
                    "description": prod.get("body_html") or "",
                    "attributes": attrs,
                    "weight_g": var.get("grams") or 0,
                    "available": bool(var.get("available")),
                    "image_urls": [i.get("src") for i in (prod.get("images") or [])
                                   if i.get("src")],
                    "is_kit_formula": is_kit_formula(sku),
                    "kit_components": parse_kit_components(sku),
                    "tiers": [],
                }
                by_key[key] = item
                items.append(item)
            price = float(var.get("price") or 0.0)
            item["tiers"].append({
                "min_qty": parse_price_tier(tier_label),
                "price": price,
                "label": tier_label or "",
            })
        # Collapse duplicate tiers and order them so the cheapest break for a
        # given quantity is found predictably.
        for item in by_key.values():
            seen = {}
            for tier in item["tiers"]:
                prev = seen.get(tier["min_qty"])
                if prev is None or tier["price"] < prev["price"]:
                    seen[tier["min_qty"]] = tier
            item["tiers"] = sorted(seen.values(), key=lambda t: t["min_qty"])
    return items
