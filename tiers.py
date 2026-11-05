"""Tiers and prices — John's decided 4 tiers (2026-11-02).

Flat software subscription, NOT a commission. Each tier includes
everything below it. Human approval gates stay ON at every tier.
Distributor fees and ad spend are separate; the artist uses their
own ad account. Data integrity and security are FREE at every tier,
including Try — key-bound identity, sealed pockets, hash-chained
ledger are baseline, not an upsell.

Prices are taken from the core TIERS table, not re-typed here, so
the price list can never drift from the enforcement caps.
"""
from talent_agent_plus import TIERS, normalize_tier

FEATURES = {
    "try": ["Profile vault", "Up to 3 active bookings", "Expense tracking", "1 release prep (lifetime)",
            "Data integrity and security (baseline, free)"],
    "starter": ["Everything in Try, uncapped bookings/releases", "Calendar", "Booking inbox and follow-ups",
                "Pay tracking", "11VT receipts on cleared money", "Materials vault",
                "Social posting schedule, drafted for artist approval"],
    "pro": ["Everything in Starter", "Disclosed AI calls (inbound + artist-approved outbound only)",
            "Booking negotiation drafts", "Casting submission engine", "Release prep for a distributor",
            "Ad campaigns with an artist-set budget cap", "Merch/CD inventory with low-stock alerts",
            "Tour logistics"],
    "full_label": ["Everything in Pro", "Full agent work", "Tour planning end to end",
                   "Investor package (attorney-reviewed contracts)", "Royalty/recoupment accounting",
                   "Manager dashboard", "Season/run/shoot management, as built"],
}


def tier_info(tier: str) -> dict:
    t = normalize_tier(tier)
    base = TIERS[t]
    return {"tier": t, "name": base["name"],
            "monthly": f"${base['monthly_cents'] / 100:,.2f}" if base["monthly_cents"] else "FREE",
            "yearly": f"${base['yearly_cents'] / 100:,.2f}" if base["yearly_cents"] else "FREE",
            "monthly_cents": base["monthly_cents"], "yearly_cents": base["yearly_cents"],
            "max_active_bookings": base["max_active_bookings"],
            "max_releases": base["max_releases"],
            "features": FEATURES[t],
            "approval_gates": "ON at every tier",
            "pricing_note": "Flat software price, not commission. Distributor fees and ad spend separate."}


def all_tiers() -> list[dict]:
    return [tier_info(t) for t in ("try", "starter", "pro", "full_label")]
