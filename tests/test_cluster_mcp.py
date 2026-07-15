import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from bk.cluster import ClusterConfig
from bk.cluster_mcp import ClusterMcpBackend
from bk.cluster_transport import ClusterNode
from bk.models import BookingError


class ClusterMcpBackendTests(unittest.TestCase):
    def setUp(self):
        self.node = ClusterNode(
            "gpu-a",
            "a" * 20,
            "ssh",
            "gpu-a",
            "/usr/local/bin/bk",
            0,
            8,
        )
        self.config = ClusterConfig(Path("/etc/gpubk/cluster.json"), (self.node,))
        self.backend = ClusterMcpBackend()

    def invoke(self, payload, call):
        encoded = json.dumps(payload).encode("utf-8")
        with (
            mock.patch("bk.cluster_mcp.load_cluster_config", return_value=self.config),
            mock.patch(
                "bk.cluster_mcp.run_bounded_command",
                return_value=(0, encoded, b""),
            ) as run,
        ):
            result = call()
        return result, run

    def test_context_uses_the_versioned_cluster_cli_with_bounded_execution(self):
        payload = {"schema_version": "gpubk.cluster.v1", "kind": "cluster-context"}

        result, run = self.invoke(payload, self.backend.context)

        self.assertEqual(result, payload)
        command = run.call_args.args[0]
        self.assertEqual(
            command,
            [sys.executable, "-m", "bk", "cluster", "status", "--json"],
        )
        self.assertEqual(run.call_args.kwargs["timeout_seconds"], 50)
        self.assertEqual(run.call_args.kwargs["environment"]["NO_COLOR"], "1")

    def test_readiness_check_can_require_remote_scheduled_job_workers(self):
        _, run = self.invoke(
            {"kind": "cluster-check", "ready": True},
            lambda: self.backend.check(require_jobs=True),
        )

        self.assertEqual(
            run.call_args.args[0][4:],
            ["check", "--jobs", "--json"],
        )

    def test_recommend_forwards_exact_structured_placement_options(self):
        _, run = self.invoke(
            {"kind": "cluster-recommendation"},
            lambda: self.backend.recommend(
                2,
                "45m",
                mode="x",
                start="2030-01-01T12:00:00Z",
                exclude_gpus=[6, 7],
                expected_memory="12g",
            ),
        )

        command = run.call_args.args[0]
        self.assertEqual(command[:4], [sys.executable, "-m", "bk", "cluster"])
        self.assertEqual(command[4:7], ["recommend", "2", "45m"])
        self.assertEqual(command[command.index("--mode") + 1], "exclusive")
        self.assertEqual(
            command[command.index("--start") + 1], "2030-01-01T12:00:00Z"
        )
        self.assertEqual(command[command.index("--exclude-gpu") + 1], "6,7")
        self.assertEqual(command[command.index("--mem") + 1], "12g")
        self.assertNotIn("--op-id", command)

    def test_booking_requires_operation_id_and_keeps_job_argv_after_separator(self):
        with self.assertRaisesRegex(BookingError, "operation_id is required"):
            self.backend.book(1, "30m", "")

        _, run = self.invoke(
            {"kind": "cluster-booking-result"},
            lambda: self.backend.book(
                1,
                "30m",
                "cluster-create-1",
                gpus=[2],
                share=2,
                command=["python", "train.py", "--json", "--op-id", "job-value"],
            ),
        )

        command = run.call_args.args[0]
        separator = command.index("--")
        booking = command[:separator]
        self.assertEqual(booking[booking.index("--op-id") + 1], "cluster-create-1")
        self.assertEqual(booking[booking.index("--gpu") + 1], "2")
        self.assertEqual(booking[booking.index("--share") + 1], "2")
        self.assertEqual(
            command[separator + 1 :],
            ["python", "train.py", "--json", "--op-id", "job-value"],
        )

    def test_edit_and_cancel_require_node_qualified_ids_and_operation_ids(self):
        with self.assertRaisesRegex(BookingError, "node-qualified"):
            self.backend.edit("123456", "edit-1", duration="1h")
        with self.assertRaisesRegex(BookingError, "operation_id is required"):
            self.backend.cancel("gpu-a/123456", "")
        with self.assertRaisesRegex(BookingError, "at least one changed field"):
            self.backend.edit("gpu-a/123456", "edit-1")

        _, edit_run = self.invoke(
            {"kind": "cluster-mutation-result"},
            lambda: self.backend.edit(
                "gpu-a/123456",
                "edit-2",
                duration="1h",
                gpus=[0, 2],
                expected_memory="-",
                allow_queue=True,
            ),
        )
        edit_command = edit_run.call_args.args[0]
        self.assertEqual(edit_command[4:6], ["edit", "gpu-a/123456"])
        self.assertEqual(edit_command[edit_command.index("--op-id") + 1], "edit-2")
        self.assertEqual(edit_command[edit_command.index("--gpu") + 1], "0,2")
        self.assertEqual(edit_command[edit_command.index("--mem") + 1], "-")
        self.assertIn("--queue", edit_command)

        _, cancel_run = self.invoke(
            {"kind": "cluster-mutation-result"},
            lambda: self.backend.cancel("gpu-a/123456", "cancel-1"),
        )
        self.assertEqual(
            cancel_run.call_args.args[0][4:],
            [
                "cancel",
                "gpu-a/123456",
                "--op-id",
                "cancel-1",
                "--json",
            ],
        )

    def test_usage_is_bound_to_the_remote_ssh_identity(self):
        _, run = self.invoke(
            {"kind": "cluster-usage"},
            lambda: self.backend.usage("7d", "1h", 250),
        )

        command = run.call_args.args[0]
        self.assertEqual(command[4], "usage")
        self.assertEqual(command[command.index("--since") + 1], "7d")
        self.assertEqual(command[command.index("--resolution") + 1], "1h")
        self.assertEqual(command[command.index("--limit") + 1], "250")
        self.assertNotIn("--user", command)
        self.assertNotIn("--all", command)

    def test_invalid_inputs_fail_before_starting_a_child(self):
        with mock.patch("bk.cluster_mcp.run_bounded_command") as run:
            with self.assertRaisesRegex(BookingError, "mutually exclusive"):
                self.backend.recommend(1, "30m", gpus=[0], exclude_gpus=[1])
            with self.assertRaisesRegex(BookingError, "non-negative integers"):
                self.backend.book(1, "30m", "op-1", gpus=[True])
            with self.assertRaisesRegex(BookingError, "non-empty argv"):
                self.backend.book(1, "30m", "op-2", command=[])
        run.assert_not_called()

    def test_subprocess_failures_are_bounded_and_do_not_echo_job_arguments(self):
        long_error = ("bad\x00\n" + "x" * 5000).encode()
        with (
            mock.patch("bk.cluster_mcp.load_cluster_config", return_value=self.config),
            mock.patch(
                "bk.cluster_mcp.run_bounded_command",
                return_value=(2, b"", long_error),
            ),
            self.assertRaises(BookingError) as raised,
        ):
            self.backend.book(
                1,
                "30m",
                "op-private",
                command=["python", "--token=do-not-echo"],
            )

        message = str(raised.exception)
        self.assertLessEqual(len(message), 1030)
        self.assertNotIn("do-not-echo", message)
        self.assertNotIn("\x00", message)

    def test_unreachable_context_and_usage_keep_their_structured_diagnostics(self):
        for payload, call in (
            (
                {"kind": "cluster-check", "ready": False, "nodes": []},
                self.backend.check,
            ),
            (
                {"kind": "cluster-context", "nodes": [{"available": False}]},
                self.backend.context,
            ),
            (
                {"kind": "cluster-usage", "nodes": [{"available": False}]},
                self.backend.usage,
            ),
        ):
            with self.subTest(kind=payload["kind"]):
                with (
                    mock.patch(
                        "bk.cluster_mcp.load_cluster_config", return_value=self.config
                    ),
                    mock.patch(
                        "bk.cluster_mcp.run_bounded_command",
                        return_value=(3, json.dumps(payload).encode(), b"unreachable"),
                    ),
                ):
                    self.assertEqual(call(), payload)

    def test_timeout_output_limit_and_invalid_json_are_clear_booking_errors(self):
        cases = (
            (subprocess.TimeoutExpired(["bk"], 1), "timed out"),
            (ValueError("large"), "safe output limit"),
        )
        for failure, message in cases:
            with self.subTest(message=message):
                with (
                    mock.patch(
                        "bk.cluster_mcp.load_cluster_config", return_value=self.config
                    ),
                    mock.patch(
                        "bk.cluster_mcp.run_bounded_command", side_effect=failure
                    ),
                    self.assertRaisesRegex(BookingError, message),
                ):
                    self.backend.context()

        with (
            mock.patch("bk.cluster_mcp.load_cluster_config", return_value=self.config),
            mock.patch(
                "bk.cluster_mcp.run_bounded_command",
                return_value=(0, b"[]", b""),
            ),
            self.assertRaisesRegex(BookingError, "non-object"),
        ):
            self.backend.context()

    def test_timeout_has_a_global_upper_bound(self):
        slow = ClusterNode(
            "gpu-slow",
            "b" * 20,
            "ssh",
            "gpu-slow",
            "/usr/local/bin/bk",
            0,
            500,
        )
        config = ClusterConfig(Path("/etc/gpubk/cluster.json"), (slow,))
        with mock.patch("bk.cluster_mcp.load_cluster_config", return_value=config):
            self.assertEqual(self.backend._timeout_seconds(), 600)


if __name__ == "__main__":
    unittest.main()
