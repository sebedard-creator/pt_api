"""Atomic mono/stereo Volume writing on the corroborated native profile."""
import copy
from pathlib import Path
import struct
import tempfile
import unittest

from pt_api import ProToolsSession
from tests.test_anonymous_mono_playlists import snapshot
from tests.test_get_tracks import block
from tests.test_volume_read import state, payload, TARGET
from tests.test_volume_stereo_read import mixed_session, BEFORE, AFTER
from tests.test_stereo_to_mono_bus_output import metadata
from tests.test_stereo_to_stereo_bus_output import pointer_rows


PT_RESAVED = Path(__file__).parent / 'fixtures' / 'native_volume_stereo_pt_resaved.ptx'


class StereoVolumeWriteTests(unittest.TestCase):
    def test_only_target_volume_payload_changes_preserving_lane_offset_and_metadata(self):
        s, names = mixed_session()
        s._removed_offsets = [99]
        s._removed_block_types = {99: 0x2077}
        offsets, types = s._removed_offsets, s._removed_block_types
        lane = state(s, 1).items[4]
        lane.original_offset = 1234
        reference = copy.deepcopy(s)
        state(reference, 1).items[4].items[0] = payload(((0, 0), (TARGET, 60)))
        self.assertEqual(s.set_volume_automation(names[1], [(TARGET, 6), (0, 0)], ordinal=1), [(1, 2)])
        self.assertEqual(snapshot(s), snapshot(reference))
        self.assertIs(state(s, 1).items[4], lane)
        self.assertEqual(lane.original_offset, 1234)
        self.assertIs(s._removed_offsets, offsets)
        self.assertIs(s._removed_block_types, types)

    def test_merge_keeps_other_points_and_updates_exact_timestamp(self):
        s, names = mixed_session()
        state(s, 1).items[4].items[0] = payload(((0, 0), (10, 60), (30, -30)))
        self.assertEqual(s.set_volume_automation(names[1], [(20, 1.5), (10, 9)], replace=False), [(1, 4)])
        self.assertEqual(state(s, 1).items[4].items[0], payload(((0, 0), (10, 90), (20, 15), (30, -30))))

    def test_stereo_positions_and_adjacent_stereos_compose_without_cross_track_changes(self):
        for widths in ((2,), (2, 1), (1, 2), (1, 2, 1), (2, 2), (1, 2, 2, 1)):
            with self.subTest(widths=widths):
                s, names = mixed_session(widths)
                original = snapshot(s)
                for index, name in enumerate(names):
                    self.assertEqual(s.set_volume_automation(name, [(0, index), (TARGET, 6)], ordinal=index),
                                     [(index, 2)])
                for index, row in enumerate(s.get_volume_automation()):
                    self.assertEqual(row['ordinal'], index)
                    self.assertEqual(row['nodes'][0]['db'], index)
                for name in names: s.set_volume_automation(name, [(0, 0)])
                self.assertEqual(snapshot(s), original)

    def test_mono_after_stereo_uses_track_ordinal_not_channel_playlist_index(self):
        s, names = mixed_session()
        original = snapshot(s)
        with self.assertRaisesRegex(ValueError, 'ordinal does not match'):
            s.set_volume_automation(names[2], [(TARGET, 6)], ordinal=3)
        self.assertEqual(snapshot(s), original)
        reference = copy.deepcopy(s)
        state(reference, 2).items[4].items[0] = payload(((TARGET, 60),))
        self.assertEqual(s.set_volume_automation(names[2], [(TARGET, 6)], ordinal=2), [(2, 1)])
        self.assertEqual(snapshot(s), snapshot(reference))

    def test_identical_replacement_and_empty_merge_preserve_payload_object(self):
        s, names = mixed_session()
        lane = state(s, 1).items[4]
        old = lane.items[0]
        for nodes, replace in (([(0, 0)], True), ([], False), ([(0, 0)], False)):
            self.assertEqual(s.set_volume_automation(names[1], nodes, replace=replace), [(1, 1)])
            self.assertIs(lane.items[0], old)

    def test_late_bad_point_or_interrupted_generator_never_writes_early_points(self):
        s, names = mixed_session()
        original = snapshot(s)
        for bad, error in (((TARGET, 9), ValueError), ((-1, 6), ValueError),
                           ((TARGET + 100, float('nan')), ValueError), ((True, 6), TypeError),
                           ((TARGET + 100, 3276.8), ValueError), ((1.0, 6), TypeError)):
            for replace in (True, False):
                with self.assertRaises(error):
                    s.set_volume_automation(names[1], [(TARGET, 6), bad], replace=replace)
                self.assertEqual(snapshot(s), original)
        def interrupted():
            yield (TARGET, 6)
            raise RuntimeError('iterator interrupted')
        with self.assertRaisesRegex(RuntimeError, 'iterator interrupted'):
            s.set_volume_automation(names[1], interrupted())
        self.assertEqual(snapshot(s), original)

    def test_invalid_later_track_envelope_is_not_ignored_in_replacement_or_merge(self):
        for target in (0, 1):
            for replace in (True, False):
                s, names = mixed_session()
                state(s, 2).items[4].items[0][10] = 2  # Count exceeds this payload.
                original = snapshot(s)
                with self.assertRaises(ValueError):
                    s.set_volume_automation(names[target], [(0, 0), (TARGET, 6)], replace=replace)
                self.assertEqual(snapshot(s), original)

    def test_identity_ordinal_width_and_geometry_errors_precede_mutation(self):
        for variant in ('identity', 'ordinal', 'width', 'geometry'):
            s, names = mixed_session()
            slot = s._root_blocks(0x2624)[0].get_all_blocks(0x261c)[2]
            if variant == 'identity': slot.get_all_blocks(0x2619)[0].items[-1][8] ^= 1
            elif variant == 'ordinal': struct.pack_into('<I', slot.items[-1], 0, 3)
            elif variant == 'width':
                p = s._root_blocks(0x2519)[0].get_all_blocks(0x251a)[1].items[0]
                p[6 + struct.unpack_from('<I', p, 2)[0]] = 3
            else: state(s, 2).items.pop()
            original = snapshot(s)
            with self.assertRaises(ValueError): s.set_volume_automation(names[1], [(0, 0), (TARGET, 6)])
            self.assertEqual(snapshot(s), original)

    def test_sibling_automation_and_nested_left_right_pan_are_not_written(self):
        s, names = mixed_session()
        st = state(s, 1)
        st.items[6].items[0] = b'opaque non-Volume automation'
        st.items[11].items[0] = payload(((123, -123),))
        for index, value in ((8, -100), (9, 100)):
            st.items[index].items = [b'\x01', block(1, 0x260a, [payload(((0, value),))]), bytes(13)]
        reference = copy.deepcopy(s)
        state(reference, 1).items[4].items[0] = payload(((0, 0), (TARGET, 60)))
        s.set_volume_automation(names[1], [(0, 0), (TARGET, 6)])
        self.assertEqual(snapshot(s), snapshot(reference))

    def test_stereo_writer_retains_uint32_int16_bounds_and_rounding(self):
        s, names = mixed_session()
        self.assertEqual(s.set_volume_automation(names[1], [(0, -3276.8), (0xffffffff, 3276.7)]), [(1, 2)])
        self.assertEqual(state(s, 1).items[4].items[0], payload(((0, -32768), (0xffffffff, 32767))))
        s.set_volume_automation(names[1], [(0, '1.25'), (10, 1.35)])
        self.assertEqual(state(s, 1).items[4].items[0], payload(((0, 12), (10, 14))))


