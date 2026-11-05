"""Talent Agent Plus Complete — agent + merchant store + promoter + producer + secretary.

One system, one artist at a time, under the same rules:
  * Official v2 core (FIND / BOOK / COLLECT / COMMUNICATE, 11VT,
    receipt-only, no bank movement) — untouched.
  * v2.1 secure layer — every call needs a session token; artists
    cannot act for each other; mutating calls are locked.
  * Merchant store — merch products, stock, orders, merch-cleared
    11VT receipts in the store's own ledger (never fused with
    booking receipts).
  * Promoter / Producer / Secretary — job-lists with the same
    gates: campaigns capped, posts drafted not posted, production
    advances but only the artist approves a release, secretary
    drafts but does not send.

This is a local, stdlib-only engine. It is NOT a Play Store app,
NOT a live checkout, NOT a distributor, NOT a licensed talent
agency, and NOT a social platform. The phone app, when it comes,
must call THIS class with a token — never the core directly.
"""
from __future__ import annotations

import threading

from talent_agent_plus import TalentAgent, AgentError
from secure_agent import SecureAgent, AuthError, OWNER
from merchant_store import MerchantStore
from roles import Promoter, Producer, Secretary
from tiers import tier_info, all_tiers
from branding import BRAND, PRODUCT, COLORS, STYLE, header_text
from calls import CallLog
from payment_connections import PaymentConnections
from showcase import Showcase


