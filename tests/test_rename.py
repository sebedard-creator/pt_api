import struct
import unittest

from pt_api import PTBlock, ProToolsSession


def make_session(name="AUDIO TRACK_01"):
    session = ProToolsSession.__new__(ProToolsSession)
    session.is_bigendian = False

    name_bytes = name.encode("utf-8")
    tail = bytearray(b"\x00\x00\x30\x44\x08")
    tail.extend(struct.pack("<I", 48_000))
    tail.extend(bytes(range(32)))
    payload = bytearray(struct.pack("<I", len(name_bytes)) + name_bytes + tail)

    b2628 = PTBlock(4, 0x2628, len(payload) + 2)
    b2628.items = [payload]
    definition = PTBlock(11, 0x2629, 1)
    definition.items = [b2628, bytearray(48)]
    clip_list = PTBlock(1, 0x262A, 1)
    clip_list.items = [bytearray(struct.pack("<I", 1)), definition]
    session.root_items = [clip_list]
    return session, b2628, bytes(tail)


def append_clip(session, name):
    name_bytes = name.encode("utf-8")
    tail = bytearray(b"\x00\x00\x30\x44\x08")
    tail.extend(struct.pack("<I", 48_000))
    tail.extend(bytes(range(32)))
    payload = bytearray(struct.pack("<I", len(name_bytes)) + name_bytes + tail)

    b2628 = PTBlock(4, 0x2628, len(payload) + 2)
    b2628.items = [payload]
    definition = PTBlock(11, 0x2629, 1)
    definition.items = [b2628, bytearray(48)]

    clip_list = session.root_items[0]
    clip_list.items.append(definition)
    struct.pack_into("<I", clip_list.items[0], 0, len(clip_list.items) - 1)
    return b2628


class RenameTests(unittest.TestCase):
    def test_odd_length_name_has_no_nul_or_alignment_padding(self):
        session, b2628, original_tail = make_session()
        old_payload_length = len(b2628.items[0])

        renamed = session.rename_clip("AUDIO TRACK_01", "AUDIO TRACK_01X")

        payload = b2628.items[0]
        name_len = struct.unpack_from("<I", payload, 0)[0]
        self.assertEqual(renamed, 1)
        self.assertEqual(name_len, 15)
        self.assertEqual(payload[4:4 + name_len], b"AUDIO TRACK_01X")
        self.assertFalse(payload[4:4 + name_len].endswith(b"\x00"))
        self.assertEqual(bytes(payload[4 + name_len:]), original_tail)
        self.assertEqual(len(payload), old_payload_length + 1)

    def test_length_is_utf8_byte_length_not_character_count(self):
        session, b2628, _ = make_session()

        session.rename_clip("AUDIO TRACK_01", "é")

        payload = b2628.items[0]
        self.assertEqual(struct.unpack_from("<I", payload, 0)[0], 2)
        self.assertEqual(payload[4:6], "é".encode("utf-8"))

    def test_empty_or_nul_name_is_rejected(self):
        session, _, _ = make_session()

        with self.assertRaisesRegex(ValueError, "non-empty"):
            session.rename_clip("AUDIO TRACK_01", "")
        with self.assertRaisesRegex(ValueError, "NUL"):
            session.rename_clip("AUDIO TRACK_01", "BAD\x00NAME")

    def test_missing_or_ambiguous_source_is_rejected_without_mutation(self):
        session, first, _ = make_session("DUPLICATE")
        second = append_clip(session, "DUPLICATE")
        before = (bytes(first.items[0]), bytes(second.items[0]))

        with self.assertRaisesRegex(ValueError, "ambiguous"):
            session.rename_clip("DUPLICATE", "RENAMED")

        self.assertEqual((bytes(first.items[0]), bytes(second.items[0])), before)

        unique_session, unique, _ = make_session("EXISTING")
        unique_before = bytes(unique.items[0])
        with self.assertRaisesRegex(ValueError, "not found"):
            unique_session.rename_clip("MISSING", "RENAMED")
        self.assertEqual(bytes(unique.items[0]), unique_before)

    def test_existing_destination_name_is_rejected_without_mutation(self):
        session, source, _ = make_session("SOURCE")
        destination = append_clip(session, "DESTINATION")
        before = (bytes(source.items[0]), bytes(destination.items[0]))

        with self.assertRaisesRegex(ValueError, "already exists"):
            session.rename_clip("SOURCE", "DESTINATION")

        self.assertEqual((bytes(source.items[0]), bytes(destination.items[0])), before)

    def test_invalid_types_and_unencodable_name_are_rejected(self):
        session, _, _ = make_session()

        with self.assertRaises(TypeError):
            session.rename_clip(123, "VALID")
        with self.assertRaises(TypeError):
            session.rename_clip("AUDIO TRACK_01", 123)
        with self.assertRaisesRegex(ValueError, "valid UTF-8"):
            session.rename_clip("AUDIO TRACK_01", "\ud800")


if __name__ == "__main__":
    unittest.main()
