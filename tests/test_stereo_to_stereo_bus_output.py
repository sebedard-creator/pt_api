"""Same-width bus reassignment, restricted to the native centered static pan."""
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
from tests.test_stereo_bus_output import stereo_session, stereo_path, pan_containers, CENTER
from tests.test_stereo_to_mono_bus_output import metadata


FIXTURES = Path(__file__).parent / 'fixtures'
BEFORE = FIXTURES / 'native_track_output_stereo_to_stereo_before.ptx'
AFTER = FIXTURES / 'native_track_output_stereo_to_stereo_after.ptx'
RESAVED = FIXTURES / 'native_track_output_stereo_to_stereo_pt_resaved.ptx'
NEXT = 'NEXT_STEREO_é_LONG'
NEXT_ID = struct.pack('<Q', 0x887701)


def same_width_session(names=('FIRST', 'SECOND', 'THIRD')):
    session = stereo_session(names=names)
    session.set_track_output('FIRST', 'STEREO_é')
    catalog = session._root_blocks(0x2603)[0]
    catalog.items.append(stereo_path(NEXT, NEXT_ID))
    struct.pack_into('<I', catalog.items[0], 0, 4)
    return session


def pointer_rows(session):
    """Compare standard pointer metadata and logical targets, not relocated offsets."""
    def walk(items, path=()):
        for index, item in enumerate(items):
            if isinstance(item, PTBlock):
                here = path + ((item.content_type, index),)
                yield item.original_offset, here
                yield from walk(item.items, here)
    offsets = dict(walk(session._content_root_items()))
    payload = bytes(session._raw_0002_payload(session._root_blocks(2)[0]))
    return [(start, pos, offsets.get(struct.unpack_from('<I', payload, pos)[0]), payload[start:pos])
            for start, pos in session._validate_0002_record_layout(payload)]


