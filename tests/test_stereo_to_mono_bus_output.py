"""Native width reduction: remove only the verified centered static pan."""
import copy
from pathlib import Path
import struct
import tempfile
import unittest

from pt_api import ProToolsSession, PTBlock
from tests.test_anonymous_mono_playlists import snapshot
from tests.test_get_tracks import block
from tests.test_track_outputs import routes
from tests.test_track_output_write import snapshot_root
from tests.test_stereo_bus_output import stereo_session, pan_containers, CENTER


FIXTURES = Path(__file__).parent / 'fixtures'
BEFORE = FIXTURES / 'native_track_output_stereo_to_mono_before.ptx'
AFTER = FIXTURES / 'native_track_output_stereo_to_mono_after.ptx'
RESAVED = FIXTURES / 'native_track_output_stereo_to_mono_pt_resaved.ptx'
REOPENED = FIXTURES / 'native_track_output_stereo_to_mono_pt_reopened.ptx'


def down_session():
    session = stereo_session()
    session.set_track_output('MONO A', 'STEREO_é')
    return session


def metadata(session):
    return (list(getattr(session, '_removed_offsets', [])),
            dict(getattr(session, '_removed_block_types', {})))


class StereoToMonoTests(unittest.TestCase):
    def test_unused_or_assigned_mono_destination_removes_only_centered_pan_and_route(self):
        for destination in ('BUS_A', 'BUS_B_LONG'):
            with self.subTest(destination=destination):
                session = down_session()
                reference = stereo_session()
                if destination == 'BUS_B_LONG':
                    # Existing-code reuse must not demand a catalog calibration.
                    routes(session)[1].items[0][0] = 0xfe
                    routes(reference)[1].items[0][0] = 0xfe
                    routes(reference)[0].items[0] = bytes(routes(reference)[1].items[0])
                self.assertEqual(session.set_track_output('MONO A', destination), 1)
                self.assertEqual(snapshot(session), snapshot(reference))
                self.assertEqual(metadata(session), ([], {}))  # unsaved lane had offset 0
                before = snapshot(session)
                self.assertEqual(session.set_track_output('MONO A', destination), 0)
                self.assertEqual(snapshot(session), before)

    def test_composed_down_up_calls_use_current_tree_and_restore_all_bytes(self):
        session = down_session()
        original = snapshot(session)
        self.assertEqual(session.set_track_output('MONO A', 'BUS_A'), 1)
        self.assertEqual(session.set_track_output('MONO A', 'STEREO_é'), 1)
        self.assertEqual(snapshot(session), original)
        self.assertEqual(metadata(session), ([], {}))
        self.assertEqual(pan_containers(session)[0].items[1].items[0], CENTER)

    def test_unknown_or_noncentered_pan_and_automation_are_never_discarded(self):
        for variant in ('value', 'timestamp', 'flags', 'short', 'lane_type', 'lane_kind',
                        'extra_payload', 'header', 'padding', 'second_pan', 'third', 'nested'):
            with self.subTest(variant=variant):
                session = down_session()
                pan, second = pan_containers(session)
                lane = pan.items[1]
                if variant in ('value', 'timestamp', 'flags'):
                    p = bytearray(lane.items[0]); p[{'value': 26, 'timestamp': 22, 'flags': 14}[variant]] ^= 1
                    lane.items[0] = p
                elif variant == 'short': lane.items[0] = lane.items[0][:-1]
                elif variant == 'lane_type': lane.block_type = 2
                elif variant == 'lane_kind': lane.content_type = 0x260b
                elif variant == 'extra_payload': lane.items.append(b'opaque')
                elif variant == 'header': pan.items[0] = b'\x02'
                elif variant == 'padding': pan.items[-1] = bytes(12) + b'\x01'
                elif variant == 'second_pan': second.items[0] = bytes(13) + b'\x01'
                else:
                    state = session._root_blocks(0x2624)[0].get_all_blocks(0x260d)[0]
                    item = block(2, 0x260c, [bytes(14)])
                    state.items.append(item if variant == 'third' else block(1, 0x200b, [item]))
                session._removed_offsets = [99]
                session._removed_block_types = {99: 0x2077}
                before, removed = snapshot(session), metadata(session)
                with self.assertRaisesRegex(ValueError, 'pan'):
                    session.set_track_output('MONO A', 'BUS_A')
                self.assertEqual(snapshot(session), before)
                self.assertEqual(metadata(session), removed)

    def test_existing_pan_offset_requires_a_current_unambiguous_pointer_table(self):
        session = down_session()
        pan_containers(session)[0].items[1].original_offset = 1234
        before, removed = snapshot(session), metadata(session)
        with self.assertRaisesRegex(ValueError, 'pan pointer table'):
            session.set_track_output('MONO A', 'BUS_A')
        self.assertEqual(snapshot(session), before)
        self.assertEqual(metadata(session), removed)


