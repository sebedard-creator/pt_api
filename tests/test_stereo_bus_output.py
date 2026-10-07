"""Mono Audio to autonomous stereo bus, with a native centered-pan oracle."""
import copy
from pathlib import Path
import struct
import tempfile
import unittest

from pt_api import ProToolsSession, PTBlock
from tests.test_anonymous_mono_playlists import snapshot
from tests.test_get_tracks import block
from tests.test_track_outputs import path_record, routes
from tests.test_track_output_write import snapshot_root
from tests.test_unused_mono_bus_output import calibrated_session


FIXTURES = Path(__file__).parent / 'fixtures'
BEFORE = FIXTURES / 'native_track_output_stereo_bus_before.ptx'
AFTER = FIXTURES / 'native_track_output_stereo_bus_after.ptx'
RESAVED = FIXTURES / 'native_track_output_stereo_bus_pt_resaved.ptx'
STEREO_TRAILER = bytes.fromhex('0101ffffffffffffffff00ffffffff000c0100')
CENTER = bytes.fromhex('014601001400000000000100000002000000000000000000000000000000')
STEREO_ID = struct.pack('<Q', 0x887700)


def stereo_path(name='STEREO_é', identity=STEREO_ID):
    name = name.encode('utf-8')
    tail = (struct.pack('<IHHHHHH', 2, 150, 151, 0xffff, 1, 0, 0)
            + bytes.fromhex('2a000000') + identity + bytes(16))
    return block(0x0e, 0x2602, [bytearray(b'\x02\x01' + struct.pack('<I', len(name)) + name + tail)])


def stereo_session(names=('MONO A', 'MONO B'), output='STEREO_é'):
    session = calibrated_session(names=names, output=output)
    session._root_blocks(0x2603)[0].items[-1] = stereo_path(output)
    for slot in session._root_blocks(0x2624)[0].get_all_blocks(0x261c):
        state = slot.items[0].get_all_blocks(0x260d)[0]
        state.items.extend([block(2, 0x260c, [bytes(14)]), block(2, 0x260c, [bytes(14)])])
    return session


def pan_containers(session, index=0):
    slot = session._root_blocks(0x2624)[0].get_all_blocks(0x261c)[index]
    return slot.get_all_blocks(0x260c)


