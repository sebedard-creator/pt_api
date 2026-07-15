import copy
import struct
import unittest

from pt_api import PTBlock, ProToolsSession


def block(block_type, content_type, items, original_offset=-1):
    result = PTBlock(block_type, content_type, 1)
    result.items = items
    result.original_offset = original_offset
    return result


def clip_definition(name, clip_id, length=480_000):
    encoded = name.encode("utf-8")
    clip_payload = bytearray(
        struct.pack("<I", len(encoded))
        + encoded
        + b"\x00\x00\x30\x44\x00"
        + struct.pack("<I", length)
        + struct.pack("<I", 0)
        + struct.pack("<I", 0)
        + b"TAIL"
    )
    identity = bytearray(48)
    struct.pack_into("<I", identity, 0, clip_id)
    return block(
        11,
        0x2629,
        [block(1, 0x2628, [clip_payload]), identity, bytearray(b"REST")],
    )


def event(clip_id, timestamp=0, event_type=0x03, original_offset=-1):
    payload = bytearray(35)
    struct.pack_into("<I", payload, 2, clip_id)
    struct.pack_into("<Q", payload, 7, timestamp)
    payload[15] = event_type
    tail = b"\x00\x01\x01" if event_type == 0x03 else b"\x01\x01\x01"
    return block(
        3,
        0x1050,
        [block(3, 0x104F, [payload]), bytearray(tail)],
        original_offset=original_offset,
    )


def make_session(existing_suffixes=(), length=480_000, timeline_events=None):
    session = ProToolsSession.__new__(ProToolsSession)
    session.sample_rate = 48_000
    session.frame_rate_enum = 0x01
    session.is_bigendian = False
    session._removed_offsets = []

    definitions = [clip_definition("CLIP", 0, length)]
    for suffix in existing_suffixes:
        definitions.append(clip_definition(f"CLIP-{suffix:02d}", len(definitions), 100))
    clip_list = block(
        1,
        0x262A,
        [bytearray(struct.pack("<I", len(definitions))), *definitions],
    )

    if timeline_events is None:
        timeline_events = [event(0, original_offset=500)]
    track_name = b"AUDIO TRACK"
    playlist = block(
        3,
        0x1052,
        [
            bytearray(
                struct.pack("<I", len(track_name))
                + track_name
                + struct.pack("<I", len(timeline_events))
            ),
            *timeline_events,
            bytearray(b"TRAILER!"),
        ],
    )
    track_map = block(2, 0x1054, [bytearray(struct.pack("<I", 1)), playlist])
    session.root_items = [clip_list, track_map]
    return session, clip_list, playlist


def definition_name(definition):
    payload = next(
        child.items[0] for child in definition.items
        if isinstance(child, PTBlock) and child.content_type == 0x2628
    )
    length = struct.unpack_from("<I", payload, 0)[0]
    return payload[4:4 + length].decode("utf-8")


def timeline_info(playlist):
    result = []
    for item in playlist.items:
        if not isinstance(item, PTBlock) or item.content_type != 0x1050:
            continue
        payload = next(
            child.items[0] for child in item.items
            if isinstance(child, PTBlock) and child.content_type == 0x104F
        )
        result.append(
            (
                struct.unpack_from("<I", payload, 2)[0],
                struct.unpack_from("<Q", payload, 7)[0],
                item.original_offset,
            )
        )
    return result


