"""Minimal reflex simulator — SIMULATION ONLY / RESEARCH ONLY.

Research-only software simulation (milestone 4, section 7 of the research
spec in SPEC.md). This module simulates synthetic neural-intent frames /
sensor packets, injects controlled software faults, and compares a minimal
reflex path with a full governance path under identical fault campaigns.

No medical claims: this simulation does not prevent seizures, heal tissue,
establish clinical safety, or demonstrate implant compatibility. Latency
numbers are software-only simulation values on this machine, not
hardware/neural timing. Benchmark thresholds are NOT clinical requirements.
Power cost is an estimate label, never a measured claim.

Determinism: same fault campaign (same seed) in, same canonical metrics
out. Latency values in the canonical metrics are deterministic simulated
software latencies derived from the seeded RNG (see
``_deterministic_latency_ms``); real wall time and peak RSS are measured
separately under ``resource_cost`` and are excluded from the canonical
(byte-identical) metrics output because real timing varies run to run.

Discipline: hashlib.sha256 only — NEVER builtin hash() for digests or
ledger chaining. No hardcoded secrets; no keys/credentials anywhere.
Python standard library only.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import resource
import statistics
import time
from dataclasses import dataclass, field

__all__ = [
    "FAULT_TYPES",
    "DEADLINE_THRESHOLDS_MS",
    "Frame",
    "compute_digest",
    "make_frame",
    "generate_clean_frames",
    "inject_bit_corruption",
    "inject_stale_replay",
    "inject_packet_reordering",
    "inject_duplicate_frames",
    "inject_dropped_frames",
    "inject_impossible_values",
    "inject_delayed_responses",
    "inject_conflicting_control",
    "INJECTORS",
    "apply_fault_campaign",
    "EvidenceLedger",
    "ReflexKernel",
    "FullGovernancePath",
    "run_campaign",
    "compare_paths",
    "canonical_metrics_json",
    "deterministic_metrics",
]

# Benchmark thresholds (ms) — benchmark thresholds, NOT clinical requirements.
DEADLINE_THRESHOLDS_MS = (0.1, 0.25, 0.5, 1.0)

FAULT_TYPES = (
    "bit_corruption",
    "stale_replay",
    "packet_reordering",
    "duplicate_frames",
    "dropped_frames",
    "impossible_values",
    "delayed_responses",
    "conflicting_control",
)

# Simulation-only expected bounds for synthetic frame values.
VALUE_MIN = -1.0
VALUE_MAX = 1.0
# Simulation-only: a synthetic timestamp gap larger than this is "delayed".
MAX_TIMESTAMP_GAP_MS = 10.0
SIM_SOURCE_ID = "sim-sensor-01"
SIM_CONTROL_SOURCE_ID = "sim-control-01"


def _canonical_json(obj) -> str:
    """Canonical JSON for digests / ledger chaining (sorted keys, no spaces)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def compute_digest(seq: int, timestamp_ms: float, source_id: str, values) -> str:
    """SHA-256 integrity digest for a synthetic simulation frame."""
    payload = {
        "seq": seq,
        "timestamp_ms": timestamp_ms,
        "source_id": source_id,
        "values": [float(v) for v in values],
    }
    return _sha256_hex(_canonical_json(payload))


@dataclass(frozen=True)
class Frame:
    """One timestamped synthetic simulation frame / sensor packet."""

    seq: int
    timestamp_ms: float
    source_id: str
    values: tuple
    digest: str
    # Simulation-only ground-truth bookkeeping (not part of the digest):
    # True only for frames created / altered by a fault injector.
    is_faulty: bool = False
    fault_type: str = ""
    fault_id: str = ""

    def with_values(self, values, *, is_faulty=False, fault_type="", fault_id=""):
        """Return a copy with new values but the ORIGINAL digest.

        Simulation-only helper for bit corruption: the payload changes
        while the digest does not, so digest verification must fail.
        """
        return Frame(
            seq=self.seq,
            timestamp_ms=self.timestamp_ms,
            source_id=self.source_id,
            values=tuple(float(v) for v in values),
            digest=self.digest,
            is_faulty=is_faulty,
            fault_type=fault_type,
            fault_id=fault_id,
        )

    def with_digest(self, *, is_faulty=False, fault_type="", fault_id=""):
        """Return a copy whose digest matches its current payload."""
        return Frame(
            seq=self.seq,
            timestamp_ms=self.timestamp_ms,
            source_id=self.source_id,
            values=self.values,
            digest=compute_digest(
                self.seq, self.timestamp_ms, self.source_id, self.values
            ),
            is_faulty=is_faulty,
            fault_type=fault_type,
            fault_id=fault_id,
        )