@unittest.skipUnless(BEFORE.is_file() and AFTER.is_file(), 'Local native stereo Volume pair absent.')
class NativeStereoVolumeWriteTests(unittest.TestCase):
    def test_both_modes_match_native_volume_preserving_all_opaque_fields(self):
        for replace, nodes in ((True, [(0, 0), (TARGET, 6)]), (False, [(TARGET, 6)])):
            s, native = ProToolsSession(BEFORE), ProToolsSession(AFTER)
            reference = copy.deepcopy(s)
            state(reference, 1).items[4].items[0] = bytes(state(native, 1).items[4].items[0])
            lane = state(s, 1).items[4]
            offset = lane.original_offset
            self.assertEqual(s.set_volume_automation('C_STEREO', nodes, replace=replace, ordinal=1), [(1, 2)])
            self.assertEqual(snapshot(s), snapshot(reference))
            self.assertEqual(s.get_volume_automation(), native.get_volume_automation())
            self.assertEqual(s.get_timeline_clips(), native.get_timeline_clips())
            self.assertEqual(s.get_clips(), native.get_clips())
            self.assertEqual(pointer_rows(s), pointer_rows(native))
            self.assertIs(state(s, 1).items[4], lane)
            self.assertEqual(lane.original_offset, offset)
            self.assertEqual(metadata(s), ([], {}))

    def test_modes_write_identical_files_roundtrip_twice_and_restore_entire_before(self):
        with tempfile.TemporaryDirectory() as directory:
            files = []
            for replace, nodes in ((True, [(0, 0), (TARGET, 6)]), (False, [(TARGET, 6)])):
                s = ProToolsSession(BEFORE)
                s.set_volume_automation('C_STEREO', nodes, replace=replace)
                expected = s.get_volume_automation()
                clips, pointers = s.get_timeline_clips(), pointer_rows(s)
                out = Path(directory) / 'cycle.ptx'
                s.save(out)
                data = out.read_bytes()
                files.append(data)
                for _ in range(2):
                    s = ProToolsSession(out)
                    self.assertEqual(s.get_volume_automation(), expected)
                    self.assertEqual(s.get_timeline_clips(), clips)
                    self.assertEqual(pointer_rows(s), pointers)
                    s.save(out)
                    self.assertEqual(out.read_bytes(), data)
                s.set_volume_automation('C_STEREO', [(0, 0)])
                s.save(out)
                self.assertEqual(out.read_bytes(), BEFORE.read_bytes())
            self.assertEqual(files[0], files[1])

    def test_live_compositions_across_mono_stereo_mono_restore_original_file(self):
        s = ProToolsSession(BEFORE)
        for index, name in enumerate(('A_OUTSIDE', 'C_STEREO', 'B_INSIDE')):
            self.assertEqual(s.set_volume_automation(name, [(TARGET + index, index + 1)], replace=False,
                                                   ordinal=index), [(index, 2)])
        for name in ('A_OUTSIDE', 'C_STEREO', 'B_INSIDE'):
            s.set_volume_automation(name, [(0, 0)])
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / 'restored.ptx'
            s.save(out)
            self.assertEqual(out.read_bytes(), BEFORE.read_bytes())


