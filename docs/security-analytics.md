# Security analytics: unexpected configuration changes

D2D already turns every configuration and topology change into a typed change event (stage 3).
`d2d.security` reads the same events from an OT-security angle: **was this change expected and
authorized, or does it deviate from the approved configuration?** This is the change-control idea
applied to control-system integrity (compare NERC CIP-010 configuration change management).

```bash
uv run d2d security scan --data-dir data          # score changes, write data/security/alerts.parquet
uv run d2d eval security --data-dir data          # red-team eval with gates (also in CI)
```

## How a change is scored

Only `config` and `topology` events are scored. The household inventory
([`config/household.toml`](../config/household.toml)) is the **approved baseline**, like an asset
inventory or CMDB.

| Evidence | Weight | Meaning |
|---|---|---|
| `firmware_downgrade` | +0.60 | New firmware version is older than the old one (rollback to a vulnerable build) |
| `unknown_network` | +0.60 | Device joined a network that is not an upstream in the inventory (rogue access point) |
| `unknown_circuit` | +0.60 | Device moved to a circuit that is not in the inventory |
| `out_of_range` | +0.50 | Numeric value is more than 25% beyond both the approved value and everything this device has reported (setpoint or safety-limit manipulation) |
| `undersized_circuit` | +0.40 | Device moved to a smaller circuit whose continuous capacity (80% of rating × voltage) cannot carry its rated load |
| `off_hours` | +0.15 | 00:00–04:59 local time, unless part of a campaign |
| `lone_change` | +0.10 | No other device changed within 30 minutes |
| `rare_property` | +0.10 | This device has changed this property only once |
| `campaign` | −0.30 | Other devices changed the same property to the same value within 3 hours (a planned rollout or maintenance window) |
| Isolation Forest | up to +0.25 | Unsupervised score over time-of-day and the indicators above |

A score of **0.5 or more is an alert**. Every alert carries its reasons, so an analyst can see why.

## Evaluation: red-team injection

The simulator and its ground truth stay untouched. `evaluate_security` takes a real event stream and,
for each trial, injects one attack of each type at a random time on a random active device:

| Attack | What is injected |
|---|---|
| `firmware_downgrade` | Firmware set to an old `0.x.y` build |
| `rogue_network` | `network_id` set to an unknown access point |
| `setpoint_manipulation` | Setpoint raised 25–40 units above the approved value |
| `safety_limit_raised` | A numeric safety setting raised 40–80% |
| `unscheduled_circuit_move` | Device moved to another *existing* circuit |

The stream also contains realistic **hard negatives** that must not alert: a 03:00 firmware rollout,
a fleet-wide network move, a 60 → 70 °C water-heater limit change, a child lock turned off and an EV
charger moved between two 50 A circuits.

Default data, 25 trials (125 attacks):

| Metric | Value | CI gate |
|---|---|---|
| precision@k (k = attacks injected) | 0.87 | ≥ 0.80 |
| recall at the alert threshold | 0.86 | ≥ 0.80 |
| false alerts per trial | 0.00 | ≤ 1.0 |
| recall: firmware downgrade / rogue network / setpoint | 1.00 / 1.00 / 1.00 | |
| recall: safety limit raised | 0.92 | |
| recall: move to an existing circuit | 0.36 | |

**Known limit.** A move to an existing circuit that can carry the device looks like a legitimate
topology change; only timing and rarity hint at it. Closing that gap needs authorization context, for
example matching each topology change to an approved work order, which the simulator does not model.

## Where it fits

- `data/security/alerts.parquet` is exported to the analytics warehouse as `fact_security_alert`
  (see [analytics-warehouse.md](analytics-warehouse.md)).
- The same change-event stream feeds both the change stories (what happened and why) and this
  integrity view (should it have happened at all).