@unittest.skipUnless(BEFORE.is_file() and AFTER.is_file(), 'Local native stereo-to-mono pair absent.')
class NativeStereoToMonoTests(unittest.TestCase):
    def test_native_route_and_empty_pan_match_exactly_without_other_tree_changes(self):
        session = ProToolsSession(BEFORE)
        after = ProToolsSession(AFTER)
        reference = copy.deepcopy(session)
        old_pan = pan_containers(session)[0]
        old_lane = old_pan.items[1]
        old_offset = old_pan.original_offset
        routes(reference)[0].items[0] = bytes(routes(after)[0].items[0])
        pan_containers(reference)[0].items = [bytes(14)]
        self.assertEqual(session.set_track_output('A_OUTSIDE', 'API_ROUTE_B_LONG'), 1)
        self.assertEqual(snapshot(session), snapshot(reference))
        self.assertEqual(old_pan.original_offset, old_offset)
        self.assertEqual(metadata(session), ([old_lane.original_offset], {old_lane.original_offset: 0x260a}))
        self.assertEqual(session.get_track_outputs(), after.get_track_outputs())
        self.assertEqual(session.get_timeline_clips(), after.get_timeline_clips())
        for kind in (0x2603, 0x1022):
            self.assertEqual(snapshot_root(session, kind), snapshot_root(after, kind))

    def test_referenced_pan_lane_is_rejected_without_mutating_payloads_or_metadata(self):
        for variant in ('reference', 'truncated', 'duplicate', 'missing'):
            with self.subTest(variant=variant):
                session = ProToolsSession(BEFORE)
                table = session._root_blocks(2)[0]
                if variant == 'reference':
                    payload = bytearray(session._raw_0002_payload(table))
                    _, pointer = session._validate_0002_record_layout(bytes(payload))[0]
                    lane_offset = pan_containers(session)[0].items[1].original_offset
                    struct.pack_into('<I', payload, pointer, lane_offset)
                    table.items = [payload]
                elif variant == 'truncated': table.items = [bytes(3)]
                elif variant == 'duplicate': session.root_items.append(copy.deepcopy(table))
                else: session.root_items.remove(table)
                before, removed = snapshot(session), metadata(session)
                with self.assertRaises(ValueError):
                    session.set_track_output('A_OUTSIDE', 'API_ROUTE_B_LONG')
                self.assertEqual(snapshot(session), before)
                self.assertEqual(metadata(session), removed)

    def test_originals_and_authored_reduction_reload_and_cycle_identically(self):
        for path, mutate in ((BEFORE, False), (AFTER, False), (BEFORE, True)):
            with self.subTest(path=path.name, mutate=mutate):
                original = path.read_bytes()
                session = ProToolsSession(path)
                if mutate: session.set_track_output('A_OUTSIDE', 'API_ROUTE_B_LONG')
                outputs, timeline = session.get_track_outputs(), session.get_timeline_clips()
                with tempfile.TemporaryDirectory() as directory:
                    out = Path(directory) / 'cycle.ptx'
                    session.save(out)
                    data = out.read_bytes()
                    if not mutate: self.assertEqual(data, original)
                    for _ in range(2):
                        session = ProToolsSession(out)
                        self.assertEqual(session.get_track_outputs(), outputs)
                        self.assertEqual(session.get_timeline_clips(), timeline)
                        session.save(out)
                        self.assertEqual(out.read_bytes(), data)
                    self.assertEqual(metadata(session), ([], {}))
                self.assertEqual(path.read_bytes(), original)

    def test_native_down_up_composition_restores_entire_ptx_byte_for_byte(self):
        session = ProToolsSession(BEFORE)
        original = BEFORE.read_bytes()
        session.set_track_output('A_OUTSIDE', 'API_ROUTE_B_LONG')
        session.set_track_output('A_OUTSIDE', 'Out 2.0')
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / 'cycle.ptx'
            session.save(out)
            self.assertEqual(out.read_bytes(), original)
            self.assertEqual(ProToolsSession(out).get_track_outputs(), ProToolsSession(BEFORE).get_track_outputs())


@unittest.skipUnless(AFTER.is_file(), 'Local native reduction reference absent.')
class NativeStereoToMonoResavedTests(unittest.TestCase):
    @unittest.skipUnless(RESAVED.is_file(), 'Local Pro Tools-resaved reduction fixture absent.')
    def test_resaved_route_empty_pan_catalogs_and_geometry_survive_cycles_and_composition(self):
        self._check_native_save(RESAVED)

    @unittest.skipUnless(REOPENED.is_file(), 'Local Pro Tools-reopened reduction fixture absent.')
    def test_reopened_route_empty_pan_catalogs_and_geometry_survive_cycles_and_composition(self):
        self._check_native_save(REOPENED)

    def _check_native_save(self, path):
        session = ProToolsSession(path)
        native = ProToolsSession(AFTER)
        original = path.read_bytes()
        expected = [('A_OUTSIDE', ['API_ROUTE_B_LONG']), ('B_INSIDE', ['API_ROUTE_A'])]
        self.assertEqual(session.get_tracks(), ['A_OUTSIDE', 'B_INSIDE'])
        self.assertEqual(session.get_track_outputs(), expected)
        self.assertEqual(session.get_timeline_clips(), native.get_timeline_clips())
        self.assertEqual([bytes(b.items[0]) for b in routes(session)],
                         [bytes(b.items[0]) for b in routes(native)])
        for kind in (0x2603, 0x1022):
            self.assertEqual(snapshot_root(session, kind), snapshot_root(native, kind))
        for track_index in (0, 1):
            self.assertEqual([b.items for b in pan_containers(session, track_index)],
                             [[bytes(14)], [bytes(14)]])
        before = snapshot(session)
        self.assertEqual(session.set_track_output('A_OUTSIDE', 'API_ROUTE_B_LONG'), 0)
        self.assertEqual(snapshot(session), before)
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / 'cycle.ptx'
            for _ in range(2):
                session.save(out)
                self.assertEqual(out.read_bytes(), original)
                session = ProToolsSession(out)
                self.assertEqual(session.get_track_outputs(), expected)
                self.assertEqual(session.get_timeline_clips(), native.get_timeline_clips())
            session.set_track_output('A_OUTSIDE', 'Out 2.0')
            session.set_track_output('A_OUTSIDE', 'API_ROUTE_B_LONG')
            session.save(out)
            self.assertEqual(out.read_bytes(), original)
            self.assertEqual(metadata(session), ([], {}))
        self.assertEqual(path.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
