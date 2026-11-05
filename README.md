# Talent Agent Plus — actual app, v1

A real, runnable app, not a mockup. Python stdlib only — no pip installs.

## Run it

```bash
python3 server.py --port 8080
# open http://127.0.0.1:8080/  (on a phone on the same Wi-Fi, use the machine's LAN address instead of 127.0.0.1)
```

First run: set an owner password (8+ characters) on Home, sign in as
owner, add an artist, set that artist's password, then sign in as the
artist. The app starts empty — no demo artists, bookings, or sales.

## What it does, with the real engine

Agent (find, book, approve, confirm, complete, collect, cut),
shop (products, stock, orders, clear, fulfill), promoter (capped
campaigns, drafted posts only), producer (pipeline, artist-only
release approval, local export), secretary (tasks, brief, drafts),
calls (log only, not live telephony), 11VT calculator, and Discover
(opt-in publish, samples labeled provided by the artist, pending
connection requests).

Money rules are the engine's, unchanged: cleared-only splits with
the real 11VT, cuts out of V8 only, no bank movement, Stripe
NOT_CONNECTED, no live distributor, no live social post.

## Tests

```bash
python3 -m unittest tests.test_server -v
```

Verified 2026-11-04: full journey plus negatives — unauthenticated
booking refused, double collect refused, wrong-amount clear refused,
over-cap campaign spend refused, uncleared calculator refused,
cut over V8 refused. $1,000 cleared gig → V8 $20,000 cents, cut
$4,000 → V8 $16,000 cents. $5,000 calculator split sums exactly,
V7 $6,000 cents. Frontend JS syntax-checked with node --check.
Live server test: health, home page, empty Discover, owner
setup/login all answered.

## Limits — read before a real artist uses it

- Single-process, in-memory v1. Restarting the server clears all
  data and sessions. A public multi-user release needs a real
  database with transactions first.
- Tokens live in browser localStorage in v1. A packaged phone app
  must move them to platform secure storage.
- This zip is NOT an APK/AAB and is NOT on the Play Store. The
  Play Store step is packaging this web app (Capacitor/Android
  Studio) plus John's Google Play developer account. This machine
  has no Java or Android SDK, so the APK cannot be compiled here.
- This is a tool for artist self-representation, not a licensed
  talent agency. Union and CA/NY bookings go through a licensed
  franchised agent or attorney.
