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

ROUND 3 — canonical numbers (finding G): non-integral floats are hashed
through `canonical_number` (shared spec with the TypeScript hashOf):
integer-valued floats hash as their exact integer, other finite floats
as shortest-round-trip scientific notation (`1.5` -> `1.5e+0`,
`0.000001` -> `1e-6`). Python `json.dumps` and JS `JSON.stringify`
disagree on floats (`1e-06` vs `0.000001`), so hashing raw dumps made
the two verifiers disagree; the canonical form is the same bytes in
both languages. Non-finite numbers are rejected, never hashed.
Boundary (pinned by the test vectors, reconciled with the TypeScript
side in round 3): the integer form applies when the value is
integer-valued AND its shortest representation has no non-zero
fractional digits — i.e. the mantissa of the shortest repr, trailing
zeros stripped, has no fractional part (`1e21` ->
`"1000000000000000000000"`, `100.0` -> `"100"`). When the shortest
mantissa does carry fractional digits, the value takes scientific
form even if it is integer-valued as a double
(`1.7976931348623157e308` -> `"1.7976931348623157e+308"`). This is a
pure function of the number's shortest digits, so both languages
agree on every value; the earlier 1e21 magnitude boundary is
superseded.

ROUND 3 — naming (finding F): the transition hash is an unkeyed SHA-256
binding — `hashOf({stateHash, priorStateHash})`. It binds a state to its
prior; it authenticates nothing and no name in this module claims
otherwise. (The TypeScript side renamed its braid binding to
`braidBindingHash` in round 3; there is no Python counterpart to
rename — see the round-3 report.)

ROUND 3 — incomplete anchored arguments: `verify_transition` rejects
explicitly when exactly one of `registry`/`identity` is supplied.
Anchored verification needs both; one without the other used to fall
back silently to unanchored verification.

ROUND 3 — registry validation: `BctHeadRegistry.register` (and
`advance`) accept only well-formed `sha256:` + 64-hex heads and
non-empty string identities, and raise `BctVerificationError` on
anything else. Seeding a head stays an operator trust action — the
validation only stops malformed values, it does not make an untrusted
seed trustworthy.

ROUND 3 — ledger cross-check (finding H): `verify_head_against_ledger`
verifies a local head against an evidence ledger through a
ledger-reader interface (list of rows, callable, or object with
`read_entries()`/`entries`). It re-verifies the ledger chain itself,
then requires the head to equal the latest entry's `payload_hash` —
the entry that records that head. This module never talks to Supabase
and takes no credentials; the caller supplies the reader. The apps do
NOT yet record certified heads as evidence entries or call this on
startup — wiring that is operator work; see the function's docstring
for exactly what enabling it takes.

