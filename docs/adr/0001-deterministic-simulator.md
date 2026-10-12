# ADR 0001 · Deterministic simulator as the first data source

Status: accepted (2026-10-11)

## Context

Every later stage (changelog, linking, ML, enrichment, agent) needs labeled inputs to be scored.
Home Assistant data has no ground truth and is not available until the Mac mini is set up.

## Decision

- Stage 1 is a seeded simulator (`d2d simulate`) driven by `config/household.toml`.
- Scenarios are planted by code (`src/d2d/simulator/scenarios.py`); each action records the
  `(device_id, property, ts)` keys it changed into `data/ground_truth/scenarios.jsonl`.
- Run ids are derived from the snapshot timestamp (`run_YYYYMMDDTHHMMSSZ`) instead of ULIDs so a
  seeded simulation is byte-for-byte replayable.
- Snapshots carry two columns beyond the domain-model schema: `settings` (JSON, safety settings)
  and `reported_ts` (device clock, used by the clock-skew data-quality rule).
- Spare devices (`active = false`) let replacement and new-device scenarios stay config-driven.

## Consequences

Stages can be evaluated in CI without any external system. A Home Assistant adapter can be added
later behind the same snapshot schema.
