"""Talent Agent Plus — actual app, v1.

A real, runnable phone app (not a mockup, not a demo dashboard):
a stdlib-only Python HTTP server wrapping the verified
TalentAgentPlusComplete engine, plus a phone-first PWA frontend
in frontend/. Open http://127.0.0.1:8080/ after starting it.

    python3 server.py [--port 8080] [--host 127.0.0.1]

Auth: owner setup once, then owner/artist login with passwords
(PBKDF2 hashes in the engine, session tokens, 24h TTL). Every
mutating call needs a Bearer token. Artists can act only for
themselves; the owner can act for any artist.

Money rules, unchanged from the engine:
- Bookings and merch are split ONLY when cleared, with the real
  11VT (V1 15, V2 7.5, V3 3.5, V4 5, V5 2, V6 15, V7 residual,
  V8 20, V9 10, V10 8, V11 2). Collaborator cuts come out of V8 only.
- No bank movement, no live Stripe charge, no live distributor,
  no live social post. Posts are drafts, releases export locally,
  payments are a registry (Stripe NOT_CONNECTED).
- Cleared status is NEVER toggled by a click. Clearing requires
  the cleared amount, and it must equal the order/booking amount
  the engine checks.

Limits, stated so they cannot drift:
- v1 is SINGLE-PROCESS and IN-MEMORY by default. Restarting the server
  clears all artists, bookings, shop, campaigns, and sessions. That is the
  engine's own in-memory token/store design, not a bug to hide.
  A public multi-user Play Store release needs a real database
  with transactions before it can serve many phones at once.
- Supabase mode: when SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are set
  in the environment, the server uses the Supabase project as its durable
  store (supabase_store.py, stdlib PostgREST, service-role key held by
  the server only). Trial claims go through tap.claim_trial() so
  one-time-ever holds across restarts; money/state changes go through
  the security-definer functions. If Supabase is unset or unreachable
  at startup, the server runs in-memory exactly as before and says so
  on /api/health ("store": "in-memory"). The runtime engine still holds
  live state in memory in v1 — the adapter is the durable-store client,
  not a full engine-to-Postgres migration (that is verified against the
  live database, not claimed here).
- This is NOT yet an APK/AAB and NOT on the Play Store. It is the
  working app that a Play Store wrapper (Capacitor/Android Studio)
  will package, and the API the wrapper will call. Publishing
  needs John's Google Play developer account, which is his step.
- Tokens are stored in the browser's localStorage in v1. A packaged
  phone app must move them to the platform secure storage.
"""
import hmac
import json
import os
import sys
from dataclasses import asdict, is_dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from combined import TalentAgentPlusComplete  # noqa: E402
from talent_agent_plus import AgentError, split_cleared, OWNER  # noqa: E402
from secure_agent import AuthError  # noqa: E402
from supabase_store import SupabaseStore, SupabaseError, TrialAlreadyClaimed  # noqa: E402

APP = TalentAgentPlusComplete()
VERSION = "1.0.0-app"

# Durable store (Supabase) if configured and reachable at startup;
# otherwise the existing in-memory store, exactly as before.
# Never crashes, never logs a key — see supabase_store.py.
STORE = SupabaseStore.from_env()
STORE_MODE = "supabase" if STORE.initialize() else "in-memory"

# In-memory trial claims, used ONLY when Supabase is not live.
# Process-local: does NOT survive a restart (the v1 limit the Supabase
# wiring removes — tap.claim_trial() is the durable record).
TRIAL_CLAIMS: set = set()


