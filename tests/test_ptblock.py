import os
import struct
import tempfile
import unittest

from pt_api import PTBlock, ProToolsSession


class PTBlockSerializationTests(unittest.TestCase):
    def test_serialization_is_stable_and_maps_nested_offsets(self):
        child = PTBlock(2, 0x1234, 1)
        child.items = [b"A"]
        child.original_offset = 200

        root = PTBlock(1, 0x5678, 1)
        root.items = [bytearray(b"X"), child]
        root.original_offset = 100
        original_raw_item = bytes(root.items[0])

        payload, mapping = root.to_bytes(False, 20)

        self.assertEqual(payload[0], 0x5A)
        self.assertEqual(struct.unpack_from("<H", payload, 1)[0], 1)
        self.assertEqual(struct.unpack_from("<H", payload, 7)[0], 0x5678)
        self.assertEqual(mapping, {100: 20, 200: 30})
        self.assertEqual(bytes(root.items[0]), original_raw_item)

    def test_cycles_and_duplicate_nested_offsets_are_rejected(self):
        cyclic = PTBlock(1, 0x1000, 1)
        cyclic.items = [cyclic]
        with self.assertRaisesRegex(ValueError, "Cycle"):
            cyclic.to_bytes()
        with self.assertRaisesRegex(ValueError, "Cycle"):
            cyclic.get_all_blocks()

        first = PTBlock(1, 0x1001, 1)
        first.items = [b"A"]
        first.original_offset = 50
        second = PTBlock(1, 0x1002, 1)
        second.items = [b"B"]
        second.original_offset = 50
        root = PTBlock(1, 0x1000, 1)
        root.items = [first, second]
        with self.assertRaisesRegex(ValueError, "Duplicate original block offset"):
            root.to_bytes()

    def test_invalid_fields_items_and_file_range_are_rejected(self):
        block = PTBlock(0x100, 0x1000, 1)
        block.items = [b"A"]
        with self.assertRaisesRegex(ValueError, "block_type"):
            block.to_bytes()

        block = PTBlock(1, 0x1000, 1)
        block.items = [123]
        with self.assertRaisesRegex(TypeError, "PTBlock items"):
            block.to_bytes()

        block.items = [b"A"]
        with self.assertRaisesRegex(OverflowError, "UInt32 file range"):
            block.to_bytes(base_offset=0xFFFFFFFF)

    def test_excessive_in_memory_nesting_is_rejected_cleanly(self):
        root = PTBlock(1, 0x1000, 1)
        current = root
        for _ in range(130):
            child = PTBlock(1, 0x1000, 1)
            current.items = [child]
            current = child

        with self.assertRaisesRegex(ValueError, "nesting exceeds"):
            root.to_bytes()
        with self.assertRaisesRegex(ValueError, "nesting exceeds"):
            root.get_all_blocks()

    def test_duplicate_offsets_across_root_blocks_abort_before_write(self):
        session = ProToolsSession.__new__(ProToolsSession)
        session.is_bigendian = False
        session.data = bytearray(b"\x00" * 18 + b"\x01\x00")
        session._removed_offsets = []

        first = PTBlock(1, 0x1000, 1)
        first.items = [b"A"]
        first.original_offset = 100
        second = PTBlock(1, 0x1001, 1)
        second.items = [b"B"]
        second.original_offset = 100
        session.root_items = [first, second]

        with tempfile.TemporaryDirectory() as directory:
            destination = os.path.join(directory, "session.ptx")
            with self.assertRaisesRegex(ValueError, "Duplicate original block offset"):
                session.save(destination)
            self.assertFalse(os.path.exists(destination))


if __name__ == "__main__":
    unittest.main()
