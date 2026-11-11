"""BCT verification tests — stdlib only, no network.

Covers the shared bct_verify.py mirror module (transition rules copied
from the fixed BctVerifier layer 5, ledger rules copied from the SQL
chain verifiers) and the booking-transition gate wired into
TalentAgent (milestone 2).
"""
import hashlib
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bct_verify  # noqa: E402
from talent_agent_plus import AgentError, TalentAgent  # noqa: E402

# Pinned cross-language values: computed from the TypeScript hashOf
# (sb688-public src/utils/hash.ts) via node — the Python hashes must
# match byte-for-byte, or the two verifiers disagree.
PIN_TRANSITION_AB = ("sha256:f7450415ebf814f2f542858bcfc415ec3eaf9910d72d1b0"
                     "bb350d03dcbb78b3c")
PIN_STATE_BOOKING = ("sha256:34a137ce51694b7c3301074c32e4f2316d16f283620a5"
                     "25b36f764aaa2e5835c")
PIN_STATE_ORDER = ("sha256:2abd625e4a19a63f87b24f2d0141a08c4e4a960ee49a0"
                   "791d3f0724c1ae7f13a")
PIN_TRANSITION_GENESIS = ("sha256:60442b2495bf9be493656d4d7a90ca71168a2ba5a5"
                          "e04df8dc58d51e2f9e36bc")


def _tap_row(seq, prev, correlation_id="11111111-1111-1111-1111-111111111111",
             artist_id="22222222-2222-2222-2222-222222222222",
             event_type="BOOKING_APPROVED", payload_hash="ab" * 32):
    """Build a tap ledger row the way tap.append_evidence does."""
    entry = hashlib.sha256(
        ((prev or "GENESIS") + "|" + correlation_id + "|"
         + (artist_id or "") + "|" + event_type + "|"
         + payload_hash).encode("utf-8")).hexdigest()
    return {"seq": seq, "correlation_id": correlation_id,
            "artist_id": artist_id, "event_type": event_type,
            "payload_hash": payload_hash, "predecessor_hash": prev,
            "entry_hash": entry}


def _brick1_row(seq, prev, correlation_id="33333333-3333-3333-3333-333333333333",
                order_id="44444444-4444-4444-4444-444444444444",
                actor="owner", event_type="STATE_TRANSITION",
                payload_hash="cd" * 32):
    """Build a brick1 ledger row the way brick1.append_evidence does."""
    entry = hashlib.sha256(
        ((prev or "GENESIS") + "|" + correlation_id + "|"
         + (order_id or "") + "|" + actor + "|" + event_type + "|"
         + payload_hash).encode("utf-8")).hexdigest()
    return {"seq": seq, "correlation_id": correlation_id,
            "order_id": order_id, "actor": actor,
            "event_type": event_type, "payload_hash": payload_hash,
            "predecessor_hash": prev, "entry_hash": entry}


def _booked_agent():
    agent = TalentAgent(key="test-key")
    artist = agent.add_artist("Test Artist", skills=["music"],
                              location="Peoria", tier="pro")
    opp = agent.add_opportunity("Peoria Gig", kind="gig", skills=["music"],
                                location="Peoria", date="2026-11-20",
                                pay_cents=100000)
    booking = agent.offer_booking(artist.artist_id, opp.opp_id)
    return agent, artist, booking


class HashCompatibilityTests(unittest.TestCase):
    def test_transition_hash_matches_typescript(self):
        got = bct_verify.compute_transition_hash(
            "sha256:" + "a" * 64, "sha256:" + "b" * 64)
        self.assertEqual(got, PIN_TRANSITION_AB)

    def test_state_hash_matches_typescript(self):
        self.assertEqual(
            bct_verify.state_hash({"booking_id": "booking-0001",
                                   "state": "APPROVED"}),
            PIN_STATE_BOOKING)
        self.assertEqual(
            bct_verify.state_hash({"order_id": "order-0001",
                                   "state": "IN_DESIGN"}),
            PIN_STATE_ORDER)

    def test_genesis_transition_hash_matches_typescript(self):
        got = bct_verify.compute_transition_hash(
            "sha256:" + "c" * 64, "GENESIS")
        self.assertEqual(got, PIN_TRANSITION_GENESIS)


