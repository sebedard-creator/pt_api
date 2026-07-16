import struct
import unittest

from pt_api import PTBlock, ProToolsSession


def block(block_type, content_type, items):
    result = PTBlock(block_type, content_type, 1)
    result.items = items
    return result


def definition(name, length=1_000, flags=0x0000, src_offset=0):
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
        raise AssertionError("Unsupported test flag")
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
        payload.extend(src_offset.to_bytes(source_width, "little"))
    payload.extend(length.to_bytes(length_width, "little"))
    return block(11, 0x2629, [block(1, 0x2628, [payload])])


def event(clip_id, timestamp, event_type=0x03, muted=False, secondary_prefix=0):
    payload = bytearray(35)
    payload[0] = 1 if muted else 0
    struct.pack_into("<I", payload, 2, clip_id)
    struct.pack_into("<Q", payload, 7, timestamp)
    payload[15] = event_type
    tail = (
        bytes([secondary_prefix, 0x01, 0x01])
        if event_type == 0x03 else b"\x01\x01\x01"
    )
    return block(3, 0x1050, [block(3, 0x104F, [payload]), bytearray(tail)])


def playlist(name, events):
    encoded = name.encode("utf-8")
    header = bytearray(
        struct.pack("<I", len(encoded))
        + encoded
        + struct.pack("<I", len(events))
    )
    return block(3, 0x1052, [header, *events, bytearray(8)])


def geometry(payload):
    return block(2, 0x262F, [bytearray(payload)])


def make_session(definitions, playlists, geometries=(), extra_clip_items=()):
    session = ProToolsSession.__new__(ProToolsSession)
    session.sample_rate = 48_000
    session.frame_rate_enum = 0x01
    session.is_bigendian = False
    session.file_path = r"Z:\missing\session.ptx"
    session._removed_offsets = []

    clip_items = [bytearray(struct.pack("<I", len(definitions)))]
    for index, item in enumerate(definitions):
        clip_items.append(item)
        if index < len(extra_clip_items):
            clip_items.append(extra_clip_items[index])
    clip_list = block(1, 0x262A, clip_items)
    track_map = block(
        2,
        0x1054,
        [bytearray(struct.pack("<I", len(playlists))), *playlists],
    )
    session.root_items = [clip_list, track_map]
    if geometries is not None:
        session.root_items.append(
            block(
                1,
                0x2630,
                [bytearray(struct.pack("<I", len(geometries))), *geometries],
            )
        )
    return session


