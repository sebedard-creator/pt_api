import struct
import tempfile
import unittest
from pathlib import Path

from pt_api import PTBlock, ProToolsSession


def block(block_type, content_type, items):
    result = PTBlock(block_type, content_type, 1)
    result.items = items
    return result


def filename_record(filename, suffix=b"EVAW"):
    encoded = filename.encode("utf-8")
    return (
        b"\x02\x00\x00\x00\x00"
        + struct.pack("<I", len(encoded))
        + encoded
        + suffix
    )


def media_entry(umid, ordinal=1, production_layout=False):
    media_info = bytearray(31)
    media_info[22:31] = b"\x2a" + umid[16:24]
    detail_header = bytearray(142 if production_layout else 151)
    detail_tail = bytearray(58)
    detail_tail[18:50] = umid[:32]
    detail = block(
        0x23,
        0x2106,
        [
            detail_header,
            block(0x01, 0x4301, [bytearray(32)]),
            detail_tail,
        ],
    )
    return block(
        0x09,
        0x1003,
        [
            bytearray(struct.pack("<I", ordinal)),
            block(0x02, 0x1001, [media_info]),
            block(0x02, 0x1033, []),
            detail,
            bytearray(4),
        ],
    )


def clip_definition(
    name,
    time_reference,
    virtual_source_offset=None,
    virtual_length=120_000,
):
    encoded = name.encode("utf-8")
    payload = bytearray(struct.pack("<I", len(encoded)) + encoded)
    if virtual_source_offset is None:
        payload.extend(b"\x00\x00\x30\x44\x00")
        payload.extend((48_000).to_bytes(3, "little"))
        payload.extend(bytearray(40))
        struct.pack_into("<I", payload, 4 + len(encoded) + 8, time_reference)
        struct.pack_into("<I", payload, 4 + len(encoded) + 12, time_reference)
    else:
        if virtual_source_offset == 0:
            flags = 0x0001
            source_width = 0
        elif virtual_source_offset <= 0xFFFF:
            flags = 0x2001
            source_width = 2
        elif virtual_source_offset <= 0xFFFFFF:
            flags = 0x3001
            source_width = 3
        else:
            flags = 0x4001
            source_width = 4
        if virtual_length <= 0xFF:
            width_selector, length_width = 0x10, 1
        elif virtual_length <= 0xFFFF:
            width_selector, length_width = 0x20, 2
        elif virtual_length <= 0xFFFFFF:
            width_selector, length_width = 0x30, 3
        else:
            width_selector, length_width = 0x40, 4
        payload.extend(struct.pack("<H", flags))
        payload.extend(bytes((width_selector, ((flags >> 8) & 0xF0) | 0x04, 0x08)))
        payload.extend(virtual_source_offset.to_bytes(source_width, "little"))
        payload.extend(virtual_length.to_bytes(length_width, "little"))
        payload.extend(struct.pack("<I", time_reference + virtual_source_offset))
        payload.extend(bytearray(b"\xff" * 16 + b"\x00" * 20))
    identity = bytearray(48)
    identity[22] = 0xFF
    identity[23:39] = bytes(range(16))
    media_link = bytearray(104)
    media_link[96] = 0
    return block(
        0x0B,
        0x2629,
        [
            block(0x04, 0x2628, [payload]),
            identity,
            block(0x01, 0x4403, [block(0x00, 0x1000, [bytearray(1)]), bytearray(1)]),
            media_link,
        ],
    )


def timeline_event(start_samples):
    payload = bytearray(35)
    struct.pack_into("<I", payload, 2, 0)
    struct.pack_into("<Q", payload, 7, start_samples)
    payload[15] = 0x03
    return block(
        0x01,
        0x1050,
        [block(0x0A, 0x104F, [payload]), bytearray(b"\x00\x01\x01")],
    )


