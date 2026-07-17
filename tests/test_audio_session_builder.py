"""Tests for generic template-based audio-session construction."""

import hashlib
import struct
import tempfile
import unittest
from pathlib import Path

from pt_api import (
    PTBlock,
    ProToolsSession,
    _inspect_template_audio_wave,
    _prepare_template_audio_clips,
    build_audio_session,
)


def block(block_type, content_type, items):
    result = PTBlock(block_type, content_type, 1)
    result.items = items
    return result


def riff_chunk(chunk_id, payload):
    result = bytearray(chunk_id + struct.pack("<I", len(payload)) + payload)
    if len(payload) & 1:
        result.append(0)
    return result


def write_float_bwf_wave(path, samples, time_reference, *, valid_fact=True):
    fmt_payload = bytearray(
        struct.pack("<HHIIHHH", 0xFFFE, 1, 48_000, 192_000, 4, 32, 22)
    )
    fmt_payload.extend(struct.pack("<HI", 32, 4))
    fmt_payload.extend(
        bytes.fromhex("03000000 0000 1000 8000 00aa00389b71")
    )
    bext = bytearray(602)
    struct.pack_into("<Q", bext, 338, time_reference)
    bext[348:380] = bytes.fromhex(
        "060a2b340101010501010f1013000000"
        "7fffffffffffffff65b0cb5acce13e00"
    )
    fact_samples = samples if valid_fact else samples + 1
    body = bytearray(b"WAVE")
    body.extend(riff_chunk(b"fmt ", fmt_payload))
    body.extend(riff_chunk(b"fact", struct.pack("<I", fact_samples)))
    body.extend(riff_chunk(b"bext", bext))
    body.extend(riff_chunk(b"data", bytes(samples * 4)))
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)


def filename_record(filename):
    encoded = filename.encode("utf-8")
    return (
        b"\x02\x00\x00\x00\x00"
        + struct.pack("<I", len(encoded))
        + encoded
        + b"EVAW"
    )


def imported_media_entry(length_samples=100, ordinal=1):
    media_info = bytearray(struct.pack("<I", 48_000))
    media_info.extend(b"\x01\x20")
    media_info.extend(length_samples.to_bytes(3, "little"))
    media_info.extend(b"\x00" * 5)
    media_info.append(0x03)
    detail = block(
        0x23,
        0x2106,
        [
            bytearray(142),
            block(0x01, 0x4301, [bytearray(32)]),
            bytearray(58),
        ],
    )
    return block(
        0x09,
        0x1003,
        [
            bytearray(struct.pack("<I", ordinal)),
            block(0x01, 0x1001, [media_info]),
            block(0x02, 0x1033, []),
            detail,
            bytearray(4),
        ],
    )


def imported_clip_definition(
    name="Prototype.A1", length=100, start=1_000, *, split_identity=False
):
    encoded = name.encode("utf-8")
    payload = bytearray(struct.pack("<I", len(encoded)) + encoded)
    payload.extend(b"\x00\x00\x30\x04\x00")
    payload.extend(length.to_bytes(3, "little"))
    payload.extend(struct.pack("<I", start))
    payload.extend(
        bytes.fromhex(
            "feff00000000ffff040004000000000000000000000000000000000000"
            "00000000ffffffff0000"
        )
    )
    identity = bytearray(48)
    identity[22] = 0xFF
    identity[23:39] = bytes(range(16))
    media_link = bytearray(104)
    identity_items = [identity]
    if split_identity:
        false_empty_block = block(0x00, 0x0000, [])
        false_empty_block.original_size = 0
        identity_items = [identity[:10], false_empty_block, identity[17:]]
    return block(
        0x0B,
        0x2629,
        [
            block(0x04, 0x2628, [payload]),
            *identity_items,
            block(
                0x01,
                0x4403,
                [block(0x00, 0x1000, [bytearray(1)]), bytearray(1)],
            ),
            media_link,
        ],
    )


