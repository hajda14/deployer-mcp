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

For pool targets, `.deployer.yml` may include `workloads` entries with
`replicated` or `global` mode, replicas, resource reservations/limits, placement
constraints, and spread preferences. A Git pool deployment that uses Compose
`build:` must give every build service a registry-backed `image:`. Deployer
builds and pushes those images from the selected manager and passes its registry
credentials to Swarm workers during stack deployment.
Set `high_availability: true` on a replicated workload to require at least two
replicas, keep one replica per node, update one replica at a time with automatic
rollback, and require either `workloads[].healthcheck` or a Compose healthcheck.
Pool routes automatically fail over across eligible Swarm nodes.
Run `docker login <registry>` on a manager before the first such deployment.
Registry passwords are intentionally not stored by Deployer.

Applications that terminate TLS themselves, such as SMTP or IMAP servers, may
declare `certificate_mounts` in `.deployer.yml`. Each entry names a Compose
service, a read-only certificate path, and optionally a private-key path. Pass
`certificate_bindings` to the plan/deploy tool to select an existing Deployer
identity. The MCP process receives only identity metadata; Deployer decrypts
and provisions key material directly on the selected device or every eligible
pool node. Redeploy after renewal to distribute the renewed files.

The plan/deploy tools also accept `environment_variables`, for example:

```json
[
  {
    "name": "DATABASE_PASSWORD",
    "value": "replace-me",
    "is_secret": true
  }
]
```

Deployer encrypts all values at rest. Values marked as secrets are write-only:
MCP never receives them in a response. Omit `environment_variables` when
updating a deployment to preserve the current set. These values become
container environment variables and remain inspectable by authorized Docker
administrators; use certificate mounts for private identity files.

Git deployments may set `git_provider` to `github`, `gitlab`,
`azure_devops`, or `generic`; common hosted repository URLs are inferred when
it is omitted. GitHub uses the user's OAuth connection. GitLab, Azure DevOps,
and private generic HTTPS credentials must be configured in the Deployer web
UI first. Provider secrets are encrypted by Deployer and are never exposed to
this MCP process. MCP can select a provider for a deployment, but cannot create,
read, rotate, or remove provider credentials.

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
