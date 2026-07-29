# Deployer MCP Guide

This directory is a Git submodule and a separately published Python package.
Changes here need their own commit/release before an installed MCP client sees
them; the parent repository records only the submodule commit.

## Scope

`src/deployer_mcp/server.py` exposes narrow FastMCP tools that:

- create and validate a local `.deployer.yml`;
- list deployment targets and public TLS identity metadata;
- plan and execute owned deployments;
- inspect owned status/logs and redeploy.

MCP must not manage profiles, users, personal tokens, Git credentials, SSH
credentials, private keys, devices, pools, DNS clusters/nodes/zones, arbitrary
DNS records, or global settings. Deployment may mutate only route-owned A/AAAA
records and may select an existing identity for a declared certificate mount.

## Safety

- Resolve project paths and reject any file escaping the project root.
- Do not overwrite `.deployer.yml` unless the caller explicitly requests it.
- Never embed repository credentials in URLs or return provider secrets.
- Environment secrets are accepted only as deployment inputs, encrypted by the
  Deployer API, and never returned. Omission during update preserves them.
- Private certificate/key material never enters the MCP process.
- Keep tool docstrings explicit because they are instructions to coding agents.

## Alignment and Verification

Any payload change must be reflected in:

- the backend `McpDeploymentRequest` and `/mcp` routes;
- `_deployment_payload` and both plan/deploy tool signatures;
- this submodule README;
- the main in-product `/docs` page.

Run:

```bash
python3 -m compileall -q src
git diff --check
```

Install editable for local protocol testing when needed:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
```