class TransitionTests(unittest.TestCase):
    def _chain(self):
        s1 = {"booking_id": "booking-0001", "state": "OFFERED"}
        d1 = bct_verify.state_hash(s1)
        t1 = bct_verify.compute_transition_hash(d1, "GENESIS")
        s2 = {"booking_id": "booking-0001", "state": "APPROVED"}
        d2 = bct_verify.state_hash(s2)
        t2 = bct_verify.compute_transition_hash(d2, d1)
        return s1, d1, t1, s2, d2, t2

    def test_genesis_first_entry_passes(self):
        s1, d1, t1, _, _, _ = self._chain()
        ok, _ = bct_verify.verify_transition("GENESIS", s1, d1, t1)
        self.assertTrue(ok)
        ok, _ = bct_verify.verify_transition(None, s1, d1, None)
        self.assertTrue(ok)

    def test_valid_chained_transition_passes(self):
        s1, d1, _, s2, d2, t2 = self._chain()
        ok, reason = bct_verify.verify_transition(d1, s2, d2, t2)
        self.assertTrue(ok, reason)

    def test_noop_transition_rejected(self):
        s1, d1, t1, _, _, _ = self._chain()
        ok, reason = bct_verify.verify_transition(d1, s1, d1, t1)
        self.assertFalse(ok)
        self.assertIn("No-op", reason)

    def test_wrong_transition_hash_rejected(self):
        _, d1, _, s2, d2, _ = self._chain()
        ok, reason = bct_verify.verify_transition(
            d1, s2, d2, "sha256:" + "0" * 64)
        self.assertFalse(ok)
        self.assertIn("unchained", reason)

    def test_missing_transition_hash_rejected(self):
        _, d1, _, s2, d2, _ = self._chain()
        ok, _ = bct_verify.verify_transition(d1, s2, d2, None)
        self.assertFalse(ok)

    def test_malformed_prior_hash_rejected(self):
        _, _, _, s2, d2, t2 = self._chain()
        ok, reason = bct_verify.verify_transition("not-a-hash", s2, d2, t2)
        self.assertFalse(ok)
        self.assertIn("malformed", reason)

    def test_declared_hash_must_bind_the_state(self):
        _, d1, _, s2, d2, t2 = self._chain()
        # Declared hash is d1 (the prior state), not the hash of s2.
        ok, reason = bct_verify.verify_transition(d1, s2, d1, t2)
        self.assertFalse(ok)
        self.assertIn("does not match", reason)


