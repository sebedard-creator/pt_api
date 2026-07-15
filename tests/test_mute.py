import struct
import unittest

from pt_api import PTBlock, ProToolsSession


def block(block_type, content_type, items):
    result = PTBlock(block_type, content_type, 1)
    result.items = items
    return result


def clip_definition(name):
    encoded = name.encode("utf-8")
    name_block = block(
        1,
        0x2628,
        [bytearray(struct.pack("<I", len(encoded)) + encoded + b"TAIL")],
    )
    return block(11, 0x2629, [name_block])


def event(clip_id, event_type=0x03, muted=False):
    payload = bytearray(35)
    payload[0] = 1 if muted else 0
    struct.pack_into("<I", payload, 2, clip_id)
    struct.pack_into("<Q", payload, 7, 10_000)
    payload[15] = event_type
    tail = b"\x00\x01\x01" if event_type == 0x03 else b"\x01\x01\x01"
    return block(3, 0x1050, [block(3, 0x104F, [payload]), bytearray(tail)])


def make_session(include_placement=True):
    session = ProToolsSession.__new__(ProToolsSession)
    session.is_bigendian = False
    session._removed_offsets = []

    clip_list = block(
        1,
        0x262A,
        [bytearray(struct.pack("<I", 1)), clip_definition("CLIP")],
    )

    audio_one = event(0)
    fade = event(0, event_type=0x01)
    audio_two = event(0, muted=True)
    events = [audio_one, fade, audio_two] if include_placement else []
    track_name = b"AUDIO TRACK"
    header = bytearray(
        struct.pack("<I", len(track_name))
        + track_name
        + struct.pack("<I", len(events))
    )
    playlist = block(3, 0x1052, [header, *events, bytearray(8)])
    track_map = block(2, 0x1054, [bytearray(struct.pack("<I", 1)), playlist])
    session.root_items = [clip_list, track_map]
    return session, playlist, audio_one, fade, audio_two


def mute_flag(timeline_event):
    b104f = next(
        child for child in timeline_event.items
        if isinstance(child, PTBlock) and child.content_type == 0x104F
    )
    return b104f.items[0][0]


class MuteClipTests(unittest.TestCase):
    def test_only_visible_audio_events_are_muted(self):
        session, _, audio_one, fade, audio_two = make_session()

        count = session.mute_clip("CLIP", True)

        self.assertEqual(count, 2)
        self.assertEqual(mute_flag(audio_one), 1)
        self.assertEqual(mute_flag(audio_two), 1)
        self.assertEqual(mute_flag(fade), 0)

    def test_unmute_updates_all_audio_occurrences_but_not_fade(self):
        session, _, audio_one, fade, audio_two = make_session()
        session.mute_clip("CLIP", True)

        count = session.mute_clip("CLIP", False)

        self.assertEqual(count, 2)
        self.assertEqual(mute_flag(audio_one), 0)
        self.assertEqual(mute_flag(audio_two), 0)
        self.assertEqual(mute_flag(fade), 0)

    def test_inconsistent_playlist_count_is_rejected_without_mutation(self):
        session, playlist, audio_one, _, audio_two = make_session()
        header = playlist.items[0]
        count_offset = 4 + struct.unpack_from("<I", header, 0)[0]
        struct.pack_into("<I", header, count_offset, 99)

        with self.assertRaisesRegex(ValueError, "event count"):
            session.mute_clip("CLIP", True)

        self.assertEqual(mute_flag(audio_one), 0)
        self.assertEqual(mute_flag(audio_two), 1)

    def test_clip_without_visible_placement_is_rejected(self):
        session, _, _, _, _ = make_session(include_placement=False)

        with self.assertRaisesRegex(ValueError, "no visible audio placement"):
            session.mute_clip("CLIP", True)

    def test_mute_argument_must_be_boolean(self):
        session, _, _, _, _ = make_session()

        with self.assertRaisesRegex(TypeError, "boolean"):
            session.mute_clip("CLIP", 1)

    def test_clip_group_macro_with_colliding_id_is_not_muted(self):
        session, playlist, audio_one, _, audio_two = make_session()
        macro = event(0)
        macro.items[-1] = bytearray(b"\x00\x00\x01")
        playlist.items.insert(-1, macro)
        name_length = struct.unpack_from("<I", playlist.items[0], 0)[0]
        struct.pack_into("<I", playlist.items[0], 4 + name_length, 4)

        count = session.mute_clip("CLIP", True)

        self.assertEqual(count, 2)
        self.assertEqual(mute_flag(audio_one), 1)
        self.assertEqual(mute_flag(audio_two), 1)
        self.assertEqual(mute_flag(macro), 0)


if __name__ == "__main__":
    unittest.main()