def ser(obj):
    if is_dataclass(obj):
        return asdict(obj)
    if isinstance(obj, (list, tuple)):
        return [ser(x) for x in obj]
    if isinstance(obj, dict):
        return {k: ser(v) for k, v in obj.items()}
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return str(obj)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass  # keep the console clean; errors go in the JSON body

    def _send(self, code, obj):
        body = json.dumps(ser(obj)).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path, ctype):
        try:
            with open(path, "rb") as f:
                data = f.read()
        except FileNotFoundError:
            self._send(404, {"error": "not found"})
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode())
        except Exception:
            return {}

    def _token(self):
        auth = self.headers.get("Authorization") or ""
        if auth.startswith("Bearer "):
            return auth[7:]
        return ""

    def do_GET(self):
        parsed = urlparse(self.path)
        path, qs = parsed.path, parse_qs(parsed.query)
        try:
            if path == "/":
                return self._send_file(os.path.join(HERE, "frontend", "index.html"), "text/html")
            if path.startswith("/frontend/"):
                name = os.path.basename(path)
                ctype = {"html": "text/html", "js": "text/javascript",
                         "css": "text/css", "json": "application/json",
                         "svg": "image/svg+xml"}.get(name.rsplit(".", 1)[-1], "text/plain")
                return self._send_file(os.path.join(HERE, "frontend", name), ctype)
            if path == "/api/health":
                return self._send(200, {"ok": True, "product": "Talent Agent Plus",
                                        "version": VERSION, "banking_execution": "NONE_LEDGER_RECEIPT_ONLY",
                                        "store": STORE_MODE, "supabase_configured": STORE.configured})
            if path == "/api/store/verify":
                if APP.secure._require(self._token()) != OWNER:
                    raise AuthError("owner only")
                if STORE.enabled:
                    try:
                        valid = STORE.verify_evidence_chain()
                    except SupabaseError as e:
                        raise AgentError("Supabase verify failed: " + str(e))
                    return self._send(200, {"ok": True, "store": STORE_MODE,
                                            "evidence_chain_valid": valid})
                return self._send(200, {"ok": True, "store": STORE_MODE,
                                        "evidence_chain_valid": None,
                                        "note": "Supabase is not configured — running in-memory; nothing to verify"})
            if path == "/api/tiers":
                tok = self._token()
                return self._send(200, APP.all_tiers(tok))
            if path == "/api/discover/artists":
                return self._send(200, APP.browse_artists(
                    skill=qs.get("skill", [""])[0], location=qs.get("location", [""])[0]))
            if path == "/api/discover/samples":
                return self._send(200, APP.browse_samples())
            if path.startswith("/api/artists/") and path.endswith("/calendar"):
                aid = path.split("/")[3]
                return self._send(200, APP.booking_calendar(self._token(), aid))
            if path.startswith("/api/artists/") and path.endswith("/agenda"):
                aid = path.split("/")[3]
                return self._send(200, APP.agenda(self._token(), aid))
            if path.startswith("/api/artists/") and path.endswith("/summary"):
                aid = path.split("/")[3]
                return self._send(200, APP.summary(self._token(), aid))
            if path.startswith("/api/shop/"):
                aid = path.split("/")[3]
                return self._send(200, APP.storefront(self._token(), aid))
            if path == "/api/campaigns":
                return self._send(200, APP.list_campaigns(self._token(), qs.get("artist_id", [""])[0]))
            if path == "/api/productions":
                return self._send(200, APP.list_productions(self._token(), qs.get("artist_id", [""])[0]))
            if path == "/api/tasks":
                return self._send(200, APP.open_tasks(self._token(), qs.get("artist_id", [""])[0]))
            if path == "/api/calls":
                return self._send(200, APP.list_calls(self._token(), qs.get("artist_id", [""])[0]))
            if path == "/api/opportunities":
                return self._send(200, APP.find_opportunities(
                    self._token(), skill=qs.get("skill", [""])[0],
                    kind=qs.get("kind", [""])[0], location=qs.get("location", [""])[0]))
            if path == "/api/connect-requests":
                return self._send(200, APP.artist_connect_requests(self._token(), qs.get("artist_id", [""])[0]))
            if path == "/api/payments":
                return self._send(200, APP.payment_status(self._token()))
            if path == "/api/branding":
                return self._send(200, APP.branding(self._token()))
            return self._send(404, {"error": "unknown route"})
        except (AgentError, AuthError) as e:
            self._send(400, {"error": str(e)})
        except Exception as e:
            self._send(500, {"error": type(e).__name__ + ": " + str(e)})

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        body = self._body()
        tok = self._token()
        try:
            if path == "/api/setup-owner":
                bootstrap = os.environ.get("TAP_OWNER_SETUP_TOKEN", "")
                supplied = body.get("setup_token", "")
                if not bootstrap:
                    return self._send(503, {"error": "owner bootstrap is disabled; configure TAP_OWNER_SETUP_TOKEN"})
                if not isinstance(supplied, str) or not hmac.compare_digest(supplied, bootstrap):
                    return self._send(403, {"error": "invalid owner bootstrap credential"})
                APP.create_owner(body["password"])
                return self._send(200, {"ok": True})
            if path == "/api/login-owner":
                return self._send(200, {"token": APP.login_owner(body["password"]), "principal": "owner"})
            if path == "/api/login-artist":
                return self._send(200, {"token": APP.login_artist(body["artist_id"], body["password"]),
                                        "principal": body["artist_id"]})
            if path == "/api/logout":
                APP.logout(tok)
                return self._send(200, {"ok": True})
            if path == "/api/artists":
                return self._send(200, APP.add_artist(tok, body["name"], skills=body.get("skills", []),
                                                      location=body.get("location", ""), tier=body.get("tier", "try")))
            if path.startswith("/api/artists/") and path.endswith("/password"):
                aid = path.split("/")[3]
                APP.register_artist_password(tok, aid, body["password"])
                return self._send(200, {"ok": True})
            if path.startswith("/api/artists/") and path.endswith("/tier"):
                aid = path.split("/")[3]
                return self._send(200, {"tier": APP.set_tier(tok, aid, body["tier"])})
            if path == "/api/opportunities":
                return self._send(200, APP.add_opportunity(tok, body["title"], body["kind"],
                                                           body.get("skills", []), body.get("location", ""),
                                                           body.get("date", ""), int(body.get("pay_cents", 0))))
            if path == "/api/bookings/offer":
                return self._send(200, APP.offer_booking(tok, body["artist_id"], body["opp_id"]))
            if path == "/api/bookings/approve":
                return self._send(200, APP.approve_booking(tok, body["booking_id"]))
            if path == "/api/bookings/confirm":
                return self._send(200, APP.confirm_booking(tok, body["booking_id"]))
            if path == "/api/bookings/reject":
                return self._send(200, APP.reject_booking(tok, body["booking_id"]))
            if path == "/api/bookings/cancel":
                return self._send(200, APP.cancel_booking(tok, body["booking_id"]))
            if path == "/api/bookings/complete":
                return self._send(200, APP.complete_booking(tok, body["booking_id"]))
            if path == "/api/bookings/collect":
                return self._send(200, APP.collect(tok, body["booking_id"], int(body["cleared_cents"]), body["stage"]))
            if path == "/api/bookings/pay-cut":
                return self._send(200, APP.pay_cut(tok, body["receipt"], int(body["cut_cents"]), body["payee"]))
            if path == "/api/bookings/message":
                return self._send(200, APP.send_message(tok, body["booking_id"], body["text"]))
            if path == "/api/calc-11vt":
                cents = int(body["cleared_cents"])
                if body.get("cleared") is False:
                    return self._send(400, {"error": "uncleared money is never split"})
                if cents <= 0:
                    return self._send(400, {"error": "cleared gross must be positive"})
                receipt = split_cleared(cents, "gig-pay-cleared")
                split = dict(receipt["split"])
                cut = int(body.get("cut_cents", 0))
                if cut > split["V8"]:
                    return self._send(400, {"error": "cut exceeds V8; cuts come out of V8 only"})
                split["V8_after_cut"] = split["V8"] - cut
                return self._send(200, {"split": split, "total_check": sum(split[k] for k in split if k != "V8_after_cut")})
            if path == "/api/shop/products":
                return self._send(200, APP.add_product(tok, body["artist_id"], body["name"], body["kind"],
                                                       int(body["price_cents"]), int(body["stock"])))
            if path == "/api/shop/order":
                return self._send(200, APP.order_merch(tok, body["artist_id"], body["product_id"], int(body["qty"])))
            if path == "/api/shop/confirm":
                return self._send(200, APP.confirm_merch_order(tok, body["order_id"]))
            if path == "/api/shop/clear":
                return self._send(200, APP.clear_merch_order(tok, body["order_id"], int(body["cleared_cents"])))
            if path == "/api/shop/fulfill":
                return self._send(200, APP.fulfill_merch_order(tok, body["order_id"], body.get("shipping_note", "")))
            if path == "/api/shop/cancel":
                return self._send(200, APP.cancel_merch_order(tok, body["order_id"]))
            if path == "/api/shop/restock":
                return self._send(200, APP.restock_product(tok, body["product_id"], int(body["qty"])))
            if path == "/api/campaigns":
                return self._send(200, APP.create_campaign(tok, body["artist_id"], body["title"],
                                                           body.get("channels", []), int(body["budget_cap_cents"])))
            if path == "/api/campaigns/spend":
                return self._send(200, APP.campaign_spend(tok, body["campaign_id"], int(body["amount_cents"]), body.get("note", "")))
            if path == "/api/campaigns/draft":
                return self._send(200, APP.draft_post(tok, body["campaign_id"], body["text"]))
            if path == "/api/campaigns/approve":
                return self._send(200, APP.approve_post(tok, body["campaign_id"], int(body["index"])))
            if path == "/api/releases":
                return self._send(200, APP.add_release(tok, body["artist_id"], body["title"]))
            if path == "/api/releases/approve":
                return self._send(200, APP.approve_release(tok, body["release_id"]))
            if path == "/api/releases/export":
                return self._send(200, APP.export_release(tok, body["release_id"]))
            if path == "/api/productions":
                return self._send(200, APP.start_production(tok, body["artist_id"], body["release_id"]))
            if path == "/api/productions/advance":
                return self._send(200, APP.advance_production(tok, body["production_id"], body.get("note", "")))
            if path == "/api/productions/note":
                return self._send(200, APP.production_note(tok, body["production_id"], body["text"]))
            if path == "/api/tasks":
                return self._send(200, APP.add_task(tok, body["artist_id"], body["kind"], body["title"], body.get("due", "")))
            if path == "/api/tasks/complete":
                return self._send(200, APP.complete_task(tok, body["task_id"]))
            if path == "/api/brief":
                return self._send(200, APP.daily_brief(tok, body["artist_id"]))
            if path == "/api/messages/draft":
                return self._send(200, APP.draft_message(tok, body["artist_id"], body.get("title", body.get("to", "")), body["text"]))
            if path == "/api/calls":
                return self._send(200, APP.schedule_call(tok, body["artist_id"], body["contact"], body["purpose"], body.get("date", "")))
            if path == "/api/calls/log":
                return self._send(200, APP.log_call(tok, body["call_id"], body["outcome"], body.get("notes", "")))
            if path == "/api/calls/script":
                return self._send(200, APP.draft_call_script(tok, body["call_id"], body["text"]))
            if path == "/api/discover/publish":
                return self._send(200, APP.publish_profile(tok, body["artist_id"], body["bio"]))
            if path == "/api/discover/unpublish":
                return self._send(200, APP.unpublish_profile(tok, body["artist_id"]))
            if path == "/api/discover/sample":
                return self._send(200, APP.add_sample(tok, body["artist_id"], body["title"], body["kind"], body.get("notes", "")))
            if path == "/api/discover/connect":
                return self._send(200, APP.request_connection(body["artist_id"], body["seeker_name"],
                                                              body["seeker_contact"], body.get("message", "")))
            if path == "/api/discover/decide":
                return self._send(200, APP.decide_connect_request(tok, body["request_id"], bool(body["accept"])))
            if path == "/api/expenses":
                return self._send(200, APP.add_expense(tok, body["artist_id"], body.get("category", ""),
                                                       int(body.get("amount_cents", 0)), body.get("note", "")))
            if path == "/api/trial/claim":
                # Owner only. Durable when Supabase is live (tap.claim_trial);
                # process-local claim set when in-memory (does not survive
                # a restart — the v1 limit the Supabase wiring removes).
                if APP.secure._require(tok) != OWNER:
                    raise AuthError("only the owner can claim a trial")
                email = (body.get("email") or "").strip().lower()
                if "@" not in email:
                    raise AgentError("a real email is required to claim a trial")
                if STORE.enabled:
                    try:
                        claimed = STORE.claim_trial(email, body.get("artist_id"))
                    except TrialAlreadyClaimed as e:
                        raise AgentError(str(e))
                    except SupabaseError as e:
                        raise AgentError("Supabase trial claim failed: " + str(e))
                    return self._send(200, {"ok": True, "store": STORE_MODE, **claimed})
                if email in TRIAL_CLAIMS:
                    raise AgentError("trial already claimed for %s — one trial per email, ever" % email)
                TRIAL_CLAIMS.add(email)
                return self._send(200, {"ok": True, "store": STORE_MODE, "email": email,
                                        "artist_id": body.get("artist_id"), "trial": "claimed",
                                        "note": "in-memory claim — does not survive a restart"})
            return self._send(404, {"error": "unknown route"})
        except (AgentError, AuthError, KeyError, ValueError, TypeError) as e:
            self._send(400, {"error": str(e)})
        except Exception as e:
            self._send(500, {"error": type(e).__name__ + ": " + str(e)})


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8080)
    a = p.parse_args()
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print(f"Talent Agent Plus v{VERSION} running at http://{a.host}:{a.port}/  (Ctrl-C stops it)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
