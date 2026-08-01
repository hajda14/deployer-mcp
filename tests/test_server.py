from __future__ import annotations

from unittest import TestCase
from unittest.mock import patch

from deployer_mcp.server import (
    _deployment_payload,
    get_deployer_build_job,
    list_deployer_build_jobs,
)


PROJECT_CONTENT = (None, "version: 1\n", "services: {}\n")


def _payload(image_strategy: str | None) -> dict[str, object]:
    with patch("deployer_mcp.server._read_project", return_value=PROJECT_CONTENT):
        return _deployment_payload(
            "/tmp/example",
            target_type="device",
            target_id="device-id",
            route_bindings=None,
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

    @patch("deployer_mcp.server._client")
    def test_build_history_tools_use_owner_scoped_mcp_endpoints(self, client) -> None:
        api = client.return_value
        api.request.side_effect = [[{"id": "job-id"}], {"id": "job-id"}]

        self.assertEqual(
            list_deployer_build_jobs("deployment-id"),
            [{"id": "job-id"}],
        )
        self.assertEqual(
            get_deployer_build_job("deployment-id", "job-id"),
            {"id": "job-id"},
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
