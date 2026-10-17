"""Agent runtime: route -> prefetch tools -> LLM tool loop -> grounding check -> answer record."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

from d2d.agent.checker import CheckResult, check_answer
from d2d.agent.provider import Provider, ProviderError, ToolCall
from d2d.agent.skills import Skill, SkillSet
from d2d.agent.tools import TOOL_NAMES, TOOL_SPECS, ToolError, run_tool
from d2d.api.repo import Ontology
from d2d.api.scope import Scope

THINK = re.compile(r"<think>.*?</think>", re.DOTALL)
GROUPED = re.compile(r"\[((?:story:)?[0-9a-f]{16}(?:\s*[,;]\s*(?:story:)?[0-9a-f]{16})+)\]")
UNBRACKETED = re.compile(r"(?<!\[)\bstory:([0-9a-f]{16})\b(?!\])")
TOOL_LEAK = re.compile(
    r"\s*\[(?:list_stories|get_story|get_device_history|list_devices|"
    r"get_category_playbook|get_kpis)[^\]]*\]"
)


def normalize_citations(text: str) -> str:
    """[story:a, story:b] -> [story:a] [story:b]; bare story:<id> -> [story:<id>]."""
    text = GROUPED.sub(
        lambda m: " ".join(
            f"[story:{i.strip().removeprefix('story:')}]" for i in re.split(r"[,;]", m.group(1))
        ),
        text,
    )
    text = UNBRACKETED.sub(r"[story:\1]", text)
    return TOOL_LEAK.sub("", text)


SYSTEM = """You are the D2D household Copilot for a simulated smart home (toy data, no real
devices). You are a read-only explainer.

Rules:
- Use only facts from tool results. Never invent stories, devices, numbers or times.
- Never assign, change or "correct" a category label: report the stored label and its source.
- You cannot execute or approve actions. Suggestions are always "(proposed)".
- Cite every story you mention exactly as [story:<story_id>] (16-character id), one id per
  bracket, e.g. [story:aaaa] [story:bbbb]. Never mention tool names.
- Times in tool results are already in the home's local time; copy them as given.
- If the data does not answer the question, say so and point to the review queue.
- Answer in under 180 words, Markdown allowed, no tables.

Audience: {role}. {tone}

