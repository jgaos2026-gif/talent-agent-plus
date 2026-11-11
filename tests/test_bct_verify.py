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


if __name__ == "__main__":
    unittest.main()