def make_audio_import_template_session(
    track_names=("Production 1", "Production 2"), *, split_identity=False
):
    prototype_filename = "Prototype.wav"
    label = b"Workspace"
    session_label = b"template"
    names = bytearray(struct.pack("<I", 4))
    names.extend(b"\x01")
    names.extend(struct.pack("<I", 3))
    names.extend(struct.pack("<I", len(b"Audio Files")))
    names.extend(b"Audio Files")
    names.extend(bytearray(4))
    names.extend(filename_record(prototype_filename))
    names.extend(b"\x00\xff\xff\xff\xff")
    names.extend(struct.pack("<I", len(label)))
    names.extend(label)
    names.extend(b"\x01\x02\x03\x04\x01")
    names.extend(struct.pack("<I", 2))
    names.extend(struct.pack("<I", len(session_label)))
    names.extend(session_label)
    names.extend(bytearray(4))

    catalog = block(
        0x03,
        0x1004,
        [
            bytearray(struct.pack("<I", 1)),
            block(0x01, 0x103A, [names]),
            imported_media_entry(),
            bytearray(4),
        ],
    )
    clip_list = block(
        0x01,
        0x262A,
        [
            bytearray(struct.pack("<I", 1)),
            imported_clip_definition(split_identity=split_identity),
        ],
    )
    playlists = []
    for track_name in track_names:
        encoded = track_name.encode("utf-8")
        header = bytearray(struct.pack("<I", len(encoded)) + encoded)
        header.extend(struct.pack("<I", 0))
        header.extend(b"\x01\x00")
        playlists.append(block(0x03, 0x1052, [header]))
    track_map = block(
        0x01,
        0x1054,
        [bytearray(struct.pack("<I", len(playlists))), *playlists],
    )

    session = ProToolsSession.__new__(ProToolsSession)
    session.is_bigendian = False
    session.sample_rate = 48_000
    session.frame_rate_enum = 0x09
    session.root_items = [catalog, clip_list, track_map]
    session._removed_offsets = []
    return session


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class TemplateAudioWaveTests(unittest.TestCase):
    def test_inspection_reads_bwf_position_and_float_length(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "arbitrary_source.wav"
            write_float_bwf_wave(path, 321, 1_824_829_006)
            info = _inspect_template_audio_wave(path)
            self.assertEqual(info["source_filename"], path.name)
            self.assertEqual(info["length_samples"], 321)
            self.assertEqual(info["bwf_time_reference"], 1_824_829_006)

    def test_inspection_rejects_mismatched_fact_count(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.wav"
            write_float_bwf_wave(path, 10, 1_000, valid_fact=False)
            with self.assertRaisesRegex(ValueError, "fact sample count"):
                _inspect_template_audio_wave(path)

    def test_one_path_is_not_accepted_as_the_iterable(self):
        with self.assertRaisesRegex(TypeError, "descriptor mappings"):
            _prepare_template_audio_clips("one.wav")

    def test_descriptor_order_and_explicit_track_mapping_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            z_source = root / "z_source.wav"
            a_source = root / "a_source.wav"
            write_float_bwf_wave(z_source, 10, 1_010)
            write_float_bwf_wave(a_source, 10, 1_000)
            prepared, tracks = _prepare_template_audio_clips([
                {
                    "audio_path": z_source,
                    "track_name": "Effects B",
                    "physical_filename": "renamed_z.wav",
                    "clip_name": "Custom Z",
                    "placement_start_samples": 9_000,
                },
                {"audio_path": a_source, "track_name": "Effects A"},
            ])
            self.assertEqual(
                [item["physical_filename"] for item in prepared],
                ["renamed_z.wav", a_source.name],
            )
            self.assertEqual([item["order"] for item in prepared], [1, 2])
            self.assertEqual(prepared[0]["clip_name"], "Custom Z")
            self.assertEqual(prepared[0]["start_samples"], 9_000)
            self.assertEqual(prepared[0]["bwf_time_reference"], 1_010)
            self.assertEqual(tracks, ["Effects B", "Effects A"])

    def test_descriptors_require_path_and_track(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.wav"
            write_float_bwf_wave(path, 10, 1_000)
            with self.assertRaisesRegex(ValueError, "audio_path and track_name"):
                _prepare_template_audio_clips([{"audio_path": path}])


class TemplateAudioMutationTests(unittest.TestCase):
    def test_placement_override_does_not_change_bwf_media_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.wav"
            write_float_bwf_wave(path, 100, 1_000)
            prepared, _ = _prepare_template_audio_clips([{
                "audio_path": path,
                "track_name": "Production 1",
                "placement_start_samples": 9_000,
            }])
            session = make_audio_import_template_session()
            session._populate_audio_import_template(prepared)
            self.assertEqual(
                session.get_timeline_clips()[0]["start_samples"], 9_000
            )
            clip_list = session._root_blocks(0x262A)[0]
            definition = next(
                item for item in clip_list.items
                if isinstance(item, PTBlock) and item.content_type == 0x2629
            )
            name_block = next(
                item for item in definition.items
                if isinstance(item, PTBlock) and item.content_type == 0x2628
            )
            payload = name_block.items[0]
            name_length = struct.unpack_from("<I", payload, 0)[0]
            self.assertEqual(
                struct.unpack_from("<I", payload, 4 + name_length + 8)[0],
                1_000,
            )

    def test_same_track_overlap_preserves_descriptor_order(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.wav"
            second = root / "second.wav"
            write_float_bwf_wave(first, 101, 1_000)
            write_float_bwf_wave(second, 200, 1_100)
            prepared, _ = _prepare_template_audio_clips([
                {"audio_path": first, "track_name": "Production 1"},
                {"audio_path": second, "track_name": "Production 1"},
            ])
            session = make_audio_import_template_session()
            manifest = session._populate_audio_import_template(prepared)
            self.assertEqual([item["order"] for item in manifest], [1, 2])
            self.assertEqual(
                [item["name"] for item in session.get_clips()],
                [f"{first.stem}.A1", f"{second.stem}.A1"],
            )
            timeline = session.get_timeline_clips()
            self.assertEqual(
                [item["track"] for item in timeline], ["Production 1"] * 2
            )
            self.assertEqual(timeline[0]["end_samples"], 1_101)
            self.assertEqual(timeline[1]["start_samples"], 1_100)
            playlists = session._validated_main_playlists()
            self.assertEqual([len(events) for _, _, events in playlists], [2, 0])
            self.assertEqual(bytes(playlists[0][0].items[-1]), b"\x01\x00")

    def test_any_number_of_existing_template_tracks_can_be_targeted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources = []
            track_names = ("Cloth", "Foley", "Props")
            for index, track_name in enumerate(track_names):
                path = root / f"source_{index}.wav"
                write_float_bwf_wave(path, 100, 1_000 + index)
                sources.append({"audio_path": path, "track_name": track_name})
            prepared, used_tracks = _prepare_template_audio_clips(sources)
            self.assertEqual(used_tracks, list(track_names))
            session = make_audio_import_template_session(track_names)
            session._populate_audio_import_template(prepared)
            timeline = session.get_timeline_clips()
            self.assertEqual(
                {item["track"] for item in timeline},
                set(track_names),
            )

    def test_unknown_target_track_is_rejected_before_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.wav"
            write_float_bwf_wave(path, 100, 1_000)
            prepared, _ = _prepare_template_audio_clips([
                {"audio_path": path, "track_name": "Missing"}
            ])
            session = make_audio_import_template_session()
            before = repr(session.root_items)
            with self.assertRaisesRegex(ValueError, "not found"):
                session._populate_audio_import_template(prepared)
            self.assertEqual(repr(session.root_items), before)

    def test_hidden_timeline_event_is_rejected_before_mutation(self):
        session = make_audio_import_template_session()
        hidden_event = block(0x03, 0x1050, [bytearray(1)])
        session.root_items.append(block(0x01, 0x2428, [hidden_event]))
        before = repr(session.root_items)
        with self.assertRaisesRegex(ValueError, "visible or hidden"):
            session._validated_audio_import_template()
        self.assertEqual(repr(session.root_items), before)

    def test_false_block_in_identity_does_not_duplicate_media_record(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            descriptors = []
            for index in range(2):
                path = root / f"source_{index}.wav"
                write_float_bwf_wave(path, 100 + index, 1_000 + index)
                descriptors.append({
                    "audio_path": path,
                    "track_name": "Production 1",
                })
            prepared, _ = _prepare_template_audio_clips(descriptors)
            session = make_audio_import_template_session(split_identity=True)
            session._populate_audio_import_template(prepared)
            definitions = [
                item for item in session._root_blocks(0x262A)[0].items
                if isinstance(item, PTBlock) and item.content_type == 0x2629
            ]
            self.assertEqual(len(definitions), 2)
            for expected_index, definition in enumerate(definitions):
                identity_span, media_span = session._validated_2629_fixed_records(
                    definition
                )
                self.assertEqual(len(identity_span[2]), 48)
                self.assertEqual(len(media_span[2]), 104)
                self.assertEqual(
                    struct.unpack_from("<I", media_span[2], 96)[0],
                    expected_index,
                )


REFERENCE_ROOT = Path(__file__).resolve().parents[1] / "pfx_import_reference"
REFERENCE_TEMPLATE = (
    REFERENCE_ROOT
    / "01_A_imported_cliplist"
    / "01_A_imported_cliplist.ptx"
)
REFERENCE_SOURCE = REFERENCE_ROOT / "Source PFX"


@unittest.skipUnless(
    REFERENCE_TEMPLATE.is_file() and REFERENCE_SOURCE.is_dir(),
    "Local native template-audio comparison corpus is not installed.",
)
class NativeTemplateAudioReferenceTests(unittest.TestCase):
    def test_builds_and_reloads_native_reference_without_touching_audio(self):
        sources = sorted(
            (
                path for path in REFERENCE_SOURCE.glob("*.wav")
                if not path.name.startswith("._")
            ),
            key=lambda path: path.name.casefold(),
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "A144_generated"
            descriptors = [
                {"audio_path": source, "track_name": "PFX 01"}
                for source in sources
            ]
            result = build_audio_session(
                REFERENCE_TEMPLATE,
                descriptors,
                output,
                session_name="A144_generated",
            )
            self.assertEqual(result["track_count"], 1)
            self.assertEqual(result["tracks"], ["PFX 01"])
            generated = ProToolsSession(result["session_path"])
            timeline = generated.get_timeline_clips()
            self.assertEqual(len(timeline), 2)
            self.assertEqual([item["track"] for item in timeline], ["PFX 01"] * 2)
            self.assertEqual(timeline[0]["end_samples"] - timeline[1]["start_samples"], 1)
            definitions = [
                item for item in generated._root_blocks(0x262A)[0].items
                if isinstance(item, PTBlock) and item.content_type == 0x2629
            ]
            for expected_index, definition in enumerate(definitions):
                _, media_span = generated._validated_2629_fixed_records(definition)
                self.assertEqual(
                    struct.unpack_from("<I", media_span[2], 96)[0],
                    expected_index,
                )
            for source in sources:
                copied = output / "Audio Files" / source.name
                self.assertEqual(sha256(source), sha256(copied))


if __name__ == "__main__":
    unittest.main()