class StereoToStereoTests(unittest.TestCase):
    def test_unused_utf8_target_changes_only_route_and_preserves_live_pan_objects(self):
        session = same_width_session(names=('FIRST', 'SECOND', 'THIRD'))
        reference = copy.deepcopy(session)
        old = bytes(routes(session)[0].items[0])
        encoded = NEXT.encode('utf-8')
        routes(reference)[0].items[0] = (b'\x0a' + old[1:12] + NEXT_ID + old[20:36]
                                       + struct.pack('<I', len(encoded)) + encoded + old[-19:])
        pan, second = pan_containers(session)
        lane, items = pan.items[1], pan.items
        session._removed_offsets = [99]
        session._removed_block_types = {99: 0x2077}
        removed = metadata(session)
        self.assertEqual(session.set_track_output('FIRST', NEXT), 1)
        self.assertEqual(snapshot(session), snapshot(reference))
        self.assertIs(pan_containers(session)[0], pan)
        self.assertIs(pan.items, items)
        self.assertIs(pan.items[1], lane)
        self.assertIs(pan_containers(session)[1], second)
        self.assertEqual(metadata(session), removed)
        self.assertEqual(session.set_track_output('FIRST', 'STEREO_é'), 1)
        self.assertEqual(routes(session)[0].items[0], old)

    def test_assigned_target_reuses_actual_code_without_copying_donor_pan(self):
        session = same_width_session()
        session.set_track_output('SECOND', NEXT)
        routes(session)[1].items[0] = b'\xfc' + bytes(routes(session)[1].items[0])[1:]
        pan_containers(session, 1)[0].items[1].items[0] = CENTER[:26] + b'\x01\x00' + CENTER[28:]
        reference = copy.deepcopy(session)
        routes(reference)[0].items[0] = bytes(routes(reference)[1].items[0])
        self.assertEqual(session.set_track_output('FIRST', NEXT), 1)
        self.assertEqual(snapshot(session), snapshot(reference))
        self.assertEqual(routes(session)[0].items[0][0], 0xfc)
        self.assertEqual(pan_containers(session)[0].items[1].items[0], CENTER)
        self.assertNotEqual(pan_containers(session, 1)[0].items[1].items[0], CENTER)

    def test_composed_width_changes_use_current_tree_and_restore_the_original(self):
        session = same_width_session()
        before = snapshot(session)
        session.set_track_output('FIRST', NEXT)
        session.set_track_output('FIRST', 'BUS_A')
        session.set_track_output('FIRST', NEXT)
        session.set_track_output('FIRST', 'STEREO_é')
        self.assertEqual(snapshot(session), before)
        self.assertEqual(metadata(session), ([], {}))

    def test_unknown_pan_or_automation_is_rejected_without_mutating_metadata(self):
        for variant in ('value', 'timestamp', 'flags', 'short', 'lane_type', 'lane_kind',
                        'extra_payload', 'header', 'padding', 'second_pan', 'third', 'nested'):
            with self.subTest(variant=variant):
                session = same_width_session()
                pan, second = pan_containers(session)
                lane = pan.items[1]
                if variant in ('value', 'timestamp', 'flags'):
                    payload = bytearray(lane.items[0])
                    payload[{'value': 26, 'timestamp': 22, 'flags': 14}[variant]] ^= 1
                    lane.items[0] = payload
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
                    session.set_track_output('FIRST', NEXT)
                self.assertEqual(snapshot(session), before)
                self.assertEqual(metadata(session), removed)

    def test_unverified_target_or_late_calibration_failure_leaves_pan_and_route_untouched(self):
        for variant in ('geometry', 'alias', 'duplicate', 'calibration'):
            with self.subTest(variant=variant):
                session = same_width_session()
                catalog = session._root_blocks(0x2603)[0]
                if variant == 'geometry': catalog.items[-1].items[0][-1] = 1
                elif variant == 'alias':
                    catalog.items.append(stereo_path('ALIAS', NEXT_ID))
                    struct.pack_into('<I', catalog.items[0], 0, 5)
                elif variant == 'duplicate':
                    catalog.items.append(copy.deepcopy(catalog.items[-1]))
                    struct.pack_into('<I', catalog.items[0], 0, 5)
                else: routes(session)[1].items[0][0] += 1
                before, removed = snapshot(session), metadata(session)
                with self.assertRaises(ValueError): session.set_track_output('FIRST', NEXT)
                self.assertEqual(snapshot(session), before)
                self.assertEqual(metadata(session), removed)

    def test_unused_target_needs_distinct_anchors_and_uint8_code_without_wrap(self):
        for variant in ('one_bus', 'overflow'):
            with self.subTest(variant=variant):
                session = same_width_session()
                if variant == 'one_bus':
                    session.set_track_output('SECOND', 'STEREO_é')
                    session.set_track_output('THIRD', 'STEREO_é')
                else:
                    # Base 253 gives existing codes 253/254/255 but target 256.
                    for route, code in zip(routes(session), (255, 254, 253)):
                        route.items[0] = bytes((code,)) + bytes(route.items[0])[1:]
                before = snapshot(session)
                with self.assertRaisesRegex(ValueError, 'two distinct|UInt8'):
                    session.set_track_output('FIRST', NEXT)
                self.assertEqual(snapshot(session), before)

    def test_identical_assignment_does_not_inspect_or_replace_pan(self):
        session = same_width_session()
        pan_containers(session)[0].items = [b'opaque pan']
        before, removed = snapshot(session), metadata(session)
        self.assertEqual(session.set_track_output('FIRST', 'STEREO_é'), 0)
        self.assertEqual(snapshot(session), before)
        self.assertEqual(metadata(session), removed)


