"""MCP server: the Copilot's read-only ontology tools over the Model Context Protocol.

Any MCP client (Claude Desktop, VS Code, an agent framework) can ask D2D the same questions the
built-in Copilot asks, through the same ``run_tool`` code path. Every session runs as one role
(default ``guest``, least privilege), so role scoping is identical to the web app. Tool names,
descriptions and required arguments come from ``TOOL_SPECS``; a test keeps them in sync.
See docs/mcp.md.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError as MCPToolError
from mcp.types import ToolAnnotations

from d2d import __version__
from d2d.agent.tools import TOOL_SPECS, ToolError, run_tool
from d2d.api.repo import Ontology
from d2d.api.scope import Scope

READ_ONLY = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)
Status = Literal["new", "needs_review", "published", "closed"]

INSTRUCTIONS = (
    "D2D change intelligence for a simulated smart home whose structure mirrors grid operations. "
    "All tools are read-only and limited to what the session's role may see. Cite stories as "
    "[story:<id>] and use only facts returned by the tools; categories are assigned by D2D's "
    "classifier and human reviewers, never by you."
)


def build_server(onto: Ontology, scope: Scope) -> MCPServer:
    specs = {t["function"]["name"]: t["function"] for t in TOOL_SPECS}

    def call(name: str, args: dict[str, Any]) -> dict[str, Any]:
        try:
            result = run_tool(name, {k: v for k, v in args.items() if v is not None}, onto, scope)
        except (ToolError, ValueError, TypeError) as exc:
            raise MCPToolError(str(exc)) from exc
        out: dict[str, Any] = json.loads(json.dumps(result, default=str))
        return out

    def list_stories(
        status: Status | None = None,
        category: str | None = None,
        device_id: str | None = None,
        last_days: int | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        return call(
            "list_stories",
            {
                "status": status,
                "category": category,
                "device_id": device_id,
                "last_days": last_days,
                "since": since,
                "until": until,
                "limit": limit,
            },
        )

    def get_story(story_id: str) -> dict[str, Any]:
        return call("get_story", {"story_id": story_id})

    def get_device_history(device_id: str) -> dict[str, Any]:
        return call("get_device_history", {"device_id": device_id})

    def list_devices() -> dict[str, Any]:
        return call("list_devices", {})

    def get_category_playbook(category: str) -> dict[str, Any]:
        return call("get_category_playbook", {"category": category})

    def get_kpis() -> dict[str, Any]:
        return call("get_kpis", {})

    def search_stories(query: str, limit: int | None = None) -> dict[str, Any]:
        return call("search_stories", {"query": query, "limit": limit})

    server: MCPServer = MCPServer(
        "d2d", instructions=INSTRUCTIONS, version=__version__, log_level="WARNING"
    )
    for fn in (
        list_stories,
        get_story,
        get_device_history,
        list_devices,
        get_category_playbook,
        get_kpis,
        search_stories,
    ):
        server.add_tool(
            fn,
            name=fn.__name__,
            description=specs[fn.__name__]["description"],
            annotations=READ_ONLY,
        )
    return server
