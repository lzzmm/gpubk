import unittest
from pathlib import Path

from bk.cluster import ClusterConfig, ClusterNode, NodeReply
from bk.cluster_tui import render_cluster_lines


class ClusterTuiTests(unittest.TestCase):
    def test_render_selects_one_node_and_keeps_lines_bounded(self):
        first = ClusterNode("gpu-a", "a" * 20, "local", None, "/usr/bin/bk", 0, 8)
        second = ClusterNode("gpu-b", "b" * 20, "ssh", "gpu-b", "/usr/bin/bk", 10, 8)
        config = ClusterConfig(Path("/cluster.json"), (first, second))
        payload = {
            "actor": {"uid": 1003, "username": "user"},
            "policy": {"gpu_count": 1, "monitoring": {"collector": {"state": "running"}}},
            "gpu_advice": {
                "gpus": [
                    {
                        "index": 0,
                        "live": {"status": "idle", "utilization_percent": 2},
                        "memory": {"free_mb": 24576},
                        "history": {"predicted_percent": 4},
                    }
                ]
            },
            "reservations": [
                {
                    "short_id": "123456",
                    "username": "user",
                    "mode": "shared",
                    "gpus": [0],
                    "start_at": "2030-01-01T00:00:00Z",
                    "end_at": "2030-01-01T01:00:00Z",
                    "mine": True,
                }
            ],
        }
        lines = render_cluster_lines(
            config,
            [NodeReply(first, payload, None), NodeReply(second, None, "timeout")],
            0,
            80,
            24,
        )
        self.assertEqual(len(lines), 24)
        self.assertTrue(all(len(line) <= 80 for line in lines))
        self.assertTrue(any(line.startswith(">gpu-a") for line in lines))
        self.assertTrue(any("123456" in line for line in lines))
        self.assertTrue(any("24.0GiB" in line for line in lines))


if __name__ == "__main__":
    unittest.main()
