"""Unused mono-bus code calibration, with independent native before/after bytes."""
import copy
from pathlib import Path
import struct
import tempfile
import unittest

from pt_api import ProToolsSession
from tests.test_anonymous_mono_playlists import snapshot
from tests.test_get_tracks import block
from tests.test_track_outputs import path_record, routes
from tests.test_track_output_write import writer_session, snapshot_root


FIXTURES = Path(__file__).parent / 'fixtures'
BEFORE = FIXTURES / 'native_track_output_unused_mono_before.ptx'
AFTER = FIXTURES / 'native_track_output_unused_mono_after.ptx'
ADR_RESAVED = FIXTURES / 'native_track_output_unused_mono_adr_pt_resaved.ptx'
LONG_RESAVED = FIXTURES / 'native_track_output_unused_mono_long_pt_resaved.ptx'


def opaque_path(name):
    encoded = name.encode('utf-8')
    return block(0x0e, 0x2602, [bytearray(b'\x02\x06' + struct.pack('<I', len(encoded))
                                        + encoded + bytes(52))])


def calibrated_session(base=7, gap=0, names=('MONO A', 'MONO B'), output='UNUSED_é'):
    session = writer_session(names)
    catalog = session._root_blocks(0x2603)[0]
    # Opaque multichannel paths between the two anchors must count too.
    catalog.items[2:2] = [opaque_path('OPAQUE_%d' % i) for i in range(gap)]
    target = path_record(output, struct.pack('<Q', 0x887700))
    catalog.items.append(target)
    struct.pack_into('<I', catalog.items[0], 0, 3 + gap)
    for i, route in enumerate(routes(session)):
        route.items[0][0] = base + (0 if i % 2 == 0 else 1 + gap)
    return session


