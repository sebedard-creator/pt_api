"""Read one Volume entry per native Audio track, not per L/R playlist."""
import copy
from pathlib import Path
import struct
import tempfile
import unittest

from pt_api import ProToolsSession
from tests.test_anonymous_mono_playlists import snapshot
from tests.test_get_tracks import playlist
from tests.test_volume_read import read_session, state, payload, TARGET
from tests.test_stereo_to_stereo_bus_output import pointer_rows

FIXTURES = Path(__file__).parent / 'fixtures'
BEFORE = FIXTURES / 'native_volume_stereo_before.ptx'
AFTER = FIXTURES / 'native_volume_stereo_after.ptx'


def mixed_session(widths=(1, 2, 1)):
    """Synthetic corroborated mirrors; not an oracle for unknown PTX variants."""
    names = tuple('PISTE É %d' % i for i in range(len(widths)))
    s = read_session(names)
    descriptors = s._root_blocks(0x1015)[0].get_all_blocks(0x1014)
    mirrors = s._root_blocks(0x2519)[0].get_all_blocks(0x251a)
    slots = s._root_blocks(0x2624)[0].get_all_blocks(0x261c)
    cursor = 0
    for index, (name, width, descriptor, slot) in enumerate(zip(names, widths, descriptors, slots)):
        encoded = name.encode('utf-8')
        if width == 2:
            tail = bytearray(45)
            tail[:5] = b'\x01\x02\x00\x00\x00'
            struct.pack_into('<HH', tail, 5, cursor, cursor + 1)
            tail[13:17] = b'\x2a\x00\x00\x00'
            struct.pack_into('<II', tail, 32, cursor, cursor + 1)
            descriptor.items[0] = bytearray(struct.pack('<I', len(encoded)) + encoded + tail)
        else:
            tail_start = 4 + len(encoded)
            struct.pack_into('<I', descriptor.items[0], tail_start + 5, cursor)
            struct.pack_into('<I', descriptor.items[0], tail_start + 30, cursor)
        for offset in (index, index + len(names)):
            p = mirrors[offset].items[0]
            tail_start = 6 + len(encoded)
            p[tail_start] = width - 1
            p[tail_start + 40] = width - 1
        slot.items.append(bytearray(struct.pack('<IHH', index, 1, index) + b'\x00\x00\xff\xff'))
        cursor += width
    root = s._root_blocks(0x1054)[0]
    root.items = [bytearray(struct.pack('<I', cursor))] + [
        playlist(name) for name, width in zip(names, widths) for _ in range(width)]
    return s, names


