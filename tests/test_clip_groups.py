import struct
import unittest

from pt_api import PTBlock, ProToolsSession


def make_block(block_type, content_type, items, offset=0):
    block = PTBlock(block_type, content_type, 1)
    block.items = items
    block.original_offset = offset
    return block


def make_event(clip_id, timestamp, tail=b"\x00\x01\x01", offset=0):
    payload = bytearray(35)
    struct.pack_into("<I", payload, 2, clip_id)
    struct.pack_into("<Q", payload, 7, timestamp)
    payload[15] = 0x03
    b104f = make_block(10, 0x104F, [payload], offset + 9 if offset else 0)
    return make_block(3, 0x1050, [b104f, bytearray(tail)], offset)


def make_track(name, events, offset=0):
    encoded_name = name.encode("utf-8")
    header = bytearray(
        struct.pack("<I", len(encoded_name))
        + encoded_name
        + struct.pack("<I", len(events))
    )
    return make_block(3, 0x1052, [header, *events, bytearray(b"\x01\x00")], offset)


def make_session(nested=False):
    session = ProToolsSession.__new__(ProToolsSession)
    session.is_bigendian = False
    session._removed_offsets = []

    group_name = "GROUP_TEST"
    encoded_name = group_name.encode("utf-8")

    group_name_payload = bytearray(struct.pack("<I", len(encoded_name)) + encoded_name)
    group_name_payload.extend(b"\x00" * 12)
    group_definition = make_block(
        11,
        0x262B,
        [make_block(4, 0x2628, [group_name_payload], 111), bytearray(9)],
        110,
    )
    b262c = make_block(
        1,
        0x262C,
        [bytearray(struct.pack("<I", 1)), group_definition, bytearray(4)],
        100,
    )

    macro = make_event(0, 1_000, b"\x00\x00\x01", 210)
    control = make_event(2, 5_000, b"\x00\x01\x01", 220)
    main_track = make_track("TRACK", [macro, control], 200)
    main_1054 = make_block(
        2,
        0x1054,
        [bytearray(struct.pack("<I", 1)), main_track, make_block(1, 0x4828, [])],
        190,
    )

    first = make_event(0, 1_000_000, b"\x00\x01\x01", 330)
    second_tail = b"\x00\x00\x01" if nested else b"\x00\x01\x01"
    second = make_event(1, 1_002_000, second_tail, 340)
    hidden_track = make_track("?", [first, second], 320)
    hidden_1054 = make_block(
        2,
        0x1054,
        [bytearray(struct.pack("<I", 1)), hidden_track, make_block(1, 0x4828, [])],
        310,
    )
    b2428 = make_block(1, 0x2428, [hidden_1054], 300)

    name_index_payload = bytearray(4)
    name_index_payload.extend(struct.pack("<I", len(encoded_name)))
    name_index_payload.extend(encoded_name)
    group_name_index = make_block(1, 0x2423, [name_index_payload], 410)
    b2424 = make_block(
        1, 0x2424, [bytearray(struct.pack("<I", 1)), group_name_index], 400
    )

    group_metadata = make_block(1, 0x2425, [bytearray(b"metadata")], 510)
    b2426 = make_block(
        1, 0x2426, [bytearray(struct.pack("<I", 1)), group_metadata], 500
    )

    session.root_items = [b262c, main_1054, b2428, b2424, b2426]
    return {
        'session': session,
        'b262c': b262c,
        'main_track': main_track,
        'macro': macro,
        'control': control,
        'hidden_1054': hidden_1054,
        'hidden_track': hidden_track,
        'first': first,
        'second': second,
        'b2424': b2424,
        'b2426': b2426,
    }


def child_blocks(container, content_type):
    return [
        item for item in container.items
        if isinstance(item, PTBlock) and item.content_type == content_type
    ]


