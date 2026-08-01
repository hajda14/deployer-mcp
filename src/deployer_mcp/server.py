from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from mcp.server.fastmcp import FastMCP

from deployer_mcp.client import DeployerClient


mcp = FastMCP(
    "deployer",
    instructions=(
        "Create and validate .deployer.yml files, then plan, deploy, inspect, "
        "and redeploy projects. This server cannot manage profiles, tokens, "
        "credentials, identities, devices, pools, DNS infrastructure, arbitrary "
        "DNS records, or global settings. Deployment tools may automatically "
        "manage only the A/AAAA records owned by their gateway routes."
    ),
)


def _client() -> DeployerClient:
    return DeployerClient()


def _project_root(project_path: str) -> Path:
    root = Path(project_path).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"Project directory does not exist: {root}")
    return root


def _safe_project_file(root: Path, relative_path: str) -> Path:
    candidate = (root / relative_path).resolve()
    if not candidate.is_relative_to(root):
        raise ValueError("Project file must stay within the project directory")
    return candidate


def _read_project(project_path: str) -> tuple[Path, str, str | None]:
    root = _project_root(project_path)
    manifest_path = root / ".deployer.yml"
    if not manifest_path.is_file():
        raise ValueError(f".deployer.yml was not found in {root}")
    manifest_content = manifest_path.read_text(encoding="utf-8")
    try:
        raw_manifest = yaml.safe_load(manifest_content)
        compose_relative = raw_manifest["compose"]["file"]
    except (KeyError, TypeError, yaml.YAMLError) as exc:
        raise ValueError("Unable to resolve compose.file from .deployer.yml") from exc
    compose_path = _safe_project_file(root, str(compose_relative))
    compose_content = (
        compose_path.read_text(encoding="utf-8")
        if compose_path.is_file()
        else None
    )
    return root, manifest_content, compose_content


def _deployment_payload(
    project_path: str,
    *,
    target_type: Literal["device", "pool"],
    target_id: str,
    route_bindings: list[dict[str, Any]] | None,
    certificate_bindings: list[dict[str, Any]] | None,
    environment_variables: list[dict[str, Any]] | None,
    stack_name: str | None,
    source_type: Literal["manual", "git"],
    git_provider: Literal["github", "gitlab", "azure_devops", "generic"] | None,
    git_repository_url: str | None,
    git_ref: str | None,
    auto_redeploy_enabled: bool,
    image_strategy: Literal["deployer", "target", "prebuilt"] | None,
) -> dict[str, Any]:
    _, manifest_content, compose_content = _read_project(project_path)
    payload: dict[str, Any] = {
        "manifest_content": manifest_content,
        # Git deployments still send the local Compose document for validation
        # and non-mutating pool/build planning. The API does not persist it as
        # the runtime source for Git deployments.
        "compose_content": compose_content,
        "stack_name": stack_name,
        "source_type": source_type,
        "git_provider": git_provider,
        "git_repository_url": git_repository_url,
        "git_ref": git_ref,
        "auto_redeploy_enabled": auto_redeploy_enabled,
        "target_type": target_type,
        "device_id": target_id if target_type == "device" else None,
        "pool_id": target_id if target_type == "pool" else None,
        "routes": route_bindings or [],
        "certificate_bindings": certificate_bindings or [],
        "environment_variables": environment_variables,
    }
    if image_strategy is not None:
        payload["image_strategy"] = image_strategy
    return payload


@mcp.tool()
def get_deployer_capabilities() -> dict[str, Any]:
    """Show exactly what this MCP credential can and cannot do."""
    return _client().request("GET", "/mcp")


@mcp.tool()
def get_deployer_manifest_guide() -> dict[str, Any]:
    """Return the current .deployer.yml JSON schema and a small example."""
    return _client().request("GET", "/mcp/manifest")


