"""Calls — a call log and call scripts, NOT a live phone system.

What is here, honestly:
  * Schedule a call (as a secretary task, kind "call").
  * Log a call that actually happened, with contact, outcome, and
    notes — entered by a human after the call.
  * Draft a call script for artist approval. A script is text, not
    a dial. No telephony API, no auto-dialer, no recorded call
    from here. Disclosed AI calls (Pro tier and up, inbound +
    artist-approved outbound only) are a future adapter, not
    built here: when one is built, it must disclose it is AI,
    get recording consent, and never cold-call blast.

A call log is not proof a call happened — it is the artist's own
record. That distinction is kept in the field names on purpose.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from talent_agent_plus import AgentError, _check_date, gate_text

CALL_OUTCOMES = ("scheduled", "completed", "no_answer", "rescheduled", "declined")


@dataclass
class CallRecord:
    call_id: str
    artist_id: str
    contact: str
    purpose: str
    date: str = ""
    outcome: str = "scheduled"
    notes: str = ""
    script: str = ""


class CallLog:
    def __init__(self):
        self.calls: dict[str, CallRecord] = {}
        self._counter = 0

    def schedule_call(self, artist_id: str, contact: str, purpose: str, date: str = "") -> CallRecord:
        if not contact or not contact.strip():
            raise AgentError("call needs a contact")
        if not purpose or not purpose.strip():
            raise AgentError("call needs a purpose")
        _check_date(date)
        self._counter += 1
        c = CallRecord(f"call-{self._counter:04d}", artist_id, contact.strip(), purpose.strip(), date)
        self.calls[c.call_id] = c
        return c

    def draft_script(self, call_id: str, script: str) -> CallRecord:
        gate_text(script)
        c = self._call(call_id)
        c.script = script
        return c

    def log_outcome(self, call_id: str, outcome: str, notes: str = "") -> CallRecord:
        c = self._call(call_id)
        if outcome not in CALL_OUTCOMES:
            raise AgentError(f"unknown call outcome {outcome!r}")
        c.outcome = outcome
        c.notes = notes
        return c

    def list_calls(self, artist_id: str) -> list[CallRecord]:
        return sorted((c for c in self.calls.values() if c.artist_id == artist_id),
                      key=lambda c: (c.date or "9999", c.contact))

    def _call(self, call_id: str) -> CallRecord:
        if call_id not in self.calls:
            raise AgentError(f"unknown call {call_id!r}")
        return self.calls[call_id]