def make_session(
    start_samples=1_000,
    source_time_reference=None,
    catalog_labels=("SHARE TO NETWORK",),
    physical_count=1,
    virtual_source_offset=None,
    filename_record_suffix=b"EVAW",
):
    if source_time_reference is None:
        source_time_reference = start_samples
    source_filename = "Audio 1_01.wav"
    umid = bytearray(64)
    umid[:32] = bytes.fromhex(
        "06 0a 2b 34 01 01 01 05 01 01 0f 10 13 00 00 00 "
        "d7 5f 80 b7 ef 34 80 00 c5 c5 cb 5a cc e1 3e 00"
    )
    physical_names = [source_filename]
    physical_names.extend(
        "Filler {0:03d}.wav".format(index)
        for index in range(2, physical_count + 1)
    )
    names = bytearray(
        struct.pack("<I", physical_count + len(catalog_labels) + 2)
    )
    names.extend(b"\x01")
    names.extend(struct.pack("<I", physical_count + len(catalog_labels) + 1))
    names.extend(struct.pack("<I", len(b"Audio Files")))
    names.extend(b"Audio Files")
    names.extend(bytearray(4))
    for physical_name in physical_names:
        names.extend(filename_record(physical_name, filename_record_suffix))
    names.extend(b"\x00\xff\xff\xff\xff")
    for depth, label in enumerate(catalog_labels, start=1):
        encoded_label = label.encode("utf-8")
        names.extend(struct.pack("<I", len(encoded_label)))
        names.extend(encoded_label)
        names.extend(bytes((depth, depth + 1, depth + 2, depth + 3)))
        names.extend(b"\x01")
        names.extend(struct.pack("<I", physical_count + depth))
    session_stem = b"relink_before"
    names.extend(struct.pack("<I", len(session_stem)))
    names.extend(session_stem)
    names.extend(bytearray(4))

    catalog = block(
        0x03,
        0x1004,
        [
            bytearray(struct.pack("<I", physical_count)),
            block(0x01, 0x103A, [names]),
            *(
                media_entry(
                    umid,
                    ordinal,
                    production_layout=virtual_source_offset is not None,
                )
                for ordinal in range(1, physical_count + 1)
            ),
            bytearray(4),
            block(0x01, 0x103A, [bytearray(4)]),
        ],
    )
    clip_list = block(
        0x01,
        0x262A,
        [
            bytearray(struct.pack("<I", 1)),
            clip_definition(
                "Audio 1_01",
                source_time_reference,
                virtual_source_offset=virtual_source_offset,
            ),
        ],
    )
    track_name = b"Audio 1"
    playlist_header = bytearray(struct.pack("<I", len(track_name)) + track_name)
    playlist_header.extend(struct.pack("<I", 1))
    playlist = block(
        0x03,
        0x1052,
        [playlist_header, timeline_event(start_samples)],
    )
    track_map = block(
        0x01,
        0x1054,
        [bytearray(struct.pack("<I", 1)), playlist],
    )

    session = ProToolsSession.__new__(ProToolsSession)
    session.is_bigendian = False
    session.sample_rate = 48_000
    session.frame_rate_enum = 0x01
    session.root_items = [catalog, clip_list, track_map]
    session._removed_offsets = []
    return session, bytes(umid)


def riff_chunk(chunk_id, payload):
    result = bytearray(chunk_id + struct.pack("<I", len(payload)) + payload)
    if len(payload) & 1:
        result.append(0)
    return result


