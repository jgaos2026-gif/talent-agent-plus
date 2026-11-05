"""Merchant store for an artist's merch — inside Talent Agent Plus Complete.

What it is: a local storefront ledger. An artist lists products
(shirts, CDs, hats, posters, etc.), stock is tracked, orders reserve
stock, and money is split ONLY when it is cleared, with the same
11VT rule as bookings: split the full cleared gross, collaborator
cuts out of V8 only, receipt-only, NO bank movement, ever, here.

What it is NOT:
  * Not a live checkout, not Stripe, not a payment processor.
  * Not a distributor or a shipping carrier. Fulfilment here means
    "the artist (or their team) marked this order packed/shipped
    locally" — the actual carrier label and tracking are outside
    this module, entered by hand if the artist wants them.
  * Merch receipts live in THIS store's own ledger, separate from
    the booking ledger in talent_agent_plus.py. Ledgers are never
    fused: a merch receipt is not a booking receipt, and the core
    store validator is never asked to bless a merch receipt as a
    booking receipt. That separation is deliberate.

Order states: PENDING -> CONFIRMED -> CLEARED -> FULFILLED.
CANCELLED is allowed only from PENDING or CONFIRMED, before money
is cleared, and it returns the reserved stock. Once money is
CLEARED, there is no cancel-and-refund button here — a refund
would be a real money movement, and this module moves no money.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from talent_agent_plus import AgentError, split_cleared, _seal

MERCH_KINDS = ("shirt", "cd", "hat", "poster", "hoodie", "other")
ORDER_STATES = ("PENDING", "CONFIRMED", "CLEARED", "FULFILLED", "CANCELLED")


@dataclass
class Product:
    product_id: str
    artist_id: str
    name: str
    kind: str
    price_cents: int
    stock: int


@dataclass
class Order:
    order_id: str
    artist_id: str
    product_id: str
    product_name: str
    qty: int
    total_cents: int
    state: str = "PENDING"
    receipt: dict | None = None
    shipping_note: str = ""


class MerchantStore:
    def __init__(self):
        self.products: dict[str, Product] = {}
        self.orders: dict[str, Order] = {}
        self.receipts: list[dict] = []
        self._counters = {"product": 0, "order": 0}

    def _next(self, kind: str) -> str:
        self._counters[kind] += 1
        return f"{kind}-{self._counters[kind]:04d}"

    def add_product(self, artist_id: str, name: str, kind: str, price_cents: int, stock: int) -> Product:
        if not name or not name.strip():
            raise AgentError("product needs a name")
        if kind not in MERCH_KINDS:
            raise AgentError(f"unknown merch kind {kind!r}; use one of {MERCH_KINDS}")
        if not isinstance(price_cents, int) or price_cents <= 0:
            raise AgentError("price must be a positive integer number of cents")
        if not isinstance(stock, int) or stock < 0:
            raise AgentError("stock must be a non-negative integer")
        p = Product(self._next("product"), artist_id, name.strip(), kind, price_cents, stock)
        self.products[p.product_id] = p
        return p

    def storefront(self, artist_id: str) -> list[Product]:
        return sorted((p for p in self.products.values() if p.artist_id == artist_id),
                      key=lambda p: p.name)

    def low_stock(self, artist_id: str, threshold: int = 5) -> list[Product]:
        return [p for p in self.storefront(artist_id) if p.stock <= threshold]

    def order(self, artist_id: str, product_id: str, qty: int) -> Order:
        if product_id not in self.products:
            raise AgentError(f"unknown product {product_id!r}")
        p = self.products[product_id]
        if p.artist_id != artist_id:
            raise AgentError("product does not belong to this artist")
        if not isinstance(qty, int) or qty <= 0:
            raise AgentError("quantity must be a positive integer")
        if p.stock < qty:
            raise AgentError(f"not enough stock for {p.name}: {p.stock} left, {qty} asked")
        p.stock -= qty
        o = Order(self._next("order"), artist_id, product_id, p.name, qty, p.price_cents * qty)
        self.orders[o.order_id] = o
        return o

    def confirm_order(self, order_id: str) -> Order:
        o = self._order(order_id)
        if o.state != "PENDING":
            raise AgentError(f"only a PENDING order can be confirmed (now {o.state})")
        o.state = "CONFIRMED"
        return o

    def cancel_order(self, order_id: str) -> Order:
        o = self._order(order_id)
        if o.state not in ("PENDING", "CONFIRMED"):
            raise AgentError(f"cannot cancel a {o.state} order; cleared money is never auto-refunded here")
        o.state = "CANCELLED"
        self.products[o.product_id].stock += o.qty
        return o

    def clear_order(self, order_id: str, cleared_cents: int, key=None) -> dict:
        """Split cleared merch money with 11VT. cleared_cents must equal the order total."""
        o = self._order(order_id)
        if o.state != "CONFIRMED":
            raise AgentError(f"only a CONFIRMED order can be cleared (now {o.state})")
        if cleared_cents != o.total_cents:
            raise AgentError(f"cleared {cleared_cents} does not equal order total {o.total_cents}; refused")
        receipt = split_cleared(cleared_cents, "merch-cleared", key)
        receipt["order_id"] = o.order_id
        receipt["artist_id"] = o.artist_id
        receipt = _seal(receipt, key)
        o.receipt = receipt
        o.state = "CLEARED"
        self.receipts.append(receipt)
        return receipt

    def fulfill_order(self, order_id: str, shipping_note: str = "") -> Order:
        o = self._order(order_id)
        if o.state != "CLEARED":
            raise AgentError(f"only a CLEARED order can be fulfilled (now {o.state})")
        o.state = "FULFILLED"
        o.shipping_note = shipping_note
        return o

    def _order(self, order_id: str) -> Order:
        if order_id not in self.orders:
            raise AgentError(f"unknown order {order_id!r}")
        return self.orders[order_id]

    def status(self) -> dict:
        return {"products": len(self.products), "orders": len(self.orders),
                "cleared_cents_total": sum(r["cleared_cents"] for r in self.receipts),
                "banking_execution": "NONE_LEDGER_RECEIPT_ONLY"}
