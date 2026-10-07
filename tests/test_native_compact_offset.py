"""Optional native head-trim pair; only the UInt8 reader is extended."""
from pathlib import Path
import struct
import tempfile
import unittest

from pt_api import ProToolsSession

FIXTURES = Path(__file__).parent / 'fixtures'
BEFORE = FIXTURES / 'native_source_offset_uint8_before.ptx'
AFTER = FIXTURES / 'native_source_offset_uint8_after.ptx'
RESAVED = FIXTURES / 'native_source_offset_uint8_pt_resaved.ptx'


class NativeCompactOffsetTests(unittest.TestCase):
    @unittest.skipUnless(RESAVED.is_file(), 'Local Pro Tools-resaved UInt8 offset fixture absent.')
    def test_pro_tools_resaved_geometry_and_two_identical_api_round_trips(self):
        original = RESAVED.read_bytes()
        session = ProToolsSession(RESAVED)
        expected = session.get_timeline_clips(False)
        self.assertEqual(session.get_tracks(), ['Audio 1'])
        self.assertEqual(len(expected), 1)
        clip = expected[0]
        self.assertEqual(clip['start_samples'], 1729728100)
        self.assertEqual(clip['end_samples'], 1738408672)
        self.assertEqual(clip['length_samples'], 8680572)
        self.assertEqual(clip['src_offset_samples'], 100)
        status = session.get_relink_write_status('Audio 1', clip['clip_name'], 1729728100)
        self.assertFalse(status['supported'])
        self.assertEqual(status['code'], 'unsupported_clip_layout')
        with tempfile.TemporaryDirectory() as directory:
            for i in range(2):
                output = Path(directory) / ('resaved_noop_%d.ptx' % i)
                session.save(output)
                self.assertEqual(output.read_bytes(), original)
                session = ProToolsSession(output)
                self.assertEqual(session.get_timeline_clips(False), expected)

    @unittest.skipUnless(BEFORE.is_file() and AFTER.is_file(), 'Local native trim pair absent.')
    def test_native_head_trim_exact_geometry_and_unchanged_parent(self):
        before = ProToolsSession(BEFORE)
        after = ProToolsSession(AFTER)
        original = AFTER.read_bytes()
        first = before.get_timeline_clips(False)[0]
        last = after.get_timeline_clips(False)[0]
        self.assertEqual(after.sample_rate, 48000)
        self.assertEqual(after.frame_rate_enum, 0x09)
        self.assertEqual(after.get_tracks(), ['Audio 1'])
        self.assertEqual(last['start_samples'], 1729728100)
        self.assertEqual(last['end_samples'], 1738408672)
        self.assertEqual(last['length_samples'], 8680572)
        self.assertEqual(last['src_offset_samples'], 100)
        self.assertEqual(last['start_samples'] - first['start_samples'], 100)
        self.assertEqual(first['length_samples'] - last['length_samples'], 100)
        self.assertEqual(first['end_samples'], last['end_samples'])
        self.assertEqual(first['physical_filename'], last['physical_filename'])
        before_defs = before._root_blocks(0x262a)[0].get_all_blocks(0x2628)
        after_defs = after._root_blocks(0x262a)[0].get_all_blocks(0x2628)
        self.assertEqual(bytes(before_defs[0].items[0]), bytes(after_defs[0].items[0]))
        raw = after_defs[1].items[0]
        a = 4 + struct.unpack_from('<I', raw)[0]
        self.assertEqual(bytes(raw[a:a+5]), b'\x01\x10\x30\x44\x08')
        self.assertEqual(raw[a+5], 100)
        self.assertEqual(int.from_bytes(raw[a+6:a+9], 'little'), 8680572)
        self.assertEqual(struct.unpack_from('<II', raw, a+9), (1729728100, 1729728100))
        status = after.get_relink_write_status('Audio 1', last['clip_name'], 1729728100)
        self.assertFalse(status['supported'])
        self.assertEqual(status['code'], 'unsupported_clip_layout')
        self.assertEqual(AFTER.read_bytes(), original)

    @unittest.skipUnless(BEFORE.is_file() and AFTER.is_file(), 'Local native trim pair absent.')
    def test_native_pair_two_byte_identical_round_trips(self):
        for path in (BEFORE, AFTER):
            original = path.read_bytes()
            session = ProToolsSession(path)
            expected = session.get_timeline_clips(False)
            expected_clips = session.get_clips()
            with tempfile.TemporaryDirectory() as directory:
                for i in range(2):
                    output = Path(directory) / ('noop_%d.ptx' % i)
                    session.save(output)
                    self.assertEqual(output.read_bytes(), original)
                    session = ProToolsSession(output)
                    self.assertEqual(session.get_timeline_clips(False), expected)
                    self.assertEqual(session.get_clips(), expected_clips)


if __name__ == '__main__':
    unittest.main()
