"""Read-only principal mono bus output profile; no routing writer tests."""
import copy
from pathlib import Path
import struct
import tempfile
import unittest

from pt_api import ProToolsSession
from tests.test_get_tracks import block, playlist
from tests.test_anonymous_mono_playlists import anonymous_session, snapshot


FIXTURES = Path(__file__).parent / 'fixtures'
BEFORE = FIXTURES / 'native_track_output_mono_before.ptx'
AFTER = FIXTURES / 'native_track_output_mono_after.ptx'
RESAVED = FIXTURES / 'native_track_output_mono_pt_resaved.ptx'
MARKER = b'\x2a\x00\x00\x00'
TRAILER = bytes.fromhex('0000ffffffffffffffff00ffffffff000c0000')


def path_record(name, identity):
    encoded = name.encode('utf-8')
    tail = (struct.pack('<IHHHHH', 1, 137, 0xffff, 1, 0, 0)
            + MARKER + identity + bytes(16))
    return block(0x0e, 0x2602, [bytearray(b'\x02\x00' + struct.pack('<I', len(encoded))
                                    + encoded + tail), bytearray(b'opaque catalog state')])


def route_payload(name, identity, code=101):
    encoded = name.encode('utf-8')
    return bytearray(bytes((code,)) + b'\x00\x01\x01' + bytes(4) + MARKER
                     + identity + bytes(16) + struct.pack('<I', len(encoded))
                     + encoded + TRAILER)


def routing_session(names=('MONO A', 'MONO B'), anonymous=False):
    session = anonymous_session(names)
    if not anonymous:
        main = session._root_blocks(0x1054)[0]
        main.items[1:] = [playlist(name) for name in names]
    identities = [struct.pack('<Q', 0x998800 + i) for i in range(2)]
    catalog = block(2, 0x2603, [bytearray(struct.pack('<I', 2)),
                               path_record('BUS_A', identities[0]),
                               path_record('BUS_B_LONG', identities[1])])
    session.root_items.append(catalog)
    slots = session._root_blocks(0x2624)[0].get_all_blocks(0x261c)
    for index, slot in enumerate(slots):
        output_index = index % 2
        output = ('BUS_A', 'BUS_B_LONG')[output_index]
        route = block(2, 0x260e, [route_payload(output, identities[output_index])])
        slot.items[0].items.append(block(1, 0x260d, [route]))
    # Detect accidental use of cached bytes or source offsets.
    session.data = object()
    return session


def routes(session):
    return session._root_blocks(0x2624)[0].get_all_blocks(0x260e)


