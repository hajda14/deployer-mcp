from __future__ import annotations

import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Literal
from urllib.parse import urlencode

import yaml
from mcp.server import MCPServer

from deployer_mcp.client import DeployerClient


mcp = MCPServer(
    "deployer",
    version="0.2.9",
    instructions=(
        "Create and validate .deployer.yml files, then plan, deploy, inspect, "
        "and redeploy projects, enable owner-scoped Private Preview, and manage "
        "signed GitHub push webhooks for owned deployments, and declared UDP "
        "endpoints for owned device deployments. This server cannot manage profiles, tokens, "
        "credentials, identities, devices, pools, DNS infrastructure, arbitrary "
        "DNS records, public TCP endpoints, or global settings. Development sessions are scoped to "
        "deployments owned by the token's user. Deployment tools may "
        "automatically "
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


def _safe_relative_compose_file(compose_file: str) -> str:
    """Validate a portable relative Compose path before sending it to the API."""
    if not compose_file or "\x00" in compose_file or "\\" in compose_file:
        raise ValueError("compose_file must be a non-empty relative POSIX path")
    posix_path = PurePosixPath(compose_file)
    windows_path = PureWindowsPath(compose_file)
    if (
        posix_path.is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
        or any(part in {"", ".", ".."} for part in compose_file.split("/"))
    ):
        raise ValueError("compose_file must stay within the project directory")
    return posix_path.as_posix()


def _safe_resource_id(resource_id: str, label: str) -> str:
    """Restrict API path identifiers to opaque single path segments."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", resource_id):
        raise ValueError(f"{label} must be a valid identifier")
    return resource_id


_DEV_SESSION_TOKEN_FIELDS = {
    "token",
    "session_token",
    "sync_token",
    "attach_token",
    "capability_token",
}


def _public_dev_session(value: Any) -> Any:
    """Defensively strip session credentials from every returned DTO level."""
    if isinstance(value, dict):
        return {
            key: _public_dev_session(item)
            for key, item in value.items()
            if str(key).lower() not in _DEV_SESSION_TOKEN_FIELDS
        }
    if isinstance(value, list):
        return [_public_dev_session(item) for item in value]
    return value


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
    udp_routes: list[dict[str, Any]] | None = None,
    workloads: list[dict[str, Any]] | None = None,
    certificate_mounts: list[dict[str, Any]] | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Create a validated .deployer.yml in an existing local project.

    `ports` entries describe named internal service ports. `routes` entries
    connect an HTTP route name to a Compose service and port. `udp_routes`
    declares direct UDP service ports for real-time traffic. `workloads`
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
        "udp_routes": udp_routes or [],
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
    route binding. A binding may include `proxy_headers`, for example
    [{"name": "Upgrade", "value": "$http_upgrade"},
    {"name": "Connection", "value": "$connection_upgrade"}].
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
    "certificate_email": "ops@example.com", "acme_challenge_mode": "auto",
    "proxy_headers": [{"name": "Upgrade", "value": "$http_upgrade"},
    {"name": "Connection", "value": "$connection_upgrade"}]}].
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
def get_deployer_github_webhook(deployment_id: str) -> dict[str, Any]:
    """Read the signed GitHub push webhook URL and enabled state for an owned deployment.

    The signing secret is never returned by this read operation.
    """
    return _client().request(
        "GET",
        f"/mcp/deployments/{deployment_id}/github-webhook",
    )


@mcp.tool()
def configure_deployer_github_webhook(deployment_id: str) -> dict[str, Any]:
    """Create or rotate a signed GitHub push webhook for an owned GitHub deployment.

    Deployer registers the Push webhook through the connected GitHub account.
    The response includes the URL and signing secret once; verify the active
    hook and recent delivery in the repository's GitHub webhook settings. Run
    the get operation later to read the URL; it never returns the secret again.
    """
    return _client().request(
        "POST",
        f"/mcp/deployments/{deployment_id}/github-webhook",
    )


@mcp.tool()
def disable_deployer_github_webhook(deployment_id: str) -> dict[str, Any]:
    """Disable the signed GitHub push webhook for an owned deployment."""
    return _client().request(
        "DELETE",
        f"/mcp/deployments/{deployment_id}/github-webhook",
    )


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
def list_deployer_releases(deployment_id: str) -> list[dict[str, Any]]:
    """List immutable digest releases for an owned deployment."""
    return _client().request(
        "GET",
        f"/mcp/deployments/{deployment_id}/releases",
    )


@mcp.tool()
def rollback_deployer_release(
    deployment_id: str,
    release_id: str,
) -> dict[str, Any]:
    """Activate an older immutable release without rebuilding images."""
    return _client().request(
        "POST",
        f"/mcp/deployments/{deployment_id}/releases/{release_id}/rollback",
        json={"confirmation": "rollback-deployment-release"},
        timeout=600,
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


@mcp.tool()
def list_deployer_udp_endpoints(deployment_id: str) -> list[dict[str, Any]]:
    """List direct UDP endpoints configured for an owned device deployment."""
    deployment_id = _safe_resource_id(deployment_id, "deployment_id")
    return _client().request(
        "GET",
        f"/mcp/deployments/{deployment_id}/udp-endpoints",
    )


@mcp.tool()
def create_deployer_udp_endpoint(
    deployment_id: str,
    name: str,
    domain: str,
    ports: list[dict[str, Any]],
    advertised_ipv4: str | None = None,
    advertised_ipv6: str | None = None,
    bind_ip: str | None = None,
    enabled: bool = True,
) -> dict[str, Any]:
    """Create a UDP endpoint on an owned device deployment.

    Every `ports` entry binds a manifest `udp_routes[].name` to an
    allowlisted public UDP port. Optional advertised addresses override the
    target/border address used for managed DNS. This cannot create TCP
    endpoints or modify devices, DNS infrastructure, or global settings.
    """
    deployment_id = _safe_resource_id(deployment_id, "deployment_id")
    return _client().request(
        "POST",
        f"/mcp/deployments/{deployment_id}/udp-endpoints",
        json={
            "name": name,
            "domain": domain,
            "ports": ports,
            "advertised_ipv4": advertised_ipv4,
            "advertised_ipv6": advertised_ipv6,
            "bind_ip": bind_ip,
            "enabled": enabled,
        },
    )


@mcp.tool()
def update_deployer_udp_endpoint(
    deployment_id: str,
    endpoint_id: str,
    name: str,
    domain: str,
    ports: list[dict[str, Any]],
    advertised_ipv4: str | None = None,
    advertised_ipv6: str | None = None,
    bind_ip: str | None = None,
    enabled: bool = True,
) -> dict[str, Any]:
    """Update a UDP endpoint owned by this deployment's account."""
    deployment_id = _safe_resource_id(deployment_id, "deployment_id")
    endpoint_id = _safe_resource_id(endpoint_id, "endpoint_id")
    return _client().request(
        "PUT",
        f"/mcp/deployments/{deployment_id}/udp-endpoints/{endpoint_id}",
        json={
            "name": name,
            "domain": domain,
            "ports": ports,
            "advertised_ipv4": advertised_ipv4,
            "advertised_ipv6": advertised_ipv6,
            "bind_ip": bind_ip,
            "enabled": enabled,
        },
    )


@mcp.tool()
def delete_deployer_udp_endpoint(
    deployment_id: str,
    endpoint_id: str,
) -> None:
    """Delete a UDP endpoint from an owned device deployment."""
    deployment_id = _safe_resource_id(deployment_id, "deployment_id")
    endpoint_id = _safe_resource_id(endpoint_id, "endpoint_id")
    _client().request(
        "DELETE",
        f"/mcp/deployments/{deployment_id}/udp-endpoints/{endpoint_id}",
    )


@mcp.tool()
def upsert_environment_variable(
    deployment_id: str,
    name: str,
    value: str,
    is_secret: bool = True,
) -> dict[str, Any]:
    """Add or update one encrypted environment variable on an owned deployment.

    Other environment variables are preserved. Secret values are write-only;
    the response includes only the name and whether a value is set. The
    deployment is redeployed and a new immutable environment release is saved.
    """
    return _client().request(
        "PUT",
        f"/mcp/deployments/{deployment_id}/environment-variables",
        json={"name": name, "value": value, "is_secret": is_secret},
    )


@mcp.tool()
def enable_deployer_private_preview(
    deployment_id: str,
    development_session_id: str | None = None,
) -> dict[str, Any]:
    """Enable deployment-wide Private Preview or publish one isolated dev session.

    Requires an administrator MCP token and an owned deployment with a route
    that uses HTTPS or redirects HTTP to HTTPS. This preserves any existing
    review-password policy and never returns a password, preview cookie, or
    owner credential. Without a session ID it gates every listed deployment
    domain, including the production URL, so inspect `protected_domains`.
    Passing an existing running `development_session_id` publishes only that
    session at its HTTPS preview path; it does not enable deployment-wide
    access or change production route access. It does not rebuild or restart
    the session.
    """
    deployment_id = _safe_resource_id(deployment_id, "deployment_id")
    payload: dict[str, str] = {}
    if development_session_id is not None:
        payload["development_session_id"] = _safe_resource_id(
            development_session_id,
            "development_session_id",
        )
    return _client().request(
        "POST",
        f"/mcp/deployments/{deployment_id}/preview-access/enable",
        json=payload,
    )


@mcp.tool()
def create_deployer_dev_session(
    deployment_id: str,
    compose_file: str,
    runtime_device_id: str | None = None,
    include_deployment_environment: bool = False,
) -> dict[str, Any]:
    """Create an owner-scoped development session for an existing deployment.

    `compose_file` must be a relative POSIX path inside the local project, such
    as `compose.dev.yaml`. An optional `runtime_device_id` selects an authorized
    SSH runner; otherwise the deployment's device is used. Set
    `include_deployment_environment` only when the temporary runtime needs the
    deployment's current environment values for Compose interpolation. Deployer
    snapshots them encrypted and writes a mode-0600 remote `.env` file that is
    removed with the session workspace. The returned session DTO contains no
    attach or sync token. After creation, start local file streaming with
    `deployer dev attach <session-id> --path <project-dir> --compose-file
    <relative-compose-file>`. This MCP operation manages the session only; it
    cannot manage devices, pools, credentials, or other infrastructure.
    """
    deployment_id = _safe_resource_id(deployment_id, "deployment_id")
    compose_file = _safe_relative_compose_file(compose_file)
    if runtime_device_id is not None:
        runtime_device_id = _safe_resource_id(runtime_device_id, "runtime_device_id")
    response = _client().request(
        "POST",
        "/mcp/dev-sessions",
        json={
            "deployment_id": deployment_id,
            "compose_file": compose_file,
            **({"runtime_device_id": runtime_device_id} if runtime_device_id else {}),
            **(
                {"include_deployment_environment": True}
                if include_deployment_environment
                else {}
            ),
        },
    )
    return _public_dev_session(response)


@mcp.tool()
def list_deployer_dev_session_runners(deployment_id: str) -> list[dict[str, Any]]:
    """List authorized SSH devices that can run a development session.

    Use the returned device ID as `runtime_device_id` when creating a session.
    The deployment device remains the default when no runner is selected.
    """
    deployment_id = _safe_resource_id(deployment_id, "deployment_id")
    return _public_dev_session(
        _client().request(
            "GET",
            f"/mcp/dev-sessions/runners?deployment_id={deployment_id}",
        )
    )


@mcp.tool()
def list_deployer_dev_sessions(deployment_id: str | None = None) -> list[dict[str, Any]]:
    """List development sessions owned by the MCP token's user.

    Optionally filter by an owned `deployment_id`. Use
    `create_deployer_dev_session` to create one, then run
    `deployer dev attach <session-id> --path <project-dir> --compose-file
    <relative-compose-file>` locally to stream project changes.
    """
    if deployment_id is None:
        path = "/mcp/dev-sessions"
    else:
        deployment_id = _safe_resource_id(deployment_id, "deployment_id")
        path = f"/mcp/dev-sessions?deployment_id={deployment_id}"
    return _public_dev_session(_client().request("GET", path))


@mcp.tool()
def get_deployer_dev_session(session_id: str) -> dict[str, Any]:
    """Read status and preview details for a development session you own."""
    session_id = _safe_resource_id(session_id, "session_id")
    return _public_dev_session(
        _client().request("GET", f"/mcp/dev-sessions/{session_id}")
    )


@mcp.tool()
def get_deployer_dev_session_logs(
    session_id: str,
    service: str | None = None,
    tail_lines: int = 200,
    since_seconds: int | None = None,
) -> dict[str, Any]:
    """Read bounded logs for one owned session, optionally filtered by service/time.

    `service` selects one Compose service such as `backend` or `nginx`.
    `tail_lines` is capped at 500 and `since_seconds` at 86400. Query strings
    in log content are redacted by Deployer.
    """
    session_id = _safe_resource_id(session_id, "session_id")
    if not 1 <= tail_lines <= 500:
        raise ValueError("tail_lines must be between 1 and 500")
    if since_seconds is not None and not 1 <= since_seconds <= 86_400:
        raise ValueError("since_seconds must be between 1 and 86400")
    query: dict[str, str | int] = {"tail_lines": tail_lines}
    if service is not None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,62}", service):
            raise ValueError("service must be a valid Compose service name")
        query["service"] = service
    if since_seconds is not None:
        query["since_seconds"] = since_seconds
    return _public_dev_session(
        _client().request(
            "GET",
            f"/mcp/dev-sessions/{session_id}/logs?{urlencode(query)}",
        )
    )


@mcp.tool()
def get_deployer_dev_session_timings(
    session_id: str,
    tail_lines: int = 200,
    since_seconds: int = 3600,
) -> dict[str, Any]:
    """Read bounded WebSocket timing samples for one development preview you own.

    Samples correlate the public gateway and device ingress with an opaque
    request ID. Only status and upstream connect/header durations are returned;
    no request URI, query, cookies, headers, or bodies are included. Successful
    WebSocket upgrade samples appear after the socket closes. Both limits are
    bounded by Deployer.
    """
    session_id = _safe_resource_id(session_id, "session_id")
    if not 1 <= tail_lines <= 500:
        raise ValueError("tail_lines must be between 1 and 500")
    if not 1 <= since_seconds <= 86_400:
        raise ValueError("since_seconds must be between 1 and 86400")
    query = urlencode({"tail_lines": tail_lines, "since_seconds": since_seconds})
    return _public_dev_session(
        _client().request(
            "GET",
            f"/mcp/dev-sessions/{session_id}/timings?{query}",
        )
    )


@mcp.tool()
def get_deployer_dev_session_metrics(session_id: str) -> dict[str, Any]:
    """Read live resource counters for containers in one running session you own.

    Returns container state, CPU, memory, network and block I/O, and process
    counts for the exact session Compose project. It does not return container
    environment values, inspect production containers, or execute commands.
    """
    session_id = _safe_resource_id(session_id, "session_id")
    return _public_dev_session(
        _client().request("GET", f"/mcp/dev-sessions/{session_id}/metrics")
    )


@mcp.tool()
def apply_deployer_dev_session_fixture(
    session_id: str,
    fixture_id: Literal["dense-hostile-npcs-v1"],
) -> dict[str, Any]:
    """Apply the allowlisted dense hostile NPC fixture to one owned dev session.

    The Deployer API verifies session ownership and invokes only the fixed
    `deployer-dev-fixture` Compose service in that session's project. The
    fixture runner must confirm its database is the project-local `db` service.
    """
    session_id = _safe_resource_id(session_id, "session_id")
    return _public_dev_session(
        _client().request(
            "POST",
            f"/mcp/dev-sessions/{session_id}/fixtures",
            json={"fixture_id": fixture_id},
        )
    )


@mcp.tool()
def stop_deployer_dev_session(session_id: str) -> dict[str, Any]:
    """Stop and clean up one development session owned by the MCP user."""
    session_id = _safe_resource_id(session_id, "session_id")
    return _public_dev_session(
        _client().request("POST", f"/mcp/dev-sessions/{session_id}/stop")
    )


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
