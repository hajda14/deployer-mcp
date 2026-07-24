# Deployer MCP

This stdio MCP server creates and validates `.deployer.yml`, plans deployments,
deploys projects with domains/TLS, and reads owned deployment status and logs.
It has no tools for profiles, users, tokens, credentials, infrastructure
administration, authoritative DNS administration, arbitrary/manual DNS records,
or global settings.

When a route domain belongs to a Deployer-managed zone, deployment automatically
publishes the route-owned `A` and/or `AAAA` values shown by the planning tool.
Changing the route domain moves those values and deleting the route removes
them. Manual values in the same RRset remain untouched. This lifecycle is a
scoped deployment side effect, not a general-purpose DNS administration tool.

For Let's Encrypt route bindings, set `acme_challenge_mode` to:

- `auto` (recommended): DNS-01 for an active Deployer-managed zone, otherwise
  HTTP-01.
- `http-01`: always validate through the public gateway, including domains
  hosted by an external DNS provider.
- `dns-01`: require an active Deployer-managed zone or fail during planning.

## Install

```bash
python3 -m venv "$HOME/.local/share/deployer-mcp"
"$HOME/.local/share/deployer-mcp/bin/python" -m pip install \
  "deployer-mcp @ git+https://github.com/hajda14/deployer-mcp.git@v0.1.1"
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