class UnusedMonoBusTests(unittest.TestCase):
    def test_calibration_counts_opaque_paths_and_changes_only_the_target(self):
        for gap in (0, 1, 4):
            with self.subTest(gap=gap):
                session = calibrated_session(gap=gap, names=('PISTE É', 'SECOND', 'THIRD'))
                expected = copy.deepcopy(session)
                donor = bytes(routes(session)[0].items[0])
                encoded = 'UNUSED_é'.encode('utf-8')
                payload = (bytes((7 + gap + 2,)) + donor[1:12] + struct.pack('<Q', 0x887700)
                           + donor[20:36] + struct.pack('<I', len(encoded)) + encoded + donor[-19:])
                routes(expected)[0].items[0] = payload
                self.assertEqual(session.set_track_output('PISTE É', 'UNUSED_é'), 1)
                self.assertEqual(snapshot(session), snapshot(expected))
                self.assertEqual(session.get_track_outputs(), [('PISTE É', ['UNUSED_é']),
                                                              ('SECOND', ['BUS_B_LONG']), ('THIRD', ['BUS_A'])])
                self.assertEqual(session.set_track_output('PISTE É', 'UNUSED_é'), 0)

    def test_composed_calls_recalibrate_from_current_tree_without_code_cache(self):
        session = calibrated_session()
        before = snapshot(session)
        self.assertEqual(session.set_track_output('MONO A', 'UNUSED_é'), 1)
        self.assertEqual(session.set_track_output('MONO A', 'BUS_A'), 1)
        self.assertEqual(snapshot(session), before)
        # Erase one distinct anchor. Two tracks with the same bus are not two anchors.
        self.assertEqual(session.set_track_output('MONO A', 'BUS_B_LONG'), 1)
        before = snapshot(session)
        with self.assertRaisesRegex(ValueError, 'two distinct assigned buses'):
            session.set_track_output('MONO A', 'UNUSED_é')
        self.assertEqual(snapshot(session), before)

    def test_inconsistent_and_negative_calibrations_are_rejected_without_mutation(self):
        for variant in ('inconsistent', 'negative'):
            with self.subTest(variant=variant):
                session = calibrated_session()
                if variant == 'inconsistent':
                    routes(session)[1].items[0][0] += 1
                else:
                    catalog = session._root_blocks(0x2603)[0]
                    catalog.items[1:1] = [opaque_path('PREFIX_%d' % i) for i in range(3)]
                    struct.pack_into('<I', catalog.items[0], 0, 6)
                    routes(session)[0].items[0][0] = 0
                    routes(session)[1].items[0][0] = 1
                before = snapshot(session)
                with self.assertRaisesRegex(ValueError, 'Inconsistent or negative'):
                    session.set_track_output('MONO A', 'UNUSED_é')
                self.assertEqual(snapshot(session), before)

    def test_uint8_boundary_is_exact_and_overflow_never_wraps(self):
        session = calibrated_session(base=250, gap=3)
        self.assertEqual(session.set_track_output('MONO A', 'UNUSED_é'), 1)
        self.assertEqual(routes(session)[0].items[0][0], 255)
        session = calibrated_session(base=250, gap=4)
        before = snapshot(session)
        with self.assertRaisesRegex(ValueError, 'outside UInt8 range'):
            session.set_track_output('MONO A', 'UNUSED_é')
        self.assertEqual(snapshot(session), before)

    def test_target_must_be_unique_autonomous_mono_bus_with_unique_identity(self):
        for variant in ('physical', 'stereo', 'subchannel', 'zero_id', 'trailer', 'duplicate', 'alias'):
            with self.subTest(variant=variant):
                session = calibrated_session()
                catalog = session._root_blocks(0x2603)[0]
                target = catalog.items[-1]
                p = target.items[0]
                tail = 6 + struct.unpack_from('<I', p, 2)[0]
                if variant == 'physical': p[tail + 11] = 1
                elif variant == 'stereo': p[1] = 1
                elif variant == 'subchannel': p[tail + 6:tail + 8] = bytes(2)
                elif variant == 'zero_id': p[tail + 18:tail + 26] = bytes(8)
                elif variant == 'trailer': p[-1] = 1
                elif variant == 'duplicate': catalog.items.append(copy.deepcopy(target))
                else: catalog.items.append(path_record('ALIAS', struct.pack('<Q', 0x887700)))
                struct.pack_into('<I', catalog.items[0], 0, len(catalog.items) - 1)
                before = snapshot(session)
                with self.assertRaises(ValueError):
                    session.set_track_output('MONO A', 'UNUSED_é')
                self.assertEqual(snapshot(session), before)

    def test_unused_writer_requires_verified_live_catalog_block_types(self):
        for variant in ('root', 'entry'):
            with self.subTest(variant=variant):
                session = calibrated_session()
                catalog = session._root_blocks(0x2603)[0]
                if variant == 'root': catalog.block_type = 3
                else: catalog.items[-1].block_type = 1
                before = snapshot(session)
                with self.assertRaisesRegex(ValueError, 'catalog block types'):
                    session.set_track_output('MONO A', 'UNUSED_é')
                self.assertEqual(snapshot(session), before)

    def test_all_anchors_must_agree_not_just_the_first_two(self):
        session = calibrated_session(names=('FIRST', 'SECOND', 'THIRD'))
        self.assertEqual(session.set_track_output('THIRD', 'UNUSED_é'), 1)
        catalog = session._root_blocks(0x2603)[0]
        catalog.items.append(path_record('ANOTHER', struct.pack('<Q', 0x887701)))
        struct.pack_into('<I', catalog.items[0], 0, 4)
        routes(session)[2].items[0] = bytearray(routes(session)[2].items[0])
        routes(session)[2].items[0][0] += 2
        before = snapshot(session)
        with self.assertRaisesRegex(ValueError, 'Inconsistent or negative'):
            session.set_track_output('FIRST', 'ANOTHER')
        self.assertEqual(snapshot(session), before)


