import math
import struct
import unittest

from pt_api import PTBlock, ProToolsSession


def make_clip(name, clip_id):
    name_bytes = name.encode("utf-8")
    payload = bytearray(struct.pack("<I", len(name_bytes)) + name_bytes)
    payload.extend(b"\x00\x00\x30\x44\x08")
    payload.extend(struct.pack("<I", 48_000))
    payload.extend(b"\x00" * 24)
    payload.extend(struct.pack("<i", -1))
    payload.extend(b"\x00\x00")

    b2628 = PTBlock(4, 0x2628, 1)
    b2628.items = [payload]
    definition_payload = bytearray(48)
    struct.pack_into("<I", definition_payload, 0, clip_id)
    definition = PTBlock(11, 0x2629, 1)
    definition.items = [b2628, definition_payload]
    return definition, b2628


def make_session(include_dictionary=True):
    session = ProToolsSession.__new__(ProToolsSession)
    session.is_bigendian = False
    clip_a, payload_a = make_clip("A", 0)
    clip_b, payload_b = make_clip("B", 1)

    clip_list = PTBlock(1, 0x262A, 1)
    clip_list.items = [bytearray(struct.pack("<I", 2)), clip_a, clip_b]
    session.root_items = [clip_list]

    dictionary = None
    if include_dictionary:
        dictionary = PTBlock(1, 0x2637, 1)
        dictionary.items = [bytearray(struct.pack("<I", 0))]
        session.root_items.append(dictionary)

    return session, dictionary, payload_a, payload_b


def gain_index(b2628):
    payload = b2628.items[0]
    return struct.unpack_from("<i", payload, len(payload) - 6)[0]


def gain_values(dictionary):
    payload = dictionary.items[0]
    count = struct.unpack_from("<I", payload, 0)[0]
    return [
        struct.unpack_from("<f", payload, 4 + (index * 30) + 26)[0]
        for index in range(count)
    ]


class ClipGainTests(unittest.TestCase):
    def test_first_gain_creates_one_point_and_links_clip(self):
        session, dictionary, clip_a, _ = make_session()

        index = session.set_clip_gain("A", 6.0)

        self.assertEqual(index, 0)
        self.assertEqual(gain_index(clip_a), 0)
        self.assertEqual(gain_values(dictionary), [6.0])

    def test_second_gain_updates_existing_point_without_orphan(self):
        session, dictionary, clip_a, _ = make_session()
        session.set_clip_gain("A", 6.0)
        original_metadata = bytes(dictionary.items[0][4:30])

        index = session.set_clip_gain("A", -10.0)

        self.assertEqual(index, 0)
        self.assertEqual(gain_index(clip_a), 0)
        self.assertEqual(struct.unpack_from("<I", dictionary.items[0], 0)[0], 1)
        self.assertEqual(bytes(dictionary.items[0][4:30]), original_metadata)
        self.assertEqual(gain_values(dictionary), [-10.0])

    def test_two_clips_keep_independent_points_when_one_is_updated(self):
        session, dictionary, clip_a, clip_b = make_session()
        session.set_clip_gain("A", 6.0)
        session.set_clip_gain("B", -3.0)
        session.set_clip_gain("A", -10.0)

        self.assertEqual(gain_index(clip_a), 0)
        self.assertEqual(gain_index(clip_b), 1)
        self.assertEqual(gain_values(dictionary), [-10.0, -3.0])

    def test_shared_point_is_cloned_before_one_clip_is_updated(self):
        session, dictionary, clip_a, clip_b = make_session()
        session.set_clip_gain("A", 6.0)
        struct.pack_into("<i", clip_b.items[0], len(clip_b.items[0]) - 6, 0)

        index = session.set_clip_gain("A", -10.0)

        self.assertEqual(index, 1)
        self.assertEqual(gain_index(clip_a), 1)
        self.assertEqual(gain_index(clip_b), 0)
        self.assertEqual(gain_values(dictionary), [6.0, -10.0])

    def test_missing_dictionary_and_non_finite_value_are_rejected(self):
        session, _, _, _ = make_session(include_dictionary=False)
        with self.assertRaisesRegex(ValueError, "No Clip Gain dictionary"):
            session.set_clip_gain("A", 6.0)

        session, dictionary, _, _ = make_session()
        with self.assertRaisesRegex(ValueError, "finite"):
            session.set_clip_gain("A", float("nan"))
        self.assertEqual(gain_values(dictionary), [])

    def test_ambiguous_clip_name_is_rejected_without_mutation(self):
        session, dictionary, clip_a, clip_b = make_session()
        clip_b.items[0][4:5] = b"A"
        before = (bytes(dictionary.items[0]), bytes(clip_a.items[0]), bytes(clip_b.items[0]))

        with self.assertRaisesRegex(ValueError, "ambiguous"):
            session.set_clip_gain("A", 6.0)

        self.assertEqual(
            (bytes(dictionary.items[0]), bytes(clip_a.items[0]), bytes(clip_b.items[0])),
            before,
        )

    def test_multiple_dictionaries_and_invalid_other_index_are_rejected(self):
        session, dictionary, clip_a, clip_b = make_session()
        duplicate = PTBlock(1, 0x2637, 1)
        duplicate.items = [bytearray(struct.pack("<I", 0))]
        session.root_items.append(duplicate)

        with self.assertRaisesRegex(ValueError, "multiple root 0x2637"):
            session.set_clip_gain("A", 6.0)
        self.assertEqual(gain_index(clip_a), -1)
        self.assertEqual(gain_values(dictionary), [])

        session.root_items.remove(duplicate)
        struct.pack_into("<i", clip_b.items[0], len(clip_b.items[0]) - 6, 42)
        before = bytes(dictionary.items[0])
        with self.assertRaisesRegex(ValueError, "invalid Clip Gain index"):
            session.set_clip_gain("A", 6.0)
        self.assertEqual(bytes(dictionary.items[0]), before)
        self.assertEqual(gain_index(clip_a), -1)

    def test_boolean_gain_is_rejected(self):
        session, dictionary, _, _ = make_session()

        with self.assertRaises(TypeError):
            session.set_clip_gain("A", True)
        self.assertEqual(gain_values(dictionary), [])

    def test_numeric_negative_infinity_uses_the_pro_tools_sentinel(self):
        session, dictionary, _, _ = make_session()

        session.set_clip_gain("A", -math.inf)

        self.assertAlmostEqual(gain_values(dictionary)[0], -290.21057, places=4)

    def test_values_outside_float32_are_rejected_without_mutation(self):
        session, dictionary, clip_a, _ = make_session()
        original_clip = bytes(clip_a.items[0])

        with self.assertRaisesRegex(ValueError, "Float32"):
            session.set_clip_gain("A", 1e100)

        self.assertEqual(gain_values(dictionary), [])
        self.assertEqual(bytes(clip_a.items[0]), original_clip)

        with self.assertRaises(TypeError):
            session.set_clip_gain("A", 10**1000)
        self.assertEqual(gain_values(dictionary), [])


if __name__ == "__main__":
    unittest.main()