class StereoBusOutputTests(unittest.TestCase):
    def test_mono_to_stereo_changes_only_route_and_first_pan_with_utf8_byte_length(self):
        session = stereo_session(names=('PISTE É', 'SECOND', 'THIRD'))
        reference = copy.deepcopy(session)
        current = bytes(routes(reference)[0].items[0])
        name = 'STEREO_é'.encode('utf-8')
        expected = (b'\x09' + current[1:12] + STEREO_ID + current[20:36]
                    + struct.pack('<I', len(name)) + name + STEREO_TRAILER)
        routes(reference)[0].items[0] = expected
        pan_containers(reference)[0].items = [b'\x01', block(1, 0x260a, [CENTER]), bytes(13)]
        self.assertEqual(session.set_track_output('PISTE É', 'STEREO_é'), 1)
        self.assertEqual(snapshot(session), snapshot(reference))
        self.assertEqual(session.get_track_outputs(), [('PISTE É', ['STEREO_é']),
                                                      ('SECOND', ['BUS_B_LONG']), ('THIRD', ['BUS_A'])])
        before = snapshot(session)
        self.assertEqual(session.set_track_output('PISTE É', 'STEREO_é'), 0)
        self.assertEqual(snapshot(session), before)
        self.assertEqual(session._removed_offsets, [])
        # Later calls calibrate from the current mixed mono/stereo assignments.
        catalog = session._root_blocks(0x2603)[0]
        catalog.items.append(stereo_path('ANOTHER', struct.pack('<Q', 0x887701)))
        struct.pack_into('<I', catalog.items[0], 0, 4)
        self.assertEqual(session.set_track_output('SECOND', 'ANOTHER'), 1)
        self.assertEqual(routes(session)[1].items[0][0], 10)
        self.assertEqual(session.get_track_outputs(), [('PISTE É', ['STEREO_é']),
                                                      ('SECOND', ['ANOTHER']), ('THIRD', ['BUS_A'])])

    def test_used_stereo_destination_reuses_actual_code_and_not_other_tracks_pan(self):
        session = stereo_session(names=('FIRST', 'SECOND', 'THIRD'))
        session.set_track_output('FIRST', 'STEREO_é')
        routes(session)[0].items[0] = bytes((0xfc,)) + bytes(routes(session)[0].items[0])[1:]
        # A donor's existing non-centered pan must never be copied to the target.
        pan_containers(session)[0].items[1].items[0] = CENTER[:26] + struct.pack('<h', -37) + CENTER[28:]
        donor_pan = snapshot_root(session, 0x2624)
        self.assertEqual(session.set_track_output('SECOND', 'STEREO_é'), 1)
        self.assertEqual(routes(session)[1].items[0], routes(session)[0].items[0])
        self.assertEqual(pan_containers(session, 1)[0].items[1].items[0], CENTER)
        self.assertNotEqual(snapshot_root(session, 0x2624), donor_pan)
        self.assertEqual(pan_containers(session)[0].items[1].items[0][26:28], struct.pack('<h', -37))

    def test_stereo_path_corruption_is_rejected_before_any_mutation(self):
        for variant in ('short', 'prefix', 'width', 'parent', 'physical', 'magic', 'null', 'tail'):
            with self.subTest(variant=variant):
                session = stereo_session()
                entry = session._root_blocks(0x2603)[0].items[-1]
                p = entry.items[0]
                end = 6 + struct.unpack_from('<I', p, 2)[0]
                if variant == 'short': p.pop()
                elif variant == 'prefix': p[1] = 6
                elif variant == 'width': p[end] = 3
                elif variant == 'parent': p[end + 8] = 1
                elif variant == 'physical': p[end + 13] = 1
                elif variant == 'magic': p[end + 16] ^= 1
                elif variant == 'null': p[end + 20:end + 28] = bytes(8)
                else: p[-1] = 1
                before = snapshot(session)
                with self.assertRaises(ValueError):
                    session.set_track_output('MONO A', 'STEREO_é')
                self.assertEqual(snapshot(session), before)

    def test_cross_width_identity_aliases_and_duplicate_names_are_rejected(self):
        for variant in ('alias', 'duplicate'):
            with self.subTest(variant=variant):
                session = stereo_session()
                catalog = session._root_blocks(0x2603)[0]
                catalog.items.append(path_record('MONO_ALIAS', STEREO_ID) if variant == 'alias'
                                     else stereo_path())
                struct.pack_into('<I', catalog.items[0], 0, 4)
                before = snapshot(session)
                with self.assertRaises(ValueError): session.set_track_output('MONO A', 'STEREO_é')
                self.assertEqual(snapshot(session), before)

    def test_width_mismatch_is_rejected_in_reading_without_partial_results(self):
        for variant in ('mono_trailer', 'stereo_trailer', 'third_width'):
            with self.subTest(variant=variant):
                session = stereo_session()
                session.set_track_output('MONO A', 'STEREO_é')
                if variant == 'mono_trailer':
                    routes(session)[0].items[0] = routes(session)[0].items[0][:-19] + ProToolsSession._BUS_OUTPUT_TRAILERS[1]
                elif variant == 'stereo_trailer':
                    routes(session)[1].items[0] = bytes(routes(session)[1].items[0][:-19]) + STEREO_TRAILER
                else:
                    routes(session)[0].items[0] = routes(session)[0].items[0][:-2] + b'\x02\x00'
                before = snapshot(session)
                with self.assertRaises(ValueError): session.get_track_outputs()
                self.assertEqual(snapshot(session), before)

    def test_unverified_pan_containers_are_rejected_before_route_replacement(self):
        for variant in ('missing', 'third', 'nested', 'type', 'opaque', 'nonempty', 'second'):
            with self.subTest(variant=variant):
                session = stereo_session()
                state = session._root_blocks(0x2624)[0].get_all_blocks(0x260d)[0]
                pans = pan_containers(session)
                if variant == 'missing': state.items.remove(pans[0])
                elif variant == 'third': state.items.append(copy.deepcopy(pans[0]))
                elif variant == 'nested': state.items.append(block(1, 0x200b, [copy.deepcopy(pans[0])]))
                elif variant == 'type': pans[0].block_type = 1
                elif variant == 'opaque': pans[0].items = [b'\x01' + bytes(13)]
                elif variant == 'nonempty': pans[0].items.append(block(1, 0x260a, [CENTER]))
                else: pans[1].items = [b'\x01' + bytes(13)]
                before = snapshot(session)
                with self.assertRaisesRegex(ValueError, 'pan containers'):
                    session.set_track_output('MONO A', 'STEREO_é')
                self.assertEqual(snapshot(session), before)

    def test_width_drop_with_noncentered_pan_is_refused_without_discarding_it(self):
        session = stereo_session()
        session.set_track_output('MONO A', 'STEREO_é')
        pan_containers(session)[0].items[1].items[0] = CENTER[:26] + b'\x01\x00' + CENTER[28:]
        before = snapshot(session)
        with self.assertRaisesRegex(ValueError, 'centered static pan'):
            session.set_track_output('MONO A', 'BUS_B_LONG')
        self.assertEqual(snapshot(session), before)

    def test_changing_existing_stereo_destination_rejects_unverified_pan(self):
        session = stereo_session(names=('FIRST', 'SECOND', 'THIRD'))
        session.set_track_output('FIRST', 'STEREO_é')
        other = stereo_path('OTHER_STEREO', struct.pack('<Q', 0x887701))
        catalog = session._root_blocks(0x2603)[0]
        catalog.items.append(other)
        struct.pack_into('<I', catalog.items[0], 0, 4)
        pan_containers(session)[0].items[1].items[0] = CENTER[:26] + b'\x01\x00' + CENTER[28:]
        before = snapshot(session)
        with self.assertRaisesRegex(ValueError, 'Stereo-to-stereo.*centered static pan'):
            session.set_track_output('FIRST', 'OTHER_STEREO')
        self.assertEqual(snapshot(session), before)