class TimelineReadTests(unittest.TestCase):
    def test_clip_ids_are_ordinal_even_with_raw_items_between_definitions(self):
        session = make_session(
            [definition("FIRST"), definition("SECOND")],
            [playlist("TRACK", [event(1, 100)])],
            geometries=None,
            extra_clip_items=(bytearray(b"RAW"),),
        )

        result = session.get_timeline_clips()

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["clip_name"], "SECOND")

    def test_parent_length_uses_all_four_uint32_bytes(self):
        parent = definition("PARENT", length=0x01345678)
        session = make_session(
            [parent],
            [playlist("TRACK", [event(0, 100)])],
            geometries=None,
        )

        result = session.get_timeline_clips()

        self.assertEqual(result[0]["length_samples"], 0x01345678)

    def test_clip_group_macro_is_not_mislabeled_as_colliding_audio_id(self):
        macro = event(0, 100)
        macro.items[-1] = bytearray(b"\x00\x00\x01")
        session = make_session(
            [definition("ORDINARY")],
            [playlist("TRACK", [macro])],
            geometries=None,
        )

        self.assertEqual(session.get_timeline_clips(), [])

    def test_observed_alternate_audio_secondary_prefix_is_supported(self):
        session = make_session(
            [definition("ORDINARY")],
            [playlist("TRACK", [event(0, 100, secondary_prefix=1)])],
            geometries=None,
        )

        result = session.get_timeline_clips()

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["clip_name"], "ORDINARY")

    def test_fade_extents_use_native_geometry_instead_of_parent_length(self):
        fade_in = bytearray(22)
        struct.pack_into("<H", fade_in, 8, 20)
        crossfade = bytearray(34)
        struct.pack_into("<H", crossfade, 8, 10)
        struct.pack_into("<H", crossfade, 10, 20)
        fade_out = bytearray(27)
        struct.pack_into("<H", fade_out, 8, 20)
        session = make_session(
            [
                definition("TAKE", length=1_000),
                definition("TAKE-01", length=400),
                definition("TAKE-02", length=600),
            ],
            [
                playlist(
                    "TRACK",
                    [
                        # Fade IDs index geometries, independently of clip IDs.
                        event(1, 100, event_type=0x01, muted=True),
                        event(0, 100),
                        event(0, 1_100, event_type=0x01, muted=True),
                        event(1, 1_600),
                        event(2, 2_000, event_type=0x01),
                        event(2, 2_000),
                    ],
                )
            ],
            [geometry(fade_out), geometry(fade_in), geometry(crossfade)],
        )
        file_links = block(
            1,
            0x1004,
            [block(1, 0x103A, [bytearray(b"Disk:Audio Files:TAKE.aif\x00")])],
        )
        session.root_items.append(file_links)

        result = session.get_timeline_clips()

        fades = [item for item in result if item["is_fade"]]
        self.assertEqual(
            [
                (item["start_samples"], item["length_samples"], item["end_samples"])
                for item in fades
            ],
            [(100, 20, 120), (1_080, 20, 1_100), (1_990, 20, 2_010)],
        )
        self.assertTrue(all(item["muted"] is False for item in fades))
        self.assertTrue(all(item["physical_filename"] == "TAKE.aif" for item in result))

    def test_events_are_sorted_globally_by_timestamp_not_track_name(self):
        session = make_session(
            [definition("CLIP")],
            [
                playlist("Z_TRACK", [event(0, 100)]),
                playlist("A_TRACK", [event(0, 200)]),
            ],
            geometries=None,
        )

        result = session.get_timeline_clips()

        self.assertEqual(
            [(item["track"], item["start_samples"]) for item in result],
            [("Z_TRACK", 100), ("A_TRACK", 200)],
        )

    def test_fade_geometry_count_mismatch_is_rejected(self):
        session = make_session(
            [definition("CLIP")],
            [playlist("TRACK", [event(0, 100, event_type=0x01)])],
            [],
        )

        with self.assertRaisesRegex(ValueError, "geometry counts do not match"):
            session.get_timeline_clips()

    def test_audio_only_read_skips_unneeded_fade_validation(self):
        session = make_session(
            [definition("CLIP")],
            [
                playlist(
                    "TRACK",
                    [event(0, 100), event(0, 200, event_type=0x01)],
                )
            ],
            [],
        )

        result = session.get_timeline_clips(include_fades=False)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["clip_name"], "CLIP")

    def test_include_fades_must_be_boolean(self):
        session = make_session(
            [definition("CLIP")],
            [playlist("TRACK", [event(0, 100)])],
            geometries=None,
        )

        with self.assertRaisesRegex(TypeError, "include_fades"):
            session.get_timeline_clips(include_fades=1)

    def test_duplicate_geometry_id_is_rejected(self):
        fade_in = bytearray(22)
        fade_out = bytearray(27)
        session = make_session(
            [definition("CLIP")],
            [
                playlist(
                    "TRACK",
                    [
                        event(0, 100, event_type=0x01),
                        event(0, 100),
                        event(0, 1_100, event_type=0x01),
                    ],
                )
            ],
            [geometry(fade_in), geometry(fade_out)],
        )

        with self.assertRaisesRegex(ValueError, "Duplicate fade geometry ID"):
            session.get_timeline_clips()


if __name__ == "__main__":
    unittest.main()
