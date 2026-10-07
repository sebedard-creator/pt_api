"""Strict named mono routing writer, using codes already present in this session."""
import copy
from pathlib import Path
import struct
import tempfile
import unittest

from pt_api import ProToolsSession
from tests.test_anonymous_mono_playlists import snapshot
from tests.test_track_outputs import path_record, route_payload, routes, routing_session


FIXTURES = Path(__file__).parent / 'fixtures'
BEFORE = FIXTURES / 'native_track_output_mono_write_before.ptx'
AFTER = FIXTURES / 'native_track_output_mono_write_after.ptx'
RESAVED = FIXTURES / 'native_track_output_mono_write_pt_resaved.ptx'


def writer_session(names=('MONO A', 'MONO B'), anonymous=False):
    session = routing_session(names, anonymous)
    for index, route in enumerate(routes(session)):
        route.block_type = 0x09
        # Nonconsecutive codes: no base+index arithmetic or byte masking.
        route.items[0][0] = (0x91, 0xfb)[index % 2]
    return session


class TrackOutputWriteTests(unittest.TestCase):
    def test_growth_shrink_and_composed_calls_modify_only_the_target_payload(self):
        session = writer_session(('FIRST', 'SECOND', 'THIRD'))
        original = copy.deepcopy(session)
        target, donor, other = routes(session)
        before = snapshot(session)
        self.assertEqual(session.set_track_output('FIRST', 'BUS_B_LONG'), 1)
        routes(original)[0].items[0] = bytes(donor.items[0])
        self.assertEqual(snapshot(session), snapshot(original))
        self.assertNotEqual(snapshot(session), before)
        self.assertEqual(target.items[0][0], 0xfb)
        self.assertEqual(bytes(donor.items[0]), target.items[0])
        self.assertIsNot(target.items[0], donor.items[0])
        self.assertEqual(session.set_track_output('SECOND', 'BUS_A'), 1)
        self.assertEqual(donor.items[0], bytes(other.items[0]))
        self.assertEqual(session.get_track_outputs(), [('FIRST', ['BUS_B_LONG']),
                                                      ('SECOND', ['BUS_A']), ('THIRD', ['BUS_A'])])
        self.assertEqual(session.set_track_output('THIRD', 'BUS_B_LONG'), 1)
        self.assertEqual(session.get_track_outputs(), [('FIRST', ['BUS_B_LONG']),
                                                      ('SECOND', ['BUS_A']), ('THIRD', ['BUS_B_LONG'])])
        self.assertEqual(session._removed_offsets, [])

    def test_identical_assignment_is_a_noop(self):
        session = writer_session()
        before = snapshot(session)
        target = routes(session)[0]
        original_payload = target.items[0]
        self.assertEqual(session.set_track_output('MONO A', 'BUS_A'), 0)
        self.assertIs(target.items[0], original_payload)
        self.assertEqual(snapshot(session), before)

    def test_utf8_track_and_output_names_use_byte_lengths(self):
        session = writer_session(('PISTE É', 'PISTE Ω'))
        output = 'BUS_é_中文'
        identity = bytes(routes(session)[1].items[0][12:20])
        session._root_blocks(0x2603)[0].items[2] = path_record(output, identity)
        routes(session)[1].items[0] = route_payload(output, identity, 0xfb)
        self.assertEqual(session.set_track_output('PISTE É', output), 1)
        self.assertEqual(session.get_track_outputs(), [('PISTE É', [output]), ('PISTE Ω', [output])])
        self.assertEqual(struct.unpack_from('<I', routes(session)[0].items[0], 36)[0],
                         len(output.encode('utf-8')))

    def test_invalid_parameters_are_rejected_without_mutation(self):
        for position in (0, 1):
            for value in (None, 1, [], b'name', '', 'bad\x00name', '\ud800'):
                with self.subTest(position=position, value=repr(value)):
                    session = writer_session()
                    before = snapshot(session)
                    args = ['MONO A', 'BUS_B_LONG']
                    args[position] = value
                    with self.assertRaises((TypeError, ValueError)):
                        session.set_track_output(*args)
                    self.assertEqual(snapshot(session), before)

    def test_missing_track_unknown_output_and_uncalibrated_bus_are_rejected(self):
        for track, output, message in (('ABSENT', 'BUS_A', 'Track not found'),
                                       ('MONO A', 'ABSENT', 'missing or ambiguous'),
                                       ('MONO A', 'UNUSED', 'Inconsistent.*calibration')):
            with self.subTest(track=track, output=output):
                session = writer_session()
                catalog = session._root_blocks(0x2603)[0]
                catalog.items.append(path_record('UNUSED', struct.pack('<Q', 0x889900)))
                struct.pack_into('<I', catalog.items[0], 0, 3)
                before = snapshot(session)
                with self.assertRaisesRegex(ValueError, message):
                    session.set_track_output(track, output)
                self.assertEqual(snapshot(session), before)

    def test_inconsistent_donor_codes_and_descriptor_types_are_rejected(self):
        for variant in ('same_bus_different_code', 'same_code_different_bus', 'block_type'):
            with self.subTest(variant=variant):
                session = writer_session(('FIRST', 'SECOND', 'THIRD'))
                records = routes(session)
                if variant == 'same_bus_different_code':
                    records[2].items[0][0] = 0x55
                elif variant == 'same_code_different_bus':
                    records[1].items[0][0] = records[0].items[0][0]
                else:
                    records[2].block_type = 0x0a
                # Read-only code inspection is not code generation/validation.
                self.assertEqual(len(session.get_track_outputs()), 3)
                before = snapshot(session)
                with self.assertRaisesRegex(ValueError, 'Conflicting descriptors|Ambiguous code|block type'):
                    session.set_track_output('FIRST', 'BUS_B_LONG')
                self.assertEqual(snapshot(session), before)

    def test_late_profile_error_does_not_change_an_earlier_target(self):
        for variant in ('trailer', 'catalog', 'slot_identity'):
            with self.subTest(variant=variant):
                session = writer_session(('FIRST', 'SECOND', 'THIRD'))
                if variant == 'trailer':
                    routes(session)[2].items[0][-1] = 1
                elif variant == 'catalog':
                    session._root_blocks(0x2603)[0].items[2].items[0][-1] = 1
                else:
                    session._root_blocks(0x2624)[0].get_all_blocks(0x2619)[2].items[-1][8] ^= 1
                before = snapshot(session)
                with self.assertRaises(ValueError):
                    session.set_track_output('FIRST', 'BUS_B_LONG')
                self.assertEqual(snapshot(session), before)

    def test_verified_anonymous_read_profile_is_not_implicitly_writable(self):
        session = writer_session(anonymous=True)
        self.assertEqual(len(session.get_track_outputs()), 2)
        before = snapshot(session)
        with self.assertRaisesRegex(ValueError, 'Track name cannot be empty'):
            session.set_track_output('MONO A', 'BUS_B_LONG')
        self.assertEqual(snapshot(session), before)

    def test_current_donor_code_is_used_without_cached_offsets_or_calibration(self):
        for code in (0, 1, 0xff):
            with self.subTest(code=code):
                session = writer_session()
                donor = routes(session)[1]
                donor.items[0][0] = code
                expected = bytes(donor.items[0])
                self.assertEqual(session.set_track_output('MONO A', 'BUS_B_LONG'), 1)
                self.assertEqual(routes(session)[0].items[0], expected)
                before = snapshot(session)
                # The last BUS_A assignment was replaced. No hidden code cache.
                with self.assertRaisesRegex(ValueError, 'two distinct assigned buses'):
                    session.set_track_output('MONO A', 'BUS_A')
                self.assertEqual(snapshot(session), before)


