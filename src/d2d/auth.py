"""Login: scrypt password hashes in a users file outside git, plus signed session cookies.

The users file (default ``~/d2d/<env>/users.toml``, mode 600) stores only salted scrypt hashes; the
password itself is never written anywhere. Sessions are HMAC-signed with ``D2D_SECRET_KEY`` and
include a fingerprint of the user's hash, so changing a password or removing a user ends their
sessions immediately.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import tempfile
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from d2d.config import ConfigError, load_toml

USERNAME = re.compile(r"^[a-z0-9][a-z0-9_.-]{1,31}$")
MIN_PASSWORD = 12
_N, _R, _P, _DKLEN = 2**15, 8, 1, 32
COOKIE = "d2d_session"


class AuthError(ValueError):
    """Invalid username, password or users file."""


# --- password hashing -------------------------------------------------------------------------
def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def hash_password(password: str) -> str:
    if len(password) < MIN_PASSWORD:
        raise AuthError(f"password must be at least {MIN_PASSWORD} characters")
    salt = secrets.token_bytes(16)
    key = hashlib.scrypt(
        password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=_DKLEN, maxmem=64 * 1024 * 1024
    )
    return f"scrypt${_N}${_R}${_P}${_b64(salt)}${_b64(key)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algo, n, r, p, salt, key = encoded.split("$")
        if algo != "scrypt":
            return False
        expected = _unb64(key)
        got = hashlib.scrypt(
            password.encode(),
            salt=_unb64(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
            maxmem=64 * 1024 * 1024,
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, expected)


def fingerprint(encoded_hash: str) -> str:
    return hashlib.sha256(encoded_hash.encode()).hexdigest()[:16]


# --- users file -------------------------------------------------------------------------------
@dataclass(frozen=True)
class User:
    username: str
    role: str
    password: str  # scrypt hash, never the password
    created: str = ""


class UsersFile:
    """``[users.<name>]`` tables with role + password hash. Reloaded when the file changes."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._mtime: float | None = None
        self._users: dict[str, User] = {}

    def users(self) -> dict[str, User]:
        try:
            mtime = self.path.stat().st_mtime
        except FileNotFoundError:
            self._mtime, self._users = None, {}
            return {}
        if mtime != self._mtime:
            raw = load_toml(self.path).get("users", {})
            self._users = {
                name: User(name, str(u["role"]), str(u["password"]), str(u.get("created", "")))
                for name, u in raw.items()
            }
            self._mtime = mtime
        return dict(self._users)

    def get(self, username: str) -> User | None:
        return self.users().get(username)

    def write(self, users: dict[str, User]) -> None:
        lines = [
            "# Change Control users: password hashes only (scrypt). Managed by `d2d user`.",
            "# Keep this file outside git with mode 600.",
            "",
        ]
        for u in sorted(users.values(), key=lambda u: u.username):
            lines += [
                f"[users.{u.username}]",
                f'role = "{u.role}"',
                f'password = "{u.password}"',
                f'created = "{u.created}"',
                "",
            ]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".users.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write("\n".join(lines))
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        self._mtime = None

    def set_user(self, username: str, role: str, password: str, roles: tuple[str, ...]) -> User:
        if not USERNAME.match(username):
            raise AuthError("username: 2-32 chars, lowercase letters, digits, '.', '_' or '-'")
        if role not in roles:
            raise AuthError(f"role must be one of {', '.join(roles)}")
        users = self.users()
        created = users[username].created if username in users else _now()
        user = User(username, role, hash_password(password), created)
        users[username] = user
        self.write(users)
        return user

    def remove(self, username: str) -> None:
        users = self.users()
        if username not in users:
            raise AuthError(f"no user {username!r}")
        del users[username]
        self.write(users)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def users_file_for(env: str | None, users_file: Path | None) -> Path:
    if users_file is not None:
        return users_file.expanduser()
    if env is not None:
        if env not in ("staging", "production"):
            raise ConfigError("env must be staging or production")
        return Path(os.environ.get("D2D_HOME") or Path.home() / "d2d") / env / "users.toml"
    if from_env := os.environ.get("D2D_USERS_FILE"):
        return Path(from_env).expanduser()
    raise ConfigError("pass --env staging|production or --users-file PATH (or set D2D_USERS_FILE)")


# --- sessions ---------------------------------------------------------------------------------
class SessionSigner:
    def __init__(self, secret: str, ttl_seconds: int) -> None:
        if len(secret) < 16:
            raise ConfigError("D2D_SECRET_KEY must be at least 16 characters")
        self._key = hashlib.sha256(("d2d-session:" + secret).encode()).digest()
        self.ttl = ttl_seconds

    def _sig(self, payload: str) -> str:
        return _b64(hmac.new(self._key, payload.encode(), hashlib.sha256).digest())

    def issue(self, username: str, fp: str, now: float | None = None) -> str:
        exp = int((now or time.time()) + self.ttl)
        payload = _b64(json.dumps({"u": username, "f": fp, "e": exp}).encode())
        return f"{payload}.{self._sig(payload)}"

    def verify(self, token: str, now: float | None = None) -> tuple[str, str] | None:
        try:
            payload, sig = token.split(".")
        except ValueError:
            return None
        if not hmac.compare_digest(sig, self._sig(payload)):
            return None
        try:
            data = json.loads(_unb64(payload))
        except ValueError:
            return None
        if data.get("e", 0) < (now or time.time()):
            return None
        return str(data["u"]), str(data["f"])


class LoginLimiter:
    """At most ``max_failures`` failed logins per key (username and client) per ``window``."""

    def __init__(self, max_failures: int = 5, window_s: int = 900) -> None:
        self.max = max_failures
        self.window = window_s
        self._fails: dict[str, deque[float]] = defaultdict(deque)

    def _prune(self, key: str, now: float) -> deque[float]:
        q = self._fails[key]
        while q and q[0] < now - self.window:
            q.popleft()
        return q

    def blocked(self, *keys: str, now: float | None = None) -> bool:
        t = now or time.time()
        return any(len(self._prune(k, t)) >= self.max for k in keys)

    def fail(self, *keys: str, now: float | None = None) -> None:
        t = now or time.time()
        for k in keys:
            self._prune(k, t).append(t)

    def reset(self, *keys: str) -> None:
        for k in keys:
            self._fails.pop(k, None)


@dataclass
class AuthConfig:
    users: UsersFile
    signer: SessionSigner
    limiter: LoginLimiter


def auth_from_env(env: dict[str, str] | None = None) -> AuthConfig | None:
    """Login is on unless D2D_AUTH=off (local development only)."""
    env = dict(os.environ) if env is None else env
    if env.get("D2D_AUTH", "on").lower() == "off":
        return None
    secret = env.get("D2D_SECRET_KEY") or ""
    if not secret:
        # Dev fallback: sessions end when the server restarts.
        secret = secrets.token_hex(32)
    users = Path(env.get("D2D_USERS_FILE") or Path.cwd() / "users.toml").expanduser()
    hours = float(env.get("D2D_SESSION_HOURS") or 12)
    return AuthConfig(UsersFile(users), SessionSigner(secret, int(hours * 3600)), LoginLimiter())