class TalentAgentPlusComplete:
    BRANDING = {"brand": "JGA Enterprise", "product": "Talent Agent Plus Complete",
                "colors": "black and gold", "style": "classical serif, luxury weight"}

    def __init__(self, agent: TalentAgent | None = None):
        self.secure = SecureAgent(agent or TalentAgent())
        self.core = self.secure.core
        self.store = MerchantStore()
        self.promoter = Promoter(self.core)
        self.producer = Producer(self.core)
        self.secretary = Secretary(self.core, store=self.store, promoter=self.promoter)
        self.calls = CallLog()
        self.payments = PaymentConnections()
        self.showcase = Showcase(self.core)
        self._lock = threading.RLock()

    # -- auth passthrough (owner sets up, artists log in) ---------------
    def create_owner(self, password: str):
        return self.secure.create_owner(password)

    def login_owner(self, password: str) -> str:
        return self.secure.login_owner(password)

    def login_artist(self, artist_id: str, password: str) -> str:
        return self.secure.login_artist(artist_id, password)

    def logout(self, token: str):
        return self.secure.logout(token)

    def _artist_only(self, token: str, artist_id: str) -> str:
        principal = self.secure._require(token)
        if principal != OWNER and principal != artist_id:
            raise AuthError(f"artist {principal} may not act for {artist_id}")
        return principal

    # -- agent (delegates to the secure layer, token required) ----------
    def add_artist(self, token, *a, **k):
        return self.secure.add_artist(token, *a, **k)

    def add_opportunity(self, token, *a, **k):
        return self.secure.add_opportunity(token, *a, **k)

    def find_opportunities(self, token, *a, **k):
        return self.secure.find_opportunities(token, *a, **k)

    def offer_booking(self, token, *a, **k):
        return self.secure.offer_booking(token, *a, **k)

    def approve_booking(self, token, *a, **k):
        return self.secure.approve_booking(token, *a, **k)

    def confirm_booking(self, token, *a, **k):
        return self.secure.confirm_booking(token, *a, **k)

    def complete_booking(self, token, *a, **k):
        return self.secure.complete_booking(token, *a, **k)

    def collect(self, token, *a, **k):
        return self.secure.collect(token, *a, **k)

    def pay_cut(self, token, *a, **k):
        return self.secure.pay_cut(token, *a, **k)

    def send_message(self, token, *a, **k):
        return self.secure.send_message(token, *a, **k)

    def add_release(self, token, *a, **k):
        return self.secure.add_release(token, *a, **k)

    def approve_release(self, token, *a, **k):
        return self.secure.approve_release(token, *a, **k)

    def export_release(self, token, *a, **k):
        return self.secure.export_release(token, *a, **k)

    def register_artist_password(self, token, *a, **k):
        return self.secure.register_artist_password(token, *a, **k)

    # -- merchant store (token + artist isolation + lock) ---------------
    def add_product(self, token, artist_id, *a, **k):
        self._artist_only(token, artist_id)
        with self._lock:
            return self.store.add_product(artist_id, *a, **k)

    def storefront(self, token, artist_id):
        self._artist_only(token, artist_id)
        with self._lock:
            return self.store.storefront(artist_id)

    def order_merch(self, token, artist_id, product_id, qty):
        self._artist_only(token, artist_id)
        with self._lock:
            return self.store.order(artist_id, product_id, qty)

    def confirm_merch_order(self, token, order_id):
        o = self.store.orders.get(order_id)
        if o is None:
            raise AgentError(f"unknown order {order_id!r}")
        self._artist_only(token, o.artist_id)
        with self._lock:
            return self.store.confirm_order(order_id)

    def clear_merch_order(self, token, order_id, cleared_cents):
        o = self.store.orders.get(order_id)
        if o is None:
            raise AgentError(f"unknown order {order_id!r}")
        self._artist_only(token, o.artist_id)
        with self._lock:
            return self.store.clear_order(order_id, cleared_cents, key=self.core._key)

    def fulfill_merch_order(self, token, order_id, shipping_note=""):
        o = self.store.orders.get(order_id)
        if o is None:
            raise AgentError(f"unknown order {order_id!r}")
        self._artist_only(token, o.artist_id)
        with self._lock:
            return self.store.fulfill_order(order_id, shipping_note)

    def cancel_merch_order(self, token, order_id):
        o = self.store.orders.get(order_id)
        if o is None:
            raise AgentError(f"unknown order {order_id!r}")
        self._artist_only(token, o.artist_id)
        with self._lock:
            return self.store.cancel_order(order_id)

    # -- promoter / producer / secretary (token + isolation + lock) -----
    def create_campaign(self, token, artist_id, *a, **k):
        self._artist_only(token, artist_id)
        with self._lock:
            return self.promoter.create_campaign(artist_id, *a, **k)

    def campaign_spend(self, token, campaign_id, *a, **k):
        c = self.promoter.campaigns.get(campaign_id)
        if c is None:
            raise AgentError(f"unknown campaign {campaign_id!r}")
        self._artist_only(token, c.artist_id)
        with self._lock:
            return self.promoter.spend(campaign_id, *a, **k)

    def draft_post(self, token, campaign_id, *a, **k):
        c = self.promoter.campaigns.get(campaign_id)
        if c is None:
            raise AgentError(f"unknown campaign {campaign_id!r}")
        self._artist_only(token, c.artist_id)
        with self._lock:
            return self.promoter.draft_post(campaign_id, *a, **k)

    def approve_post(self, token, campaign_id, index):
        c = self.promoter.campaigns.get(campaign_id)
        if c is None:
            raise AgentError(f"unknown campaign {campaign_id!r}")
        principal = self._artist_only(token, c.artist_id)
        with self._lock:
            return self.promoter.approve_post(campaign_id, index, c.artist_id)

    def start_production(self, token, artist_id, *a, **k):
        self._artist_only(token, artist_id)
        with self._lock:
            return self.producer.start_production(artist_id, *a, **k)

    def advance_production(self, token, production_id, *a, **k):
        p = self.producer.productions.get(production_id)
        if p is None:
            raise AgentError(f"unknown production {production_id!r}")
        self._artist_only(token, p.artist_id)
        with self._lock:
            return self.producer.advance(production_id, *a, **k)

    def add_task(self, token, artist_id, *a, **k):
        self._artist_only(token, artist_id)
        with self._lock:
            return self.secretary.add_task(artist_id, *a, **k)

    def complete_task(self, token, task_id):
        t = self.secretary.tasks.get(task_id)
        if t is None:
            raise AgentError(f"unknown task {task_id!r}")
        self._artist_only(token, t.artist_id)
        with self._lock:
            return self.secretary.complete_task(task_id)

    def daily_brief(self, token, artist_id):
        self._artist_only(token, artist_id)
        with self._lock:
            return self.secretary.daily_brief(artist_id)

    # -- piece by piece: the missing agent methods ----------------------
    def set_tier(self, token, *a, **k):
        return self.secure.set_tier(token, *a, **k)

    def reject_booking(self, token, *a, **k):
        return self.secure.reject_booking(token, *a, **k)

    def cancel_booking(self, token, *a, **k):
        return self.secure.cancel_booking(token, *a, **k)

    def add_expense(self, token, *a, **k):
        return self.secure.add_expense(token, *a, **k)

    def agenda(self, token, artist_id):
        return self.secure.agenda(token, artist_id)

    def booking_calendar(self, token, artist_id):
        """Bookings sorted by date — the calendar view, no invented events."""
        self._artist_only(token, artist_id)
        with self._lock:
            return [{"booking_id": b.booking_id, "title": b.title, "date": b.date,
                     "state": b.state, "pay_cents": b.pay_cents}
                    for b in self.core.agenda(artist_id)]

    # -- tiers and prices ------------------------------------------------
    def tier_info(self, token, tier):
        self.secure._require(token)
        return tier_info(tier)

    def all_tiers(self, token):
        self.secure._require(token)
        return all_tiers()

    def branding(self, token):
        self.secure._require(token)
        return {"brand": BRAND, "product": PRODUCT, "colors": COLORS,
                "style": STYLE, "header": header_text()}

    # -- shop: restock, search, summary ----------------------------------
    def restock_product(self, token, product_id, qty):
        p = self.store.products.get(product_id)
        if p is None:
            raise AgentError(f"unknown product {product_id!r}")
        self._artist_only(token, p.artist_id)
        if not isinstance(qty, int) or qty <= 0:
            raise AgentError("restock quantity must be a positive integer")
        with self._lock:
            p.stock += qty
            return p

    def search_products(self, token, artist_id, text=""):
        self._artist_only(token, artist_id)
        with self._lock:
            out = self.store.storefront(artist_id)
            if text:
                out = [p for p in out if text.lower() in p.name.lower() or text.lower() in p.kind.lower()]
            return out

    def shop_summary(self, token, artist_id):
        self._artist_only(token, artist_id)
        with self._lock:
            products = self.store.storefront(artist_id)
            orders = [o for o in self.store.orders.values() if o.artist_id == artist_id]
            return {"products": len(products), "low_stock": [p.name for p in self.store.low_stock(artist_id)],
                    "orders": len(orders),
                    "orders_by_state": {st: sum(1 for o in orders if o.state == st) for st in
                                        ("PENDING", "CONFIRMED", "CLEARED", "FULFILLED", "CANCELLED")},
                    "cleared_cents_total": sum(r["cleared_cents"] for r in self.store.receipts if r["artist_id"] == artist_id)}

    # -- producer / promoter lists ---------------------------------------
    def list_productions(self, token, artist_id):
        self._artist_only(token, artist_id)
        return [p for p in self.producer.productions.values() if p.artist_id == artist_id]

    def production_note(self, token, production_id, text):
        p = self.producer.productions.get(production_id)
        if p is None:
            raise AgentError(f"unknown production {production_id!r}")
        self._artist_only(token, p.artist_id)
        with self._lock:
            return self.producer.note(production_id, text)

    def list_campaigns(self, token, artist_id):
        self._artist_only(token, artist_id)
        return [c for c in self.promoter.campaigns.values() if c.artist_id == artist_id]

    # -- secretary: drafts and open tasks --------------------------------
    def draft_message(self, token, artist_id, *a, **k):
        self._artist_only(token, artist_id)
        with self._lock:
            return self.secretary.draft_message(artist_id, *a, **k)

    def open_tasks(self, token, artist_id):
        self._artist_only(token, artist_id)
        return self.secretary.open_tasks(artist_id)

    # -- calls ------------------------------------------------------------
    def schedule_call(self, token, artist_id, *a, **k):
        self._artist_only(token, artist_id)
        with self._lock:
            return self.calls.schedule_call(artist_id, *a, **k)

    def draft_call_script(self, token, call_id, *a, **k):
        c = self.calls.calls.get(call_id)
        if c is None:
            raise AgentError(f"unknown call {call_id!r}")
        self._artist_only(token, c.artist_id)
        with self._lock:
            return self.calls.draft_script(call_id, *a, **k)

    def log_call(self, token, call_id, *a, **k):
        c = self.calls.calls.get(call_id)
        if c is None:
            raise AgentError(f"unknown call {call_id!r}")
        self._artist_only(token, c.artist_id)
        with self._lock:
            return self.calls.log_outcome(call_id, *a, **k)

    def list_calls(self, token, artist_id):
        self._artist_only(token, artist_id)
        return self.calls.list_calls(artist_id)

    # -- payment connections (registry only, no processor) ---------------
    def payment_status(self, token):
        self.secure._require(token)
        return self.payments.status()

    def register_payment_name(self, token, name):
        if self.secure._require(token) != OWNER:
            raise AuthError("only the owner can register a payment processor name")
        return self.payments.register_name(name)

    def functions(self, token):
        """Every public function in this app, piece by piece, with what it does."""
        self.secure._require(token)
        return {
            "tiers": ["tier_info", "all_tiers", "set_tier"],
            "branding": ["branding"],
            "agent": ["add_artist", "add_opportunity", "find_opportunities", "offer_booking",
                      "approve_booking", "confirm_booking", "reject_booking", "cancel_booking",
                      "complete_booking", "collect", "pay_cut", "send_message", "add_expense",
                      "agenda", "booking_calendar"],
            "secretary": ["add_task", "complete_task", "open_tasks", "draft_message", "daily_brief"],
            "shop": ["add_product", "storefront", "search_products", "restock_product",
                     "order_merch", "confirm_merch_order", "clear_merch_order",
                     "fulfill_merch_order", "cancel_merch_order", "shop_summary"],
            "producer": ["add_release", "approve_release", "export_release", "start_production",
                         "advance_production", "production_note", "list_productions"],
            "promoter": ["create_campaign", "campaign_spend", "draft_post", "approve_post", "list_campaigns"],
            "calls": ["schedule_call", "draft_call_script", "log_call", "list_calls"],
            "bookings": ["offer_booking", "approve_booking", "confirm_booking", "reject_booking",
                         "cancel_booking", "complete_booking", "booking_calendar", "agenda"],
            "payments": ["payment_status", "register_payment_name"],
            "discover": ["publish_profile", "unpublish_profile", "add_sample",
                         "browse_artists", "browse_samples", "request_connection",
                         "artist_connect_requests", "decide_connect_request"],
            "payment_note": "No processor is connected. Charges are refused by design; cleared money is recorded and split only.",
        }

    # -- discover: public showcase (no token for browsing) ---------------
    def publish_profile(self, token, artist_id, *a, **k):
        self._artist_only(token, artist_id)
        with self._lock:
            return self.showcase.publish_profile(artist_id, *a, **k)

    def unpublish_profile(self, token, artist_id):
        self._artist_only(token, artist_id)
        with self._lock:
            return self.showcase.unpublish_profile(artist_id)

    def add_sample(self, token, artist_id, *a, **k):
        self._artist_only(token, artist_id)
        with self._lock:
            return self.showcase.add_sample(artist_id, *a, **k)

    def browse_artists(self, **kwargs):
        """Public. Published profiles and their samples only — no private data."""
        return self.showcase.browse_artists(**kwargs)

    def browse_samples(self, **kwargs):
        return self.showcase.browse_samples(**kwargs)

    def request_connection(self, artist_id, seeker_name, seeker_contact, message):
        """Public. A pending request for the artist, not a sent message or booking."""
        with self._lock:
            return self.showcase.request_connection(artist_id, seeker_name, seeker_contact, message)

    def artist_connect_requests(self, token, artist_id):
        self._artist_only(token, artist_id)
        return self.showcase.artist_requests(artist_id)

    def decide_connect_request(self, token, request_id, accept: bool):
        # resolve the artist from the request, then enforce isolation
        r = self.showcase.requests.get(request_id)
        if r is None:
            raise AgentError(f"unknown connect request {request_id!r}")
        self._artist_only(token, r.artist_id)
        with self._lock:
            return self.showcase.decide_request(request_id, r.artist_id, accept)

    def summary(self, token, artist_id):
        self._artist_only(token, artist_id)
        return {"branding": self.BRANDING,
                "agent": self.secure.status(token),
                "store": self.store.status(),
                "campaigns": len([c for c in self.promoter.campaigns.values() if c.artist_id == artist_id]),
                "productions": len([p for p in self.producer.productions.values() if p.artist_id == artist_id]),
                "open_tasks": len(self.secretary.open_tasks(artist_id)),
                "calls": len(self.calls.list_calls(artist_id)),
                "payments": self.payments.status()["connections"],
                "banking_execution": "NONE_LEDGER_RECEIPT_ONLY"}
