"""Bounded Clip Gain records; envelope preservation, not envelope editing."""
import copy
from pathlib import Path
import struct
import tempfile
import unittest

from pt_api import ProToolsSession, PTBlock
from tests.test_clip_gain import make_session, make_clip, gain_index
from tests.test_anonymous_mono_playlists import snapshot
from tests.test_stereo_to_stereo_bus_output import pointer_rows


FIXTURES = Path(__file__).parent / 'fixtures'
BEFORE = FIXTURES / 'native_clip_gain_mixed_before.ptx'
AFTER = FIXTURES / 'native_clip_gain_mixed_after.ptx'
RESAVED = FIXTURES / 'native_clip_gain_mixed_pt_resaved.ptx'
SHARED_RESAVED = FIXTURES / 'native_clip_gain_mixed_shared_pt_resaved.ptx'
STATIC_META = bytes.fromhex('0146010016000000000001000000040000000000000000000000')
# Actual native four-node envelope, including the exact mouse-entered values.
ENVELOPE = bytes.fromhex(
    '014601002e0000000000040000000400030000000000'
    '0000000000000000607701000000000080dd05009b73bec040cc08000000c9b6')


def static(value):
    return STATIC_META + struct.pack('<f', value)


def mixed_session(records=None, indexes=(1, 0)):
    session, dictionary, a, b = make_session()
    if records is None:
        records = [ENVELOPE, static(3)]
    dictionary.items[0] = bytearray(struct.pack('<I', len(records)) + b''.join(records))
    for block, index in zip((a, b), indexes):
        struct.pack_into('<i', block.items[0], len(block.items[0]) - 6, index)
    return session, dictionary, a, b


def clip_payloads(session):
    result = {}
    for definition in session._root_blocks(0x262a)[0].items:
        if not isinstance(definition, PTBlock) or definition.content_type != 0x2629:
            continue
        block = next(b for b in definition.items if isinstance(b, PTBlock) and b.content_type == 0x2628)
        raw = bytes(block.items[0])
        n = struct.unpack_from('<I', raw)[0]
        name = raw[4:4+n].decode('utf-8')
        result[name] = block
    return result


def gains_by_name(session):
    _, _, records = session._validated_clip_gain_dictionary()
    return {name: None if gain_index(block) == -1 else records[gain_index(block)][1]
            for name, block in clip_payloads(session).items()}


