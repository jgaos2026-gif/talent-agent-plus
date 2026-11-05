"""Payment connections — a registry, NOT a processor. Read this first.

Status, verified from John's own history (2026-11-02, still true here):
  * Stripe: NOT_CONNECTED. John confirmed connecting with
    Jaysgraphicarts@gmail.com, but the OAuth step was never
    completed in evidence, and the site's Stripe form ownership is
    UNVERIFIED. Do NOT record Stripe as verified, and do NOT send
    a customer to that form, until a connection returns an account
    context and its verification status is checked.
  * No other processor is configured in this app.

What this module does:
  * Lists every payment connection and its status, so the app can
    never claim a connection it does not have.
  * Lets the OWNER register a connection NAME only (e.g. "stripe").
    Registering a name does NOT connect anything, does NOT store
    a key, and does NOT change status to connected. A key, if one
    is ever used, belongs in an environment variable (e.g.
    STRIPE_SECRET_KEY), never in code, never in chat, never in a
    store file.
  * Enforces the money rule at the boundary: this app records
    cleared money and splits it with 11VT, receipt-only. It moves
    NO bank funds. A future processor adapter must be a separate
    module, owner-approved, with its own tests, before any charge
    is attempted — and cleared money in this app must never be
    confused with a processor's "charge succeeded" event.

If a future build claims "payments connected," check this
registry first. A name in this list is not a connection.
"""
from __future__ import annotations

from talent_agent_plus import AgentError

KNOWN_PROCESSORS = ("stripe", "shop_pay", "paypal")
# PayPal is listed only to record the decision: John does NOT use
# PayPal (2026-10-28) — leave it blocked, never register it as live.


class PaymentConnections:
    def __init__(self):
        self._registry: dict[str, dict] = {
            "stripe": {"status": "NOT_CONNECTED", "email_on_file": "Jaysgraphicarts@gmail.com",
                       "note": "OAuth not completed in evidence; site form ownership unverified."},
            "shop_pay": {"status": "NOT_CONNECTED", "email_on_file": "",
                         "note": "No wallet connection configured in this app."},
            "paypal": {"status": "BLOCKED_BY_OWNER_DECISION", "email_on_file": "",
                       "note": "John does not use PayPal (2026-10-28). Leave blocked."},
        }

    def status(self) -> dict:
        return {"connections": dict(self._registry),
                "money_moved_by_this_app": False,
                "rule": "Receipt-only. Cleared money is recorded and split; no bank funds are moved here."}

    def is_connected(self, name: str) -> bool:
        return self._registry.get(name, {}).get("status") == "CONNECTED"

    def register_name(self, name: str) -> dict:
        """Record that a processor is INTENDED, not that it is connected."""
        if name not in KNOWN_PROCESSORS:
            raise AgentError(f"unknown processor {name!r}")
        if name == "paypal":
            raise AgentError("PayPal is blocked by John's decision; it cannot be registered as live.")
        self._registry[name]["status"] = "NAME_REGISTERED_NOT_CONNECTED"
        self._registry[name]["note"] = "Name only. No key stored. Connection still requires the processor's own OAuth/verification."
        return self._registry[name]

    def charge(self, *args, **kwargs):
        raise AgentError("No payment processor is connected in this app. Charges are refused by design. "
                         "Record cleared money with collect()/clear_merch_order() only, after it actually clears elsewhere.")