@unittest.skipUnless(BEFORE.is_file() and AFTER.is_file(), 'Local native stereo-to-stereo pair absent.')
class NativeStereoToStereoTests(unittest.TestCase):
    def test_native_route_matches_exactly_with_pan_catalogs_and_pointer_targets_preserved(self):
        session, after = ProToolsSession(BEFORE), ProToolsSession(AFTER)
        reference = copy.deepcopy(session)
        routes(reference)[0].items[0] = bytes(routes(after)[0].items[0])
        old_pans = pan_containers(session)
        old_lane = old_pans[0].items[1]
        offsets = [b.original_offset for b in old_pans] + [old_lane.original_offset]
        self.assertEqual(session.set_track_output('A_OUTSIDE', 'API_ROUTE_STEREO_NEXT'), 1)
        self.assertEqual(snapshot(session), snapshot(reference))
        self.assertIs(pan_containers(session)[0], old_pans[0])
        self.assertIs(pan_containers(session)[0].items[1], old_lane)
        self.assertEqual([b.original_offset for b in old_pans] + [old_lane.original_offset], offsets)
        self.assertEqual(session.get_track_outputs(), after.get_track_outputs())
        self.assertEqual(session.get_timeline_clips(), after.get_timeline_clips())
        for kind in (0x2603, 0x1022):
            self.assertEqual(snapshot_root(session, kind), snapshot_root(after, kind))
        self.assertEqual(pointer_rows(session), pointer_rows(after))
        self.assertEqual(len(pointer_rows(session)), 172)
        self.assertEqual(metadata(session), ([], {}))

    def test_native_originals_and_authored_output_survive_two_byte_identical_cycles(self):
        for path, mutate in ((BEFORE, False), (AFTER, False), (BEFORE, True)):
            with self.subTest(path=path.name, mutate=mutate):
                session = ProToolsSession(path)
                original = path.read_bytes()
                if mutate: session.set_track_output('A_OUTSIDE', 'API_ROUTE_STEREO_NEXT')
                expected, clips = session.get_track_outputs(), session.get_timeline_clips()
                with tempfile.TemporaryDirectory() as directory:
                    out = Path(directory) / 'cycle.ptx'
                    session.save(out)
                    data = out.read_bytes()
                    if not mutate: self.assertEqual(data, original)
                    for _ in range(2):
                        session = ProToolsSession(out)
                        self.assertEqual(session.get_track_outputs(), expected)
                        self.assertEqual(session.get_timeline_clips(), clips)
                        session.save(out)
                        self.assertEqual(out.read_bytes(), data)
                self.assertEqual(path.read_bytes(), original)

    def test_native_growth_and_shrink_restore_entire_before_byte_for_byte(self):
        session = ProToolsSession(BEFORE)
        session.set_track_output('A_OUTSIDE', 'API_ROUTE_STEREO_NEXT')
        session.set_track_output('A_OUTSIDE', 'Out 2.0')
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / 'restored.ptx'
            session.save(out)
            self.assertEqual(out.read_bytes(), BEFORE.read_bytes())


@unittest.skipUnless(RESAVED.is_file() and AFTER.is_file(), 'Local Pro Tools-resaved same-width fixture absent.')
class NativeStereoToStereoResavedTests(unittest.TestCase):
    def test_resaved_route_pan_catalogs_and_pointers_survive_cycles_and_composition(self):
        session = ProToolsSession(RESAVED)
        native = ProToolsSession(AFTER)
        original = RESAVED.read_bytes()
        expected = [('A_OUTSIDE', ['API_ROUTE_STEREO_NEXT']), ('B_INSIDE', ['API_ROUTE_A'])]
        self.assertEqual(session.get_track_outputs(), expected)
        self.assertEqual(session.get_tracks(), ['A_OUTSIDE', 'B_INSIDE'])
        self.assertEqual(session.get_timeline_clips(), native.get_timeline_clips())
        self.assertEqual([bytes(b.items[0]) for b in routes(session)],
                         [bytes(b.items[0]) for b in routes(native)])
        for kind in (0x2603, 0x1022):
            self.assertEqual(snapshot_root(session, kind), snapshot_root(native, kind))
        self.assertEqual(pointer_rows(session), pointer_rows(native))
        self.assertEqual(pan_containers(session)[0].items[1].items[0], CENTER)
        self.assertEqual(pan_containers(session)[1].items, [bytes(14)])
        self.assertEqual([b.items for b in pan_containers(session, 1)], [[bytes(14)], [bytes(14)]])
        before = snapshot(session)
        self.assertEqual(session.set_track_output('A_OUTSIDE', 'API_ROUTE_STEREO_NEXT'), 0)
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
            session.set_track_output('A_OUTSIDE', 'API_ROUTE_STEREO_NEXT')
            session.save(out)
            self.assertEqual(out.read_bytes(), original)
            self.assertEqual(metadata(session), ([], {}))
        self.assertEqual(RESAVED.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
