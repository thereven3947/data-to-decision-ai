"""Story features: sparse indicator tokens from events, device kinds and link reasons."""

from __future__ import annotations

from typing import Any

FEATURE_VERSION = "1"


def _direction(old: str | None, new: str | None) -> str | None:
    try:
        a, b = float(old or ""), float(new or "")
    except ValueError:
        return None
    return "up" if b > a else "down" if b < a else None


def features(example: dict[str, Any]) -> dict[str, float]:
    f: dict[str, float] = {}
    events = example["events"]
    for e in events:
        prop_root = e["property"].split(".")[0]
        f[f"tag={e['semantic_tag']}"] = 1.0
        f[f"kind={e['change_kind']}"] = 1.0
        f[f"prop={prop_root}"] = 1.0
        f[f"device_kind={e['device_kind']}"] = 1.0
        f[f"tag|device_kind={e['semantic_tag']}|{e['device_kind']}"] = 1.0
        if direction := _direction(e.get("old_value"), e.get("new_value")):
            f[f"dir={prop_root}:{direction}"] = 1.0
    for r in example["link_reasons"]:
        f[f"reason={r}"] = 1.0
    devices = {e["device_id"] for e in events}
    f[f"n_devices={min(len(devices), 4)}"] = 1.0
    f[f"n_events={min(len(events), 6)}"] = 1.0
    return f
