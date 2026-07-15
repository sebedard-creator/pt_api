import copy
import os
import struct
import tempfile
import unittest
from unittest import mock

from pt_api import PTBlock, ProToolsSession


PREFIX_0002 = bytes.fromhex("0000000104000100")


class RepeatedSaveOffsetTests(unittest.TestCase):
    def test_dangerous_global_offset_wipe_is_not_public(self):
        self.assertFalse(hasattr(ProToolsSession, "wipe_all_offsets"))

    def _mapping(self, session):
        mapping = {}
        current_offset = 0x14
        for item in session.root_items:
            if isinstance(item, PTBlock):
                block_bytes, block_mapping = item.to_bytes(
                    session.is_bigendian, current_offset
                )
                mapping.update(block_mapping)
                current_offset += len(block_bytes)
            else:
                current_offset += len(item)
        return mapping

    def _minimal_savable_session(self):
        session = ProToolsSession.__new__(ProToolsSession)
        session.is_bigendian = False
        session.data = bytearray(20)
        session.data[0] = 0x03
        session.data[1:17] = b"0" * 16
        session.data[0x11] = 0x00
        session.data[0x12] = 0x01
        session.data[0x13] = 0x00
        session.file_path = "original.ptx"
        session._removed_offsets = []

        pointer = PTBlock(1, 0x0000, 4)
        pointer.items = [bytearray(b"\x00\x00")]
        pointer.original_offset = 0x14

        sample_rate = PTBlock(1, 0x1028, 8)
        sample_rate.items = [bytearray(b"\x00\x00" + struct.pack("<I", 48_000))]
        frame_rate = PTBlock(1, 0x204D, 3)
        frame_rate.items = [bytearray(b"\x09")]

        pointer_table = PTBlock(1, 0x0002, 2)
        pointer_table.items = []
        pointer_table.original_offset = 0x1F
        session.root_items = [pointer, sample_rate, frame_rate, pointer_table]
        return session

    def test_failed_save_preserves_destination_and_restores_memory(self):
        session = self._minimal_savable_session()
        original_tree = [
            item.to_bytes(False, 0x14)[0] for item in session.root_items
        ]

        with tempfile.TemporaryDirectory() as directory:
            destination = os.path.join(directory, "session.ptx")
            with open(destination, "wb") as output:
                output.write(b"ORIGINAL")

            def fail_after_partial_write(_, temporary_path):
                with open(temporary_path, "wb") as output:
                    output.write(b"PARTIAL")
                raise OSError("simulated write failure")

            with mock.patch("pt_api.xor_session", side_effect=fail_after_partial_write):
                with self.assertRaisesRegex(OSError, "simulated write failure"):
                    session.save(destination)

            with open(destination, "rb") as output:
                self.assertEqual(output.read(), b"ORIGINAL")
            self.assertEqual(os.listdir(directory), ["session.ptx"])

        restored_tree = [
            item.to_bytes(False, 0x14)[0] for item in session.root_items
        ]
        self.assertEqual(restored_tree, original_tree)
        self.assertEqual(session.file_path, "original.ptx")
        self.assertEqual(session._removed_offsets, [])

    def test_successful_save_atomically_replaces_destination(self):
        session = self._minimal_savable_session()

        with tempfile.TemporaryDirectory() as directory:
            destination = os.path.join(directory, "session.ptx")
            with open(destination, "wb") as output:
                output.write(b"OLD")

            session.save(destination)

            with open(destination, "rb") as output:
                self.assertNotEqual(output.read(), b"OLD")
            self.assertEqual(os.listdir(directory), ["session.ptx"])
            self.assertEqual(session.file_path, os.path.abspath(destination))
            self.assertEqual(session.sample_rate, 48_000)
            self.assertEqual(session.frame_rate_enum, 0x09)

    def test_save_rejects_invalid_pointer_table_structure_before_write(self):
        cases = []

        missing = self._minimal_savable_session()
        missing.root_items.pop()
        cases.append(("missing", missing, "unique root 0x0002"))

        duplicate = self._minimal_savable_session()
        extra_table = copy.deepcopy(duplicate.root_items[-1])
        extra_table.original_offset = 0
        duplicate.root_items.append(extra_table)
        cases.append(("duplicate", duplicate, "unique root 0x0002"))

        nonfinal = self._minimal_savable_session()
        nonfinal.root_items.append(bytearray(b"TRAILING"))
        cases.append(("nonfinal", nonfinal, "final root block"))

        for label, session, message in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                destination = os.path.join(directory, "session.ptx")
                with self.assertRaisesRegex(ValueError, message):
                    session.save(destination)
                self.assertFalse(os.path.exists(destination))

    def test_save_accepts_root_bytes_and_rejects_unknown_root_items(self):
        session = self._minimal_savable_session()
        session.root_items.insert(-1, b"ROOT-METADATA")

        with tempfile.TemporaryDirectory() as directory:
            destination = os.path.join(directory, "session.ptx")
            session.save(destination)
            with open(destination, "rb") as saved:
                self.assertIn(b"ROOT-METADATA", saved.read())

        invalid = self._minimal_savable_session()
        invalid.root_items.insert(-1, object())
        with tempfile.TemporaryDirectory() as directory:
            destination = os.path.join(directory, "session.ptx")
            with self.assertRaisesRegex(TypeError, "Session root items"):
                invalid.save(destination)
            self.assertFalse(os.path.exists(destination))

    def test_refresh_offsets_allows_a_second_pointer_rebuild(self):
        session = ProToolsSession.__new__(ProToolsSession)
        session.is_bigendian = False

        target = PTBlock(1, 0x1000, 3)
        target.items = [bytearray(b"A")]
        target.original_offset = 100

        pointer_table = PTBlock(1, 0x0002, 18)
        pointer_table.items = [
            bytearray(PREFIX_0002 + struct.pack("<I", 100) + b"META")
        ]
        pointer_table.original_offset = 200
        session.root_items = [target, pointer_table]

        first_mapping = self._mapping(session)
        session._rebuild_0002(pointer_table, first_mapping, False)
        first_pointer = struct.unpack_from("<I", pointer_table.items[0], 8)[0]
        self.assertEqual(first_pointer, 0x14)

        session._refresh_original_offsets()
        self.assertEqual(target.original_offset, 0x14)

        session.root_items.insert(0, bytearray(b"ABC"))
        second_mapping = self._mapping(session)
        session._rebuild_0002(pointer_table, second_mapping, False)
        second_pointer = struct.unpack_from("<I", pointer_table.items[0], 8)[0]

        self.assertEqual(second_pointer, 0x17)

    def test_pointer_purge_keeps_metadata_between_records(self):
        session = ProToolsSession.__new__(ProToolsSession)
        first_record = PREFIX_0002 + struct.pack("<I", 100) + b"\x00\x00\x00"
        second_record = PREFIX_0002 + struct.pack("<I", 200) + b"\x00\x00\x00"
        metadata = b"INDEPENDENT-METADATA"
        pointer_table = PTBlock(1, 0x0002, 1)
        pointer_table.items = [
            bytearray(
                b"HEAD" + struct.pack(">H", 1) + first_record
                + metadata + struct.pack(">H", 1) + second_record + b"TAIL"
            )
        ]

        purged = session._purge_0002_records(pointer_table, [100], False)

        self.assertEqual(purged, 1)
        self.assertEqual(
            pointer_table.items[0],
            b"HEAD" + struct.pack(">H", 0)
            + metadata + struct.pack(">H", 1) + second_record + b"TAIL",
        )

    def test_pointer_purge_decrements_the_record_run_count(self):
        session = ProToolsSession.__new__(ProToolsSession)
        parent_record = PREFIX_0002 + struct.pack("<I", 100) + b"\x00\x00\x00"
        child_record = PREFIX_0002 + struct.pack("<I", 200) + b"\x00\x00\x00"
        pointer_table = PTBlock(1, 0x0002, 1)
        pointer_table.items = [
            bytes(struct.pack(">H", 2) + parent_record + child_record)
        ]

        purged = session._purge_0002_records(pointer_table, [200], False)

        self.assertEqual(purged, 1)
        self.assertEqual(
            pointer_table.items[0],
            struct.pack(">H", 1) + parent_record,
        )

    def test_pointer_table_reassembly_preserves_bytes_and_rejects_blocks(self):
        session = ProToolsSession.__new__(ProToolsSession)
        pointer_table = PTBlock(1, 0x0002, 1)
        pointer_table.items = [b"META", struct.pack("<I", 100), b"TAIL"]

        patched = session._rebuild_0002(pointer_table, {100: 250}, False)

        self.assertEqual(patched, 1)
        self.assertEqual(pointer_table.items, [b"META" + struct.pack("<I", 250) + b"TAIL"])

        pointer_table.items = [PTBlock(1, 0x1000, 0)]
        with self.assertRaisesRegex(ValueError, "only raw bytes"):
            session._rebuild_0002(pointer_table, {}, False)

    def test_rebuild_relocates_offsets_outside_standard_records(self):
        session = ProToolsSession.__new__(ProToolsSession)
        pointer_table = PTBlock(1, 0x0002, 1)
        pointer_table.items = [
            bytearray(b"META" + struct.pack("<I", 100) + b"TAIL")
        ]

        patched = session._rebuild_0002(pointer_table, {100: 250}, False)

        self.assertEqual(patched, 1)
        self.assertEqual(
            pointer_table.items[0],
            b"META" + struct.pack("<I", 250) + b"TAIL",
        )

    def test_rebuild_does_not_patch_a_value_crossing_record_prefix(self):
        session = ProToolsSession.__new__(ProToolsSession)
        pointer_table = PTBlock(1, 0x0002, 1)
        pointer_table.items = [
            bytearray(
                struct.pack(">H", 1)
                + PREFIX_0002
                + struct.pack("<I", 0x00000155)
                + b"\x00\x00\x00"
            )
        ]

        # 0x00015500 is the accidental UInt32 formed by the prefix's final
        # zero byte plus the first three bytes of the real pointer.
        patched = session._rebuild_0002(
            pointer_table,
            {0x00000155: 0x00000200, 0x00015500: 0x00015774},
            False,
        )

        expected = (
            struct.pack(">H", 1)
            + PREFIX_0002
            + struct.pack("<I", 0x00000200)
            + b"\x00\x00\x00"
        )
        self.assertEqual(patched, 1)
        self.assertEqual(pointer_table.items[0], expected)


if __name__ == "__main__":
    unittest.main()
