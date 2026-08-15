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


def marker_blocks(marker_ruler):
    return [
        item for item in marker_ruler.items
        if isinstance(item, PTBlock) and item.content_type == 0x2077
    ]


def add_marker_track_catalogue(session, names):
    """Add the verified read-only marker-ruler catalogue test profile."""
    entries = []
    for track_id, name in enumerate(names):
        encoded_name = name.encode("utf-8")
        entry = PTBlock(1, 0x251C, 1)
        entry.items = [bytearray(
            struct.pack("<HI", track_id, len(encoded_name))
            + encoded_name
            + b"\x01"
            + struct.pack("<I", track_id)
        )]
        entries.append(entry)
    # Native non-ruler entries share the same container and must be ignored.
    other = PTBlock(1, 0x251C, 1)
    other.items = [bytearray(b"\x03\x00\x00\x00\x00\x00\x03\x00\x00\x00\x00")]
    catalogue = PTBlock(2, 0x251B, 1)
    catalogue.items = [bytearray(struct.pack("<I", len(entries) + 1)), *entries, other]
    root = PTBlock(3, 0x2519, 1)
    root.items = [catalogue]
    session.root_items.append(root)


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

    def test_get_markers_filters_by_native_marker_track_name(self):
        session, ruler = make_session()
        session.add_marker("M1_A", 48_048)
        session.add_marker("M2_A", 96_096)
        session.add_marker("M3_A", 144_144)
        for track_id, marker in enumerate(marker_blocks(ruler)):
            marker.items[-1] = bytearray(struct.pack("<II", 0, track_id))
        add_marker_track_catalogue(session, ["MARKER 1", "MARKER 2", "MARKER 3"])

        self.assertEqual(
            session.get_markers("MARKER 2"),
            [{"index": 2, "name": "M2_A", "timecode": "00:00:02:00"}],
        )
        self.assertEqual(
            [marker["name"] for marker in session.get_markers()],
            ["M1_A", "M2_A", "M3_A"],
        )

    def test_marker_track_filter_rejects_unknown_or_invalid_native_assignment(self):
        session, ruler = make_session()
        session.add_marker("M1_A", 48_048)
        marker_blocks(ruler)[0].items[-1] = bytearray(struct.pack("<II", 0, 9))
        add_marker_track_catalogue(session, ["MARKER 1"])

        with self.assertRaisesRegex(ValueError, "assignment"):
            session.get_markers("MARKER 1")
        with self.assertRaisesRegex(ValueError, "not found"):
            session.get_markers("UNKNOWN")
        with self.assertRaisesRegex(TypeError, "must be a string"):
            session.get_markers(1)

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