def write_pro_tools_wave(
    path,
    stem,
    umid,
    time_reference=1_000,
    production_regn=False,
):
    bext = bytearray(412)
    bext[256:265] = b"Pro Tools"
    bext[288:300] = b"SOURCE000001"
    bext[320:330] = b"2026-07-15"
    bext[330:338] = b"12:00:00"
    struct.pack_into("<Q", bext, 338, time_reference)
    bext[348:412] = umid
    minf = bytearray(16)
    regn = bytearray(92)
    regn[11:20] = b"\x2a" + umid[16:24]
    encoded_stem = stem.encode("utf-8")
    regn[60] = len(encoded_stem)
    regn[61:61 + len(encoded_stem)] = encoded_stem
    struct.pack_into("<Q", regn, 44, 0 if production_regn else time_reference)
    struct.pack_into("<Q", regn, 52, time_reference)
    if not production_regn:
        regn[76:80] = b"\x10\x20\x30\x40"
        regn[84:88] = b"\x10\x20\x30\x40"
    compact_umid = bytearray(24)
    compact_umid[7:16] = b"\x2a" + umid[16:24]
    body = bytearray(b"WAVE")
    body.extend(
        riff_chunk(
            b"fmt ",
            struct.pack("<HHIIHH", 1, 1, 48_000, 96_000, 2, 16),
        )
    )
    body.extend(riff_chunk(b"bext", bext))
    body.extend(riff_chunk(b"minf", minf))
    body.extend(riff_chunk(b"regn", regn))
    body.extend(riff_chunk(b"umid", compact_umid))
    body.extend(riff_chunk(b"data", b"\x01\x02\x03\x04"))
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)


def write_pcm_wave(path, data, sample_rate=48_000, channels=1, bits=16):
    block_align = channels * (bits // 8)
    body = bytearray(b"WAVE")
    body.extend(
        riff_chunk(
            b"fmt ",
            struct.pack(
                "<HHIIHH",
                1,
                channels,
                sample_rate,
                sample_rate * block_align,
                block_align,
                bits,
            ),
        )
    )
    body.extend(riff_chunk(b"data", data))
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)


def read_chunk(path, wanted):
    data = path.read_bytes()
    position = 12
    while position + 8 <= len(data):
        chunk_id = data[position:position + 4]
        size = struct.unpack_from("<I", data, position + 4)[0]
        payload = data[position + 8:position + 8 + size]
        if chunk_id == wanted:
            return payload
        position += 8 + size + (size & 1)
    raise AssertionError(f"Missing chunk {wanted!r}")


