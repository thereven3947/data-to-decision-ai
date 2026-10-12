# Feature roadmap

Milestones follow the pipeline stages. Each one ends with something demoable; issues live in
[planning/backlog.toml](../planning/backlog.toml).

| Milestone | Stages | Scope | Exit criteria | Status |
|-----------|--------|-------|---------------|--------|
| **M0 · Foundations** | — | uv project, `d2d` CLI, ruff/mypy/pytest, CI, config loading | `make check` green locally and in CI | ✅ done |
| **M1 · Simulator** | 1 | Household topology, baseline device model, 12 scenario injectors, Parquet snapshots, ground truth JSONL | `d2d simulate` is deterministic for a seed; every scenario visible in snapshots | ✅ done |
| **M2 · Changelog & events** | 2–3 | Typed diff, `semantics.toml` rules, data-quality rules, change events, `d2d run`, `d2d eval changelog` | Event recall 1.0 and DQ recall ≥ 0.95 on ground truth; no noise events | in progress: M2-01 done; next M2-02 (data-quality rules) |
| **M3 · Event linking** | 4 | Graph linking by device / topology / campaign / window, `linking.toml` | Pairwise F1 and ARI vs scenarios reported; baseline committed | |
| **M4 · Mac mini & Postgres** | — | Mac mini setup, OrbStack Postgres, Alembic, self-hosted runner, deploy workflow, Cloudflare Tunnel | Push to `main` deploys to the Mac mini after an approval click | |
| **M5 · HITL review** | 5–6 | Review queue, cases, labeled training set with provenance, simulated reviewer | Stories labeled through the UI/CLI and versioned | |
| **M6 · ML classifier** | 7 | Features, calibrated classifier, per-class thresholds, abstain | Precision ≥ 0.90 per class on golden set | |
| **M7 · Enrichment & ontology** | 8–9 | Templated descriptions, impact calculators, ontology tables | 100 % numeric correctness on golden stories | |
| **M8 · Ontology API & UI** | 10 | FastAPI read API, permissions, Change Control web UI | Each role sees only its scope (tests) | |
| **M9 · Copilot agent** | 11 | Ollama provider, read-only tools, skills, router, grounding checker | Grounding ≥ 0.98 on golden conversations | |
| **M10 · Feedback, calibration & KPIs** | 12 | Feedback capture/routing, nightly calibration PRs, KPI dashboard | One full loop: feedback → calibration PR → merged → better metric | |

M4 is placed right after the first data-plane slices so the Mac mini (arriving 2026-10-15) runs the
pipeline on a schedule as early as possible.
