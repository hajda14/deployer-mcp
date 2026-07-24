# Deployer MCP

This stdio MCP server creates and validates `.deployer.yml`, plans deployments,
deploys projects with domains/TLS, and reads owned deployment status and logs.
It has no tools for profiles, users, tokens, credentials, infrastructure
administration, or global settings.

## Install

```bash
python3 -m venv "$HOME/.local/share/deployer-mcp"
"$HOME/.local/share/deployer-mcp/bin/python" -m pip install \
  "deployer-mcp @ git+https://github.com/hajda14/deployer-mcp.git@v0.1.0"
```

Create an `MCP only` or `REST API + MCP` token in Deployer’s Profile Settings,
then configure the MCP process:

```json
{
  "mcpServers": {
    "deployer": {
      "command": "/home/you/.local/share/deployer-mcp/bin/deployer-mcp",
      "env": {
        "DEPLOYER_API_URL": "https://deployer.example.com/api/v1",
        "DEPLOYER_API_TOKEN": "dpl_copy-the-token-shown-once"
      }
    }
  }
}
```

Replace `/home/you` with your absolute home directory. MCP clients do not
necessarily expand `$HOME` or `~` in JSON configuration.

`DEPLOYER_API_TOKEN` is required. `DEPLOYER_API_URL` defaults to
`http://localhost:8000/api/v1`.

The server uses stdout only for the MCP stdio protocol.

## Development

```bash
git clone https://github.com/hajda14/deployer-mcp.git
cd deployer-mcp
python3 -m venv .venv
.venv/bin/python -m pip install -e .
```

The main Deployer repository pins a tested version of this repository as its
`mcp_server` Git submodule.