WHY TWO COPIES (shared-package evaluation, round 3): the two Python
verifier copies should eventually be one shared package, but not by
breaking the apps to get there. Both apps deploy separately on Render
from their own repos with `buildCommand: echo "stdlib only - no build
step"`, no requirements file, and `import bct_verify` from the repo
root; both test suites import it the same way. A shared package would
need a third repo plus a `pip install` from a git URL in each Render
build (a new network dependency at deploy time, plus version-pinning
and skew questions: the two apps could silently run different verifier
versions), and a rewrite of every `import bct_verify` in both apps and
their tests. That is a deploy-contract change, not a code move, and it
needs John's call. Until then the copies stay byte-identical mirrors
(MD5-checked after every edit) — that check is what stops them
drifting apart.
"""

from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal

__all__ = [
    "GENESIS",
    "BctVerificationError",
    "BctHeadRegistry",
    "canonical_number",
    "stable_stringify",
    "hash_of",
    "state_hash",
    "compute_transition_hash",
    "verify_transition",
    "verify_ledger_rows",
    "verify_head_against_ledger",
]

GENESIS = "GENESIS"

_SHA256_PREFIX = "sha256:"
_SHA256_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


class BctVerificationError(Exception):
    """A BCT transition or ledger verification failed. Callers must block
    the transition — never proceed past this error."""


# -- canonical hashing (byte-compatible with TS hashOf) ------------------

def _shortest_has_fraction(value):
    """True if the shortest repr of a float carries non-zero fractional
    digits — i.e. the mantissa of repr(value), trailing zeros stripped,
    has a fractional part. This is the shared rule with the TypeScript
    canonicalNumber: the integer form applies only when this is False.
    (`100.0` -> repr `100.0` -> fractional `0` -> stripped empty ->
    False; `1e21` -> repr `1e+21` -> no dot -> False; `1.5` -> `5` ->
    True; `1.7976931348623157e308` -> `7976931348623157` -> True.)"""
    text = repr(value)
    mantissa = text.split("e")[0].split("E")[0]
    if "." not in mantissa:
        return False
    return len(mantissa.split(".", 1)[1].rstrip("0")) > 0


def canonical_number(value):
    """Canonical decimal string for one JSON number — the SHARED spec
    implemented identically by the TypeScript `hashOf` (finding G).

      * ints, and integer-valued floats whose shortest repr has no
        non-zero fractional digits: the exact integer value as a
        decimal string, no decimal point, no exponent (`1.0` -> `"1"`,
        `1e21` -> `"1000000000000000000000"`). Negative zero
        normalizes to `"0"`. When the shortest mantissa does carry
        fractional digits, the value takes scientific form even if it
        is integer-valued as a double (`1.7976931348623157e308` ->
        `"1.7976931348623157e+308"`).
      * non-integral finite floats: scientific notation with the
        shortest round-trip digits — one digit, optional `"."` +
        remaining digits (no trailing zeros), then `"e"`, then a
        signed exponent with an explicit sign and no leading zeros
        (`0.000001` -> `"1e-6"`, `1.5` -> `"1.5e+0"`,
        `123456.789` -> `"1.23456789e+5"`, `0.1` -> `"1e-1"`).
      * non-finite (NaN, Infinity): rejected with ValueError — JSON
        cannot represent them, so they are never hashed.

    Digits come from `repr(value)`, which is the shortest round-trip
    representation in CPython; `Decimal(repr(value))` parses those
    digits exactly (no binary-float noise leaks in). Bools are rejected
    (a bool is an `int` subclass in Python, but it is not a number
    here — `stable_stringify` handles bools before reaching this).
    """
    if isinstance(value, bool):
        raise TypeError("canonical_number takes a number, not a bool")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError("non-finite numbers cannot be hashed")
        if value == 0:
            return "0"
        if value.is_integer() and not _shortest_has_fraction(value):
            return str(int(value))
        dec = Decimal(repr(value))
        digits = "".join(str(d) for d in dec.as_tuple().digits)
        exponent = dec.adjusted()
        sign = "-" if dec.as_tuple().sign else ""
        mantissa = digits if len(digits) == 1 else digits[0] + "." + digits[1:]
        return "%s%se%+d" % (sign, mantissa, exponent)
    raise TypeError("canonical_number takes an int or float, got %r"
                    % type(value).__name__)


def stable_stringify(value):
    """Mirror of TS `stableStringify`: JSON with object keys sorted,
    no spaces, strings JSON-escaped. Non-JSON values are rejected.
    Numbers go through `canonical_number` so the hashed bytes match
    the TypeScript verifier exactly (finding G)."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return canonical_number(value)
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

def _is_well_formed_hash(value):
    """True only for a well-formed head: `sha256:` + 64 lowercase hex
    chars (the exact output shape of `hash_of` / `state_hash`)."""
    return (isinstance(value, str)
            and value.startswith(_SHA256_PREFIX)
            and _SHA256_DIGEST_RE.match(value[len(_SHA256_PREFIX):]) is not None)


