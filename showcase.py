"""Discover — the public section for people looking for an artist.

Who can see what, by design:
  * An artist is listed ONLY if they explicitly publish their
    profile. Default is private. Unpublishing removes them from
    discovery at once. No bookings, money, messages, tasks,
    campaigns, or private details are ever in a public listing —
    the public record is only: showcase name, skills, location,
    bio (written by the artist), tier name, and their samples.
  * Samples are the artist's own work, added by the artist.
    They are labeled "provided by the artist." This module does
    NOT verify that a sample is client work, and it never invents
    a sample, a client, or a credit. A sample can carry a link or
    a local file reference the artist supplies; this module does
    not fetch it, play it, or claim it is live.
  * "Connect" is a REQUEST, not a message sent out, not an email,
    not a text. A seeker fills in their name, what they are
    looking for, and a contact they choose to share with that
    artist only. The request sits PENDING for the artist to see.
    The artist accepts or declines. Accepting does NOT book,
    sign, or pay anything — it only lets the artist see the
    seeker's contact and reply outside this app, if they choose.
    Declining closes the request. No seeker contact is shown to
    anyone except that one artist, and only after the request
    exists — browsing never reveals seeker data, because seekers
    are not listed at all.

Legal gate, unchanged: this is a discovery and self-representation
tool, NOT a licensed talent agency. Union / California / New York
procurement still routes through a licensed franchised agent
and/or attorney. A connection here is an introduction, not a
booking, a contract, or a representation agreement.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from talent_agent_plus import AgentError, gate_text

SAMPLE_KINDS = ("song", "design", "video", "photo", "performance", "other")


@dataclass
class PublicProfile:
    artist_id: str
    showcase_name: str
    skills: list
    location: str
    bio: str
    tier_name: str
    published: bool = True


@dataclass
class Sample:
    sample_id: str
    artist_id: str
    title: str
    kind: str
    description: str = ""
    link_or_reference: str = ""
    provided_by: str = "artist"


@dataclass
class ConnectRequest:
    request_id: str
    artist_id: str
    seeker_name: str
    seeker_contact: str
    message: str
    state: str = "PENDING"  # PENDING, ACCEPTED, DECLINED


class Showcase:
    def __init__(self, agent):
        self.agent = agent  # TalentAgent core, for name/skills/location/tier
        self.profiles: dict[str, PublicProfile] = {}
        self.samples: dict[str, Sample] = {}
        self.requests: dict[str, ConnectRequest] = {}
        self._counters = {"sample": 0, "request": 0}

    # -- artist side: publish, samples, requests -------------------------
    def publish_profile(self, artist_id: str, bio: str = "", showcase_name: str = "") -> PublicProfile:
        if artist_id not in self.agent.artists:
            raise AgentError(f"unknown artist {artist_id!r}")
        a = self.agent.artists[artist_id]
        gate_text(bio)
        p = PublicProfile(artist_id, showcase_name.strip() or a.name, list(a.skills),
                          a.location, bio, self.agent.artists[artist_id].tier)
        self.profiles[artist_id] = p
        return p

    def unpublish_profile(self, artist_id: str) -> None:
        self.profiles.pop(artist_id, None)

    def add_sample(self, artist_id: str, title: str, kind: str, description: str = "",
                   link_or_reference: str = "") -> Sample:
        if artist_id not in self.profiles:
            raise AgentError("artist must publish a profile before adding samples")
        if not title or not title.strip():
            raise AgentError("sample needs a title")
        if kind not in SAMPLE_KINDS:
            raise AgentError(f"unknown sample kind {kind!r}")
        gate_text(description)
        self._counters["sample"] += 1
        s = Sample(f"sample-{self._counters['sample']:04d}", artist_id, title.strip(), kind,
                   description, link_or_reference)
        self.samples[s.sample_id] = s
        return s

    def artist_requests(self, artist_id: str) -> list[ConnectRequest]:
        return [r for r in self.requests.values() if r.artist_id == artist_id]

    def decide_request(self, request_id: str, artist_id: str, accept: bool) -> ConnectRequest:
        if request_id not in self.requests:
            raise AgentError(f"unknown connect request {request_id!r}")
        r = self.requests[request_id]
        if r.artist_id != artist_id:
            raise AgentError("request does not belong to this artist")
        if r.state != "PENDING":
            raise AgentError(f"request is already {r.state}")
        r.state = "ACCEPTED" if accept else "DECLINED"
        return r

    # -- public side: browse, no login, published only -------------------
    def browse_artists(self, skill: str = "", location: str = "", text: str = "") -> list[dict]:
        out = []
        for p in self.profiles.values():
            if not p.published:
                continue
            if skill and skill not in p.skills:
                continue
            if location and location.lower() not in p.location.lower():
                continue
            if text and text.lower() not in (p.showcase_name + " " + p.bio).lower():
                continue
            out.append({"artist_id": p.artist_id, "showcase_name": p.showcase_name,
                        "skills": list(p.skills), "location": p.location, "bio": p.bio,
                        "tier": p.tier_name,
                        "samples": [self._public_sample(s) for s in self.samples.values()
                                    if s.artist_id == p.artist_id]})
        return sorted(out, key=lambda d: d["showcase_name"])

    def browse_samples(self, kind: str = "", skill: str = "") -> list[dict]:
        out = []
        for s in self.samples.values():
            if s.artist_id not in self.profiles:
                continue
            if kind and s.kind != kind:
                continue
            if skill and skill not in self.profiles[s.artist_id].skills:
                continue
            d = self._public_sample(s)
            d["artist_name"] = self.profiles[s.artist_id].showcase_name
            out.append(d)
        return sorted(out, key=lambda d: d["title"])

    @staticmethod
    def _public_sample(s: Sample) -> dict:
        return {"sample_id": s.sample_id, "title": s.title, "kind": s.kind,
                "description": s.description, "link_or_reference": s.link_or_reference,
                "provided_by": s.provided_by,
                "note": "Sample provided by the artist. Not verified as client work by this app."}

    def request_connection(self, artist_id: str, seeker_name: str, seeker_contact: str,
                           message: str) -> ConnectRequest:
        """Public, no login. The seeker chooses what contact to share, for this artist only."""
        if artist_id not in self.profiles:
            raise AgentError("that artist is not listed publicly; a connection request cannot be made")
        if not seeker_name or not seeker_name.strip():
            raise AgentError("a connection request needs the seeker's name")
        if not seeker_contact or not seeker_contact.strip():
            raise AgentError("a connection request needs a contact the seeker chooses to share with this artist")
        gate_text(message)
        self._counters["request"] += 1
        r = ConnectRequest(f"request-{self._counters['request']:04d}", artist_id,
                           seeker_name.strip(), seeker_contact.strip(), message)
        self.requests[r.request_id] = r
        return r
