"""Read-only volume, grounded in a native named mono before/after pair."""
import copy
from pathlib import Path
import struct
import tempfile
import unittest

from pt_api import ProToolsSession
from tests.test_get_tracks import block, playlist
from tests.test_anonymous_mono_playlists import anonymous_session, snapshot
from tests.test_track_output_write import snapshot_root
from tests.test_stereo_to_stereo_bus_output import pointer_rows


FIXTURES = Path(__file__).parent / 'fixtures'
BEFORE = FIXTURES / 'native_volume_mono_before.ptx'
AFTER = FIXTURES / 'native_volume_mono_after.ptx'
TARGET = 1730208480


def payload(nodes=((0, 0),)):
    return bytearray(bytes.fromhex('01460100') + struct.pack('<I', 14 + len(nodes) * 6)
                     + bytes(2) + struct.pack('<I', len(nodes)) + b'\x02\x00'
                     + struct.pack('<I', max(0, len(nodes) - 1)) + bytes(2)
                     + b''.join(struct.pack('<Ih', *node) for node in nodes) + bytes(2))


def read_session(names=('FIRST', 'SECOND')):
    s = anonymous_session(names)
    s._root_blocks(0x1054)[0].items[1:] = [playlist(name) for name in names]
    for slot in s._root_blocks(0x2624)[0].get_all_blocks(0x261c):
        definition = slot.items[0]
        definition.block_type = 0x0d
        state = block(5, 0x260d, [block(0x0d, 0x1029, [b'opaque fader state']), b'\x01\x00',
                                block(9, 0x260e, [b'opaque routing']), b'\x01',
                                block(1, 0x260a, [payload()]), b'\x01',
                                block(1, 0x260a, [payload()]), b'\x00',
                                block(2, 0x260c, [bytes(14)]), block(2, 0x260c, [bytes(14)]),
                                b'\x01', block(1, 0x260a, [payload()]), bytes(8)])
        definition.items.append(state)
    s.data = object()  # Cached source bytes must not be consulted.
    return s


def state(s, index=0):
    return s._root_blocks(0x2624)[0].get_all_blocks(0x261c)[index].get_all_blocks(0x260d)[0]


