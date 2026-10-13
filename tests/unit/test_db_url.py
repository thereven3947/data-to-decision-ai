from __future__ import annotations

import pytest

from d2d.config import ConfigError
from d2d.db import database_url


def test_explicit_url_wins() -> None:
    url = database_url(
        {"D2D_DATABASE_URL": "postgresql+psycopg://u:p@db:6543/x", "D2D_PG_PORT": "1"}
    )
    assert (url.host, url.port, url.database, url.username) == ("db", 6543, "x", "u")


def test_url_from_deploy_env_keys() -> None:
    url = database_url({"POSTGRES_PASSWORD": "s3cr/t@", "D2D_PG_PORT": "5433"})
    assert url.drivername == "postgresql+psycopg"
    assert (url.host, url.port, url.database, url.username) == ("127.0.0.1", 5433, "d2d", "d2d")
    assert url.password == "s3cr/t@"
    assert "s3cr" not in url.render_as_string(hide_password=True)


def test_empty_port_defaults_to_5432() -> None:
    assert database_url({"POSTGRES_PASSWORD": "x", "D2D_PG_PORT": ""}).port == 5432


def test_missing_password_is_a_config_error() -> None:
    with pytest.raises(ConfigError, match="POSTGRES_PASSWORD"):
        database_url({})