@unittest.skipUnless(BEFORE.is_file() and AFTER.is_file(), 'Local unused mono-bus native pair absent.')
class NativeUnusedMonoBusTests(unittest.TestCase):
    def test_native_code_identity_name_and_descriptor_match_exactly(self):
        session = ProToolsSession(BEFORE)
        native = ProToolsSession(AFTER)
        expected = copy.deepcopy(session)
        self.assertEqual(session.set_track_output('A_OUTSIDE', 'ADR'), 1)
        routes(expected)[0].items[0] = bytes(routes(native)[0].items[0])
        self.assertEqual(snapshot(session), snapshot(expected))
        self.assertEqual(routes(session)[0].items[0][0], 0x19)
        self.assertEqual(len(routes(session)[0].items[0]), 62)
        self.assertEqual(session.get_track_outputs(), [('A_OUTSIDE', ['ADR']), ('B_INSIDE', ['API_ROUTE_A'])])
        self.assertEqual(session.get_timeline_clips(), native.get_timeline_clips())
        for kind in (0x2603, 0x1022):
            self.assertEqual(snapshot_root(session, kind), snapshot_root(native, kind))

    def test_native_unused_assignment_and_return_long_save_reload_are_exact(self):
        session = ProToolsSession(BEFORE)
        original = BEFORE.read_bytes()
        rows = session.get_timeline_clips()
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(session.set_track_output('A_OUTSIDE', 'ADR'), 1)
            short = Path(directory) / 'unused_adr.ptx'
            session.save(short)
            reference = short.read_bytes()
            session = ProToolsSession(short)
            for index in range(2):
                output = Path(directory) / ('unused_cycle_%d.ptx' % index)
                session.save(output)
                self.assertEqual(output.read_bytes(), reference)
                session = ProToolsSession(output)
                self.assertEqual(session.get_timeline_clips(), rows)
                self.assertEqual(session.get_track_outputs(), [('A_OUTSIDE', ['ADR']), ('B_INSIDE', ['API_ROUTE_A'])])
            # B_LONG is now unused, and the remaining ADR/A anchors still agree.
            self.assertEqual(session.set_track_output('A_OUTSIDE', 'API_ROUTE_B_LONG'), 1)
            long = Path(directory) / 'return_long.ptx'
            session.save(long)
            self.assertEqual(long.read_bytes(), original)
            session = ProToolsSession(long)
            self.assertEqual(session.get_timeline_clips(), rows)
            self.assertEqual(session.get_track_outputs(), [('A_OUTSIDE', ['API_ROUTE_B_LONG']), ('B_INSIDE', ['API_ROUTE_A'])])
        self.assertEqual(BEFORE.read_bytes(), original)

    def test_native_pair_two_noop_cycles_remain_byte_identical(self):
        for path in (BEFORE, AFTER):
            session = ProToolsSession(path)
            original = path.read_bytes()
            with tempfile.TemporaryDirectory() as directory:
                for index in range(2):
                    output = Path(directory) / ('unused_noop_%d.ptx' % index)
                    session.save(output)
                    self.assertEqual(output.read_bytes(), original)
                    session = ProToolsSession(output)
            self.assertEqual(path.read_bytes(), original)


class NativeUnusedMonoBusResavedTests(unittest.TestCase):
    def check_resaved(self, path, reference_path, output_name):
        session = ProToolsSession(path)
        reference = ProToolsSession(reference_path)
        original = path.read_bytes()
        expected = [('A_OUTSIDE', [output_name]), ('B_INSIDE', ['API_ROUTE_A'])]
        self.assertEqual(session.get_track_outputs(), expected)
        self.assertEqual(session.get_timeline_clips(), reference.get_timeline_clips())
        self.assertEqual([bytes(r.items[0]) for r in routes(session)],
                         [bytes(r.items[0]) for r in routes(reference)])
        for kind in (0x2603, 0x1022):
            self.assertEqual(snapshot_root(session, kind), snapshot_root(reference, kind))
        before = snapshot(session)
        self.assertEqual(session.set_track_output('A_OUTSIDE', output_name), 0)
        self.assertEqual(snapshot(session), before)
        with tempfile.TemporaryDirectory() as directory:
            for index in range(2):
                output = Path(directory) / ('unused_resaved_%d.ptx' % index)
                session.save(output)
                self.assertEqual(output.read_bytes(), original)
                session = ProToolsSession(output)
                self.assertEqual(session.get_track_outputs(), expected)
                self.assertEqual(session.get_timeline_clips(), reference.get_timeline_clips())
            # Recalibration also uses the resaved live tree, not historical bytes.
            other = 'API_ROUTE_B_LONG' if output_name == 'ADR' else 'ADR'
            self.assertEqual(session.set_track_output('A_OUTSIDE', other), 1)
            self.assertEqual(session.set_track_output('A_OUTSIDE', output_name), 1)
            output = Path(directory) / 'unused_return_resaved.ptx'
            session.save(output)
            self.assertEqual(output.read_bytes(), original)
        self.assertEqual(path.read_bytes(), original)

    @unittest.skipUnless(ADR_RESAVED.is_file() and AFTER.is_file(), 'Local Pro Tools-resaved ADR output absent.')
    def test_protools_resaved_unused_adr_assignment_is_exact(self):
        self.check_resaved(ADR_RESAVED, AFTER, 'ADR')

    @unittest.skipUnless(LONG_RESAVED.is_file() and BEFORE.is_file(), 'Local Pro Tools-resaved long output absent.')
    def test_protools_resaved_return_long_assignment_is_exact(self):
        self.check_resaved(LONG_RESAVED, BEFORE, 'API_ROUTE_B_LONG')


if __name__ == '__main__':
    unittest.main()
