import os
import struct
import tempfile
import unittest

from pt_api import ProToolsSession


ZERO_TICKS = 0xE8D4A51000


def generic_block(block_type, content_type, payload=b""):
    return (
        b"\x5a"
        + struct.pack("<H", block_type)
        + struct.pack("<I", 2 + len(payload))
        + struct.pack("<H", content_type)
        + payload
    )


def tempo_payload(changes):
    """Encode a ``0x2028`` payload: ``"Tempo"`` header + 61-byte records."""
    count = len(changes)
    header = bytearray()
    header += b"Tempo"
    header += b"\x02\x00"
    header += struct.pack("<I", count * 61 + 4)
    header += struct.pack("<I", count)
    header += b"\x00\x00\x00\x00\x00"
    header += b"Constr"
    header += b"\x01\x00"
    header += struct.pack("<I", 46)
    header += b"TMS"
    header += b"\x01\x00"
    header += struct.pack("<I", 20)
    header += bytes.fromhex("0001a404b0000000")
    assert len(header) == 49
    body = bytearray()
    for ticks, bpm in changes:
        record = bytearray(61)
        record[0:5] = (ZERO_TICKS + ticks).to_bytes(5, "little")
        struct.pack_into("<d", record, 10, bpm)
        body += record
    return bytes(header + body)


SESSION_METADATA = (
    generic_block(1, 0x1028, b"\x00\x00" + struct.pack("<I", 48_000))
    + generic_block(1, 0x204D, bytearray(44))
)


def session_bytes(middle=b""):
    header = bytearray(b"\x03" + b"0010111100101011" + b"\x00\x01\x00")
    body = SESSION_METADATA + middle
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
        + struct.pack("<I", 2)
        + struct.pack("<H", 0x0002)
    )
    return bytes(header + first + body + pointer_table)


def load(payload):
    with tempfile.TemporaryDirectory() as directory:
        path = os.path.join(directory, "session.ptx")
        with open(path, "wb") as output:
            output.write(payload)
        return ProToolsSession(path)


def tempo_block(changes):
    return generic_block(2, 0x2718, generic_block(3, 0x2028, tempo_payload(changes)))


class TempoMapTests(unittest.TestCase):
    def test_no_tempo_container_returns_empty(self):
        self.assertEqual(load(session_bytes()).get_tempo_map(), [])

    def test_constant_tempo(self):
        session = load(session_bytes(tempo_block([(0, 120.0)])))
        self.assertEqual(
            session.get_tempo_map(),
            [{"position_ticks": 0, "beats": 0.0, "bpm": 120.0}],
        )
        self.assertEqual(session.beats_to_samples(16), 384_000.0)
        self.assertEqual(session.samples_to_beats(384_000), 16.0)

    def test_tempo_automation_is_ordered(self):
        session = load(session_bytes(tempo_block([(0, 90.0), (960_000, 120.0)])))
        changes = session.get_tempo_map()
        self.assertEqual([change["bpm"] for change in changes], [90.0, 120.0])
        self.assertEqual(changes[1]["beats"], 1.0)
        # 1 beat at 90 BPM then 1 more at 120 BPM, at 48 kHz.
        self.assertEqual(session.beats_to_samples(1), 32_000.0)
        self.assertEqual(session.beats_to_samples(2), 56_000.0)
        self.assertEqual(session.samples_to_beats(56_000), 2.0)

    def test_unsupported_header_is_refused(self):
        session = load(session_bytes(generic_block(2, 0x2718, generic_block(
            3, 0x2028, b"XXXXX" + bytearray(88)
        ))))
        with self.assertRaisesRegex(ValueError, "header"):
            session.get_tempo_map()


if __name__ == "__main__":
    unittest.main()