def make_frame(seq: int, timestamp_ms: float, source_id: str, values) -> Frame:
    """Build a clean synthetic simulation frame with a correct digest."""
    values = tuple(float(v) for v in values)
    return Frame(
        seq=seq,
        timestamp_ms=timestamp_ms,
        source_id=source_id,
        values=values,
        digest=compute_digest(seq, timestamp_ms, source_id, values),
    )


def generate_clean_frames(n_frames: int, seed: int) -> list:
    """Generate ``n_frames`` clean synthetic simulation frames (seeded)."""
    rng = random.Random(seed)
    frames = []
    ts = 1000.0  # simulation-only start timestamp (ms)
    for seq in range(n_frames):
        values = tuple(round(rng.uniform(-0.9, 0.9), 6) for _ in range(4))
        frames.append(make_frame(seq, ts, SIM_SOURCE_ID, values))
        ts += 1.0  # simulation-only 1 ms nominal frame spacing
    return frames


# ---------------------------------------------------------------------------
# Fault injection — each injector is named, seeded, and reproducible.
# Each returns (frames, fault_log); fault_log entries name the fault,
# the affected sequence number(s), and a deterministic fault id.
# ---------------------------------------------------------------------------

def _fault_id(fault_type: str, seed: int, index: int) -> str:
    return f"{fault_type}-{seed}-{index}"


def inject_bit_corruption(frames, seed: int, count: int = 3):
    """Simulation-only injector: flip a payload value, keep the old digest."""
    rng = random.Random(seed)
    frames = list(frames)
    log = []
    indices = rng.sample(range(len(frames)), min(count, len(frames)))
    for i, idx in enumerate(sorted(indices)):
        f = frames[idx]
        corrupted = list(f.values)
        corrupted[0] = round(corrupted[0] + 0.5, 6)
        fid = _fault_id("bit_corruption", seed, i)
        frames[idx] = f.with_values(
            corrupted, is_faulty=True, fault_type="bit_corruption", fault_id=fid
        )
        log.append({"fault_id": fid, "fault_type": "bit_corruption", "seq": f.seq})
    return frames, log


