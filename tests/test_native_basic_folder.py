"""Optional named-playlist Basic Folder pair, not an anonymous AAF fixture."""
from pathlib import Path
import struct
import tempfile
import unittest

from pt_api import ProToolsSession

FIXTURES = Path(__file__).parent / 'fixtures'
BEFORE = FIXTURES / 'native_basic_folder_before.ptx'
AFTER = FIXTURES / 'native_basic_folder_after.ptx'
RESAVED = FIXTURES / 'native_basic_folder_pt_resaved.ptx'


class NativeBasicFolderTests(unittest.TestCase):
    @unittest.skipUnless(RESAVED.is_file(), 'Local Pro Tools-resaved Basic Folder fixture absent.')
    def test_pro_tools_resaved_folder_and_audio_preserved_in_api_round_trips(self):
        original = RESAVED.read_bytes()
        session = ProToolsSession(RESAVED)
        expected = session.get_timeline_clips(False)
        self.assertEqual(session.get_tracks(), ['A_OUTSIDE', 'B_INSIDE'])
        self.assertEqual([clip['start_samples'] for clip in expected],
                         [1729728000, 1730208480])
        self.assertEqual([clip['length_samples'] for clip in expected], [1355354, 1355354])
        self.assertEqual([clip['src_offset_samples'] for clip in expected], [0, 0])
        mirrors = session._root_blocks(0x2519)[0].get_all_blocks(0x251a)
        folders = [bytes(b.items[0]) for b in mirrors if b.items[0][0] == 0x0b]
        self.assertEqual(len(folders), 2)
        with tempfile.TemporaryDirectory() as directory:
            for i in range(2):
                output = Path(directory) / ('resaved_noop_%d.ptx' % i)
                session.save(output)
                self.assertEqual(output.read_bytes(), original)
                session = ProToolsSession(output)
                self.assertEqual(session.get_timeline_clips(False), expected)
                actual_folders = [bytes(b.items[0]) for b in
                                  session._root_blocks(0x2519)[0].get_all_blocks(0x251a)
                                  if b.items[0][0] == 0x0b]
                self.assertEqual(actual_folders, folders)

    @unittest.skipUnless(BEFORE.is_file() and AFTER.is_file(), 'Local native Basic Folder pair absent.')
    def test_basic_folder_mirrors_do_not_add_an_audio_playlist_or_change_clips(self):
        before = ProToolsSession(BEFORE)
        after = ProToolsSession(AFTER)
        expected_tracks = ['A_OUTSIDE', 'B_INSIDE']
        self.assertEqual(before.get_tracks(), expected_tracks)
        self.assertEqual(after.get_tracks(), expected_tracks)
        self.assertEqual(after.sample_rate, 48000)
        self.assertEqual(after.frame_rate_enum, 0x09)
        first = before.get_timeline_clips(False)
        last = after.get_timeline_clips(False)
        self.assertEqual(first, last)
        self.assertEqual(len(last), 2)
        self.assertEqual([clip['track'] for clip in last], expected_tracks)
        self.assertEqual([clip['start_samples'] for clip in last],
                         [1729728000, 1730208480])
        self.assertEqual([clip['length_samples'] for clip in last], [1355354, 1355354])
        self.assertEqual([clip['src_offset_samples'] for clip in last], [0, 0])
        self.assertEqual([clip['physical_filename'] for clip in last], ['MEDIA.wav', 'MEDIA.wav'])
        for session, folder_count in ((before, 0), (after, 2)):
            root = session._root_blocks(0x1054)[0]
            self.assertEqual(struct.unpack_from('<I', root.items[0])[0], 2)
            self.assertEqual(len(session._validated_main_playlists()), 2)
            mirrors = session._root_blocks(0x2519)[0].get_all_blocks(0x251a)
            folders = [b for b in mirrors if b.items[0][0] == 0x0b]
            self.assertEqual(len(folders), folder_count)
            for folder in folders:
                raw = folder.items[0]
                n = struct.unpack_from('<I', raw, 2)[0]
                self.assertEqual(bytes(raw[6:6+n]), b'FOLDER_TEST')
        self.assertEqual(
            [bytes(b.items[0]) for b in before._root_blocks(0x262a)[0].get_all_blocks(0x2628)],
            [bytes(b.items[0]) for b in after._root_blocks(0x262a)[0].get_all_blocks(0x2628)],
        )

    @unittest.skipUnless(BEFORE.is_file() and AFTER.is_file(), 'Local native Basic Folder pair absent.')
    def test_basic_folder_pair_two_byte_identical_api_round_trips(self):
        for path in (BEFORE, AFTER):
            original = path.read_bytes()
            session = ProToolsSession(path)
            timeline = session.get_timeline_clips(False)
            with tempfile.TemporaryDirectory() as directory:
                for i in range(2):
                    output = Path(directory) / ('noop_%d.ptx' % i)
                    session.save(output)
                    self.assertEqual(output.read_bytes(), original)
                    session = ProToolsSession(output)
                    self.assertEqual(session.get_tracks(), ['A_OUTSIDE', 'B_INSIDE'])
                    self.assertEqual(session.get_timeline_clips(False), timeline)
            self.assertEqual(path.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
