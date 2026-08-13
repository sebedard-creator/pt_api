import struct
import unittest

from pt_api import PTBlock, ProToolsSession


def block(block_type, content_type, items):
    result = PTBlock(block_type, content_type, 1)
    result.items = items
    return result


def playlist(name, event_count=0, events=()):
    encoded = name.encode("utf-8")
    header = bytearray(
        struct.pack("<I", len(encoded))
        + encoded
        + struct.pack("<I", event_count)
    )
    return block(3, 0x1052, [header, *events])


def make_session(playlists, declared=None):
    session = ProToolsSession.__new__(ProToolsSession)
    if declared is None:
        declared = len(playlists)
    session.root_items = [
        block(2, 0x1054, [bytearray(struct.pack("<I", declared)), *playlists])
    ]
    return session


def make_native_rename_profile(track_names=("AUDIO 1", "AUDIO 2")):
    """Build the verified six-name-mirror profile used by rename_track()."""
    playlists = [playlist(name) for name in track_names]
    session = make_session(playlists)

    aggregate = bytearray(b"\x01\x00\x00\x00")
    display_entries = []
    identity_entries = []
    metadata_entries = []
    descriptor_entries = []
    for index, name in enumerate(track_names):
        encoded = name.encode("utf-8")
        aggregate.extend(struct.pack("<I", len(encoded)) + encoded + bytes((index,)))
        for _ in range(2):
            display_entries.append(
                block(10, 0x251A, [
                    bytearray(b"\x00\x00" + struct.pack("<I", len(encoded)) + encoded + b"\x00"),
                    bytearray(b"\x00\x00"),
                ])
            )
        descriptor_entries.append(
            block(8, 0x1014, [bytearray(struct.pack("<I", len(encoded)) + encoded + b"\x00")])
        )
        metadata_entries.append(
            block(1, 0x210B, [
                bytearray(b"\x00" * 4 + struct.pack("<I", len(encoded)) + encoded + b"\x00")
            ])
        )
        # This legacy identity label is intentionally not a name mirror.
        identity_entries.append(
            block(9, 0x2619, [bytearray(struct.pack("<I", len(encoded)) + encoded)])
        )

    session.root_items.extend([
        block(2, 0x1015, [bytearray(struct.pack("<I", len(track_names))), *descriptor_entries]),
        block(5, 0x2107, [bytearray(b"\x00" * 13), *metadata_entries]),
        block(8, 0x2519, [aggregate, *display_entries]),
        block(1, 0x2624, [*identity_entries]),
    ])
    return session


def make_native_visibility_profile(
    track_names=("AUDIO 1", "AUDIO 2", "AUDIO 3"),
    visibility_suffixes=None,
):
    """Build the explicit 0x251a/0x2589 visibility mirrors."""
    session = make_session([playlist(name) for name in track_names])
    if visibility_suffixes is None:
        visibility_suffixes = [b"\xfe\xff"] * len(track_names)
    if len(visibility_suffixes) != len(track_names):
        raise ValueError("visibility_suffixes must match track_names.")
    if any(not isinstance(value, bytes) or len(value) != 2 for value in visibility_suffixes):
        raise ValueError("visibility suffixes must be two-byte values.")
    aggregate = bytearray(b"\x01" * 16 + struct.pack("<I", len(track_names)))
    displays_first = []
    displays_second = []
    states = []
    for index, name in enumerate(track_names):
        encoded = name.encode("utf-8")
        identity = struct.pack("<I", 0x1000 + index) + b"\x00\x00\x00\x00"
        entry = (
            struct.pack("<I", len(encoded)) + encoded + b"\x00" * 8
            + b"\x2a\x00\x00\x00" + identity + bytes((index + 1,))
        )
        aggregate.extend(entry)
        for target in (displays_first, displays_second):
            target.append(block(10, 0x251A, [
                bytearray(b"\x00\x00" + entry + b"\x00"),
                bytearray(b"\x00\x00"),
                bytearray(
                    b"\x00\x00\x00\x00\x01\x01\x00\x00\x00"
                    + visibility_suffixes[index]
                ),
            ]))
        states.append(block(9, 0x2589, [
            bytearray(struct.pack("<HH", index, 1) + b"\x3d\x00"),
            bytearray(b"\x00\x00\x00\x00\x00"),
        ]))
    session.root_items.extend([
        block(8, 0x2519, [aggregate, *displays_first, bytearray(struct.pack("<I", len(track_names))), *displays_second]),
        block(1, 0x2587, [*states]),
    ])
    return session


class GetTracksTests(unittest.TestCase):
    def test_returns_ordered_main_track_names_including_empty_tracks(self):
        session = make_session([playlist("PISTE É"), playlist("SECOND")])

        self.assertEqual(session.get_tracks(), ["PISTE É", "SECOND"])

    def test_track_count_mismatch_is_rejected(self):
        session = make_session([playlist("TRACK")], declared=2)

        with self.assertRaisesRegex(ValueError, "track count"):
            session.get_tracks()

    def test_event_count_mismatch_is_rejected(self):
        session = make_session([playlist("TRACK", event_count=1)])

        with self.assertRaisesRegex(ValueError, "event count"):
            session.get_tracks()

    def test_truncated_playlist_header_has_clear_error(self):
        session = make_session([block(3, 0x1052, [bytearray(b"BAD")])])

        with self.assertRaisesRegex(ValueError, "playlist header"):
            session.get_tracks()

    def test_missing_main_track_map_returns_empty_list(self):
        session = ProToolsSession.__new__(ProToolsSession)
        session.root_items = []

        self.assertEqual(session.get_tracks(), [])