class MixedClipGainTests(unittest.TestCase):
    def test_static_after_envelope_changes_only_one_float_preserving_objects(self):
        s, d, a, b = mixed_session()
        reference = copy.deepcopy(s)
        raw = bytearray(reference._root_blocks(0x2637)[0].items[0])
        struct.pack_into('<f', raw, 4 + len(ENVELOPE) + 26, 6)
        reference._root_blocks(0x2637)[0].items[0] = raw
        d.original_offset = reference._root_blocks(0x2637)[0].original_offset = 1234
        s._removed_offsets = []
        s._removed_block_types = {}
        removed, types = s._removed_offsets, s._removed_block_types
        old_b = b.items[0]
        self.assertEqual(s.set_clip_gain('A', 6), 1)
        self.assertEqual(snapshot(s), snapshot(reference))
        self.assertIs(s._root_blocks(0x2637)[0], d)
        self.assertEqual(d.original_offset, 1234)
        self.assertIs(b.items[0], old_b)
        self.assertEqual((gain_index(a), gain_index(b)), (1, 0))
        self.assertIs(s._removed_offsets, removed)
        self.assertIs(s._removed_block_types, types)

    def test_static_before_or_between_envelopes_uses_record_offset_not_stride(self):
        for rows, index in (([static(3), ENVELOPE], 0),
                            ([ENVELOPE, static(3), ENVELOPE], 1),
                            ([ENVELOPE, ENVELOPE, static(3)], 2)):
            s, _, _, _ = mixed_session(rows, (index, 1 if index == 0 else 0))
            self.assertEqual(s.set_clip_gain('A', -10), index)
            _, _, actual = s._validated_clip_gain_dictionary()
            expected = list(rows)
            expected[index] = static(-10)
            self.assertEqual([record for _, record in actual], expected)

    def test_signatures_inside_node_data_do_not_create_record_boundaries(self):
        envelope = bytearray(ENVELOPE)
        envelope[-4:] = b'\x01\x46\x01\x00'  # Finite Float32, not another record.
        s, _, _, _ = mixed_session([bytes(envelope), static(3)])
        self.assertEqual(s.set_clip_gain('A', 6), 1)
        self.assertEqual(s._validated_clip_gain_dictionary()[2][0][1], envelope)

    def test_shared_static_point_is_cloned_and_other_definition_keeps_old_gain(self):
        s, d, a, b = mixed_session(indexes=(1, 1))
        before = bytes(d.items[0])
        self.assertEqual(s.set_clip_gain('A', 6), 2)
        self.assertEqual((gain_index(a), gain_index(b)), (2, 1))
        self.assertEqual(struct.unpack_from('<I', d.items[0])[0], 3)
        self.assertEqual(bytes(d.items[0][4:-30]), before[4:])
        self.assertEqual(bytes(d.items[0][-30:]), static(6))
        self.assertEqual(s._validated_clip_gain_dictionary()[2][0][1], ENVELOPE)

    def test_first_target_gain_in_mixed_dictionary_appends_one_static_record(self):
        s, d, a, b = mixed_session(indexes=(-1, 0))
        raw = bytes(d.items[0])
        reference = copy.deepcopy(s)
        expected = reference._root_blocks(0x2637)[0]
        expected.items[0].extend(static(6))
        struct.pack_into('<I', expected.items[0], 0, 3)
        expected_a = clip_payloads(reference)['A']
        struct.pack_into('<i', expected_a.items[0], len(expected_a.items[0]) - 6, 2)
        self.assertEqual(s.set_clip_gain('A', 6), 2)
        self.assertEqual(snapshot(s), snapshot(reference))
        self.assertEqual((gain_index(a), gain_index(b)), (2, 0))
        self.assertEqual(bytes(d.items[0][4:-30]), raw[4:])
        self.assertEqual(gains_by_name(s), {'A': static(6), 'B': ENVELOPE})

    def test_envelope_target_is_never_overwritten_with_a_static_gain(self):
        s, _, _, _ = mixed_session(indexes=(0, 1))
        before = snapshot(s)
        with self.assertRaisesRegex(NotImplementedError, 'Editing a Clip Gain envelope'):
            s.set_clip_gain('A', 6)
        self.assertEqual(snapshot(s), before)

    def test_all_clip_indexes_are_checked_even_for_non_target_definition(self):
        for index in (-2, 2, 0x7fffffff):
            s, _, _, _ = mixed_session(indexes=(1, index))
            before = snapshot(s)
            with self.assertRaisesRegex(ValueError, 'invalid Clip Gain index'):
                s.set_clip_gain('A', 6)
            self.assertEqual(snapshot(s), before)

    def test_bad_count_trailing_bytes_and_truncation_never_mutate(self):
        for variant in ('too_many', 'too_few', 'tail', 'truncated_header', 'truncated_record', 'huge_size'):
            s, d, _, _ = mixed_session()
            raw = bytearray(d.items[0])
            if variant == 'too_many': struct.pack_into('<I', raw, 0, 0xffffffff)
            elif variant == 'too_few': struct.pack_into('<I', raw, 0, 1)
            elif variant == 'tail': raw.extend(bytes(30))
            elif variant == 'truncated_header': raw = raw[:4 + len(ENVELOPE) + 3]
            elif variant == 'truncated_record': raw.pop()
            else: struct.pack_into('<I', raw, 8, 0xffffffff)
            d.items[0] = raw
            before = snapshot(s)
            with self.assertRaises(ValueError): s.set_clip_gain('A', 6)
            self.assertEqual(snapshot(s), before, variant)

    def test_unsupported_three_node_record_is_not_guessed(self):
        raw = bytearray(ENVELOPE[:-8])
        struct.pack_into('<I', raw, 4, 0x26)
        struct.pack_into('<I', raw, 10, 3)
        struct.pack_into('<I', raw, 16, 2)
        s, _, _, _ = mixed_session([raw, static(3)])
        before = snapshot(s)
        with self.assertRaisesRegex(NotImplementedError, 'record size 0x26'):
            s.set_clip_gain('A', 6)
        self.assertEqual(snapshot(s), before)

    def test_envelope_header_count_flags_reserved_and_nodes_are_validated(self):
        for offset in (0, 8, 10, 14, 16, 20, 22):
            rows = bytearray(ENVELOPE)
            rows[offset] ^= 1
            s, _, _, _ = mixed_session([static(3), rows], (0, 1))
            before = snapshot(s)
            with self.assertRaises(ValueError): s.set_clip_gain('A', 6)
            self.assertEqual(snapshot(s), before)
        for variant in ('duplicate', 'descending', 'nan', 'inf'):
            rows = bytearray(ENVELOPE)
            if variant == 'duplicate': struct.pack_into('<I', rows, 30, 0)
            elif variant == 'descending': struct.pack_into('<I', rows, 38, 1)
            else: struct.pack_into('<f', rows, 50, float(variant))
            s, _, _, _ = mixed_session([rows, static(3)])
            before = snapshot(s)
            with self.assertRaises(ValueError): s.set_clip_gain('A', 6)
            self.assertEqual(snapshot(s), before)

    def test_malformed_static_record_in_other_definition_precedes_mutation(self):
        for offset in (0, 8, 10, 14, 16, 20, 22):
            rows = bytearray(static(0))
            rows[offset] ^= 1
            s, _, _, _ = mixed_session([ENVELOPE, static(3), rows])
            before = snapshot(s)
            with self.assertRaises(ValueError): s.set_clip_gain('A', 6)
            self.assertEqual(snapshot(s), before)
        s, _, _, _ = mixed_session([ENVELOPE, static(3), static(float('nan'))])
        before = snapshot(s)
        with self.assertRaises(ValueError): s.set_clip_gain('A', 6)
        self.assertEqual(snapshot(s), before)

    def test_incoming_numeric_errors_leave_mixed_dictionary_unchanged(self):
        for value, error in ((True, TypeError), ('bad', TypeError), (1e100, ValueError),
                             (float('nan'), ValueError), (float('inf'), ValueError)):
            s, _, _, _ = mixed_session()
            before = snapshot(s)
            with self.assertRaises(error): s.set_clip_gain('A', value)
            self.assertEqual(snapshot(s), before)
        s, _, _, _ = mixed_session()
        self.assertEqual(s.set_clip_gain('A', '-inf'), 1)
        self.assertEqual(s._validated_clip_gain_dictionary()[2][1][1][-4:], bytes.fromhex('f41a91c3'))

    def test_zero_record_dictionary_remains_valid_and_bad_tail_is_rejected(self):
        s, d, _, _ = make_session()
        self.assertEqual(s._validated_clip_gain_dictionary()[2], [])
        d.items[0].extend(b'\x00')
        before = snapshot(s)
        with self.assertRaisesRegex(ValueError, 'trailing bytes'): s.set_clip_gain('A', 6)
        self.assertEqual(snapshot(s), before)

    def test_shared_clone_keeps_active_envelope_and_only_changes_target_link(self):
        s, d, a, b = mixed_session()
        c, c_payload = make_clip('C', 2)
        struct.pack_into('<i', c_payload.items[0], len(c_payload.items[0]) - 6, 1)
        clip_list = s._root_blocks(0x262a)[0]
        clip_list.items.append(c)
        struct.pack_into('<I', clip_list.items[0], 0, 3)
        reference = copy.deepcopy(s)
        expected_dict = reference._root_blocks(0x2637)[0]
        expected_dict.items[0].extend(static(6))
        struct.pack_into('<I', expected_dict.items[0], 0, 3)
        expected_a = reference._root_blocks(0x262a)[0].items[1].items[0]
        struct.pack_into('<i', expected_a.items[0], len(expected_a.items[0]) - 6, 2)
        self.assertEqual(s.set_clip_gain('A', 6), 2)
        self.assertEqual(snapshot(s), snapshot(reference))
        self.assertEqual((gain_index(a), gain_index(b), gain_index(c_payload)), (2, 0, 1))
        self.assertEqual(gains_by_name(s), {'A': static(6), 'B': ENVELOPE, 'C': static(3)})

    def test_sequential_updates_do_not_clone_unique_target_again(self):
        s, d, a, b = mixed_session(indexes=(1, 1))
        self.assertEqual(s.set_clip_gain('A', 6), 2)
        self.assertEqual(s.set_clip_gain('A', -10), 2)
        self.assertEqual(s.set_clip_gain('A', 6), 2)
        self.assertEqual(struct.unpack_from('<I', d.items[0])[0], 3)
        self.assertEqual((gain_index(a), gain_index(b)), (2, 1))
        self.assertEqual(gains_by_name(s), {'A': static(6), 'B': static(3)})
        # The remaining old point is now unshared and updates in place.
        self.assertEqual(s.set_clip_gain('B', -3), 1)
        self.assertEqual(struct.unpack_from('<I', d.items[0])[0], 3)
        self.assertEqual(gains_by_name(s), {'A': static(6), 'B': static(-3)})

    def test_shared_clone_works_at_each_static_record_position(self):
        for records, index in (([static(3), ENVELOPE], 0),
                               ([ENVELOPE, static(3)], 1),
                               ([ENVELOPE, ENVELOPE, static(3)], 2)):
            s, d, a, b = mixed_session(records, (index, index))
            raw = bytes(d.items[0])
            self.assertEqual(s.set_clip_gain('A', 6), len(records))
            self.assertEqual(bytes(d.items[0][4:-30]), raw[4:])
            self.assertEqual(bytes(d.items[0][-30:]), static(6))
            self.assertEqual(gain_index(b), index)
            self.assertEqual(gain_index(a), len(records))

    def test_multiple_missing_indexes_are_not_treated_as_one_shared_point(self):
        s, d, a, b = mixed_session([ENVELOPE], (-1, -1))
        c, c_payload = make_clip('C', 2)
        struct.pack_into('<i', c_payload.items[0], len(c_payload.items[0]) - 6, 0)
        clips = s._root_blocks(0x262a)[0]
        clips.items.append(c)
        struct.pack_into('<I', clips.items[0], 0, 3)
        self.assertEqual(s.set_clip_gain('A', 6), 1)
        self.assertEqual((gain_index(a), gain_index(b), gain_index(c_payload)), (1, -1, 0))
        self.assertEqual(gains_by_name(s), {'A': static(6), 'B': None, 'C': ENVELOPE})
        self.assertEqual(s.set_clip_gain('B', -3), 2)
        self.assertEqual(gains_by_name(s), {'A': static(6), 'B': static(-3), 'C': ENVELOPE})
        self.assertEqual(struct.unpack_from('<I', d.items[0])[0], 3)

    def test_first_gain_then_updates_reuse_new_point_and_keep_envelope(self):
        s, d, _, _ = mixed_session([ENVELOPE], (-1, 0))
        self.assertEqual(s.set_clip_gain('A', -float('inf')), 1)
        self.assertEqual(bytes(d.items[0][-4:]), bytes.fromhex('f41a91c3'))
        for value in (3, -10, -3):
            self.assertEqual(s.set_clip_gain('A', value), 1)
        self.assertEqual(struct.unpack_from('<I', d.items[0])[0], 2)
        self.assertEqual(gains_by_name(s), {'A': static(-3), 'B': ENVELOPE})

    def test_first_gain_validates_all_records_and_other_indexes_before_append(self):
        for variant in ('bad_index', 'bad_envelope', 'bad_static', 'unknown_record'):
            s, d, _, b = mixed_session(indexes=(-1, 0))
            if variant == 'bad_index':
                struct.pack_into('<i', b.items[0], len(b.items[0]) - 6, 2)
            elif variant == 'bad_envelope':
                d.items[0][4 + 16] ^= 1
            elif variant == 'bad_static':
                d.items[0][4 + len(ENVELOPE) + 8] ^= 1
            else:
                row = bytearray(ENVELOPE[:-8])
                struct.pack_into('<I', row, 4, 0x26)
                d.items[0] = bytearray(struct.pack('<I', 2) + static(3) + row)
            before = snapshot(s)
            error = NotImplementedError if variant == 'unknown_record' else ValueError
            with self.assertRaises(error): s.set_clip_gain('A', 6)
            self.assertEqual(snapshot(s), before, variant)


