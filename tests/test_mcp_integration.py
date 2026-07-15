import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bk.config import Config
from bk.mcp_server import BkMcpBackend, create_mcp_server
from bk.storage import LedgerStore

try:
    from mcp.shared.memory import create_connected_server_and_client_session
    from pydantic import AnyUrl

    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False


@unittest.skipUnless(MCP_AVAILABLE, "install the mcp extra to run protocol integration tests")
class McpProtocolIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_in_memory_client_lists_and_calls_structured_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            config = Config(
                data_dir=data_dir,
                gpu_count=2,
                job_log_dir=Path(tmp) / "jobs",
            )
            app = create_mcp_server(
                BkMcpBackend(config, LedgerStore(data_dir)),
                cluster_backend=None,
            )
            future = datetime.now(timezone.utc) + timedelta(days=1)
            timestamp = int(future.timestamp())
            remainder = timestamp % 300
            if remainder:
                timestamp += 300 - remainder
            start = datetime.fromtimestamp(timestamp, timezone.utc).isoformat().replace("+00:00", "Z")

            async with create_connected_server_and_client_session(app, raise_exceptions=True) as session:
                tools = await session.list_tools()
                by_name = {item.name: item for item in tools.tools}
                names = set(by_name)
                resources = await session.list_resources()
                context_resource = await session.read_resource(AnyUrl("bk://context"))
                usage_resource = await session.read_resource(AnyUrl("bk://usage/me/recent"))
                prompts = await session.list_prompts()
                prompt = await session.get_prompt(
                    "plan_gpu_experiment",
                    {"count": "1", "duration": "30m", "expected_memory": "8g"},
                )
                recommendation = await session.call_tool(
                    "recommend_gpu_booking",
                    {"count": 1, "duration": "30m", "mode": "shared"},
                )
                usage = await session.call_tool("get_my_gpu_usage", {"since": "1h"})
                created = await session.call_tool(
                    "create_gpu_booking",
                    {
                        "count": 1,
                        "duration": "30m",
                        "mode": "shared",
                        "start": start,
                        "expected_memory": "8g",
                        "operation_id": "mcp-protocol-test-1",
                    },
                )
                retried = await session.call_tool(
                    "create_gpu_booking",
                    {
                        "count": 1,
                        "duration": "30m",
                        "mode": "shared",
                        "start": start,
                        "expected_memory": "8g",
                        "operation_id": "mcp-protocol-test-1",
                    },
                )
                edited = await session.call_tool(
                    "edit_my_gpu_booking",
                    {
                        "reservation_id": created.structuredContent["reservation"]["short_id"],
                        "duration": "35m",
                        "operation_id": "mcp-protocol-edit-1",
                    },
                )
                edit_retried = await session.call_tool(
                    "edit_my_gpu_booking",
                    {
                        "reservation_id": created.structuredContent["reservation"]["short_id"],
                        "duration": "35m",
                        "operation_id": "mcp-protocol-edit-1",
                    },
                )
                cancelled = await session.call_tool(
                    "cancel_my_gpu_booking",
                    {"reservation_id": created.structuredContent["reservation"]["short_id"]},
                )
                cleanup = await session.call_tool("cleanup_my_job_specs", {})
                log_cleanup = await session.call_tool("cleanup_my_job_logs", {})

            self.assertEqual(
                names,
                {
                    "get_gpu_context",
                    "recommend_gpu_booking",
                    "create_gpu_booking",
                    "list_gpu_reservations",
                    "get_my_gpu_usage",
                    "edit_my_gpu_booking",
                    "cancel_my_gpu_booking",
                    "cleanup_my_job_specs",
                    "cleanup_my_job_logs",
                    "read_my_job_log",
                },
            )
            resource_uris = {str(resource.uri) for resource in resources.resources}
            self.assertEqual(resource_uris, {"bk://context", "bk://usage/me/recent"})
            self.assertTrue(by_name["get_gpu_context"].annotations.readOnlyHint)
            self.assertTrue(by_name["recommend_gpu_booking"].annotations.readOnlyHint)
            self.assertTrue(by_name["list_gpu_reservations"].annotations.readOnlyHint)
            self.assertTrue(by_name["read_my_job_log"].annotations.readOnlyHint)
            self.assertTrue(by_name["get_my_gpu_usage"].annotations.readOnlyHint)
            self.assertTrue(by_name["create_gpu_booking"].annotations.idempotentHint)
            self.assertFalse(by_name["create_gpu_booking"].annotations.destructiveHint)
            self.assertTrue(by_name["edit_my_gpu_booking"].annotations.idempotentHint)
            self.assertFalse(by_name["edit_my_gpu_booking"].annotations.destructiveHint)
            self.assertTrue(by_name["cancel_my_gpu_booking"].annotations.destructiveHint)
            self.assertFalse(by_name["cancel_my_gpu_booking"].annotations.idempotentHint)
            self.assertTrue(by_name["cleanup_my_job_specs"].annotations.destructiveHint)
            self.assertTrue(by_name["cleanup_my_job_specs"].annotations.idempotentHint)
            self.assertTrue(by_name["cleanup_my_job_logs"].annotations.destructiveHint)
            self.assertTrue(by_name["cleanup_my_job_logs"].annotations.idempotentHint)
            self.assertTrue(all(tool.annotations.openWorldHint is False for tool in by_name.values()))
            self.assertIn('"schema_version": "bk.agent.v1"', context_resource.contents[0].text)
            self.assertIn('"schema_version": "gpubk.usage.v1"', usage_resource.contents[0].text)
            self.assertEqual(prompts.prompts[0].name, "plan_gpu_experiment")
            self.assertIn("recommend_gpu_booking", prompt.messages[0].content.text)
            self.assertTrue(recommendation.structuredContent["available"])
            self.assertEqual(recommendation.structuredContent["schema_version"], "bk.agent.v1")
            self.assertEqual(usage.structuredContent["schema_version"], "gpubk.usage.v1")
            self.assertEqual(created.structuredContent["status"], "created")
            self.assertEqual(created.structuredContent["allocation"]["selected"][0]["gpu"], 0)
            self.assertEqual(retried.structuredContent["status"], "exists")
            self.assertEqual(
                retried.structuredContent["allocator"]["source"],
                "idempotent-replay",
            )
            self.assertEqual(
                created.structuredContent["reservation"]["id"],
                retried.structuredContent["reservation"]["id"],
            )
            self.assertEqual(edited.structuredContent["status"], "updated")
            self.assertEqual(edit_retried.structuredContent["status"], "exists")
            self.assertEqual(
                edit_retried.structuredContent["allocator"]["source"],
                "idempotent-replay",
            )
            self.assertEqual(cancelled.structuredContent["reservation"]["status"], "cancelled")
            self.assertEqual(cleanup.structuredContent["kind"], "job-spec-cleanup")
            self.assertEqual(log_cleanup.structuredContent["kind"], "job-log-cleanup")

    async def test_cluster_tools_are_registered_only_when_a_backend_is_enabled(self):
        class FakeClusterBackend:
            def __init__(self):
                self.calls = []

            def _result(self, kind, *arguments):
                self.calls.append((kind, arguments))
                return {"schema_version": "gpubk.cluster.v1", "kind": kind}

            def context(self):
                return self._result("cluster-context")

            def check(self, require_jobs=False):
                return self._result("cluster-check", require_jobs)

            def recommend(self, **kwargs):
                return self._result("cluster-recommendation", kwargs)

            def book(self, **kwargs):
                return self._result("cluster-booking-result", kwargs)

            def usage(self, **kwargs):
                return self._result("cluster-usage", kwargs)

            def edit(self, **kwargs):
                return self._result("cluster-mutation-result", kwargs)

            def cancel(self, reservation_id, operation_id):
                return self._result(
                    "cluster-mutation-result", reservation_id, operation_id
                )

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            local = BkMcpBackend(Config(data_dir=data_dir), LedgerStore(data_dir))
            cluster = FakeClusterBackend()
            app = create_mcp_server(local, cluster_backend=cluster)

            async with create_connected_server_and_client_session(
                app, raise_exceptions=True
            ) as session:
                tools = await session.list_tools()
                by_name = {item.name: item for item in tools.tools}
                resources = await session.list_resources()
                prompts = await session.list_prompts()
                context = await session.read_resource(AnyUrl("bk://cluster/context"))
                recommendation = await session.call_tool(
                    "recommend_cluster_gpu_booking",
                    {"count": 2, "duration": "1h", "expected_memory": "12g"},
                )
                readiness = await session.call_tool(
                    "check_gpu_cluster_readiness", {"require_jobs": True}
                )
                booking = await session.call_tool(
                    "create_cluster_gpu_booking",
                    {
                        "count": 2,
                        "duration": "1h",
                        "operation_id": "cluster-create-1",
                        "command": ["python", "train.py"],
                    },
                )
                usage = await session.call_tool(
                    "get_my_cluster_gpu_usage", {"since": "7d"}
                )
                edit = await session.call_tool(
                    "edit_my_cluster_gpu_booking",
                    {
                        "reservation_id": "gpu-a/123456",
                        "operation_id": "cluster-edit-1",
                        "duration": "2h",
                    },
                )
                cancel = await session.call_tool(
                    "cancel_my_cluster_gpu_booking",
                    {
                        "reservation_id": "gpu-a/123456",
                        "operation_id": "cluster-cancel-1",
                    },
                )

            cluster_names = {
                "get_gpu_cluster_context",
                "check_gpu_cluster_readiness",
                "recommend_cluster_gpu_booking",
                "create_cluster_gpu_booking",
                "get_my_cluster_gpu_usage",
                "edit_my_cluster_gpu_booking",
                "cancel_my_cluster_gpu_booking",
            }
            self.assertTrue(cluster_names.issubset(by_name))
            self.assertTrue(by_name["get_gpu_cluster_context"].annotations.readOnlyHint)
            self.assertTrue(
                by_name["check_gpu_cluster_readiness"].annotations.readOnlyHint
            )
            self.assertTrue(
                by_name["recommend_cluster_gpu_booking"].annotations.readOnlyHint
            )
            self.assertTrue(
                by_name["create_cluster_gpu_booking"].annotations.idempotentHint
            )
            self.assertTrue(
                by_name["edit_my_cluster_gpu_booking"].annotations.idempotentHint
            )
            self.assertTrue(
                by_name["cancel_my_cluster_gpu_booking"].annotations.destructiveHint
            )
            self.assertTrue(
                by_name["cancel_my_cluster_gpu_booking"].annotations.idempotentHint
            )
            self.assertIn(
                "bk://cluster/context",
                {str(resource.uri) for resource in resources.resources},
            )
            self.assertIn(
                "plan_cluster_gpu_experiment",
                {prompt.name for prompt in prompts.prompts},
            )
            self.assertIn('"kind": "cluster-context"', context.contents[0].text)
            self.assertEqual(
                recommendation.structuredContent["kind"], "cluster-recommendation"
            )
            self.assertEqual(readiness.structuredContent["kind"], "cluster-check")
            self.assertEqual(booking.structuredContent["kind"], "cluster-booking-result")
            self.assertEqual(usage.structuredContent["kind"], "cluster-usage")
            self.assertEqual(edit.structuredContent["kind"], "cluster-mutation-result")
            self.assertEqual(cancel.structuredContent["kind"], "cluster-mutation-result")


if __name__ == "__main__":
    unittest.main()
