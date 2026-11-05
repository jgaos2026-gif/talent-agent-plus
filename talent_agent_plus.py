"""Talent Agent Plus — core rules engine (hardened revision, v2).
===========================================================
A talent-agent core for music artists AND actors/entertainers, in one
self-contained, stdlib-only file. No network, no LLM, no bank movement,
no distributor, no Play Store code. It is the CORE that a phone app,
web dashboard, or distributor client would call into.

The four operations:
  FIND        — opportunities: gigs, auditions, casting calls, venues,
                producers, collaborators. Search by skill/kind/location.
  BOOK        — engagements with a human approval gate. Never confirmed
                without the booked artist's approval, never double-booked
                for the same artist on the same date, never changed after
                a terminal state.
  COLLECT     — cleared money only, receipt-only. 11VT rule: split the
                FULL cleared gross, then pay any collaborator/manager cut
                OUT OF V8 only. No bank movement, ever, in this module.
  COMMUNICATE — messages tied to a live booking, gated text only.

Rules baked in:
  * Tiers: Try FREE, Starter $19/mo|$190/yr, Pro $49/mo|$490/yr,
    Full Label $129/mo|$1,290/yr. Try is capped at 3 active bookings and
    1 release (LIFETIME count of releases, see TRY_RELEASE_CAP_MODE).
    Downgrading into a tier you are already over is refused.
  * Human approval gates stay ON at every tier. This core never signs,
    publishes, or pays on its own. NOTE: `actor` is a string the CALLER
    asserts. This module enforces who may act per state, but real
    authentication (who is actually "artist-0001") belongs to the app
    layer that calls it.
  * Legal: artist self-representation tool, NOT a licensed talent agency.
  * Releases: PREP -> READY. Export is a local metadata package only.
  * Persistence: save() seals the file. With a key it is HMAC-SHA256
    (tamper-evident). With NO key it is plain SHA-256 — that detects
    accidental corruption only; anyone who can edit the file can reseal
    it. load() also re-validates every record's invariants, so a
    resealed-but-inconsistent file is still refused.

Vetted and applied 2026-11-04 by Rumi, from a pasted hardened revision:
existing v1 tests were re-run against this file, plus new adversarial
tests in tests/test_adversarial.py. The v1 file is kept as
talent_agent_plus_v1.py. Breaking changes vs v1, stated plainly:
undated opportunities can no longer be booked, gig-pay-cleared may not
exceed booking pay, collect is once per booking per stage, pay_cut only
works on the ledger's current receipt, and messages are party-only and
refused on terminal bookings.

Run the demo:   python3 talent_agent_plus.py
Run the tests:  python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import unicodedata
from dataclasses import dataclass, field, asdict
from datetime import date as _date
from decimal import Decimal, ROUND_HALF_UP, ROUND_DOWN

# ---------------------------------------------------------------- 11VT ---
RATES = {
    "V1": Decimal("0.15"), "V2": Decimal("0.075"), "V3": Decimal("0.035"),
    "V4": Decimal("0.05"), "V5": Decimal("0.02"), "V6": Decimal("0.15"),
    "V7": Decimal("0.12"),  # TARGET only — actual V7 is the residual plug
    "V8": Decimal("0.20"), "V9": Decimal("0.10"), "V10": Decimal("0.08"),
    "V11": Decimal("0.02"),
}
if sum(RATES.values()) != Decimal("1"):  # explicit, survives python -O
    raise RuntimeError("11VT rates must sum to exactly 1")

CLEARED_STAGES = ("gig-pay-cleared", "merch-cleared", "payout-cleared")
CAPPED_STAGE = "gig-pay-cleared"   # cleared amount may not exceed booking pay
BANKING_EXECUTION = "NONE_LEDGER_RECEIPT_ONLY"
TAX_DISCLAIMER = "INTERNAL_RESERVE_TARGETS_NOT_STATUTORY_TAX_LIABILITY"
V7_PLUG_NOTICE = "ABSORBS_PENNY_ROUNDING_DELTAS"
COLLABORATOR_RULE = "OPTION_B_SPLIT_GROSS_PAY_FROM_V8"

TIERS = {
    "try": {"name": "Try", "monthly_cents": 0, "yearly_cents": 0,
            "max_active_bookings": 3, "max_releases": 1},
    "starter": {"name": "Starter", "monthly_cents": 1900, "yearly_cents": 19000,
                "max_active_bookings": None, "max_releases": None},
    "pro": {"name": "Pro", "monthly_cents": 4900, "yearly_cents": 49000,
            "max_active_bookings": None, "max_releases": None},
    "full_label": {"name": "Full Label", "monthly_cents": 12900, "yearly_cents": 129000,
                   "max_active_bookings": None, "max_releases": None},
}
# "lifetime": every release ever created counts against the cap.
TRY_RELEASE_CAP_MODE = "lifetime"

EXPENSE_CATEGORIES = ("travel", "taxes", "advertising", "pay", "wardrobe",
                      "cds", "online")
OPPORTUNITY_KINDS = ("gig", "audition", "casting", "venue", "producer",
                     "collaborator", "theatre", "dance", "film",
                     "voice_acting", "commercial")
BOOKING_STATES = ("DRAFT", "OFFERED", "APPROVED", "CONFIRMED",
                  "COMPLETED", "CANCELLED", "REJECTED")
ACTIVE_STATES = ("DRAFT", "OFFERED", "APPROVED", "CONFIRMED")
OWNER = "owner"
MAX_MESSAGE_CHARS = 4000
STORE_VERSION = 2


class AgentError(ValueError):
    pass


# ------------------------------------------------------------- helpers ---
def _is_cents(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _canon(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


def _norm_key(key):
    if key is None:
        return None
    if isinstance(key, str):
        key = key.encode()
    if not isinstance(key, (bytes, bytearray)) or not key:
        raise AgentError("key must be a non-empty str/bytes, or None")
    return bytes(key)


def _check_date(d: str, allow_empty: bool = True) -> None:
    """Strict YYYY-MM-DD, must be a real calendar date. No padding, no variants."""
    if d == "" and allow_empty:
        return
    if not isinstance(d, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
        raise AgentError(f"date must be YYYY-MM-DD, got {d!r}")
    try:
        _date.fromisoformat(d)
    except ValueError:
        raise AgentError(f"not a real calendar date: {d!r}")


def _hexmac(payload: bytes, key) -> str:
    return (hmac.new(key, payload, hashlib.sha256).hexdigest()
            if key else hashlib.sha256(payload).hexdigest())


# ------------------------------------------------------------ receipts ---
def _derive_split(cleared_cents: int) -> dict:
    """11VT split of a gross. V7 is the plug so the sum is exact."""
    total = Decimal(cleared_cents)

    def build(rounding):
        s = {v: int((total * r).quantize(Decimal("1"), rounding=rounding))
             for v, r in RATES.items() if v != "V7"}
        s["V7"] = cleared_cents - sum(s.values())
        return s

    s = build(ROUND_HALF_UP)
    if s["V7"] < 0:
        s = build(ROUND_DOWN)
    if s["V7"] < 0 or sum(s.values()) != cleared_cents:
        raise AgentError("11VT split failed its own invariant")
    return {f"V{i}": s[f"V{i}"] for i in range(1, 12)}


def _validate_amount(cleared_cents, stage) -> None:
    if stage not in CLEARED_STAGES:
        raise AgentError(f"stage not cleared: {stage!r}; cleared stages are {CLEARED_STAGES}")
    if not _is_cents(cleared_cents) or cleared_cents < 0:
        raise AgentError("cleared amount must be a non-negative integer number of cents")


def _seal(receipt: dict, key) -> dict:
    receipt = dict(receipt)
    receipt.pop("receipt_hash", None)
    receipt["hash_mode"] = "HMAC-SHA256" if key else "SHA256"
    body = {k: v for k, v in receipt.items() if k != "receipt_hash"}
    receipt["receipt_hash"] = _hexmac(_canon(body), key)
    return receipt


def _verify_receipt(receipt: dict, key) -> None:
    if not isinstance(receipt, dict):
        raise AgentError("receipt must be a dict")
    claimed, mode = receipt.get("receipt_hash"), receipt.get("hash_mode")
    if not isinstance(claimed, str) or mode not in ("SHA256", "HMAC-SHA256"):
        raise AgentError("receipt is unsealed")
    if key and mode != "HMAC-SHA256":
        raise AgentError("receipt key configured but receipt is not HMAC-sealed")
    if mode == "HMAC-SHA256" and not key:
        raise AgentError("receipt is HMAC-sealed but no key is configured")
    body = {k: v for k, v in receipt.items() if k != "receipt_hash"}
    if not hmac.compare_digest(claimed, _hexmac(_canon(body), key)):
        raise AgentError("receipt hash mismatch: altered or forged")


def _check_receipt(receipt: dict, key) -> None:
    """Full receipt audit: seal, math, and cut bookkeeping."""
    _verify_receipt(receipt, key)
    if receipt.get("banking_execution") != BANKING_EXECUTION:
        raise AgentError("receipt claims a banking execution mode other than ledger-only")
    _validate_amount(receipt.get("cleared_cents"), receipt.get("stage"))
    expected = _derive_split(receipt["cleared_cents"])
    cuts = receipt.get("cuts")
    if not isinstance(cuts, list):
        raise AgentError("receipt cuts must be a list")
    cut_total = 0
    for c in cuts:
        if (not isinstance(c, dict) or not _is_cents(c.get("cents")) or c["cents"] <= 0
                or not isinstance(c.get("payee"), str) or not c["payee"].strip()):
            raise AgentError("receipt has a malformed cut (needs payee and positive integer cents)")
        cut_total += c["cents"]
    if receipt.get("collaborator_cut_cents") != cut_total or not _is_cents(cut_total):
        raise AgentError("collaborator_cut_cents does not equal the sum of cuts")
    if cut_total > expected["V8"]:
        raise AgentError("cuts exceed the V8 allocation of this gross")
    expected["V8"] -= cut_total
    got = receipt.get("split")
    if (not isinstance(got, dict) or got != expected
            or not all(_is_cents(v) for v in got.values())):
        raise AgentError("receipt split does not match the 11VT split of its gross")


def split_cleared(cleared_cents: int, stage: str, key=None) -> dict:
    """Split a cleared amount. V7 = plug, so the split always sums exactly."""
    key = _norm_key(key)
    _validate_amount(cleared_cents, stage)
    return _seal({
        "cleared_cents": cleared_cents, "stage": stage,
        "split": _derive_split(cleared_cents),
        "cuts": [], "collaborator_cut_cents": 0,
        "banking_execution": BANKING_EXECUTION, "tax_disclaimer": TAX_DISCLAIMER,
        "v7_plug_notice": V7_PLUG_NOTICE, "collaborator_rule": COLLABORATOR_RULE,
    }, key)


def pay_collaborator_cut(receipt: dict, cut_cents: int, payee: str, key=None) -> dict:
    """Pay a collaborator/manager cut OUT OF V8 ONLY (Option B)."""
    key = _norm_key(key)
    _check_receipt(receipt, key)
    if not _is_cents(cut_cents) or cut_cents <= 0:
        raise AgentError("cut must be a positive integer number of cents")
    if not isinstance(payee, str) or not payee.strip():
        raise AgentError("a collaborator cut requires a named payee")
    if cut_cents > receipt["split"]["V8"]:
        raise AgentError(f"cut {cut_cents} exceeds V8 balance {receipt['split']['V8']}; refused")
    out = dict(receipt)
    out["split"] = dict(receipt["split"])
    out["split"]["V8"] -= cut_cents
    out["cuts"] = list(receipt["cuts"]) + [{"payee": payee.strip(), "cents": cut_cents}]
    out["collaborator_cut_cents"] = receipt["collaborator_cut_cents"] + cut_cents
    return _seal(out, key)


# ---------------------------------------------------------------- text ---
_INJECTION = re.compile(r"tool[\W_]*call|<[\W_]*tool|override[\W_]*vera")


def gate_text(text: str) -> None:
    """Core text gate (a small, honest subset of VERA v2's job)."""
    if not isinstance(text, str) or not text.strip():
        raise AgentError("message text must be non-empty")
    if len(text) > MAX_MESSAGE_CHARS:
        raise AgentError(f"message refused: over {MAX_MESSAGE_CHARS} characters")
    for ch in text:
        if unicodedata.category(ch) == "Cc" and ch not in "\n\r\t":
            raise AgentError("message refused: control/null byte")
    norm = unicodedata.normalize("NFKC", text)
    norm = "".join(ch for ch in norm if unicodedata.category(ch) != "Cf").casefold()
    if _INJECTION.search(norm):
        raise AgentError("message refused: tool-shaped / injection payload")


def normalize_tier(tier: str) -> str:
    t = (tier or "").strip().lower().replace(" ", "_").replace("-", "_")
    if t in ("full", "label", "all_in"):
        t = "full_label"
    if t not in TIERS:
        raise AgentError(f"unknown tier {tier!r}; tiers are {sorted(TIERS)}")
    return t


# ------------------------------------------------------------- records ---
@dataclass
class Artist:
    artist_id: str
    name: str
    skills: list = field(default_factory=list)   # e.g. ["music", "acting"]
    location: str = ""
    tier: str = "try"


@dataclass
class Opportunity:
    opp_id: str
    title: str
    kind: str
    skills: list = field(default_factory=list)
    location: str = ""
    date: str = ""          # YYYY-MM-DD, or "" if undated
    pay_cents: int = 0


@dataclass
class Booking:
    booking_id: str
    artist_id: str
    opp_id: str
    title: str
    date: str = ""
    pay_cents: int = 0
    state: str = "DRAFT"
    history: list = field(default_factory=list)

    def record(self, actor: str, action: str):
        self.history.append({"actor": actor, "action": action, "state": self.state})


@dataclass
class Release:
    release_id: str
    artist_id: str
    title: str
    state: str = "PREP"     # PREP, READY
    approved_by: str = ""


# -------------------------------------------------------------- agent ----
class TalentAgent:
    """One agent instance = one artist roster with separate ledgers.

    Per-artist data is never fused: each booking, release, expense, and
    receipt belongs to exactly one artist_id. A subscriber's agent is a
    separate TalentAgent instance with its own store file.
    """

    def __init__(self, key=None):
        self._key = _norm_key(key)
        self.artists: dict[str, Artist] = {}
        self.opportunities: dict[str, Opportunity] = {}
        self.bookings: dict[str, Booking] = {}
        self.releases: dict[str, Release] = {}
        self.expenses: list[dict] = []
        self.messages: list[dict] = []
        self.receipts: list[dict] = []
        self._counters = {"artist": 0, "opp": 0, "booking": 0, "release": 0}

    def _next(self, kind: str) -> str:
        self._counters[kind] += 1
        return f"{kind}-{self._counters[kind]:04d}"

    # -- lookups --------------------------------------------------------
    def _artist(self, artist_id: str) -> Artist:
        if artist_id not in self.artists:
            raise AgentError(f"unknown artist {artist_id!r}")
        return self.artists[artist_id]

    def _booking(self, booking_id: str) -> Booking:
        if booking_id not in self.bookings:
            raise AgentError(f"unknown booking {booking_id!r}")
        return self.bookings[booking_id]

    @staticmethod
    def _owner_or_artist(b: Booking, actor: str) -> None:
        if actor != OWNER and actor != b.artist_id:
            raise AgentError(f"actor {actor!r} may not act on {b.booking_id}")

    # -- FIND -----------------------------------------------------------
    def add_artist(self, name: str, skills=None, location: str = "", tier: str = "try") -> Artist:
        if not isinstance(name, str) or not name.strip():
            raise AgentError("artist needs a name")
        a = Artist(self._next("artist"), name.strip(), list(skills or []),
                   location, normalize_tier(tier))
        self.artists[a.artist_id] = a
        return a

    def _active_count(self, artist_id: str) -> int:
        return sum(1 for b in self.bookings.values()
                   if b.artist_id == artist_id and b.state in ACTIVE_STATES)

    def _release_count(self, artist_id: str) -> int:
        return sum(1 for r in self.releases.values() if r.artist_id == artist_id)

    def set_tier(self, artist_id: str, tier: str) -> str:
        a = self._artist(artist_id)
        new = normalize_tier(tier)
        cap_b, cap_r = TIERS[new]["max_active_bookings"], TIERS[new]["max_releases"]
        if cap_b is not None and self._active_count(artist_id) > cap_b:
            raise AgentError(f"cannot move to {new}: {self._active_count(artist_id)} active bookings exceed its cap of {cap_b}")
        if cap_r is not None and self._release_count(artist_id) > cap_r:
            raise AgentError(f"cannot move to {new}: {self._release_count(artist_id)} releases exceed its cap of {cap_r}")
        a.tier = new
        return a.tier

    def add_opportunity(self, title: str, kind: str, skills=None, location: str = "",
                        date: str = "", pay_cents: int = 0) -> Opportunity:
        if not isinstance(title, str) or not title.strip():
            raise AgentError("opportunity needs a title")
        if kind not in OPPORTUNITY_KINDS:
            raise AgentError(f"unknown opportunity kind {kind!r}")
        if not _is_cents(pay_cents) or pay_cents < 0:
            raise AgentError("opportunity pay must be non-negative integer cents")
        _check_date(date)
        o = Opportunity(self._next("opp"), title.strip(), kind, list(skills or []),
                        location, date, pay_cents)
        self.opportunities[o.opp_id] = o
        return o

    def find_opportunities(self, skill: str = "", kind: str = "", location: str = "") -> list[Opportunity]:
        out = []
        for o in self.opportunities.values():
            if skill and skill not in o.skills:
                continue
            if kind and o.kind != kind:
                continue
            if location and location.lower() not in o.location.lower():
                continue
            out.append(o)
        return sorted(out, key=lambda o: (o.date or "9999", o.title))

    # -- BOOK -----------------------------------------------------------
    def _check_tier_cap(self, artist: Artist, kind: str):
        cap = TIERS[artist.tier]["max_active_bookings" if kind == "booking" else "max_releases"]
        if cap is None:
            return
        have = (self._active_count(artist.artist_id) if kind == "booking"
                else self._release_count(artist.artist_id))
        if have >= cap:
            what = "active bookings" if kind == "booking" else "release(s)"
            raise AgentError(f"{artist.tier} tier allows {cap} {what}; this would be {have + 1}")

    def _check_double_booking(self, artist_id: str, date: str):
        for b in self.bookings.values():
            if (b.artist_id == artist_id and b.date == date
                    and b.state in ACTIVE_STATES):
                raise AgentError(f"double booking: artist already has {b.booking_id} on {date}")

    def offer_booking(self, artist_id: str, opp_id: str, actor: str = OWNER) -> Booking:
        artist = self._artist(artist_id)
        if actor != OWNER and actor != artist_id:
            raise AgentError(f"actor {actor!r} may not offer a booking for {artist_id}")
        if opp_id not in self.opportunities:
            raise AgentError(f"unknown opportunity {opp_id!r}")
        opp = self.opportunities[opp_id]
        if not opp.date:
            raise AgentError("cannot book an undated opportunity: double-booking cannot be checked without a date")
        self._check_tier_cap(artist, "booking")
        self._check_double_booking(artist_id, opp.date)
        b = Booking(self._next("booking"), artist_id, opp_id, opp.title, opp.date, opp.pay_cents)
        b.state = "OFFERED"
        b.record(actor, "offer")
        self.bookings[b.booking_id] = b
        return b

    def approve_booking(self, booking_id: str, actor: str) -> Booking:
        b = self._booking(booking_id)
        if b.state != "OFFERED":
            raise AgentError(f"only an OFFERED booking can be approved (now {b.state})")
        if actor != b.artist_id:
            raise AgentError("only the booked artist can approve")
        b.state = "APPROVED"
        b.record(actor, "approve")
        return b

    def confirm_booking(self, booking_id: str, actor: str = OWNER) -> Booking:
        b = self._booking(booking_id)
        self._owner_or_artist(b, actor)
        if b.state != "APPROVED":
            raise AgentError(f"only an APPROVED booking can be confirmed (now {b.state})")
        if not any(h.get("action") == "approve" and h.get("actor") == b.artist_id for h in b.history):
            raise AgentError("no recorded artist approval in history; refusing to confirm")
        b.state = "CONFIRMED"
        b.record(actor, "confirm")
        return b

    def reject_booking(self, booking_id: str, actor: str = OWNER) -> Booking:
        b = self._booking(booking_id)
        self._owner_or_artist(b, actor)
        if b.state not in ("DRAFT", "OFFERED", "APPROVED"):
            raise AgentError(f"cannot reject a {b.state} booking")
        b.state = "REJECTED"
        b.record(actor, "reject")
        return b

    def cancel_booking(self, booking_id: str, actor: str = OWNER) -> Booking:
        b = self._booking(booking_id)
        self._owner_or_artist(b, actor)
        if b.state != "CONFIRMED":
            raise AgentError(f"only a CONFIRMED booking can be cancelled (now {b.state})")
        b.state = "CANCELLED"
        b.record(actor, "cancel")
        return b

    def complete_booking(self, booking_id: str, actor: str = OWNER) -> Booking:
        b = self._booking(booking_id)
        self._owner_or_artist(b, actor)
        if b.state != "CONFIRMED":
            raise AgentError(f"only a CONFIRMED booking can be completed (now {b.state})")
        b.state = "COMPLETED"
        b.record(actor, "complete")
        return b

    # -- COLLECT --------------------------------------------------------
    def collect(self, booking_id: str, cleared_cents: int, stage: str) -> dict:
        b = self._booking(booking_id)
        if b.state != "COMPLETED":
            raise AgentError(f"collect requires a COMPLETED booking (now {b.state}); uncleared money is never split")
        _validate_amount(cleared_cents, stage)
        if any(r["booking_id"] == booking_id and r["stage"] == stage for r in self.receipts):
            raise AgentError(f"{booking_id} already has a {stage} receipt; collect is once per booking per stage")
        if stage == CAPPED_STAGE and cleared_cents > b.pay_cents:
            raise AgentError(f"cleared {cleared_cents} exceeds booking pay {b.pay_cents}; refused")
        receipt = split_cleared(cleared_cents, stage, self._key)
        receipt["booking_id"] = b.booking_id
        receipt["artist_id"] = b.artist_id
        receipt = _seal(receipt, self._key)
        self.receipts.append(receipt)
        return receipt

    def pay_cut(self, receipt: dict, cut_cents: int, payee: str) -> dict:
        """Pay a cut out of V8 on a receipt that is in THIS ledger and current."""
        if not isinstance(receipt, dict):
            raise AgentError("receipt must be a dict")
        idx = next((i for i, r in enumerate(self.receipts)
                    if r["booking_id"] == receipt.get("booking_id")
                    and r["stage"] == receipt.get("stage")), None)
        if idx is None:
            raise AgentError("receipt is not in this ledger; refused")
        if self.receipts[idx]["receipt_hash"] != receipt.get("receipt_hash"):
            raise AgentError("stale or foreign receipt: it does not match the ledger's current copy")
        out = pay_collaborator_cut(self.receipts[idx], cut_cents, payee, self._key)
        self.receipts[idx] = out
        return out

    # -- COMMUNICATE ----------------------------------------------------
    def send_message(self, booking_id: str, sender: str, text: str) -> dict:
        b = self._booking(booking_id)
        if b.state in ("REJECTED", "CANCELLED"):
            raise AgentError(f"cannot message on a {b.state} booking")
        if sender != OWNER and sender != b.artist_id:
            raise AgentError(f"sender {sender!r} is not a party to {booking_id}")
        gate_text(text)
        msg = {"booking_id": booking_id, "sender": sender, "text": text}
        self.messages.append(msg)
        return msg

    # -- expenses, releases, agenda ------------------------------------
    def add_expense(self, artist_id: str, category: str, cents: int, note: str = "") -> dict:
        self._artist(artist_id)
        if category not in EXPENSE_CATEGORIES:
            raise AgentError(f"unknown expense category {category!r}")
        if not _is_cents(cents) or cents <= 0:
            raise AgentError("expense must be a positive integer number of cents")
        exp = {"artist_id": artist_id, "category": category, "cents": cents, "note": note}
        self.expenses.append(exp)
        return exp

    def add_release(self, artist_id: str, title: str) -> Release:
        artist = self._artist(artist_id)
        if not isinstance(title, str) or not title.strip():
            raise AgentError("release needs a title")
        self._check_tier_cap(artist, "release")
        r = Release(self._next("release"), artist_id, title.strip())
        self.releases[r.release_id] = r
        return r

    def approve_release(self, release_id: str, actor: str) -> Release:
        if release_id not in self.releases:
            raise AgentError(f"unknown release {release_id!r}")
        r = self.releases[release_id]
        if r.state != "PREP":
            raise AgentError(f"release already {r.state}")
        if actor != r.artist_id:
            raise AgentError("only the releasing artist can approve a release")
        r.state = "READY"
        r.approved_by = actor
        return r

    def export_release(self, release_id: str) -> dict:
        """Local export package only. READY != live on any platform."""
        if release_id not in self.releases:
            raise AgentError(f"unknown release {release_id!r}")
        r = self.releases[release_id]
        if r.state != "READY":
            raise AgentError("export requires an approved READY release; unapproved releases are never exported")
        return {"release_id": r.release_id, "artist_id": r.artist_id, "title": r.title,
                "status": "LOCAL_EXPORT_ONLY_NOT_A_DISTRIBUTOR_RELEASE",
                "note": "Distribution to Spotify/Apple/SoundCloud/Bandcamp needs an authorized distributor integration; none is wired here."}

    def agenda(self, artist_id: str) -> list[Booking]:
        self._artist(artist_id)
        return sorted((b for b in self.bookings.values() if b.artist_id == artist_id),
                      key=lambda b: (b.date or "9999", b.title))

    def status(self) -> dict:
        return {"artists": len(self.artists), "opportunities": len(self.opportunities),
                "bookings": len(self.bookings), "releases": len(self.releases),
                "receipts": len(self.receipts),
                "cleared_cents_total": sum(r["cleared_cents"] for r in self.receipts),
                "banking_execution": BANKING_EXECUTION}

    # -- persistence ----------------------------------------------------
    def _dump(self) -> dict:
        return {"artists": [asdict(a) for a in self.artists.values()],
                "opportunities": [asdict(o) for o in self.opportunities.values()],
                "bookings": [asdict(b) for b in self.bookings.values()],
                "releases": [asdict(r) for r in self.releases.values()],
                "expenses": self.expenses, "messages": self.messages,
                "receipts": self.receipts, "counters": self._counters}

    def save(self, path: str) -> dict:
        data = self._dump()
        mode = "HMAC-SHA256" if self._key else "SHA256"
        sealed = {"version": STORE_VERSION, "hash_mode": mode, "data": data,
                  "file_hash": _hexmac(_canon(data), self._key)}
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(sealed, f, indent=2, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        return {"path": path, "file_hash": sealed["file_hash"], "hash_mode": mode}

    @classmethod
    def load(cls, path: str, key=None):
        try:
            with open(path) as f:
                sealed = json.load(f)
        except (OSError, ValueError) as e:
            raise AgentError(f"cannot read store file: {e}")
        if (not isinstance(sealed, dict) or not isinstance(sealed.get("data"), dict)
                or sealed.get("version") != STORE_VERSION):
            raise AgentError("store file is malformed or an unsupported version")
        agent = cls(key=key)
        mode = sealed.get("hash_mode")
        if mode not in ("SHA256", "HMAC-SHA256"):
            raise AgentError("store file is unsealed")
        if agent._key and mode != "HMAC-SHA256":
            raise AgentError("key configured but store file is not HMAC-sealed; refused")
        if mode == "HMAC-SHA256" and not agent._key:
            raise AgentError("store file is HMAC-sealed but no key was given")
        claimed = sealed.get("file_hash")
        expected = _hexmac(_canon(sealed["data"]), agent._key)
        if not isinstance(claimed, str) or not hmac.compare_digest(claimed, expected):
            raise AgentError("store file hash mismatch: file changed or corrupt, refused")
        data = sealed["data"]
        try:
            for a in data["artists"]:
                agent.artists[a["artist_id"]] = Artist(**a)
            for o in data["opportunities"]:
                agent.opportunities[o["opp_id"]] = Opportunity(**o)
            for b in data["bookings"]:
                agent.bookings[b["booking_id"]] = Booking(**b)
            for r in data["releases"]:
                agent.releases[r["release_id"]] = Release(**r)
            agent.expenses = list(data["expenses"])
            agent.messages = list(data["messages"])
            agent.receipts = list(data["receipts"])
            agent._counters = dict(data["counters"])
            agent._validate()
        except (KeyError, TypeError, AttributeError, ValueError) as e:
            if isinstance(e, AgentError):
                raise
            raise AgentError(f"malformed store contents: {e!r}")
        return agent

    def _validate(self) -> None:
        """Re-check every invariant on loaded data; a valid seal is not trust."""
        for kind, store in (("artist", self.artists), ("opp", self.opportunities),
                            ("booking", self.bookings), ("release", self.releases)):
            for id_, rec in store.items():
                if getattr(rec, f"{'opp' if kind == 'opp' else kind}_id") != id_:
                    raise AgentError(f"{kind} id does not match its key: {id_!r}")
                m = re.fullmatch(rf"{kind}-(\d+)", id_)
                if not m or not _is_cents(self._counters.get(kind)) or int(m.group(1)) > self._counters[kind]:
                    raise AgentError(f"{kind} id {id_!r} is ahead of its counter")
        for a in self.artists.values():
            if a.tier not in TIERS:
                raise AgentError(f"unknown tier {a.tier!r} on {a.artist_id}")
        for o in self.opportunities.values():
            if o.kind not in OPPORTUNITY_KINDS or not _is_cents(o.pay_cents) or o.pay_cents < 0:
                raise AgentError(f"invalid opportunity {o.opp_id}")
            _check_date(o.date)
        seen = set()
        for b in self.bookings.values():
            if b.state not in BOOKING_STATES:
                raise AgentError(f"invalid state on {b.booking_id}")
            if b.artist_id not in self.artists or b.opp_id not in self.opportunities:
                raise AgentError(f"{b.booking_id} references a missing artist or opportunity")
            if not _is_cents(b.pay_cents) or b.pay_cents < 0:
                raise AgentError(f"invalid pay on {b.booking_id}")
            _check_date(b.date, allow_empty=False)
            hist = b.history
            if (not isinstance(hist, list) or not hist
                    or any(not isinstance(h, dict) for h in hist)
                    or hist[0].get("action") != "offer"
                    or hist[-1].get("state") != b.state):
                raise AgentError(f"{b.booking_id} has an inconsistent history")
            acts = {(h.get("action"), h.get("actor")) for h in hist}
            if b.state in ("APPROVED", "CONFIRMED", "COMPLETED", "CANCELLED"):
                if ("approve", b.artist_id) not in acts:
                    raise AgentError(f"{b.booking_id} is {b.state} with no artist approval on record")
            if b.state in ("CONFIRMED", "COMPLETED", "CANCELLED"):
                if not any(a == "confirm" for a, _ in acts):
                    raise AgentError(f"{b.booking_id} is {b.state} with no confirmation on record")
            if b.state in ACTIVE_STATES:
                k = (b.artist_id, b.date)
                if k in seen:
                    raise AgentError(f"double booking in store for {k}")
                seen.add(k)
        for r in self.releases.values():
            if r.artist_id not in self.artists or r.state not in ("PREP", "READY"):
                raise AgentError(f"invalid release {r.release_id}")
            if r.state == "READY" and r.approved_by != r.artist_id:
                raise AgentError(f"{r.release_id} is READY without its artist's approval")
        for e in self.expenses:
            if (not isinstance(e, dict) or e.get("artist_id") not in self.artists
                    or e.get("category") not in EXPENSE_CATEGORIES
                    or not _is_cents(e.get("cents")) or e["cents"] <= 0):
                raise AgentError("invalid expense in store")
        for m in self.messages:
            if not isinstance(m, dict) or m.get("booking_id") not in self.bookings:
                raise AgentError("message references a missing booking")
            b = self.bookings[m["booking_id"]]
            if m.get("sender") not in (OWNER, b.artist_id):
                raise AgentError("message has a sender who is not a party to the booking")
            gate_text(m.get("text"))
        rseen = set()
        for r in self.receipts:
            _check_receipt(r, self._key)
            b = self.bookings.get(r.get("booking_id"))
            if b is None or b.state != "COMPLETED" or b.artist_id != r.get("artist_id"):
                raise AgentError("receipt does not belong to a COMPLETED booking of the same artist")
            k = (r["booking_id"], r["stage"])
            if k in rseen:
                raise AgentError(f"duplicate receipt for {k}")
            rseen.add(k)
            if r["stage"] == CAPPED_STAGE and r["cleared_cents"] > b.pay_cents:
                raise AgentError("receipt clears more than the booking pays")


def demo() -> dict:
    """One full pass, no network, no money moved. Includes a save/load round trip."""
    import tempfile
    agent = TalentAgent(key="demo-key")
    artist = agent.add_artist("Test Artist", skills=["music"], location="Peoria", tier="try")
    opp = agent.add_opportunity("Peoria Gig", kind="gig", skills=["music"],
                                location="Peoria", date="2026-11-20", pay_cents=100000)
    found = agent.find_opportunities(skill="music")
    booking = agent.offer_booking(artist.artist_id, opp.opp_id)
    agent.approve_booking(booking.booking_id, actor=artist.artist_id)
    agent.confirm_booking(booking.booking_id)
    agent.send_message(booking.booking_id, artist.artist_id, "Load-in at 6pm works for me.")
    agent.complete_booking(booking.booking_id)
    receipt = agent.collect(booking.booking_id, 100000, "gig-pay-cleared")
    receipt = agent.pay_cut(receipt, 4000, "Test Collaborator")
    release = agent.add_release(artist.artist_id, "First Stitch")
    agent.approve_release(release.release_id, actor=artist.artist_id)
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "store.json")
        saved = agent.save(p)
        reloaded = TalentAgent.load(p, key="demo-key")
    return {"found": len(found), "booking_state": booking.state,
            "receipt": receipt, "export": agent.export_release(release.release_id),
            "status": agent.status(),
            "save_load_roundtrip": {"hash_mode": saved["hash_mode"],
                                    "receipts_after_load": len(reloaded.receipts),
                                    "identical_status": reloaded.status() == agent.status()}}


if __name__ == "__main__":
    print(json.dumps(demo(), indent=2))
    print("BANKING: NONE_LEDGER_RECEIPT_ONLY. No money was moved.")