class RegistryTests(unittest.TestCase):
    """Finding 15: prior-state anchoring via BctHeadRegistry.

    Knowing two hashes is no longer enough to forge a transition: the
    prior must be the registered head for the identity.
    """

    def _states(self):
        s1 = {"booking_id": "booking-0001", "state": "OFFERED"}
        d1 = bct_verify.state_hash(s1)
        s2 = {"booking_id": "booking-0001", "state": "APPROVED"}
        d2 = bct_verify.state_hash(s2)
        s3 = {"booking_id": "booking-0001", "state": "COMPLETED"}
        d3 = bct_verify.state_hash(s3)
        return s1, d1, s2, d2, s3, d3

    def test_forged_parent_rejected(self):
        _, d1, s2, d2, _, _ = self._states()
        reg = bct_verify.BctHeadRegistry()
        t2 = bct_verify.compute_transition_hash(d2, d1)
        ok, reason = bct_verify.verify_transition(
            d1, s2, d2, t2, identity="booking-0001", registry=reg)
        self.assertFalse(ok)
        self.assertIn("registered chain head", reason)

    def test_unauthorized_genesis_rejected(self):
        s1, d1, _, _, _, _ = self._states()
        reg = bct_verify.BctHeadRegistry()
        t1 = bct_verify.compute_transition_hash(d1, "GENESIS")
        ok, reason = bct_verify.verify_transition(
            "GENESIS", s1, d1, t1, identity="booking-0001", registry=reg)
        self.assertFalse(ok)
        self.assertIn("allow_genesis", reason)

    def test_authorized_genesis_advances_registry_then_second_genesis_rejected(self):
        s1, d1, s2, d2, _, _ = self._states()
        reg = bct_verify.BctHeadRegistry()
        t1 = bct_verify.compute_transition_hash(d1, "GENESIS")
        ok, reason = bct_verify.verify_transition(
            "GENESIS", s1, d1, t1, identity="booking-0001", registry=reg,
            allow_genesis=True)
        self.assertTrue(ok, reason)
        self.assertEqual(reg["booking-0001"], d1)
        t2 = bct_verify.compute_transition_hash(d2, "GENESIS")
        ok, _ = bct_verify.verify_transition(
            "GENESIS", s2, d2, t2, identity="booking-0001", registry=reg,
            allow_genesis=True)
        self.assertFalse(ok)

    def test_old_parent_rejected_after_head_moves_on(self):
        _, d1, s2, d2, s3, d3 = self._states()
        reg = bct_verify.BctHeadRegistry()
        reg.register("booking-0001", d1)
        t2 = bct_verify.compute_transition_hash(d2, d1)
        ok, reason = bct_verify.verify_transition(
            d1, s2, d2, t2, identity="booking-0001", registry=reg)
        self.assertTrue(ok, reason)
        self.assertEqual(reg["booking-0001"], d2)
        t3 = bct_verify.compute_transition_hash(d3, d1)
        ok, reason = bct_verify.verify_transition(
            d1, s3, d3, t3, identity="booking-0001", registry=reg)
        self.assertFalse(ok)
        self.assertIn("registered chain head", reason)

    def test_competing_branch_rejected(self):
        _, d1, s2, d2, s3, d3 = self._states()
        reg = bct_verify.BctHeadRegistry()
        reg.register("booking-0001", d1)
        t2 = bct_verify.compute_transition_hash(d2, d1)
        ok, _ = bct_verify.verify_transition(
            d1, s2, d2, t2, identity="booking-0001", registry=reg)
        self.assertTrue(ok)
        # A different state claiming the same parent is a competing branch.
        t3 = bct_verify.compute_transition_hash(d3, d1)
        ok, _ = bct_verify.verify_transition(
            d1, s3, d3, t3, identity="booking-0001", registry=reg)
        self.assertFalse(ok)

    def test_sequential_chain_verifies_and_advances(self):
        s1, d1, s2, d2, s3, d3 = self._states()
        reg = bct_verify.BctHeadRegistry()
        t1 = bct_verify.compute_transition_hash(d1, "GENESIS")
        ok, reason = bct_verify.verify_transition(
            "GENESIS", s1, d1, t1, identity="booking-0001", registry=reg,
            allow_genesis=True)
        self.assertTrue(ok, reason)
        t2 = bct_verify.compute_transition_hash(d2, d1)
        ok, reason = bct_verify.verify_transition(
            d1, s2, d2, t2, identity="booking-0001", registry=reg)
        self.assertTrue(ok, reason)
        t3 = bct_verify.compute_transition_hash(d3, d2)
        ok, reason = bct_verify.verify_transition(
            d2, s3, d3, t3, identity="booking-0001", registry=reg)
        self.assertTrue(ok, reason)
        self.assertEqual(reg["booking-0001"], d3)

    def test_plain_dict_registry_works_like_the_app_gates_use_it(self):
        # The gates pass their plain _bct_chain_head dict as the registry.
        s1, d1, s2, d2, _, _ = self._states()
        reg = {}
        t1 = bct_verify.compute_transition_hash(d1, "GENESIS")
        ok, reason = bct_verify.verify_transition(
            "GENESIS", s1, d1, t1, identity="booking-0001", registry=reg,
            allow_genesis=True)
        self.assertTrue(ok, reason)
        t2 = bct_verify.compute_transition_hash(d2, d1)
        ok, reason = bct_verify.verify_transition(
            d1, s2, d2, t2, identity="booking-0001", registry=reg)
        self.assertTrue(ok, reason)
        self.assertEqual(reg["booking-0001"], d2)


