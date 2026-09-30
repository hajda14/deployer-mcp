# Deployer MCP

This stdio MCP server creates and validates `.deployer.yml`, plans deployments,
deploys projects with domains/TLS, and reads owned deployment status, persisted
build history, structured cache results, cooperative build cancellation,
credential-redacted build logs, and container logs.
It has no tools for profiles, users, tokens, credentials, infrastructure
administration, authoritative DNS administration, arbitrary/manual DNS records,
device-agent installation, public TCP endpoint administration, or global
settings.

The server uses the stable MCP Python SDK 2.x and supports the sessionless MCP
`2026-07-28` protocol revision. The same stdio endpoint remains compatible with
clients using the earlier `2025-11-25` initialization handshake.

MCP may validate portable `tcp_routes` in `.deployer.yml`, but an administrator
must enable the outbound agent and bind those route names to environment-
specific domains and public ports in the Deployer web UI or REST API.

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

Both `plan_deployer_project` and `deploy_deployer_project` accept an explicit
`image_strategy`:

- `deployer` builds Git Compose services once in Deployer's separate build
  worker, pushes multi-platform images to the built-in registry, and deploys
  immutable digests.
- `target` builds on the selected device or on one eligible Swarm manager.
- `prebuilt` skips builds and pulls the images already declared by Compose.

New deployments default to `target` for compatibility with existing MCP
clients. Omitting `image_strategy` while updating an existing stack preserves
its current strategy. Deployment status returns the selected strategy and any
immutable per-service image digests resolved by a Deployer build.
`list_deployer_build_jobs` returns the latest persisted attempts for an owned
deployment, while `get_deployer_build_job` returns one attempt with its bounded,
credential-redacted worker logs. These tools cannot read another user's jobs.
`cancel_deployer_build_job` records and cooperatively stops one owned
queued/running build before target rollout. Build summaries include structured
cache-use and cached/completed step counts.
`list_deployer_releases` returns exact activated service digests and
`rollback_deployer_release` reactivates an older release without rebuilding.

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

Use `upsert_environment_variable` to add or change one value on an existing
deployment without replacing sibling variables. The operation encrypts the
value, performs a redeploy, and records an immutable environment snapshot for
rollback. Responses show only the name and whether a value is set for secrets.
`WORLD_RUNTIME_SHARED_SECRET` must be marked secret and contain at least 32
ASCII characters.

Use `enable_deployer_private_preview` to enable the existing deployment's
Private Preview policy through an MCP-only token. The token must belong to an
administrator and the deployment. At least one clearnet route must use HTTPS
or redirect HTTP to HTTPS. The operation preserves the current owner/password
mode and any configured review-password hash; it returns only the mode,
enabled state, configured-password status, owner ID, and protected domains.
Private Preview gates every listed deployment domain, including its ordinary
production URL. Review the returned `protected_domains` before enabling it. The
operation does not create domains or change route/TLS settings.

Development sessions are temporary, owner-scoped environments attached to an
existing deployment. Use `list_deployer_dev_session_runners` to see authorized
SSH devices that can host the session, then pass the selected ID as the optional
`runtime_device_id` to `create_deployer_dev_session`. Omitting it uses the
deployment device. The tool returns the session DTO without an attach token.
Set `include_deployment_environment=true` only when the temporary runtime needs
the deployment's current values for Compose interpolation. Deployer snapshots
them encrypted and writes a mode-0600 remote `.env` file that is removed with
the session workspace; secret values are never returned by MCP.
Then run the local Rust CLI to stream the working tree into that session:

```bash
deployer dev attach <session-id> --path <project-dir> \
  --compose-file <relative-compose-file>
```

Use `list_deployer_dev_sessions`, `get_deployer_dev_session`,
`get_deployer_dev_session_logs`, and `stop_deployer_dev_session` to inspect and
manage sessions. Sessions are scoped to deployments owned by the MCP token's
user. These tools do not manage devices, pools, credentials, or infrastructure.
The MCP process does not stream local files itself; the Rust CLI connects to
Deployer and sends the local changes.

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

Route bindings may also include validated upstream request headers:

```json
{
  "route_name": "web",
  "domain": "app.example.com",
  "certificate_mode": "letsencrypt",
  "http_mode": "redirect_to_https",
  "certificate_email": "ops@example.com",
  "proxy_headers": [
    {"name": "Upgrade", "value": "$http_upgrade"},
    {"name": "Connection", "value": "$connection_upgrade"}
  ]
}
```

Matching names replace Deployer's standard forwarding value without creating
duplicate headers. The shown pair is the WebSocket template used by the web UI.
Nginx variables are supported, but route headers are not encrypted secret
storage; do not place passwords or API tokens in them.

## Install

```bash
python3 -m venv "$HOME/.local/share/deployer-mcp"
"$HOME/.local/share/deployer-mcp/bin/python" -m pip install \
  "deployer-mcp @ git+https://github.com/hajda14/deployer-mcp.git@v0.2.3"
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