class TrackOutputReadTests(unittest.TestCase):
    def test_generic_one_two_three_tracks_named_or_anonymous_without_mutation(self):
        for names in (('ONE',), ('MONO A', 'MONO B'), ('PISTE É', 'SECOND', 'THIRD')):
            for anonymous in (False, True):
                with self.subTest(names=names, anonymous=anonymous):
                    session = routing_session(names, anonymous)
                    before = snapshot(session)
                    expected = [(name, [('BUS_A', 'BUS_B_LONG')[i % 2]])
                                for i, name in enumerate(names)]
                    self.assertEqual(session.get_track_outputs(), expected)
                    self.assertEqual(session.get_track_outputs(), expected)
                    self.assertEqual(snapshot(session), before)

    def test_result_does_not_alias_tree_and_current_tree_not_stale_bytes_is_read(self):
        session = routing_session(('TRACK',))
        result = session.get_track_outputs()
        result[0][1].append('not a real output')
        self.assertEqual(session.get_track_outputs(), [('TRACK', ['BUS_A'])])
        route = routes(session)[0]
        route.items[0] = route_payload('BUS_B_LONG', struct.pack('<Q', 0x998801), 102)
        before = snapshot(session)
        self.assertEqual(session.get_track_outputs(), [('TRACK', ['BUS_B_LONG'])])
        self.assertEqual(snapshot(session), before)

    def test_missing_main_map_or_zero_tracks_returns_empty(self):
        for roots in ([], [block(2, 0x1054, [bytearray(4)])]):
            session = ProToolsSession.__new__(ProToolsSession)
            session.root_items = roots
            self.assertEqual(session.get_track_outputs(), [])

    def test_live_catalog_is_required_and_factory_libraries_are_not_used(self):
        session = routing_session()
        catalog = session._root_blocks(0x2603)[0]
        session.root_items.extend([block(1, 0x4501, [copy.deepcopy(catalog)]),
                                   block(1, 0x4702, [copy.deepcopy(catalog)])])
        self.assertEqual(session.get_track_outputs(), [('MONO A', ['BUS_A']),
                                                      ('MONO B', ['BUS_B_LONG'])])
        session.root_items.remove(catalog)
        before = snapshot(session)
        with self.assertRaisesRegex(ValueError, 'missing or ambiguous live I/O catalog'):
            session.get_track_outputs()
        self.assertEqual(snapshot(session), before)

    def test_catalog_counter_root_and_nested_paths_are_validated(self):
        for variant in ('duplicate', 'counter', 'truncated', 'nested'):
            with self.subTest(variant=variant):
                session = routing_session()
                catalog = session._root_blocks(0x2603)[0]
                if variant == 'duplicate':
                    session.root_items.append(copy.deepcopy(catalog))
                elif variant == 'counter':
                    struct.pack_into('<I', catalog.items[0], 0, 3)
                elif variant == 'truncated':
                    catalog.items[0] = bytearray(3)
                else:
                    catalog.items.append(block(1, 0x4501, [copy.deepcopy(catalog.items[1])]))
                before = snapshot(session)
                with self.assertRaisesRegex(ValueError, 'Unsupported mono bus track-output profile'):
                    session.get_track_outputs()
                self.assertEqual(snapshot(session), before)

    def test_unverified_audio_identity_profile_is_rejected(self):
        for variant in ('identity', 'stereo', 'name'):
            with self.subTest(variant=variant):
                session = routing_session()
                if variant == 'identity':
                    config = session._root_blocks(0x2624)[0].get_all_blocks(0x2619)[0]
                    config.items[-1][8] ^= 1
                elif variant == 'stereo':
                    mirror = session._root_blocks(0x2519)[0].get_all_blocks(0x251a)[0]
                    p = mirror.items[0]; n = struct.unpack_from('<I', p, 2)[0]
                    p[6 + n] = 1
                else:
                    session._root_blocks(0x1054)[0].items[1] = playlist('OTHER')
                before = snapshot(session)
                with self.assertRaisesRegex(ValueError, 'Unsupported mono bus track-output profile'):
                    session.get_track_outputs()
                self.assertEqual(snapshot(session), before)

    def test_only_one_principal_descriptor_is_accepted(self):
        for variant in ('missing', 'duplicate', 'extra_descendant', 'container'):
            with self.subTest(variant=variant):
                session = routing_session()
                slot = session._root_blocks(0x2624)[0].items[1]
                state = slot.items[0].get_all_blocks(0x260d)[0]
                route = state.items[0]
                if variant == 'missing':
                    state.items.clear()
                elif variant == 'duplicate':
                    state.items.append(copy.deepcopy(route))
                elif variant == 'extra_descendant':
                    slot.items.append(block(1, 0x200b, [copy.deepcopy(route)]))
                else:
                    slot.items[0].items.append(copy.deepcopy(state))
                before = snapshot(session)
                with self.assertRaisesRegex(ValueError, 'Unsupported mono bus track-output profile'):
                    session.get_track_outputs()
                self.assertEqual(snapshot(session), before)

    def test_descriptor_and_names_reject_corruption_without_partial_results(self):
        variants = ('prefix', 'magic', 'reserved', 'short', 'length', 'utf8', 'nul', 'trailer', 'nonraw')
        for variant in variants:
            with self.subTest(variant=variant):
                session = routing_session()
                route = routes(session)[1]  # Second track fails after first has been validated.
                p = route.items[0]
                if variant == 'prefix': p[1] = 1
                elif variant == 'magic': p[8] = 0
                elif variant == 'reserved': p[20] = 1
                elif variant == 'short': route.items[0] = p[:39]
                elif variant == 'length': struct.pack_into('<I', p, 36, 0xffffffff)
                elif variant == 'utf8': p[40] = 0xff
                elif variant == 'nul': p[40] = 0
                elif variant == 'trailer': p[-1] = 1
                else: route.items[0] = block(1, 0x4301, [])
                before = snapshot(session)
                with self.assertRaisesRegex(ValueError, 'Unsupported mono bus track-output profile'):
                    session.get_track_outputs()
                self.assertEqual(snapshot(session), before)

    def test_selected_bus_must_have_matching_unique_name_identity_and_verified_geometry(self):
        for variant in ('missing', 'duplicate', 'alias', 'identity', 'null', 'stereo',
                        'physical', 'subchannel', 'trailer'):
            with self.subTest(variant=variant):
                session = routing_session()
                catalog = session._root_blocks(0x2603)[0]
                entry = catalog.items[1]
                p = entry.items[0]
                end = 6 + struct.unpack_from('<I', p, 2)[0]
                if variant == 'missing':
                    catalog.items.pop(1); struct.pack_into('<I', catalog.items[0], 0, 1)
                elif variant == 'duplicate':
                    catalog.items.append(copy.deepcopy(entry)); struct.pack_into('<I', catalog.items[0], 0, 3)
                elif variant == 'alias':
                    catalog.items.append(path_record('ALIAS', struct.pack('<Q', 0x998800)))
                    struct.pack_into('<I', catalog.items[0], 0, 3)
                elif variant == 'identity': p[end + 18] ^= 1
                elif variant == 'null':
                    p[end + 18:end + 26] = bytes(8)
                    routes(session)[0].items[0][12:20] = bytes(8)
                elif variant == 'stereo': p[1] = 1
                elif variant == 'physical': p[end + 11] = 1
                elif variant == 'subchannel': struct.pack_into('<H', p, end + 6, 2)
                else: p[-1] = 1
                before = snapshot(session)
                with self.assertRaisesRegex(ValueError, 'Unsupported (mono )?bus track-output profile'):
                    session.get_track_outputs()
                self.assertEqual(snapshot(session), before)

    def test_unselected_multichannel_path_is_preserved_without_width_guessing(self):
        session = routing_session()
        catalog = session._root_blocks(0x2603)[0]
        catalog.items.append(block(2, 0x2602, [bytearray(b'\x02\x06' + struct.pack('<I', 8)
                                                       + b'OPAQUE51' + bytes(52))]))
        struct.pack_into('<I', catalog.items[0], 0, 3)
        before = snapshot(session)
        self.assertEqual(session.get_track_outputs(), [('MONO A', ['BUS_A']),
                                                      ('MONO B', ['BUS_B_LONG'])])
        self.assertEqual(snapshot(session), before)


