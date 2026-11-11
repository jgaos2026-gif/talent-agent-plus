"""BCT transition + ledger verification — shared mirror module.

This file is a MIRROR: the identical content lives in both Talent Agent
Plus and Jay's Graphic Arts Store (repo roots). Keep the two copies
byte-identical; a change in one must be copied to the other.

What it does: the Python mirror of the FIXED BctVerifier layer-5 rules
(sb688-public `src/truth/BctVerifier.ts`, `layerTransaction`):

  * genesis passes (no prior state hash, or prior hash "GENESIS");
  * the prior hash must be a well-formed `sha256:` hash;
  * a no-op transition (new state hash equals prior state hash) is rejected;
  * the transition hash must equal `hashOf({stateHash, priorStateHash})`
    — the proof that the new state follows from that prior state.

One deliberate addition, documented so it cannot drift: `verify_transition`
also checks the layer-3 binding — the declared hash must equal the hash of
the supplied new state. Layer 5 alone cannot do that (a caller could
supply any declared hash with a consistently computed transition hash).
Without the binding, the chaining proves nothing about the actual state.

Hashing is byte-compatible with the TypeScript `hashOf` (stable JSON:
object keys sorted, no spaces, `sha256:` prefix), so a transition hash
computed here verifies against the TypeScript verifier and vice versa.

Ledger verification mirrors the SQL verifiers (`tap_verify_evidence_chain`
and `brick1_verify_evidence_chain`): predecessor linkage plus a
recomputed `entry_hash` over the stored row fields, done here in-process
so the app never trusts a database round-trip blindly.

Standard library only (hashlib, json, re). No network, no Supabase
client, no app imports — importable on its own.

Fail closed everywhere: every failure returns a rejection, never a pass.
`verify_transition` and `verify_ledger_rows` return results; they do not
raise for bad input values. Only wrong argument types raise.

FINDING 15 — prior-state anchoring (mirrors BctVerifier's registry):
knowing two hashes used to be enough to forge a transition, because
`verify_transition` accepted ANY well-formed prior with a matching
transition hash (and transition hashes are just sha256 — anyone can
compute one). Pass the caller's authoritative head registry
(`identity=` + `registry=`, e.g. the app's `_bct_chain_head` dict) and
the rule becomes: a non-genesis prior must EQUAL the registered head
for that identity — forged, stale (old), and competing-branch parents
are rejected; genesis needs `allow_genesis=True` AND no registered head
for the identity; on success the registry advances to the new hash.
Without a registry the function still checks chaining only — it CANNOT
detect a forged/stale/branching parent, and the docstring says so; the
two app gates always pass their registry, so the enforced path is the
anchored one. A registry that lives only in memory is lost on restart:
the gates persist `_bct_chain_head` in the app snapshot (see the
save/load tests), and a head must never be seeded from untrusted input.
"""

from __future__ import annotations

import hashlib
import json
import re

__all__ = [
    "GENESIS",
    "BctVerificationError",
    "BctHeadRegistry",
    "stable_stringify",
    "hash_of",
    "state_hash",
    "compute_transition_hash",
    "verify_transition",
    "verify_ledger_rows",
]

GENESIS = "GENESIS"

_SHA256_PREFIX = "sha256:"
_SHA256_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


class BctVerificationError(Exception):
    """A BCT transition or ledger verification failed. Callers must block
    the transition — never proceed past this error."""


# -- canonical hashing (byte-compatible with TS hashOf) ------------------

