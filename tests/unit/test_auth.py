from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
from typer.testing import CliRunner

from d2d.api.scope import ROLES, AccountPolicy, load_account_policy
from d2d.auth import (
    AuthError,
    LoginLimiter,
    SessionSigner,
    UsersFile,
    auth_from_env,
    fingerprint,
    hash_password,
    users_file_for,
    verify_password,
)
from d2d.cli import app
from d2d.config import ConfigError

PW = "correct horse battery"
ROOT = Path(__file__).resolve().parents[2]


def test_hash_and_verify() -> None:
    h = hash_password(PW)
    assert h.startswith("scrypt$") and PW not in h
    assert verify_password(PW, h)
    assert not verify_password(PW + "!", h)
    assert hash_password(PW) != h  # salted
    assert not verify_password(PW, "garbage") and not verify_password(PW, "md5$x$y$z$a$b")
    with pytest.raises(AuthError):
        hash_password("too short")


def test_session_tokens() -> None:
    s = SessionSigner("x" * 32, ttl_seconds=60)
    token = s.issue("alice", "fp1", now=1000)
    assert s.verify(token, now=1030) == ("alice", "fp1")
    assert s.verify(token, now=1061) is None  # expired
    payload, sig = token.split(".")
    assert s.verify(payload + "." + sig[:-2] + "AA", now=1030) is None  # tampered
    assert SessionSigner("y" * 32, 60).verify(token, now=1030) is None  # other key
    assert s.verify("nonsense", now=1030) is None
    with pytest.raises(ConfigError):
        SessionSigner("short", 60)


def test_limiter() -> None:
    lim = LoginLimiter(max_failures=3, window_s=60)
    for _ in range(3):
        assert not lim.blocked("user:a", now=100)
        lim.fail("user:a", "ip:1", now=100)
    assert lim.blocked("user:a", now=120) and lim.blocked("ip:1", now=120)
    assert not lim.blocked("user:a", now=200)  # window passed
    lim.fail("user:b", now=300)
    lim.reset("user:b")
    assert not lim.blocked("user:b", now=300)


def test_users_file_is_private_and_holds_hashes_only(tmp_path: Path) -> None:
    f = UsersFile(tmp_path / "env" / "users.toml")
    alice = f.set_user("alice", "owner", PW, ROLES)
    assert stat.S_IMODE(os.stat(f.path).st_mode) == 0o600
    assert PW not in f.path.read_text()
    assert f.get("alice") == alice and verify_password(PW, alice.password)
    old_fp = fingerprint(alice.password)
    f.set_user("alice", "owner", PW + "2", ROLES)
    assert fingerprint(f.get("alice").password) != old_fp  # type: ignore[union-attr]
    for bad in [("Alice!", "owner"), ("bob", "admin")]:
        with pytest.raises(AuthError):
            f.set_user(*bad, PW, ROLES)
    f.remove("alice")
    assert f.users() == {}
    with pytest.raises(AuthError):
        f.remove("alice")


def test_users_file_location(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("D2D_HOME", str(tmp_path))
    assert users_file_for("staging", None) == tmp_path / "staging" / "users.toml"
    assert users_file_for(None, Path("/x/u.toml")) == Path("/x/u.toml")
    monkeypatch.delenv("D2D_USERS_FILE", raising=False)
    with pytest.raises(ConfigError):
        users_file_for(None, None)
    with pytest.raises(ConfigError):
        users_file_for("dev", None)


def test_auth_from_env(tmp_path: Path) -> None:
    assert auth_from_env({"D2D_AUTH": "off"}) is None
    cfg = auth_from_env(
        {
            "D2D_SECRET_KEY": "k" * 40,
            "D2D_USERS_FILE": str(tmp_path / "u.toml"),
            "D2D_SESSION_HOURS": "2",
        }
    )
    assert cfg is not None and cfg.signer.ttl == 7200 and cfg.users.path == tmp_path / "u.toml"


def test_user_cli_blocks_extra_accounts_by_default(tmp_path: Path) -> None:
    runner = CliRunner()
    f = str(tmp_path / "users.toml")
    cfg = ["--config", str(ROOT / "config")]
    add = ["user", "add", "--users-file", f, "--password-stdin", *cfg]
    assert runner.invoke(app, [*add[:2], "me", *add[2:]], input=PW + "\n").exit_code == 0
    second = runner.invoke(app, [*add[:2], "friend", *add[2:]], input=PW + "\n")
    assert second.exit_code == 1 and "user limit reached" in second.output
    guest = runner.invoke(app, [*add[:2], "me", "--role", "guest", *add[2:]], input=PW + "\n")
    assert guest.exit_code == 1 and "not allowed" in guest.output
    # Changing your own password is still allowed.
    assert (
        runner.invoke(
            app,
            ["user", "passwd", "me", "--users-file", f, "--password-stdin", *cfg],
            input=PW + "x\n",
        ).exit_code
        == 0
    )
    assert len(UsersFile(Path(f)).users()) == 1


def test_user_cli(tmp_path: Path) -> None:
    runner = CliRunner()
    f = str(tmp_path / "users.toml")
    wide = tmp_path / "cfg"
    wide.mkdir()
    (wide / "roles.toml").write_text(
        (ROOT / "config" / "roles.toml")
        .read_text()
        .replace("max_users = 1", "max_users = 5")
        .replace('roles = ["owner"]', 'roles = ["owner", "guest"]')
    )
    cfg = ["--config", str(wide)]
    r = runner.invoke(
        app,
        ["user", "add", "alice", "--role", "owner", "--users-file", f, "--password-stdin", *cfg],
        input=PW + "\n",
    )
    assert r.exit_code == 0, r.output
    r = runner.invoke(
        app,
        ["user", "add", "bob", "--role", "guest", "--users-file", f, "--password-stdin", *cfg],
        input="short\n",
    )
    assert r.exit_code == 1 and "at least 12" in r.output
    listed = runner.invoke(app, ["user", "list", "--users-file", f]).output
    assert "alice" in listed and "owner" in listed and "scrypt" not in listed
    passwd = ["user", "passwd", "alice", "--users-file", f, "--password-stdin", *cfg]
    assert runner.invoke(app, passwd, input=PW + "x\n").exit_code == 0
    assert runner.invoke(app, ["user", "remove", "alice", "--users-file", f]).exit_code == 0
    assert runner.invoke(app, ["user", "passwd", "nobody", "--users-file", f]).exit_code == 1


def test_account_policy() -> None:
    policy = load_account_policy(ROOT / "config" / "roles.toml")
    assert policy == AccountPolicy(max_users=1, roles=("owner",))
    policy.check_new({}, "me", "owner")
    policy.check_new({"me": "owner"}, "me", "owner")  # replacing yourself is fine
    with pytest.raises(ConfigError, match="limit"):
        policy.check_new({"me": "owner"}, "other", "owner")
    with pytest.raises(ConfigError, match="not allowed"):
        policy.check_new({}, "me", "guest")
    assert policy.allows({"me": "owner"}, "me")
    assert not policy.allows({"me": "owner", "x": "owner"}, "me")
    assert not policy.allows({"me": "guest"}, "me")