class LedgerTests(unittest.TestCase):
    def _tap_chain(self, n=3):
        rows, prev = [], None
        for seq in range(1, n + 1):
            row = _tap_row(seq, prev)
            rows.append(row)
            prev = row["entry_hash"]
        return rows

    def test_valid_tap_chain_passes(self):
        ok, failures = bct_verify.verify_ledger_rows(self._tap_chain())
        self.assertTrue(ok, failures)

    def test_tampered_entry_hash_detected(self):
        rows = self._tap_chain()
        rows[1]["entry_hash"] = "0" * 64
        ok, failures = bct_verify.verify_ledger_rows(rows)
        self.assertFalse(ok)
        self.assertTrue(any("mismatch" in f for f in failures))

    def test_broken_predecessor_linkage_detected(self):
        rows = self._tap_chain()
        rows[2]["predecessor_hash"] = "0" * 64
        ok, failures = bct_verify.verify_ledger_rows(rows)
        self.assertFalse(ok)
        self.assertTrue(any("linkage" in f for f in failures))

    def test_deleted_row_detected(self):
        rows = self._tap_chain()
        del rows[1]
        ok, _ = bct_verify.verify_ledger_rows(rows)
        self.assertFalse(ok)

    def test_empty_ledger_passes(self):
        ok, failures = bct_verify.verify_ledger_rows([])
        self.assertTrue(ok, failures)

    def test_brick1_flavor_passes_and_tamper_detected(self):
        rows, prev = [], None
        for seq in range(1, 3):
            row = _brick1_row(seq, prev)
            rows.append(row)
            prev = row["entry_hash"]
        ok, failures = bct_verify.verify_ledger_rows(rows, flavor="brick1")
        self.assertTrue(ok, failures)
        rows[0]["actor"] = "someone-else"
        ok, _ = bct_verify.verify_ledger_rows(rows, flavor="brick1")
        self.assertFalse(ok)

    def test_auto_flavor_detection(self):
        ok, _ = bct_verify.verify_ledger_rows(self._tap_chain())
        self.assertTrue(ok)
        rows, prev = [], None
        for seq in range(1, 3):
            row = _brick1_row(seq, prev)
            rows.append(row)
            prev = row["entry_hash"]
        ok, _ = bct_verify.verify_ledger_rows(rows)
        self.assertTrue(ok)


class BookingGateTests(unittest.TestCase):
    def test_full_lifecycle_is_bct_certified(self):
        agent, artist, booking = _booked_agent()
        bid = booking.booking_id
        self.assertEqual(booking.state, "OFFERED")
        agent.approve_booking(bid, actor=artist.artist_id)
        agent.confirm_booking(bid)
        agent.complete_booking(bid)
        self.assertEqual(agent.bookings[bid].state, "COMPLETED")
        self.assertEqual(agent._bct_chain_head[bid], bct_verify.state_hash(
            {"booking_id": bid, "state": "COMPLETED"}))
        self.assertEqual(agent._bct_rejections, [])

    def test_reject_and_cancel_paths_are_certified(self):
        agent, artist, booking = _booked_agent()
        agent.reject_booking(booking.booking_id, actor=artist.artist_id)
        self.assertEqual(agent.bookings[booking.booking_id].state, "REJECTED")
        agent2, artist2, booking2 = _booked_agent()
        agent2.approve_booking(booking2.booking_id, actor=artist2.artist_id)
        agent2.confirm_booking(booking2.booking_id)
        agent2.cancel_booking(booking2.booking_id)
        self.assertEqual(agent2.bookings[booking2.booking_id].state,
                         "CANCELLED")
        self.assertEqual(agent2._bct_rejections, [])

    def test_malformed_chain_head_blocks_transition(self):
        agent, artist, booking = _booked_agent()
        bid = booking.booking_id
        agent._bct_chain_head[bid] = "not-a-hash"
        with self.assertRaises(AgentError) as ctx:
            agent.approve_booking(bid, actor=artist.artist_id)
        self.assertIn("BCT verification failed", str(ctx.exception))
        self.assertEqual(agent.bookings[bid].state, "OFFERED")
        self.assertEqual(len(agent._bct_rejections), 1)
        self.assertEqual(agent._bct_rejections[0]["to_state"], "APPROVED")

    def test_chain_head_survives_save_load(self):
        agent, artist, booking = _booked_agent()
        bid = booking.booking_id
        agent.approve_booking(bid, actor=artist.artist_id)
        head_before = dict(agent._bct_chain_head)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "store.json")
            agent.save(path)
            loaded = TalentAgent.load(path, key="test-key")
        self.assertEqual(loaded._bct_chain_head, head_before)
        loaded.confirm_booking(bid)
        loaded.complete_booking(bid)
        self.assertEqual(loaded.bookings[bid].state, "COMPLETED")
        self.assertEqual(loaded._bct_rejections, [])