@unittest.skipUnless(BEFORE.is_file() and AFTER.is_file(), 'Local native mono-routing pair absent.')
class NativeTrackOutputTests(unittest.TestCase):
    def test_native_pair_outputs_catalog_and_timeline_geometry(self):
        before, after = ProToolsSession(BEFORE), ProToolsSession(AFTER)
        expected = ([('A_OUTSIDE', ['API_ROUTE_A']), ('B_INSIDE', ['API_ROUTE_A'])],
                    [('A_OUTSIDE', ['API_ROUTE_B_LONG']), ('B_INSIDE', ['API_ROUTE_A'])])
        for session, outputs in zip((before, after), expected):
            raw = snapshot(session)
            self.assertEqual(session.get_track_outputs(), outputs)
            self.assertEqual(session.get_tracks(), ['A_OUTSIDE', 'B_INSIDE'])
            self.assertEqual(session.sample_rate, 48000)
            self.assertEqual(session.frame_rate_enum, 0x09)
            self.assertEqual(len(session._root_blocks(0x2603)[0].get_all_blocks(0x2602)), 90)
            self.assertEqual(len(routes(session)), 2)  # One per track, not two mirrors.
            self.assertEqual(snapshot(session), raw)
        self.assertEqual(before.get_timeline_clips(), after.get_timeline_clips())
        self.assertEqual([r['start_samples'] for r in after.get_timeline_clips(False)],
                         [1729728000, 1730208480])
        self.assertEqual([r['length_samples'] for r in after.get_timeline_clips(False)],
                         [1355354, 1355354])
        self.assertEqual([bytes(b.items[0]) for b in before._root_blocks(0x2603)[0].get_all_blocks(0x2602)],
                         [bytes(b.items[0]) for b in after._root_blocks(0x2603)[0].get_all_blocks(0x2602)])
        a, b = routes(before), routes(after)
        self.assertEqual(bytes(a[1].items[0]), bytes(b[1].items[0]))
        self.assertEqual((a[0].items[0][0], b[0].items[0][0]), (0x65, 0x66))
        self.assertEqual(bytes(a[0].items[0][-19:]), bytes(b[0].items[0][-19:]))

    def test_native_pair_two_read_save_reload_cycles_are_byte_identical(self):
        for path in (BEFORE, AFTER):
            session = ProToolsSession(path)
            original = path.read_bytes()
            outputs = session.get_track_outputs()
            rows = session.get_timeline_clips()
            with tempfile.TemporaryDirectory() as directory:
                for index in range(2):
                    output = Path(directory) / ('routing_noop_%d.ptx' % index)
                    session.save(output)
                    self.assertEqual(output.read_bytes(), original)
                    session = ProToolsSession(output)
                    self.assertEqual(session.get_track_outputs(), outputs)
                    self.assertEqual(session.get_timeline_clips(), rows)
            self.assertEqual(path.read_bytes(), original)

    def test_native_existing_rename_guard_is_not_relaxed_by_routing_reader(self):
        session = ProToolsSession(AFTER)
        raw = snapshot(session)
        with self.assertRaisesRegex(ValueError, 'must be opened and saved once'):
            session.rename_track('A_OUTSIDE', 'RENAMED')
        self.assertEqual(session.get_track_outputs(), [('A_OUTSIDE', ['API_ROUTE_B_LONG']),
                                                      ('B_INSIDE', ['API_ROUTE_A'])])
        self.assertEqual(snapshot(session), raw)


