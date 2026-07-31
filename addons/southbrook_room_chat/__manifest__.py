# SPDX-License-Identifier: LGPL-3.0-only
{
    "name": "Southbrook Room Chat (AI)",
    "summary": "Conversational AI room builder: describe your kitchen in "
               "chat and watch the existing \"Set Up Your Room\" wizard "
               "pre-fill for mandatory human review.",
    "description": """
Southbrook Room Chat
=====================

A customer chats with an AI (a vision-capable Claude model, Sonnet 5 — the
same model already used by southbrook_room_capture) describing their kitchen
room: shape, wall lengths, doors, windows. Each turn, the AI calls tools that
build up a DRAFT room geometry — it never creates southbrook.room / .wall /
.constraint records directly. The draft is shown live next to the chat via a
reused preview panel, and a "finish in Room Setup" action hands the draft to
the EXISTING "Set Up Your Room" wizard (southbrook_estimating_website's
RoomSetupWizard) for mandatory human review, exactly like the sibling
southbrook_room_capture (photo-based) addon. Every geometry value still
passes through southbrook.room.validate_geometry before persistence, whether
it arrived by chat, photo, or manual entry — this addon adds no new
persistence path onto the core room models.

Components:
* southbrook.room.chat.session — persists the per-order conversation draft
  (a plain JSON blob, in the same shape southbrook_room_capture's
  `_estimate_to_existing_room` already produces) and a lightweight text
  transcript, so a conversation can resume across page loads.
* southbrook.room.chat.agent (AbstractModel) — builds the Anthropic tools
  request, runs the tool_use loop (set_room_shape, add_wall, update_wall,
  remove_wall, add_constraint, remove_constraint,
  validate_current_draft), and normalizes the reply. Mock mode (the
  default) returns a canned scripted reply so cold installs and CI never
  touch the network.
* controllers/main.py — one JSON-RPC route:
  POST /southbrook/api/order/<id>/room/chat.

Frontend: a "Chat to build your room" panel on the Order Builder page (a
new RoomChatWidget, mirroring southbrook_hermes's chat widget pattern),
showing the draft filling in live via a reused RoomOutlinePreview. A
"Looks good — finish in Room Setup" button hands the draft to the existing
wizard via the same idless-prefill path southbrook_room_capture already
established (no wizard code changes needed beyond exporting
RoomOutlinePreview for reuse).
""",
    "author": "Southbrook Cabinetry",
    "license": "LGPL-3",
    "category": "Website/eCommerce",
    "version": "19.0.2.3.0",
    "depends": [
        "southbrook_estimating",
        "southbrook_estimating_website",
        # Reuses southbrook.room.capture._get_api_key() so there is ONE
        # Anthropic API key configured for both AI features, not two.
        "southbrook_room_capture",
        # v2 (2026-07-05): the chat now also gathers the customer's
        # contact + project details and lands them in Contacts + CRM for
        # live follow-up — mirroring the AI-agent gateway's gather/
        # validate/CRM abilities. Reuses that addon's phone validator,
        # shared offerings catalog, and the "AI Agent"/CRM tag pattern
        # (crm + utm come transitively via the gateway).
        "southbrook_agent_gateway",
        # Phase 0 design-system consolidation (2026-07-31) — this
        # addon's room_chat.scss already reads var(--sb-*) tokens;
        # southbrook_ui is the new canonical, dependency-light token
        # layer those names should resolve through going forward.
        "southbrook_ui",
    ],
    # httpx is imported lazily inside southbrook.room.chat.agent._call_anthropic
    # (guarded exactly like southbrook_room_capture's own _call_anthropic) so
    # the addon installs fine without it — southbrook_room_chat.use_mock
    # defaults to True, so mock mode (no httpx needed) is what cold
    # installs/CI exercise.
    "data": [
        "security/ir.model.access.csv",
        "data/utm_data.xml",
        "views/order_builder_room_chat_inject.xml",
    ],
    "assets": {
        "web.assets_frontend": [
            "southbrook_room_chat/static/src/js/room_chat.esm.js",
            "southbrook_room_chat/static/src/xml/room_chat.xml",
            "southbrook_room_chat/static/src/scss/room_chat.scss",
        ],
    },
    "installable": True,
    "application": False,
    "auto_install": False,
}
