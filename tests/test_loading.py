import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock

from pt_api import PTBlock, ProToolsSession, unxor_session, xor_session


PREFIX_0002 = bytes.fromhex("0000000104000100")


def generic_block(block_type, content_type, payload=b""):
    return (
        b"\x5a"
        + struct.pack("<H", block_type)
        + struct.pack("<I", 2 + len(payload))
        + struct.pack("<H", content_type)
        + payload
    )


SESSION_METADATA = (
    generic_block(1, 0x1028, b"\x00\x00" + struct.pack("<I", 48_000))
    + generic_block(1, 0x204D, b"\x09")
)


def valid_xor_data(length=64, xor_type=0x01, xor_value=0x00):
    if length < 20:
        raise ValueError("length must include the PTX header")
    data = bytearray(index & 0xFF for index in range(length))
    data[0] = 0x03
    data[1:17] = b"0" * 16
    data[0x11] = 0x00
    data[0x12] = xor_type
    data[0x13] = xor_value
    return data


def minimal_ptx(pointer_payload=b"", trailing=b"", middle=b"", include_metadata=True):
    header = bytearray(b"\x03" + b"0010111100101011" + b"\x00\x01\x00")
    body = (SESSION_METADATA if include_metadata else b"") + middle
    pointer_table_offset = 0x14 + 11 + len(body)
    first = (
        b"\x5a"
        + struct.pack("<H", 0x0001)
        + struct.pack("<I", 4)
        + struct.pack("<I", pointer_table_offset)
    )
    pointer_table = (
        b"\x5a"
        + struct.pack("<H", 0x0002)
        + struct.pack("<I", 2 + len(pointer_payload))
        + struct.pack("<H", 0x0002)
        + pointer_payload
    )
    return header + first + body + pointer_table + trailing


