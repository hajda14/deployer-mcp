from __future__ import annotations

import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch

from mcp import StdioServerParameters
from mcp.client import Client
from mcp.client.stdio import stdio_client

from deployer_mcp.server import (
    _deployment_payload,
    _safe_relative_compose_file,
    cancel_deployer_build_job,
    configure_deployer_github_webhook,
    create_deployer_dev_session,
    create_deployer_manifest,
    create_deployer_udp_endpoint,
    disable_deployer_github_webhook,
    delete_deployer_udp_endpoint,
    enable_deployer_private_preview,
    apply_deployer_dev_session_fixture,
    get_deployer_build_job,
    get_deployer_github_webhook,
    get_deployer_dev_session,
    get_deployer_dev_session_logs,
    get_deployer_dev_session_metrics,
    get_deployer_dev_session_timings,
    list_deployer_dev_session_runners,
    list_deployer_build_jobs,
    list_deployer_dev_sessions,
    list_deployer_udp_endpoints,
    list_deployer_releases,
    mcp,
    rollback_deployer_release,
    stop_deployer_dev_session,
    update_deployer_udp_endpoint,
    upsert_environment_variable,
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
        self.assertIn("upsert_environment_variable", tool_names)
        self.assertIn("list_deployer_udp_endpoints", tool_names)
        self.assertIn("create_deployer_udp_endpoint", tool_names)
        self.assertIn("update_deployer_udp_endpoint", tool_names)
        self.assertIn("delete_deployer_udp_endpoint", tool_names)
        self.assertIn("configure_deployer_github_webhook", tool_names)
        self.assertIn("get_deployer_github_webhook", tool_names)
        self.assertIn("disable_deployer_github_webhook", tool_names)
        self.assertIn("enable_deployer_private_preview", tool_names)
        self.assertIn("create_deployer_dev_session", tool_names)
        self.assertIn("list_deployer_dev_session_runners", tool_names)
        self.assertIn("list_deployer_dev_sessions", tool_names)
        self.assertIn("get_deployer_dev_session", tool_names)
        self.assertIn("get_deployer_dev_session_logs", tool_names)
        self.assertIn("get_deployer_dev_session_metrics", tool_names)
        self.assertIn("get_deployer_dev_session_timings", tool_names)
        self.assertIn("apply_deployer_dev_session_fixture", tool_names)
        self.assertIn("stop_deployer_dev_session", tool_names)

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
    def test_manifest_tool_writes_udp_routes(self, client) -> None:
        client.return_value.request.return_value = {"valid": True, "errors": []}
        udp_routes = [
            {
                "name": "livekit-ice",
                "service": "livekit",
                "port_name": "livekit-ice",
            }
        ]
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "compose.yml").write_text(
                "services:\n  livekit:\n    image: livekit/livekit-server\n",
                encoding="utf-8",
            )

            result = create_deployer_manifest(
                str(root),
                "voice-service",
                ports=[{"name": "livekit-ice", "port": 7882, "protocol": "udp"}],
                udp_routes=udp_routes,
            )

            self.assertEqual(result["validation"]["valid"], True)
            self.assertIn("udp_routes:", result["manifest_content"])
            self.assertIn("livekit-ice", result["manifest_content"])


class GithubWebhookTests(TestCase):
    @patch("deployer_mcp.server._client")
    def test_github_webhook_tools_use_owner_scoped_endpoints(self, client) -> None:
        api = client.return_value
        api.request.side_effect = [
            {"enabled": True, "github_registered": True},
            {"enabled": True, "secret": "shown-once"},
            {"disabled": True, "remote_hook_removed": True},
        ]

        self.assertTrue(get_deployer_github_webhook("deployment-id")["enabled"])
        self.assertEqual(
            configure_deployer_github_webhook("deployment-id")["secret"],
            "shown-once",
        )
        self.assertTrue(disable_deployer_github_webhook("deployment-id")["disabled"])

        self.assertEqual(
            [call.args for call in api.request.call_args_list],
            [
                ("GET", "/mcp/deployments/deployment-id/github-webhook"),
                ("POST", "/mcp/deployments/deployment-id/github-webhook"),
                ("DELETE", "/mcp/deployments/deployment-id/github-webhook"),
            ],
        )


