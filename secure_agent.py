"""Talent Agent Plus v2.1 — auth + concurrency layer over the official v2 core.

Why a layer, not a core rewrite:
  * v2 is now the OFFICIAL baseline (John, 2026-11-04). Rewriting it
    for auth would risk the 20 tests that just proved it. This layer
    wraps it, so every core party/state/11VT check still runs, and
    auth + locking run on top of it (defense in depth).

What it fixes, plainly:
  * AUTHENTICATION (v2 gap): in v2, `actor` is just a string the
    caller asserts — anyone could claim to be artist-0001. Here,
    every mutating call needs a session token issued by a password
    login. Passwords are NEVER stored: only a PBKDF2-HMAC-SHA256
    hash + salt (200,000 iterations, stdlib hashlib). Tokens are
    secrets.token_hex(32), in-memory only, valid 24 hours.
    - Owner token: may act as "owner" for any artist.
    - Artist token: may act ONLY as its own artist_id. An artist
      token trying to approve, collect, message, or spend for
      another artist is refused here, before the core is even called.
    - Read methods (find, agenda, status) also need a valid token,
      and an artist sees only its own agenda/bookings.
  * CONCURRENCY (v2 gap): every mutating call runs under one
    threading.RLock on this wrapper instance, so two threads cannot
    both collect the same booking/stage, or both offer the same
    artist/date, at the same instant. Exactly one wins; the others
    get the core's normal refusal (already-collected / double-booked).

What it does NOT fix, stated so it cannot drift:
  * This lock is IN-PROCESS only. It does not protect two separate
    processes, two phones, or two servers sharing one store file.
    A public multi-user server needs a database with real
    transactions — that is a later piece, not claimed here.
  * This is not a real identity provider, OAuth, or 2FA. It is a
    local password + session-token layer for a single-owner / small
    roster app. Phone login must use this layer, never call the
    core directly with a caller-asserted actor string.
  * Tokens are in-memory: restarting the process logs everyone out.
    That is a safety property here, not a bug to hide.

Use:  agent = SecureAgent(TalentAgent(key="..."))
      agent.create_owner("owner-password")   # once, by the owner
      token = agent.login_owner("owner-password")
      agent.offer_booking(token, artist_id, opp_id)
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time

from talent_agent_plus import TalentAgent, AgentError, OWNER

_TOKEN_TTL_SECONDS = 24 * 3600
_PBKDF2_ITERATIONS = 200_000


class AuthError(AgentError):
    pass


def _hash_password(password: str, salt: bytes) -> bytes:
    if not isinstance(password, str) or len(password) < 8:
        raise AuthError("password must be at least 8 characters")
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITERATIONS)


class SecureAgent:
    def __init__(self, agent: TalentAgent | None = None):
        self.core = agent or TalentAgent()
        self._lock = threading.RLock()
        self._credentials: dict[str, tuple[bytes, bytes]] = {}  # principal -> (salt, hash)
        self._sessions: dict[str, tuple[str, float]] = {}  # token -> (principal, expires_at)
        # principal is "owner" or an artist_id

    # -- credentials ----------------------------------------------------
    def create_owner(self, password: str) -> None:
        with self._lock:
            if OWNER in self._credentials:
                raise AuthError("owner credential already exists")
            salt = secrets.token_bytes(16)
            self._credentials[OWNER] = (salt, _hash_password(password, salt))

    def register_artist_password(self, owner_token: str, artist_id: str, password: str) -> None:
        principal = self._require(owner_token)
        if principal != OWNER:
            raise AuthError("only the owner can set an artist password")
        if artist_id not in self.core.artists:
            raise AgentError(f"unknown artist {artist_id!r}")
        with self._lock:
            salt = secrets.token_bytes(16)
            self._credentials[artist_id] = (salt, _hash_password(password, salt))

    def _login(self, principal: str, password: str) -> str:
        cred = self._credentials.get(principal)
        if cred is None:
            raise AuthError("no credential for that principal")
        salt, expected = cred
        got = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITERATIONS)
        if not hmac.compare_digest(got, expected):
            raise AuthError("wrong password")
        token = secrets.token_hex(32)
        with self._lock:
            self._sessions[token] = (principal, time.time() + _TOKEN_TTL_SECONDS)
        return token

    def login_owner(self, password: str) -> str:
        return self._login(OWNER, password)

    def login_artist(self, artist_id: str, password: str) -> str:
        return self._login(artist_id, password)

    def logout(self, token: str) -> None:
        with self._lock:
            self._sessions.pop(token, None)

    def _require(self, token: str) -> str:
        with self._lock:
            entry = self._sessions.get(token)
        if entry is None:
            raise AuthError("invalid or missing session token")
        principal, expires_at = entry
        if time.time() > expires_at:
            with self._lock:
                self._sessions.pop(token, None)
            raise AuthError("session token expired")
        return principal

    def _actor_for(self, token: str, artist_id: str | None = None) -> str:
        """Resolve a token to a core actor string, enforcing isolation."""
        principal = self._require(token)
        if principal == OWNER:
            return OWNER
        # artist principal: may only act as itself
        if artist_id is not None and artist_id != principal:
            raise AuthError(f"artist {principal} may not act for {artist_id}")
        return principal

    # -- read methods (token required, artist-scoped) -------------------
    def find_opportunities(self, token: str, **kwargs):
        self._require(token)
        with self._lock:
            return self.core.find_opportunities(**kwargs)

    def agenda(self, token: str, artist_id: str):
        self._actor_for(token, artist_id)
        with self._lock:
            return self.core.agenda(artist_id)

    def status(self, token: str):
        principal = self._require(token)
        with self._lock:
            s = self.core.status()
        if principal != OWNER:
            # artists get counts, not the whole ledger summary
            s = {"artists": 1, "bookings": len([b for b in self.core.bookings.values() if b.artist_id == principal]),
                 "receipts": len([r for r in self.core.receipts if r["artist_id"] == principal])}
        return s

    # -- mutating methods (locked + authenticated) ----------------------
    def add_artist(self, token: str, *args, **kwargs):
        if self._require(token) != OWNER:
            raise AuthError("only the owner can add an artist")
        with self._lock:
            return self.core.add_artist(*args, **kwargs)

    def set_tier(self, token: str, artist_id: str, tier: str):
        if self._require(token) != OWNER:
            raise AuthError("only the owner can set a tier")
        with self._lock:
            return self.core.set_tier(artist_id, tier)

    def add_opportunity(self, token: str, *args, **kwargs):
        if self._require(token) != OWNER:
            raise AuthError("only the owner can add an opportunity")
        with self._lock:
            return self.core.add_opportunity(*args, **kwargs)

    def offer_booking(self, token: str, artist_id: str, opp_id: str):
        actor = self._actor_for(token, artist_id)
        # owner offers; an artist may offer for itself, core still checks
        with self._lock:
            return self.core.offer_booking(artist_id, opp_id, actor=actor if actor == OWNER else artist_id)

    def approve_booking(self, token: str, booking_id: str):
        booking = self.core.bookings.get(booking_id)
        if booking is None:
            raise AgentError(f"unknown booking {booking_id!r}")
        actor = self._actor_for(token, booking.artist_id)
        if actor != booking.artist_id:
            raise AuthError("only the booked artist can approve")
        with self._lock:
            return self.core.approve_booking(booking_id, actor=booking.artist_id)

    def confirm_booking(self, token: str, booking_id: str):
        booking = self.core.bookings.get(booking_id)
        if booking is None:
            raise AgentError(f"unknown booking {booking_id!r}")
        actor = self._actor_for(token, booking.artist_id)
        with self._lock:
            return self.core.confirm_booking(booking_id, actor=OWNER if actor == OWNER else booking.artist_id)

    def reject_booking(self, token: str, booking_id: str):
        booking = self.core.bookings.get(booking_id)
        if booking is None:
            raise AgentError(f"unknown booking {booking_id!r}")
        actor = self._actor_for(token, booking.artist_id)
        with self._lock:
            return self.core.reject_booking(booking_id, actor=OWNER if actor == OWNER else booking.artist_id)

    def cancel_booking(self, token: str, booking_id: str):
        booking = self.core.bookings.get(booking_id)
        if booking is None:
            raise AgentError(f"unknown booking {booking_id!r}")
        actor = self._actor_for(token, booking.artist_id)
        with self._lock:
            return self.core.cancel_booking(booking_id, actor=OWNER if actor == OWNER else booking.artist_id)

    def complete_booking(self, token: str, booking_id: str):
        booking = self.core.bookings.get(booking_id)
        if booking is None:
            raise AgentError(f"unknown booking {booking_id!r}")
        actor = self._actor_for(token, booking.artist_id)
        with self._lock:
            return self.core.complete_booking(booking_id, actor=OWNER if actor == OWNER else booking.artist_id)

    def collect(self, token: str, booking_id: str, cleared_cents: int, stage: str):
        booking = self.core.bookings.get(booking_id)
        if booking is None:
            raise AgentError(f"unknown booking {booking_id!r}")
        self._actor_for(token, booking.artist_id)
        with self._lock:
            return self.core.collect(booking_id, cleared_cents, stage)

    def pay_cut(self, token: str, receipt: dict, cut_cents: int, payee: str):
        if receipt.get("artist_id"):
            self._actor_for(token, receipt["artist_id"])
        else:
            self._require(token)
        with self._lock:
            return self.core.pay_cut(receipt, cut_cents, payee)

    def send_message(self, token: str, booking_id: str, text: str):
        booking = self.core.bookings.get(booking_id)
        if booking is None:
            raise AgentError(f"unknown booking {booking_id!r}")
        actor = self._actor_for(token, booking.artist_id)
        with self._lock:
            return self.core.send_message(booking_id, OWNER if actor == OWNER else booking.artist_id, text)

    def add_expense(self, token: str, artist_id: str, *args, **kwargs):
        self._actor_for(token, artist_id)
        with self._lock:
            return self.core.add_expense(artist_id, *args, **kwargs)

    def add_release(self, token: str, artist_id: str, *args, **kwargs):
        self._actor_for(token, artist_id)
        with self._lock:
            return self.core.add_release(artist_id, *args, **kwargs)

    def approve_release(self, token: str, release_id: str):
        release = self.core.releases.get(release_id)
        if release is None:
            raise AgentError(f"unknown release {release_id!r}")
        actor = self._actor_for(token, release.artist_id)
        if actor != release.artist_id:
            raise AuthError("only the releasing artist can approve a release")
        with self._lock:
            return self.core.approve_release(release_id, actor=release.artist_id)

    def export_release(self, token: str, release_id: str):
        release = self.core.releases.get(release_id)
        if release is None:
            raise AgentError(f"unknown release {release_id!r}")
        self._actor_for(token, release.artist_id)
        with self._lock:
            return self.core.export_release(release_id)

    def save(self, token: str, path: str):
        if self._require(token) != OWNER:
            raise AuthError("only the owner can save the store")
        with self._lock:
            return self.core.save(path)

    @classmethod
    def load(cls, path: str, key=None):
        # Loading does not authenticate by itself: the caller must
        # create credentials / log in after load, same as a fresh start.
        # The core load already re-validates the whole store.
        return cls(TalentAgent.load(path, key=key))
