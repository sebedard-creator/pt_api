"""Strict read-only resolution of the native anonymous mono playlist profile."""
import copy
from pathlib import Path
import struct
import tempfile
import unittest

from pt_api import ProToolsSession
from tests.test_get_tracks import block, make_session, playlist
from tests.test_timeline_read import definition, event
from tests.test_timeline_clip_groups import group_definition, timeline_event


MARKER = b'\x2a\x00\x00\x00'
FIXTURES = Path(__file__).parent / 'fixtures'
ORIGINAL = FIXTURES / 'native_aaf_roundtrip_original.ptx'
REIMPORTED = FIXTURES / 'native_aaf_roundtrip_reimported.ptx'
RESAVED = FIXTURES / 'native_aaf_roundtrip_pt_resaved.ptx'


def anonymous_session(names=('MONO A', 'MONO B')):
    """Synthetic mirrors of the verified layout, not a native PTX fixture."""
    count = len(names)
    session = make_session([playlist('') for _ in names])
    session.sample_rate = 48000
    session.frame_rate_enum = 0x09
    session.is_bigendian = False
    session.file_path = str(FIXTURES / 'absent.ptx')
    session._removed_offsets = []
    descriptors, metadata, slots = [], [], []
    families = [[], []]
    for index, name in enumerate(names):
        encoded = name.encode('utf-8')
        named = struct.pack('<I', len(encoded)) + encoded
        identity = struct.pack('<Q', 0x123400 + index)
        tail = bytearray(39)
        tail[:5] = b'\x00\x01\x00\x00\x00'
        struct.pack_into('<I', tail, 5, index)
        tail[11:15] = MARKER
        struct.pack_into('<I', tail, 30, index)
        descriptors.append(block(8, 0x1014, [bytearray(named + tail)]))
        tail = bytearray(32)
        tail[4:8] = MARKER
        tail[8:16] = identity
        metadata.append(block(1, 0x210b, [bytearray(bytes(4) + named + tail)]))
        tail = bytearray(42)
        tail[6:10] = MARKER
        tail[10:18] = identity
        struct.pack_into('<I', tail, 18, index + 1)
        tail[28:32] = MARKER
        tail[32:40] = identity
        for family in families:
            family.append(block(10, 0x251a, [
                bytearray(bytes(2) + named + tail), bytearray(b'opaque UI flags'),
            ]))
        config = block(9, 0x2619, [bytearray(b'opaque config'),
                                  bytearray(bytes(4) + MARKER + identity + bytes(2))])
        slots.append(block(4, 0x261c, [block(3, 0x261b, [block(2, 0x102d, [config])])]))
    session.root_items.extend([
        block(2, 0x1015, [bytearray(struct.pack('<I', count)), *descriptors]),
        block(5, 0x2107, [bytearray(bytes(8) + b'\x01' + struct.pack('<I', count)), *metadata]),
        block(8, 0x2519, [bytearray(b'opaque aggregate'), *families[0],
                            bytearray(struct.pack('<I', count)), *families[1]]),
        block(1, 0x2624, [bytearray(struct.pack('<I', count)), *slots]),
    ])
    return session


def snapshot(session):
    return [b.to_bytes()[0] for b in session.root_items]


