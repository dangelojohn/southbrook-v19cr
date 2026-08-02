# SPDX-License-Identifier: LGPL-3.0-only
"""The unforgeable PLM lifecycle sentinel.

FIX 1 (2026-08-02 blindspot pass). Before this pass, every lifecycle guard
in this module — ``southbrook.eco.write()``'s terminal-immutability check,
its terminal-forge check in ``create()``, ``action_reject`` /
``action_reset_draft`` / ``action_create_superseding`` / ``action_apply``'s
own authorising writes — tested the context key ``plm_lifecycle`` for plain
truthiness:

    if not self.env.context.get("plm_lifecycle"):
        raise UserError(...)

That is a JSON-RPC-shaped hole. Odoo's external API (XML-RPC, JSON-RPC,
``/web/dataset/call_kw``) lets ANY authenticated caller attach an arbitrary
``context`` dict to ANY method call — including a bare ``write()`` or
``create()``. JSON can only carry scalars, lists and dicts; it cannot
serialise a Python object identity. So a client with nothing more than
ordinary ``southbrook_plm.group_southbrook_plm_user`` write access could
call::

    write([[eco_id], {"state": "applied"}], {"context": {"plm_lifecycle": true}})

and ``self.env.context.get("plm_lifecycle")`` would come back ``True`` —
indistinguishable, under a truthiness test, from the flag this module's own
lifecycle actions set internally. That forges a terminal ECO with no
approval, no handler run, and no trace of the forgery: every guarantee this
module makes (terminal-state forge protection, applied/rejected
immutability, the reset-to-draft approver gate, cut-spec activation-only-
via-ECO) is defeated by the same one-line trick, because they all gate on
the same context key.

The fix: gate on OBJECT IDENTITY, not truthiness. ``PLM_LIFECYCLE`` below is
a module-level singleton instance that exists exactly once, in this
process's memory, for the lifetime of the Odoo worker. A JSON-RPC payload
cannot reference it, name it, or fabricate an equivalent — the transport
can carry the boolean ``True``, the string ``"true"``, the integer ``1``,
even a dict shaped like ``repr(PLM_LIFECYCLE)``, but none of those ``is``
this object. Only Python code running INSIDE this addon (or a module that
imports this exact symbol, e.g. ``southbrook_plm_productgraph``'s ECO
bridge) can pass the real thing, because passing it requires importing it:

    from odoo.addons.southbrook_plm.models.plm_lifecycle import PLM_LIFECYCLE
    eco.with_context(plm_lifecycle=PLM_LIFECYCLE).write({...})

Guards MUST compare with ``is``, NEVER with truthiness or ``==``. A stray
``==`` would still be safe here (this class defines no ``__eq__``, so ``==``
falls back to identity anyway) but ``is`` is the unambiguous, unmistakable
spelling of the intent and is what every guard in this module now uses via
the ``_plm_lifecycle_authorised()`` helper on ``southbrook.eco`` and
``southbrook.cut.spec``.

Import path stability: this module is imported by name from at least one
other Southbrook addon (``southbrook_plm_productgraph``), so the fully
qualified path — ``odoo.addons.southbrook_plm.models.plm_lifecycle`` — and
the symbol name ``PLM_LIFECYCLE`` are a cross-module contract. Do not rename
either without updating every importer.
"""


class _PlmLifecycleToken:
    """A deliberately featureless sentinel — see module docstring.

    ``__slots__ = ()`` keeps it free of any instance ``__dict__`` (nothing
    to serialise, nothing to spoof by attribute-copying). No ``__eq__``, no
    ``__hash__`` override, no pickling support: the ONLY way to get a
    reference that compares ``is`` this object is to import
    ``PLM_LIFECYCLE`` from this exact module in-process. There must only
    ever be ONE instance of this class — do not instantiate a second one
    anywhere; a second instance would ``is``-compare False against this
    one and silently break every guard that checks against it.
    """

    __slots__ = ()

    def __repr__(self):
        return "<southbrook_plm.PLM_LIFECYCLE>"


PLM_LIFECYCLE = _PlmLifecycleToken()


def is_plm_lifecycle_authorised(env):
    """Shared predicate backing the ``_plm_lifecycle_authorised()`` method
    on both ``southbrook.eco`` and ``southbrook.cut.spec`` (FIX 1). Kept as
    one module-level function so the identity check is written exactly
    once and both models' helpers stay byte-for-byte the same behaviour —
    the two models don't share a base class, so the helper is duplicated
    as a one-line method on each, but both delegate to this single
    implementation rather than re-deriving the ``is`` comparison twice.

    Truthiness would defeat the whole point (see module docstring) — this
    is an identity check, deliberately spelled with ``is``.
    """
    return env.context.get("plm_lifecycle") is PLM_LIFECYCLE
