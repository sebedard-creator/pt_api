import struct
import unittest

from pt_api import PTBlock, ProToolsSession


def make_block(block_type, content_type, items):
    block = PTBlock(block_type, content_type, 1)
    block.items = items
    return block


def make_clip(name="CLIP", length=1_000, clip_id=0):
    name_bytes = name.encode("utf-8")
    payload = bytearray(struct.pack("<I", len(name_bytes)) + name_bytes)
    payload.extend(b"\x00\x00\x30\x44\x08")
    payload.extend(struct.pack("<I", length))
    payload.extend(b"\x00" * 50)

    definition_payload = bytearray(48)
    struct.pack_into("<I", definition_payload, 0, clip_id)
    b2628 = make_block(4, 0x2628, [payload])
    return make_block(11, 0x2629, [b2628, definition_payload])


def make_event(clip_id=0, timestamp=10_000, muted=False, event_type=0x03):
    payload = bytearray(35)
    payload[0] = 1 if muted else 0
    struct.pack_into("<I", payload, 2, clip_id)
    struct.pack_into("<Q", payload, 7, timestamp)
    payload[15] = event_type
    b104f = make_block(10, 0x104F, [payload])
    tail = b"\x00\x01\x01" if event_type == 0x03 else b"\x01\x01\x01"
    return make_block(3, 0x1050, [b104f, bytearray(tail)])


def make_session(muted=False, events=None):
    session = ProToolsSession.__new__(ProToolsSession)
    session.is_bigendian = False
    session.sample_rate = 48_000
    session.frame_rate_enum = 0x01
    session._removed_offsets = []

    clip_list = make_block(
        1,
        0x262A,
        [bytearray(struct.pack("<I", 1)), make_clip()],
    )
    event = make_event(muted=muted)
    if events is None:
        events = [event]
    track_name = b"TRACK"
    header = bytearray(
        struct.pack("<I", len(track_name))
        + track_name
        + struct.pack("<I", len(events))
    )
    track = make_block(
        3, 0x1052, [header, *events, bytearray(b"\x01\x00")]
    )
    track_map = make_block(
        2, 0x1054, [bytearray(struct.pack("<I", 1)), track]
    )
    session.root_items = [clip_list, track_map]
    return session, clip_list, events[0]


def event_payload(event):
    return next(
        item
        for item in event.items
        if isinstance(item, PTBlock) and item.content_type == 0x104F
    ).items[0]


def clip_payload(definition):
    return next(
        item
        for item in definition.items
        if isinstance(item, PTBlock) and item.content_type == 0x2628
    ).items[0]