def event_details(track):
    details = []
    for event in child_blocks(track, 0x1050):
        b104f = child_blocks(event, 0x104F)[0]
        payload = b104f.items[0]
        tail = b"".join(
            bytes(item) for item in event.items
            if isinstance(item, (bytes, bytearray))
        )
        details.append(
            (
                struct.unpack_from("<I", payload, 2)[0],
                struct.unpack_from("<Q", payload, 7)[0],
                tail,
            )
        )
    return details


def serialize_roots(session):
    chunks = []
    offset = 0
    for item in session.root_items:
        if isinstance(item, PTBlock):
            payload, _ = item.to_bytes(session.is_bigendian, offset)
            chunks.append(payload)
            offset += len(payload)
        else:
            chunks.append(bytes(item))
            offset += len(item)
    return b"".join(chunks)


class ClipGroupTests(unittest.TestCase):
    def test_dissolve_restores_components_and_preserves_ordinary_clip(self):
        fixture = make_session()
        session = fixture['session']

        result = session.delete_clip_group("GROUP_TEST")

        self.assertEqual(result, 1)
        self.assertEqual(struct.unpack_from("<I", fixture['b262c'].items[0], 0)[0], 0)
        self.assertEqual(child_blocks(fixture['b262c'], 0x262B), [])

        self.assertEqual(
            event_details(fixture['main_track']),
            [
                (0, 1_000, b"\x00\x01\x01"),
                (1, 3_000, b"\x00\x01\x01"),
                (2, 5_000, b"\x00\x01\x01"),
            ],
        )
        self.assertIn(fixture['first'], fixture['main_track'].items)
        self.assertIn(fixture['second'], fixture['main_track'].items)
        self.assertIn(fixture['control'], fixture['main_track'].items)
        self.assertNotIn(fixture['macro'], fixture['main_track'].items)

        header = fixture['main_track'].items[0]
        name_length = struct.unpack_from("<I", header, 0)[0]
        self.assertEqual(struct.unpack_from("<I", header, 4 + name_length)[0], 3)
        self.assertEqual(struct.unpack_from("<I", fixture['hidden_1054'].items[0], 0)[0], 0)
        self.assertEqual(child_blocks(fixture['hidden_1054'], 0x1052), [])
        self.assertEqual(child_blocks(fixture['b2424'], 0x2423), [])
        self.assertEqual(child_blocks(fixture['b2426'], 0x2425), [])

        self.assertIn(fixture['hidden_track'].original_offset, session._removed_offsets)
        self.assertIn(fixture['macro'].original_offset, session._removed_offsets)
        self.assertNotIn(fixture['first'].original_offset, session._removed_offsets)
        self.assertNotIn(fixture['second'].original_offset, session._removed_offsets)

    def test_nested_group_is_rejected_before_mutation(self):
        fixture = make_session(nested=True)
        session = fixture['session']

        with self.assertRaisesRegex(NotImplementedError, "Nested groups"):
            session.delete_clip_group("GROUP_TEST")

        self.assertEqual(event_details(fixture['main_track'])[0][2], b"\x00\x00\x01")
        self.assertEqual(len(child_blocks(fixture['hidden_track'], 0x1050)), 2)
        self.assertEqual(struct.unpack_from("<I", fixture['b262c'].items[0], 0)[0], 1)
        self.assertEqual(session._removed_offsets, [])

    def test_late_failure_restores_the_complete_session(self):
        fixture = make_session()
        session = fixture['session']
        session._removed_offsets = [999]
        before = serialize_roots(session)

        def fail_during_removal(node, removed_offsets):
            raise RuntimeError("simulated late failure")

        session._collect_offsets_recursive = fail_during_removal
        with self.assertRaisesRegex(RuntimeError, "simulated late failure"):
            session.delete_clip_group("GROUP_TEST")

        self.assertEqual(serialize_roots(session), before)
        self.assertEqual(session._removed_offsets, [999])


if __name__ == "__main__":
    unittest.main()
