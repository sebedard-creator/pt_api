import struct
import unittest

from pt_api import PTBlock, ProToolsSession


def block(block_type, content_type, items):
    result = PTBlock(block_type, content_type, 1)
    result.items = items
    return result


def group_definition(name, length):
    encoded = name.encode("utf-8")
    attributes = bytearray(b"\x00\x50\x30\x44\x08")
    attributes.extend(b"\x11\x22\x33\x44\x55")
    attributes.extend(length.to_bytes(3, "little"))
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

    def test_rejects_macro_that_references_an_unknown_group_id(self):
        session = make_session(
            groups=[group_definition("GROUP_TEST", 48_000)],
            playlists=[playlist("TRACK", [timeline_event(1, 0)])],
        )

        with self.assertRaisesRegex(ValueError, "unknown group ID"):
            session.get_timeline_clip_groups()


if __name__ == "__main__":
    unittest.main()
