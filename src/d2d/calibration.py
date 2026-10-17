"""Nightly calibration (M11-01): measure, propose, never apply.

``d2d calibrate`` writes a proposal directory; the scheduled ``calibrate`` workflow turns it into a
branch and an issue with a one-click pull request link. Nothing is merged automatically.

1. Linking: grid search over the windows in ``linking.toml`` on scenario ground truth plus human
   "wrong grouping" reports (a reported story must not come back as one story). A new config is
   proposed only if pairwise F1 improves, precision does not drop and no report is violated.
2. Classifier: retrain on the current training set and compare with the deployed model on the
   frozen golden set.
3. Feedback: export new eval items to ``evals/feedback/<set>.jsonl`` and summarize data reports.
"""

from __future__ import annotations

import itertools
import json
import re
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
from sqlalchemy import Engine, func, select, update

from d2d.db import eval_item
from d2d.evaluation import linking_metrics, linking_truth
from d2d.hitl.training_set import read_training_set, training_set_version
from d2d.ml.evaluate import evaluate_classifier
from d2d.ml.train import Classifier, load_meta, save, train
from d2d.pipeline.linking import LinkingConfig, TopologyIndex, build_stories, load_linking
from d2d.pipeline.snapshots import load_snapshots

GRID = {
    "device_minutes": (30, 60, 90),
    "topology_minutes": (15, 30, 45),
    "campaign_hours": (12, 24, 36),
    "recurrence_hours": (6, 12, 24),
}


@dataclass(frozen=True)
class LinkingCandidate:
    device_minutes: int
    topology_minutes: int
    campaign_hours: int
    recurrence_hours: int
    pair_precision: float
    pair_f1: float
    ari: float
    violations: int


def _config(base: LinkingConfig, c: dict[str, int]) -> LinkingConfig:
    return replace(
        base,
        device_window=timedelta(minutes=c["device_minutes"]),
        topology_window=timedelta(minutes=c["topology_minutes"]),
        campaign_window=timedelta(hours=c["campaign_hours"]),
        recurrence_window=timedelta(hours=c["recurrence_hours"]),
    )


def _violations(stories: list[dict[str, Any]], reports: list[set[str]]) -> int:
    """A wrong-grouping report is violated if all its events are still in one story."""
    groups = [set(s["event_ids"]) for s in stories]
    return sum(1 for r in reports if len(r) > 1 and any(r <= g for g in groups))


