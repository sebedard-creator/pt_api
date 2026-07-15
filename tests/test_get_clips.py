import struct
import unittest

from pt_api import PTBlock, ProToolsSession


def block(block_type, content_type, items):
    result = PTBlock(block_type, content_type, 1)
    result.items = items
    return result


def audio_definition(name, flags, length, source_offset=0, timestamp_low=0):
    encoded = name.encode("utf-8")
    payload = bytearray(struct.pack("<I", len(encoded)) + encoded)
    if flags in (0x0000, 0x0001):
        payload.extend(struct.pack("<H", flags) + b"\x30\x44\x00")
        payload.extend(length.to_bytes(3, "little") + bytes([timestamp_low]))
    elif flags in (0x2001, 0x3001):
        payload.extend(struct.pack("<H", flags) + b"\x30\x44\x08")
        payload.extend(source_offset.to_bytes(3, "little"))
        payload.extend(length.to_bytes(3, "little"))
    else:
        raise AssertionError("Unsupported test flags")
    return block(11, 0x2629, [block(1, 0x2628, [payload])])


def group_definition(name, length):
    encoded = name.encode("utf-8")
    attributes = bytearray(b"\x00\x50\x30\x44\x08")
    attributes.extend(b"\x11\x22\x33\x44\x55")
    attributes.extend(length.to_bytes(3, "little"))
    attributes.extend(b"\x00" * 8)
    payload = bytearray(struct.pack("<I", len(encoded)) + encoded) + attributes
    return block(11, 0x262B, [block(1, 0x2628, [payload])])


def make_session(audio=(), groups=()):
    session = ProToolsSession.__new__(ProToolsSession)
    session.sample_rate = 48_000
    session.frame_rate_enum = 0x01
    session.root_items = [
        block(1, 0x262A, [bytearray(struct.pack("<I", len(audio))), *audio]),
        block(1, 0x262C, [bytearray(struct.pack("<I", len(groups))), *groups]),
    ]
    return session


class GetClipsTests(unittest.TestCase):
    def test_parent_and_virtual_24_bit_fields_are_decoded_exactly(self):
        session = make_session(
            audio=[
                audio_definition("PARENT", 0x0000, 48_000, timestamp_low=0xAB),
                audio_definition("RIGHT", 0x3001, 24_000, source_offset=12_000),
            ]
        )

        result = session.get_clips()

        self.assertEqual(result, [
            {
                "name": "PARENT",
                "type": "parent",
                "length": "00:00:01:00",
                "src_offset": "00:00:00:00",
            },
            {
                "name": "RIGHT",
                "type": "virtual",
                "length": "00:00:00:12",
                "src_offset": "00:00:00:06",
            },
        ])

    def test_clip_group_length_uses_verified_offset_plus_ten(self):
        session = make_session(groups=[group_definition("GROUP.grp", 96_000)])

        result = session.get_clips()

        self.assertEqual(result[-1], {
            "name": "GROUP.grp",
            "type": "group",
            "length": "00:00:02:00",
            "src_offset": "00:00:00:00",
        })

    def test_inconsistent_container_count_is_rejected(self):
        session = make_session(audio=[audio_definition("CLIP", 0x0000, 100)])
        struct.pack_into("<I", session.root_items[0].items[0], 0, 2)

        with self.assertRaisesRegex(ValueError, "clip count"):
            session.get_clips()


if __name__ == "__main__":
    unittest.main()
