"""Notification stories (M5-05): severity per category, quiet hours, one push per story.

Runs after ``d2d classify`` on every pipeline cycle. Each published story gets exactly one row in
``notify.notification``. ``info`` stories only go to the digest; ``notice`` and ``alert`` stories
are pushed through ntfy when it is configured (``D2D_NTFY_URL`` + ``D2D_NTFY_TOPIC``), otherwise
recorded. ``notice`` waits for quiet hours to end; ``alert`` does not. The first run on an
environment backfills existing stories without pushing, so turning this on never floods a phone.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import Engine, func, select, update
from sqlalchemy.dialects.postgresql import insert

from d2d.api.repo import Ontology
from d2d.api.scope import Scope
from d2d.config import ConfigError, load_toml
from d2d.db import SEVERITIES, notification
from d2d.enrichment import in_window


@dataclass(frozen=True)
class NotifyConfig:
    severity: dict[str, str]
    default: str
    quiet_hours: tuple[str, str]
    priority: dict[str, int]
    max_push_per_run: int


def load_notify(path: Path) -> NotifyConfig:
    raw = load_toml(path)
    severity = {str(k): str(v) for k, v in raw.get("severity", {}).items()}
    default = str(raw.get("default", "info"))
    for value in (*severity.values(), default):
        if value not in SEVERITIES:
            raise ConfigError(f"notifications.toml: unknown severity {value!r}")
    window = raw.get("quiet_hours", {}).get("window", ["22:00", "07:00"])
    return NotifyConfig(
        severity=severity,
        default=default,
        quiet_hours=(str(window[0]), str(window[1])),
        priority={str(k): int(v) for k, v in raw.get("ntfy", {}).get("priority", {}).items()},
        max_push_per_run=int(raw.get("max_push_per_run", 5)),
    )


def decide(severity: str, quiet: bool, first_run: bool) -> tuple[str, str]:
    """(channel, initial status) of a new notification."""
    channel = "digest" if severity == "info" else "push"
    if first_run:
        return channel, "backfill"
    if channel == "digest":
        return channel, "recorded"
    return channel, "deferred" if severity == "notice" and quiet else "pending"


class Sender(Protocol):
    def send(self, title: str, body: str, priority: int, tags: list[str]) -> None: ...


class NtfySender:
    def __init__(self, url: str, topic: str, token: str | None = None) -> None:
        self.endpoint = f"{url.rstrip('/')}/{topic}"
        self.headers = {"Authorization": f"Bearer {token}"} if token else {}

    def send(self, title: str, body: str, priority: int, tags: list[str]) -> None:
        resp = httpx.post(
            self.endpoint,
            content=body.encode(),
            headers={
                **self.headers,
                "Title": title.encode("ascii", "replace").decode(),
                "Priority": str(priority),
                "Tags": ",".join(tags),
            },
            timeout=10,
        )
        resp.raise_for_status()


def sender_from_env(env: Mapping[str, str] | None = None) -> NtfySender | None:
    env = os.environ if env is None else env
    url, topic = env.get("D2D_NTFY_URL"), env.get("D2D_NTFY_TOPIC")
    if not url or not topic:
        return None
    return NtfySender(url, topic, env.get("D2D_NTFY_TOKEN") or None)


@dataclass(frozen=True)
class NotifySummary:
    new: int
    sent: int
    deferred: int
    recorded: int
    failed: int
    backfill: int


TAGS = {"alert": ["rotating_light"], "notice": ["house"], "info": ["memo"]}


def run_notifications(
    onto: Ontology,
    owner: Scope,
    cfg: NotifyConfig,
    sender: Sender | None,
    now: datetime | None = None,
) -> NotifySummary:
    """Create one notification per new published story, then push what is due.

    Status flow: ``pending`` (push due) / ``deferred`` (notice in quiet hours) -> ``sent`` or
    ``failed`` (retried up to 3 attempts) or ``recorded`` (no push channel configured).
    ``info`` stories are ``recorded`` for the digest; the first run marks stories ``backfill``.
    """
    engine: Engine = onto.engine
    now = now or datetime.now(UTC)
    quiet = in_window(
        now.astimezone(ZoneInfo(onto.household.timezone)).strftime("%H:%M"), cfg.quiet_hours
    )
    with engine.connect() as conn:
        known = set(conn.execute(select(notification.c.story_id)).scalars())
    first_run = not known
    stories = onto.list_stories(owner, status="published", limit=10_000)
    counts = dict.fromkeys(("new", "sent", "deferred", "recorded", "failed", "backfill"), 0)

    with engine.begin() as conn:
        for s in stories:
            if s["story_id"] in known:
                continue
            severity = cfg.severity.get(s["category"] or "", cfg.default)
            channel, status = decide(severity, quiet, first_run)
            conn.execute(
                insert(notification)
                .values(
                    story_id=s["story_id"],
                    severity=severity,
                    channel=channel,
                    status=status,
                    title=s["title"],
                    body=s["summary"],
                )
                .on_conflict_do_nothing()
            )
            counts["new"] += 1
            if status in counts:
                counts[status] += 1

    with engine.connect() as conn:
        due = [
            dict(r)
            for r in conn.execute(
                select(notification)
                .where(notification.c.channel == "push")
                .where(notification.c.status.in_(("pending", "deferred", "failed")))
                .where(notification.c.attempts < 3)
                .order_by(notification.c.created_at.desc())
            ).mappings()
        ]
    pushed = 0
    for n in due:
        if n["severity"] == "notice" and quiet:
            if n["status"] == "pending":
                _mark(engine, n["story_id"], "deferred", attempt=False)
            continue
        if sender is None:
            _mark(engine, n["story_id"], "recorded", attempt=False)
            counts["recorded"] += 1
            continue
        if pushed >= cfg.max_push_per_run:
            break
        try:
            sender.send(
                n["title"], n["body"], cfg.priority.get(n["severity"], 3), TAGS[n["severity"]]
            )
        except httpx.HTTPError as exc:
            _mark(engine, n["story_id"], "failed", error=str(exc)[:500])
            counts["failed"] += 1
        else:
            _mark(engine, n["story_id"], "sent", sent=True)
            counts["sent"] += 1
            pushed += 1
    return NotifySummary(**counts)


def _mark(
    engine: Engine,
    story_id: str,
    status: str,
    *,
    error: str | None = None,
    sent: bool = False,
    attempt: bool = True,
) -> None:
    values: dict[str, Any] = {"status": status, "error": error}
    if attempt:
        values["attempts"] = notification.c.attempts + 1
    if sent:
        values["sent_at"] = func.now()
    with engine.begin() as conn:
        conn.execute(update(notification).where(notification.c.story_id == story_id).values(values))