Skill `{skill}`: {instructions}"""


@dataclass
class ToolRecord:
    name: str
    arguments: dict[str, Any]
    ok: bool
    error: str | None = None
    prefetch: bool = False


@dataclass
class AnswerRecord:
    answer: str
    skill: str
    skill_sha: str
    llm_model_tag: str
    tool_calls: list[ToolRecord]
    cited_ids: list[str]
    checker: dict[str, Any]
    latency_ms: int
    tokens_in: int = 0
    tokens_out: int = 0
    citations: dict[str, str] = field(default_factory=dict)


class Agent:
    def __init__(
        self, onto: Ontology, provider: Provider | None, skills: SkillSet, max_tool_calls: int = 6
    ) -> None:
        self.onto = onto
        self.provider = provider
        self.skills = skills
        self.max_tool_calls = max_tool_calls

    def _call(
        self,
        call: ToolCall,
        scope: Scope,
        records: list[ToolRecord],
        outputs: list[Any],
        *,
        prefetch: bool = False,
    ) -> str:
        try:
            if call.name not in TOOL_NAMES:
                raise ToolError(f"unknown tool {call.name!r}")
            result: dict[str, Any] = run_tool(call.name, call.arguments, self.onto, scope)
            records.append(ToolRecord(call.name, call.arguments, True, prefetch=prefetch))
            outputs.append(result)
        except (ToolError, ValueError, TypeError) as exc:
            records.append(ToolRecord(call.name, call.arguments, False, str(exc), prefetch))
            result = {"error": str(exc)}
        return json.dumps(result, default=str, separators=(",", ":"))

    def _check(self, answer: str, outputs: list[Any], scope: Scope) -> CheckResult:
        visible = self.onto.list_stories(scope, limit=100_000)
        hidden = set(self.onto.household.devices) - scope.devices
        return check_answer(
            answer,
            outputs,
            {s["story_id"] for s in visible},
            {s["story_id"]: s["category"] for s in visible},
            hidden,
            set(self.onto.taxonomy.categories),
        )

    def answer(
        self, question: str, scope: Scope, history: list[tuple[str, str]] | None = None
    ) -> AnswerRecord:
        started = time.monotonic()
        skill = self.skills.route(question)
        records: list[ToolRecord] = []
        outputs: list[Any] = []
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": SYSTEM.format(
                    role=scope.role,
                    tone=self.skills.tones.get(scope.role, ""),
                    skill=skill.name,
                    instructions=skill.instructions,
                ),
            }
        ]
        for q, a in (history or [])[-3:]:
            messages += [{"role": "user", "content": q}, {"role": "assistant", "content": a}]
        messages.append({"role": "user", "content": question})
        prefetch_calls = [ToolCall(p["tool"], dict(p.get("args", {}))) for p in skill.prefetch]
        if prefetch_calls:
            messages.append(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"function": {"name": c.name, "arguments": c.arguments}}
                        for c in prefetch_calls
                    ],
                }
            )
            for c in prefetch_calls:
                content = self._call(c, scope, records, outputs, prefetch=True)
                messages.append({"role": "tool", "tool_name": c.name, "content": content})

        tokens_in = tokens_out = 0
        answer, check, model_tag = "", CheckResult(ok=False), "fallback"
        attempts = 0
        if self.provider is not None:
            model_tag = self.provider.model_tag
            try:
                for attempt in (1, 2):
                    attempts = attempt
                    answer, t_in, t_out = self._loop(messages, scope, records, outputs)
                    tokens_in, tokens_out = tokens_in + t_in, tokens_out + t_out
                    check = self._check(answer, outputs, scope)
                    if check.ok:
                        break
                    messages += [
                        {"role": "assistant", "content": answer},
                        {
                            "role": "user",
                            "content": "Your answer failed the grounding check: "
                            + "; ".join(check.errors)
                            + ". Rewrite it using only facts from the tool results.",
                        },
                    ]
            except ProviderError as exc:
                check = CheckResult(ok=False, errors=[str(exc)])
        if not check.ok:
            failed = list(check.errors)
            answer = self._fallback(skill, outputs, failed)
            check = self._check(answer, outputs, scope)
            check.errors = failed
            model_tag = f"{model_tag}+fallback" if self.provider else "fallback"
        visible = {s["story_id"]: s["title"] for s in self.onto.list_stories(scope, limit=100_000)}
        return AnswerRecord(
            answer=answer,
            skill=skill.name,
            skill_sha=skill.sha,
            llm_model_tag=model_tag,
            tool_calls=records,
            cited_ids=check.cited_ids,
            checker={"ok": not check.errors, "errors": check.errors, "attempts": attempts},
            latency_ms=int((time.monotonic() - started) * 1000),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            citations={sid: visible[sid] for sid in check.cited_ids if sid in visible},
        )

    def _loop(
        self,
        messages: list[dict[str, Any]],
        scope: Scope,
        records: list[ToolRecord],
        outputs: list[Any],
    ) -> tuple[str, int, int]:
        assert self.provider is not None
        tokens_in = tokens_out = calls = 0
        while True:
            tools = TOOL_SPECS if calls < self.max_tool_calls else []
            reply = self.provider.chat(messages, tools)
            tokens_in += reply.tokens_in
            tokens_out += reply.tokens_out
            if not reply.tool_calls or not tools:
                answer = normalize_citations(THINK.sub("", reply.content).strip())
                return answer, tokens_in, tokens_out
            messages.append(
                {
                    "role": "assistant",
                    "content": reply.content,
                    "tool_calls": [
                        {"function": {"name": c.name, "arguments": c.arguments}}
                        for c in reply.tool_calls
                    ],
                }
            )
            for c in reply.tool_calls:
                calls += 1
                content = self._call(c, scope, records, outputs)
                messages.append({"role": "tool", "tool_name": c.name, "content": content})

    @staticmethod
    def _fallback(skill: Skill, outputs: list[Any], errors: list[str]) -> str:
        """Templated answer from the tool results (no LLM, always grounded)."""
        stories: dict[str, dict[str, Any]] = {}
        for out in outputs:
            listed = out.get("stories")
            for s in listed if isinstance(listed, list) else []:
                stories.setdefault(s["story_id"], s)
            if "story_id" in out and "title" in out:
                stories.setdefault(out["story_id"], out)
        lines = []
        if errors:
            lines.append("_The assistant could not produce a checked answer, so here is the data._")
        kpis = next((o for o in outputs if "by_status" in o), None)
        if kpis:
            lines.append(
                f"{kpis['stories']} stories, {kpis['open_reviews']} open in the review queue."
            )
        for s in list(stories.values())[:8]:
            label = s.get("category") or "unlabeled"
            lines.append(f"- {s['title']} ({label}, {s['window_start']}) [story:{s['story_id']}]")
        if not stories and not kpis:
            lines.append("I could not find data for that question. Try the review queue.")
        return "\n".join(lines)
