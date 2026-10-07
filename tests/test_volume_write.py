"""Atomic mono Volume replacement/merge; no stereo or opaque fader writes."""
import copy
import math
from pathlib import Path
import struct
import tempfile
import unittest

from pt_api import ProToolsSession
from tests.test_anonymous_mono_playlists import snapshot
from tests.test_get_tracks import playlist
from tests.test_volume_read import read_session, state, payload, BEFORE, AFTER, TARGET
from tests.test_stereo_to_mono_bus_output import metadata
from tests.test_stereo_to_stereo_bus_output import pointer_rows


PT_RESAVED = Path(__file__).parent / 'fixtures' / 'native_volume_mono_pt_resaved.ptx'


class VolumeWriteTests(unittest.TestCase):
    def test_replacement_sorts_points_and_changes_only_target_payload(self):
        s = read_session(('PISTE É', 'SECOND'))
        reference = copy.deepcopy(s)
        state(reference).items[4].items[0] = payload(((0, 0), (10, 60), (20, -30)))
        lane = state(s).items[4]
        lane.original_offset = 1234
        s._removed_offsets = [99]
        s._removed_block_types = {99: 0x2077}
        offsets, types = s._removed_offsets, s._removed_block_types
        self.assertEqual(s.set_volume_automation('PISTE É', [(20, -3), (0, 0), (10, 6)]), [(0, 3)])
        self.assertEqual(snapshot(s), snapshot(reference))
        self.assertIs(state(s).items[4], lane)
        self.assertEqual(lane.original_offset, 1234)
        self.assertIs(s._removed_offsets, offsets)
        self.assertIs(s._removed_block_types, types)
        self.assertEqual(metadata(s), ([99], {99: 0x2077}))

    def test_merge_preserves_existing_points_and_updates_only_equal_timestamps(self):
        s = read_session()
        state(s).items[4].items[0] = payload(((0, 0), (10, 60), (30, -30)))
        self.assertEqual(s.set_volume_automation('FIRST', [(20, 1.5), (10, 9)], replace=False), [(0, 4)])
        self.assertEqual(state(s).items[4].items[0], payload(((0, 0), (10, 90), (20, 15), (30, -30))))
        self.assertEqual(s.get_volume_automation('FIRST')[0]['node_count'], 4)

    def test_composed_calls_use_live_envelope_and_restore_all_original_tree_bytes(self):
        s = read_session()
        original = snapshot(s)
        s.set_volume_automation('FIRST', [(0, 0), (TARGET, 6)])
        s.set_volume_automation('FIRST', [(TARGET, 3), (TARGET + 100, -3)], replace=False)
        self.assertEqual([p['db'] for p in s.get_volume_automation('FIRST')[0]['nodes']], [0, 3, -3])
        s.set_volume_automation('FIRST', [(0, 0)])
        self.assertEqual(snapshot(s), original)
        self.assertEqual(metadata(s), ([], {}))

    def test_identical_replacement_or_empty_merge_preserves_payload_object(self):
        s = read_session()
        lane = state(s).items[4]
        old = lane.items[0]
        before = snapshot(s)
        for nodes, replace in (([(0, 0)], True), ([], False), ([(0, 0)], False)):
            with self.subTest(replace=replace, nodes=nodes):
                self.assertEqual(s.set_volume_automation('FIRST', nodes, replace=replace), [(0, 1)])
                self.assertIs(lane.items[0], old)
                self.assertEqual(snapshot(s), before)

    def test_invalid_parameters_never_change_any_lane(self):
        s = read_session()
        before, removed = snapshot(s), metadata(s)
        cases = [({'track_name': None}, TypeError), ({'track_name': ''}, ValueError),
                 ({'track_name': 'A\x00B'}, ValueError), ({'track_name': '\ud800'}, ValueError),
                 ({'track_name': 'MISSING'}, ValueError), ({'replace': 1}, TypeError),
                 ({'replace': None}, TypeError), ({'ordinal': True}, TypeError),
                 ({'ordinal': 0.0}, TypeError), ({'ordinal': -1}, ValueError),
                 ({'ordinal': 1}, ValueError), ({'nodes': None}, TypeError),
                 ({'nodes': 1}, TypeError), ({'nodes': []}, ValueError),
                 ({'nodes': '12'}, TypeError)]
        for overrides, error in cases:
            with self.subTest(overrides=repr(overrides)):
                args = {'track_name': 'FIRST', 'nodes': [(0, 0), (10, 6)]}
                args.update(overrides)
                with self.assertRaises(error): s.set_volume_automation(**args)
                self.assertEqual(snapshot(s), before)
                self.assertEqual(metadata(s), removed)

    def test_invalid_later_point_never_commits_the_earlier_good_point(self):
        invalid = [(None, TypeError), ((20,), TypeError), ((20, 1, 2), TypeError),
                   ('ab', TypeError), (b'ab', TypeError), ((True, 6), TypeError),
                   ((1.0, 6), TypeError), ((-1, 6), ValueError), ((0x100000000, 6), ValueError),
                   ((20, True), TypeError), ((20, object()), TypeError),
                   ((20, math.nan), ValueError), ((20, math.inf), ValueError),
                   ((20, -math.inf), ValueError), ((20, 1e308), ValueError),
                   ((20, 3276.8), ValueError), ((20, -3276.9), ValueError),
                   ((10, 3), ValueError)]
        for replace in (True, False):
            for point, error in invalid:
                with self.subTest(replace=replace, point=repr(point)):
                    s = read_session()
                    before = snapshot(s)
                    with self.assertRaises(error):
                        s.set_volume_automation('FIRST', [(10, 6), point], replace=replace)
                    self.assertEqual(snapshot(s), before)
                    self.assertEqual(metadata(s), ([], {}))

    def test_failing_iterator_propagates_its_error_without_partial_writes(self):
        s = read_session()
        before = snapshot(s)
        def source():
            yield 10, 6
            raise RuntimeError('source stopped')
        with self.assertRaisesRegex(RuntimeError, 'source stopped'):
            s.set_volume_automation('FIRST', source())
        self.assertEqual(snapshot(s), before)

    def test_malformed_existing_target_or_later_lane_is_never_discarded_by_replace(self):
        for index in (0, 1):
            for replace in (True, False):
                for variant in ('count', 'segments', 'flags', 'order'):
                    with self.subTest(index=index, replace=replace, variant=variant):
                        s = read_session()
                        lane = state(s, index).items[4]
                        lane.items[0] = payload(((0, 0), (100, 60)))
                        p = lane.items[0]
                        if variant == 'count': struct.pack_into('<I', p, 10, 99)
                        elif variant == 'segments': struct.pack_into('<I', p, 16, 9)
                        elif variant == 'flags': p[14] = 4
                        else: struct.pack_into('<I', p, 28, 0)
                        before = snapshot(s)
                        with self.assertRaises(ValueError):
                            s.set_volume_automation('FIRST', [(0, 0), (TARGET, 6)], replace=replace)
                        self.assertEqual(snapshot(s), before)

    def test_anonymous_stereo_or_unknown_state_is_refused_before_assignment(self):
        for variant in ('anonymous', 'stereo', 'state'):
            with self.subTest(variant=variant):
                s = read_session()
                if variant == 'anonymous': s._root_blocks(0x1054)[0].items[1] = playlist('')
                elif variant == 'stereo': s._root_blocks(0x2519)[0].get_all_blocks(0x251a)[0].items[0][0] = 1
                else: state(s).items[3] = b'\x02'
                before = snapshot(s)
                with self.assertRaises(ValueError): s.set_volume_automation('FIRST', [(0, 0)])
                self.assertEqual(snapshot(s), before)

    def test_uint32_int16_boundaries_and_python_deci_db_rounding(self):
        s = read_session()
        self.assertEqual(s.set_volume_automation('FIRST', [(0, -3276.8), (0xffffffff, 3276.7)]), [(0, 2)])
        self.assertEqual(state(s).items[4].items[0], payload(((0, -32768), (0xffffffff, 32767))))
        s.set_volume_automation('FIRST', [(0, '1.25'), (10, 1.35)])
        self.assertEqual(state(s).items[4].items[0], payload(((0, 12), (10, 14))))

    def test_matching_ordinal_targets_exact_named_track_not_another_lane(self):
        s = read_session(('FIRST', 'PISTE É'))
        original = bytes(state(s).items[4].items[0])
        self.assertEqual(s.set_volume_automation('PISTE É', iter([(0, 0), (TARGET, 6)]), ordinal=1), [(1, 2)])
        self.assertEqual(state(s).items[4].items[0], original)
        self.assertEqual(s.get_volume_automation('PISTE É')[0]['nodes'][-1]['db'], 6)
        self.assertEqual(state(s, 1).items[6].items[0], payload())
        self.assertEqual(state(s, 1).items[11].items[0], payload())