@unittest.skipUnless(BEFORE.exists() and AFTER.exists(), 'Local native mixed Clip Gain pair absent')
class NativeMixedClipGainTests(unittest.TestCase):
    def test_native_records_match_despite_pro_tools_reordering_dictionary(self):
        before, after = (ProToolsSession(str(p)) for p in (BEFORE, AFTER))
        a, b = gains_by_name(before), gains_by_name(after)
        self.assertEqual(a['MEDIA-01'], static(3))
        self.assertEqual(b['MEDIA-01'], static(6))
        self.assertEqual(a['MEDIA_01-01'], ENVELOPE)
        a['MEDIA-01'] = static(6)
        self.assertEqual(a, b)
        self.assertEqual(before.get_timeline_clips(), after.get_timeline_clips())

    def test_native_writer_changes_only_target_float_and_is_exactly_reversible(self):
        s = ProToolsSession(str(BEFORE))
        reference = copy.deepcopy(s)
        d, raw, records = reference._validated_clip_gain_dictionary()
        changed = bytearray(raw)
        struct.pack_into('<f', changed, records[1][0] + 26, 6)
        d.items[0] = changed
        original = snapshot(s)
        self.assertEqual(s.set_clip_gain('MEDIA-01', 6), 1)
        self.assertEqual(snapshot(s), snapshot(reference))
        expected = gains_by_name(ProToolsSession(str(AFTER)))
        self.assertEqual(gains_by_name(s), expected)
        self.assertEqual(s.set_clip_gain('MEDIA-01', 3), 1)
        self.assertEqual(snapshot(s), original)

    def test_native_noop_modified_cycles_pointer_targets_and_complete_restoration(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / 'cycle.ptx'
            for source in (BEFORE, AFTER):
                s = ProToolsSession(str(source))
                original = source.read_bytes()
                s.save(str(out))
                self.assertEqual(out.read_bytes(), original)
                s = ProToolsSession(str(out))
                s.save(str(out))
                self.assertEqual(out.read_bytes(), original)
            s = ProToolsSession(str(BEFORE))
            rows, clips, tracks = pointer_rows(s), s.get_timeline_clips(), s.get_tracks()
            s.set_clip_gain('MEDIA-01', 6)
            s.save(str(out))
            produced = out.read_bytes()
            for _ in range(2):
                s = ProToolsSession(str(out))
                self.assertEqual(pointer_rows(s), rows)
                self.assertEqual(s.get_timeline_clips(), clips)
                self.assertEqual(s.get_tracks(), tracks)
                self.assertEqual(gains_by_name(s)['MEDIA_01-01'], ENVELOPE)
                s.save(str(out))
                self.assertEqual(out.read_bytes(), produced)
            with self.assertRaises(NotImplementedError): s.set_clip_gain('MEDIA_01-01', 6)
            s.set_clip_gain('MEDIA-01', 3)
            s.save(str(out))
            self.assertEqual(out.read_bytes(), BEFORE.read_bytes())

    @unittest.skipUnless(RESAVED.exists(), 'Local Pro Tools-resaved mixed Clip Gain output absent')
    def test_pro_tools_resaved_unshared_output_keeps_curve_and_exact_compositions(self):
        s = ProToolsSession(str(RESAVED))
        expected = gains_by_name(ProToolsSession(str(AFTER)))
        self.assertEqual(gains_by_name(s), expected)
        self.assertEqual(s.get_clips(), ProToolsSession(str(BEFORE)).get_clips())
        self.assertEqual(s.get_timeline_clips(), ProToolsSession(str(BEFORE)).get_timeline_clips())
        self.assertEqual(pointer_rows(s), pointer_rows(ProToolsSession(str(BEFORE))))
        original = RESAVED.read_bytes()
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / 'cycle.ptx'
            for _ in range(2):
                s.save(str(out))
                self.assertEqual(out.read_bytes(), original)
                s = ProToolsSession(str(out))
            for gain in (6, 3, 6):
                s.set_clip_gain('MEDIA-01', gain)
            self.assertEqual(gains_by_name(s), expected)
            s.save(str(out))
            self.assertEqual(out.read_bytes(), original)

    @unittest.skipUnless(RESAVED.exists(), 'Local Pro Tools-resaved mixed Clip Gain output absent')
    def test_controlled_shared_reference_clones_one_point_and_preserves_active_curve(self):
        # This sharing is constructed, not claimed as a native before/after oracle.
        s = ProToolsSession(str(RESAVED))
        blocks = clip_payloads(s)
        target = blocks['MEDIA-01']
        witness = blocks['MEDIA']
        old_index = gain_index(target)
        struct.pack_into('<i', witness.items[0], len(witness.items[0]) - 6, old_index)
        controlled_gains = gains_by_name(s)
        self.assertEqual(controlled_gains['MEDIA'], static(6))
        self.assertEqual(controlled_gains['MEDIA-01'], static(6))
        d, raw, records = s._validated_clip_gain_dictionary()
        count = len(records)
        expected = copy.deepcopy(s)
        ed = expected._root_blocks(0x2637)[0]
        ed.items[0] = bytearray(raw + static(3))
        struct.pack_into('<I', ed.items[0], 0, count + 1)
        expected_target = clip_payloads(expected)['MEDIA-01']
        struct.pack_into('<i', expected_target.items[0], len(expected_target.items[0]) - 6, count)
        rows, clips, tracks = pointer_rows(s), s.get_timeline_clips(), s.get_tracks()
        self.assertEqual(s.set_clip_gain('MEDIA-01', 3), count)
        self.assertEqual(snapshot(s), snapshot(expected))
        self.assertEqual(gain_index(witness), old_index)
        self.assertEqual(bytes(d.items[0][4:-30]), raw[4:])
        result = dict(controlled_gains)
        result['MEDIA-01'] = static(3)
        self.assertEqual(gains_by_name(s), result)
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / 'shared.ptx'
            s.save(str(out))
            produced = out.read_bytes()
            for _ in range(2):
                s = ProToolsSession(str(out))
                self.assertEqual(gains_by_name(s), result)
                self.assertEqual(pointer_rows(s), rows)
                self.assertEqual(s.get_timeline_clips(), clips)
                self.assertEqual(s.get_tracks(), tracks)
                s.save(str(out))
                self.assertEqual(out.read_bytes(), produced)
            # Both definitions are now independent; neither update clones again.
            s.set_clip_gain('MEDIA', 5)
            s.set_clip_gain('MEDIA-01', -10)
            s.set_clip_gain('MEDIA', 6)
            s.set_clip_gain('MEDIA-01', 3)
            s.save(str(out))
            self.assertEqual(out.read_bytes(), produced)

    @unittest.skipUnless(SHARED_RESAVED.exists() and RESAVED.exists(), 'Local native gain references absent')
    def test_pro_tools_resaved_shared_clone_keeps_independent_gains_after_compaction(self):
        s = ProToolsSession(str(SHARED_RESAVED))
        expected = gains_by_name(ProToolsSession(str(RESAVED)))
        expected['MEDIA'] = static(6)
        expected['MEDIA-01'] = static(3)
        self.assertEqual(gains_by_name(s), expected)
        blocks = clip_payloads(s)
        self.assertNotEqual(gain_index(blocks['MEDIA']), gain_index(blocks['MEDIA-01']))
        base = ProToolsSession(str(RESAVED))
        self.assertEqual(s.get_clips(), base.get_clips())
        self.assertEqual(s.get_timeline_clips(), base.get_timeline_clips())
        self.assertEqual(pointer_rows(s), pointer_rows(base))
        original = SHARED_RESAVED.read_bytes()
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / 'cycle.ptx'
            for _ in range(2):
                s.save(str(out))
                self.assertEqual(out.read_bytes(), original)
                s = ProToolsSession(str(out))
            for name, value in (('MEDIA', 5), ('MEDIA-01', -10), ('MEDIA', 6), ('MEDIA-01', 3)):
                s.set_clip_gain(name, value)
            s.save(str(out))
            self.assertEqual(out.read_bytes(), original)

    @unittest.skipUnless(SHARED_RESAVED.exists(), 'Local Pro Tools-resaved shared gain output absent')
    def test_controlled_missing_gain_reference_appends_static_and_preserves_curve(self):
        # Known historical -1 sentinel constructed on the validated native session.
        s = ProToolsSession(str(SHARED_RESAVED))
        target = clip_payloads(s)['MEDIA-01']
        struct.pack_into('<i', target.items[0], len(target.items[0]) - 6, -1)
        expected = gains_by_name(s)
        self.assertIsNone(expected['MEDIA-01'])
        d, raw, rows = s._validated_clip_gain_dictionary()
        count = len(rows)
        reference = copy.deepcopy(s)
        rd = reference._root_blocks(0x2637)[0]
        rd.items[0] = bytearray(raw + static(-3))
        struct.pack_into('<I', rd.items[0], 0, count + 1)
        rt = clip_payloads(reference)['MEDIA-01']
        struct.pack_into('<i', rt.items[0], len(rt.items[0]) - 6, count)
        clips, pointers = s.get_timeline_clips(), pointer_rows(s)
        self.assertEqual(s.set_clip_gain('MEDIA-01', -3), count)
        self.assertEqual(snapshot(s), snapshot(reference))
        self.assertEqual(bytes(d.items[0][4:-30]), raw[4:])
        expected['MEDIA-01'] = static(-3)
        self.assertEqual(gains_by_name(s), expected)
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / 'first.ptx'
            s.save(str(out))
            produced = out.read_bytes()
            for _ in range(2):
                s = ProToolsSession(str(out))
                self.assertEqual(gains_by_name(s), expected)
                self.assertEqual(s.get_timeline_clips(), clips)
                self.assertEqual(pointer_rows(s), pointers)
                s.save(str(out))
                self.assertEqual(out.read_bytes(), produced)
            for value in (6, -10, -3): s.set_clip_gain('MEDIA-01', value)
            self.assertEqual(len(s._validated_clip_gain_dictionary()[2]), count + 1)
            s.save(str(out))
            self.assertEqual(out.read_bytes(), produced)


if __name__ == '__main__':
    unittest.main()