class BctHeadRegistry(dict):
    """Authoritative registry of known chain heads, one per identity.

    A dict subclass, so the apps' existing ``_bct_chain_head`` dicts can
    be passed as a registry as-is. The verifier trusts ONLY the head
    recorded here as a transition's parent — never a hash the caller
    merely asserts. Seed with :meth:`register` from a durable record
    you trust; never seed from caller-supplied input.

    Round 3: every write through :meth:`register` or :meth:`advance`
    is validated — the identity must be a non-empty string and the head
    a well-formed `sha256:` hash, or `BctVerificationError` is raised
    and nothing is stored. (Direct item assignment bypasses that check;
    do not seed heads that way.) Validation stops malformed values; it
    does not make an untrusted seed trustworthy.
    """

    def head(self, identity):
        """Current registered head for an identity, or None."""
        return self.get(identity)

    def has(self, identity):
        """True when the identity has a registered head."""
        return identity in self

    def register(self, identity, state_hash):
        """Seed (or re-seed) a head. Operator/caller trust action.
        Raises `BctVerificationError` (and stores nothing) unless the
        identity is a non-empty string and the head is a well-formed
        `sha256:` hash."""
        if not isinstance(identity, str) or identity == "":
            raise BctVerificationError(
                "registry identity must be a non-empty string, got %r"
                % (identity,))
        if not _is_well_formed_hash(state_hash):
            raise BctVerificationError(
                "registry head must be a well-formed 'sha256:' hash "
                "(64 hex chars), got %r" % (state_hash,))
        self[identity] = state_hash

    def advance(self, identity, expected_prior, new_hash):
        """Advance the head after a trusted transition. Returns False
        (and changes nothing) unless the recorded head equals the
        expected prior — a competing branch can never silently
        overwrite the head. Raises `BctVerificationError` (and stores
        nothing) if the new head is not a well-formed `sha256:` hash."""
        if self.get(identity) != expected_prior:
            return False
        if not _is_well_formed_hash(new_hash):
            raise BctVerificationError(
                "registry head must be a well-formed 'sha256:' hash "
                "(64 hex chars), got %r" % (new_hash,))
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

    Anchored verification requires BOTH `registry` and `identity`.
    Supplying exactly one of them is rejected explicitly — it never
    falls back silently to unanchored (chaining-only) verification.
    Supply neither for chaining-only verification.

    Any failure returns (False, reason). Bad input values never raise —
    they fail closed here.
    """
    registry_supplied = registry is not None
    identity_supplied = isinstance(identity, str) and identity != ""
    if registry_supplied != identity_supplied:
        return False, ("Anchored verification requires both a registry "
                       "and an identity — a registry without an identity "
                       "(or an identity without a registry) is rejected; "
                       "supply both for anchored verification, or neither "
                       "for chaining-only verification.")
    anchored = registry_supplied and identity_supplied
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


# -- ledger cross-check (finding H) --------------------------------------

def _read_ledger_entries(ledger_reader):
    """Resolve a ledger reader to a list of row dicts.

    The reader interface — the seam between this module and wherever
    the ledger actually lives — accepts:
      * a list/tuple of row dicts (tests, in-memory stand-ins);
      * a callable returning rows;
      * an object with a `read_entries()` method;
      * an object with an `entries` attribute (a list, or a callable
        returning one).
    Raises TypeError for anything else. This module never opens a
    network connection and takes no credentials: a Supabase-backed
    reader is the caller's adapter, not this module's job.
    """
    if isinstance(ledger_reader, (list, tuple)):
        return list(ledger_reader)
    if callable(ledger_reader):
        return list(ledger_reader())
    read_entries = getattr(ledger_reader, "read_entries", None)
    if callable(read_entries):
        return list(read_entries())
    entries = getattr(ledger_reader, "entries", None)
    if entries is not None:
        return list(entries() if callable(entries) else entries)
    raise TypeError("ledger_reader must be a list of rows, a callable "
                    "returning rows, or an object with read_entries()/entries")


def _ledger_tip(rows):
    """The latest ledger row: highest `seq` when every row carries one,
    otherwise the last row as given."""
    if all(isinstance(r, dict) and isinstance(r.get("seq"), int)
           for r in rows):
        return sorted(rows, key=lambda r: r["seq"])[-1]
    return rows[-1]


def verify_head_against_ledger(head, ledger_reader, flavor="auto"):
    """Cross-check a local chain head against the evidence ledger.
    Returns (ok, reason). Fail closed: a corrupt ledger, an empty
    ledger, a failing reader, or a head that is not the ledger's latest
    recorded head all return (False, reason) — never True.

    What it checks, in order:
      1. the head is a well-formed `sha256:` hash;
      2. the ledger reads, and its chain re-verifies
         (`verify_ledger_rows`) — a tampered ledger anchors nothing;
      3. the head equals the latest entry's `payload_hash` — the entry
         that records that head.

    The contract the ledger side must keep: every certified transition
    is recorded as an evidence entry whose `payload_hash` IS the
    declared head for that transition. Without that, the latest entry
    records some other value and the check (correctly) fails.

    What the operator enables, and how (NOT wired here — mechanism
    only, no live Supabase, no credentials in this module):
      1. Record — on each certified transition, append an evidence
         entry with `payload_hash` = the declared head (TAP:
         `tap_append_evidence`; JGA: the brick1 append). The apps do
         not do this yet.
      2. Read — supply a `ledger_reader` that returns the ledger's
         rows in tap/brick1 shape, e.g. a small adapter that SELECTs
         the evidence table through the app's Supabase store.
      3. Check — call this before trusting a head restored from a
         snapshot (e.g. at startup, after load): on False, do NOT
         trust the head — re-derive it from a trusted source or halt;
         a snapshot file an operator can rewrite is not a trust root
         on its own. (The `verify_ledger_rows` empty-ledger rule does
         NOT apply here: an empty ledger has no recorded head, so it
         cannot anchor anything.)
    """
    if not _is_well_formed_hash(head):
        return False, ("local head is malformed — not a well-formed "
                       "'sha256:' hash; it cannot be anchored to any "
                       "ledger")
    try:
        rows = _read_ledger_entries(ledger_reader)
    except Exception as e:
        return False, "ledger reader failed: %s" % e
    if not rows:
        return False, ("evidence ledger is empty — no recorded head to "
                       "anchor the local head against")
    ok, failures = verify_ledger_rows(rows, flavor=flavor)
    if not ok:
        return False, ("evidence ledger failed verification (%s) — local "
                       "head cannot be trusted" % "; ".join(failures))
    tip = _ledger_tip(rows)
    recorded = tip.get("payload_hash") if isinstance(tip, dict) else None
    if recorded is None:
        return False, ("latest evidence-ledger entry records no head "
                       "(payload_hash missing) — local head cannot be "
                       "anchored")
    if recorded != head:
        return False, ("local head does not match the latest "
                       "evidence-ledger entry — stale, forged, or "
                       "ahead-of-ledger head rejected (ledger tip "
                       "records %r)" % (recorded,))
    return True, ("local head matches the latest evidence-ledger entry — "
                  "anchored to a verified ledger chain")
