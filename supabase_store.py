"""Talent Agent Plus — Supabase durable store adapter (stdlib only).

What this is: the PostgREST client that lets the Render server use the
Supabase project "jga-apps" as its durable store for the `tap` schema
(see supabase/migrations/001_tap_core.sql and
002_tap_public_wrappers.sql). No new dependency — stdlib urllib only.

How it is used:
  * The server reads SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY from
    the environment ONLY. Both must be set, and the URL must be https.
  * `SupabaseStore.from_env().initialize()` pings the project. On any
    failure (not configured, unreachable, wrong key) it returns False
    and the server keeps the existing in-memory store EXACTLY as today
    — it never crashes, never retries in a loop, never logs a key.
  * Money and state changes go through the security-definer functions
    in the migrations, reached here as public RPC wrappers
    (tap_claim_trial, tap_record_cleared_payment, tap_transition_booking,
    tap_append_evidence, tap_verify_evidence_chain). The server holds
    the service-role key and is the only database client; per-person
    isolation stays enforced in the app layer (an actor may act only
    for their own artist id; the owner may act for any artist), exactly
    as the engine does today.
  * Trial claims go through tap.claim_trial() so one-time-ever holds
    across restarts. In-memory mode keeps a process-local claim set
    instead — that set does NOT survive a restart (the v1 limit this
    wiring removes once Supabase is live).

What this is NOT (stated so it cannot drift):
  * This adapter is the durable-store CLIENT. The runtime engine
    (TalentAgent / SecureAgent / MerchantStore) still holds the live
    state in memory in v1; a full migration of the engine to read
    from Postgres on every call is a later step, verified against the
    live database — it is NOT claimed here.
  * Table reads on the `tap` schema need the schema added to the
    Supabase project's exposed schemas (Dashboard -> Settings -> API).
    The RPC wrappers live in `public` and need no extra exposure.
  * The database 11VT split (tap.split_11vt) floors the ten fixed
    buckets and gives V7 the remainder. The Python engine rounds
    half-up (with a round-down fallback), V7 as plug. Both sum exactly
    to the cleared amount, but the per-bucket distribution can differ
    by a cent on odd amounts. Reconcile the rounding rule in the
    live-DB step; neither side was changed here.

Secrets: the service-role key is read from the environment and is
NEVER written to a file, a log line, or an exception message. The
project URL host is not secret and may appear in logs.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

_PING_TIMEOUT = 5.0
_RPC_TIMEOUT = 10.0
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


class SupabaseError(Exception):
    """A Supabase call failed. The message never contains a key."""


class TrialAlreadyClaimed(SupabaseError):
    """tap.claim_trial() refused: this email already claimed a trial."""


class ArtistNotFound(SupabaseError):
    """No tap.artists row exists for that email — add the artist first."""


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


class SupabaseStore:
    """PostgREST client for the `tap` schema. Fail-safe by design."""

    def __init__(self, url: str | None = None, service_role_key: str | None = None):
        self.url = (url if url is not None else _env("SUPABASE_URL")).rstrip("/")
        self._key = service_role_key if service_role_key is not None else _env("SUPABASE_SERVICE_ROLE_KEY")
        self.configured = bool(self.url and self._key and self.url.startswith("https://"))
        self.enabled = False  # True only after a successful ping

    @classmethod
    def from_env(cls) -> "SupabaseStore":
        return cls()

    # -- lifecycle ------------------------------------------------------
    def initialize(self) -> bool:
        """Ping the project once. True = durable store live. False = keep
        the in-memory store. Never raises, never logs a key."""
        if not self.configured:
            return False
        try:
            self.enabled = self.ping()
        except Exception:
            self.enabled = False
        return self.enabled

    def ping(self) -> bool:
        """A plain GET on the PostgREST root. True on HTTP 200 only."""
        try:
            status, _ = self._request("GET", "/rest/v1/", timeout=_PING_TIMEOUT)
            return status == 200
        except SupabaseError:
            return False

    # -- low-level HTTP -------------------------------------------------
    def _headers(self, profile: str | None = None) -> dict:
        headers = {
            "apikey": self._key,
            "Authorization": "Bearer " + self._key,
            "Content-Type": "application/json",
        }
        if profile:
            headers["Accept-Profile"] = profile
            headers["Content-Profile"] = profile
        return headers

    def _request(self, method: str, path: str, body=None, profile: str | None = None,
                 timeout: float = _RPC_TIMEOUT):
        """One HTTP call. Returns (status, parsed JSON or None).
        Raises SupabaseError with the status only — never the key, never
        a response body that could carry one."""
        if not self.configured:
            raise SupabaseError("Supabase is not configured (SUPABASE_URL and "
                                "SUPABASE_SERVICE_ROLE_KEY must both be set)")
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data=data,
                                     headers=self._headers(profile), method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode()
                return resp.status, (json.loads(raw) if raw.strip() else None)
        except urllib.error.HTTPError as e:
            # PostgREST puts the raised exception name in the body's
            # `message` field (e.g. "trial_already_claimed"). Carry that
            # short message only — never a raw body, never a key.
            detail = ""
            try:
                body = json.loads(e.read().decode())
                if isinstance(body, dict) and isinstance(body.get("message"), str):
                    detail = ": " + body["message"][:200]
            except Exception:
                detail = ""
            raise SupabaseError("Supabase request failed with HTTP %d%s" % (e.code, detail))
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise SupabaseError("Supabase request failed: %s" % type(e).__name__)

    # -- PostgREST primitives -------------------------------------------
    def rpc(self, name: str, params: dict):
        """Call a public RPC wrapper. Void functions return None (204)."""
        status, payload = self._request("POST", "/rest/v1/rpc/" + name, body=params or {})
        if status not in (200, 201, 204):
            raise SupabaseError("RPC %s failed with HTTP %d" % (name, status))
        return payload

    def table_select(self, table: str, filters: dict, profile: str = "tap"):
        """Select rows from a schema table. `filters` are exact matches
        (eq). Returns a list (possibly empty). Needs the schema exposed
        in the project's API settings."""
        query = "&".join("%s=eq.%s" % (k, urllib.request.quote(str(v), safe=""))
                         for k, v in filters.items())
        path = "/rest/v1/%s?%s" % (table, query) if query else "/rest/v1/%s" % table
        status, payload = self._request("GET", path, profile=profile)
        if status != 200 or not isinstance(payload, list):
            raise SupabaseError("select on %s failed with HTTP %d" % (table, status))
        return payload

    # -- tap schema: artists --------------------------------------------
    def artist_by_email(self, email: str):
        """The tap.artists row for an email, or None. Artists are keyed
        by email in the tap schema (unique index on lower(email))."""
        email = (email or "").strip().lower()
        if not email or "@" not in email:
            raise SupabaseError("a real email is required")
        rows = self.table_select("artists", {"email": email})
        return rows[0] if rows else None

    # -- tap schema: trials (one time per email, ever) ------------------
    def claim_trial(self, email: str, artist_id: str | None = None):
        """Claim the 7-day Full Label trial for an email, through
        tap.claim_trial(). A second claim for the same email raises
        TrialAlreadyClaimed — the claim survives restarts because the
        row lives in Postgres, not in process memory.
        `artist_id` may be a tap artist uuid; otherwise the artist is
        resolved by email first."""
        email = (email or "").strip().lower()
        if not email or "@" not in email:
            raise SupabaseError("a real email is required")
        if artist_id and _UUID_RE.match(artist_id):
            tap_artist_id = artist_id
        else:
            row = self.artist_by_email(email)
            if row is None:
                raise ArtistNotFound(
                    "no Supabase artist for %s — add the artist first" % email)
            tap_artist_id = row["id"]
        try:
            self.rpc("tap_claim_trial",
                     {"p_email": email, "p_artist_id": tap_artist_id})
        except SupabaseError as e:
            if "trial_already_claimed" in str(e):
                raise TrialAlreadyClaimed(
                    "trial already claimed for %s — one trial per email, ever" % email)
            raise
        return {"email": email, "artist_id": tap_artist_id, "trial": "claimed"}

    # -- tap schema: money (security-definer functions) -----------------
    def record_cleared_payment(self, artist_id: str, stripe_object_id: str,
                               stage: str, amount_cents: int, actor: str,
                               booking_id: str | None = None,
                               design_order_id: str | None = None,
                               correlation_id: str | None = None):
        """Record a cleared payment once (idempotent on the Stripe object
        id). Splits 11VT in the database and appends ledger evidence.
        Returns the payment uuid. Same Stripe object id twice returns
        the first row, never a double."""
        params = {
            "p_artist_id": artist_id,
            "p_booking_id": booking_id,
            "p_design_order_id": design_order_id,
            "p_stripe_object_id": stripe_object_id,
            "p_stage": stage,
            "p_amount_cents": int(amount_cents),
            "p_actor": actor,
        }
        if correlation_id:
            params["p_correlation_id"] = correlation_id
        return self.rpc("tap_record_cleared_payment", params)

    def transition_booking(self, booking_id: str, to_state: str, actor: str,
                           correlation_id: str | None = None):
        """Move a booking through the state machine in the database.
        Invalid transitions are refused by tap.transition_booking()."""
        params = {"p_booking_id": booking_id, "p_to": to_state, "p_actor": actor}
        if correlation_id:
            params["p_correlation_id"] = correlation_id
        return self.rpc("tap_transition_booking", params)

    def append_evidence(self, correlation_id: str, artist_id: str | None,
                        event_type: str, payload: dict):
        """Append one entry to the hash-chained tap ledger. Returns the
        entry hash."""
        return self.rpc("tap_append_evidence", {
            "p_correlation_id": correlation_id,
            "p_artist_id": artist_id,
            "p_event_type": event_type,
            "p_payload": payload or {},
        })

    def verify_evidence_chain(self) -> bool:
        """Re-verify the whole tap ledger chain in the database."""
        result = self.rpc("tap_verify_evidence_chain", {})
        return bool(result)
