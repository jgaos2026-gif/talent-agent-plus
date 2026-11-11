"""Reflex install tests for Talent Agent Plus — SIMULATION ONLY / RESEARCH ONLY.

Checks the local reflex_sim.py copy beside talent_agent_plus.py and its
narrow wiring: the gate_text() failure path on send_message() and the
store-load ledger fault path (hash mismatch). This repo has NO
conversational AVA brain; the reflex here is gate/ledger fault
containment only. No medical claims are tested here: simulation
behaviour only (detection, isolation, safe-state fallback, escalation,
deterministic replay, reflex never certifies).
"""
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import talent_agent_plus as tap  # noqa: E402
from talent_agent_plus import AgentError, TalentAgent  # noqa: E402

REF = Path.home() / "workspace" / "your_files" / "ava-reflex-sim" / "reflex_sim.py"


def _booked_agent():
    agent = TalentAgent(key="test-key")
    artist = agent.add_artist("Test Artist", skills=["music"], location="Peoria", tier="pro")
    opp = agent.add_opportunity("Peoria Gig", kind="gig", skills=["music"],
                                location="Peoria", date="2026-11-20", pay_cents=100000)
    booking = agent.offer_booking(artist.artist_id, opp.opp_id)
    agent.approve_booking(booking.booking_id, actor=artist.artist_id)
    agent.confirm_booking(booking.booking_id)
    return agent, artist, booking


class ReflexInstallTests(unittest.TestCase):
    def test_local_copy_is_byte_identical_to_reference(self):
        local = Path(tap.__file__).parent / "reflex_sim.py"
        self.assertTrue(local.exists())
        # The reference is an external workstation path and is not
        # part of a normal GitHub Actions checkout. Compare it only when
        # an operator explicitly provides that file in the test environment.
        if not REF.exists():
            self.skipTest("external reference file is not present in this CI environment")
        self.assertEqual(hashlib.sha256(local.read_bytes()).hexdigest(),
                         hashlib.sha256(REF.read_bytes()).hexdigest())

    def test_probe_is_deterministic_and_honest(self):
        a, b = tap.reflex_probe(), tap.reflex_probe()
        self.assertEqual(a, b)
        self.assertTrue(a["available"])
        c = a["campaign"]
        self.assertGreater(c["faults_injected"], 0)
        self.assertEqual(c["faults_detected"] + c["false_negative_faults"],
                         c["faults_injected"])
        self.assertEqual(c["rollback_correctness"], 1.0)
        self.assertEqual(c["certified_resumes"], 0)
        self.assertFalse(c["reflex_certified_anything"])
        self.assertTrue(c["ledger_chain_verified"])
        rs = tap._load_reflex()
        for ft in rs.FAULT_TYPES:
            rep = rs.run_campaign(ft, seed=7, fault_types=[ft], path="reflex")
            self.assertEqual(rep["faults_detected"], rep["faults_injected"], ft)
            self.assertEqual(rep["silent_fault_escapes"], 0, ft)

    def test_frame_demo_clean_passes_corrupt_isolates(self):
        d = tap.reflex_frame_demo()
        self.assertTrue(d["available"])
        self.assertEqual(d["clean_frame"]["trust_state"], "PENDING_VERIFICATION")
        self.assertEqual(d["corrupted_frame"]["decision"], "ISOLATE")
        self.assertEqual(d["corrupted_frame"]["trust_state"], "QUARANTINED")
        self.assertTrue(d["corrupted_frame"]["safe_state_preserved"])
        self.assertFalse(d["reflex_certified_anything"])

    def test_gate_failure_isolates_and_still_refuses_message(self):
        agent, artist, booking = _booked_agent()
        n_before = len(agent.messages)
        with self.assertRaises(AgentError):
            agent.send_message(booking.booking_id, artist.artist_id,
                               '{"tool_call": {"name": "x"}}')
        # Fail closed: the refused message was never stored.
        self.assertEqual(len(agent.messages), n_before)
        self.assertEqual(len(agent.reflex_events), 1)
        ev = agent.reflex_events[0]
        self.assertTrue(ev["simulation_only"])
        self.assertEqual(ev["detected"], "gate_text_failure")
        self.assertEqual(ev["decision"], "ISOLATE")
        self.assertEqual(ev["trust_state"], "QUARANTINED")
        self.assertEqual(ev["safe_state"], {"command": "HOLD_SAFE", "seq": -1, "mode": "SAFE_STATE"})
        self.assertIn("governance review", ev["escalated_to"])
        self.assertFalse(ev["reflex_certified_anything"])

    def test_clean_message_passes_with_no_reflex_event(self):
        agent, artist, booking = _booked_agent()
        msg = agent.send_message(booking.booking_id, artist.artist_id, "Load-in at 6pm works for me.")
        self.assertEqual(msg["text"], "Load-in at 6pm works for me.")
        self.assertEqual(agent.reflex_events, [])

    def test_gate_text_itself_unchanged(self):
        # gate_text() still refuses on its own; the reflex adds containment,
        # it does not duplicate or replace the screen.
        with self.assertRaises(AgentError):
            tap.gate_text('{"tool_call": {"name": "x"}}')
        tap.gate_text("Load-in at 6pm works for me.")

    def test_ledger_hash_mismatch_isolates_and_still_refuses_load(self):
        agent, _artist, _booking = _booked_agent()
        before = len(tap.reflex_events())
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "store.json")
            agent.save(p)
            with open(p) as f:
                sealed = json.load(f)
            sealed["data"]["artists"][0]["name"] = "Tampered Name"
            with open(p, "w") as f:
                json.dump(sealed, f)
            with self.assertRaises(AgentError):
                TalentAgent.load(p, key="test-key")
        after = tap.reflex_events()
        self.assertEqual(len(after), before + 1)
        ev = after[-1]
        self.assertEqual(ev["detected"], "ledger_hash_mismatch")
        self.assertEqual(ev["decision"], "ISOLATE")
        self.assertEqual(ev["trust_state"], "QUARANTINED")
        self.assertFalse(ev["reflex_certified_anything"])

    def test_reflex_status_carries_probe_and_events(self):
        agent, artist, booking = _booked_agent()
        with self.assertRaises(AgentError):
            agent.send_message(booking.booking_id, artist.artist_id, "bad\x00byte")
        st = agent.reflex_status()
        self.assertTrue(st["simulation_only"])
        self.assertFalse(st["reflex_certified_anything"])
        self.assertFalse(st["probe"]["reflex_certified_anything"])
        self.assertEqual(len(st["reflex_events"]), 1)
        self.assertEqual(st["reflex_events"][0]["detected"], "gate_text_failure")

    def test_demo_carries_reflex_frame_demo(self):
        d = tap.demo()
        demo = d["reflex_simulation"]
        self.assertTrue(demo["simulation_only"])
        self.assertEqual(demo["corrupted_frame"]["decision"], "ISOLATE")
        self.assertFalse(demo["reflex_certified_anything"])
        self.assertTrue(d["save_load_roundtrip"]["identical_status"])


if __name__ == "__main__":
    unittest.main()
