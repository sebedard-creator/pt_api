import struct
import unittest

from pt_api import PTBlock, ProToolsSession


def block(block_type, content_type, items):
    result = PTBlock(block_type, content_type, 1)
    result.items = items
    return result


def audio_definition(name, flags, length, source_offset=0):
    encoded = name.encode("utf-8")
    payload = bytearray(struct.pack("<I", len(encoded)) + encoded)
    source_width = {
        0x0000: 0,
        0x0001: 0,
        0x2000: 2,
        0x2001: 2,
        0x3000: 3,
        0x3001: 3,
        0x4001: 4,
    }.get(flags)
    if source_width is None:
        raise AssertionError("Unsupported test flags")
    if length <= 0xFF:
        length_width = 1
    elif length <= 0xFFFF:
        length_width = 2
    elif length <= 0xFFFFFF:
        length_width = 3
    else:
        length_width = 4
    selector = bytes([length_width << 4])
    layout = bytes([(source_width << 4) | 0x04, 0x08])
    payload.extend(struct.pack("<H", flags) + selector + layout)
    if source_width:
        payload.extend(source_offset.to_bytes(source_width, "little"))
    payload.extend(length.to_bytes(length_width, "little"))
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
    def test_parent_uint32_and_virtual_selected_widths_are_decoded_exactly(self):
        session = make_session(
            audio=[
                audio_definition("PARENT", 0x0000, 17_280_000),
                audio_definition(
                    "RIGHT", 0x3001, 16_800_000, source_offset=480_000
                ),
                audio_definition(
                    "LATE", 0x4001, 480_000, source_offset=16_800_000
                ),
            ]
        )

        result = session.get_clips()

        self.assertEqual(result, [
            {
                "name": "PARENT",
                "type": "parent",
                "length": "00:06:00:00",
                "src_offset": "00:00:00:00",
            },
            {
                "name": "RIGHT",
                "type": "virtual",
                "length": "00:05:50:00",
                "src_offset": "00:00:10:00",
            },
            {
                "name": "LATE",
                "type": "virtual",
                "length": "00:00:10:00",
                "src_offset": "00:05:50:00",
            },
        ])

    def test_observed_compact_widths_and_parent_variants_are_decoded(self):
        definitions = [
            audio_definition("ZERO", 0x0001, 0x7F),
            audio_definition("SRC16", 0x2001, 0x123456, source_offset=0x1234),
            audio_definition("LEN16", 0x3001, 0x3456, source_offset=0x123456),
            audio_definition("PARENT", 0x3000, 0x654321, source_offset=0x234567),
        ]
        session = make_session(audio=definitions)

        decoded = [
            session._decode_audio_clip_payload(item.items[0].items[0])
            for item in definitions
        ]

        self.assertEqual(
            [(item["flags"], item["src_offset"], item["length"]) for item in decoded],
            [
                (0x0001, 0, 0x7F),
                (0x2001, 0x1234, 0x123456),
                (0x3001, 0x123456, 0x3456),
                (0x3000, 0x234567, 0x654321),
            ],
        )
        self.assertEqual(session.get_clips()[-1]["type"], "parent")

    def test_clip_group_length_uses_verified_offset_plus_ten(self):
        session = make_session(groups=[group_definition("GROUP.grp", 96_000)])

        result = session.get_clips()

        self.assertEqual(result[-1], {
            "name": "GROUP.grp",
            "type": "group",
            "length": "00:00:02:00",
            "src_offset": "00:00:00:00",
        })

    def test_unknown_audio_width_selector_is_rejected(self):
        definition = audio_definition("CLIP", 0x0000, 48_000)
        payload = definition.items[0].items[0]
        name_length = struct.unpack_from("<I", payload, 0)[0]
        payload[4 + name_length + 2] = 0x50
        session = make_session(audio=[definition])

        with self.assertRaisesRegex(ValueError, "width selector"):
            session.get_clips()

    def test_inconsistent_container_count_is_rejected(self):
        session = make_session(audio=[audio_definition("CLIP", 0x0000, 100)])
        struct.pack_into("<I", session.root_items[0].items[0], 0, 2)

        with self.assertRaisesRegex(ValueError, "clip count"):
            session.get_clips()


if __name__ == "__main__":
    unittest.main()
