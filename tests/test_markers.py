import struct
import unittest
import uuid

from pt_api import PTBlock, ProToolsSession


def make_session(trailer=b"TRAILER!"):
    session = ProToolsSession.__new__(ProToolsSession)
    session.sample_rate = 48_000
    session.frame_rate_enum = 0x09
    session.is_bigendian = False
    session._removed_offsets = []

    marker_ruler = PTBlock(5, 0x2030, 14)
    marker_ruler.original_offset = 100
    marker_ruler.items = [bytearray(struct.pack("<I", 0) + trailer)]

    track_name = b"AUDIO TRACK"
    track = PTBlock(3, 0x1052, 1)
    track.items = [
        bytearray(
            struct.pack("<I", len(track_name))
            + track_name
            + struct.pack("<I", 0)
        )
    ]
    track_map = PTBlock(2, 0x1054, 1)
    track_map.items = [bytearray(struct.pack("<I", 1)), track]

    session.root_items = [marker_ruler, track_map]
    return session, marker_ruler


def marker_payload(marker_ruler):
    marker = next(
        item for item in marker_ruler.items
        if isinstance(item, PTBlock) and item.content_type == 0x2077
    )
    return marker, marker.items[0], marker.items[2]


class MarkerTests(unittest.TestCase):
    def test_add_marker_preserves_structure_and_wipes_template_offsets(self):
        session, ruler = make_session()

        result = session.add_marker("Repere é", 48_048, index=7)

        self.assertEqual(result, 7)
        self.assertEqual(struct.unpack("<I", ruler.items[0])[0], 1)
        self.assertEqual(ruler.items[-1], b"TRAILER!")
        marker, primary, secondary = marker_payload(ruler)
        self.assertEqual(struct.unpack_from("<H", primary, 0)[0], 7)
        name_length = struct.unpack_from("<I", primary, 6)[0]
        self.assertEqual(primary[10:10 + name_length].decode("utf-8"), "Repere é")
        self.assertEqual(struct.unpack_from("<q", primary, 10 + name_length)[0], 48_048)
        self.assertEqual(secondary[20:23], b"\x00\xff\xff")
        parsed_uuid = uuid.UUID(bytes=bytes(secondary[23:39]))
        self.assertEqual(parsed_uuid.version, 4)
        self.assertEqual(secondary[39:], b"\xff" * 12)
        self.assertTrue(all(block.original_offset == -1 for block in marker.get_all_blocks()))

    def test_second_marker_increments_count_and_uses_next_index(self):
        session, ruler = make_session()
        session.add_marker("First", 0)

        result = session.add_marker("Second", 96_096)

        self.assertEqual(result, 2)
        self.assertEqual(struct.unpack("<I", ruler.items[0])[0], 2)
        self.assertEqual([item["index"] for item in session.get_markers()], [1, 2])

    def test_duplicate_index_is_rejected_without_mutation(self):
        session, ruler = make_session()
        session.add_marker("First", 0, index=3)
        original = ruler.to_bytes(False, 100)[0]

        with self.assertRaisesRegex(ValueError, "already exists"):
            session.add_marker("Duplicate", 100, index=3)

        self.assertEqual(ruler.to_bytes(False, 100)[0], original)

    def test_inconsistent_marker_count_is_rejected(self):
        session, ruler = make_session()
        struct.pack_into("<I", ruler.items[0], 0, 1)

        with self.assertRaisesRegex(ValueError, "empty.*counter"):
            session.add_marker("Invalid", 0)

    def test_missing_marker_ruler_is_rejected(self):
        session, _ = make_session()
        session.root_items = [
            item for item in session.root_items
            if not isinstance(item, PTBlock) or item.content_type != 0x2030
        ]

        with self.assertRaisesRegex(ValueError, "No valid.*marker ruler"):
            session.add_marker("Marker", 0)

    def test_session_without_audio_track_is_rejected(self):
        session, ruler = make_session()
        session.root_items = [ruler]

        with self.assertRaisesRegex(ValueError, "without an audio track"):
            session.add_marker("Marker", 0)

    def test_malformed_track_map_is_rejected_before_marker_mutation(self):
        session, ruler = make_session()
        track_map = session.root_items[1]
        struct.pack_into("<I", track_map.items[0], 0, 2)
        original = ruler.to_bytes(False, 100)[0]

        with self.assertRaisesRegex(ValueError, "track count"):
            session.add_marker("Marker", 0)

        self.assertEqual(ruler.to_bytes(False, 100)[0], original)

    def test_invalid_inputs_are_rejected(self):
        session, _ = make_session()

        with self.assertRaisesRegex(ValueError, "between 1 and 65535"):
            session.add_marker("Marker", 0, index=0)
        with self.assertRaisesRegex(ValueError, "null byte"):
            session.add_marker("Bad\x00Name", 0)
        with self.assertRaisesRegex(ValueError, "timestamp"):
            session.add_marker("Marker", -1)
        with self.assertRaisesRegex(ValueError, "valid UTF-8"):
            session.add_marker("Bad\ud800Name", 0)


if __name__ == "__main__":
    unittest.main()
