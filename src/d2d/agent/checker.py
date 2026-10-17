"""Grounding checker: deterministic post-generation guard (docs/agent-and-skills.md)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

STORY_REF = re.compile(r"\[story:([0-9a-f]{16})\]")
BARE_ID = re.compile(r"\b([0-9a-f]{16})\b")
UNIT_NUMBER = re.compile(
    r"(?:\$\s?(\d+(?:\.\d+)?))|(?:(\d+(?:\.\d+)?)\s?(?:kWh|kW|W|dB|%))", re.IGNORECASE
)
ANY_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
SNAKE = re.compile(r"\b[a-z]+(?:_[a-z0-9]+)+\b")


@dataclass
class CheckResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    cited_ids: list[str] = field(default_factory=list)


def cited_story_ids(answer: str) -> list[str]:
    ids = STORY_REF.findall(answer) + BARE_ID.findall(answer)
    return list(dict.fromkeys(ids))


def check_answer(
    answer: str,
    tool_outputs: list[dict[str, Any]],
    visible_story_ids: set[str],
    story_categories: dict[str, str | None],
    hidden_device_ids: set[str],
    category_ids: set[str],
) -> CheckResult:
    errors: list[str] = []
    cited = cited_story_ids(answer)
    for sid in cited:
        if sid not in visible_story_ids:
            errors.append(f"story {sid} does not exist or is not visible to this user")

    tokens = set(SNAKE.findall(answer)) | set(re.findall(r"`([a-z0-9_]+)`", answer))
    for dev in sorted(tokens & hidden_device_ids):
        errors.append(f"device {dev} is not visible to this user")

    # A category id mentioned next to a cited story must be that story's stored label.
    for sid in cited:
        stored = story_categories.get(sid)
        for m in STORY_REF.finditer(answer):
            if m.group(1) != sid:
                continue
            window = answer[max(0, m.start() - 160) : m.end() + 160]
            for cat in set(SNAKE.findall(window)) & category_ids:
                if stored and cat != stored and not _mentions_other_story(window, sid):
                    errors.append(f"story {sid} is labeled {stored}, not {cat}")

    blob = json.dumps(tool_outputs, default=str)
    known = [float(x) for x in ANY_NUMBER.findall(blob)]
    for m in UNIT_NUMBER.finditer(answer):
        value = float(m.group(1) or m.group(2))
        if not any(abs(value - k) <= max(0.01, 0.01 * abs(k)) for k in known):
            errors.append(f"number {m.group(0).strip()} is not in the tool results")
    return CheckResult(ok=not errors, errors=list(dict.fromkeys(errors)), cited_ids=cited)


def _mentions_other_story(window: str, sid: str) -> bool:
    return any(other != sid for other in STORY_REF.findall(window))