@unittest.skipUnless(BEFORE.is_file() and AFTER.is_file(), 'Local native mono Volume pair absent.')
class NativeVolumeWriteTests(unittest.TestCase):
    def test_replacement_and_merge_match_native_lane_without_opaque_fader_changes(self):
        for replace, incoming in ((True, [(0, 0), (TARGET, 6)]), (False, [(TARGET, 6)])):
            with self.subTest(replace=replace):
                s, native = ProToolsSession(BEFORE), ProToolsSession(AFTER)
                reference = copy.deepcopy(s)
                state(reference).items[4].items[0] = bytes(state(native).items[4].items[0])
                lane = state(s).items[4]
                offset = lane.original_offset
                opaque = bytes(state(s).items[0].items[0])
                self.assertEqual(s.set_volume_automation('A_OUTSIDE', incoming, replace=replace), [(0, 2)])
                self.assertEqual(snapshot(s), snapshot(reference))
                self.assertEqual(s.get_volume_automation(), native.get_volume_automation())
                self.assertIs(state(s).items[4], lane)
                self.assertEqual(lane.original_offset, offset)
                self.assertEqual(state(s).items[0].items[0], opaque)
                self.assertEqual(pointer_rows(s), pointer_rows(native))
                self.assertEqual(metadata(s), ([], {}))

    def test_authored_volume_roundtrips_twice_without_changing_other_outputs_or_clips(self):
        for path in (BEFORE, AFTER):
            with self.subTest(path=path.name):
                s = ProToolsSession(path)
                original = path.read_bytes()
                s.set_volume_automation('A_OUTSIDE', [(0, 0), (TARGET, 6)])
                expected = s.get_volume_automation()
                clips, outputs, pointers = s.get_timeline_clips(), s.get_track_outputs(), pointer_rows(s)
                with tempfile.TemporaryDirectory() as d:
                    out = Path(d) / 'cycle.ptx'
                    s.save(out)
                    data = out.read_bytes()
                    if path == AFTER: self.assertEqual(data, original)  # identical envelope is a no-op
                    for _ in range(2):
                        s = ProToolsSession(out)
                        self.assertEqual(s.get_volume_automation(), expected)
                        self.assertEqual(s.get_timeline_clips(), clips)
                        self.assertEqual(s.get_track_outputs(), outputs)
                        self.assertEqual(pointer_rows(s), pointers)
                        s.save(out)
                        self.assertEqual(out.read_bytes(), data)
                self.assertEqual(path.read_bytes(), original)

    def test_both_modes_write_identical_files_and_replacement_restores_the_entire_before(self):
        with tempfile.TemporaryDirectory() as d:
            files = []
            for replace, nodes in ((True, [(0, 0), (TARGET, 6)]), (False, [(TARGET, 6)])):
                s = ProToolsSession(BEFORE)
                s.set_volume_automation('A_OUTSIDE', nodes, replace=replace)
                out = Path(d) / ('replace.ptx' if replace else 'merge.ptx')
                s.save(out)
                files.append(out.read_bytes())
                s = ProToolsSession(out)
                s.set_volume_automation('A_OUTSIDE', [(0, 0)])
                s.save(out)
                self.assertEqual(out.read_bytes(), BEFORE.read_bytes())
            self.assertEqual(files[0], files[1])


