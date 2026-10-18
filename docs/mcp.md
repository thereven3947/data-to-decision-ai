# MCP server: D2D tools for any AI client

`d2d mcp` serves the Copilot's read-only tools over the
[Model Context Protocol](https://modelcontextprotocol.io) (stdio), so Claude Desktop, VS Code or
any MCP-capable agent can ask D2D questions with the same code path, scoping and facts as the
built-in Copilot.

```bash
uv run d2d mcp --role guest --data-dir data      # needs D2D_DATABASE_URL (or the deploy .env keys)
```

## Tools

The MCP tools are the [Copilot's tools](agent-and-skills.md#read-only-tools): `list_stories`,
`get_story`, `get_device_history`, `list_devices`, `get_category_playbook`, `get_kpis` and
`search_stories`. Names, descriptions and required arguments come from `TOOL_SPECS`, and a test
fails if the two ever drift apart. Every tool is annotated `readOnlyHint: true`,
`destructiveHint: false`.

## Security model

- **One role per session.** `--role` (`owner`, `family`, `energy_advisor`, `guest`) is applied to
  every call exactly as in the web app: hidden devices, energy-only categories and
  published-only stories stay hidden. The default is `guest`, the least privilege.
- **Read-only.** There is no tool that labels, closes or writes anything; categories stay the
  classifier's and reviewers' job.
- **Local stdio.** The server is a child process of the MCP client on the same machine and opens no
  network port. It needs database credentials, so only run it where you would run `d2d` itself.
- **No secrets in client config.** Pass `D2D_DATABASE_URL` through the client's `env` block or
  inherit it from the shell; never commit it.

## Claude Desktop

`~/Library/Application Support/Claude/claude_desktop_config.json` on the Mac mini:

```json
{
  "mcpServers": {
    "d2d": {
      "command": "/Users/YOUR_USER/.local/bin/uv",
      "args": ["run", "--directory", "/Users/YOUR_USER/Documents/project/data-to-decision-ai",
               "d2d", "mcp", "--role", "owner", "--data-dir", "/Users/YOUR_USER/d2d/production/data"],
      "env": { "D2D_DATABASE_URL": "postgresql+psycopg://d2d:...@127.0.0.1:5432/d2d" }
    }
  }
}
```

## VS Code

`.vscode/mcp.json` (keep it out of git if it holds credentials):

```json
{
  "servers": {
    "d2d": {
      "type": "stdio",
      "command": "uv",
      "args": ["run", "d2d", "mcp", "--role", "guest", "--data-dir", "data"],
      "envFile": "${workspaceFolder}/.env"
    }
  }
}
```

## Tested

- `tests/unit/test_mcp.py`: tool list and schemas match `TOOL_SPECS`, read-only annotations, role
  scoping (the energy advisor cannot read a non-energy playbook), bad arguments return tool errors.
- `tests/integration/test_api.py::test_mcp_server_serves_scoped_ontology`: against Postgres, a guest
  client only ever receives stories the guest may see, through `list_stories` and `search_stories`.