class CanonicalNumberTests(unittest.TestCase):
    """Finding G: shared canonical numeric representation.

    The pinned vectors are the cross-language contract with the
    TypeScript hashOf — the Python and TS outputs must match these
    strings exactly. Integers and integer-valued floats whose
    shortest repr has no non-zero fractional digits hash as their
    exact integer; everything else finite takes shortest-round-trip
    scientific notation.
    """

    VECTORS = [
        (0.000001, "1e-6"),
        (0.0000001, "1e-7"),
        (1.5, "1.5e+0"),
        (123456.789, "1.23456789e+5"),
        (0.1, "1e-1"),
        (-0.0, "0"),
        (1.0, "1"),
        # Reconciled with the TS side in round 3: the integer form
        # applies when the shortest mantissa has no non-zero
        # fractional digits, so 1e21 hashes as its exact integer.
        (1e21, "1000000000000000000000"),
        (1e22, "10000000000000000000000"),
        (2e21, "2000000000000000000000"),
        (1.7976931348623157e308, "1.7976931348623157e+308"),
        (7, "7"),
        (-3, "-3"),
        (2.0, "2"),
        (100.5, "1.005e+2"),
        (5e-324, "5e-324"),
        (-1.5, "-1.5e+0"),
        (1e20, "100000000000000000000"),
    ]

    def test_pinned_vectors(self):
        for value, expected in self.VECTORS:
            self.assertEqual(bct_verify.canonical_number(value), expected,
                             "canonical_number(%r)" % (value,))

    def test_stable_stringify_routes_floats_through_canonical_numbers(self):
        self.assertEqual(bct_verify.stable_stringify(0.000001), "1e-6")
        self.assertEqual(bct_verify.stable_stringify(1.5), "1.5e+0")
        self.assertEqual(
            bct_verify.stable_stringify({"amount": 0.000001,
                                         "label": "café"}),
            '{"amount":1e-6,"label":"café"}')
        self.assertEqual(bct_verify.stable_stringify([0.1, 1e21]),
                         "[1e-1,1000000000000000000000]")

    def test_non_finite_numbers_are_rejected(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.assertRaises(ValueError):
                bct_verify.canonical_number(bad)
            with self.assertRaises(ValueError):
                bct_verify.stable_stringify(bad)
            with self.assertRaises(ValueError):
                bct_verify.hash_of({"x": bad})

    def test_unicode_strings_unescaped_escapes_kept(self):
        self.assertEqual(bct_verify.stable_stringify("café"), '"café"')
        self.assertEqual(bct_verify.stable_stringify("braid→knot"),
                         '"braid→knot"')
        self.assertEqual(bct_verify.stable_stringify('a\nb\tc"d'),
                         '"a\\nb\\tc\\"d"')

    def test_canonical_hash_bytes_are_pinned(self):
        # The hashed bytes for a float-bearing state are exactly the
        # canonical JSON — pin them, not just the number rendering.
        expected = hashlib.sha256(
            '{"amount":1e-6}'.encode("utf-8")).hexdigest()
        self.assertEqual(bct_verify.hash_of({"amount": 0.000001}),
                         "sha256:" + expected)

    def test_bool_is_not_a_number(self):
        with self.assertRaises(TypeError):
            bct_verify.canonical_number(True)
        # ... but stable_stringify still renders bools as JSON bools.
        self.assertEqual(bct_verify.stable_stringify(True), "true")


class IncompleteAnchoredArgsTests(unittest.TestCase):
    """verify_transition() rejects incomplete anchored arguments.

    Anchored verification needs BOTH a registry and an identity. One
    without the other used to fall back silently to unanchored
    (chaining-only) verification — a valid-looking pass with no
    anchoring at all.
    """

    def _valid_chain(self):
        s1 = {"booking_id": "booking-0001", "state": "OFFERED"}
        d1 = bct_verify.state_hash(s1)
        s2 = {"booking_id": "booking-0001", "state": "APPROVED"}
        d2 = bct_verify.state_hash(s2)
        t2 = bct_verify.compute_transition_hash(d2, d1)
        return s1, d1, s2, d2, t2

    def test_registry_without_identity_rejected(self):
        _, d1, s2, d2, t2 = self._valid_chain()
        ok, reason = bct_verify.verify_transition(
            d1, s2, d2, t2, registry={})
        self.assertFalse(ok)
        self.assertIn("both", reason)

    def test_identity_without_registry_rejected(self):
        _, d1, s2, d2, t2 = self._valid_chain()
        ok, reason = bct_verify.verify_transition(
            d1, s2, d2, t2, identity="booking-0001")
        self.assertFalse(ok)
        self.assertIn("both", reason)

    def test_empty_string_identity_with_registry_rejected(self):
        _, d1, s2, d2, t2 = self._valid_chain()
        ok, _ = bct_verify.verify_transition(
            d1, s2, d2, t2, identity="", registry={})
        self.assertFalse(ok)

    def test_genesis_with_registry_but_no_identity_rejected(self):
        s1, d1, _, _, _ = self._valid_chain()
        t1 = bct_verify.compute_transition_hash(d1, "GENESIS")
        ok, _ = bct_verify.verify_transition(
            "GENESIS", s1, d1, t1, registry={})
        self.assertFalse(ok)

    def test_both_supplied_still_anchors(self):
        # Both supplied: the anchored rule still applies — an empty
        # registry has no head for the identity, so this is rejected
        # as an unregistered parent, not as incomplete arguments.
        _, d1, s2, d2, t2 = self._valid_chain()
        ok, reason = bct_verify.verify_transition(
            d1, s2, d2, t2, identity="booking-0001", registry={})
        self.assertFalse(ok)
        self.assertIn("registered chain head", reason)

    def test_neither_supplied_still_chaining_only(self):
        _, d1, s2, d2, t2 = self._valid_chain()
        ok, reason = bct_verify.verify_transition(d1, s2, d2, t2)
        self.assertTrue(ok, reason)


class RegistryValidationTests(unittest.TestCase):
    """BctHeadRegistry.register() validates heads (round 3 mirror)."""

    GOOD = "sha256:" + "a" * 64

    def test_valid_head_registers(self):
        reg = bct_verify.BctHeadRegistry()
        reg.register("booking-0001", self.GOOD)
        self.assertEqual(reg.head("booking-0001"), self.GOOD)

    def test_malformed_heads_rejected_and_nothing_stored(self):
        bad_heads = ["not-a-hash", "", "sha256:", "sha256:" + "a" * 63,
                     "sha256:" + "a" * 65, "sha256:" + "g" * 64,
                     "sha256:" + "A" * 64, "sha256:" + "a" * 64 + " ",
                     None, 123, b"sha256:" + b"a" * 64]
        for bad in bad_heads:
            reg = bct_verify.BctHeadRegistry()
            with self.assertRaises(bct_verify.BctVerificationError,
                                   msg="register(%r)" % (bad,)):
                reg.register("booking-0001", bad)
            self.assertEqual(dict(reg), {})

    def test_bad_identities_rejected(self):
        for bad in ["", None, 123]:
            reg = bct_verify.BctHeadRegistry()
            with self.assertRaises(bct_verify.BctVerificationError):
                reg.register(bad, self.GOOD)
            self.assertEqual(dict(reg), {})

    def test_advance_rejects_malformed_new_head(self):
        reg = bct_verify.BctHeadRegistry()
        reg.register("booking-0001", self.GOOD)
        with self.assertRaises(bct_verify.BctVerificationError):
            reg.advance("booking-0001", self.GOOD, "garbage")
        self.assertEqual(reg.head("booking-0001"), self.GOOD)
        # Non-matching prior still returns False without storing.
        self.assertFalse(reg.advance("booking-0001", "sha256:" + "b" * 64,
                                     self.GOOD))
        self.assertEqual(reg.head("booking-0001"), self.GOOD)


class LedgerCrossCheckTests(unittest.TestCase):
    """Finding H: verify_head_against_ledger (local stand-in ledger).

    The stand-in is an in-memory list of tap/brick1-shaped rows whose
    payload_hash carries the certified head at each step — the contract
    the ledger side must keep for the cross-check to mean anything.
    """

    def _head_chain(self):
        heads = []
        for state in ("OFFERED", "APPROVED", "COMPLETED"):
            heads.append(bct_verify.state_hash(
                {"booking_id": "booking-0001", "state": state}))
        return heads

    def _ledger(self, heads):
        rows, prev = [], None
        for seq, head in enumerate(heads, start=1):
            row = _tap_row(seq, prev, payload_hash=head)
            rows.append(row)
            prev = row["entry_hash"]
        return rows

    def test_matching_head_anchors(self):
        heads = self._head_chain()
        ok, reason = bct_verify.verify_head_against_ledger(
            heads[-1], self._ledger(heads))
        self.assertTrue(ok, reason)

    def test_stale_head_rejected(self):
        heads = self._head_chain()
        ok, reason = bct_verify.verify_head_against_ledger(
            heads[0], self._ledger(heads))
        self.assertFalse(ok)
        self.assertIn("does not match", reason)

    def test_forged_head_rejected(self):
        heads = self._head_chain()
        ok, _ = bct_verify.verify_head_against_ledger(
            "sha256:" + "f" * 64, self._ledger(heads))
        self.assertFalse(ok)

    def test_tampered_ledger_rejected(self):
        heads = self._head_chain()
        rows = self._ledger(heads)
        rows[1]["entry_hash"] = "0" * 64
        ok, reason = bct_verify.verify_head_against_ledger(heads[-1], rows)
        self.assertFalse(ok)
        self.assertIn("failed verification", reason)

    def test_empty_ledger_rejected(self):
        heads = self._head_chain()
        ok, _ = bct_verify.verify_head_against_ledger(heads[-1], [])
        self.assertFalse(ok)

    def test_malformed_head_rejected(self):
        heads = self._head_chain()
        ok, _ = bct_verify.verify_head_against_ledger(
            "not-a-hash", self._ledger(heads))
        self.assertFalse(ok)

    def test_reader_forms(self):
        heads = self._head_chain()
        rows = self._ledger(heads)

        class CallableReader(object):
            """Object with a read_entries() method."""
            def __init__(self, rows):
                self._rows = rows

            def read_entries(self):
                return list(self._rows)

        class AttrReader(object):
            """Object with an entries attribute."""
            def __init__(self, rows):
                self.entries = list(rows)

        for reader in (lambda: list(rows), CallableReader(rows),
                       AttrReader(rows)):
            ok, reason = bct_verify.verify_head_against_ledger(
                heads[-1], reader)
            self.assertTrue(ok, reason)

    def test_failing_reader_fails_closed(self):
        def boom():
            raise RuntimeError("storage down")

        heads = self._head_chain()
        ok, reason = bct_verify.verify_head_against_ledger(heads[-1], boom)
        self.assertFalse(ok)
        self.assertIn("reader failed", reason)

    def test_brick1_flavor_ledger(self):
        heads = self._head_chain()
        rows, prev = [], None
        for seq, head in enumerate(heads, start=1):
            row = _brick1_row(seq, prev, payload_hash=head)
            rows.append(row)
            prev = row["entry_hash"]
        ok, reason = bct_verify.verify_head_against_ledger(
            heads[-1], rows, flavor="brick1")
        self.assertTrue(ok, reason)

    def test_out_of_order_rows_still_anchor_on_latest_seq(self):
        heads = self._head_chain()
        rows = self._ledger(heads)
        ok, reason = bct_verify.verify_head_against_ledger(
            heads[-1], list(reversed(rows)))
        self.assertTrue(ok, reason)


class NoFalseSignatureNamingTests(unittest.TestCase):
    """Finding F (mirror): no Python name claims a hash authenticates.

    The braid binding on the TypeScript side is an unkeyed SHA-256
    hash, not a signature — and the Python mirror has no counterpart
    name at all: nothing in bct_verify.py or the app gate module may
    call any hash a signature. Pin that, so a future rename (or a
    careless comment) cannot reintroduce the claim.
    """

    def test_no_signature_naming_in_verifier_or_gate(self):
        import talent_agent_plus
        for path in (bct_verify.__file__, talent_agent_plus.__file__):
            with open(path, encoding="utf-8") as f:
                source = f.read()
            self.assertNotIn("signature", source.lower(), path)


if __name__ == "__main__":
    unittest.main()
