# Copilot agent and skills (stage 11)

## Contract: what the LLM may and may not do

| The LLM **may** | The LLM **must not** |
|-----------------|----------------------|
| Read published stories, labels, enrichment, playbooks and history through tools | Assign, change or "correct" a category label |
| Translate labels and codes into plain language for the asking stakeholder | Invent stories, devices, numbers or timestamps |
| Summarize, compare and answer "why / what / so what" questions | Write to the ontology or execute actions |
| *Propose* action items, each linked to a playbook step and story IDs | Mark action items as approved |
| Say "I don't know" and point to the review queue | See data outside the user's permissions |

Labels come from humans (stage 5) or the ML classifier (stage 7). The agent is a **reader and explainer**,
which keeps it cheap, auditable and replaceable.

## Runtime

- **Provider switch:** `config/llm.toml` selects an OpenAI-compatible endpoint.
  - Default: **Ollama on the Mac mini**, `qwen3:14b` (32 GB unified memory). Fallback `qwen3:8b` for speed.
  - Optional: Claude (Anthropic API, own key, monthly spend cap) for comparison runs and as an eval judge.
- **Context strategy:** label-grounded retrieval. Tools return compact, structured JSON filtered by
  category / device / time / case — no vector search needed for core questions. A small embedding index
  (`nomic-embed-text`) over playbooks and case notes is an optional later add-on.
- **Tool loop:** max 6 tool calls per turn, 8k-token context budget, streaming responses (SSE).

## Read-only tools

| Tool | Returns |
|------|---------|
| `list_stories(filters)` | Story summaries plus `total_matching`, `by_category` and `data_covers`; filters: status, category, device, `last_days`, `since`, `until` |
| `get_story(story_id)` | Story + linked events + enrichment + label provenance |
| `get_device_history(device_id, since)` | Recent events and stories for a device |
| `get_category_playbook(category)` | Definition, plain-language glossary, checks, recommended actions |
| `get_case(case_id)` | Case, linked stories, action items and their status |
| `get_kpis(period)` | Time-to-detect / diagnose / action, abstain rate, precision |
| `get_feedback_summary(target)` | Prior corrections and complaints for a story or skill |

All tools run under the caller's permission scope (see [domain model](domain-model.md#roles-and-permissions-stage-10)).

## Coded skills

Skills are versioned Markdown + TOML files under `agent/skills/`. Each skill declares its intent,
required tools, output template and rubric.

```
agent/skills/
  explain_story/       skill.md  skill.toml  examples.jsonl
  household_briefing/  ...
  device_history/
  why_question/
  action_items/
  glossary.toml        # label / code → plain-language terms per audience
```

| Skill | Trigger | Output |
|-------|---------|--------|
| `explain_story` | "What happened with X?" | Plain-language what / when / which devices / impact / confidence + source |
| `household_briefing` | "Brief me on today/this week" | Leadership-style summary: top stories, risks, open cases, actions to confirm |
| `device_history` | "What's been going on with the washer?" | Timeline narrative with story citations |
| `why_question` | "Why did the dishwasher run at 2 a.m.?" | Causal explanation grounded in linked events + category playbook |
| `action_items` | "What should I do?" | Proposed items, each tied to a playbook step and story IDs, status `proposed` |
| `about` | "Are you a robot?", "Who are you?" | Honest description of the Copilot: an AI agent on a local model, read-only tools, fact-checked |

Every answer in the UI is labeled with its source: **🤖 Live AI answer** (written by the local
model), **📋 Data summary, no AI** (templated fallback when the model is unavailable or its answer
fails the grounding checker), or **📼 Recorded AI answer** (replayed in the static demo).

Audience adaptation uses `glossary.toml`, for example `load_shift` →
*"the dishwasher's schedule was moved to cheaper overnight hours"* for `family`, and
*"2.1 kWh moved from peak to off-peak, est. $0.38/day saved"* for `energy_advisor`.

## Time ranges

Questions with a period ("past 15 days", "last week", "on October 2", "between Oct 1 and Oct 2")
are parsed deterministically (`d2d.agent.timefilter`) and the matching `list_stories` filter is
prefetched, so counts for a period come from the database, not from the model. "Past N days"
counts back from the end of the data; if the period is longer than the data covers, the tool
returns a `coverage_note` and the Copilot says which period the simulated data covers.

## Intent routing

Deterministic first, LLM second: a keyword/regex + small classifier router picks a skill; the LLM is only
asked to choose when the router is unsure. The chosen skill and its version are logged with every answer.

## Grounding checker (post-generation guard)

Every answer passes a deterministic checker before it is shown:

1. Every story/case/device ID cited exists and is visible to the user.
2. Every category mentioned matches the stored label (no relabeling).
3. Numbers (kWh, $, counts, times) match tool outputs within tolerance.
4. Action items reference an existing playbook step.

On failure: one regeneration with the checker's error message; on second failure, return the
structured tool output with a templated explanation and log a `grounding_failure` for calibration.

## Answer record

Each answer is stored with `session_id, user_role, skill, skill_sha, llm_model_tag, tool_calls,
cited_ids, checker_result, latency_ms, tokens_in/out`, so feedback can be routed to the right layer.