@unittest.skipUnless(PT_RESAVED.is_file() and BEFORE.is_file() and AFTER.is_file(),
                     'Local Pro Tools-resaved stereo Volume fixture absent.')
class NativeResavedStereoVolumeWriteTests(unittest.TestCase):
    def test_native_resave_preserves_state_and_exact_cycles_and_compositions(self):
        original = PT_RESAVED.read_bytes()
        s = ProToolsSession(PT_RESAVED)
        before, after = ProToolsSession(BEFORE), ProToolsSession(AFTER)
        expected = after.get_volume_automation()
        self.assertEqual(s.get_volume_automation(), expected)
        self.assertEqual(s.get_clips(), before.get_clips())
        self.assertEqual(s.get_timeline_clips(), before.get_timeline_clips())
        self.assertEqual(pointer_rows(s), pointer_rows(after))
        for index in range(3):
            for item in (2, 6, 8, 9, 11):
                self.assertEqual(state(s, index).items[item].to_bytes()[0],
                                 state(before, index).items[item].to_bytes()[0])
            # Native opaque-field bytes are compared, not interpreted as a cache.
            self.assertEqual(state(s, index).items[0].items[0],
                             state(after, index).items[0].items[0])
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / 'cycle.ptx'
            for _ in range(2):
                s.save(out)
                self.assertEqual(out.read_bytes(), original)
                s = ProToolsSession(out)
                self.assertEqual(s.get_volume_automation(), expected)
            for replace, nodes in ((True, [(0, 0), (TARGET, 6)]), (False, [(TARGET, 6)])):
                with self.subTest(replace=replace):
                    s = ProToolsSession(PT_RESAVED)
                    self.assertEqual(s.set_volume_automation('C_STEREO', nodes, replace=replace,
                                                           ordinal=1), [(1, 2)])
                    s.save(out)
                    self.assertEqual(out.read_bytes(), original)
                    s.set_volume_automation('C_STEREO', [(0, 0)])
                    s.set_volume_automation('C_STEREO', [(TARGET, 6)], replace=False)
                    for index, name in ((0, 'A_OUTSIDE'), (2, 'B_INSIDE')):
                        s.set_volume_automation(name, [(TARGET + index, -3)], replace=False,
                                                ordinal=index)
                        s.set_volume_automation(name, [(0, 0)], ordinal=index)
                    s.save(out)
                    self.assertEqual(out.read_bytes(), original)
        self.assertEqual(PT_RESAVED.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