class UdpEndpointToolsTests(TestCase):
    @patch("deployer_mcp.server._client")
    def test_udp_endpoint_tools_use_owner_scoped_deployment_routes(self, client) -> None:
        api = client.return_value
        endpoint = {"id": "endpoint-id", "transport": "udp"}
        api.request.side_effect = [[endpoint], endpoint, endpoint, None]
        ports = [{"route_name": "livekit-ice", "public_port": 7882}]

        self.assertEqual(list_deployer_udp_endpoints("deployment-id"), [endpoint])
        self.assertEqual(
            create_deployer_udp_endpoint(
                "deployment-id",
                "game-voice",
                "voice.example.test",
                ports,
                advertised_ipv6="2001:db8::10",
            ),
            endpoint,
        )
        self.assertEqual(
            update_deployer_udp_endpoint(
                "deployment-id",
                "endpoint-id",
                "game-voice",
                "voice.example.test",
                ports,
            ),
            endpoint,
        )
        delete_deployer_udp_endpoint("deployment-id", "endpoint-id")

        calls = api.request.call_args_list
        self.assertEqual(
            calls[0].args,
            ("GET", "/mcp/deployments/deployment-id/udp-endpoints"),
        )
        self.assertEqual(
            calls[1].args[:2],
            ("POST", "/mcp/deployments/deployment-id/udp-endpoints"),
        )
        self.assertEqual(calls[1].kwargs["json"]["ports"], ports)
        self.assertEqual(
            calls[2].args[:2],
            ("PUT", "/mcp/deployments/deployment-id/udp-endpoints/endpoint-id"),
        )
        self.assertEqual(
            calls[3].args,
            ("DELETE", "/mcp/deployments/deployment-id/udp-endpoints/endpoint-id"),
        )

class EnvironmentVariableTests(TestCase):
    @patch("deployer_mcp.server._client")
    def test_environment_upsert_calls_single_variable_mcp_operation(self, client) -> None:
        api = client.return_value
        api.request.return_value = {
            "name": "DATABASE_PASSWORD",
            "is_secret": True,
            "has_value": True,
        }

        result = upsert_environment_variable(
            "deployment-id",
            "DATABASE_PASSWORD",
            "new-secret-value",
        )

        self.assertEqual(result["has_value"], True)
        api.request.assert_called_once_with(
            "PUT",
            "/mcp/deployments/deployment-id/environment-variables",
            json={
                "name": "DATABASE_PASSWORD",
                "value": "new-secret-value",
                "is_secret": True,
            },
        )


class PrivatePreviewTests(TestCase):
    @patch("deployer_mcp.server._client")
    def test_enable_private_preview_uses_owner_scoped_mcp_endpoint(self, client) -> None:
        api = client.return_value
        api.request.return_value = {
            "enabled": True,
            "mode": "password",
            "password_configured": True,
            "owner_user_id": "owner-id",
            "protected_domains": ["app.example.com"],
        }

        result = enable_deployer_private_preview("deployment-id")

        self.assertTrue(result["enabled"])
        self.assertTrue(result["password_configured"])
        api.request.assert_called_once_with(
            "POST",
            "/mcp/deployments/deployment-id/preview-access/enable",
            json={},
        )

    def test_enable_private_preview_rejects_path_like_deployment_id(self) -> None:
        with self.assertRaises(ValueError):
            enable_deployer_private_preview("../other-deployment")

    @patch("deployer_mcp.server._client")
    def test_enable_private_preview_can_publish_a_running_development_session(self, client) -> None:
        api = client.return_value
        api.request.return_value = {
            "preview_access": {"enabled": True, "protected_domains": ["app.example.com"]},
            "development_session": {
                "id": "session-id",
                "preview_url": "https://app.example.com/.deployer-dev/session-id/",
            },
        }

        result = enable_deployer_private_preview("deployment-id", "session-id")

        self.assertIn("development_session", result)
        api.request.assert_called_once_with(
            "POST",
            "/mcp/deployments/deployment-id/preview-access/enable",
            json={"development_session_id": "session-id"},
        )

    def test_enable_private_preview_rejects_path_like_session_id(self) -> None:
        with self.assertRaises(ValueError):
            enable_deployer_private_preview("deployment-id", "../other-session")