def stable_stringify(value):
    """Mirror of TS `stableStringify`: JSON with object keys sorted,
    no spaces, strings JSON-escaped. Non-JSON values are rejected."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError("non-finite numbers cannot be hashed")
        if value.is_integer():
            return str(int(value))
        return json.dumps(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(stable_stringify(v) for v in value) + "]"
    if isinstance(value, dict):
        items = sorted(value.items(), key=lambda kv: str(kv[0]))
        return "{" + ",".join(
            json.dumps(str(k), ensure_ascii=False) + ":" + stable_stringify(v)
            for k, v in items
        ) + "}"
    raise TypeError("value is not JSON-serializable: %r" % type(value).__name__)


def hash_of(value) -> str:
    """`sha256:` + hex(sha256(stable_stringify(value))). Identical output
    to the TypeScript `hashOf` for the same input."""
    return _SHA256_PREFIX + hashlib.sha256(
        stable_stringify(value).encode("utf-8")).hexdigest()


def state_hash(state: dict) -> str:
    """Hash of a canonical state record (e.g. {"booking_id": ..., "state":
    ...}). Raises TypeError for a non-dict state — fail closed upstream."""
    if not isinstance(state, dict):
        raise TypeError("state must be a dict")
    return hash_of(state)


def compute_transition_hash(state_hash_value: str, prior_state_hash: str) -> str:
    """The canonical transition hash: hashOf({stateHash, priorStateHash}).
    A transition is only accepted when the caller supplies this exact
    value — it is the proof the new state follows from that prior state."""
    return hash_of({"stateHash": state_hash_value,
                    "priorStateHash": prior_state_hash})


# -- transition verification (fixed BctVerifier layer 5) -----------------

class BctHeadRegistry(dict):
    """Authoritative registry of known chain heads, one per identity.

    A dict subclass, so the apps' existing ``_bct_chain_head`` dicts can
    be passed as a registry as-is. The verifier trusts ONLY the head
    recorded here as a transition's parent — never a hash the caller
    merely asserts. Seed with :meth:`register` from a durable record
    you trust; never seed from caller-supplied input.
    """

    def head(self, identity):
        """Current registered head for an identity, or None."""
        return self.get(identity)

    def has(self, identity):
        """True when the identity has a registered head."""
        return identity in self

    def register(self, identity, state_hash):
        """Seed (or re-seed) a head. Operator/caller trust action."""
        self[identity] = state_hash

    def advance(self, identity, expected_prior, new_hash):
        """Advance the head after a trusted transition. Returns False
        (and changes nothing) unless the recorded head equals the
        expected prior — a competing branch can never silently
        overwrite the head."""
        if self.get(identity) != expected_prior:
            return False
        self[identity] = new_hash
        return True


def verify_transition(prior_state_hash, new_state, declared_hash,
                      transition_hash, identity=None, registry=None,
                      allow_genesis=False):
    """Verify one state transition. Returns (ok, reason).

    Rules, in order (mirroring fixed `layerTransaction`):
      1. Genesis — prior is None, "" or "GENESIS": the first transition;
         the declared hash must still bind the new state. When a
         registry is supplied (anchored mode), genesis ALSO needs
         `allow_genesis=True` and the identity must have no registered
         head — an identity with a head can never start over.
      2. Prior must be a well-formed `sha256:` hash.
      3. ANCHORED ONLY (registry supplied): the prior must equal the
         registered head for `identity` — a forged, stale, or
         branching parent is rejected. Without a registry this check
         cannot run: chaining is still verified, but the parent is
         NOT anchored — see the module docstring.
      4. Declared hash must equal the hash of the supplied new state
         (layer-3 binding — see module docstring).
      5. No-op rejected — declared hash equal to prior hash is not a
         new state.
      6. Transition hash must equal the expected chained hash.

    On success in anchored mode the registry advances to the declared
    hash, so the next transition must chain from THIS state.

    Any failure returns (False, reason). Bad input values never raise —
    they fail closed here.
    """
    anchored = registry is not None and identity is not None
    try:
        if prior_state_hash is None or prior_state_hash == "" \
                or prior_state_hash == GENESIS:
            if anchored:
                if not allow_genesis:
                    return False, ("Genesis transition requires explicit "
                                   "authorization (allow_genesis) — genesis "
                                   "without it is rejected.")
                if identity in registry:
                    return False, ("Genesis rejected — this identity already "
                                   "has a registered chain head; starting "
                                   "over would fork the chain.")
            if not isinstance(declared_hash, str):
                return False, "declared hash is missing"
            try:
                actual = state_hash(new_state)
            except (TypeError, ValueError) as e:
                return False, "new state is not hashable: %s" % e
            if declared_hash != actual:
                return False, "declared hash does not match the new state"
            if anchored:
                registry[identity] = declared_hash
            return True, "Authorized genesis transition — no prior hash required."
        if not isinstance(prior_state_hash, str) \
                or not prior_state_hash.startswith(_SHA256_PREFIX):
            return False, "Prior state hash is malformed — transaction chain broken."
        if anchored:
            head = registry.get(identity)
            if head is None:
                return False, ("Prior state is not a registered chain head "
                               "for this identity — unanchored parent "
                               "rejected (register the head first).")
            if head != prior_state_hash:
                return False, ("Prior state is not the registered chain head "
                               "— forged, stale, or branching parent "
                               "rejected.")
        if not isinstance(declared_hash, str):
            return False, "declared hash is missing"
        try:
            actual = state_hash(new_state)
        except (TypeError, ValueError) as e:
            return False, "new state is not hashable: %s" % e
        if declared_hash != actual:
            return False, "Hash mismatch — declared hash does not match the new state."
        if declared_hash == prior_state_hash:
            return False, ("No-op transition — new state hash equals prior "
                           "state hash; not a new state.")
        expected = compute_transition_hash(declared_hash, prior_state_hash)
        if transition_hash != expected:
            return False, ("Transition hash does not bind the new state to "
                           "the prior state — unchained transition rejected.")
        if anchored:
            registry[identity] = declared_hash
        return True, "Transition is chained to the registered prior state and changes state."
    except (TypeError, ValueError) as e:
        return False, "invalid transition input: %s" % e


# -- ledger verification (mirror of the SQL chain verifiers) -------------

# entry_hash = hex(sha256((prev or "GENESIS") + "|" + fields...))
#   tap:    correlation_id | artist_id | event_type | payload_hash
#   brick1: correlation_id | order_id | actor | event_type | payload_hash
_LEDGER_FIELDS = {
    "tap": ("correlation_id", "artist_id", "event_type", "payload_hash"),
    "brick1": ("correlation_id", "order_id", "actor", "event_type",
               "payload_hash"),
}
# Fields that may be NULL in the database (rendered as "" in the hash).
_LEDGER_NULLABLE = {"artist_id", "order_id", "predecessor_hash"}


def _ledger_entry_hash(flavor: str, row: dict, prev):
    """Recompute one row's entry_hash exactly as the SQL verifiers do.
    Returns the hex digest, or None if a required field is unusable."""
    middle = []
    for field in _LEDGER_FIELDS[flavor]:
        value = row.get(field)
        if value is None:
            if field in _LEDGER_NULLABLE:
                middle.append("")
            else:
                return None
        elif isinstance(value, str):
            middle.append(value)
        else:
            return None
    joined = (prev if prev is not None else GENESIS) + "|" + "|".join(middle)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def verify_ledger_rows(rows, flavor: str = "auto"):
    """Re-verify hash-chained ledger rows in-process. Returns
    (ok, failures), where failures is a list of human-readable problems
    (one per bad row). Empty input is valid (an empty chain verifies,
    matching the SQL verifiers). Never raises for bad row content —
    bad rows are reported as failures (fail closed).

    `flavor` is "tap", "brick1", or "auto" (detected from the first
    row: a row with an "actor" key is brick1, one with "artist_id" is
    tap). Rows are checked in `seq` order when every row carries one.
    """
    if not isinstance(rows, (list, tuple)):
        raise TypeError("rows must be a list of dicts")
    if flavor not in ("auto", "tap", "brick1"):
        raise ValueError("unknown ledger flavor: %r" % (flavor,))
    rows = list(rows)
    if not rows:
        return True, []
    if flavor == "auto":
        first = rows[0]
        if not isinstance(first, dict):
            return False, ["row 0: not a dict"]
        if "actor" in first:
            flavor = "brick1"
        elif "artist_id" in first:
            flavor = "tap"
        else:
            return False, ["row 0: cannot detect ledger flavor "
                           "(need 'actor' or 'artist_id')"]
    ordered = rows
    if all(isinstance(r, dict) and isinstance(r.get("seq"), int)
           for r in rows):
        ordered = sorted(rows, key=lambda r: r["seq"])
    failures = []
    expected_prev = None
    for index, row in enumerate(ordered):
        label = "row %s" % (row.get("seq", index)
                            if isinstance(row, dict) else index)
        if not isinstance(row, dict):
            failures.append("%s: not a dict" % label)
            continue
        predecessor = row.get("predecessor_hash")
        if predecessor is not None and not isinstance(predecessor, str):
            failures.append("%s: predecessor_hash is not a string" % label)
            continue
        if predecessor != expected_prev:
            failures.append(
                "%s: predecessor linkage broken (expected %r, found %r)"
                % (label, expected_prev, predecessor))
            expected_prev = row.get("entry_hash") \
                if isinstance(row.get("entry_hash"), str) else expected_prev
            continue
        entry_hash = row.get("entry_hash")
        if not isinstance(entry_hash, str):
            failures.append("%s: entry_hash is missing" % label)
            continue
        expected = _ledger_entry_hash(flavor, row, expected_prev)
        if expected is None:
            failures.append("%s: required field missing or not a string"
                            % label)
            continue
        if expected != entry_hash:
            failures.append("%s: entry_hash mismatch — row tampered with"
                            % label)
            expected_prev = entry_hash
            continue
        expected_prev = entry_hash
    return (len(failures) == 0), failures
