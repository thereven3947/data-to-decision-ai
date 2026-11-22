# Decision memo: ranked suggested actions in the review queue

**Decision: ITERATE. Do not ship as is; fix the wrong-action guardrail and re-test.**
Pre-registered rule; simulated experiment, seed 20261113. *All data are simulated; no real reviewers
or production traffic.* Details: [experimentation.md](experimentation.md) ·
[pre-registration](experiment-preregistration.md).

## What we tested

We randomized 120 reviewers 50/50 for 28 days (16,629 cases). The treatment arm sees a ranked list
of suggested actions for the story's category; the control arm sees today's screen.

## What happened

| Metric | Control | Treatment | Effect (95 % CI) | Verdict |
|---|---:|---:|---:|---|
| Action conversion (primary, CUPED) | 29.5 % | 37.2 % | **+7.6 pp** [+5.4, +9.8], Holm p = 1e-9 | Win |
| Conversion, weeks 3–4 only | | | +7.9 pp [+5.5, +10.3] | Holds after novelty |
| Time to resolve | 22.5 min | 21.8 min | -0.7 min [-2.6, +1.1] | No change |
| Wrong-action rate (guardrail, margin +1 pp) | 1.27 % | 2.29 % | **+1.0 pp** [+0.5, +1.5] | **Fail** |
| False escalation of `info` cases (guardrail) | 5.71 % | 4.46 % | -1.3 pp [-1.9, -0.6] | Pass (improves) |

Trust checks: no sample-ratio mismatch (p = 0.61). The analysis used cluster-robust errors because
naive errors gave a 52 % false-positive rate in A/A tests. Peeking was controlled with O'Brien-Fleming
boundaries.

## Why "iterate", not "ship"

The lift is large and durable: about 26 % more cases end in an action (+7.6 pp on 29.5 %). It also costs
about one extra **wrong** action per 100 cases, nearly double the control rate. We cannot rule out
an increase of up to 1.5 pp, which is beyond the 1 pp we agreed to tolerate. A wrong action on a
device is worse than no action, so the pre-registered rule says iterate.

## Recommended next step

1. Show suggestions only when the classifier is confident in the category, and label low-confidence
   suggestions "check category first". In this simulation, the extra wrong actions come from
   reviewers following suggestions made for the wrong category, so this change targets the cause.
2. Re-run a reviewer-randomized test of the revised design, sized for the guardrail. Non-inferiority
   at a 1 pp margin is the binding constraint, not the primary metric.
3. Segment results (exploratory) show the largest gains for junior reviewers (forest estimate ~11 pp vs
   ~5 pp for senior). If the guardrail stays tight, a junior-only rollout is a fallback option.

## What would change this decision

- The revised design passes the wrong-action guardrail with the conversion lift above the 2 pp
  practical threshold → **ship**.
- The lift falls below 2 pp in weeks 3–4 of the re-test → **don't ship**.