@unittest.skipUnless(PT_RESAVED.is_file() and BEFORE.is_file() and AFTER.is_file(),
                     'Local Pro Tools-resaved mono Volume fixture absent.')
class NativeResavedVolumeWriteTests(unittest.TestCase):
    def test_pt_resaved_envelope_preserves_other_lanes_and_exact_api_compositions(self):
        original = PT_RESAVED.read_bytes()
        session = ProToolsSession(PT_RESAVED)
        before, after = ProToolsSession(BEFORE), ProToolsSession(AFTER)
        self.assertEqual(session.get_volume_automation(), after.get_volume_automation())
        self.assertEqual(session.get_track_outputs(), before.get_track_outputs())
        self.assertEqual(session.get_timeline_clips(), before.get_timeline_clips())
        self.assertEqual(pointer_rows(session), pointer_rows(after))
        for index in (0, 1):
            for item in (2, 6, 8, 9, 11):
                self.assertEqual(state(session, index).items[item].to_bytes()[0],
                                 state(before, index).items[item].to_bytes()[0])
            # Compare opaque bytes with the native after, without interpreting them.
            self.assertEqual(state(session, index).items[0].items[0],
                             state(after, index).items[0].items[0])
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / 'cycle.ptx'
            for _ in range(2):
                session.save(out)
                self.assertEqual(out.read_bytes(), original)
                session = ProToolsSession(out)
            for replace, nodes in ((True, [(0, 0), (TARGET, 6)]), (False, [(TARGET, 6)])):
                with self.subTest(replace=replace):
                    session = ProToolsSession(PT_RESAVED)
                    session.set_volume_automation('A_OUTSIDE', nodes, replace=replace)
                    session.save(out)
                    self.assertEqual(out.read_bytes(), original)
                    session.set_volume_automation('A_OUTSIDE', [(0, 0)])
                    session.set_volume_automation('A_OUTSIDE', [(TARGET, 6)], replace=False)
                    session.save(out)
                    self.assertEqual(out.read_bytes(), original)
        self.assertEqual(PT_RESAVED.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