class LoadingValidationTests(unittest.TestCase):
    def load_bytes(self, payload):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "session.ptx")
            with open(path, "wb") as output:
                output.write(payload)
            return ProToolsSession(path)

    def test_minimal_valid_envelope_loads(self):
        session = self.load_bytes(minimal_ptx())

        self.assertEqual(len(session.root_items), 4)
        self.assertEqual(session.root_items[-1].content_type, 0x0002)
        self.assertEqual(session.sample_rate, 48_000)
        self.assertEqual(session.frame_rate_enum, 0x09)

    def test_invalid_header_signature_and_endianness_are_rejected(self):
        bad_signature = minimal_ptx()
        bad_signature[1] = ord("X")
        with self.assertRaisesRegex(ValueError, "header signature"):
            self.load_bytes(bad_signature)

        bad_endianness = minimal_ptx()
        bad_endianness[0x11] = 2
        with self.assertRaisesRegex(ValueError, "endianness"):
            self.load_bytes(bad_endianness)

    def test_big_endian_session_is_rejected_before_block_parsing(self):
        big_endian = minimal_ptx()
        big_endian[0x11] = 1
        big_endian[0x14:] = b"X" * (len(big_endian) - 0x14)

        with self.assertRaisesRegex(ValueError, "Big-endian PTX sessions"):
            self.load_bytes(big_endian)

    def test_0001_must_point_to_the_unique_final_0002(self):
        wrong_pointer = minimal_ptx()
        struct.pack_into("<I", wrong_pointer, 0x1B, 0x20)
        with self.assertRaisesRegex(ValueError, "does not point"):
            self.load_bytes(wrong_pointer)

        with self.assertRaisesRegex(ValueError, "final root block"):
            self.load_bytes(minimal_ptx(trailing=b"X"))

    def test_0001_pointer_low_word_cannot_impersonate_a_content_root(self):
        base_offset = 0x14 + 11 + len(SESSION_METADATA)
        cases = (
            (0x0002, lambda session: session.root_items[-1].content_type),
            (0x1028, lambda session: session.sample_rate),
            (0x1054, lambda session: session.get_tracks()),
            (0x262A, lambda session: session.get_clips()),
        )

        for low_word, operation in cases:
            with self.subTest(low_word=hex(low_word)):
                pointer_offset = 0x10000 + low_word
                padding = b"\x00" * (pointer_offset - base_offset)
                session = self.load_bytes(minimal_ptx(middle=padding))

                self.assertEqual(session.root_items[0].content_type, low_word)
                operation(session)

    def test_pointer_low_word_collision_survives_repeated_noop_saves(self):
        pointer_offset = 0x10000 + 0x262A
        base_offset = 0x14 + 11 + len(SESSION_METADATA)
        original = minimal_ptx(
            middle=b"\x00" * (pointer_offset - base_offset)
        )

        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, "source.ptx")
            first = os.path.join(directory, "first.ptx")
            second = os.path.join(directory, "second.ptx")
            with open(source, "wb") as output:
                output.write(original)

            session = ProToolsSession(source)
            session.save(first)
            self.assertEqual(session.get_clips(), [])
            session.save(second)

            with open(first, "rb") as output:
                first_bytes = output.read()
            with open(second, "rb") as output:
                second_bytes = output.read()
            self.assertEqual(first_bytes, original)
            self.assertEqual(second_bytes, original)

    def test_dangling_or_malformed_standard_pointer_is_rejected(self):
        dangling = (
            struct.pack(">H", 1)
            + PREFIX_0002 + struct.pack("<I", 999) + b"\x00\x00\x00"
        )
        with self.assertRaisesRegex(ValueError, "unknown block offset"):
            self.load_bytes(minimal_ptx(dangling))

        malformed = (
            struct.pack(">H", 1)
            + PREFIX_0002 + struct.pack("<I", 0x14) + b"\x00\x00\x01"
        )
        with self.assertRaisesRegex(ValueError, "Invalid standard"):
            self.load_bytes(minimal_ptx(malformed))

    def test_pointer_run_counter_is_required_and_must_match(self):
        record = PREFIX_0002 + struct.pack("<I", 0x14) + b"\x00\x00\x00"

        with self.assertRaisesRegex(ValueError, "Missing 0x0002 pointer-run counter"):
            self.load_bytes(minimal_ptx(record))
        with self.assertRaisesRegex(ValueError, "pointer-run count"):
            self.load_bytes(minimal_ptx(struct.pack(">H", 2) + record))

    def test_unknown_encryption_type_has_a_value_error(self):
        data = minimal_ptx()
        data[0x12] = 0x7F
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "session.ptx")
            with open(path, "wb") as output:
                output.write(data)
            with self.assertRaisesRegex(ValueError, "Unknown encryption type"):
                unxor_session(path)

    def test_missing_source_preserves_file_not_found_error(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = os.path.join(directory, "missing.ptx")

            with self.assertRaises(FileNotFoundError):
                unxor_session(missing)
            with self.assertRaises(FileNotFoundError):
                ProToolsSession(missing)

    def test_xor_round_trip_preserves_the_input_for_both_key_modes(self):
        for xor_type, xor_value in ((0x01, 0x35), (0x05, 0x0B)):
            with self.subTest(xor_type=xor_type):
                # Mode 0x05 advances its XOR index only every 4096 bytes.
                original = valid_xor_data(5000, xor_type, xor_value)
                before = bytes(original)

                with tempfile.TemporaryDirectory() as directory:
                    path = os.path.join(directory, "session.ptx")
                    xor_session(original, path)

                    self.assertEqual(bytes(original), before)
                    with open(path, "rb") as encrypted_file:
                        self.assertNotEqual(encrypted_file.read(), before)
                    self.assertEqual(unxor_session(path), original)

    def test_short_xor_write_preserves_destination_and_removes_temp_file(self):
        data = valid_xor_data(64, 0x01, 0x35)

        class ShortWriter:
            def __init__(self, descriptor):
                self.descriptor = descriptor

            def __enter__(self):
                return self

            def write(self, payload):
                return len(payload) - 1

            def __exit__(self, exc_type, exc_value, traceback):
                os.close(self.descriptor)

        with tempfile.TemporaryDirectory() as directory:
            destination = os.path.join(directory, "session.ptx")
            with open(destination, "wb") as output:
                output.write(b"ORIGINAL")

            with mock.patch(
                "pt_api.os.fdopen",
                side_effect=lambda descriptor, mode: ShortWriter(descriptor),
            ):
                with self.assertRaisesRegex(OSError, "Short write"):
                    xor_session(data, destination)

            with open(destination, "rb") as output:
                self.assertEqual(output.read(), b"ORIGINAL")
            self.assertEqual(os.listdir(directory), ["session.ptx"])

    def test_xor_path_and_data_validation(self):
        with self.assertRaises(TypeError):
            xor_session("not bytes", "output.ptx")
        with self.assertRaisesRegex(ValueError, "20-byte header"):
            xor_session(bytearray(19), "output.ptx")
        with self.assertRaisesRegex(ValueError, "cannot be empty"):
            xor_session(valid_xor_data(20), "")

    def test_pathlike_support_and_bytes_path_rejection_are_consistent(self):
        valid_data = valid_xor_data(20)

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.ptx"
            destination = Path(directory) / "saved.ptx"
            source.write_bytes(minimal_ptx())

            session = ProToolsSession(source)
            session.save(destination)
            self.assertEqual(session.file_path, str(destination.resolve()))
            self.assertTrue(destination.is_file())

            for operation in (
                lambda: unxor_session(b"session.ptx"),
                lambda: xor_session(valid_data, b"session.ptx"),
                lambda: ProToolsSession(b"session.ptx"),
                lambda: session.save(b"session.ptx"),
            ):
                with self.subTest(operation=operation):
                    with self.assertRaisesRegex(TypeError, "string path"):
                        operation()

    def test_session_stores_an_absolute_source_path(self):
        previous_directory = os.getcwd()
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.ptx"
            source.write_bytes(minimal_ptx())
            try:
                os.chdir(directory)
                session = ProToolsSession("source.ptx")
            finally:
                os.chdir(previous_directory)

            self.assertEqual(session.file_path, str(source.resolve()))

    def test_invalid_header_is_rejected_before_block_parsing(self):
        invalid = minimal_ptx()
        invalid[1] = ord("X")
        invalid[0x14:] = b"X" * (len(invalid) - 0x14)

        with mock.patch.object(
            ProToolsSession,
            "_parse_block",
            side_effect=AssertionError("Parser must not run for an invalid header."),
        ) as parser:
            with self.assertRaisesRegex(ValueError, "header signature"):
                self.load_bytes(invalid)
            parser.assert_not_called()

    def test_zero_size_child_at_container_end_is_parsed_and_pointable(self):
        empty_child = b"\x5a" + struct.pack("<H", 1) + struct.pack("<I", 0)
        container = generic_block(1, 0x1000, empty_child)
        child_offset = 0x14 + 11 + len(SESSION_METADATA) + 9
        pointer = (
            struct.pack(">H", 1)
            + PREFIX_0002
            + struct.pack("<I", child_offset)
            + b"\x00\x00\x00"
        )

        session = self.load_bytes(minimal_ptx(pointer, middle=container))

        parsed_container = next(
            item
            for item in session.root_items
            if isinstance(item, PTBlock) and item.content_type == 0x1000
        )
        self.assertEqual(parsed_container.content_type, 0x1000)
        self.assertEqual(len(parsed_container.items), 1)
        self.assertEqual(parsed_container.items[0].original_size, 0)
        self.assertEqual(parsed_container.items[0].original_offset, child_offset)

    def test_size_one_false_header_remains_raw_on_noop_save(self):
        false_header = (
            b"\x5a" + struct.pack("<H", 1) + struct.pack("<I", 1) + b"\x99"
        )
        original = minimal_ptx(middle=generic_block(1, 0x1000, false_header))

        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, "source.ptx")
            destination = os.path.join(directory, "saved.ptx")
            with open(source, "wb") as output:
                output.write(original)

            session = ProToolsSession(source)
            session.save(destination)

            with open(destination, "rb") as output:
                self.assertEqual(output.read(), original)

    def test_fixed_record_payload_does_not_parse_a_false_child_block(self):
        false_child = generic_block(1, 0x9999, b"PAYLOAD")
        event_payload = b"\x00" * 7 + false_child + b"\x00" * 8
        original = minimal_ptx(middle=generic_block(10, 0x104F, event_payload))

        session = self.load_bytes(original)

        event = next(
            item
            for item in session.root_items
            if isinstance(item, PTBlock) and item.content_type == 0x104F
        )
        self.assertEqual(event.items, [event_payload])

    def test_library_load_and_save_do_not_write_to_stdout(self):
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, "source.ptx")
            destination = os.path.join(directory, "saved.ptx")
            with open(source, "wb") as output:
                output.write(minimal_ptx())

            with mock.patch(
                "builtins.print",
                side_effect=AssertionError("Library API wrote to stdout."),
            ):
                ProToolsSession(source).save(destination)

            self.assertTrue(os.path.isfile(destination))

    def test_successful_save_refreshes_data_and_the_0001_pointer(self):
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, "source.ptx")
            destination = os.path.join(directory, "saved.ptx")
            with open(source, "wb") as output:
                output.write(minimal_ptx())

            session = ProToolsSession(source)
            session.root_items.insert(-1, bytearray(b"SHIFT-0002"))
            session.save(destination)

            saved_data = unxor_session(destination)
            self.assertEqual(session.data, saved_data)
            pointer_bytes, _ = session.root_items[0].to_bytes(False, 0x14)
            self.assertEqual(pointer_bytes, saved_data[0x14:0x1F])
            session._validate_session_envelope()

    def test_excessive_block_nesting_is_rejected_cleanly(self):
        nested = b"\x5a" + struct.pack("<H", 1) + struct.pack("<I", 0)
        for _ in range(130):
            nested = generic_block(1, 0x1000, nested)

        with self.assertRaisesRegex(ValueError, "nesting exceeds"):
            self.load_bytes(minimal_ptx(middle=nested))

    def test_missing_or_duplicate_session_metadata_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unique root 0x1028"):
            self.load_bytes(minimal_ptx(include_metadata=False))

        duplicate_sample_rate = generic_block(
            1, 0x1028, b"\x00\x00" + struct.pack("<I", 48_000)
        )
        with self.assertRaisesRegex(ValueError, "unique root 0x1028"):
            self.load_bytes(minimal_ptx(middle=duplicate_sample_rate))

    def test_invalid_or_zero_sample_rate_is_rejected(self):
        frame_rate = generic_block(1, 0x204D, b"\x09")
        malformed = generic_block(1, 0x1028, b"\x00") + frame_rate
        with self.assertRaisesRegex(ValueError, "sample-rate payload"):
            self.load_bytes(
                minimal_ptx(middle=malformed, include_metadata=False)
            )

        zero = generic_block(1, 0x1028, b"\x00" * 6) + frame_rate
        with self.assertRaisesRegex(ValueError, "must be positive"):
            self.load_bytes(minimal_ptx(middle=zero, include_metadata=False))


if __name__ == "__main__":
    unittest.main()
