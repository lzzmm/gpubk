import unittest

from bk.node_identity import node_record_extension, record_node_id, stable_node_identity


class NodeIdentityTests(unittest.TestCase):
    def test_identity_is_stable_and_does_not_store_raw_machine_id(self):
        first = stable_node_identity(machine_id="secret-machine-id", hostname="gpu-a")
        second = stable_node_identity(machine_id="secret-machine-id", hostname="gpu-a")

        self.assertEqual(first, second)
        self.assertEqual(len(first["id"]), 20)
        self.assertNotIn("secret-machine-id", str(first))

    def test_record_extension_keeps_node_and_device_identity(self):
        identity = stable_node_identity(machine_id="node-a", hostname="gpu-a")
        extensions = node_record_extension(identity, device_uuid="GPU-123")

        self.assertEqual(record_node_id({"extensions": extensions}), identity["id"])
        self.assertEqual(extensions["gpubk.node"]["device_uuid"], "GPU-123")
        self.assertEqual(record_node_id({}), "legacy")


if __name__ == "__main__":
    unittest.main()
