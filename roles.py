"""Promoter, Producer, and Secretary — the team roles around the agent.

These are NOT separate AIs and NOT people being hired. They are
named job-lists inside Talent Agent Plus Complete, so one artist's
team work is organised in one place, with the same gates as the
agent core: human approval stays on, nothing is published, sent,
or paid on its own.

PROMOTER — does what a promoter does, in draft form:
  * Builds a campaign for a release, gig, or merch drop, with a
    budget CAP. Ad spend is tracked as an expense (category
    "advertising") and can never pass the cap unless the owner
    raises it. A cap is a cap.
  * Drafts social posts. Drafts are DRAFTS: they are never posted
    live from here. An artist-approved draft becomes READY, and
    posting it is a human's step, on the platform, outside this
    module. No platform API, no auto-posting, no invented reach
    numbers — results are notes the promoter enters by hand.

PRODUCER — does what a producer does for a release:
  * Runs a production pipeline for a core release: DEMO ->
    RECORDING -> MIX -> MASTER -> READY_FOR_APPROVAL. The producer
    may move it forward and leave notes, but the producer does
    NOT approve the release — only the artist approves it, in the
    core (approve_release). A release that is not artist-approved
    is never exported, and export is local-only, never a live
    distributor release.

SECRETARY — does what a secretary does:
  * Keeps a task list (calls, paperwork, travel, booking follow-up)
    with due dates, marks tasks done, and drafts messages.
  * Drafts are local text, NOT sent messages. Sending is the
    agent core's send_message, on a live booking, by a party to
    that booking, through the message gate.
  * Writes the daily brief: today's agenda, open tasks, active
    campaigns, and low-stock merch — one page the artist can
    actually act on, with no invented numbers.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from talent_agent_plus import AgentError

CAMPAIGN_CHANNELS = ("social", "radio", "venue", "press", "email", "street")
PRODUCTION_STAGES = ("DEMO", "RECORDING", "MIX", "MASTER", "READY_FOR_APPROVAL")
TASK_KINDS = ("call", "paperwork", "travel", "booking", "merch", "other")


@dataclass
class Campaign:
    campaign_id: str
    artist_id: str
    title: str
    channels: list
    budget_cents: int
    spent_cents: int = 0
    posts: list = field(default_factory=list)  # {"text": ..., "state": DRAFT|READY}


@dataclass
class Production:
    production_id: str
    artist_id: str
    release_id: str
    stage: str = "DEMO"
    notes: list = field(default_factory=list)


@dataclass
class Task:
    task_id: str
    artist_id: str
    kind: str
    title: str
    due_date: str = ""
    done: bool = False


class Promoter:
    def __init__(self, agent):
        self.agent = agent  # TalentAgent core (for expense tracking)
        self.campaigns: dict[str, Campaign] = {}
        self._counter = 0

    def create_campaign(self, artist_id: str, title: str, channels, budget_cents: int) -> Campaign:
        if artist_id not in self.agent.artists:
            raise AgentError(f"unknown artist {artist_id!r}")
        if not title or not title.strip():
            raise AgentError("campaign needs a title")
        for ch in channels:
            if ch not in CAMPAIGN_CHANNELS:
                raise AgentError(f"unknown campaign channel {ch!r}")
        if not isinstance(budget_cents, int) or budget_cents < 0:
            raise AgentError("campaign budget must be a non-negative integer number of cents")
        self._counter += 1
        c = Campaign(f"campaign-{self._counter:04d}", artist_id, title.strip(), list(channels), budget_cents)
        self.campaigns[c.campaign_id] = c
        return c

    def spend(self, campaign_id: str, cents: int, note: str = "") -> Campaign:
        c = self._campaign(campaign_id)
        if not isinstance(cents, int) or cents <= 0:
            raise AgentError("spend must be a positive integer number of cents")
        if c.spent_cents + cents > c.budget_cents:
            raise AgentError(f"campaign cap {c.budget_cents} would be exceeded by spending {cents} (already spent {c.spent_cents})")
        # Recorded as a real expense in the core, so it shows up in the artist's books.
        self.agent.add_expense(c.artist_id, "advertising", cents, note or f"Campaign {c.title}")
        c.spent_cents += cents
        return c

    def draft_post(self, campaign_id: str, text: str) -> dict:
        from talent_agent_plus import gate_text
        c = self._campaign(campaign_id)
        gate_text(text)  # same gate as messages: no tool/control payloads
        post = {"text": text, "state": "DRAFT"}
        c.posts.append(post)
        return post

    def approve_post(self, campaign_id: str, index: int, actor_artist_id: str) -> dict:
        c = self._campaign(campaign_id)
        if actor_artist_id != c.artist_id:
            raise AgentError("only the campaign's artist can approve a post")
        if not 0 <= index < len(c.posts):
            raise AgentError("no such post in this campaign")
        c.posts[index]["state"] = "READY"
        return c.posts[index]

    def _campaign(self, campaign_id: str) -> Campaign:
        if campaign_id not in self.campaigns:
            raise AgentError(f"unknown campaign {campaign_id!r}")
        return self.campaigns[campaign_id]


class Producer:
    def __init__(self, agent):
        self.agent = agent
        self.productions: dict[str, Production] = {}
        self._counter = 0

    def start_production(self, artist_id: str, release_id: str) -> Production:
        if release_id not in self.agent.releases:
            raise AgentError(f"unknown release {release_id!r}")
        rel = self.agent.releases[release_id]
        if rel.artist_id != artist_id:
            raise AgentError("release does not belong to this artist")
        self._counter += 1
        p = Production(f"production-{self._counter:04d}", artist_id, release_id)
        self.productions[p.production_id] = p
        return p

    def advance(self, production_id: str, note: str = "") -> Production:
        p = self._production(production_id)
        idx = PRODUCTION_STAGES.index(p.stage)
        if idx >= len(PRODUCTION_STAGES) - 1:
            raise AgentError("production is already at READY_FOR_APPROVAL; only the artist can approve the release")
        p.stage = PRODUCTION_STAGES[idx + 1]
        if note:
            p.notes.append(note)
        return p

    def note(self, production_id: str, text: str) -> Production:
        p = self._production(production_id)
        p.notes.append(text)
        return p

    def _production(self, production_id: str) -> Production:
        if production_id not in self.productions:
            raise AgentError(f"unknown production {production_id!r}")
        return self.productions[production_id]


class Secretary:
    def __init__(self, agent, store=None, promoter=None):
        self.agent = agent
        self.store = store
        self.promoter = promoter
        self.tasks: dict[str, Task] = {}
        self.drafts: list[dict] = []
        self._counter = 0

    def add_task(self, artist_id: str, kind: str, title: str, due_date: str = "") -> Task:
        if artist_id not in self.agent.artists:
            raise AgentError(f"unknown artist {artist_id!r}")
        if kind not in TASK_KINDS:
            raise AgentError(f"unknown task kind {kind!r}")
        if not title or not title.strip():
            raise AgentError("task needs a title")
        if due_date:
            from talent_agent_plus import _check_date
            _check_date(due_date)
        self._counter += 1
        t = Task(f"task-{self._counter:04d}", artist_id, kind, title.strip(), due_date)
        self.tasks[t.task_id] = t
        return t

    def complete_task(self, task_id: str) -> Task:
        if task_id not in self.tasks:
            raise AgentError(f"unknown task {task_id!r}")
        self.tasks[task_id].done = True
        return self.tasks[task_id]

    def draft_message(self, artist_id: str, title: str, text: str) -> dict:
        """A local draft only. It is NOT sent. Sending is the core's job."""
        from talent_agent_plus import gate_text
        gate_text(text)
        d = {"artist_id": artist_id, "title": title, "text": text, "state": "DRAFT"}
        self.drafts.append(d)
        return d

    def open_tasks(self, artist_id: str) -> list[Task]:
        return sorted((t for t in self.tasks.values() if t.artist_id == artist_id and not t.done),
                      key=lambda t: (t.due_date or "9999", t.title))

    def daily_brief(self, artist_id: str) -> dict:
        if artist_id not in self.agent.artists:
            raise AgentError(f"unknown artist {artist_id!r}")
        brief = {
            "artist_id": artist_id,
            "agenda": [{"booking_id": b.booking_id, "title": b.title, "date": b.date, "state": b.state}
                       for b in self.agent.agenda(artist_id)],
            "open_tasks": [{"task_id": t.task_id, "kind": t.kind, "title": t.title, "due_date": t.due_date}
                           for t in self.open_tasks(artist_id)],
            "active_campaigns": [],
            "low_stock": [],
        }
        if self.promoter:
            brief["active_campaigns"] = [{"campaign_id": c.campaign_id, "title": c.title,
                                          "budget_cents": c.budget_cents, "spent_cents": c.spent_cents}
                                         for c in self.promoter.campaigns.values() if c.artist_id == artist_id]
        if self.store:
            brief["low_stock"] = [{"product_id": p.product_id, "name": p.name, "stock": p.stock}
                                  for p in self.store.low_stock(artist_id)]
        return brief
