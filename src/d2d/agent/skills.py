"""Coded skills (agent/skills/<name>/skill.{toml,md}) and the deterministic intent router."""

from __future__ import annotations

import hashlib
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Skill:
    name: str
    intent: str
    priority: int
    patterns: tuple[re.Pattern[str], ...]
    prefetch: tuple[dict[str, Any], ...]
    instructions: str
    sha: str


@dataclass(frozen=True)
class SkillSet:
    skills: dict[str, Skill]
    tones: dict[str, str]

    def route(self, question: str) -> Skill:
        q = question.lower()
        for skill in sorted(self.skills.values(), key=lambda s: s.priority):
            if any(p.search(q) for p in skill.patterns):
                return skill
        return self.skills["general"]


def load_skills(root: Path) -> SkillSet:
    skills = {}
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        toml_bytes = (d / "skill.toml").read_bytes()
        md = (d / "skill.md").read_text(encoding="utf-8").strip()
        raw = tomllib.loads(toml_bytes.decode("utf-8"))
        skills[d.name] = Skill(
            name=d.name,
            intent=str(raw["intent"]),
            priority=int(raw.get("priority", 50)),
            patterns=tuple(re.compile(p) for p in raw.get("patterns", [])),
            prefetch=tuple(raw.get("prefetch", [])),
            instructions=md,
            sha=hashlib.sha1(toml_bytes + md.encode()).hexdigest()[:12],
        )
    if "general" not in skills:
        raise ValueError(f"{root}: a 'general' skill is required")
    glossary = tomllib.loads((root / "glossary.toml").read_text(encoding="utf-8"))
    return SkillSet(skills, {role: str(v.get("tone", "")) for role, v in glossary.items()})