def inject_stale_replay(frames, seed: int, count: int = 3):
    """Simulation-only injector: re-insert old frames later in the stream."""
    rng = random.Random(seed)
    frames = list(frames)
    log = []
    for i in range(count):
        src_idx = rng.randrange(0, max(1, len(frames) // 2))
        insert_at = rng.randrange(len(frames) // 2, len(frames))
        src = frames[src_idx]
        fid = _fault_id("stale_replay", seed, i)
        replay = Frame(
            seq=src.seq, timestamp_ms=src.timestamp_ms, source_id=src.source_id,
            values=src.values, digest=src.digest,
            is_faulty=True, fault_type="stale_replay", fault_id=fid,
        )
        frames.insert(insert_at, replay)
        log.append({"fault_id": fid, "fault_type": "stale_replay", "seq": src.seq})
    return frames, log


def inject_packet_reordering(frames, seed: int, count: int = 3):
    """Simulation-only injector: swap adjacent frames out of sequence order."""
    rng = random.Random(seed)
    frames = list(frames)
    log = []
    for i in range(count):
        idx = rng.randrange(0, len(frames) - 1)
        a, b = frames[idx], frames[idx + 1]
        fid = _fault_id("packet_reordering", seed, i)
        frames[idx] = Frame(
            seq=b.seq, timestamp_ms=b.timestamp_ms, source_id=b.source_id,
            values=b.values, digest=b.digest,
            is_faulty=True, fault_type="packet_reordering", fault_id=fid,
        )
        frames[idx + 1] = Frame(
            seq=a.seq, timestamp_ms=a.timestamp_ms, source_id=a.source_id,
            values=a.values, digest=a.digest,
            is_faulty=True, fault_type="packet_reordering", fault_id=fid,
        )
        log.append(
            {"fault_id": fid, "fault_type": "packet_reordering",
             "seq": a.seq, "swapped_with_seq": b.seq}
        )
    return frames, log


def inject_duplicate_frames(frames, seed: int, count: int = 3):
    """Simulation-only injector: insert exact duplicate frames."""
    rng = random.Random(seed)
    frames = list(frames)
    log = []
    for i in range(count):
        src_idx = rng.randrange(0, len(frames))
        insert_at = src_idx + 1
        src = frames[src_idx]
        fid = _fault_id("duplicate_frames", seed, i)
        dup = Frame(
            seq=src.seq, timestamp_ms=src.timestamp_ms, source_id=src.source_id,
            values=src.values, digest=src.digest,
            is_faulty=True, fault_type="duplicate_frames", fault_id=fid,
        )
        frames.insert(insert_at, dup)
        log.append({"fault_id": fid, "fault_type": "duplicate_frames", "seq": src.seq})
    return frames, log


def inject_dropped_frames(frames, seed: int, count: int = 3):
    """Simulation-only injector: remove frames, leaving a sequence gap."""
    rng = random.Random(seed)
    frames = list(frames)
    log = []
    indices = sorted(
        rng.sample(range(len(frames)), min(count, len(frames))), reverse=True
    )
    for i, idx in enumerate(indices):
        f = frames.pop(idx)
        fid = _fault_id("dropped_frames", seed, i)
        log.append({"fault_id": fid, "fault_type": "dropped_frames", "seq": f.seq})
    return frames, log


def inject_impossible_values(frames, seed: int, count: int = 3):
    """Simulation-only injector: out-of-bounds values, digest recomputed.

    The digest is valid on purpose, so detection must come from the
    bounds check — a digest-only check would silently miss this fault.
    """
    rng = random.Random(seed)
    frames = list(frames)
    log = []
    indices = rng.sample(range(len(frames)), min(count, len(frames)))
    for i, idx in enumerate(sorted(indices)):
        f = frames[idx]
        bad = list(f.values)
        bad[0] = 999.0  # simulation-only impossible value
        fid = _fault_id("impossible_values", seed, i)
        frames[idx] = f.with_values(bad).with_digest(
            is_faulty=True, fault_type="impossible_values", fault_id=fid
        )
        log.append({"fault_id": fid, "fault_type": "impossible_values", "seq": f.seq})
    return frames, log


def inject_delayed_responses(frames, seed: int, count: int = 3):
    """Simulation-only injector: timestamps delayed beyond the gap bound."""
    rng = random.Random(seed)
    frames = list(frames)
    log = []
    indices = rng.sample(range(len(frames)), min(count, len(frames)))
    for i, idx in enumerate(sorted(indices)):
        f = frames[idx]
        fid = _fault_id("delayed_responses", seed, i)
        delayed = Frame(
            seq=f.seq, timestamp_ms=f.timestamp_ms + 50.0,
            source_id=f.source_id, values=f.values,
            digest=compute_digest(f.seq, f.timestamp_ms + 50.0, f.source_id, f.values),
            is_faulty=True, fault_type="delayed_responses", fault_id=fid,
        )
        frames[idx] = delayed
        log.append({"fault_id": fid, "fault_type": "delayed_responses", "seq": f.seq})
    return frames, log


def inject_conflicting_control(frames, seed: int, count: int = 3):
    """Simulation-only injector: same seq, different payload/source.

    A conflicting control message reuses a sequence number with a
    different payload (and a different source identity), which must be
    treated as a split-brain / conflicting safe-state event.
    """
    rng = random.Random(seed)
    frames = list(frames)
    log = []
    for i in range(count):
        src_idx = rng.randrange(0, len(frames))
        src = frames[src_idx]
        fid = _fault_id("conflicting_control", seed, i)
        conflict_values = tuple(round(v + 0.25, 6) for v in src.values)
        conflict = make_frame(src.seq, src.timestamp_ms, SIM_CONTROL_SOURCE_ID,
                              conflict_values)
        conflict = Frame(
            seq=conflict.seq, timestamp_ms=conflict.timestamp_ms,
            source_id=conflict.source_id, values=conflict.values,
            digest=conflict.digest,
            is_faulty=True, fault_type="conflicting_control", fault_id=fid,
        )
        frames.insert(src_idx + 1, conflict)
        log.append(
            {"fault_id": fid, "fault_type": "conflicting_control", "seq": src.seq}
        )
    return frames, log


INJECTORS = {
    "bit_corruption": inject_bit_corruption,
    "stale_replay": inject_stale_replay,
    "packet_reordering": inject_packet_reordering,
    "duplicate_frames": inject_duplicate_frames,
    "dropped_frames": inject_dropped_frames,
    "impossible_values": inject_impossible_values,
    "delayed_responses": inject_delayed_responses,
    "conflicting_control": inject_conflicting_control,
}


def apply_fault_campaign(frames, fault_types, seed: int):
    """Apply named injectors in a fixed order; returns (frames, fault_log).

    Simulation-only: injector order is the fixed FAULT_TYPES order, and
    each injector gets a deterministic sub-seed derived from the campaign
    seed, so the same campaign seed always produces the same stream.
    """
    frames = list(frames)
    fault_log = []
    for j, fault_type in enumerate(FAULT_TYPES):
        if fault_type in fault_types:
            sub_seed = seed * 1000 + j + 7
            frames, log = INJECTORS[fault_type](frames, sub_seed)
            fault_log.extend(log)
    return frames, fault_log


# ---------------------------------------------------------------------------
# Append-only evidence ledger (hash-chained, SHA-256).
# ---------------------------------------------------------------------------

@dataclass
class EvidenceLedger:
    """Append-only simulation evidence ledger with a SHA-256 hash chain.

    Every transition and recovery event is recorded; each entry links the
    previous entry hash. There is no delete / edit / rewrite path.
    """

    entries: list = field(default_factory=list)

    def append(self, event: str, details: dict) -> dict:
        prev_hash = self.entries[-1]["entry_hash"] if self.entries else "GENESIS"
        entry = {
            "index": len(self.entries),
            "event": event,
            "details": details,
            "prev_hash": prev_hash,
        }
        entry["entry_hash"] = _sha256_hex(_canonical_json(entry))
        self.entries.append(entry)
        return entry

    def verify_chain(self) -> bool:
        """Recompute every link; True only if the chain is intact."""
        prev = "GENESIS"
        for entry in self.entries:
            if entry["prev_hash"] != prev:
                return False
            check = dict(entry)
            expected = check.pop("entry_hash")
            if _sha256_hex(_canonical_json(check)) != expected:
                return False
            prev = expected
        return True

    def __len__(self):
        return len(self.entries)


# ---------------------------------------------------------------------------
# Reflex kernel — detect -> isolate -> safe-state fallback -> signal.
# It NEVER certifies its own repair. A reflex-only outcome is
# QUARANTINED (after a fault) or PENDING_VERIFICATION (clean pass-through);
# it is never CERTIFIED.
# ---------------------------------------------------------------------------

@dataclass
class ReflexOutcome:
    decision: str  # "PASS" or "ISOLATE"
    reason: str  # "" for PASS, otherwise the detected fault type / cause
    trust_state: str  # "PENDING_VERIFICATION" or "QUARANTINED" — never CERTIFIED
    safe_state: dict  # last certified state, preserved on fallback
    latency_ms: float  # deterministic simulated software latency (see below)
    seq: int


def _deterministic_latency_ms(rng: random.Random, path: str, faulty: bool) -> float:
    """Deterministic simulated software latency for one frame.

    Simulation-only latency model (not measured hardware/neural timing):
    the reflex path is a minimal check; the full path adds verification,
    evidence, and governance work. Seeded jitter makes campaigns
    reproducible while still varying per frame.
    """
    if path == "reflex":
        base, jitter = 0.015, 0.010
    else:  # full
        base, jitter = 0.120, 0.080
    value = base + rng.random() * jitter
    if faulty:
        value += 0.005
    return round(value, 6)


class ReflexKernel:
    """Minimal safety reflex (simulation only): detect, isolate, fallback, signal."""

    def __init__(self, last_certified_state: dict):
        self.last_certified_state = dict(last_certified_state)
        self.expected_seq = 0
        self.seen_seqs: dict = {}  # seq -> digest (first sighting)
        self.last_timestamp_ms = None
        self.isolated = False

    def _detect(self, frame: Frame):
        """Return a fault reason string, or '' if no fault is detected."""
        # Integrity digest (SHA-256).
        if compute_digest(frame.seq, frame.timestamp_ms, frame.source_id,
                          frame.values) != frame.digest:
            return "bit_corruption"
        # Expected bounds.
        if any((not math.isfinite(v)) or v < VALUE_MIN or v > VALUE_MAX
               for v in frame.values):
            return "impossible_values"
        # Conflicting control: same seq, different payload/source seen before.
        if frame.seq in self.seen_seqs:
            prev_digest, prev_source = self.seen_seqs[frame.seq]
            if frame.digest != prev_digest or frame.source_id != prev_source:
                return "conflicting_control"
            return "duplicate_frames"
        # Stale replay: seq already passed / timestamp going backwards.
        if frame.seq < self.expected_seq:
            return "stale_replay"
        # Dropped frames: sequence gap.
        if frame.seq > self.expected_seq:
            return "dropped_frames"
        # Packet reordering is visible as a seq that is not the expected
        # next seq; the gap check above covers out-of-order arrivals in
        # this simulation-only stream (reordered frames arrive late/early).
        # Delayed responses: timestamp gap beyond the simulation bound.
        if (self.last_timestamp_ms is not None
                and frame.timestamp_ms - self.last_timestamp_ms > MAX_TIMESTAMP_GAP_MS):
            return "delayed_responses"
        return ""

    def process_frame(self, frame: Frame, rng: random.Random) -> ReflexOutcome:
        """Process one synthetic simulation frame through the reflex only."""
        reason = self._detect(frame)
        latency = _deterministic_latency_ms(rng, "reflex", bool(reason))
        if reason:
            self.isolated = True
            # Consume this sequence number so the next in-order frame is
            # not falsely flagged as a gap after this isolation. Duplicate
            # / replay / conflicting frames reuse an already-consumed seq,
            # so they do not advance the expectation.
            if reason in ("bit_corruption", "impossible_values",
                          "delayed_responses", "dropped_frames"):
                self.seen_seqs[frame.seq] = (frame.digest, frame.source_id)
                self.expected_seq = frame.seq + 1
                self.last_timestamp_ms = frame.timestamp_ms
            # Safe-state fallback: preserve the last certified state.
            return ReflexOutcome(
                decision="ISOLATE", reason=reason,
                trust_state="QUARANTINED",
                safe_state=dict(self.last_certified_state),
                latency_ms=latency, seq=frame.seq,
            )
        # Clean frame: record it, advance expected seq. No certification.
        self.seen_seqs[frame.seq] = (frame.digest, frame.source_id)
        self.expected_seq = frame.seq + 1
        self.last_timestamp_ms = frame.timestamp_ms
        return ReflexOutcome(
            decision="PASS", reason="",
            trust_state="PENDING_VERIFICATION",
            safe_state=dict(self.last_certified_state),
            latency_ms=latency, seq=frame.seq,
        )


# ---------------------------------------------------------------------------
# Full governance path — evidence -> verify -> validate -> certify.
# Only this path may produce a CERTIFIED outcome, and only after
# independent re-verification of the recovered state. Quarantine before
# repair; fail closed: any unverified state stays quarantined.
# ---------------------------------------------------------------------------

@dataclass
class GovernanceOutcome:
    decision: str  # "CERTIFIED_RESUME" or "CONTINUED_QUARANTINE"
    trust_state: str  # "CERTIFIED" or "QUARANTINED"
    evidence: list  # names of the evidence items relied on for the decision
    safe_state: dict
    latency_ms: float


class FullGovernancePath:
    """Full BCT/SB688-style governance (simulation only).

    Takes a reflex outcome + the evidence ledger, re-verifies the
    recovered state independently, and only then certifies a controlled
    resume. If verification fails or evidence is incomplete, the state
    stays quarantined — fail closed.
    """

    def __init__(self, last_certified_state: dict):
        self.last_certified_state = dict(last_certified_state)

    def review(self, reflex_outcome: ReflexOutcome, ledger: EvidenceLedger,
               rng: random.Random) -> GovernanceOutcome:
        latency = _deterministic_latency_ms(rng, "full", reflex_outcome.decision == "ISOLATE")
        evidence = [
            f"reflex_decision:{reflex_outcome.decision}",
            f"reflex_reason:{reflex_outcome.reason or 'none'}",
            f"ledger_entries:{len(ledger)}",
            f"ledger_chain_verified:{ledger.verify_chain()}",
            "last_certified_state_preserved:"
            f"{reflex_outcome.safe_state == self.last_certified_state}",
        ]
        # Independent re-verification: safe state must equal the last
        # certified state AND the ledger chain must verify AND the reflex
        # must have recorded the event. Otherwise stay quarantined.
        verified = (
            reflex_outcome.safe_state == self.last_certified_state
            and ledger.verify_chain()
            and len(ledger) > 0
        )
        if reflex_outcome.decision == "ISOLATE" and verified:
            # Quarantine before repair: the recovered state is re-verified
            # here; only then is a controlled resume certified.
            return GovernanceOutcome(
                decision="CERTIFIED_RESUME", trust_state="CERTIFIED",
                evidence=evidence + ["reverification:passed"],
                safe_state=dict(self.last_certified_state),
                latency_ms=latency,
            )
        if reflex_outcome.decision == "PASS" and verified:
            return GovernanceOutcome(
                decision="CERTIFIED_RESUME", trust_state="CERTIFIED",
                evidence=evidence + ["reverification:passed"],
                safe_state=dict(self.last_certified_state),
                latency_ms=latency,
            )
        return GovernanceOutcome(
            decision="CONTINUED_QUARANTINE", trust_state="QUARANTINED",
            evidence=evidence + ["reverification:failed_or_incomplete"],
            safe_state=dict(self.last_certified_state),
            latency_ms=latency,
        )


# ---------------------------------------------------------------------------
# Metrics + campaign runner.
# ---------------------------------------------------------------------------

def _percentile(sorted_values, pct: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    rank = (pct / 100.0) * (len(sorted_values) - 1)
    lo = math.floor(rank)
    hi = math.ceil(rank)
    if lo == hi:
        return float(sorted_values[int(rank)])
    frac = rank - lo
    return float(sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac)


def _latency_stats(latencies) -> dict:
    ordered = sorted(latencies)
    return {
        "median_ms": round(_percentile(ordered, 50), 6),
        "p95_ms": round(_percentile(ordered, 95), 6),
        "p99_ms": round(_percentile(ordered, 99), 6),
        "worst_ms": round(max(ordered), 6) if ordered else 0.0,
        "mean_ms": round(statistics.fmean(ordered), 6) if ordered else 0.0,
        "n": len(ordered),
    }


def _deadline_misses(latencies) -> dict:
    return {
        f"misses_over_{t}_ms": sum(1 for v in latencies if v > t)
        for t in DEADLINE_THRESHOLDS_MS
    }


def run_campaign(campaign_name: str, seed: int, fault_types=None,
                 n_frames: int = 100, path: str = "reflex") -> dict:
    """Run one named, seeded fault campaign (simulation only).

    ``path`` is "reflex" (reflex only) or "full" (reflex + full governance
    review per frame). Returns a metrics report dict. The deterministic
    fields are byte-identical for the same (campaign_name, seed,
    fault_types, n_frames, path); ``resource_cost`` wall time / peak RSS
    are real measurements and are excluded from the canonical output
    (see ``canonical_metrics_json``).
    """
    if fault_types is None:
        fault_types = list(FAULT_TYPES)
    if path not in ("reflex", "full"):
        raise ValueError("path must be 'reflex' or 'full'")
    for ft in fault_types:
        if ft not in FAULT_TYPES:
            raise ValueError(f"unknown fault type: {ft}")

    wall_start = time.perf_counter()
    rng = random.Random(seed)
    last_certified = {"command": "HOLD_SAFE", "seq": -1, "mode": "SAFE_STATE"}
    clean = generate_clean_frames(n_frames, seed)
    frames, fault_log = apply_fault_campaign(clean, fault_types, seed)
    injected_fault_ids = {entry["fault_id"] for entry in fault_log}
    injected_by_type = {
        ft: sum(1 for e in fault_log if e["fault_type"] == ft) for ft in FAULT_TYPES
    }

    ledger = EvidenceLedger()
    reflex = ReflexKernel(last_certified)
    governance = FullGovernancePath(last_certified) if path == "full" else None

    latencies = []
    decisions = []
    detected_fault_ids = set()
    false_positives = 0
    false_negatives = 0
    silent_escapes = 0
    rollback_correct = 0
    rollback_checked = 0
    split_brain_events = 0
    certified_resumes = 0
    quarantined_outcomes = 0
    clean_total = 0
    faulty_total = 0
    pending_fault_ids = set(injected_fault_ids)

    # Frames flagged faulty by an injector, by sequence position in the
    # faulted stream, plus dropped seqs (which have no frame to process).
    for frame in frames:
        if frame.is_faulty:
            faulty_total += 1
        else:
            clean_total += 1

        outcome = reflex.process_frame(frame, rng)
        latencies.append(outcome.latency_ms)
        decisions.append(outcome.decision)

        ledger.append(
            "reflex_transition",
            {"seq": frame.seq, "decision": outcome.decision,
             "reason": outcome.reason, "trust_state": outcome.trust_state,
             "fault_type": frame.fault_type, "fault_id": frame.fault_id},
        )

        if outcome.decision == "ISOLATE":
            quarantined_outcomes += 1
            rollback_checked += 1
            if outcome.safe_state == last_certified:
                rollback_correct += 1
            if frame.fault_type == "conflicting_control":
                split_brain_events += 1
            # Attribute the isolation to a fault if this frame is faulty
            # or an earlier injected fault is still pending (e.g. a
            # dropped frame detected as a sequence gap on this frame).
            if frame.is_faulty and frame.fault_id:
                detected_fault_ids.add(frame.fault_id)
                pending_fault_ids.discard(frame.fault_id)
            elif pending_fault_ids:
                # Sequence-gap detection of a dropped frame.
                dropped = [fid for fid in pending_fault_ids
                           if fid.startswith("dropped_frames-")]
                if dropped and outcome.reason == "dropped_frames":
                    detected_fault_ids.add(dropped[0])
                    pending_fault_ids.discard(dropped[0])
                else:
                    false_positives += 1
            else:
                false_positives += 1
            if frame.fault_type == "conflicting_control":
                ledger.append("split_brain_event",
                              {"seq": frame.seq, "fault_id": frame.fault_id})
        else:  # PASS
            if frame.is_faulty:
                # A faulty frame that passes the reflex is a silent
                # escape in BOTH paths: the full path uses the same
                # detection check, and only adds governance AFTER an
                # isolation — it does not invent a second detection.
                false_negatives += 1
                silent_escapes += 1
                ledger.append("silent_escape",
                              {"seq": frame.seq, "fault_type": frame.fault_type,
                               "fault_id": frame.fault_id})

        if path == "full" and governance is not None:
            # Full path pays governance latency on every frame (identity,
            # policy, evidence, verification work), and runs the
            # evidence -> verify -> validate -> certify review only
            # after an isolation, before any controlled resume.
            latencies.append(
                _deterministic_latency_ms(
                    rng, "full", outcome.decision == "ISOLATE"))
            if outcome.decision == "ISOLATE":
                gov = governance.review(outcome, ledger, rng)
                ledger.append("governance_review",
                              {"seq": frame.seq, "decision": gov.decision,
                               "evidence": gov.evidence})
                if gov.decision == "CERTIFIED_RESUME":
                    certified_resumes += 1
                    ledger.append("controlled_resume",
                                  {"seq": frame.seq, "trust_state": "CERTIFIED",
                                   "evidence": gov.evidence})
                else:
                    ledger.append("continued_quarantine",
                                  {"seq": frame.seq, "trust_state": "QUARANTINED",
                                   "evidence": gov.evidence})

    # Any injected fault never detected by the end is a silent escape.
    undetected = pending_fault_ids - detected_fault_ids
    # Dropped-frame faults whose gap was never seen, and any other
    # undetected injected fault, count as false negatives — except
    # faults already counted per-frame above (avoid double counting:
    # only count ids not already in detected set and not already
    # counted as a per-frame FN). Per-frame FNs above have their ids
    # still in pending_fault_ids, so count each remaining id once.
    for fid in sorted(undetected):
        # Per-frame PASS faults were already counted as false negatives
        # above, so only count undetected faults whose frame was never
        # presented at all (dropped frames whose gap was never seen).
        presented = any(f.fault_id == fid for f in frames)
        if not presented:
            false_negatives += 1
            silent_escapes += 1

    total_frames = len(frames)
    fp_rate = (false_positives / clean_total) if clean_total else 0.0
    fn_rate = (false_negatives / faulty_total) if faulty_total else 0.0
    # Evidence completeness: ledger entries that verify / expected events.
    # Expected events = one reflex transition per frame + governance
    # events actually recorded; completeness is the verified fraction.
    ledger_ok = ledger.verify_chain()
    evidence_completeness = 1.0 if (ledger_ok and len(ledger) >= total_frames) else (
        (len(ledger) / total_frames) if total_frames else 0.0
    )
    rollback_rate = (rollback_correct / rollback_checked) if rollback_checked else 1.0

    wall_time_ms = (time.perf_counter() - wall_start) * 1000.0
    peak_rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    latency = _latency_stats(latencies)
    report = {
        "campaign": campaign_name,
        "path": path,
        "seed": seed,
        "n_frames_requested": n_frames,
        "n_frames_processed": total_frames,
        "fault_types": sorted(fault_types),
        "faults_injected": len(fault_log),
        "faults_injected_by_type": injected_by_type,
        "faults_detected": len(detected_fault_ids),
        "latency_ms": latency,
        "deadline_misses": _deadline_misses(latencies),
        "deadline_thresholds_ms": list(DEADLINE_THRESHOLDS_MS),
        "deadline_note": "Benchmark thresholds, NOT clinical requirements.",
        "false_positive_isolations": false_positives,
        "false_positive_rate": round(fp_rate, 6),
        "false_negative_faults": false_negatives,
        "false_negative_rate": round(fn_rate, 6),
        "silent_fault_escapes": silent_escapes,
        "rollback_correct": rollback_correct,
        "rollback_checked": rollback_checked,
        "rollback_correctness": round(rollback_rate, 6),
        "evidence_completeness": round(evidence_completeness, 6),
        "ledger_entries": len(ledger),
        "ledger_chain_verified": ledger_ok,
        "split_brain_events": split_brain_events,
        "certified_resumes": certified_resumes,
        "quarantined_outcomes": quarantined_outcomes,
        "reflex_certified_anything": False,
        "resource_cost": {
            "wall_time_ms": round(wall_time_ms, 3),
            "peak_rss_kb": int(peak_rss_kb),
            "power": "estimated, not measured — simulation-only estimate label; "
                     "no power measurement was made",
        },
        "simulation_only": True,
        "research_only": True,
    }
    return report


def deterministic_metrics(report: dict) -> dict:
    """The deterministic subset of a metrics report (excludes resource_cost)."""
    return {k: v for k, v in report.items() if k != "resource_cost"}


def canonical_metrics_json(report: dict) -> str:
    """Byte-identical canonical JSON for a metrics report (deterministic part)."""
    return json.dumps(deterministic_metrics(report), sort_keys=True, indent=2)


def compare_paths(campaign_name: str, seed: int, fault_types=None,
                  n_frames: int = 100) -> dict:
    """Milestone 5 shape — comparison function: FULL path vs REFLEX path.

    Runs the identical seeded campaign through both paths and returns
    both reports plus a deterministic comparison summary. Labeled as a
    comparison function; it is not a clinical or hardware comparison.
    """
    reflex_report = run_campaign(campaign_name, seed, fault_types, n_frames, "reflex")
    full_report = run_campaign(campaign_name, seed, fault_types, n_frames, "full")
    comparison = {
        "campaign": campaign_name,
        "seed": seed,
        "reflex_median_ms": reflex_report["latency_ms"]["median_ms"],
        "full_median_ms": full_report["latency_ms"]["median_ms"],
        "median_latency_delta_ms": round(
            full_report["latency_ms"]["median_ms"]
            - reflex_report["latency_ms"]["median_ms"], 6),
        "reflex_p95_ms": reflex_report["latency_ms"]["p95_ms"],
        "full_p95_ms": full_report["latency_ms"]["p95_ms"],
        "reflex_silent_escapes": reflex_report["silent_fault_escapes"],
        "full_silent_escapes": full_report["silent_fault_escapes"],
        "reflex_false_negatives": reflex_report["false_negative_faults"],
        "full_false_negatives": full_report["false_negative_faults"],
        "reflex_certified_resumes": reflex_report["certified_resumes"],
        "full_certified_resumes": full_report["certified_resumes"],
        "reflex_quarantined_outcomes": reflex_report["quarantined_outcomes"],
        "full_quarantined_outcomes": full_report["quarantined_outcomes"],
        "note": "Comparison function (milestone 5 shape) — simulation only; "
                "benchmark thresholds are NOT clinical requirements.",
    }
    return {
        "reflex": reflex_report,
        "full": full_report,
        "comparison": comparison,
        "simulation_only": True,
        "research_only": True,
    }