class RenameTrackTests(unittest.TestCase):
    def test_renames_all_verified_visible_name_mirrors_only(self):
        session = make_native_rename_profile()

        renamed = session.rename_track("AUDIO 1", "PLAYBACK NOTES")

        self.assertEqual(renamed, 1)
        self.assertEqual(session.get_tracks(), ["PLAYBACK NOTES", "AUDIO 2"])
        self.assertEqual(
            bytes(session._root_blocks(0x1015)[0].get_all_blocks(0x1014)[0].items[0]),
            b"\x0e\x00\x00\x00PLAYBACK NOTES\x00",
        )
        self.assertEqual(
            bytes(session._root_blocks(0x2107)[0].get_all_blocks(0x210B)[0].items[0]),
            b"\x00" * 4 + b"\x0e\x00\x00\x00PLAYBACK NOTES\x00",
        )
        self.assertIn(
            b"\x0e\x00\x00\x00PLAYBACK NOTES",
            bytes(session._root_blocks(0x2519)[0].items[0]),
        )
        display_entries = [
            entry for entry in session._root_blocks(0x2519)[0].get_all_blocks(0x251A)
            if b"PLAYBACK NOTES" in bytes(entry.items[0])
        ]
        self.assertEqual(len(display_entries), 2)
        self.assertTrue(all(
            b"\x0e\x00\x00\x00PLAYBACK NOTES" in bytes(entry.items[0])
            for entry in display_entries
        ))
        # Native Pro Tools does not alter the stale 0x2619 identity label.
        self.assertEqual(
            bytes(session._root_blocks(0x2624)[0].get_all_blocks(0x2619)[0].items[0]),
            b"\x07\x00\x00\x00AUDIO 1",
        )

    def test_rejects_duplicate_destination_without_mutation(self):
        session = make_native_rename_profile()
        before = bytes(session._root_blocks(0x1054)[0].items[1].items[0])

        with self.assertRaisesRegex(ValueError, "already exists"):
            session.rename_track("AUDIO 1", "AUDIO 2")

        self.assertEqual(bytes(session._root_blocks(0x1054)[0].items[1].items[0]), before)


class TrackVisibilityTests(unittest.TestCase):
    def test_sets_exact_visible_track_set_and_requested_order(self):
        session = make_native_visibility_profile()

        shown = session.set_visible_tracks(["AUDIO 3", "AUDIO 1"])

        self.assertEqual(shown, ["AUDIO 3", "AUDIO 1"])
        root_2519 = session._root_blocks(0x2519)[0]
        aggregate = bytes(root_2519.items[0])
        def aggregate_status(name):
            marker = aggregate.find(b"\x2a\x00\x00\x00", aggregate.find(name.encode("utf-8")))
            return aggregate[marker + 12]
        self.assertEqual(aggregate_status("AUDIO 1"), 2)
        self.assertEqual(aggregate_status("AUDIO 2"), 0)
        self.assertEqual(aggregate_status("AUDIO 3"), 1)
        displays = root_2519.get_all_blocks(0x251A)
        self.assertEqual([bytes(item.items[2])[4] for item in displays[:3]], [1, 0, 1])
        states = session._root_blocks(0x2587)[0].get_all_blocks(0x2589)
        state_by_index = {
            struct.unpack_from("<H", state.items[0], 0)[0]: bytes(state.items[0])[2]
            for state in states
        }
        self.assertEqual(state_by_index, {0: 1, 1: 0, 2: 1})

    def test_rejects_unknown_track_without_mutation(self):
        session = make_native_visibility_profile()
        before = bytes(session._root_blocks(0x2519)[0].items[0])

        with self.assertRaisesRegex(ValueError, "not found"):
            session.set_visible_tracks(["MISSING"])

        self.assertEqual(bytes(session._root_blocks(0x2519)[0].items[0]), before)

    def test_preserves_native_per_track_visibility_suffixes(self):
        suffixes = [b"\xfe\xff", b"\x1e\x00", b"\x2d\x00"]
        session = make_native_visibility_profile(visibility_suffixes=suffixes)
        root_2519 = session._root_blocks(0x2519)[0]
        before = [
            bytes(item.items[2])
            for item in root_2519.get_all_blocks(0x251A)
        ]

        session.set_visible_tracks(["AUDIO 1", "AUDIO 3"])

        after = [
            bytes(item.items[2])
            for item in root_2519.get_all_blocks(0x251A)
        ]
        self.assertEqual([item[4] for item in after[:3]], [1, 0, 1])
        for old, new in zip(before, after):
            self.assertEqual(old[:4], new[:4])
            self.assertEqual(old[5:], new[5:])

    def test_rejects_incomplete_profile_without_mutation(self):
        session = make_native_rename_profile()
        root_2519 = session._root_blocks(0x2519)[0]
        root_2519.items.pop(1)
        before = bytes(session._root_blocks(0x1054)[0].items[1].items[0])

        with self.assertRaisesRegex(ValueError, "0x2519"):
            session.rename_track("AUDIO 1", "PLAYBACK NOTES")

        self.assertEqual(bytes(session._root_blocks(0x1054)[0].items[1].items[0]), before)


if __name__ == "__main__":
    unittest.main()