class RelinkTests(unittest.TestCase):
    def test_relink_reassembles_false_block_inside_1001_identity(self):
        session, source_umid = make_session()
        _, media_entries, _, _, _ = session._validated_physical_audio_catalog()
        media_info = next(
            child for child in media_entries[0].items
            if isinstance(child, PTBlock) and child.content_type == 0x1001
        )
        payload = bytearray(media_info.items[0])
        false_block = PTBlock(0x06, 0x0000, 0)
        false_block.items = []
        serialized, _ = false_block.to_bytes(False, 0)
        self.assertEqual(len(serialized), 7)
        payload[7:14] = serialized
        media_info.items = [payload[:7], false_block, payload[14:]]

        with tempfile.TemporaryDirectory() as directory:
            audio_dir = Path(directory)
            source = audio_dir / "Audio 1_01.wav"
            destination = audio_dir / "Audio 1_02.wav"
            write_pro_tools_wave(source, source.stem, source_umid)

            session.relink_clip(
                "Audio 1",
                "Audio 1_01",
                1_000,
                "Audio 1_02",
                source,
                destination,
            )

            _, updated_entries, _, _, _ = (
                session._validated_physical_audio_catalog()
            )
            cloned_info = next(
                child for child in updated_entries[-1].items
                if isinstance(child, PTBlock) and child.content_type == 0x1001
            )
            self.assertEqual(len(cloned_info.items), 1)
            self.assertEqual(len(cloned_info.items[0]), 31)

    def test_relink_preserves_zero_filename_record_suffix_variant(self):
        session, source_umid = make_session(
            filename_record_suffix=b"\x00" * 4,
        )
        with tempfile.TemporaryDirectory() as directory:
            audio_dir = Path(directory)
            source = audio_dir / "Audio 1_01.wav"
            destination = audio_dir / "Audio 1_02.wav"
            write_pro_tools_wave(source, source.stem, source_umid)

            session.relink_clip(
                "Audio 1",
                "Audio 1_01",
                1_000,
                "Audio 1_02",
                source,
                destination,
            )

            _, entries, name_block, names, insertion_offset = (
                session._validated_physical_audio_catalog()
            )
            self.assertEqual(len(entries), 2)
            self.assertEqual(names, ["Audio 1_01.wav", "Audio 1_02.wav"])
            self.assertEqual(
                name_block.items[0][insertion_offset - 4:insertion_offset],
                b"\x00" * 4,
            )

    def test_relink_clones_media_and_retargets_only_the_requested_placement(self):
        session, source_umid = make_session(source_time_reference=800)
        with tempfile.TemporaryDirectory() as directory:
            audio_dir = Path(directory)
            source = audio_dir / "Audio 1_01.wav"
            destination = audio_dir / "Audio 1_02.wav"
            write_pro_tools_wave(source, source.stem, source_umid, time_reference=800)

            result = session.relink_clip(
                "Audio 1",
                "Audio 1_01",
                1_000,
                "REGION TWO",
                source,
                destination,
            )

            self.assertEqual(result["clip_id"], 1)
            self.assertEqual(result["physical_index"], 1)
            self.assertTrue(destination.exists())
            self.assertEqual(read_chunk(source, b"data"), read_chunk(destination, b"data"))
            source_bext = read_chunk(source, b"bext")
            destination_bext = read_chunk(destination, b"bext")
            self.assertNotEqual(source_bext[348:412], destination_bext[348:412])
            self.assertEqual(source_bext[288:294], destination_bext[288:294])
            self.assertNotEqual(source_bext[294:298], destination_bext[294:298])
            self.assertEqual(source_bext[298:320], destination_bext[298:320])
            self.assertEqual(
                struct.unpack_from("<Q", destination_bext, 338)[0],
                1_000,
            )
            destination_regn = read_chunk(destination, b"regn")
            self.assertEqual(struct.unpack_from("<Q", destination_regn, 44)[0], 1_000)
            self.assertEqual(struct.unpack_from("<Q", destination_regn, 52)[0], 1_000)
            self.assertEqual(destination_regn[76:80], destination_regn[84:88])
            self.assertNotEqual(destination_regn[76:80], b"\x10\x20\x30\x40")
            self.assertEqual(len(session.get_clips()), 2)
            timeline = session.get_timeline_clips(include_fades=False)
            self.assertEqual(timeline[0]["clip_name"], "REGION TWO")
            self.assertEqual(timeline[0]["physical_filename"], "Audio 1_02.wav")
            clip_list = session._root_blocks(0x262A)[0]
            definitions = [
                item for item in clip_list.items
                if isinstance(item, PTBlock) and item.content_type == 0x2629
            ]
            new_name_block = next(
                item for item in definitions[1].items
                if isinstance(item, PTBlock) and item.content_type == 0x2628
            )
            new_payload = new_name_block.items[0]
            timestamp_offset = 4 + len(b"REGION TWO") + 8
            self.assertEqual(
                struct.unpack_from("<II", new_payload, timestamp_offset),
                (1_000, 1_000),
            )
            new_identity = next(
                item for item in definitions[1].items
                if isinstance(item, (bytes, bytearray)) and len(item) == 48
            )
            self.assertEqual(new_identity[22], 0xFF)
            self.assertNotEqual(new_identity[23:39], bytes(range(16)))
            new_media_entry = [
                item for item in session._root_blocks(0x1004)[0].items
                if isinstance(item, PTBlock) and item.content_type == 0x1003
            ][1]
            detail = next(
                item for item in new_media_entry.items
                if isinstance(item, PTBlock) and item.content_type == 0x2106
            )
            detail_header = next(
                item for item in detail.items
                if isinstance(item, (bytes, bytearray)) and len(item) == 151
            )
            self.assertEqual(struct.unpack_from("<I", detail_header, 100)[0], 1_000)
            first_filetime = struct.unpack_from("<Q", detail_header, 29)[0]
            second_filetime = struct.unpack_from("<Q", detail_header, 105)[0]
            self.assertEqual(first_filetime % 10_000_000, 0)
            self.assertEqual(second_filetime, first_filetime - 10_000_000)
            _, _, updated_names, _, insertion_offset = (
                session._validated_physical_audio_catalog()
            )
            counter_offsets = session._decode_1004_catalog_trailer(
                updated_names.items[0], insertion_offset, 2
            )
            self.assertEqual(
                [
                    struct.unpack_from("<I", updated_names.items[0], offset)[0]
                    for offset in counter_offsets
                ],
                [3],
            )

    def test_relink_supports_environment_independent_multilevel_catalog(self):
        session, source_umid = make_session(
            catalog_labels=("VIDEO", "Exports", "test ottoalign")
        )
        with tempfile.TemporaryDirectory() as directory:
            audio_dir = Path(directory)
            source = audio_dir / "Audio 1_01.wav"
            destination = audio_dir / "Audio 1_02.wav"
            write_pro_tools_wave(source, source.stem, source_umid)

            session.relink_clip(
                "Audio 1",
                "Audio 1_01",
                1_000,
                "Audio 1_02",
                source,
                destination,
            )

            _, _, updated_names, names, insertion_offset = (
                session._validated_physical_audio_catalog()
            )
            counter_offsets = session._decode_1004_catalog_trailer(
                updated_names.items[0], insertion_offset, 2
            )
            self.assertEqual(names, ["Audio 1_01.wav", "Audio 1_02.wav"])
            self.assertEqual(
                [
                    struct.unpack_from("<I", updated_names.items[0], offset)[0]
                    for offset in counter_offsets
                ],
                [3, 4, 5],
            )
            self.assertTrue(destination.exists())

    def test_relink_writes_a_uint32_physical_media_index(self):
        session, source_umid = make_session(physical_count=256)
        with tempfile.TemporaryDirectory() as directory:
            audio_dir = Path(directory)
            source = audio_dir / "Audio 1_01.wav"
            destination = audio_dir / "Audio 1_02.wav"
            write_pro_tools_wave(source, source.stem, source_umid)

            result = session.relink_clip(
                "Audio 1",
                "Audio 1_01",
                1_000,
                "Audio 1_02",
                source,
                destination,
            )

            self.assertEqual(result["physical_index"], 256)
            clip_list = session._root_blocks(0x262A)[0]
            definitions = [
                item for item in clip_list.items
                if isinstance(item, PTBlock) and item.content_type == 0x2629
            ]
            media_link = next(
                item for item in definitions[-1].items
                if isinstance(item, (bytes, bytearray)) and len(item) == 104
            )
            self.assertEqual(struct.unpack_from("<I", media_link, 96)[0], 256)
            self.assertEqual(
                session.get_timeline_clips(include_fades=False)[0][
                    "physical_filename"
                ],
                "Audio 1_02.wav",
            )

    def test_relink_reassembles_a_false_block_inside_fixed_identity(self):
        session, source_umid = make_session()
        definition = next(
            item for item in session._root_blocks(0x262A)[0].items
            if isinstance(item, PTBlock) and item.content_type == 0x2629
        )
        identity = bytearray(definition.items[1])
        false_header = PTBlock(0x00AB, 0, 0)
        definition.items[1:2] = [
            identity[:37],
            false_header,
            identity[44:],
        ]

        with tempfile.TemporaryDirectory() as directory:
            audio_dir = Path(directory)
            source = audio_dir / "Audio 1_01.wav"
            destination = audio_dir / "Audio 1_02.wav"
            write_pro_tools_wave(source, source.stem, source_umid)

            result = session.relink_clip(
                "Audio 1",
                "Audio 1_01",
                1_000,
                "Audio 1_02",
                source,
                destination,
            )

            self.assertEqual(result["physical_filename"], "Audio 1_02.wav")
            self.assertTrue(destination.exists())
            cloned = [
                item for item in session._root_blocks(0x262A)[0].items
                if isinstance(item, PTBlock) and item.content_type == 0x2629
            ][-1]
            self.assertEqual(
                [
                    len(item) for item in cloned.items
                    if isinstance(item, (bytes, bytearray))
                ],
                [48, 104],
            )

    def test_relink_can_install_compatible_rendered_pcm(self):
        session, source_umid = make_session()
        with tempfile.TemporaryDirectory() as directory:
            audio_dir = Path(directory)
            source = audio_dir / "Audio 1_01.wav"
            replacement = audio_dir / "render.wav"
            destination = audio_dir / "Audio 1_02.wav"
            write_pro_tools_wave(source, source.stem, source_umid)
            write_pcm_wave(replacement, b"\x10\x20\x30\x40")

            session.relink_clip(
                "Audio 1",
                "Audio 1_01",
                1_000,
                "Audio 1_02",
                source,
                destination,
                replacement_audio_path=replacement,
            )

            self.assertEqual(read_chunk(source, b"data"), b"\x01\x02\x03\x04")
            self.assertEqual(
                read_chunk(destination, b"data"),
                b"\x10\x20\x30\x40",
            )

    def test_incompatible_rendered_pcm_rolls_back_relink(self):
        session, source_umid = make_session()
        with tempfile.TemporaryDirectory() as directory:
            audio_dir = Path(directory)
            source = audio_dir / "Audio 1_01.wav"
            replacement = audio_dir / "render.wav"
            destination = audio_dir / "Audio 1_02.wav"
            write_pro_tools_wave(source, source.stem, source_umid)
            write_pcm_wave(replacement, b"\x10\x20", sample_rate=44_100)

            with self.assertRaisesRegex(ValueError, "PCM format"):
                session.relink_clip(
                    "Audio 1",
                    "Audio 1_01",
                    1_000,
                    "Audio 1_02",
                    source,
                    destination,
                    replacement_audio_path=replacement,
                )

            self.assertFalse(destination.exists())
            self.assertEqual(len(session.get_clips()), 1)

    def test_relink_preserves_verified_production_virtual_clip_geometry(self):
        for source_offset in (0, 120_000):
            with self.subTest(source_offset=source_offset):
                session, source_umid = make_session(
                    source_time_reference=800,
                    virtual_source_offset=source_offset,
                )
                source_definition = [
                    item for item in session._root_blocks(0x262A)[0].items
                    if isinstance(item, PTBlock) and item.content_type == 0x2629
                ][0]
                source_name_block = next(
                    item for item in source_definition.items
                    if isinstance(item, PTBlock) and item.content_type == 0x2628
                )
                source_payload = bytes(source_name_block.items[0])
                source_name_length = struct.unpack_from("<I", source_payload, 0)[0]
                source_tail = source_payload[4 + source_name_length:]

                with tempfile.TemporaryDirectory() as directory:
                    audio_dir = Path(directory)
                    source = audio_dir / "Audio 1_01.wav"
                    destination = audio_dir / "Audio 1_02.wav"
                    write_pro_tools_wave(
                        source,
                        source.stem,
                        source_umid,
                        time_reference=800,
                    )

                    session.relink_clip(
                        "Audio 1",
                        "Audio 1_01",
                        1_000,
                        "Audio 1_02",
                        source,
                        destination,
                    )

                    definitions = [
                        item for item in session._root_blocks(0x262A)[0].items
                        if isinstance(item, PTBlock)
                        and item.content_type == 0x2629
                    ]
                    new_name_block = next(
                        item for item in definitions[-1].items
                        if isinstance(item, PTBlock)
                        and item.content_type == 0x2628
                    )
                    new_payload = bytes(new_name_block.items[0])
                    new_name_length = struct.unpack_from("<I", new_payload, 0)[0]
                    self.assertEqual(
                        new_payload[4 + new_name_length:],
                        source_tail,
                    )
                    self.assertEqual(
                        session._decode_audio_clip_payload(new_payload)[
                            "src_offset"
                        ],
                        source_offset,
                    )
                    destination_bext = read_chunk(destination, b"bext")
                    self.assertEqual(
                        struct.unpack_from("<Q", destination_bext, 338)[0],
                        800,
                    )
                    new_media_entry = [
                        item for item in session._root_blocks(0x1004)[0].items
                        if isinstance(item, PTBlock)
                        and item.content_type == 0x1003
                    ][-1]
                    detail = next(
                        item for item in new_media_entry.items
                        if isinstance(item, PTBlock)
                        and item.content_type == 0x2106
                    )
                    detail_header = next(
                        item for item in detail.items
                        if isinstance(item, (bytes, bytearray))
                        and len(item) == 142
                    )
                    self.assertEqual(
                        struct.unpack_from("<I", detail_header, 91)[0],
                        800,
                    )

    def test_invalid_wav_stem_length_rolls_back_session_and_file(self):
        session, source_umid = make_session()
        with tempfile.TemporaryDirectory() as directory:
            audio_dir = Path(directory)
            source = audio_dir / "Audio 1_01.wav"
            destination = audio_dir / "DIFFERENT.wav"
            write_pro_tools_wave(source, source.stem, source_umid)

            with self.assertRaisesRegex(ValueError, "same UTF-8 byte length"):
                session.relink_clip(
                    "Audio 1",
                    "Audio 1_01",
                    1_000,
                    "Audio 1_02",
                    source,
                    destination,
                )

            self.assertFalse(destination.exists())
            self.assertEqual(len(session.get_clips()), 1)

    def test_a_production_abbreviated_regn_stem_is_preserved(self):
        session, source_umid = make_session()
        with tempfile.TemporaryDirectory() as directory:
            audio_dir = Path(directory)
            source = audio_dir / "Audio 1_01.wav"
            destination = audio_dir / "Audio 1_02.wav"
            write_pro_tools_wave(
                source,
                "Audio_Production_Rdy.A1",
                source_umid,
                production_regn=True,
            )

            session.relink_clip(
                "Audio 1",
                "Audio 1_01",
                1_000,
                "Audio 1_02",
                source,
                destination,
            )

            destination_regn = read_chunk(destination, b"regn")
            embedded_length = destination_regn[60]
            self.assertEqual(
                destination_regn[61:61 + embedded_length],
                b"Audio_Production_Rdy.A1",
            )
            self.assertEqual(
                struct.unpack_from("<QQ", destination_regn, 44),
                (0, 1_000),
            )
            self.assertEqual(len(session.get_clips()), 2)

    def test_relink_requires_an_exact_track_and_timestamp_target(self):
        session, source_umid = make_session()
        with tempfile.TemporaryDirectory() as directory:
            audio_dir = Path(directory)
            source = audio_dir / "Audio 1_01.wav"
            destination = audio_dir / "Audio 1_02.wav"
            write_pro_tools_wave(source, source.stem, source_umid)

            with self.assertRaisesRegex(ValueError, "exactly one placement"):
                session.relink_clip(
                    "Audio 1",
                    "Audio 1_01",
                    999,
                    "Audio 1_02",
                    source,
                    destination,
                )

            self.assertFalse(destination.exists())
            self.assertEqual(len(session.get_clips()), 1)

    def test_relink_rejects_an_inconsistent_trailer_counter(self):
        session, source_umid = make_session()
        _, _, name_block, _, insertion_offset = (
            session._validated_physical_audio_catalog()
        )
        payload = bytearray(name_block.items[0])
        struct.pack_into("<I", payload, insertion_offset + 30, 99)
        name_block.items[0] = payload

        with tempfile.TemporaryDirectory() as directory:
            audio_dir = Path(directory)
            source = audio_dir / "Audio 1_01.wav"
            destination = audio_dir / "Audio 1_02.wav"
            write_pro_tools_wave(source, source.stem, source_umid)

            with self.assertRaisesRegex(ValueError, "relink counters"):
                session.relink_clip(
                    "Audio 1",
                    "Audio 1_01",
                    1_000,
                    "Audio 1_02",
                    source,
                    destination,
                )

            self.assertFalse(destination.exists())
            self.assertEqual(len(session.get_clips()), 1)


if __name__ == "__main__":
    unittest.main()