@unittest.skipUnless(BEFORE.is_file() and AFTER.is_file(), 'Local native mono-to-stereo pair absent.')
class NativeStereoBusTests(unittest.TestCase):
    def test_native_new_pan_has_no_pointer_and_standard_records_keep_their_targets(self):
        records = []
        def walk(items, path=()):
            for i, item in enumerate(items):
                if isinstance(item, PTBlock):
                    here = path + ((item.content_type, i),)
                    yield item.original_offset, here
                    yield from walk(item.items, here)
        for path in (BEFORE, AFTER):
            session = ProToolsSession(path)
            offsets = dict(walk(session._content_root_items()))
            payload = bytes(session._raw_0002_payload(session._root_blocks(2)[0]))
            rows = [(start, pos, offsets.get(struct.unpack_from('<I', payload, pos)[0]), payload[start:pos])
                    for start, pos in session._validate_0002_record_layout(payload)]
            records.append(rows)
            if path == AFTER:
                pan_lane = pan_containers(session)[0].items[1]
                self.assertNotIn(pan_lane.original_offset,
                                 [struct.unpack_from('<I', payload, pos)[0] for _, pos, _, _ in rows])
        self.assertEqual(records[0], records[1])

    def test_native_width_and_centered_pan_exactly_match_without_other_tree_changes(self):
        session = ProToolsSession(BEFORE)
        after = ProToolsSession(AFTER)
        reference = copy.deepcopy(session)
        routes(reference)[0].items[0] = bytes(routes(after)[0].items[0])
        pan_containers(reference)[0].items = copy.deepcopy(pan_containers(after)[0].items)
        self.assertEqual(session.set_track_output('A_OUTSIDE', 'Out 2.0'), 1)
        self.assertEqual(snapshot(session), snapshot(reference))
        self.assertEqual(session.get_track_outputs(), [('A_OUTSIDE', ['Out 2.0']), ('B_INSIDE', ['API_ROUTE_A'])])
        self.assertEqual(session.get_track_outputs(), after.get_track_outputs())
        self.assertEqual(session.get_timeline_clips(), after.get_timeline_clips())
        for kind in (0x2603, 0x1022):
            self.assertEqual(snapshot_root(session, kind), snapshot_root(after, kind))
        self.assertEqual(pan_containers(session)[0].items[1].items[0], CENTER)

    def test_native_originals_and_authored_output_roundtrip_byte_identically(self):
        for path, mutate in ((BEFORE, False), (AFTER, False), (BEFORE, True)):
            with self.subTest(path=path.name, mutate=mutate):
                session = ProToolsSession(path)
                original = path.read_bytes()
                if mutate: session.set_track_output('A_OUTSIDE', 'Out 2.0')
                expected_outputs = session.get_track_outputs()
                rows = session.get_timeline_clips()
                with tempfile.TemporaryDirectory() as directory:
                    output = Path(directory) / 'cycle.ptx'
                    session.save(output)
                    data = output.read_bytes()
                    if not mutate: self.assertEqual(data, original)
                    for _ in range(2):
                        session = ProToolsSession(output)
                        self.assertEqual(session.get_track_outputs(), expected_outputs)
                        self.assertEqual(session.get_timeline_clips(), rows)
                        session.save(output)
                        self.assertEqual(output.read_bytes(), data)
                self.assertEqual(path.read_bytes(), original)


@unittest.skipUnless(RESAVED.is_file() and AFTER.is_file(), 'Local Pro Tools-resaved stereo bus fixture absent.')
class NativeStereoBusResavedTests(unittest.TestCase):
    def test_resaved_routing_pan_and_catalogs_remain_exact_and_roundtrip_identically(self):
        session = ProToolsSession(RESAVED)
        native = ProToolsSession(AFTER)
        original = RESAVED.read_bytes()
        outputs = [('A_OUTSIDE', ['Out 2.0']), ('B_INSIDE', ['API_ROUTE_A'])]
        self.assertEqual(session.get_track_outputs(), outputs)
        self.assertEqual([bytes(b.items[0]) for b in routes(session)],
                         [bytes(b.items[0]) for b in routes(native)])
        self.assertEqual(session.get_timeline_clips(), native.get_timeline_clips())
        self.assertEqual(pan_containers(session)[0].items[1].items[0], CENTER)
        self.assertEqual(pan_containers(session)[1].to_bytes()[0],
                         pan_containers(native)[1].to_bytes()[0])
        for kind in (0x2603, 0x1022):
            self.assertEqual(snapshot_root(session, kind), snapshot_root(native, kind))
        before = snapshot(session)
        self.assertEqual(session.set_track_output('A_OUTSIDE', 'Out 2.0'), 0)
        self.assertEqual(snapshot(session), before)
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / 'cycle.ptx'
            for _ in range(2):
                session.save(out)
                self.assertEqual(out.read_bytes(), original)
                session = ProToolsSession(out)
                self.assertEqual(session.get_track_outputs(), outputs)
        self.assertEqual(RESAVED.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
