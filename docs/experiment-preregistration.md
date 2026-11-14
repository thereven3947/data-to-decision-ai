# Pre-registration: ranked suggested actions in the review flow

Status: **frozen before analysis** (2026-11-13). Results live in [experimentation.md](experimentation.md);
any change to this plan after this commit is listed there under *Deviations*.

> All data are **simulated** by a seeded generator (`d2d experiment simulate`). There are no real
> reviewers, users or production traffic. The simulator knows the true effect; the analysis below
> is written as if it did not, and the truth is used only afterwards to score the methods.

## Question and hypothesis

When a reviewer opens a change story in the review queue, does showing **ranked suggested actions**
(from the category playbook in `config/categories.toml`) raise the share of cases where the reviewer
takes at least one suggested action?

- **H0:** action conversion is the same with and without suggestions.
- **H1 (two-sided test, directional hypothesis):** suggestions raise action conversion.

## Arms

- **Control:** the review screen as today (story, events, category), no suggestions.
- **Treatment:** the same screen plus a ranked list of suggested actions for the suggested category.

## Randomization unit

- **Primary design: reviewer (cluster) randomization**, 50/50 by complete randomization of
  `reviewers = 120` simulated reviewers. Each reviewer sees only one arm for the whole experiment.
- **Comparison design: case randomization**, each case 50/50 independently.

Tradeoff: case randomization has ~10x more effective sample size (no clustering), but every
reviewer sees both arms, so what a reviewer learns from suggestions leaks into their control cases
(**interference / SUTVA violation**) and biases the effect toward zero. Reviewer randomization
avoids that leak at the cost of a design effect `1 + (m - 1) ICC` (m ≈ 140 cases per reviewer),
which CUPED largely recovers because the pre-period covariate explains most between-reviewer
variance. The case design is analyzed only to show the interference bias, never for the decision.

## Metrics

| Role | Metric | Definition (per case) | Test |
|------|--------|-----------------------|------|
| Primary | Action conversion | 1 if at least one playbook action was taken | Two-sided, alpha 0.05 |
| Secondary | Time to resolve | Minutes from opening to resolving the case | Two-sided |
| Guardrail | Wrong-action rate | 1 if an action from the wrong category's playbook was taken | Non-inferiority, margin +1.0 pp |
| Guardrail | False-escalation rate | 1 if an `info`-severity case was escalated | Non-inferiority, margin +1.0 pp |

## Analysis plan

1. **SRM check first** (chi-square on case counts, plus a Welch test on cases logged per reviewer for
   the cluster design), threshold p < 0.001. If it fires, the run is invalid: no effects are read.
2. **Primary estimator:** difference in means of CUPED-adjusted conversion
   (`y - theta (x - mean x)`, x = reviewer's pre-period conversion, theta pooled), with
   **cluster-robust (CR1) standard errors by reviewer** and a t reference with G - 1 df. The raw
   difference in means is reported alongside.
3. **Multiple testing:** Holm over the family {primary (CUPED), time to resolve, wrong-action,
   false-escalation}; Benjamini-Hochberg reported for reference.
4. **Guardrails:** pass if the upper bound of the two-sided 95% CI of the increase is below the margin.
5. **Novelty:** weekly effects and the effect in weeks 3-4 (days 14-27).
6. **Sequential monitoring:** weekly looks (days 7, 14, 21, 28) with two-sided O'Brien-Fleming
   boundaries (overall alpha 0.05). Reported as "would have stopped at look k"; the decision uses
   the full 28 days.
7. **Heterogeneity (exploratory):** EconML `CausalForestDML` on the reviewer-randomized data,
   features = experience, pre-period conversion, device group, severity; out-of-fold by reviewer
   (5 folds); calibration by predicted-effect quintiles and a best-linear-predictor slope.
8. **Confounding demo (separate, observational data):** naive difference vs OLS adjustment,
   cross-fitted AIPW and a causal-forest doubly robust ATE, for two exposures (suggestions; an
   unrelated device-history panel).

## Power

From a pre-period A/A simulation (seed 20261113 + 1000, no treatment): baseline conversion 0.294,
ICC 0.068 (0.006 after CUPED), 140 cases per reviewer, ~8,400 cases per arm, alpha 0.05, power 0.8:

| Randomization | CUPED | Design effect | MDE |
|---|---|---:|---:|
| case | no | 1.00 | 1.97 pp |
| case | yes | 1.00 | 1.91 pp |
| reviewer | no | 10.51 | 6.38 pp |
| reviewer | yes | 1.90 | **2.63 pp** |

The primary analysis (reviewer + CUPED) can detect a lift of about 2.6 pp (9 % relative).

## Decision rule

- **Invalid** if SRM fires.
- **Don't ship** if the primary effect is not significant after Holm (p >= 0.05) or its CI lower
  bound is <= 0.
- **Iterate** if the primary is significant but a guardrail fails, or the weeks 3-4 effect is below
  the practical threshold of **+2.0 pp**.
- **Ship** otherwise.

## Fixed inputs

- Simulator parameters: `config/experiment.toml` as of this commit; seed **20261113**.
- Case templates: stories from `d2d simulate --data-dir data/check && d2d run --data-dir data/check`
  (default config), labeled by the simulated reviewer.
- Command: `uv run --group experiment d2d experiment report --data-dir data/check`.
- Development and tests use other seeds (1, 7, 8); the pre-registered seed is run once.
