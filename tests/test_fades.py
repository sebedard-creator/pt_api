import struct
import unittest

from pt_api import PTBlock, ProToolsSession


def make_block(block_type, content_type, items):
    block = PTBlock(block_type, content_type, 1)
    block.items = items
    return block


def make_clip(name, clip_id, length=48_000):
    name_bytes = name.encode("utf-8")
    payload = bytearray(struct.pack("<I", len(name_bytes)) + name_bytes)
    payload.extend(b"\x00\x00\x30\x44\x08")
    payload.extend(struct.pack("<I", length))
    payload.extend(b"\x00" * 32)
    b2628 = make_block(4, 0x2628, [payload])
    definition_payload = bytearray(48)
    struct.pack_into("<I", definition_payload, 0, clip_id)
    return make_block(11, 0x2629, [b2628, definition_payload])


def make_audio_event(clip_id, timestamp=0):
    payload = bytearray(35)
    struct.pack_into("<I", payload, 2, clip_id)
    struct.pack_into("<Q", payload, 7, timestamp)
    payload[15] = 0x03
    b104f = make_block(10, 0x104F, [payload])
    return make_block(3, 0x1050, [b104f, bytearray(b"\x00\x01\x01")])


def make_session():
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
            make_clip("OTHER", 0),
            make_clip("TARGET", 1),
        ],
    )
    geometry_list = make_block(1, 0x2630, [bytearray(struct.pack("<I", 0))])

    track_name = b"TRACK"
    track_header = bytearray(
        struct.pack("<I", len(track_name))
        + track_name
        + struct.pack("<I", 1)
    )
    track = make_block(
        3,
        0x1052,
        [track_header, make_audio_event(1), bytearray(b"\x01\x00")],
    )
    track_map = make_block(
        2,
        0x1054,
        [bytearray(struct.pack("<I", 1)), track],
    )
    session.root_items = [clip_list, geometry_list, track_map]
    return session, geometry_list, track


def timeline_events(track):
    return [
        item
        for item in track.items
        if isinstance(item, PTBlock) and item.content_type == 0x1050
    ]


def event_payload(event):
    return next(
        item
        for item in event.items
        if isinstance(item, PTBlock) and item.content_type == 0x104F
    ).items[0]


class FadeTests(unittest.TestCase):
    def test_fade_in_points_to_geometry_and_precedes_audio(self):
        session, geometry_list, track = make_session()

        session.add_fade("TRACK", "TARGET", 0, 0, 0, 0, duration_ff=1)

        geometries = [
            item
            for item in geometry_list.items
            if isinstance(item, PTBlock) and item.content_type == 0x262F
        ]
        self.assertEqual(struct.unpack_from("<I", geometry_list.items[0], 0)[0], 1)
        self.assertEqual(len(geometries), 1)
        self.assertEqual(struct.unpack_from("<H", geometries[0].items[0], 8)[0], 2_000)

        events = timeline_events(track)
        self.assertEqual(len(events), 2)
        fade_payload = event_payload(events[0])
        audio_payload = event_payload(events[1])
        self.assertEqual(fade_payload[15], 0x01)
        self.assertEqual(audio_payload[15], 0x03)
        self.assertEqual(struct.unpack_from("<I", fade_payload, 2)[0], 0)
        self.assertEqual(fade_payload[0], 0)

        name_len = struct.unpack_from("<I", track.items[0], 0)[0]
        self.assertEqual(struct.unpack_from("<I", track.items[0], 4 + name_len)[0], 2)

    def test_fade_out_points_to_geometry_and_follows_audio(self):
        session, geometry_list, track = make_session()

        session.add_fade("TRACK", "TARGET", 0, 0, 1, 0, fade_type="out", duration_ff=1)

        events = timeline_events(track)
        self.assertEqual(len(events), 2)
        audio_payload = event_payload(events[0])
        fade_payload = event_payload(events[1])
        self.assertEqual(audio_payload[15], 0x03)
        self.assertEqual(fade_payload[15], 0x01)
        self.assertEqual(struct.unpack_from("<I", fade_payload, 2)[0], 0)
        self.assertEqual(struct.unpack_from("<Q", fade_payload, 7)[0], 48_000)
        self.assertEqual(fade_payload[0], 0)
        geometry = next(
            item for item in geometry_list.items
            if isinstance(item, PTBlock) and item.content_type == 0x262F
        )
        self.assertEqual(len(geometry.items[0]), 27)

    def test_unknown_clip_is_rejected_without_mutation(self):
        session, geometry_list, track = make_session()

        with self.assertRaisesRegex(ValueError, "not found"):
            session.add_fade("TRACK", "MISSING", 0, 0, 0, 0, duration_ff=1)

        self.assertEqual(struct.unpack_from("<I", geometry_list.items[0], 0)[0], 0)
        self.assertEqual(len(timeline_events(track)), 1)

    def test_standalone_geometry_rejects_lengths_over_16_bits(self):
        session, geometry_list, track = make_session()

        with self.assertRaisesRegex(ValueError, "16-bit"):
            session.add_fade("TRACK", "TARGET", 0, 0, 0, 0, duration_ss=2)

        self.assertEqual(struct.unpack_from("<I", geometry_list.items[0], 0)[0], 0)
        self.assertEqual(len(timeline_events(track)), 1)

    def test_geometry_ids_survive_reverse_chronological_insertion(self):
        session, geometry_list, track = make_session()

        session.add_fade(
            "TRACK", "TARGET", 0, 0, 1, 0,
            fade_type="out", duration_ff=1,
        )
        session.add_fade(
            "TRACK", "TARGET", 0, 0, 0, 0,
            fade_type="in", duration_ff=1,
        )

        fade_events = [
            event for event in timeline_events(track)
            if event_payload(event)[15] == 0x01
        ]
        geometries = [
            item for item in geometry_list.items
            if isinstance(item, PTBlock) and item.content_type == 0x262F
        ]
        self.assertEqual(
            [struct.unpack_from("<Q", event_payload(item), 7)[0] for item in fade_events],
            [0, 48_000],
        )
        self.assertEqual(
            [struct.unpack_from("<I", event_payload(item), 2)[0] for item in fade_events],
            [1, 0],
        )
        self.assertEqual([len(item.items[0]) for item in geometries], [27, 22])
        self.assertEqual(struct.unpack_from("<I", geometry_list.items[0], 0)[0], 2)

    def test_late_failure_rolls_back_fade_operation(self):
        session, geometry_list, _ = make_session()
        original = geometry_list.to_bytes(False, 100)[0]

        def fail_after_mutation(*args):
            session.root_items[1].items[0] = bytearray(struct.pack("<I", 99))
            session._removed_offsets.append(123)
            raise RuntimeError("late failure")

        session._add_fade_impl = fail_after_mutation
        with self.assertRaisesRegex(RuntimeError, "late failure"):
            session.add_fade("TRACK", "TARGET", 0, 0, 0, 0, duration_ff=1)

        restored_geometry = session.root_items[1]
        self.assertEqual(restored_geometry.to_bytes(False, 100)[0], original)
        self.assertEqual(session._removed_offsets, [])


if __name__ == "__main__":
    unittest.main()