@unittest.skipUnless(RESAVED.is_file() and AFTER.is_file(), 'Local Pro Tools-resaved mono-routing fixture absent.')
class NativeTrackOutputResavedTests(unittest.TestCase):
    def test_protools_resave_preserves_outputs_geometry_and_two_api_cycles(self):
        session = ProToolsSession(RESAVED)
        reference = ProToolsSession(AFTER)
        expected = [('A_OUTSIDE', ['API_ROUTE_B_LONG']), ('B_INSIDE', ['API_ROUTE_A'])]
        original = RESAVED.read_bytes()
        raw = snapshot(session)
        self.assertEqual(session.get_track_outputs(), expected)
        self.assertEqual(session.get_tracks(), reference.get_tracks())
        self.assertEqual(session.get_timeline_clips(), reference.get_timeline_clips())
        self.assertEqual([bytes(b.items[0]) for b in routes(session)],
                         [bytes(b.items[0]) for b in routes(reference)])
        self.assertEqual([bytes(b.items[0]) for b in session._root_blocks(0x2603)[0].get_all_blocks(0x2602)],
                         [bytes(b.items[0]) for b in reference._root_blocks(0x2603)[0].get_all_blocks(0x2602)])
        self.assertEqual(snapshot(session), raw)
        with tempfile.TemporaryDirectory() as directory:
            for index in range(2):
                output = Path(directory) / ('routing_resaved_%d.ptx' % index)
                session.save(output)
                self.assertEqual(output.read_bytes(), original)
                session = ProToolsSession(output)
                self.assertEqual(session.get_track_outputs(), expected)
                self.assertEqual(session.get_timeline_clips(), reference.get_timeline_clips())
        self.assertEqual(RESAVED.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
