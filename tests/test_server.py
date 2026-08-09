from __future__ import annotations

import sys
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch

from mcp import StdioServerParameters
from mcp.client import Client
from mcp.client.stdio import stdio_client

from deployer_mcp.server import (
    _deployment_payload,
    cancel_deployer_build_job,
    get_deployer_build_job,
    list_deployer_build_jobs,
    list_deployer_releases,
    mcp,
    rollback_deployer_release,
)


PROJECT_CONTENT = (None, "version: 1\n", "services: {}\n")


class ProtocolV2Tests(IsolatedAsyncioTestCase):
    async def test_server_negotiates_current_protocol_and_lists_tools(self) -> None:
        async with Client(mcp) as client:
            self.assertEqual(str(client.protocol_version), "2026-07-28")
            result = await client.list_tools()

        tool_names = {tool.name for tool in result.tools}
        self.assertIn("plan_deployer_project", tool_names)
        self.assertIn("deploy_deployer_project", tool_names)

    async def test_server_still_negotiates_legacy_protocol(self) -> None:
        async with Client(mcp, mode="legacy") as client:
            self.assertEqual(str(client.protocol_version), "2025-11-25")

    async def test_stdio_entrypoint_negotiates_current_protocol(self) -> None:
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "deployer_mcp"],
        )
        async with Client(stdio_client(parameters)) as client:
            self.assertEqual(str(client.protocol_version), "2026-07-28")


def _payload(
    image_strategy: str | None,
    route_bindings: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    with patch("deployer_mcp.server._read_project", return_value=PROJECT_CONTENT):
        return _deployment_payload(
            "/tmp/example",
            target_type="device",
            target_id="device-id",
            route_bindings=route_bindings,
            certificate_bindings=None,
            environment_variables=None,
            stack_name="example",
            source_type="git",
            git_provider="github",
            git_repository_url="https://github.com/example/app.git",
            git_ref="main",
            auto_redeploy_enabled=False,
            image_strategy=image_strategy,
        )


class DeploymentPayloadTests(TestCase):
    def test_payload_omits_unspecified_image_strategy(self) -> None:
        self.assertNotIn("image_strategy", _payload(None))

    def test_payload_includes_explicit_image_strategy(self) -> None:
        self.assertEqual(_payload("deployer")["image_strategy"], "deployer")

    def test_payload_preserves_route_proxy_headers(self) -> None:
        route = {
            "route_name": "web",
            "domain": "app.example.com",
            "proxy_headers": [
                {"name": "Upgrade", "value": "$http_upgrade"},
                {"name": "Connection", "value": "$connection_upgrade"},
            ],
        }

        self.assertEqual(_payload(None, [route])["routes"], [route])

    @patch("deployer_mcp.server._client")
    def test_build_history_tools_use_owner_scoped_mcp_endpoints(self, client) -> None:
        api = client.return_value
        api.request.side_effect = [
            [{"id": "job-id"}],
            {"id": "job-id"},
            {"id": "job-id", "cancel_requested_at": "now"},
        ]

        self.assertEqual(
            list_deployer_build_jobs("deployment-id"),
            [{"id": "job-id"}],
        )
        self.assertEqual(
            get_deployer_build_job("deployment-id", "job-id"),
            {"id": "job-id"},
        )
        self.assertEqual(
            cancel_deployer_build_job("deployment-id", "job-id"),
            {"id": "job-id", "cancel_requested_at": "now"},
        )

        self.assertEqual(
            api.request.call_args_list[0].args,
            ("GET", "/mcp/deployments/deployment-id/build-jobs"),
        )
        self.assertEqual(
            api.request.call_args_list[1].args,
            (
                "GET",
                "/mcp/deployments/deployment-id/build-jobs/job-id",
            ),
        )
        self.assertEqual(
            api.request.call_args_list[2].args,
            (
                "POST",
                "/mcp/deployments/deployment-id/build-jobs/job-id/cancel",
            ),
        )
        self.assertEqual(
            api.request.call_args_list[2].kwargs["json"],
            {"confirmation": "cancel-build-job"},
        )

    @patch("deployer_mcp.server._client")
    def test_release_tools_use_owner_scoped_mcp_endpoints(self, client) -> None:
        api = client.return_value
        api.request.side_effect = [
            [{"id": "release-id"}],
            {"status": "deployed"},
        ]

        self.assertEqual(
            list_deployer_releases("deployment-id"),
            [{"id": "release-id"}],
        )
        self.assertEqual(
            rollback_deployer_release("deployment-id", "release-id"),
            {"status": "deployed"},
        )
        self.assertEqual(
            api.request.call_args_list[1].kwargs,
            {
                "json": {"confirmation": "rollback-deployment-release"},
                "timeout": 600,
            },
        )