def search_linking(
    data_dir: Path, linking_path: Path, home_id: str, reports: list[set[str]]
) -> tuple[LinkingCandidate, LinkingCandidate]:
    """(current, best) linking configs by F1, precision and report violations."""
    base = load_linking(linking_path)
    truth = linking_truth(data_dir)
    events = pq.read_table(data_dir / "events" / "events.parquet").to_pylist()
    topology = TopologyIndex(load_snapshots(data_dir))

    def score(c: dict[str, int]) -> LinkingCandidate:
        stories = build_stories(events, topology, _config(base, c), home_id)
        m = linking_metrics(truth, events, stories)
        return LinkingCandidate(
            **c,
            pair_precision=round(m["pair_precision"], 4),
            pair_f1=round(m["pair_f1"], 4),
            ari=round(m["ari"], 4),
            violations=_violations(stories, reports),
        )

    current = score(
        {
            "device_minutes": int(base.device_window.total_seconds() // 60),
            "topology_minutes": int(base.topology_window.total_seconds() // 60),
            "campaign_hours": int(base.campaign_window.total_seconds() // 3600),
            "recurrence_hours": int(base.recurrence_window.total_seconds() // 3600),
        }
    )
    best = current
    for values in itertools.product(*GRID.values()):
        cand = score(dict(zip(GRID, values, strict=True)))
        if cand.violations > best.violations or cand.pair_precision < current.pair_precision:
            continue
        better = (cand.violations, -cand.pair_f1, -cand.ari) < (
            best.violations,
            -best.pair_f1,
            -best.ari,
        )
        if better:
            best = cand
    return current, best


def _proposed_linking(path: Path, best: LinkingCandidate) -> str:
    """The current linking.toml with only the window values replaced (comments kept)."""
    text = path.read_text("utf-8")
    for key in GRID:
        text = re.sub(rf"^({key}\s*=\s*)\d+", rf"\g<1>{getattr(best, key)}", text, flags=re.M)
    return text


def export_eval_items(engine: Engine, out_dir: Path) -> dict[str, list[dict[str, Any]]]:
    with engine.connect() as conn:
        rows = conn.execute(
            select(eval_item).where(eval_item.c.exported_at.is_(None)).order_by(eval_item.c.item_id)
        ).mappings()
        by_set: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            by_set.setdefault(r["set_name"], []).append({"item_id": r["item_id"], **r["payload"]})
    for name, items in by_set.items():
        path = out_dir / "evals" / "feedback" / f"{name}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            for item in items:
                fh.write(json.dumps(item, sort_keys=True, default=str) + "\n")
    return by_set


def mark_exported(engine: Engine, item_ids: list[int]) -> None:
    if item_ids:
        with engine.begin() as conn:
            conn.execute(
                update(eval_item)
                .where(eval_item.c.item_id.in_(item_ids))
                .values(exported_at=func.now())
            )


def calibrate(
    data_dir: Path,
    cfg_dir: Path,
    models_dir: Path,
    golden: Path,
    home_id: str,
    out: Path,
    engine: Engine | None,
) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    items = export_eval_items(engine, out) if engine is not None else {}
    reports = [
        {e["event_id"] for e in (i.get("context") or {}).get("events", [])}
        for i in items.get("linking", [])
    ]
    current, best = search_linking(data_dir, cfg_dir / "linking.toml", home_id, reports)
    linking_changed = best != current
    if linking_changed:
        target = out / "config" / "linking.toml"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_proposed_linking(cfg_dir / "linking.toml", best), "utf-8")

    golden_rows = read_training_set(golden)
    classifier: dict[str, Any] = {}
    deployed = load_meta(models_dir)
    if deployed is not None:
        r = evaluate_classifier(Classifier(models_dir), golden_rows)
        classifier["deployed"] = {
            "model_version": deployed.model_version,
            "coverage": round(r.coverage, 3),
            "min_precision": round(r.min_precision, 3),
        }
    ts_path = data_dir / "labels" / "training_set.jsonl"
    if ts_path.is_file():
        rows = read_training_set(ts_path)
        model, meta = train(rows, training_set_version(rows))
        tmp = out / "models"
        save(model, meta, tmp)
        r = evaluate_classifier(Classifier(tmp), golden_rows)
        classifier["retrained"] = {
            "model_version": meta.model_version,
            "examples": meta.examples,
            "coverage": round(r.coverage, 3),
            "min_precision": round(r.min_precision, 3),
        }

    summary = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "linking": {"current": asdict(current), "best": asdict(best), "changed": linking_changed},
        "classifier": classifier,
        "eval_items": {k: len(v) for k, v in items.items()},
        "item_ids": [i["item_id"] for v in items.values() for i in v],
        "data_reports": items.get("data", []),
        "changed": linking_changed or bool(items),
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=str) + "\n", "utf-8")
    (out / "report.md").write_text(render_report(summary), "utf-8")
    return summary


def render_report(s: dict[str, Any]) -> str:
    cur, best = s["linking"]["current"], s["linking"]["best"]
    lines = [
        f"# Calibration report {s['generated_at'][:10]}",
        "",
        "Generated by `d2d calibrate` on the Mac mini. Nothing is applied until a human merges the",
        "pull request.",
        "",
        "## Linking",
        "",
        "| | device min | topology min | campaign h | recurrence h | precision | F1 | ARI "
        "| report violations |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for name, c in (("current", cur), ("proposed", best)):
        lines.append(
            f"| {name} | {c['device_minutes']} | {c['topology_minutes']} | {c['campaign_hours']} | "
            f"{c['recurrence_hours']} | {c['pair_precision']:.3f} | {c['pair_f1']:.3f} | "
            f"{c['ari']:.3f} | {c['violations']} |"
        )
    lines += [
        "",
        "Proposal: "
        + (
            "update `config/linking.toml`."
            if s["linking"]["changed"]
            else "keep the current config."
        ),
        "",
    ]
    lines += ["## Classifier (frozen golden set)", ""]
    for name, c in s["classifier"].items():
        lines.append(
            f"- **{name}** `{c['model_version']}`: coverage {c['coverage']:.3f}, "
            f"min per-class precision {c['min_precision']:.3f}"
            + (f", {c['examples']} training examples" if "examples" in c else "")
        )
    if not s["classifier"]:
        lines.append("- no model or training set available")
    lines += ["", "## Feedback exported to eval sets", ""]
    if s["eval_items"]:
        lines += [
            f"- `{name}`: {n} item(s) -> `evals/feedback/{name}.jsonl`"
            for name, n in s["eval_items"].items()
        ]
    else:
        lines.append("- no new feedback")
    if s["data_reports"]:
        lines += ["", "## Data reports (wrong / missing data)", ""]
        for d in s["data_reports"]:
            target = d.get("target", {})
            note = d.get("comment") or "no comment"
            lines.append(f"- {target.get('type')} `{target.get('id')}`: {d.get('reason')}, {note}")
    return "\n".join(lines) + "\n"