@mcp.tool()
def create_deployer_manifest(
    project_path: str,
    application: str,
    compose_file: str = "compose.yml",
    project_name: str | None = None,
    ports: list[dict[str, Any]] | None = None,
    routes: list[dict[str, Any]] | None = None,
    workloads: list[dict[str, Any]] | None = None,
    certificate_mounts: list[dict[str, Any]] | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Create a validated .deployer.yml in an existing local project.

    `ports` entries describe named internal service ports. `routes` entries
    connect a manifest route name to a Compose service and port. `workloads`
    optionally define Swarm mode, replicas, resources, and placement. Domains
    and TLS settings are supplied later as deployment route bindings.
    `certificate_mounts` declares a Compose service and read-only in-container
    certificate path, plus an optional private-key path. It never contains
    certificate or key material.
    """
    root = _project_root(project_path)
    manifest_path = root / ".deployer.yml"
    if manifest_path.exists() and not overwrite:
        raise ValueError(".deployer.yml already exists; set overwrite=true to replace it")

    compose_path = _safe_project_file(root, compose_file)
    compose_content = (
        compose_path.read_text(encoding="utf-8")
        if compose_path.is_file()
        else None
    )
    manifest = {
        "version": 1,
        "application": application,
        "compose": {
            "file": compose_file,
            "project_name": project_name or application,
        },
        "ports": ports or [],
        "routes": routes or [],
        "workloads": workloads or [],
        "certificate_mounts": certificate_mounts or [],
    }
    manifest_content = yaml.safe_dump(manifest, sort_keys=False)
    validation = _client().request(
        "POST",
        "/mcp/projects/validate",
        json={
            "manifest_content": manifest_content,
            "compose_content": compose_content,
        },
    )
    if not validation["valid"]:
        raise ValueError("; ".join(validation["errors"]))
    manifest_path.write_text(manifest_content, encoding="utf-8")
    return {
        "path": str(manifest_path),
        "manifest_content": manifest_content,
        "validation": validation,
    }


@mcp.tool()
def validate_deployer_project(project_path: str) -> dict[str, Any]:
    """Validate a project's .deployer.yml and referenced Compose file."""
    _, manifest_content, compose_content = _read_project(project_path)
    return _client().request(
        "POST",
        "/mcp/projects/validate",
        json={
            "manifest_content": manifest_content,
            "compose_content": compose_content,
        },
    )


@mcp.tool()
def list_deployment_options() -> dict[str, Any]:
    """List deployable device/pool targets and public TLS identity names."""
    return _client().request("GET", "/mcp/options")


@mcp.tool()
def plan_deployer_project(
    project_path: str,
    target_type: Literal["device", "pool"],
    target_id: str,
    route_bindings: list[dict[str, Any]] | None = None,
    certificate_bindings: list[dict[str, Any]] | None = None,
    environment_variables: list[dict[str, Any]] | None = None,
    stack_name: str | None = None,
    source_type: Literal["manual", "git"] = "manual",
    git_provider: Literal["github", "gitlab", "azure_devops", "generic"] | None = None,
    git_repository_url: str | None = None,
    git_ref: str | None = None,
    auto_redeploy_enabled: bool = False,
    image_strategy: Literal["deployer", "target", "prebuilt"] | None = None,
) -> dict[str, Any]:
    """Build a non-mutating deployment plan.

    The plan detects target/domain conflicts and reports the managed DNS zone,
    exact route-owned A/AAAA values, and resolved ACME challenge for every
    route binding.
    """
    payload = _deployment_payload(
        project_path,
        target_type=target_type,
        target_id=target_id,
        route_bindings=route_bindings,
        certificate_bindings=certificate_bindings,
        environment_variables=environment_variables,
        stack_name=stack_name,
        source_type=source_type,
        git_provider=git_provider,
        git_repository_url=git_repository_url,
        git_ref=git_ref,
        auto_redeploy_enabled=auto_redeploy_enabled,
        image_strategy=image_strategy,
    )
    return _client().request("POST", "/mcp/deployments/plan", json=payload)


@mcp.tool()
def deploy_deployer_project(
    project_path: str,
    target_type: Literal["device", "pool"],
    target_id: str,
    route_bindings: list[dict[str, Any]] | None = None,
    certificate_bindings: list[dict[str, Any]] | None = None,
    environment_variables: list[dict[str, Any]] | None = None,
    stack_name: str | None = None,
    source_type: Literal["manual", "git"] = "manual",
    git_provider: Literal["github", "gitlab", "azure_devops", "generic"] | None = None,
    git_repository_url: str | None = None,
    git_ref: str | None = None,
    auto_redeploy_enabled: bool = False,
    image_strategy: Literal["deployer", "target", "prebuilt"] | None = None,
) -> dict[str, Any]:
    """Create or update an owned deployment and execute it.

    Route bindings use manifest route names plus domain/TLS settings, for
    example: [{"route_name": "web", "domain": "app.example.com",
    "certificate_mode": "letsencrypt", "http_mode": "redirect_to_https",
    "certificate_email": "ops@example.com", "acme_challenge_mode": "auto"}].
    A domain inside a managed zone automatically receives route-owned A/AAAA
    records pointing to Deployer's local primary. They move with domain changes
    and are removed with the route without touching manual values. This scoped
    side effect is not general DNS administration.

    `auto` uses DNS-01 only for an active Deployer-managed DNS zone and keeps
    HTTP-01 for domains using external DNS. Explicit `http-01` and `dns-01`
    are also accepted.

    For Git sources, `git_provider` may be github, gitlab, azure_devops, or
    generic. Omit it to infer the provider from common hosted repository URLs.
    Private repository credentials must already be connected in the Deployer
    web UI; this MCP server cannot read or change them.

    `certificate_bindings` may select an existing identity returned by
    list_deployment_options for a named manifest certificate mount, for
    example [{"mount_name": "mail-tls", "identity_id": "..."}]. Private key
    material is never returned to MCP; Deployer provisions it directly to the
    declared service on the target.

    `environment_variables` contains deployment-only values such as
    [{"name": "DATABASE_PASSWORD", "value": "...", "is_secret": true}].
    Deployer encrypts every value. Secret values are never returned by MCP.
    Omit the argument on an update to preserve the current set.

    `image_strategy` explicitly chooses where Compose build services are
    prepared: `deployer` uses the dedicated BuildKit worker and built-in
    registry, `target` builds on the selected device or one pool manager, and
    `prebuilt` skips builds and requires pullable Compose images. Omit it when
    updating a deployment to preserve its current strategy; new deployments
    default to `target`.
    """
    payload = _deployment_payload(
        project_path,
        target_type=target_type,
        target_id=target_id,
        route_bindings=route_bindings,
        certificate_bindings=certificate_bindings,
        environment_variables=environment_variables,
        stack_name=stack_name,
        source_type=source_type,
        git_provider=git_provider,
        git_repository_url=git_repository_url,
        git_ref=git_ref,
        auto_redeploy_enabled=auto_redeploy_enabled,
        image_strategy=image_strategy,
    )
    return _client().request(
        "POST",
        "/mcp/deployments",
        json=payload,
        timeout=600,
    )


@mcp.tool()
def list_deployer_deployments() -> list[dict[str, Any]]:
    """List deployments owned by the MCP token's user."""
    return _client().request("GET", "/mcp/deployments")


@mcp.tool()
def get_deployer_deployment_status(deployment_id: str) -> dict[str, Any]:
    """Read redacted runtime, routes, managed zones, and route-owned DNS records."""
    return _client().request("GET", f"/mcp/deployments/{deployment_id}")


@mcp.tool()
def list_deployer_build_jobs(deployment_id: str) -> list[dict[str, Any]]:
    """List the latest persisted build attempts for an owned deployment."""
    return _client().request(
        "GET",
        f"/mcp/deployments/{deployment_id}/build-jobs",
    )


@mcp.tool()
def get_deployer_build_job(
    deployment_id: str,
    build_job_id: str,
) -> dict[str, Any]:
    """Read one persisted build result with bounded credential-redacted logs."""
    return _client().request(
        "GET",
        f"/mcp/deployments/{deployment_id}/build-jobs/{build_job_id}",
    )


@mcp.tool()
def cancel_deployer_build_job(
    deployment_id: str,
    build_job_id: str,
) -> dict[str, Any]:
    """Cancel one queued/running build owned by the MCP token's user."""
    return _client().request(
        "POST",
        f"/mcp/deployments/{deployment_id}/build-jobs/{build_job_id}/cancel",
        json={"confirmation": "cancel-build-job"},
    )


@mcp.tool()
def get_deployer_container_logs(
    deployment_id: str,
    container_id: str,
) -> dict[str, Any]:
    """Read the last 300 log lines for a container in an owned deployment."""
    return _client().request(
        "GET",
        f"/mcp/deployments/{deployment_id}/containers/{container_id}/logs",
    )


@mcp.tool()
def redeploy_deployer_project(deployment_id: str) -> dict[str, Any]:
    """Redeploy an owned deployment without changing its definition."""
    return _client().request(
        "POST",
        f"/mcp/deployments/{deployment_id}/redeploy",
        timeout=600,
    )


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