class VolumeReadTests(unittest.TestCase):
    def test_fresh_result_exact_samples_db_timecodes_and_named_filter(self):
        for names in (('ONE',), ('FIRST', 'SECOND'), ('PISTE É', 'DEUX', 'THIRD')):
            with self.subTest(names=names):
                s = read_session(names)
                state(s).items[4].items[0] = payload(((0, 0), (TARGET, 60)))
                before = snapshot(s)
                rows = s.get_volume_automation()
                self.assertEqual([r['track'] for r in rows], list(names))
                self.assertEqual([r['ordinal'] for r in rows], list(range(len(names))))
                self.assertEqual(rows[0], {'track': names[0], 'ordinal': 0, 'ok': True, 'reason': '',
                    'node_count': 2, 'payload_len': 36,
                    'nodes': [{'sample': 0, 'timecode': '00:00:00:00', 'db': 0.0},
                              {'sample': TARGET, 'timecode': '10:00:10:00', 'db': 6.0}]})
                self.assertEqual(s.get_volume_automation(names[-1]), [rows[-1]])
                rows[0]['nodes'][0]['db'] = 99
                rows[0]['track'] = 'not real'
                self.assertEqual(s.get_volume_automation(names[0])[0]['nodes'][0]['db'], 0.0)
                self.assertEqual(snapshot(s), before)

    def test_bad_filters_and_absent_track_raise_without_mutation(self):
        s = read_session()
        before = snapshot(s)
        for value, error in ((False, TypeError), (1, TypeError), ('', ValueError),
                             ('A\x00B', ValueError), ('\ud800', ValueError), ('MISSING', ValueError)):
            with self.subTest(value=repr(value)):
                with self.assertRaises(error): s.get_volume_automation(value)
                self.assertEqual(snapshot(s), before)

    def test_empty_session_returns_no_lanes_but_named_request_fails(self):
        s = ProToolsSession.__new__(ProToolsSession)
        s.root_items = []
        s.sample_rate, s.frame_rate_enum = 48000, 9
        self.assertEqual(s.get_volume_automation(), [])
        with self.assertRaisesRegex(ValueError, 'Track not found'): s.get_volume_automation('MISSING')

    def test_live_tree_values_are_reread_without_cached_offsets(self):
        s = read_session()
        self.assertEqual(s.get_volume_automation('FIRST')[0]['nodes'][0]['db'], 0)
        state(s).items[4].items[0] = payload(((0, -30), (100, 15), (200, 60)))
        self.assertEqual([n['db'] for n in s.get_volume_automation('FIRST')[0]['nodes']], [-3, 1.5, 6])
        self.assertEqual(s.get_volume_automation('FIRST')[0]['payload_len'], 42)

    def test_unknown_state_geometry_selectors_ownership_or_types_are_refused(self):
        for variant in ('short', 'extra', 'order', 'selector', 'slot_type', 'definition_type',
                        'state_type', 'lane_type', 'duplicate_state', 'nested_state', 'nested_slot'):
            with self.subTest(variant=variant):
                s = read_session()
                st = state(s)
                slot = s._root_blocks(0x2624)[0].items[1]
                if variant == 'short': st.items.pop()
                elif variant == 'extra': st.items.append(b'opaque')
                elif variant == 'order': st.items[4], st.items[6] = st.items[6], st.items[4]; st.items[4].content_type = 0x260b
                elif variant == 'selector': st.items[3] = b'\x02'
                elif variant == 'slot_type': slot.block_type = 3
                elif variant == 'definition_type': slot.items[0].block_type = 3
                elif variant == 'state_type': st.block_type = 2
                elif variant == 'lane_type': st.items[4].block_type = 2
                elif variant == 'duplicate_state': slot.items[0].items.append(copy.deepcopy(st))
                elif variant == 'nested_state': slot.items[0].items.append(block(1, 0x200b, [copy.deepcopy(st)]))
                else: s._root_blocks(0x2624)[0].items.append(block(1, 0x200b, [copy.deepcopy(slot)]))
                before = snapshot(s)
                with self.assertRaises(ValueError): s.get_volume_automation()
                self.assertEqual(snapshot(s), before)

    def test_anonymous_stereo_or_contradictory_identities_are_not_guessed(self):
        for variant in ('anonymous', 'stereo', 'identity', 'name', 'duplicate_root'):
            with self.subTest(variant=variant):
                s = read_session()
                if variant == 'anonymous': s._root_blocks(0x1054)[0].items[1] = playlist('')
                elif variant == 'stereo': s._root_blocks(0x2519)[0].get_all_blocks(0x251a)[0].items[0][0] = 1
                elif variant == 'identity': s._root_blocks(0x2107)[0].get_all_blocks(0x210b)[0].items[0][-24] ^= 1
                elif variant == 'name': s._root_blocks(0x1054)[0].items[1] = playlist('WRONG')
                else: s.root_items.append(copy.deepcopy(s._root_blocks(0x2624)[0]))
                before = snapshot(s)
                with self.assertRaises(ValueError): s.get_volume_automation()
                self.assertEqual(snapshot(s), before)

    def test_invalid_envelopes_never_return_partial_nodes(self):
        for variant in ('magic', 'short', 'size', 'padding', 'terminator', 'count', 'zero',
                        'flags', 'reserved', 'segments', 'duplicate', 'reversed', 'extra_item', 'not_raw'):
            with self.subTest(variant=variant):
                s = read_session()
                lane = state(s).items[4]
                lane.items[0] = payload(((0, 0), (100, 60)))
                p = lane.items[0]
                if variant == 'magic': p[0] ^= 1
                elif variant == 'short': lane.items[0] = p[:20]
                elif variant == 'size': struct.pack_into('<I', p, 4, 999)
                elif variant == 'padding': p[8] = 1
                elif variant == 'terminator': p[-1] = 1
                elif variant == 'count': struct.pack_into('<I', p, 10, 0xffffffff)
                elif variant == 'zero': lane.items[0] = payload(())
                elif variant == 'flags': p[14] = 4
                elif variant == 'reserved': p[20] = 1
                elif variant == 'segments': struct.pack_into('<I', p, 16, 3)
                elif variant == 'duplicate': struct.pack_into('<I', p, 28, 0)
                elif variant == 'reversed': struct.pack_into('<I', p, 22, 200)
                elif variant == 'extra_item': lane.items.append(b'opaque')
                else: lane.items[0] = block(1, 0x200b, [])
                before = snapshot(s)
                with self.assertRaises(ValueError): s.get_volume_automation()
                self.assertEqual(snapshot(s), before)

    def test_named_filter_still_validates_a_later_track_before_returning(self):
        s = read_session()
        state(s).items[4].items[0] = payload(((0, 0), (TARGET, 60)))
        state(s, 1).items[4].items[0][0] ^= 1
        before = snapshot(s)
        with self.assertRaises(ValueError): s.get_volume_automation('FIRST')
        self.assertEqual(snapshot(s), before)

    def test_sibling_and_nested_pan_lanes_are_not_mistaken_for_volume(self):
        s = read_session()
        st = state(s)
        st.items[6].items = [b'other automation: opaque']
        st.items[11].items = [payload(((123, -123),))]
        st.items[8].items = [b'\x01', block(1, 0x260a, [payload(((456, 100),))]), bytes(13)]
        before = snapshot(s)
        self.assertEqual(s.get_volume_automation('FIRST')[0]['nodes'],
                         [{'sample': 0, 'timecode': '00:00:00:00', 'db': 0}])
        self.assertEqual(snapshot(s), before)


