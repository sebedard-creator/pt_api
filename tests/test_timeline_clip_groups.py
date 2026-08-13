import struct
import unittest

from pt_api import PTBlock, ProToolsSession


def block(block_type, content_type, items):
    result = PTBlock(block_type, content_type, 1)
    result.items = items
    return result


def group_definition(name, length, width_selector=0x30):
    encoded = name.encode("utf-8")
    width_by_selector = {0x10: 1, 0x20: 2, 0x30: 3, 0x40: 4}
    attributes = bytearray(b"\x00\x50" + bytes((width_selector, 0x44, 0x08)))
    attributes.extend(b"\x11\x22\x33\x44\x55")
    attributes.extend(length.to_bytes(width_by_selector[width_selector], "little"))
    attributes.extend(b"\x00" * 8)
    payload = bytearray(struct.pack("<I", len(encoded)) + encoded) + attributes
    return block(11, 0x262B, [block(1, 0x2628, [payload])])


def timeline_event(group_id, start_samples, tail=b"\x00\x00\x01"):
    payload = bytearray(35)
    struct.pack_into("<I", payload, 2, group_id)
    struct.pack_into("<Q", payload, 7, start_samples)
    payload[15] = 0x03
    return block(3, 0x1050, [block(3, 0x104F, [payload]), bytearray(tail)])


def playlist(name, events):
    encoded = name.encode("utf-8")
    header = bytearray(
        struct.pack("<I", len(encoded)) + encoded + struct.pack("<I", len(events))
    )
    return block(3, 0x1052, [header, *events, bytearray(8)])


def make_session(groups=(), playlists=()):
    session = ProToolsSession.__new__(ProToolsSession)
    session.sample_rate = 48_000
    session.frame_rate_enum = 0x01
    session.root_items = [
        block(1, 0x262C, [bytearray(struct.pack("<I", len(groups))), *groups]),
        block(2, 0x1054, [bytearray(struct.pack("<I", len(playlists))), *playlists]),
    ]
    return session


def native_simple_group(name, group_id, start_samples, length_samples):
    """Minimal verified 0x5000 group profile used by the writer test."""
    encoded = name.encode("utf-8")
    width = 2 if length_samples <= 0xFFFF else 3
    selector = {2: 0x20, 3: 0x30}[width]
    payload = bytearray(struct.pack("<I", len(encoded)) + encoded)
    payload.extend(b"\x00\x50" + bytes((selector, 0x44, 0x08, 0x00)))
    payload.extend(struct.pack("<I", 0xE8D4A510))
    payload.extend(length_samples.to_bytes(width, "little"))
    payload.extend(struct.pack("<II", start_samples, start_samples))
    payload.extend(b"\xff" * 8 + b"\xfe\xff" + b"\x00" * 16)

    group_timing = bytearray(65)
    struct.pack_into("<I", group_timing, 0, start_samples)
    struct.pack_into("<I", group_timing, 16, group_id)
    struct.pack_into("<I", group_timing, 36, 0xE8A5CBB0 + length_samples)
    struct.pack_into("<I", group_timing, 44, start_samples + length_samples)
    return block(
        1,
        0x262B,
        [
            block(4, 0x2628, [payload]),
            block(9, 0x2523, [block(2, 0x2526, [bytearray(12)]), group_timing]),
            bytearray((0, group_id, 0, 0, 0, 0, 0, 0, 0)),
        ],
    )


def pointer_record(offset):
    return bytearray(
        b"\x00\x01" + bytes.fromhex("0000000104000100")
        + struct.pack("<I", offset) + b"\x00\x00\x00"
    )


def writable_group_session():
    """Construct the native one-track creation profile without a PTX fixture."""
    prototype_start = 1_000_000
    prototype_length = 48_048
    source_start = 1_100_000
    prototype_group = native_simple_group(
        "PROTOTYPE", 0, prototype_start, prototype_length
    )
    group_list = block(1, 0x262C, [bytearray(struct.pack("<I", 1)), prototype_group])

    name = block(4, 0x2423, [bytearray(struct.pack("<II", 0, 9) + b"PROTOTYPE")])
    name.original_offset = 0x1000
    names = block(1, 0x2424, [bytearray(struct.pack("<I", 1)), name])
    metadata = block(2, 0x2425, [bytearray(b"\x01\x00\x00\x00\x00" + struct.pack("<I", 0))])
    metadata.original_offset = 0x1100
    metadata_list = block(1, 0x2426, [bytearray(struct.pack("<I", 1)), metadata])

    hidden = block(3, 0x1052, [bytearray(b"\x01\x00\x00\x00?\x00\x00\x00\x00\x01\x00")])
    hidden.original_offset = 0x1200
    hidden_map = block(2, 0x1054, [bytearray(struct.pack("<I", 1)), hidden])
    hidden_root = block(1, 0x2428, [hidden_map])

    prototype_macro = timeline_event(0, prototype_start)
    prototype_macro.items[0].items[0][18] = 1
    source_audio = timeline_event(7, source_start, tail=b"\x00\x01\x01")
    track = playlist("TRACK", [prototype_macro, source_audio])
    main_map = block(2, 0x1054, [bytearray(struct.pack("<I", 1)), track])

    pointer_table = block(
        1, 0x0002,
        [pointer_record(name.original_offset), pointer_record(metadata.original_offset), pointer_record(hidden.original_offset)],
    )
    session = ProToolsSession.__new__(ProToolsSession)
    session.sample_rate = 48_000
    session.frame_rate_enum = 0x01
    session.is_bigendian = False
    session._removed_offsets = []
    session.root_items = [
        group_list, names, metadata_list, hidden_root, main_map, pointer_table,
    ]
    session._audio_clip_info_by_id = lambda clip_id: {
        "name": "SOURCE", "length": 168_168 if clip_id == 7 else 0
    }
    return session, source_start


