"""Role scope (stage 10): which devices, stories and fields a role may see."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from d2d.config import ConfigError, Household, Taxonomy, load_toml

ROLES = ("owner", "family", "energy_advisor", "guest")


@dataclass(frozen=True)
class RolePolicy:
    name: str
    description: str
    can_label: bool = False
    energy_only: bool = False
    hide_rooms: bool = False
    published_only: bool = False
    shared_rooms: tuple[str, ...] | None = None
    hidden_device_tags: tuple[str, ...] = ()


def load_roles(path: Path) -> dict[str, RolePolicy]:
    raw = load_toml(path)
    policies = {}
    for name in ROLES:
        if name not in raw:
            raise ConfigError(f"roles.toml: missing role {name!r}")
        r = raw[name]
        shared = r.get("shared_rooms")
        policies[name] = RolePolicy(
            name=name,
            description=str(r.get("description", "")),
            can_label=bool(r.get("can_label", False)),
            energy_only=bool(r.get("energy_only", False)),
            hide_rooms=bool(r.get("hide_rooms", False)),
            published_only=bool(r.get("published_only", False)),
            shared_rooms=tuple(shared) if shared is not None else None,
            hidden_device_tags=tuple(r.get("hidden_device_tags", ())),
        )
    return policies


@dataclass(frozen=True)
class AccountPolicy:
    max_users: int
    roles: tuple[str, ...]

    def check_new(self, existing: dict[str, str], username: str, role: str) -> None:
        """Raise ConfigError if adding/replacing ``username`` with ``role`` breaks the policy."""
        if role not in self.roles:
            raise ConfigError(
                f"accounts with role {role!r} are not allowed (config/roles.toml [accounts] roles)"
            )
        count = len(existing) + (0 if username in existing else 1)
        if count > self.max_users:
            raise ConfigError(
                f"user limit reached ({self.max_users}); adding users is blocked by "
                "config/roles.toml [accounts] max_users"
            )

    def allows(self, users: dict[str, str], username: str) -> bool:
        """Login check: the file must respect the policy as a whole (fail closed)."""
        if len(users) > self.max_users:
            return False
        return users.get(username) in self.roles


def load_account_policy(path: Path) -> AccountPolicy:
    raw = load_toml(path).get("accounts", {})
    roles = tuple(raw.get("roles", ["owner"]))
    unknown = set(roles) - set(ROLES)
    if unknown:
        raise ConfigError(f"roles.toml [accounts]: unknown roles {sorted(unknown)}")
    return AccountPolicy(max_users=int(raw.get("max_users", 1)), roles=roles)


class Scope:
    def __init__(
        self,
        policy: RolePolicy,
        household: Household,
        taxonomy: Taxonomy,
        username: str | None = None,
    ) -> None:
        self.policy = policy
        self.role = policy.name
        self.username = username
        self._taxonomy = taxonomy
        hidden = set(policy.hidden_device_tags)
        self.devices = {
            d.id
            for d in household.devices.values()
            if not hidden & set(d.tags)
            and (policy.shared_rooms is None or d.room in policy.shared_rooms)
        }

    @property
    def actor(self) -> str:
        """Who did it, for labels and cases: the username, or the role when login is off."""
        return self.username or self.role

    @property
    def event_level(self) -> bool:
        """Roles that only see events through the stories they may see."""
        return self.policy.energy_only or self.policy.published_only

    def device_visible(self, device_id: str) -> bool:
        return device_id in self.devices

    def category_visible(self, category: str | None) -> bool:
        if not self.policy.energy_only:
            return True
        if category is None or category not in self._taxonomy.categories:
            return False
        return self._taxonomy.categories[category].energy

    def story_visible(self, story: dict[str, Any]) -> bool:
        if self.policy.published_only and story["status"] != "published":
            return False
        if not self.category_visible(story.get("category")):
            return False
        return all(self.device_visible(d) for d in story["device_ids"])

    def redact_device(self, device: dict[str, Any]) -> dict[str, Any]:
        if self.policy.hide_rooms:
            return {k: v for k, v in device.items() if k != "room"}
        return device