class StereoVolumeReadTests(unittest.TestCase):
    def test_one_entry_per_track_for_stereo_first_middle_last_and_adjacent(self):
        for widths in ((2,), (2, 1), (1, 2), (1, 2, 1), (2, 2), (1, 2, 2, 1)):
            with self.subTest(widths=widths):
                s, names = mixed_session(widths)
                before = snapshot(s)
                rows = s.get_volume_automation()
                self.assertEqual([r['track'] for r in rows], list(names))
                self.assertEqual([r['ordinal'] for r in rows], list(range(len(names))))
                self.assertEqual(len(s._validated_main_playlists()), sum(widths))
                self.assertEqual(snapshot(s), before)

    def test_samples_filter_fresh_results_and_live_tree_do_not_shift_after_stereo(self):
        s, names = mixed_session()
        state(s, 1).items[4].items[0] = payload(((0, 0), (TARGET, 60)))
        state(s, 2).items[4].items[0] = payload(((0, -30), (TARGET + 100, 15)))
        self.assertIs(type(s.data), object)  # Cached source bytes are deliberately unavailable.
        rows = s.get_volume_automation()
        self.assertEqual(rows[1]['nodes'][1], {'sample': TARGET, 'timecode': '10:00:10:00', 'db': 6})
        self.assertEqual(rows[2]['ordinal'], 2)
        self.assertEqual(rows[2]['nodes'][-1]['sample'], TARGET + 100)
        self.assertEqual(s.get_volume_automation(names[2]), [rows[2]])
        rows[1]['nodes'][0]['db'] = 99
        self.assertEqual(s.get_volume_automation(names[1])[0]['nodes'][0]['db'], 0)
        state(s, 1).items[4].items[0] = payload(((0, -60),))
        self.assertEqual(s.get_volume_automation(names[1])[0]['nodes'][0]['db'], -6)

    def test_unknown_descriptor_width_indexes_marker_or_count_are_rejected(self):
        for variant in ('width', 'left', 'right', 'footer', 'marker', 'size', 'count'):
            s, _ = mixed_session()
            root = s._root_blocks(0x1015)[0]
            node = root.get_all_blocks(0x1014)[1]
            p = node.items[0]
            start = 4 + struct.unpack_from('<I', p)[0]
            if variant == 'width': p[start] = 3
            elif variant == 'left': struct.pack_into('<H', p, start + 5, 2)
            elif variant == 'right': struct.pack_into('<H', p, start + 7, 3)
            elif variant == 'footer': struct.pack_into('<I', p, start + 36, 99)
            elif variant == 'marker': p[start + 13] ^= 1
            elif variant == 'size': node.items[0] = p[:-1]
            else: struct.pack_into('<I', root.items[0], 0, 4)
            self.assert_refused_without_mutation(s)

    def test_both_width_mirrors_names_and_identities_must_agree(self):
        for family in (0, 1):
            for relative in (0, 6, 10, 18, 32, 40, 41):
                s, names = mixed_session()
                mirror = s._root_blocks(0x2519)[0].get_all_blocks(0x251a)[family * 3 + 1]
                mirror.items[0][6 + len(names[1].encode('utf-8')) + relative] ^= 1
                self.assert_refused_without_mutation(s)
        s, _ = mixed_session()
        s._root_blocks(0x2107)[0].get_all_blocks(0x210b)[1].items[0][-24] ^= 1
        self.assert_refused_without_mutation(s)

    def test_slot_ordinals_and_identities_are_required_not_just_counts(self):
        for variant in ('missing', 'duplicate', 'wrong', 'short_index', 'identity'):
            s, _ = mixed_session()
            slot = s._root_blocks(0x2624)[0].get_all_blocks(0x261c)[2]
            if variant == 'missing': slot.items.pop()
            elif variant == 'duplicate': slot.items.append(copy.deepcopy(slot.items[-1]))
            elif variant == 'wrong': struct.pack_into('<I', slot.items[-1], 0, 3)
            elif variant == 'short_index': struct.pack_into('<H', slot.items[-1], 6, 1)
            else: slot.get_all_blocks(0x2619)[0].items[-1][8] ^= 1
            self.assert_refused_without_mutation(s)

    def test_channel_playlist_names_order_and_anonymity_are_not_guessed(self):
        for variant in ('second_channel', 'order', 'count', 'anonymous'):
            s, names = mixed_session()
            root = s._root_blocks(0x1054)[0]
            if variant == 'second_channel': root.items[3] = playlist('WRONG')
            elif variant == 'order': root.items[3], root.items[4] = root.items[4], root.items[3]
            elif variant == 'count': root.items.pop(); struct.pack_into('<I', root.items[0], 0, 3)
            else: root.items[2] = playlist('')
            self.assert_refused_without_mutation(s)

    def test_later_invalid_volume_is_checked_even_with_stereo_filter(self):
        s, names = mixed_session()
        state(s, 1).items[6].items[0] = b'opaque non-Volume automation'
        state(s, 1).items[11].items[0] = payload(((123, -123),))
        self.assertEqual(s.get_volume_automation(names[1])[0]['node_count'], 1)
        state(s, 2).items[4].items[0][0] ^= 1
        before = snapshot(s)
        with self.assertRaises(ValueError): s.get_volume_automation(names[1])
        self.assertEqual(snapshot(s), before)

    def test_state_geometry_and_duplicate_roots_remain_strict(self):
        for variant in ('geometry', 'selector', 'lane_type', 'root'):
            s, _ = mixed_session()
            st = state(s, 1)
            if variant == 'geometry': st.items.pop()
            elif variant == 'selector': st.items[3] = b'\x02'
            elif variant == 'lane_type': st.items[4].block_type = 2
            else: s.root_items.append(copy.deepcopy(s._root_blocks(0x2624)[0]))
            self.assert_refused_without_mutation(s)

    def test_bulk_writer_refuses_unknown_stereo_width_in_both_modes(self):
        s, names = mixed_session()
        p = s._root_blocks(0x1015)[0].get_all_blocks(0x1014)[1].items[0]
        p[4 + struct.unpack_from('<I', p)[0]] = 3
        before = snapshot(s)
        for name in names:
            for replace in (True, False):
                with self.assertRaises(ValueError):
                    s.set_volume_automation(name, [(0, 0), (TARGET, 6)], replace=replace)
                self.assertEqual(snapshot(s), before)

    def test_other_readers_keep_their_existing_playlist_contract(self):
        s, names = mixed_session()
        self.assertEqual(s.get_tracks(), [names[0], names[1], names[1], names[2]])
        s.get_volume_automation()
        self.assertEqual(s.get_tracks(), [names[0], names[1], names[1], names[2]])
        self.assertEqual(s.get_timeline_clips(), [])

    def assert_refused_without_mutation(self, session):
        before = snapshot(session)
        with self.assertRaises(ValueError): session.get_volume_automation()
        self.assertEqual(snapshot(session), before)