class AnonymousMonoPlaylistTests(unittest.TestCase):
    def test_read_names_for_one_two_and_three_mono_tracks_without_mutation(self):
        for names in (('ONE',), ('MONO A', 'MONO B'), ('PISTE É', 'SECOND', 'THIRD')):
            with self.subTest(names=names):
                session = anonymous_session(names)
                before = snapshot(session)
                for _ in range(2):
                    self.assertEqual(session.get_tracks(), list(names))
                    self.assertEqual([n for _, n, _ in session._validated_main_playlists(True)],
                                     list(names))
                    self.assertEqual(snapshot(session), before)
                headers = [b.items[0] for b in session._root_blocks(0x1054)[0].items[1:]]
                self.assertTrue(all(bytes(header) == bytes(8) for header in headers))

    def test_audio_and_group_readers_use_names_without_expanding_writer_contract(self):
        session = anonymous_session()
        main = session._root_blocks(0x1054)[0]
        audio = event(0, 12345)
        macro = timeline_event(0, 99999)
        main.items[1] = playlist('', 1, [audio])
        main.items[2] = playlist('', 1, [macro])
        session.root_items.extend([
            block(1, 0x262a, [bytearray(struct.pack('<I', 1)), definition('AUDIO', 1000)]),
            block(1, 0x2630, [bytearray(4)]),
            block(1, 0x262c, [bytearray(struct.pack('<I', 1)), group_definition('GROUP', 2000)]),
        ])
        before = snapshot(session)
        audio_rows = session.get_timeline_clips()
        group_rows = session.get_timeline_clip_groups()
        self.assertEqual([(r['track'], r['start_samples'], r['end_samples']) for r in audio_rows],
                         [('MONO A', 12345, 13345)])
        self.assertEqual([(r['track'], r['start_samples'], r['end_samples']) for r in group_rows],
                         [('MONO B', 99999, 101999)])
        self.assertEqual(len(session._validated_main_timeline_events(True)), 2)
        with self.assertRaisesRegex(ValueError, 'Track name cannot be empty'):
            session._validated_main_timeline_events()
        self.assertEqual(snapshot(session), before)

    def test_unverified_missing_or_duplicate_catalog_roots_are_rejected(self):
        for kind in (0x1015, 0x2107, 0x2519, 0x2624):
            for duplicate in (False, True):
                with self.subTest(kind=hex(kind), duplicate=duplicate):
                    session = anonymous_session()
                    root = session._root_blocks(kind)[0]
                    if duplicate:
                        session.root_items.append(copy.deepcopy(root))
                    else:
                        session.root_items.remove(root)
                    before = snapshot(session)
                    with self.assertRaisesRegex(ValueError, 'unverified anonymous mono track catalog'):
                        session.get_tracks()
                    self.assertEqual(snapshot(session), before)

    def test_counts_in_each_mirror_are_required(self):
        for kind, offset in ((0x1015, 0), (0x2107, 9), (0x2624, 0), (0x2519, 0)):
            with self.subTest(kind=hex(kind)):
                session = anonymous_session()
                root = session._root_blocks(kind)[0]
                payload = root.items[3] if kind == 0x2519 else root.items[0]
                struct.pack_into('<I', payload, offset, 3)
                before = snapshot(session)
                with self.assertRaisesRegex(ValueError, 'unverified anonymous mono track catalog'):
                    session.get_tracks()
                self.assertEqual(snapshot(session), before)

    def test_descriptor_metadata_and_mirror_geometry_are_all_validated(self):
        mutations = (
            (0x1015, 0x1014, 0, 0, b'\x00\x00\x00\x00'),  # empty name
            (0x1015, 0x1014, 0, 4, b'\xff'),  # invalid UTF-8
            (0x1015, 0x1014, 0, 5, b'\x00'),  # NUL
            (0x1015, 0x1014, 0, 10, b'\x01'),  # non-mono descriptor
            (0x1015, 0x1014, 0, 15, b'\x01'),  # ordinal
            (0x2107, 0x210b, 0, 8, b'X'),  # ordered name
            (0x2107, 0x210b, 0, 22, bytes(8)),  # empty identity
            (0x2519, 0x251a, 0, 0, b'\x0b'),  # folder, not Audio
            (0x2519, 0x251a, 0, 12, b'\x01'),  # stereo channel discriminator
            (0x2519, 0x251a, 0, 22, b'\xff'),  # identity
            (0x2519, 0x251a, 0, 30, b'\x00'),  # hidden/reordered ordinal
            (0x2519, 0x251a, 0, 44, b'\xff'),  # repeated identity
            (0x2519, 0x251a, 2, 6, b'X'),  # second family's ordered name
        )
        for root_kind, entry_kind, index, offset, replacement in mutations:
            with self.subTest(entry=hex(entry_kind), index=index, offset=offset):
                session = anonymous_session()
                entry = session._root_blocks(root_kind)[0].get_all_blocks(entry_kind)[index]
                entry.items[0][offset:offset + len(replacement)] = replacement
                before = snapshot(session)
                with self.assertRaisesRegex(ValueError, 'unverified anonymous mono track catalog'):
                    session.get_tracks()
                self.assertEqual(snapshot(session), before)

    def test_duplicate_names_identities_and_reordered_slots_are_rejected(self):
        sessions = [anonymous_session(('SAME', 'SAME'))]
        session = anonymous_session()
        metadata = session._root_blocks(0x2107)[0].get_all_blocks(0x210b)
        metadata[1].items[0][22:30] = metadata[0].items[0][22:30]
        sessions.append(session)
        session = anonymous_session()
        root = session._root_blocks(0x2624)[0]
        root.items[1], root.items[2] = root.items[2], root.items[1]
        sessions.append(session)
        session = anonymous_session()
        slot = session._root_blocks(0x2624)[0].items[1]
        slot.items.append(copy.deepcopy(slot.get_all_blocks(0x2619)[0]))
        sessions.append(session)
        for index, session in enumerate(sessions):
            with self.subTest(index=index):
                before = snapshot(session)
                with self.assertRaisesRegex(ValueError, 'unverified anonymous mono track catalog'):
                    session.get_tracks()
                self.assertEqual(snapshot(session), before)

    def test_main_counters_and_headers_are_not_bypassed_by_resolution(self):
        for variant, error in (('track_count', 'track count'), ('event_count', 'event count'),
                               ('mixed', 'Mixed named and anonymous'),
                               ('header', 'Unsupported anonymous main playlist header')):
            with self.subTest(variant=variant):
                session = anonymous_session()
                root = session._root_blocks(0x1054)[0]
                if variant == 'track_count':
                    struct.pack_into('<I', root.items[0], 0, 3)
                elif variant == 'event_count':
                    struct.pack_into('<I', root.items[1].items[0], 4, 1)
                elif variant == 'mixed':
                    root.items[1] = playlist('NAMED')
                else:
                    root.items[1].items[0].extend(b'\x00')
                before = snapshot(session)
                with self.assertRaisesRegex(ValueError, error):
                    session.get_tracks()
                self.assertEqual(snapshot(session), before)

    def test_track_writers_do_not_use_read_only_resolution(self):
        for operation in (
            lambda s: s._validated_main_playlists(),
            lambda s: s.rename_track('MONO A', 'RENAMED'),
            lambda s: s.set_visible_tracks(['MONO A']),
            lambda s: s.delete_tracks(['MONO A']),
        ):
            session = anonymous_session()
            before = snapshot(session)
            with self.assertRaisesRegex(ValueError, 'Track name cannot be empty'):
                operation(session)
            self.assertEqual(snapshot(session), before)


