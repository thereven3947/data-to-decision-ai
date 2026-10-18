from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import anyio
from mcp.client.client import Client
from sqlalchemy import create_engine

from d2d.agent.tools import TOOL_NAMES, TOOL_SPECS
from d2d.api.repo import Ontology
from d2d.api.scope import Scope, load_roles
from d2d.config import Household, load_categories
from d2d.enrichment import load_enrichment
from d2d.mcp_server import build_server

ROOT = Path(__file__).resolve().parents[2]


def _server(household: Household, role: str) -> Any:
    cfg = ROOT / "config"
    taxonomy = load_categories(cfg / "categories.toml")
    onto = Ontology(
        create_engine("sqlite://"),  # never touched by the tools used here
        household,
        taxonomy,
        load_enrichment(cfg / "enrichment.toml", cfg / "semantics.toml"),
        ROOT / "data",
    )
    scope = Scope(load_roles(cfg / "roles.toml")[role], household, taxonomy)
    return build_server(onto, scope)


def _run(server: Any, body: Callable[[Client], Awaitable[Any]]) -> Any:
    async def main() -> Any:
        async with Client(server) as client:
            return await body(client)

    return anyio.run(main)


def test_tools_mirror_the_copilot_specs_and_are_read_only(household: Household) -> None:
    tools = _run(_server(household, "owner"), lambda c: c.list_tools()).tools
    assert {t.name for t in tools} == TOOL_NAMES
    specs = {s["function"]["name"]: s["function"] for s in TOOL_SPECS}
    for t in tools:
        spec = specs[t.name]
        assert t.description == spec["description"]
        assert set(t.input_schema.get("required", [])) == set(spec["parameters"]["required"])
        assert set(t.input_schema["properties"]) == set(spec["parameters"]["properties"])
        assert t.annotations.read_only_hint is True and t.annotations.destructive_hint is False


def test_playbook_call_and_role_scoping(household: Household) -> None:
    taxonomy = load_categories(ROOT / "config" / "categories.toml")
    hidden = next(c.id for c in taxonomy.categories.values() if not c.energy)

    async def owner(c: Client) -> Any:
        return await c.call_tool("get_category_playbook", {"category": hidden})

    ok = _run(_server(household, "owner"), owner)
    assert not ok.is_error and ok.structured_content["category"] == hidden
    assert ok.structured_content["actions"]

    # The energy advisor only sees energy categories; the call fails like the web app's would.
    denied = _run(_server(household, "energy_advisor"), owner)
    assert denied.is_error and "unknown category" in denied.content[0].text


def test_bad_arguments_are_tool_errors_not_crashes(household: Household) -> None:
    async def body(c: Client) -> Any:
        return await c.call_tool("get_category_playbook", {})

    assert _run(_server(household, "owner"), body).is_error