class TrimmingTests(unittest.TestCase):
    def test_trim_start_updates_count_id_offset_length_and_muted_event(self):
        session, clip_list, event = make_session(muted=True)

        session.trim_clip_start("CLIP", 100)

        definitions = [
            item
            for item in clip_list.items
            if isinstance(item, PTBlock) and item.content_type == 0x2629
        ]
        self.assertEqual(struct.unpack_from("<I", clip_list.items[0], 0)[0], 2)
        self.assertEqual(len(definitions), 2)
        self.assertEqual(struct.unpack_from("<I", definitions[1].items[1], 0)[0], 1)
        identity = definitions[1].items[1]
        self.assertEqual(identity[28] >> 4, 4)
        self.assertEqual(identity[30] >> 6, 2)

        timeline_payload = event_payload(event)
        self.assertEqual(timeline_payload[0], 1)
        self.assertEqual(struct.unpack_from("<I", timeline_payload, 2)[0], 1)
        self.assertEqual(struct.unpack_from("<Q", timeline_payload, 7)[0], 10_100)

        payload = clip_payload(definitions[1])
        name_len = struct.unpack_from("<I", payload, 0)[0]
        offset = 4 + name_len
        self.assertEqual(struct.unpack_from("<H", payload, offset)[0], 0x3001)
        self.assertEqual(int.from_bytes(payload[offset + 5:offset + 8], "little"), 100)
        self.assertEqual(int.from_bytes(payload[offset + 8:offset + 11], "little"), 900)

    def test_trim_end_updates_count_and_preserves_timeline_start(self):
        session, clip_list, event = make_session()

        session.trim_clip_end("CLIP", 100)

        definitions = [
            item
            for item in clip_list.items
            if isinstance(item, PTBlock) and item.content_type == 0x2629
        ]
        self.assertEqual(struct.unpack_from("<I", clip_list.items[0], 0)[0], 2)
        self.assertEqual(len(definitions), 2)
        self.assertEqual(struct.unpack_from("<I", definitions[1].items[1], 0)[0], 1)

        timeline_payload = event_payload(event)
        self.assertEqual(struct.unpack_from("<I", timeline_payload, 2)[0], 1)
        self.assertEqual(struct.unpack_from("<Q", timeline_payload, 7)[0], 10_000)

        payload = clip_payload(definitions[1])
        name_len = struct.unpack_from("<I", payload, 0)[0]
        offset = 4 + name_len
        self.assertEqual(struct.unpack_from("<H", payload, offset)[0], 0x0001)
        self.assertEqual(struct.unpack_from("<I", payload, offset + 5)[0], 900)

    def test_trim_start_rejects_values_outside_24_bit_encoding(self):
        session, _, _ = make_session()

        with self.assertRaisesRegex(ValueError, "24-bit"):
            session.create_subclip(0, "TOO_LONG", 1, 0x01000000)
        with self.assertRaisesRegex(ValueError, "24-bit"):
            session.create_subclip(0, "TOO_LONG", 0, 0x01000000)

    def test_create_subclip_rejects_invalid_utf8_without_mutation(self):
        session, clip_list, _ = make_session()
        original = clip_list.to_bytes(False, 100)[0]

        with self.assertRaisesRegex(ValueError, "valid UTF-8"):
            session.create_subclip(0, "Bad\ud800Name", 0, 100)

        self.assertEqual(clip_list.to_bytes(False, 100)[0], original)

    def test_parent_length_masks_the_following_timestamp_byte(self):
        session, clip_list, _ = make_session()
        parent_payload = clip_payload(clip_list.items[1])
        name_len = struct.unpack_from("<I", parent_payload, 0)[0]
        parent_payload[4 + name_len + 8] = 0xAB

        session.trim_clip_end("CLIP", 100)

        definitions = [
            item for item in clip_list.items
            if isinstance(item, PTBlock) and item.content_type == 0x2629
        ]
        payload = clip_payload(definitions[-1])
        new_name_len = struct.unpack_from("<I", payload, 0)[0]
        offset = 4 + new_name_len
        self.assertEqual(struct.unpack_from("<I", payload, offset + 5)[0], 900)

    def test_start_then_end_trim_composes_on_the_virtual_clip(self):
        session, clip_list, event = make_session()

        self.assertEqual(session.trim_clip_start("CLIP", 100), 1)
        self.assertEqual(session.trim_clip_end("CLIP-tS", 100), 2)

        definitions = [
            item for item in clip_list.items
            if isinstance(item, PTBlock) and item.content_type == 0x2629
        ]
        final_payload = clip_payload(definitions[-1])
        name_len = struct.unpack_from("<I", final_payload, 0)[0]
        self.assertEqual(final_payload[4:4 + name_len], b"CLIP-tS-tE")
        offset = 4 + name_len
        self.assertEqual(int.from_bytes(final_payload[offset + 5:offset + 8], "little"), 100)
        self.assertEqual(int.from_bytes(final_payload[offset + 8:offset + 11], "little"), 800)
        self.assertEqual(struct.unpack_from("<Q", event_payload(event), 7)[0], 10_100)

    def test_existing_generated_name_gets_a_unique_suffix(self):
        session, clip_list, _ = make_session()
        clip_list.items.append(make_clip("CLIP-tS", clip_id=1))
        struct.pack_into("<I", clip_list.items[0], 0, 2)

        self.assertEqual(session.trim_clip_start("CLIP", 100), 2)

        definitions = [
            item for item in clip_list.items
            if isinstance(item, PTBlock) and item.content_type == 0x2629
        ]
        payload = clip_payload(definitions[-1])
        name_len = struct.unpack_from("<I", payload, 0)[0]
        self.assertEqual(payload[4:4 + name_len], b"CLIP-tS-02")

    def test_multiple_placements_are_rejected_without_mutation(self):
        first = make_event(timestamp=10_000)
        second = make_event(timestamp=20_000)
        session, _, _ = make_session(events=[first, second])

        with self.assertRaisesRegex(ValueError, "trim target is ambiguous"):
            session.trim_clip_start("CLIP", 100)

        clips = session.root_items[0]
        self.assertEqual(struct.unpack_from("<I", clips.items[0], 0)[0], 1)

    def test_attached_fade_is_rejected_without_mutation(self):
        fade = make_event(timestamp=10_000, event_type=0x01)
        audio = make_event(timestamp=10_000)
        session, _, _ = make_session(events=[fade, audio])
        session.root_items.append(
            make_block(
                1,
                0x2630,
                [
                    bytearray(struct.pack("<I", 1)),
                    make_block(2, 0x262F, [bytearray(22)]),
                ],
            )
        )

        with self.assertRaisesRegex(NotImplementedError, "attached fades"):
            session.trim_clip_end("CLIP", 100)

        clips = session.root_items[0]
        self.assertEqual(struct.unpack_from("<I", clips.items[0], 0)[0], 1)

    def test_invalid_amount_and_late_failure_leave_session_unchanged(self):
        session, _, _ = make_session()
        with self.assertRaisesRegex(TypeError, "must be an integer"):
            session.trim_clip_start("CLIP", 1.5)
        with self.assertRaisesRegex(ValueError, "greater than zero"):
            session.trim_clip_end("CLIP", 0)

        def fail_after_mutation(*args):
            session.root_items[0].items[0][0] = 99
            session._removed_offsets.append(123)
            raise RuntimeError("late failure")

        session.create_subclip = fail_after_mutation
        with self.assertRaisesRegex(RuntimeError, "late failure"):
            session.trim_clip_start("CLIP", 100)

        self.assertEqual(struct.unpack_from("<I", session.root_items[0].items[0], 0)[0], 1)
        self.assertEqual(session._removed_offsets, [])


if __name__ == "__main__":
    unittest.main()