@unittest.skipUnless(ORIGINAL.is_file() and REIMPORTED.is_file(),
                     'Local native AAF round-trip fixtures absent.')
class NativeAnonymousMonoPlaylistTests(unittest.TestCase):
    @unittest.skipUnless(RESAVED.is_file(), 'Local Pro Tools-resaved anonymous fixture absent.')
    def test_pro_tools_resaved_anonymous_profile_preserves_exact_read_geometry(self):
        session = ProToolsSession(RESAVED)
        original_bytes = RESAVED.read_bytes()
        expected_rows = ProToolsSession(REIMPORTED).get_timeline_clips()
        self.assertEqual(session.get_tracks(), ['A_OUTSIDE', 'B_INSIDE'])
        self.assertEqual(session.sample_rate, 48000)
        self.assertEqual(session.frame_rate_enum, 0x09)
        self.assertEqual(session.get_timeline_clips(), expected_rows)
        self.assertEqual(session.get_timeline_clip_groups(), [])
        self.assertEqual(len(session.get_clips()), 4)
        self.assertEqual(session._validated_physical_audio_catalog()[3],
                         ['aafzhhsBMCSpvcmRYPY.wav', 'aafyvqhCQCSpvcmRYPY.wav'])
        self.assertTrue(all(bytes(p.items[0]) == struct.pack('<II', 0, 1)
                            for p, _, _ in session._validated_main_playlists(True)))
        with tempfile.TemporaryDirectory() as directory:
            for index in range(2):
                output = Path(directory) / ('native_resaved_noop_%d.ptx' % index)
                session.save(output)
                self.assertEqual(output.read_bytes(), original_bytes)
                session = ProToolsSession(output)
                self.assertEqual(session.get_tracks(), ['A_OUTSIDE', 'B_INSIDE'])
                self.assertEqual(session.get_timeline_clips(), expected_rows)
        self.assertEqual(RESAVED.read_bytes(), original_bytes)

    def test_native_original_and_reimport_have_identical_placement_geometry(self):
        original, imported = ProToolsSession(ORIGINAL), ProToolsSession(REIMPORTED)
        columns = ('track', 'start_samples', 'length_samples', 'end_samples', 'src_offset_samples')
        expected = [('A_OUTSIDE', 1729728000, 1355354, 1731083354, 0),
                    ('B_INSIDE', 1730208480, 1355354, 1731563834, 0)]
        for session in (original, imported):
            before = snapshot(session)
            self.assertEqual(session.sample_rate, 48000)
            self.assertEqual(session.frame_rate_enum, 0x09)
            self.assertEqual(session.get_tracks(), ['A_OUTSIDE', 'B_INSIDE'])
            rows = session.get_timeline_clips(False)
            self.assertEqual([tuple(row[c] for c in columns) for row in rows], expected)
            self.assertEqual(session.get_timeline_clip_groups(), [])
            self.assertEqual(snapshot(session), before)
        self.assertEqual([row['clip_name'] for row in imported.get_timeline_clips(False)],
                         ['MEDIA-01', 'MEDIA_01-01'])
        self.assertEqual([row['physical_filename'] for row in imported.get_timeline_clips(False)],
                         ['aafzhhsBMCSpvcmRYPY.wav', 'aafyvqhCQCSpvcmRYPY.wav'])
        self.assertEqual(len(original.get_clips()), 1)
        self.assertEqual(len(imported.get_clips()), 4)
        self.assertEqual(len(original._validated_physical_audio_catalog()[3]), 1)
        self.assertEqual(len(imported._validated_physical_audio_catalog()[3]), 2)
        self.assertTrue(all(bytes(playlist.items[0]) == struct.pack('<II', 0, 1)
                            for playlist, _, _ in imported._validated_main_playlists(True)))

    def test_native_read_save_reload_is_byte_identical_twice(self):
        for path in (ORIGINAL, REIMPORTED):
            original_bytes = path.read_bytes()
            session = ProToolsSession(path)
            rows = session.get_timeline_clips()
            with tempfile.TemporaryDirectory() as directory:
                for index in range(2):
                    output = Path(directory) / ('aaf_noop_%d.ptx' % index)
                    session.save(output)
                    self.assertEqual(output.read_bytes(), original_bytes)
                    session = ProToolsSession(output)
                    self.assertEqual(session.get_tracks(), ['A_OUTSIDE', 'B_INSIDE'])
                    self.assertEqual(session.get_timeline_clips(), rows)
            self.assertEqual(path.read_bytes(), original_bytes)

    def test_native_anonymous_track_writing_is_still_rejected_without_mutation(self):
        session = ProToolsSession(REIMPORTED)
        before = snapshot(session)
        with self.assertRaisesRegex(ValueError, 'Track name cannot be empty'):
            session.rename_track('A_OUTSIDE', 'RENAMED')
        self.assertEqual(snapshot(session), before)


if __name__ == '__main__':
    unittest.main()