@unittest.skipUnless(BEFORE.is_file() and AFTER.is_file(), 'Local native stereo Volume pair absent.')
class NativeStereoVolumeReadTests(unittest.TestCase):
    def test_native_point_changes_one_volume_lane_not_lr_pan_or_next_mono(self):
        before, after = ProToolsSession(BEFORE), ProToolsSession(AFTER)
        rows = after.get_volume_automation()
        self.assertEqual([r['track'] for r in rows], ['A_OUTSIDE', 'C_STEREO', 'B_INSIDE'])
        self.assertEqual([r['ordinal'] for r in rows], [0, 1, 2])
        self.assertEqual(rows[1]['nodes'], [{'sample': 0, 'timecode': '00:00:00:00', 'db': 0},
            {'sample': TARGET, 'timecode': '10:00:10:00', 'db': 6}])
        self.assertEqual(rows[0], before.get_volume_automation()[0])
        self.assertEqual(rows[2], before.get_volume_automation()[2])
        for index in range(3):
            for item in (2, 6, 8, 9, 11):
                self.assertEqual(state(before, index).items[item].to_bytes()[0],
                                 state(after, index).items[item].to_bytes()[0])
        self.assertEqual(before.get_timeline_clips(), after.get_timeline_clips())
        self.assertEqual(pointer_rows(before), pointer_rows(after))

    def test_both_native_originals_remain_exact_after_reading_and_two_save_cycles(self):
        for path in (BEFORE, AFTER):
            original = path.read_bytes()
            s = ProToolsSession(path)
            expected = s.get_volume_automation()
            with tempfile.TemporaryDirectory() as directory:
                out = Path(directory) / 'cycle.ptx'
                for _ in range(2):
                    s.save(out)
                    self.assertEqual(out.read_bytes(), original)
                    s = ProToolsSession(out)
                    self.assertEqual(s.get_volume_automation(), expected)
                    self.assertEqual(s.get_volume_automation('B_INSIDE'), [expected[2]])
            self.assertEqual(path.read_bytes(), original)

    def test_native_stereo_bulk_writer_refuses_a_corrupted_width_without_mutation(self):
        s = ProToolsSession(AFTER)
        p = s._root_blocks(0x2519)[0].get_all_blocks(0x251a)[1].items[0]
        p[6 + struct.unpack_from('<I', p, 2)[0]] = 0
        before = snapshot(s)
        for replace in (True, False):
            with self.assertRaises(ValueError):
                s.set_volume_automation('C_STEREO', [(0, 0), (TARGET, 6)], replace=replace)
            self.assertEqual(snapshot(s), before)


if __name__ == '__main__':
    unittest.main()
