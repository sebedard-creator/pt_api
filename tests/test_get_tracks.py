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


if __name__ == "__main__":
    unittest.main()