@unittest.skipUnless(BEFORE.is_file() and AFTER.is_file(), 'Local native mono-routing write pair absent.')
class NativeTrackOutputWriteTests(unittest.TestCase):
    def test_native_reassignment_matches_exact_descriptor_and_only_changes_one_payload(self):
        session = ProToolsSession(BEFORE)
        native_after = ProToolsSession(AFTER)
        reference = copy.deepcopy(session)
        donor = bytes(routes(session)[1].items[0])
        self.assertEqual(len(routes(session)[0].items[0]), 75)
        self.assertEqual(session.set_track_output('A_OUTSIDE', 'API_ROUTE_A'), 1)
        routes(reference)[0].items[0] = donor
        self.assertEqual(snapshot(session), snapshot(reference))
        self.assertEqual(len(routes(session)[0].items[0]), 70)
        self.assertEqual([bytes(r.items[0]) for r in routes(session)],
                         [bytes(r.items[0]) for r in routes(native_after)])
        self.assertEqual(session.get_track_outputs(), native_after.get_track_outputs())
        self.assertEqual(session.get_timeline_clips(), native_after.get_timeline_clips())
        for kind in (0x2603, 0x1022):
            self.assertEqual(snapshot_root(session, kind), snapshot_root(native_after, kind))

    def test_native_shrinking_and_growing_writes_save_reload_and_two_identical_cycles(self):
        original = BEFORE.read_bytes()
        for track, bus in (('A_OUTSIDE', 'API_ROUTE_A'), ('B_INSIDE', 'API_ROUTE_B_LONG')):
            with self.subTest(track=track, bus=bus):
                session = ProToolsSession(BEFORE)
                rows = session.get_timeline_clips()
                self.assertEqual(session.set_track_output(track, bus), 1)
                outputs = session.get_track_outputs()
                with tempfile.TemporaryDirectory() as directory:
                    initial = Path(directory) / 'reassigned.ptx'
                    session.save(initial)
                    first = initial.read_bytes()
                    session = ProToolsSession(initial)
                    self.assertEqual(session.get_track_outputs(), outputs)
                    self.assertEqual(session.get_timeline_clips(), rows)
                    for index in range(2):
                        output = Path(directory) / ('reassigned_cycle_%d.ptx' % index)
                        session.save(output)
                        self.assertEqual(output.read_bytes(), first)
                        session = ProToolsSession(output)
                        self.assertEqual(session.get_track_outputs(), outputs)
                        self.assertEqual(session.get_timeline_clips(), rows)
                self.assertEqual(BEFORE.read_bytes(), original)

    def test_native_single_distinct_assigned_bus_cannot_calibrate_an_unused_target(self):
        session = ProToolsSession(AFTER)
        before = snapshot(session)
        with self.assertRaisesRegex(ValueError, 'two distinct assigned buses'):
            session.set_track_output('A_OUTSIDE', 'API_ROUTE_B_LONG')
        self.assertEqual(snapshot(session), before)
        self.assertEqual(session.set_track_output('A_OUTSIDE', 'API_ROUTE_A'), 0)
        self.assertEqual(snapshot(session), before)


