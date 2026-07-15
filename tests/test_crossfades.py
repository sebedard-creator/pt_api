import struct
import unittest

from pt_api import PTBlock, ProToolsSession


def make_session(declared_count=0, actual_count=0):
    session = ProToolsSession.__new__(ProToolsSession)
    session.sample_rate = 48_000
    session.frame_rate_enum = 0x09
    session.is_bigendian = False
    session._removed_offsets = []

    geometries = []
    for _ in range(actual_count):
        geometry = PTBlock(2, 0x262F, 1)
        geometry.items = [bytearray(36)]
        geometries.append(geometry)
    geometry_list = PTBlock(1, 0x2630, 1)
    geometry_list.items = [
        bytearray(struct.pack("<I", declared_count)),
        *geometries,
    ]
    session.root_items = [geometry_list]
    return session


def make_event(clip_id, timestamp):
    payload = bytearray(35)
    struct.pack_into("<I", payload, 2, clip_id)
    struct.pack_into("<Q", payload, 7, timestamp)
    payload[15] = 0x03
    b104f = PTBlock(10, 0x104F, 1)
    b104f.items = [payload]
    event = PTBlock(3, 0x1050, 1)
    event.items = [b104f, bytearray(b"\x00\x01\x01")]
    return event


class CrossfadePreflightTests(unittest.TestCase):
    def test_zero_duration_is_rejected_before_implementation(self):
        session = make_session()
        called = []
        session._add_crossfade_impl = lambda *args: called.append(True)

        with self.assertRaisesRegex(ValueError, "greater than zero"):
            session.add_crossfade("T", "C", 0, 0, 1, 0, 0, 0, 0, 0)

        self.assertEqual(called, [])

    def test_inconsistent_geometry_count_is_rejected(self):
        session = make_session(declared_count=1, actual_count=0)

        with self.assertRaisesRegex(ValueError, "geometry count"):
            session.add_crossfade("T", "C", 0, 0, 1, 0, 0, 0, 1, 0)

    def test_duplicate_geometry_roots_are_rejected_before_implementation(self):
        session = make_session()
        duplicate = PTBlock(1, 0x2630, 1)
        duplicate.items = [bytearray(struct.pack("<I", 0))]
        session.root_items.append(duplicate)
        called = []
        session._add_crossfade_impl = lambda *args: called.append(True)

        with self.assertRaisesRegex(ValueError, "unique fade geometry list"):
            session.add_crossfade("T", "C", 0, 0, 1, 0, 0, 0, 1, 0)

        self.assertEqual(called, [])

    def test_late_failure_rolls_back_every_mutation(self):
        session = make_session()
        original_bytes = session.root_items[0].to_bytes(False, 20)[0]

        def fail_after_mutation(*args):
            session.root_items[0].items[0] = bytearray(struct.pack("<I", 99))
            session._removed_offsets.append(123)
            raise RuntimeError("late failure")

        session._add_crossfade_impl = fail_after_mutation

        with self.assertRaisesRegex(RuntimeError, "late failure"):
            session.add_crossfade("T", "C", 0, 0, 1, 0, 0, 0, 1, 0)

        self.assertEqual(session.root_items[0].to_bytes(False, 20)[0], original_bytes)
        self.assertEqual(session._removed_offsets, [])

    def test_native_geometry_and_link_flags_match_reference(self):
        # One existing geometry proves that the new fade ID is the geometry
        # index, not the parent clip ID returned by split_clip().
        session = make_session(declared_count=1, actual_count=1)
        left = make_event(1, 0)
        right = make_event(2, 1_000)
        track_name = b"TRACK"
        track_header = bytearray(
            struct.pack("<I", len(track_name)) + track_name + struct.pack("<I", 2)
        )
        track = PTBlock(3, 0x1052, 1)
        track.items = [track_header, left, right, bytearray(b"\x01\x00")]
        timeline = PTBlock(2, 0x1054, 1)
        timeline.items = [bytearray(struct.pack("<I", 1)), track]
        session.root_items.append(timeline)
        session.split_clip = lambda *args: (0, 1, 2, 1_000)

        session._add_crossfade_impl(
            "TRACK", "CLIP", 0, 0, 1, 0, 0, 0, 1, 0
        )

        geometry_list = session.root_items[0]
        geometry = [
            item for item in geometry_list.items
            if isinstance(item, PTBlock) and item.content_type == 0x262F
        ][-1]
        expected_geometry = bytearray(34)
        expected_geometry[5] = 0x22
        struct.pack_into("<H", expected_geometry, 8, 24_024)
        struct.pack_into("<H", expected_geometry, 10, 48_048)
        expected_geometry[12:14] = b"\x01\x01"
        self.assertEqual(geometry.items[0], expected_geometry)

        events = [
            item for item in track.items
            if isinstance(item, PTBlock) and item.content_type == 0x1050
        ]
        self.assertEqual(len(events), 3)
        fade_payload = events[1].items[0].items[0]
        right_payload = events[2].items[0].items[0]
        self.assertEqual(struct.unpack_from("<I", fade_payload, 2)[0], 1)
        self.assertEqual(fade_payload[0], 0)
        self.assertEqual(fade_payload[33], 1)
        self.assertEqual(right_payload[33], 1)

    def test_invalid_utf8_track_name_is_rejected_and_rolled_back(self):
        session = make_session()
        left = make_event(1, 0)
        right = make_event(2, 1_000)
        track = PTBlock(3, 0x1052, 1)
        track.items = [
            bytearray(struct.pack("<I", 1) + b"\xff" + struct.pack("<I", 2)),
            left,
            right,
        ]
        timeline = PTBlock(2, 0x1054, 1)
        timeline.items = [bytearray(struct.pack("<I", 1)), track]
        session.root_items.append(timeline)
        session.split_clip = lambda *args: (0, 1, 2, 1_000)
        original_tree = [
            item.to_bytes(False, 20)[0]
            for item in session.root_items
        ]

        with self.assertRaisesRegex(ValueError, "Invalid UTF-8 track name"):
            session.add_crossfade(
                "TRACK", "CLIP", 0, 0, 1, 0, 0, 0, 1, 0
            )

        self.assertEqual(
            [item.to_bytes(False, 20)[0] for item in session.root_items],
            original_tree,
        )


if __name__ == "__main__":
    unittest.main()