class SplitClipTests(unittest.TestCase):
    def test_split_creates_two_contiguous_subclips_and_preserves_original_event(self):
        session, clip_list, playlist = make_session()

        result = session.split_clip("AUDIO TRACK", "CLIP", 0, 0, 5, 0)

        self.assertEqual(result, (0, 1, 2, 240_000))
        definitions = [
            item for item in clip_list.items
            if isinstance(item, PTBlock) and item.content_type == 0x2629
        ]
        self.assertEqual(struct.unpack("<I", clip_list.items[0])[0], 3)
        self.assertEqual([definition_name(item) for item in definitions], [
            "CLIP", "CLIP-01", "CLIP-02"
        ])
        self.assertEqual(timeline_info(playlist), [(1, 0, 500), (2, 240_000, -1)])
        header = playlist.items[0]
        count_offset = 4 + struct.unpack_from("<I", header, 0)[0]
        self.assertEqual(struct.unpack_from("<I", header, count_offset)[0], 2)
        self.assertEqual(playlist.items[-1], b"TRAILER!")

    def test_existing_suffixes_advance_to_next_native_pair(self):
        session, clip_list, _ = make_session(existing_suffixes=(1, 2))

        session.split_clip("AUDIO TRACK", "CLIP", 0, 0, 5, 0)

        definitions = [
            item for item in clip_list.items
            if isinstance(item, PTBlock) and item.content_type == 0x2629
        ]
        self.assertEqual(
            [definition_name(item) for item in definitions[-2:]],
            ["CLIP-03", "CLIP-04"],
        )

    def test_24_bit_overflow_is_rejected_without_mutation(self):
        session, _, _ = make_session(length=0xFFFFFF + 48_001)
        before = copy.deepcopy(session.root_items)

        with self.assertRaisesRegex(ValueError, "fit in 24 bits"):
            session.split_clip("AUDIO TRACK", "CLIP", 0, 0, 1, 0)

        self.assertEqual(
            [item.to_bytes(False, 20)[0] for item in session.root_items],
            [item.to_bytes(False, 20)[0] for item in before],
        )

    def test_attached_fade_is_rejected_without_mutation(self):
        audio = event(0, original_offset=500)
        fade = event(0, timestamp=0, event_type=0x01, original_offset=600)
        session, _, _ = make_session(timeline_events=[fade, audio])
        geometry = bytearray(22)
        struct.pack_into("<H", geometry, 8, 24_000)
        session.root_items.append(
            block(
                1,
                0x2630,
                [bytearray(struct.pack("<I", 1)), block(2, 0x262F, [geometry])],
            )
        )
        before = copy.deepcopy(session.root_items)

        with self.assertRaisesRegex(NotImplementedError, "attached fades"):
            session.split_clip("AUDIO TRACK", "CLIP", 0, 0, 5, 0)

        self.assertEqual(
            [item.to_bytes(False, 20)[0] for item in session.root_items],
            [item.to_bytes(False, 20)[0] for item in before],
        )

    def test_late_failure_rolls_back_session_tree(self):
        session, _, _ = make_session()
        before = copy.deepcopy(session.root_items)

        def fail_after_mutation(*args):
            session.root_items[0].items[0] = bytearray(struct.pack("<I", 99))
            session._removed_offsets.append(123)
            raise RuntimeError("late failure")

        session._split_clip_impl = fail_after_mutation
        with self.assertRaisesRegex(RuntimeError, "late failure"):
            session.split_clip("AUDIO TRACK", "CLIP", 0, 0, 5, 0)

        self.assertEqual(
            [item.to_bytes(False, 20)[0] for item in session.root_items],
            [item.to_bytes(False, 20)[0] for item in before],
        )
        self.assertEqual(session._removed_offsets, [])

    def test_group_macro_with_colliding_id_is_not_split(self):
        macro = event(0, original_offset=500)
        macro.items[-1] = bytearray(b"\x00\x00\x01")
        session, _, _ = make_session(timeline_events=[macro])
        before = copy.deepcopy(session.root_items)

        with self.assertRaisesRegex(ValueError, "Could not find an instance"):
            session.split_clip("AUDIO TRACK", "CLIP", 0, 0, 5, 0)

        self.assertEqual(
            [item.to_bytes(False, 20)[0] for item in session.root_items],
            [item.to_bytes(False, 20)[0] for item in before],
        )


if __name__ == "__main__":
    unittest.main()
