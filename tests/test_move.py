import struct
import unittest

from pt_api import PTBlock, ProToolsSession


def block(block_type, content_type, items):
    result = PTBlock(block_type, content_type, 1)
    result.items = items
    return result


def clip_definition(name, length=48_000):
    encoded = name.encode("utf-8")
    payload = bytearray(struct.pack("<I", len(encoded)) + encoded)
    payload.extend(b"\x00\x00\x30\x44\x08")
    payload.extend(struct.pack("<I", length))
    return block(
        11,
        0x2629,
        [block(1, 0x2628, [payload])],
    )


def event(clip_id, timestamp, event_type=0x03):
    payload = bytearray(35)
    struct.pack_into("<I", payload, 2, clip_id)
    struct.pack_into("<Q", payload, 7, timestamp)
    payload[15] = event_type
    tail = b"\x00\x01\x01" if event_type == 0x03 else b"\x01\x01\x01"
    return block(3, 0x1050, [block(3, 0x104F, [payload]), bytearray(tail)])


def make_session(target_events=None):
    session = ProToolsSession.__new__(ProToolsSession)
    session.sample_rate = 48_000
    session.frame_rate_enum = 0x01
    session.is_bigendian = False
    session._removed_offsets = []

    clip_list = block(
        1,
        0x262A,
        [
            bytearray(struct.pack("<I", 2)),
            clip_definition("TARGET"),
            clip_definition("OTHER"),
        ],
    )
    if target_events is None:
        target_events = [event(0, 48_000), event(1, 96_000)]
    track_name = b"AUDIO TRACK"
    playlist = block(
        3,
        0x1052,
        [
            bytearray(
                struct.pack("<I", len(track_name))
                + track_name
                + struct.pack("<I", len(target_events))
            ),
            *target_events,
            bytearray(b"TRAILER!"),
        ],
    )
    track_map = block(2, 0x1054, [bytearray(struct.pack("<I", 1)), playlist])
    session.root_items = [clip_list, track_map]
    return session, playlist


def event_info(timeline_event):
    payload = next(
        child.items[0] for child in timeline_event.items
        if isinstance(child, PTBlock) and child.content_type == 0x104F
    )
    return struct.unpack_from("<I", payload, 2)[0], struct.unpack_from("<Q", payload, 7)[0]


class MoveClipTests(unittest.TestCase):
    def test_move_updates_only_target_and_resorts_playlist(self):
        session, playlist = make_session()

        count = session.move_clip("TARGET", 0, 0, 3, 0)

        self.assertEqual(count, 1)
        events = [item for item in playlist.items if isinstance(item, PTBlock)]
        self.assertEqual([event_info(item) for item in events], [(1, 96_000), (0, 144_000)])
        self.assertEqual(playlist.items[-1], b"TRAILER!")

    def test_multiple_placements_are_rejected_without_mutation(self):
        first = event(0, 48_000)
        second = event(0, 96_000)
        session, _ = make_session([first, second])

        with self.assertRaisesRegex(ValueError, "multiple visible placements"):
            session.move_clip("TARGET", 0, 0, 3, 0)

        self.assertEqual(event_info(first)[1], 48_000)
        self.assertEqual(event_info(second)[1], 96_000)

    def test_attached_fade_is_rejected_without_mutation(self):
        audio = event(0, 48_000)
        fade = event(0, 48_000, event_type=0x01)
        session, _ = make_session([fade, audio])
        geometry = bytearray(22)
        struct.pack_into("<H", geometry, 8, 1_000)
        session.root_items.append(
            block(
                1,
                0x2630,
                [bytearray(struct.pack("<I", 1)), block(2, 0x262F, [geometry])],
            )
        )

        with self.assertRaisesRegex(NotImplementedError, "attached fades"):
            session.move_clip("TARGET", 0, 0, 3, 0)

        self.assertEqual(event_info(audio)[1], 48_000)
        self.assertEqual(event_info(fade)[1], 48_000)

    def test_invalid_timecode_components_are_rejected(self):
        session, _ = make_session()

        with self.assertRaisesRegex(ValueError, "Frame component"):
            session.move_clip("TARGET", 0, 0, 0, 24)
        with self.assertRaisesRegex(ValueError, "Invalid target timecode"):
            session.move_clip("TARGET", 0, 60, 0, 0)

    def test_group_macro_with_colliding_id_is_not_moved(self):
        macro = event(0, 48_000)
        macro.items[-1] = bytearray(b"\x00\x00\x01")
        session, _ = make_session([macro])

        with self.assertRaisesRegex(ValueError, "no visible audio placement"):
            session.move_clip("TARGET", 0, 0, 3, 0)

        self.assertEqual(event_info(macro)[1], 48_000)


if __name__ == "__main__":
    unittest.main()
