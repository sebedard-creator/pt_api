import os
import struct
import tempfile
import unittest

from pt_api import PTBlock, ProToolsSession
from tests.test_loading import PREFIX_0002, SESSION_METADATA, minimal_ptx


NATIVE_90_TRACKS_FIXTURE = os.path.join(
    os.path.dirname(__file__), "fixtures", "native_90_tracks.ptx"
)
NATIVE_90_TRACKS_RESAVED_FIXTURE = os.path.join(
    os.path.dirname(__file__), "fixtures", "native_90_tracks_pt_resaved.ptx"
)


class PlaylistCounterParsingTests(unittest.TestCase):
    @staticmethod
    def track_map(count):
        children = []
        for index in range(count):
            name = ("TRACK_%03d" % index).encode("ascii")
            child = PTBlock(3, 0x1052, 1)
            # Opaque padding makes the accidental block header at count=90
            # plausible within the container. Small fixtures conceal the bug.
            child.items = [
                struct.pack("<I", len(name)) + name + struct.pack("<I", 0),
                bytes(3000),
            ]
            children.append(child)
        root = PTBlock(2, 0x1054, 1)
        root.items = [struct.pack("<I", count), *children]
        return root

    @staticmethod
    def parse(block):
        data = block.to_bytes()[0]
        session = ProToolsSession.__new__(ProToolsSession)
        session.data = data
        session.is_bigendian = False
        parsed, consumed = session._parse_block(0, len(data))
        return session, parsed, consumed, data

    def test_counter_is_raw_and_all_direct_playlists_are_preserved(self):
        for count in (0, 1, 89, 90, 91, 346):
            with self.subTest(count=count):
                session, parsed, consumed, data = self.parse(self.track_map(count))
                self.assertIsInstance(parsed.items[0], bytearray)
                self.assertEqual(bytes(parsed.items[0]), struct.pack("<I", count))
                session.root_items = [parsed]
                self.assertEqual(
                    session.get_tracks(),
                    ["TRACK_%03d" % index for index in range(count)],
                )
                self.assertEqual(consumed, len(data))
                self.assertEqual(parsed.to_bytes()[0], data)

    def test_nested_group_track_map_preserves_its_counter_and_children(self):
        wrapper = PTBlock(1, 0x2428, 1)
        wrapper.items = [self.track_map(90)]
        _, parsed, _, data = self.parse(wrapper)
        nested = parsed.items[0]
        self.assertEqual(nested.content_type, 0x1054)
        self.assertEqual(bytes(nested.items[0]), struct.pack("<I", 90))
        self.assertEqual(
            len([item for item in nested.items if isinstance(item, PTBlock)]), 90
        )
        self.assertEqual(parsed.to_bytes()[0], data)

    def test_truncated_counter_is_preserved_but_not_accepted_as_track_map(self):
        for size in (1, 2, 3):
            with self.subTest(size=size):
                block = PTBlock(2, 0x1054, 1)
                block.items = [b"\x5a" + bytes(size - 1)]
                session, parsed, _, data = self.parse(block)
                self.assertEqual(parsed.to_bytes()[0], data)
                session.root_items = [parsed]
                with self.assertRaisesRegex(ValueError, "counter payload"):
                    session.get_tracks()

    def test_prefix_rule_does_not_skip_children_of_other_container_types(self):
        wrapper = PTBlock(1, 0x2428, 1)
        wrapper.items = [self.track_map(1)]
        _, parsed, _, _ = self.parse(wrapper)
        self.assertIsInstance(parsed.items[0], PTBlock)
        self.assertEqual(parsed.items[0].content_type, 0x1054)

    def test_full_session_pointer_targets_and_repeated_noop_saves(self):
        track_map = self.track_map(90)
        data = track_map.to_bytes()[0]
        track_map_offset = 20 + 11 + len(SESSION_METADATA)
        child_offset = track_map_offset + 9 + 4
        records = []
        for child in track_map.items[1:]:
            records.append(
                PREFIX_0002 + struct.pack("<I", child_offset) + bytes(3)
            )
            child_offset += len(child.to_bytes()[0])
        original = minimal_ptx(
            struct.pack(">H", 90) + b"".join(records), middle=data
        )
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, "source.ptx")
            first = os.path.join(directory, "first.ptx")
            second = os.path.join(directory, "second.ptx")
            with open(source, "wb") as stream:
                stream.write(original)
            session = ProToolsSession(source)
            self.assertEqual(len(session.get_tracks()), 90)
            session.save(first)
            reloaded = ProToolsSession(first)
            self.assertEqual(reloaded.get_tracks(), session.get_tracks())
            reloaded.save(second)
            for path in (source, first, second):
                with open(path, "rb") as stream:
                    self.assertEqual(stream.read(), original)

            # Growing the first playlist must relocate every later standard
            # pointer to the real child block, not to a phantom count block.
            playlists = reloaded._validated_main_playlists()
            playlists[0][0].items[0].extend(b"\x00")
            changed = os.path.join(directory, "changed.ptx")
            reloaded.save(changed)
            changed_session = ProToolsSession(changed)
            self.assertEqual(changed_session.get_tracks(), session.get_tracks())
            changed_table = changed_session.root_items[-1].items[0]
            first_target = track_map_offset + 9 + 4
            expected_target = first_target
            for index, child in enumerate(track_map.items[1:]):
                target = struct.unpack_from("<I", changed_table, 2 + 15 * index + 8)[0]
                self.assertEqual(target, expected_target)
                expected_target += len(child.to_bytes()[0]) + (1 if index == 0 else 0)

    @unittest.skipUnless(
        os.path.isfile(NATIVE_90_TRACKS_FIXTURE),
        "Local native 90-track fixture not installed (not distributed).",
    )
    def test_native_90_tracks_load_and_repeated_noop_saves(self):
        self.assert_native_90_tracks_round_trip(NATIVE_90_TRACKS_FIXTURE)

    @unittest.skipUnless(
        os.path.isfile(NATIVE_90_TRACKS_RESAVED_FIXTURE),
        "Local Pro Tools-resaved 90-track fixture not installed (not distributed).",
    )
    def test_native_90_tracks_after_pro_tools_save_round_trip(self):
        self.assert_native_90_tracks_round_trip(NATIVE_90_TRACKS_RESAVED_FIXTURE)

    def assert_native_90_tracks_round_trip(self, fixture):
        with open(fixture, "rb") as stream:
            original = stream.read()
        session = ProToolsSession(fixture)
        tracks = session.get_tracks()
        self.assertEqual(session.sample_rate, 48_000)
        self.assertEqual(session.frame_rate_enum, 0x09)
        self.assertEqual(len(tracks), 90)
        self.assertEqual(len(set(tracks)), 90)
        self.assertTrue(all(not events for _, _, events in session._validated_main_playlists()))
        with tempfile.TemporaryDirectory() as directory:
            for index in range(2):
                destination = os.path.join(directory, "noop_%d.ptx" % index)
                session.save(destination)
                with open(destination, "rb") as stream:
                    self.assertEqual(stream.read(), original)
                session = ProToolsSession(destination)
                self.assertEqual(session.get_tracks(), tracks)
        with open(fixture, "rb") as stream:
            self.assertEqual(stream.read(), original)


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
