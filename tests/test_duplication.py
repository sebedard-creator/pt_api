import struct
import unittest

from pt_api import PTBlock, ProToolsSession


def make_block(block_type, content_type, items):
    block = PTBlock(block_type, content_type, 1)
    block.items = items
    return block


def make_clip(name="CLIP"):
    name_bytes = name.encode("utf-8")
    payload = bytearray(struct.pack("<I", len(name_bytes)) + name_bytes)
    payload.extend(b"\x00\x00\x30\x44\x08")
    payload.extend(struct.pack("<I", 48_000))
    payload.extend(b"\x00" * 32)
    return make_block(11, 0x2629, [make_block(4, 0x2628, [payload]), bytearray(48)])


def make_event(timestamp, event_type=0x03, clip_id=0):
    payload = bytearray(35)
    struct.pack_into("<I", payload, 2, clip_id)
    struct.pack_into("<Q", payload, 7, timestamp)
    payload[15] = event_type
    secondary = b"\x00\x01\x01" if event_type == 0x03 else b"\x01\x01\x01"
    return make_block(3, 0x1050, [make_block(10, 0x104F, [payload]), bytearray(secondary)])


def make_session(events):
    session = ProToolsSession.__new__(ProToolsSession)
    session.sample_rate = 48_000
    session.frame_rate_enum = 0x01
    session.is_bigendian = False
    session._removed_offsets = []

    clip_list = make_block(
        1,
        0x262A,
        [
            bytearray(struct.pack("<I", 2)),
            make_clip("CLIP"),
            make_clip("OTHER"),
        ],
    )
    track_name = b"TRACK"
    header = bytearray(
        struct.pack("<I", len(track_name))
        + track_name
        + struct.pack("<I", len(events))
    )
    track = make_block(3, 0x1052, [header, *events, bytearray(b"\x01\x00")])
    session.root_items = [
        clip_list,
        make_block(2, 0x1054, [bytearray(struct.pack("<I", 1)), track]),
    ]
    return session, track


def add_geometry_list(session, payloads):
    geometries = [make_block(2, 0x262F, [bytearray(payload)]) for payload in payloads]
    session.root_items.append(
        make_block(
            1,
            0x2630,
            [bytearray(struct.pack("<I", len(geometries))), *geometries],
        )
    )


def event_details(track):
    details = []
    for event in track.items:
        if not isinstance(event, PTBlock) or event.content_type != 0x1050:
            continue
        b104f = next(x for x in event.items if isinstance(x, PTBlock) and x.content_type == 0x104F)
        payload = b104f.items[0]
        details.append((struct.unpack_from("<Q", payload, 7)[0], payload[15]))
    return details


class DuplicationTests(unittest.TestCase):
    def test_duplicate_before_existing_events_keeps_playlist_sorted(self):
        session, track = make_session([
            make_event(100), make_event(200, clip_id=1)
        ])

        session.duplicate_clip("CLIP", 0, 0, 0, 0)

        self.assertEqual(event_details(track), [(0, 3), (100, 3), (200, 3)])
        name_len = struct.unpack_from("<I", track.items[0], 0)[0]
        self.assertEqual(struct.unpack_from("<I", track.items[0], 4 + name_len)[0], 3)

    def test_duplicate_ignores_fade_with_colliding_geometry_id(self):
        session, track = make_session([make_event(50, 0x01), make_event(100, 0x03)])
        add_geometry_list(session, [bytearray(22)])

        session.duplicate_clip("CLIP", 0, 0, 0, 0)

        self.assertEqual(event_details(track), [(0, 3), (50, 1), (100, 3)])

    def test_duplicate_after_existing_events_stays_before_trailing_bytes(self):
        session, track = make_session([
            make_event(100), make_event(200, clip_id=1)
        ])

        session.duplicate_clip("CLIP", 0, 0, 0, 1)

        self.assertEqual(event_details(track), [(100, 3), (200, 3), (2_000, 3)])
        self.assertIsInstance(track.items[-1], bytearray)
        self.assertEqual(track.items[-1], b"\x01\x00")

    def test_multiple_source_placements_are_rejected_without_mutation(self):
        session, track = make_session([make_event(100), make_event(200)])
        original = track.to_bytes(False, 100)[0]

        with self.assertRaisesRegex(ValueError, "source is ambiguous"):
            session.duplicate_clip("CLIP", 0, 0, 0, 0)

        restored_track = session.root_items[1].items[1]
        self.assertEqual(restored_track.to_bytes(False, 100)[0], original)

    def test_attached_fade_is_rejected_without_mutation(self):
        fade = make_event(100, 0x01)
        audio = make_event(100, 0x03)
        session, track = make_session([fade, audio])
        add_geometry_list(session, [bytearray(22)])
        original = track.to_bytes(False, 100)[0]

        with self.assertRaisesRegex(NotImplementedError, "attached fades"):
            session.duplicate_clip("CLIP", 0, 0, 0, 0)

        restored_track = session.root_items[1].items[1]
        self.assertEqual(restored_track.to_bytes(False, 100)[0], original)

    def test_mute_must_be_boolean(self):
        session, _ = make_session([make_event(100)])

        with self.assertRaisesRegex(TypeError, "mute must be a boolean"):
            session.duplicate_clip("CLIP", 0, 0, 0, 0, mute=1)

    def test_target_beyond_uint64_is_rejected_without_mutation(self):
        session, track = make_session([make_event(100)])
        original = track.to_bytes(False, 100)[0]

        with self.assertRaisesRegex(ValueError, "UInt64"):
            session.duplicate_clip("CLIP", 200_000_000_000, 0, 0, 0)

        restored_track = session.root_items[1].items[1]
        self.assertEqual(restored_track.to_bytes(False, 100)[0], original)

    def test_late_failure_rolls_back_duplicate_operation(self):
        session, track = make_session([make_event(100)])
        original = track.to_bytes(False, 100)[0]

        def fail_after_mutation(*args):
            session.root_items[1].items[1].items[0][0] = 0xFF
            session._removed_offsets.append(123)
            raise RuntimeError("late failure")

        session._duplicate_clip_impl = fail_after_mutation
        with self.assertRaisesRegex(RuntimeError, "late failure"):
            session.duplicate_clip("CLIP", 0, 0, 0, 0)

        restored_track = session.root_items[1].items[1]
        self.assertEqual(restored_track.to_bytes(False, 100)[0], original)
        self.assertEqual(session._removed_offsets, [])


if __name__ == "__main__":
    unittest.main()