@unittest.skipUnless(RESAVED.is_file() and AFTER.is_file(), 'Local Pro Tools-resaved mono-routing write fixture absent.')
class NativeTrackOutputWriteResavedTests(unittest.TestCase):
    def test_protools_resaved_reassignment_preserves_outputs_and_two_api_cycles(self):
        session = ProToolsSession(RESAVED)
        reference = ProToolsSession(AFTER)
        original = RESAVED.read_bytes()
        expected = [('A_OUTSIDE', ['API_ROUTE_A']), ('B_INSIDE', ['API_ROUTE_A'])]
        self.assertEqual(session.get_track_outputs(), expected)
        self.assertEqual(session.get_timeline_clips(), reference.get_timeline_clips())
        self.assertEqual([bytes(r.items[0]) for r in routes(session)],
                         [bytes(r.items[0]) for r in routes(reference)])
        for kind in (0x2603, 0x1022):
            self.assertEqual(snapshot_root(session, kind), snapshot_root(reference, kind))
        before = snapshot(session)
        self.assertEqual(session.set_track_output('A_OUTSIDE', 'API_ROUTE_A'), 0)
        with self.assertRaisesRegex(ValueError, 'two distinct assigned buses'):
            session.set_track_output('A_OUTSIDE', 'API_ROUTE_B_LONG')
        self.assertEqual(snapshot(session), before)
        with tempfile.TemporaryDirectory() as directory:
            for index in range(2):
                output = Path(directory) / ('routing_write_resaved_%d.ptx' % index)
                session.save(output)
                self.assertEqual(output.read_bytes(), original)
                session = ProToolsSession(output)
                self.assertEqual(session.get_track_outputs(), expected)
                self.assertEqual(session.get_timeline_clips(), reference.get_timeline_clips())
        self.assertEqual(RESAVED.read_bytes(), original)


def snapshot_root(session, kind):
    # Structural snapshot excludes source offsets (native save relocates them).
    shell = ProToolsSession.__new__(ProToolsSession)
    shell.root_items = session._root_blocks(kind)
    return snapshot(shell)


if __name__ == '__main__':
    unittest.main()