@unittest.skipUnless(BEFORE.is_file() and AFTER.is_file(), 'Local native mono volume pair absent.')
class NativeVolumeReadTests(unittest.TestCase):
    def test_native_points_and_only_the_first_direct_lane_is_volume(self):
        a, b = ProToolsSession(BEFORE), ProToolsSession(AFTER)
        original = snapshot(b)
        rows = b.get_volume_automation()
        self.assertEqual([r['track'] for r in rows], ['A_OUTSIDE', 'B_INSIDE'])
        self.assertEqual(rows[0]['nodes'], [{'sample': 0, 'timecode': '00:00:00:00', 'db': 0},
                                          {'sample': TARGET, 'timecode': '10:00:10:00', 'db': 6}])
        self.assertEqual(rows[0]['node_count'], 2)
        self.assertEqual(rows[0]['payload_len'], 36)
        self.assertEqual(rows[1]['nodes'], a.get_volume_automation('B_INSIDE')[0]['nodes'])
        for index in (6, 11): self.assertEqual(state(a).items[index].items, state(b).items[index].items)
        self.assertEqual(snapshot(b), original)
        self.assertEqual(pointer_rows(a), pointer_rows(b))
        self.assertEqual(a.get_timeline_clips(), b.get_timeline_clips())
        self.assertEqual(a.get_track_outputs(), b.get_track_outputs())
        for kind in (0x2603, 0x1022): self.assertEqual(snapshot_root(a, kind), snapshot_root(b, kind))

    def test_reading_and_two_saves_preserve_both_native_files_byte_for_byte(self):
        for path in (BEFORE, AFTER):
            with self.subTest(path=path.name):
                s = ProToolsSession(path)
                original, expected = path.read_bytes(), s.get_volume_automation()
                with tempfile.TemporaryDirectory() as d:
                    out = Path(d) / 'cycle.ptx'
                    for _ in range(2):
                        s.save(out)
                        self.assertEqual(out.read_bytes(), original)
                        s = ProToolsSession(out)
                        self.assertEqual(s.get_volume_automation(), expected)
                self.assertEqual(path.read_bytes(), original)

    def test_historical_node_writer_matches_the_native_envelope_not_unknown_fader_fields(self):
        s, native = ProToolsSession(BEFORE), ProToolsSession(AFTER)
        reference = copy.deepcopy(s)
        state(reference).items[4].items[0] = bytes(state(native).items[4].items[0])
        self.assertEqual(s.add_volume_node('A_OUTSIDE', 10, 0, 10, 0, 6), 2)
        self.assertEqual(snapshot(s), snapshot(reference))
        self.assertEqual(s.get_volume_automation(), native.get_volume_automation())
        # A native save also changes 0x1029 offsets +1/+87. Their interpretation
        # is not inferred from this pair, and the historical writer leaves them opaque.
        self.assertNotEqual(state(s).items[0].items, state(native).items[0].items)


if __name__ == '__main__':
    unittest.main()
