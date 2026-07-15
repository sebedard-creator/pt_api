import math
import struct
import unittest

from pt_api import PTBlock, ProToolsSession


def make_block(block_type, content_type, items):
    block = PTBlock(block_type, content_type, 1)
    block.items = items
    return block


def initial_volume_payload():
    return bytearray.fromhex(
        "014601001400000000000100000002000000000000000000000000000000"
    )


def make_track_definition(internal_name="Audio 1", payload=None):
    internal_name = internal_name.encode("utf-8")
    name_payload = bytearray(
        struct.pack("<I", len(internal_name)) + internal_name
    )
    b2619 = make_block(4, 0x2619, [name_payload])
    volume = make_block(
        1,
        0x260A,
        [initial_volume_payload() if payload is None else bytearray(payload)],
    )
    b260d = make_block(3, 0x260D, [volume])
    definition = make_block(1, 0x261C, [b2619, b260d])
    return definition, volume, b260d


def make_timeline_track(name):
    visible_name = name.encode("utf-8")
    track_header = bytearray(
        struct.pack("<I", len(visible_name)) + visible_name + struct.pack("<I", 0)
    )
    return make_block(3, 0x1052, [track_header])


def make_session(payload=None):
    session = ProToolsSession.__new__(ProToolsSession)
    session.sample_rate = 48_000
    session.frame_rate_enum = 0x09
    session.is_bigendian = False
    session._removed_offsets = []

    timeline_track = make_timeline_track("VISIBLE TRACK")
    timeline = make_block(
        2,
        0x1054,
        [bytearray(struct.pack("<I", 1)), timeline_track],
    )
    definition, volume, _ = make_track_definition(payload=payload)

    session.root_items = [timeline, definition]
    return session, volume


def nodes(volume):
    payload = volume.items[0]
    count = struct.unpack_from("<I", payload, 10)[0]
    result = []
    for index in range(count):
        offset = 22 + (index * 6)
        result.append(
            (
                struct.unpack_from("<I", payload, offset)[0],
                struct.unpack_from("<h", payload, offset + 4)[0],
            )
        )
    return result


class VolumeAutomationTests(unittest.TestCase):
    def test_visible_timeline_name_maps_to_stale_internal_name(self):
        session, volume = make_session()

        count = session.add_volume_node("VISIBLE TRACK", 0, 0, 1, 0, 6.0)

        self.assertEqual(count, 2)
        self.assertEqual(nodes(volume), [(0, 0), (48_048, 60)])
        payload = volume.items[0]
        self.assertEqual(struct.unpack_from("<I", payload, 4)[0], 26)
        self.assertEqual(struct.unpack_from("<I", payload, 16)[0], 1)
        self.assertEqual(payload[-2:], b"\x00\x00")

    def test_existing_timestamp_is_updated_without_duplicate(self):
        session, volume = make_session()
        session.add_volume_node("VISIBLE TRACK", 0, 0, 1, 0, 6.0)

        count = session.add_volume_node("VISIBLE TRACK", 0, 0, 1, 0, -3.0)

        self.assertEqual(count, 2)
        self.assertEqual(nodes(volume), [(0, 0), (48_048, -30)])

    def test_inconsistent_node_count_is_rejected_without_mutation(self):
        payload = initial_volume_payload()
        struct.pack_into("<I", payload, 10, 2)
        session, volume = make_session(payload)
        original = bytes(volume.items[0])

        with self.assertRaisesRegex(ValueError, "node count"):
            session.add_volume_node("VISIBLE TRACK", 0, 0, 1, 0, 6.0)

        self.assertEqual(volume.items[0], original)

    def test_non_finite_value_is_rejected(self):
        session, _ = make_session()

        with self.assertRaisesRegex(ValueError, "finite"):
            session.add_volume_node("VISIBLE TRACK", 0, 0, 1, 0, math.nan)

    def test_visible_name_takes_priority_over_another_stale_internal_name(self):
        session, first_volume = make_session()
        timeline = session.root_items[0]
        timeline.items.append(make_timeline_track("SECOND TRACK"))
        struct.pack_into("<I", timeline.items[0], 0, 2)
        second_definition, second_volume, _ = make_track_definition("VISIBLE TRACK")
        session.root_items.append(second_definition)

        session.add_volume_node("VISIBLE TRACK", 0, 0, 1, 0, 6.0)

        self.assertEqual(nodes(first_volume), [(0, 0), (48_048, 60)])
        self.assertEqual(nodes(second_volume), [(0, 0)])

    def test_nested_260a_cannot_shadow_the_first_direct_volume_playlist(self):
        session, volume = make_session()
        b260d = session.root_items[1].get_all_blocks(0x260D)[0]
        decoy = make_block(1, 0x260A, [initial_volume_payload()])
        b260d.items.insert(0, make_block(2, 0x260C, [decoy]))

        session.add_volume_node("VISIBLE TRACK", 0, 0, 1, 0, 6.0)

        self.assertEqual(nodes(volume), [(0, 0), (48_048, 60)])
        self.assertEqual(nodes(decoy), [(0, 0)])

    def test_ambiguous_260d_is_rejected_without_mutation(self):
        session, volume = make_session()
        original = bytes(volume.items[0])
        duplicate_volume = make_block(1, 0x260A, [initial_volume_payload()])
        session.root_items[1].items.append(make_block(3, 0x260D, [duplicate_volume]))

        with self.assertRaisesRegex(ValueError, "unambiguous 0x260d"):
            session.add_volume_node("VISIBLE TRACK", 0, 0, 1, 0, 6.0)

        self.assertEqual(bytes(volume.items[0]), original)

    def test_invalid_fixed_payload_fields_are_rejected_without_mutation(self):
        for label, mutate, message in (
            ("identifier", lambda payload: payload.__setitem__(0, 0xFF), "identifier"),
            (
                "declared size",
                lambda payload: struct.pack_into("<I", payload, 4, 999),
                "payload size",
            ),
            ("terminator", lambda payload: payload.__setitem__(-1, 0xFF), "terminator"),
        ):
            with self.subTest(label=label):
                session, volume = make_session()
                mutate(volume.items[0])
                original = bytes(volume.items[0])

                with self.assertRaisesRegex(ValueError, message):
                    session.add_volume_node("VISIBLE TRACK", 0, 0, 1, 0, 6.0)

                self.assertEqual(bytes(volume.items[0]), original)

    def test_boolean_value_is_rejected(self):
        session, volume = make_session()
        original = bytes(volume.items[0])

        with self.assertRaises(TypeError):
            session.add_volume_node("VISIBLE TRACK", 0, 0, 1, 0, True)

        self.assertEqual(bytes(volume.items[0]), original)

    def test_unrepresentable_numeric_values_are_rejected_without_mutation(self):
        session, volume = make_session()
        original = bytes(volume.items[0])

        with self.assertRaisesRegex(ValueError, "signed Int16"):
            session.add_volume_node("VISIBLE TRACK", 0, 0, 1, 0, 1e308)
        self.assertEqual(bytes(volume.items[0]), original)

        with self.assertRaises(TypeError):
            session.add_volume_node("VISIBLE TRACK", 0, 0, 1, 0, 10**1000)
        self.assertEqual(bytes(volume.items[0]), original)


if __name__ == "__main__":
    unittest.main()