class DevelopmentSessionTests(TestCase):
    def test_compose_path_must_be_relative_and_confined(self) -> None:
        self.assertEqual(
            _safe_relative_compose_file("configs/compose.dev.yaml"),
            "configs/compose.dev.yaml",
        )
        for path in (
            "../compose.yaml",
            "configs/../../compose.yaml",
            "/tmp/compose.yaml",
            "C:/project/compose.yaml",
            "configs\\compose.yaml",
            "",
            "configs//compose.yaml",
            "compose\x00.yaml",
        ):
            with self.subTest(path=path), self.assertRaises(ValueError):
                _safe_relative_compose_file(path)

    @patch("deployer_mcp.server._client")
    def test_metrics_tool_uses_owner_scoped_session_endpoint(self, client) -> None:
        client.return_value.request.return_value = {
            "session_id": "session-id",
            "containers": [],
        }

        result = get_deployer_dev_session_metrics("session-id")

        self.assertEqual(result["session_id"], "session-id")
        client.return_value.request.assert_called_once_with(
            "GET", "/mcp/dev-sessions/session-id/metrics"
        )

    @patch("deployer_mcp.server._client")
    def test_timings_tool_uses_bounded_owner_scoped_endpoint(self, client) -> None:
        client.return_value.request.return_value = {
            "session_id": "session-id",
            "samples": [],
        }

        result = get_deployer_dev_session_timings(
            "session-id", tail_lines=40, since_seconds=900
        )

        self.assertEqual(result["samples"], [])
        client.return_value.request.assert_called_once_with(
            "GET",
            "/mcp/dev-sessions/session-id/timings?tail_lines=40&since_seconds=900",
        )

    def test_timings_tool_rejects_unbounded_limits(self) -> None:
        with self.assertRaises(ValueError):
            get_deployer_dev_session_timings("session-id", tail_lines=501)
        with self.assertRaises(ValueError):
            get_deployer_dev_session_timings("session-id", since_seconds=86_401)

    @patch("deployer_mcp.server._client")
    def test_fixture_tool_uses_owner_scoped_session_endpoint(self, client) -> None:
        api = client.return_value
        api.request.return_value = {
            "fixture_id": "dense-hostile-npcs-v1",
            "status": "applied",
            "database_scope": "session-local",
            "world_id": "world-1",
            "npc_count": 2008,
            "hostile_npc_count": 2008,
        }

        result = apply_deployer_dev_session_fixture(
            "session-id", "dense-hostile-npcs-v1"
        )

        self.assertEqual(result["hostile_npc_count"], 2008)
        api.request.assert_called_once_with(
            "POST",
            "/mcp/dev-sessions/session-id/fixtures",
            json={"fixture_id": "dense-hostile-npcs-v1"},
        )

    @patch("deployer_mcp.server._client")
    def test_logs_tool_can_filter_service_and_time_window(self, client) -> None:
        client.return_value.request.return_value = {"logs": "recent backend output"}

        result = get_deployer_dev_session_logs(
            "session-id",
            service="backend",
            tail_lines=80,
            since_seconds=600,
        )

        self.assertEqual(result["logs"], "recent backend output")
        client.return_value.request.assert_called_once_with(
            "GET",
            "/mcp/dev-sessions/session-id/logs?tail_lines=80&service=backend&since_seconds=600",
        )

    def test_logs_tool_rejects_arbitrary_service_input(self) -> None:
        with self.assertRaises(ValueError):
            get_deployer_dev_session_logs("session-id", service="backend;touch /tmp/x")

    @patch("deployer_mcp.server._client")
    def test_create_calls_owner_scoped_endpoint_and_strips_tokens(self, client) -> None:
        api = client.return_value
        api.request.return_value = {
            "id": "session-id",
            "status": "created",
            "token": "must-not-leak",
            "metadata": {"sync_token": "also-must-not-leak"},
        }

        result = create_deployer_dev_session(
            "deployment-id",
            "compose.dev.yaml",
        )

        self.assertEqual(result, {"id": "session-id", "status": "created", "metadata": {}})
        api.request.assert_called_once_with(
            "POST",
            "/mcp/dev-sessions",
            json={
                "deployment_id": "deployment-id",
                "compose_file": "compose.dev.yaml",
            },
        )

    @patch("deployer_mcp.server._client")
    def test_create_selects_authorized_runner(self, client) -> None:
        api = client.return_value
        api.request.return_value = {"id": "session-id", "status": "syncing"}

        create_deployer_dev_session(
            "deployment-id",
            "compose.dev.yaml",
            "runner-device-id",
        )

        api.request.assert_called_once_with(
            "POST",
            "/mcp/dev-sessions",
            json={
                "deployment_id": "deployment-id",
                "compose_file": "compose.dev.yaml",
                "runtime_device_id": "runner-device-id",
            },
        )

    @patch("deployer_mcp.server._client")
    def test_create_can_opt_into_deployment_environment(self, client) -> None:
        api = client.return_value
        api.request.return_value = {"id": "session-id", "status": "syncing"}

        create_deployer_dev_session(
            "deployment-id",
            "compose.dev.yaml",
            "runner-device-id",
            include_deployment_environment=True,
        )

        api.request.assert_called_once_with(
            "POST",
            "/mcp/dev-sessions",
            json={
                "deployment_id": "deployment-id",
                "compose_file": "compose.dev.yaml",
                "runtime_device_id": "runner-device-id",
                "include_deployment_environment": True,
            },
        )

    @patch("deployer_mcp.server._client")
    def test_list_session_runners_uses_owner_scoped_endpoint(self, client) -> None:
        api = client.return_value
        api.request.return_value = [{"id": "runner-device-id", "name": "deployer-host"}]

        result = list_deployer_dev_session_runners("deployment-id")

        self.assertEqual(result, [{"id": "runner-device-id", "name": "deployer-host"}])
        api.request.assert_called_once_with(
            "GET",
            "/mcp/dev-sessions/runners?deployment_id=deployment-id",
        )

    @patch("deployer_mcp.server._client")
    def test_session_tools_use_owner_scoped_endpoints(self, client) -> None:
        api = client.return_value
        api.request.side_effect = [
            [{"id": "session-id"}],
            [{"id": "session-id", "status": "ready"}],
            {"id": "session-id", "status": "ready"},
            {"logs": "server started"},
            {"id": "session-id", "status": "stopped"},
        ]

        self.assertEqual(list_deployer_dev_sessions(), [{"id": "session-id"}])
        self.assertEqual(
            list_deployer_dev_sessions("deployment-id"),
            [{"id": "session-id", "status": "ready"}],
        )
        self.assertEqual(
            get_deployer_dev_session("session-id"),
            {"id": "session-id", "status": "ready"},
        )
        self.assertEqual(
            get_deployer_dev_session_logs("session-id"),
            {"logs": "server started"},
        )
        self.assertEqual(
            stop_deployer_dev_session("session-id"),
            {"id": "session-id", "status": "stopped"},
        )

    @patch("deployer_mcp.server._client")
    def test_filtered_list_and_session_actions_use_expected_paths(self, client) -> None:
        api = client.return_value
        api.request.return_value = []
        list_deployer_dev_sessions("deployment-id")
        self.assertEqual(
            api.request.call_args.args,
            ("GET", "/mcp/dev-sessions?deployment_id=deployment-id"),
        )

        for action, expected in (
            (get_deployer_dev_session, ("GET", "/mcp/dev-sessions/session-id")),
            (
                get_deployer_dev_session_logs,
                ("GET", "/mcp/dev-sessions/session-id/logs?tail_lines=200"),
            ),
            (stop_deployer_dev_session, ("POST", "/mcp/dev-sessions/session-id/stop")),
        ):
            action("session-id")
            self.assertEqual(api.request.call_args.args, expected)

    def test_session_ids_reject_path_injection(self) -> None:
        with patch("deployer_mcp.server._client") as client:
            with self.assertRaises(ValueError):
                get_deployer_dev_session("../other-session")
            client.return_value.request.assert_not_called()

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