class TimelineClipGroupTests(unittest.TestCase):
    def test_returns_each_placement_with_track_samples_and_timecodes(self):
        session = make_session(
            groups=[
                group_definition("GROUP_TEST", 288_000),
                group_definition("GROUP_TEST", 48_000),
            ],
            playlists=[
                playlist("Z_TRACK", [timeline_event(0, 576_000)]),
                playlist(
                    "A_TRACK",
                    [
                        # A normal audio event may share ID 0 but is not a group macro.
                        timeline_event(0, 1, tail=b"\x00\x01\x01"),
                        timeline_event(1, 576_000),
                        timeline_event(0, 864_000),
                    ],
                ),
            ],
        )

        result = session.get_timeline_clip_groups()

        self.assertEqual(
            result,
            [
                {
                    "group_id": 1,
                    "group_name": "GROUP_TEST",
                    "track": "A_TRACK",
                    "start_samples": 576_000,
                    "length_samples": 48_000,
                    "end_samples": 624_000,
                    "start_timecode": "00:00:12:00",
                    "length_timecode": "00:00:01:00",
                    "end_timecode": "00:00:13:00",
                },
                {
                    "group_id": 0,
                    "group_name": "GROUP_TEST",
                    "track": "Z_TRACK",
                    "start_samples": 576_000,
                    "length_samples": 288_000,
                    "end_samples": 864_000,
                    "start_timecode": "00:00:12:00",
                    "length_timecode": "00:00:06:00",
                    "end_timecode": "00:00:18:00",
                },
                {
                    "group_id": 0,
                    "group_name": "GROUP_TEST",
                    "track": "A_TRACK",
                    "start_samples": 864_000,
                    "length_samples": 288_000,
                    "end_samples": 1_152_000,
                    "start_timecode": "00:00:18:00",
                    "length_timecode": "00:00:06:00",
                    "end_timecode": "00:00:24:00",
                },
            ],
        )

    def test_returns_empty_list_when_session_has_no_group_definitions(self):
        session = ProToolsSession.__new__(ProToolsSession)
        session.sample_rate = 48_000
        session.frame_rate_enum = 0x01
        session.root_items = []

        self.assertEqual(session.get_timeline_clip_groups(), [])

    def test_decodes_a_uint16_clip_group_duration(self):
        session = make_session(
            groups=[group_definition("ONE_SECOND", 48_048, width_selector=0x20)],
            playlists=[playlist("TRACK", [timeline_event(0, 0)])],
        )

        result = session.get_timeline_clip_groups()

        self.assertEqual(result[0]["length_samples"], 48_048)
        self.assertEqual(result[0]["length_timecode"], "00:00:01:00")

    def test_rejects_macro_that_references_an_unknown_group_id(self):
        session = make_session(
            groups=[group_definition("GROUP_TEST", 48_000)],
            playlists=[playlist("TRACK", [timeline_event(1, 0)])],
        )

        with self.assertRaisesRegex(ValueError, "unknown group ID"):
            session.get_timeline_clip_groups()

    def test_create_converts_one_audio_placement_to_a_variable_group(self):
        session, source_start = writable_group_session()

        created = session.create_clip_group(
            "TRACK", "GENERATED", source_start, "PROTOTYPE"
        )

        self.assertEqual(
            created,
            {
                "group_id": 1,
                "group_name": "GENERATED",
                "track": "TRACK",
                "start_samples": source_start,
                "length_samples": 168_168,
                "end_samples": source_start + 168_168,
            },
        )
        groups = session.get_timeline_clip_groups()
        self.assertEqual(groups[1]["group_name"], "GENERATED")
        self.assertEqual(groups[1]["length_samples"], 168_168)
        playlist_events = session._validated_main_playlists()[0][2]
        macro = playlist_events[1]
        self.assertEqual(bytes(macro.items[-1]), b"\x00\x00\x01")
        self.assertEqual(macro.items[0].items[0][18], 1)


if __name__ == "__main__":
    unittest.main()
