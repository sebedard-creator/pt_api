import copy
import logging
import math
import os
import struct
from collections.abc import Mapping


logger = logging.getLogger(__name__)

__version__ = "1.4.0"


_TEMPLATE_AUDIO_IEEE_FLOAT_SUBFORMAT = bytes.fromhex(
    "03000000 0000 1000 8000 00aa00389b71"
)
_WINDOWS_FILETIME_EPOCH = 116_444_736_000_000_000


# Native Pro Tools import-template layouts verified as complete writer profiles.
# These dictionaries intentionally name only fields that the builder changes;
# every other byte is retained from the selected template prototype.
_AUDIO_IMPORT_TEMPLATE_PROFILES = (
    {
        "name": "native_float_15_142",
        "clip_prefix": b"\x00\x00\x30\x04\x00",
        "clip_length_offset": 5,
        "clip_length_width": 3,
        "clip_time_reference_offsets": (8,),
        "media_info_length": 15,
        "media_length_offset": 6,
        "media_length_width": 3,
        "media_zero_offset": 9,
        "media_zero_length": 5,
        "media_float_code_offset": 14,
        "detail_header_length": 142,
        "detail_tail_length": 58,
        "detail_filetime_offset": 29,
        "detail_time_reference_offset": 91,
        "detail_previous_filetime_offset": 96,
        "detail_uuid_offset": 126,
    },
    {
        "name": "native_float_31_151_u32",
        "clip_prefix": b"\x00\x00\x40\x44\x00",
        "clip_length_offset": 5,
        "clip_length_width": 4,
        "clip_time_reference_offsets": (9, 13),
        "media_info_length": 31,
        "media_length_offset": 6,
        "media_length_width": 4,
        "media_zero_offset": 10,
        "media_zero_length": 4,
        "media_float_code_offset": 14,
        "detail_header_length": 151,
        "detail_tail_length": 58,
        "detail_filetime_offset": 29,
        "detail_time_reference_offset": 100,
        "detail_previous_filetime_offset": 105,
        "detail_uuid_offset": 135,
    },
)


def _coerce_string_path(value, parameter_name):
    """Normalize a path-like value while rejecting ambiguous bytes paths."""
    try:
        path = os.fspath(value)
    except TypeError as exc:
        raise TypeError(
            f"{parameter_name} must be a string or path-like object."
        ) from exc
    if not isinstance(path, str):
        raise TypeError(f"{parameter_name} must resolve to a string path.")
    if not path:
        raise ValueError(f"{parameter_name} cannot be empty.")
    return path


def _scan_riff_wave_chunks(stream):
    """Return validated RIFF/WAVE chunk payload offsets keyed by chunk ID."""
    stream.seek(0, os.SEEK_END)
    file_size = stream.tell()
    stream.seek(0)
    header = stream.read(12)
    if len(header) != 12 or header[:4] != b"RIFF" or header[8:12] != b"WAVE":
        raise ValueError("Source audio must be a little-endian RIFF/WAVE file.")
    declared_size = struct.unpack_from("<I", header, 4)[0] + 8
    if declared_size != file_size:
        raise ValueError("RIFF/WAVE size does not match the physical file size.")

    chunks = {}
    position = 12
    while position < file_size:
        if position + 8 > file_size:
            raise ValueError("Truncated RIFF/WAVE chunk header.")
        stream.seek(position)
        chunk_header = stream.read(8)
        chunk_id = chunk_header[:4]
        chunk_size = struct.unpack_from("<I", chunk_header, 4)[0]
        payload_offset = position + 8
        payload_end = payload_offset + chunk_size
        padded_end = payload_end + (chunk_size & 1)
        if payload_end > file_size or padded_end > file_size:
            raise ValueError("Truncated RIFF/WAVE chunk payload.")
        chunks.setdefault(chunk_id, []).append((payload_offset, chunk_size))
        position = padded_end
    if position != file_size:
        raise ValueError("Invalid RIFF/WAVE chunk alignment.")
    return chunks


def _unique_wave_chunk(stream, chunks, chunk_id, minimum_size, label):
    """Read one required RIFF chunk after validating its cardinality and size."""
    matches = chunks.get(chunk_id, [])
    if len(matches) != 1 or matches[0][1] < minimum_size:
        raise ValueError(f"{label} WAV requires one valid {chunk_id.decode('ascii')} chunk.")
    offset, size = matches[0]
    stream.seek(offset)
    payload = stream.read(size)
    if len(payload) != size:
        raise ValueError(f"Truncated {label.lower()} WAV {chunk_id.decode('ascii')} chunk.")
    return payload


def _inspect_template_audio_wave(file_path, maximum_length_samples=0xFFFFFF):
    """Validate one WAV supported by the template-based audio writer."""
    if (
        isinstance(maximum_length_samples, bool)
        or not isinstance(maximum_length_samples, int)
        or maximum_length_samples < 1
        or maximum_length_samples > 0xFFFFFFFF
    ):
        raise ValueError("Invalid template-audio profile duration limit.")
    file_path = os.path.abspath(_coerce_string_path(file_path, "audio_path"))
    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"Audio WAV does not exist: {file_path}")

    filename = os.path.basename(file_path)
    if not filename.lower().endswith(".wav"):
        raise ValueError("Template-based audio import supports WAV files only.")
    stem = os.path.splitext(filename)[0]
    if not stem or "\x00" in stem:
        raise ValueError("Audio WAV stem must be non-empty and contain no NUL.")

    with open(file_path, "rb") as stream:
        chunks = _scan_riff_wave_chunks(stream)
        fmt_payload = _unique_wave_chunk(
            stream, chunks, b"fmt ", 40, "Audio source"
        )
        (
            audio_format,
            channels,
            sample_rate,
            byte_rate,
            block_align,
            bits_per_sample,
        ) = struct.unpack_from("<HHIIHH", fmt_payload, 0)
        if (
            audio_format != 0xFFFE
            or struct.unpack_from("<H", fmt_payload, 16)[0] < 22
            or struct.unpack_from("<H", fmt_payload, 18)[0] != 32
            or fmt_payload[24:40] != _TEMPLATE_AUDIO_IEEE_FLOAT_SUBFORMAT
        ):
            raise ValueError(
                "Audio WAV must use 32-bit IEEE float WAVE_EXTENSIBLE audio."
            )
        if (
            channels != 1
            or sample_rate != 48_000
            or byte_rate != 192_000
            or block_align != 4
            or bits_per_sample != 32
        ):
            raise ValueError("Audio WAV must be mono, 48 kHz and 32-bit float.")

        data_matches = chunks.get(b"data", [])
        if len(data_matches) != 1:
            raise ValueError("Audio source WAV requires one data chunk.")
        _, data_size = data_matches[0]
        if data_size == 0 or data_size % block_align:
            raise ValueError("Audio WAV data size must contain complete mono samples.")
        length_samples = data_size // block_align
        if length_samples > maximum_length_samples:
            raise ValueError(
                "Audio WAV exceeds the selected template profile clip-length "
                "limit."
            )

        fact_payload = _unique_wave_chunk(
            stream, chunks, b"fact", 4, "Audio source"
        )
        if struct.unpack_from("<I", fact_payload, 0)[0] != length_samples:
            raise ValueError("Audio WAV fact sample count does not match its data chunk.")

        bext_payload = _unique_wave_chunk(
            stream, chunks, b"bext", 412, "Audio source"
        )
        bwf_time_reference = struct.unpack_from("<Q", bext_payload, 338)[0]
        if bwf_time_reference > 0xFFFFFFFF:
            raise ValueError(
                "Audio BWF time reference exceeds the verified UInt32 PTX layout."
            )
        umid = bytes(bext_payload[348:412])
        if len(umid) != 64 or not any(umid[:32]):
            raise ValueError("Audio BWF requires a non-empty 32-byte basic UMID.")

    try:
        filename.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("Audio WAV filename must be valid UTF-8.") from exc

    return {
        "source_path": file_path,
        "source_filename": filename,
        "length_samples": length_samples,
        "bwf_time_reference": bwf_time_reference,
        "umid": umid,
    }


def _prepare_template_audio_clips(clip_specs, maximum_length_samples=0xFFFFFF):
    """Validate an ordered iterable of generic template-audio descriptors."""
    if isinstance(clip_specs, (str, bytes, os.PathLike, Mapping)):
        raise TypeError("clip_specs must be an iterable of descriptor mappings.")
    try:
        specs = list(clip_specs)
    except TypeError as exc:
        raise TypeError("clip_specs must be an iterable of descriptor mappings.") from exc
    if not specs:
        raise ValueError("At least one audio clip descriptor is required.")

    prepared = []
    track_names = []
    for order, spec in enumerate(specs, start=1):
        if not isinstance(spec, Mapping):
            raise TypeError("Each clip descriptor must be a mapping.")
        if "audio_path" not in spec or "track_name" not in spec:
            raise ValueError(
                "Each clip descriptor requires audio_path and track_name."
            )
        metadata = _inspect_template_audio_wave(
            spec["audio_path"], maximum_length_samples
        )

        track_name = spec["track_name"]
        if not isinstance(track_name, str):
            raise TypeError("Descriptor track_name must be a string.")
        if not track_name or "\x00" in track_name:
            raise ValueError("Descriptor track_name must be non-empty and contain no NUL.")

        physical_filename = spec.get(
            "physical_filename", metadata["source_filename"]
        )
        if not isinstance(physical_filename, str):
            raise TypeError("Descriptor physical_filename must be a string.")
        if (
            not physical_filename
            or "\x00" in physical_filename
            or os.path.basename(physical_filename) != physical_filename
            or not physical_filename.lower().endswith(".wav")
        ):
            raise ValueError(
                "Descriptor physical_filename must be one .wav basename without NUL."
            )

        default_clip_name = f"{os.path.splitext(physical_filename)[0]}.A1"
        clip_name = spec.get("clip_name", default_clip_name)
        if not isinstance(clip_name, str):
            raise TypeError("Descriptor clip_name must be a string.")
        if not clip_name or "\x00" in clip_name:
            raise ValueError("Descriptor clip_name must be non-empty and contain no NUL.")

        placement_start = spec.get(
            "placement_start_samples", metadata["bwf_time_reference"]
        )
        if isinstance(placement_start, bool) or not isinstance(placement_start, int):
            raise TypeError("placement_start_samples must be an integer.")
        if placement_start < 0 or placement_start > 0xFFFFFFFFFFFFFFFF:
            raise ValueError("placement_start_samples must fit UInt64.")
        placement_end = placement_start + metadata["length_samples"]
        if placement_end > 0xFFFFFFFFFFFFFFFF:
            raise ValueError("Audio placement end exceeds UInt64.")

        try:
            track_name.encode("utf-8")
            physical_filename.encode("utf-8")
            clip_name.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError("Descriptor text fields must be valid UTF-8.") from exc

        metadata.update({
            "order": order,
            "track": track_name,
            "physical_filename": physical_filename,
            "clip_name": clip_name,
            "start_samples": placement_start,
            "end_samples": placement_end,
        })
        prepared.append(metadata)
        if track_name not in track_names:
            track_names.append(track_name)

    filename_keys = [item["physical_filename"].casefold() for item in prepared]
    if len(set(filename_keys)) != len(filename_keys):
        raise ValueError("Output WAV basenames must be unique case-insensitively.")
    clip_keys = [item["clip_name"].casefold() for item in prepared]
    if len(set(clip_keys)) != len(clip_keys):
        raise ValueError("Generated clip names must be unique case-insensitively.")
    return prepared, track_names


def _wave_pcm_format_signature(stream, chunks, label):
    """Return the PCM fields that must match before replacing a data chunk."""
    matches = chunks.get(b"fmt ", [])
    if len(matches) != 1 or matches[0][1] < 16:
        raise ValueError(f"{label} WAV requires one valid fmt chunk.")
    offset, size = matches[0]
    stream.seek(offset)
    payload = stream.read(size)
    if len(payload) != size:
        raise ValueError(f"Truncated {label.lower()} WAV fmt chunk.")
    audio_format, channels, sample_rate, byte_rate, block_align, bits = (
        struct.unpack_from("<HHIIHH", payload, 0)
    )
    if audio_format == 0xFFFE:
        if (
            size < 40
            or struct.unpack_from("<H", payload, 16)[0] < 22
            or struct.unpack_from("<I", payload, 24)[0] != 1
        ):
            raise ValueError(f"{label} WAV must use PCM audio.")
    elif audio_format != 1:
        raise ValueError(f"{label} WAV must use PCM audio.")
    return channels, sample_rate, byte_rate, block_align, bits


def _replace_wave_data_chunk(destination_stream, destination_chunks, replacement_path):
    """Copy compatible rendered PCM into a cloned Pro Tools WAV in place."""
    destination_data = destination_chunks.get(b"data", [])
    if len(destination_data) != 1:
        raise ValueError("Destination Pro Tools WAV requires one data chunk.")
    destination_signature = _wave_pcm_format_signature(
        destination_stream, destination_chunks, "Destination"
    )

    with open(replacement_path, "rb") as replacement_stream:
        replacement_chunks = _scan_riff_wave_chunks(replacement_stream)
        replacement_data = replacement_chunks.get(b"data", [])
        if len(replacement_data) != 1:
            raise ValueError("Replacement WAV requires one data chunk.")
        replacement_signature = _wave_pcm_format_signature(
            replacement_stream, replacement_chunks, "Replacement"
        )
        if replacement_signature != destination_signature:
            raise ValueError(
                "Replacement WAV PCM format does not match the source WAV."
            )
        destination_offset, destination_size = destination_data[0]
        replacement_offset, replacement_size = replacement_data[0]
        if replacement_size != destination_size:
            raise ValueError(
                "Replacement WAV data size does not match the source WAV."
            )

        replacement_stream.seek(replacement_offset)
        destination_stream.seek(destination_offset)
        remaining = replacement_size
        while remaining:
            chunk = replacement_stream.read(min(1024 * 1024, remaining))
            if not chunk:
                raise ValueError("Truncated replacement WAV data chunk.")
            destination_stream.write(chunk)
            remaining -= len(chunk)


def _clone_pro_tools_wave(
    source_path,
    temporary_path,
    new_stem,
    expected_source_time_reference,
    new_time_reference,
    replacement_audio_path=None,
):
    """Clone one verified Pro Tools WAV while assigning a new media identity."""
    import datetime
    import secrets
    import shutil
    import string

    shutil.copyfile(source_path, temporary_path)
    with open(temporary_path, "r+b") as stream:
        chunks = _scan_riff_wave_chunks(stream)
        required = {b"bext": 412, b"minf": 16, b"regn": 92, b"umid": 24}
        selected = {}
        for chunk_id, minimum_size in required.items():
            matches = chunks.get(chunk_id, [])
            if len(matches) != 1 or matches[0][1] < minimum_size:
                label = chunk_id.decode("ascii")
                raise ValueError(f"A unique verified {label} WAV chunk is required.")
            selected[chunk_id] = matches[0]

        bext_offset, _ = selected[b"bext"]
        stream.seek(bext_offset + 288)
        source_originator_reference = bytearray(stream.read(32))
        if len(source_originator_reference) != 32:
            raise ValueError("Invalid Pro Tools BWF originator-reference payload.")
        stream.seek(bext_offset + 338)
        source_time_reference_raw = stream.read(8)
        if len(source_time_reference_raw) != 8:
            raise ValueError("Invalid Pro Tools BWF time-reference payload.")
        source_time_reference = struct.unpack("<Q", source_time_reference_raw)[0]
        if source_time_reference != expected_source_time_reference:
            raise ValueError(
                "Source WAV time reference does not match the PTX clip definition."
            )
        stream.seek(bext_offset + 348)
        source_umid = stream.read(64)
        if len(source_umid) != 64 or not any(source_umid[:32]):
            raise ValueError("Invalid Pro Tools BWF UMID payload.")
        new_umid = bytearray(source_umid)
        new_umid[17:20] = secrets.token_bytes(3)
        new_umid[24:26] = secrets.token_bytes(2)
        file_identifier = b"\x2a" + bytes(new_umid[16:24])

        regn_offset, regn_size = selected[b"regn"]
        stream.seek(regn_offset + 60)
        source_name_length_raw = stream.read(1)
        if len(source_name_length_raw) != 1:
            raise ValueError("Invalid Pro Tools regn filename length.")
        source_name_length = source_name_length_raw[0]
        if 61 + source_name_length > regn_size:
            raise ValueError("Truncated Pro Tools regn filename.")
        stream.seek(regn_offset + 61)
        source_stem = stream.read(source_name_length)
        try:
            decoded_source_stem = source_stem.decode("utf-8", "strict")
        except UnicodeDecodeError as exc:
            raise ValueError("Source WAV regn stem must be valid UTF-8.") from exc
        expected_source_stem = os.path.splitext(os.path.basename(source_path))[0]
        try:
            new_stem_bytes = new_stem.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError("New WAV stem must be valid UTF-8.") from exc
        replace_regn_stem = decoded_source_stem == expected_source_stem
        if replace_regn_stem:
            if len(new_stem_bytes) != source_name_length:
                raise ValueError(
                    "New WAV stem must have the same UTF-8 byte length as the source."
                )
            if decoded_source_stem == new_stem:
                raise ValueError("New WAV stem must differ from the source stem.")

        stream.seek(regn_offset + 44)
        regn_time_references = stream.read(16)
        if len(regn_time_references) != 16:
            raise ValueError(
                "Source WAV regn time references do not match its BWF time reference."
            )
        first_regn_reference, second_regn_reference = struct.unpack(
            "<QQ", regn_time_references
        )
        if (
            first_regn_reference == source_time_reference
            and second_regn_reference == source_time_reference
        ):
            regn_reference_mode = "duplicated"
        elif (
            first_regn_reference == 0
            and second_regn_reference == source_time_reference
        ):
            regn_reference_mode = "production"
        else:
            raise ValueError(
                "Source WAV regn time references do not match its BWF time reference."
            )

        new_media_token = None
        if 61 + source_name_length <= 76:
            stream.seek(regn_offset + 76)
            first_source_pointer = stream.read(8)
            stream.seek(regn_offset + 84)
            second_source_pointer = stream.read(8)
            if (
                len(first_source_pointer) != 8
                or len(second_source_pointer) != 8
                or first_source_pointer != second_source_pointer
            ):
                raise ValueError("Invalid Pro Tools regn media token pair.")
            first_source_token = first_source_pointer[:4]
            new_media_token = secrets.token_bytes(4)
            while new_media_token == first_source_token:
                new_media_token = secrets.token_bytes(4)

        now_utc = datetime.datetime.now(datetime.timezone.utc)
        filetime_epoch = datetime.datetime(1601, 1, 1, tzinfo=datetime.timezone.utc)
        filetime = int((now_utc - filetime_epoch).total_seconds() * 10_000_000)
        rounded_filetime = filetime - (filetime % 10_000_000)
        prior_filetime = max(0, rounded_filetime - 10_000_000)
        local_now = datetime.datetime.now()
        alphabet = string.ascii_letters + string.digits
        source_originator_reference[6:10] = "".join(
            secrets.choice(alphabet) for _ in range(4)
        ).encode("ascii")

        stream.seek(bext_offset + 288)
        stream.write(source_originator_reference)
        stream.seek(bext_offset + 320)
        stream.write(local_now.strftime("%Y-%m-%d%H:%M:%S").encode("ascii"))
        stream.seek(bext_offset + 338)
        stream.write(struct.pack("<Q", new_time_reference))
        stream.seek(bext_offset + 348)
        stream.write(new_umid)

        minf_offset, _ = selected[b"minf"]
        stream.seek(minf_offset)
        stream.write(struct.pack("<Q", filetime))

        stream.seek(regn_offset + 11)
        stream.write(file_identifier)
        stream.seek(regn_offset + 44)
        if regn_reference_mode == "duplicated":
            stream.write(struct.pack("<QQ", new_time_reference, new_time_reference))
        else:
            stream.write(struct.pack("<QQ", 0, new_time_reference))
        if replace_regn_stem:
            stream.seek(regn_offset + 61)
            stream.write(new_stem_bytes)
        if new_media_token is not None:
            stream.seek(regn_offset + 76)
            stream.write(new_media_token)
            stream.seek(regn_offset + 84)
            stream.write(new_media_token)

        umid_offset, _ = selected[b"umid"]
        stream.seek(umid_offset + 7)
        stream.write(file_identifier)

        if replacement_audio_path is not None:
            _replace_wave_data_chunk(stream, chunks, replacement_audio_path)

    return {
        "file_identifier": file_identifier,
        "umid": bytes(new_umid),
        "rounded_filetime": rounded_filetime,
        "prior_filetime": prior_filetime,
        "filetime": filetime,
    }


def _validate_ptx_header(data):
    """Validate the unencrypted 20-byte PTX envelope before body processing."""
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("Session data must be bytes or bytearray.")
    if len(data) < 0x14:
        raise ValueError("Session data must contain at least the 20-byte header.")
    if data[0] != 0x03 or any(
        byte not in (ord("0"), ord("1")) for byte in data[1:17]
    ):
        raise ValueError("Invalid PTX header signature or version field.")
    if data[0x11] not in (0x00, 0x01):
        raise ValueError("Invalid PTX endianness flag.")
    if data[0x12] not in (0x01, 0x05):
        raise ValueError(
            f"Unknown encryption type (xor_type: 0x{data[0x12]:02x})"
        )


def gen_xor_delta(xor_value: int, mul: int, negative: bool) -> int:
    for i in range(256):
        if ((i * mul) & 0xff) == xor_value:
            if negative:
                return (256 - i) & 0xff
            else:
                return i
    raise ValueError(f"No valid XOR delta found for xor_value=0x{xor_value:02x}, mul={mul}")


def _transform_session_xor(data) -> bytearray:
    """Return an XOR-transformed copy while preserving its 20-byte header."""
    _validate_ptx_header(data)

    transformed = bytearray(data)
    xor_type = transformed[0x12]
    xor_value = transformed[0x13]
    if xor_type == 0x01:
        xor_delta = gen_xor_delta(xor_value, 53, False)
    elif xor_type == 0x05:
        xor_delta = gen_xor_delta(xor_value, 11, True)

    xxor = bytearray((i * xor_delta) & 0xFF for i in range(256))
    for index in range(0x14, len(transformed)):
        xor_index = index & 0xFF if xor_type == 0x01 else (index >> 12) & 0xFF
        transformed[index] ^= xxor[xor_index]
    return transformed


def unxor_session(file_path: str) -> bytearray:
    file_path = _coerce_string_path(file_path, "file_path")

    with open(file_path, "rb") as f:
        data = f.read()
    if len(data) < 0x14:
        raise ValueError("File is too small to be a valid Pro Tools session.")
    return _transform_session_xor(data)


def xor_session(data: bytearray, out_path: str):
    """Atomically write an XOR-transformed copy of decrypted session data."""
    encrypted = _transform_session_xor(data)
    out_path = _coerce_string_path(out_path, "out_path")

    import tempfile

    destination = os.path.abspath(out_path)
    destination_dir = os.path.dirname(destination)
    if not os.path.isdir(destination_dir):
        raise FileNotFoundError(f"Output directory does not exist: {destination_dir}")

    descriptor = None
    temp_path = None
    try:
        descriptor, temp_path = tempfile.mkstemp(
            prefix=f".{os.path.basename(destination)}.",
            suffix=".tmp",
            dir=destination_dir,
        )
        with os.fdopen(descriptor, "wb") as output:
            descriptor = None
            written = output.write(encrypted)
            if written != len(encrypted):
                raise OSError(
                    f"Short write while encrypting session: {written}/{len(encrypted)} bytes."
                )
        os.replace(temp_path, destination)
        temp_path = None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temp_path is not None:
            try:
                os.remove(temp_path)
            except FileNotFoundError:
                pass

class TimecodeEngine:
    def __init__(self, sample_rate, frame_rate_enum):
        if (
            isinstance(sample_rate, bool)
            or not isinstance(sample_rate, (int, float))
        ):
            raise ValueError("Sample rate must be a finite positive number.")
        try:
            sample_rate_is_finite = math.isfinite(sample_rate)
        except OverflowError:
            sample_rate_is_finite = False
        if not sample_rate_is_finite or sample_rate <= 0:
            raise ValueError("Sample rate must be a finite positive number.")
        if sample_rate > 0xFFFFFFFF:
            raise ValueError("Sample rate exceeds the PTX UInt32 range.")
        if isinstance(frame_rate_enum, bool) or not isinstance(frame_rate_enum, int):
            raise TypeError("Frame-rate enum must be an integer.")
        self.sample_rate = sample_rate
        self.frame_rate_enum = frame_rate_enum

    def get_frame_rate(self):
        # Enum mapping based on block 0x204d payload offset 0
        # 0x01 = 24 fps
        # 0x09 = 23.976 fps
        # 0x05 = 29.97 DF fps
        if self.frame_rate_enum == 0x01:
            return 24.0, False # fps, is_drop_frame
        elif self.frame_rate_enum == 0x09:
            return 24000 / 1001, False
        elif self.frame_rate_enum == 0x05:
            return 30000 / 1001, True
        else:
            raise ValueError(f"Unsupported frame rate enum: {hex(self.frame_rate_enum)}. The API cannot accurately calculate timecodes for this frame rate yet.")

    def _nominal_fps(self):
        if self.frame_rate_enum in (0x01, 0x09):
            return 24
        if self.frame_rate_enum == 0x05:
            return 30
        self.get_frame_rate()  # Raises the public unsupported-rate error.

    def _validate_components(self, hh, mm, ss, ff, *, position):
        components = (hh, mm, ss, ff)
        if any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in components
        ):
            raise TypeError("Timecode components must be integers.")
        nominal_fps = self._nominal_fps()
        if (
            hh < 0 or mm < 0 or mm > 59
            or ss < 0 or ss > 59 or ff < 0 or ff >= nominal_fps
        ):
            raise ValueError("Invalid timecode components.")

        _, is_drop_frame = self.get_frame_rate()
        if (
            position
            and is_drop_frame
            and mm % 10 != 0
            and ss == 0
            and ff < 2
        ):
            raise ValueError("Timecode falls on a dropped frame number.")
        return nominal_fps

    @staticmethod
    def _round_nonnegative(value):
        return int(math.floor(value + 0.5))

    def samples_to_timecode(self, samples):
        if isinstance(samples, bool) or not isinstance(samples, int):
            raise TypeError("Sample position must be an integer.")
        if samples < 0:
            raise ValueError("Sample position cannot be negative.")
        actual_fps, is_df = self.get_frame_rate()
        nominal_fps = self._nominal_fps()
            
        try:
            total_seconds = samples / self.sample_rate

            if not is_df:
                total_frames = self._round_nonnegative(total_seconds * actual_fps)
                ff = total_frames % nominal_fps
                ss = (total_frames // nominal_fps) % 60
                mm = (total_frames // (nominal_fps * 60)) % 60
                hh = total_frames // (nominal_fps * 3600)
            else:
                # Drop frame logic (29.97)
                frames = self._round_nonnegative(total_seconds * actual_fps)
                d = frames // 17982
                m = frames % 17982
                frames += 18 * d
                if m >= 2:
                    frames += 2 * ((m - 2) // 1798)

                ff = frames % 30
                ss = (frames // 30) % 60
                mm = (frames // 1800) % 60
                hh = frames // 108000
        except OverflowError as exc:
            raise ValueError("Sample position exceeds the timecode conversion range.") from exc
            
        return f"{hh:02d}:{mm:02d}:{ss:02d}:{ff:02d}"

    def timecode_to_samples(self, hh, mm, ss, ff):
        nominal_fps = self._validate_components(hh, mm, ss, ff, position=True)
        actual_fps, is_df = self.get_frame_rate()

        try:
            if not is_df:
                total_frames = (hh * 3600 + mm * 60 + ss) * nominal_fps + ff
                total_seconds = total_frames / actual_fps
                return self._round_nonnegative(total_seconds * self.sample_rate)
            else:
                # Drop frame logic
                # For 29.97 DF, frame rate is exactly 30000/1001
                # 2 frames are dropped every minute, except every 10th minute
                total_minutes = hh * 60 + mm

                # The nominal frame count if it were exactly 30 fps
                nominal_frames = (total_minutes * 60 + ss) * 30 + ff

                # Subtract dropped frames
                dropped = 2 * total_minutes - 2 * (total_minutes // 10)

                actual_frames = nominal_frames - dropped

                total_seconds = actual_frames / (30000 / 1001)
                return self._round_nonnegative(total_seconds * self.sample_rate)
        except OverflowError as exc:
            raise ValueError("Timecode position exceeds the sample conversion range.") from exc

    def duration_to_samples(self, hh, mm, ss, ff):
        """Convert a duration without applying drop-frame label exclusions."""
        nominal_fps = self._validate_components(hh, mm, ss, ff, position=False)
        actual_fps, _ = self.get_frame_rate()
        try:
            total_frames = (hh * 3600 + mm * 60 + ss) * nominal_fps + ff
            return self._round_nonnegative(
                (total_frames / actual_fps) * self.sample_rate
            )
        except OverflowError as exc:
            raise ValueError("Timecode duration exceeds the sample conversion range.") from exc

ZMARK = 0x5a
MAX_PTX_BLOCK_DEPTH = 128
# These supported payload formats are byte records, not nested TLV streams.
# Treating an incidental 0x5A plus plausible size as a child would make later
# pointer relocation depend on arbitrary timestamps, gain values, or names.
FLAT_PTX_CONTENT_TYPES = frozenset({
    0x0002,  # Root pointer table.
    0x1028,  # Session sample rate.
    0x104F,  # Timeline event payload.
    0x204D,  # Session frame rate.
    0x260A,  # Volume automation envelope.
    0x262F,  # Fade geometry.
    0x2637,  # Clip Gain point dictionary.
})

def u_endian_read2(buf, offset, is_bigendian):
    fmt = ">H" if is_bigendian else "<H"
    return struct.unpack_from(fmt, buf, offset)[0]

def u_endian_read4(buf, offset, is_bigendian):
    fmt = ">I" if is_bigendian else "<I"
    return struct.unpack_from(fmt, buf, offset)[0]


def _merge_unique_offset_mapping(target, source):
    duplicates = set(target).intersection(source)
    if duplicates:
        duplicate = min(duplicates)
        raise ValueError(f"Duplicate original block offset: 0x{duplicate:08x}.")
    target.update(source)


class PTBlock:
    def __init__(self, block_type, content_type, original_size=0):
        self.block_type = block_type
        self.content_type = content_type
        self.original_size = original_size
        self.items = [] # list of bytearray or PTBlock
        self.original_offset = 0

    def to_bytes(self, is_bigendian=False, base_offset=0):
        if not isinstance(is_bigendian, bool):
            raise TypeError("is_bigendian must be a boolean.")
        if isinstance(base_offset, bool) or not isinstance(base_offset, int):
            raise TypeError("base_offset must be an integer.")
        if base_offset < 0 or base_offset > 0xFFFFFFFF:
            raise ValueError("base_offset must fit an unsigned 32-bit offset.")
        return self._to_bytes(is_bigendian, base_offset, set())

    def _to_bytes(self, is_bigendian, base_offset, ancestors):
        if len(ancestors) > MAX_PTX_BLOCK_DEPTH:
            raise ValueError(
                f"PTBlock nesting exceeds {MAX_PTX_BLOCK_DEPTH} levels."
            )
        identity = id(self)
        if identity in ancestors:
            raise ValueError("Cycle detected in PTBlock tree.")
        ancestors.add(identity)
        try:
            return self._to_bytes_impl(is_bigendian, base_offset, ancestors)
        finally:
            ancestors.remove(identity)

    def _to_bytes_impl(self, is_bigendian, base_offset, ancestors):
        if isinstance(self.block_type, bool) or not isinstance(self.block_type, int):
            raise TypeError("block_type must be an integer.")
        if self.block_type < 0 or self.block_type > 0xFF:
            raise ValueError("block_type must fit the supported 8-bit PTX range.")
        if isinstance(self.content_type, bool) or not isinstance(self.content_type, int):
            raise TypeError("content_type must be an integer.")
        if self.content_type < 0 or self.content_type > 0xFFFF:
            raise ValueError("content_type must fit an unsigned 16-bit value.")
        if isinstance(self.original_size, bool) or not isinstance(self.original_size, int):
            raise TypeError("original_size must be an integer.")
        if self.original_size < 0 or self.original_size > 0xFFFFFFFF:
            raise ValueError("original_size must fit an unsigned 32-bit value.")
        if isinstance(self.original_offset, bool) or not isinstance(self.original_offset, int):
            raise TypeError("original_offset must be an integer.")
        if self.original_offset < -1 or self.original_offset > 0xFFFFFFFF:
            raise ValueError("original_offset must be -1 or an unsigned 32-bit value.")
        if base_offset < 0 or base_offset > 0xFFFFFFFF:
            raise OverflowError("Serialized block offset exceeds UInt32.")

        payload = bytearray()
        mapping = {}

        # Calculate start of payload
        current_offset = base_offset + 9

        for item in self.items:
            if isinstance(item, PTBlock):
                child_bytes, child_mapping = item._to_bytes(
                    is_bigendian, current_offset, ancestors
                )
                payload.extend(child_bytes)
                _merge_unique_offset_mapping(mapping, child_mapping)
                current_offset += len(child_bytes)
            else:
                if not isinstance(item, (bytes, bytearray)):
                    raise TypeError("PTBlock items must be bytes, bytearray, or PTBlock.")
                payload.extend(item)
                current_offset += len(item)

        fmt_type = ">H" if is_bigendian else "<H"
        fmt_size = ">I" if is_bigendian else "<I"
        fmt_ctype = ">H" if is_bigendian else "<H"

        if self.original_size == 0 and len(payload) == 0:
            size = 0
            header = bytearray([0x5a])
            header.extend(struct.pack(fmt_type, self.block_type))
            header.extend(struct.pack(fmt_size, size))
        else:
            size = len(payload) + 2
            if size > 0xFFFFFFFF:
                raise OverflowError("PTBlock payload exceeds the UInt32 size field.")
            header = bytearray([0x5a])
            header.extend(struct.pack(fmt_type, self.block_type))
            header.extend(struct.pack(fmt_size, size))
            header.extend(struct.pack(fmt_ctype, self.content_type))

        serialized_length = len(header) + len(payload)
        if base_offset + serialized_length > 0x100000000:
            raise OverflowError("Serialized PTBlock extends beyond the UInt32 file range.")

        if self.original_offset > 0:
            if self.original_offset in mapping:
                raise ValueError(
                    f"Duplicate original block offset: 0x{self.original_offset:08x}."
                )
            mapping[self.original_offset] = base_offset

        return header + payload, mapping


    def get_all_blocks(self, content_type=None):
        return self._get_all_blocks(content_type, set())

    def _get_all_blocks(self, content_type, ancestors):
        if len(ancestors) > MAX_PTX_BLOCK_DEPTH:
            raise ValueError(
                f"PTBlock nesting exceeds {MAX_PTX_BLOCK_DEPTH} levels."
            )
        identity = id(self)
        if identity in ancestors:
            raise ValueError("Cycle detected in PTBlock tree.")
        ancestors.add(identity)
        res = []
        try:
            if content_type is None or self.content_type == content_type:
                res.append(self)
            for item in self.items:
                if isinstance(item, PTBlock):
                    res.extend(item._get_all_blocks(content_type, ancestors))
            return res
        finally:
            ancestors.remove(identity)

class ProToolsSession:
    def __init__(self, file_path):
        file_path = os.path.abspath(
            _coerce_string_path(file_path, "file_path")
        )
        self.file_path = file_path
        logger.info("Loading %s...", file_path)
        self.data = unxor_session(file_path)

        if self.data[0x11] == 0x01:
            raise ValueError(
                "Big-endian PTX sessions are not supported by the payload API."
            )
        self.is_bigendian = False

        # Parse root blocks
        self.root_items = []

        pos = 0x14
        current_bytes = bytearray()

        while pos < len(self.data):
            if self.data[pos] == ZMARK:
                block, jump = self._parse_block(pos, len(self.data))
                if block:
                    if current_bytes:
                        self.root_items.append(current_bytes)
                        current_bytes = bytearray()
                    self.root_items.append(block)
                    pos += jump
                    continue
            current_bytes.append(self.data[pos])
            pos += 1

        if current_bytes:
            self.root_items.append(current_bytes)

        self._validate_session_envelope()
        self._parse_session_metadata()

        # Tracks original_offset values of blocks that were physically
        # removed from the tree (e.g. by delete_clip_group()). These no
        # longer appear in global_mapping at save() time, so their stale
        # 0x0002 records must be purged explicitly instead of left dangling.
        self._removed_offsets = []

    @staticmethod
    def _is_initial_pointer_block(item):
        """Identify the special first 0x0001 block represented generically."""
        return (
            isinstance(item, PTBlock)
            and item.block_type == 0x0001
            and item.original_size == 4
            and item.original_offset == 0x14
        )

    def _content_root_items(self):
        """Return roots excluding the special 0x0001 pointer pseudo-content."""
        if self.root_items and self._is_initial_pointer_block(self.root_items[0]):
            return self.root_items[1:]
        return self.root_items

    def _root_blocks(self, content_type):
        return [
            root for root in self._content_root_items()
            if isinstance(root, PTBlock) and root.content_type == content_type
        ]

    def _validate_session_envelope(self):
        """Validate the PTX header and the two root pointer structures."""
        _validate_ptx_header(self.data)

        if not self.root_items or not isinstance(self.root_items[0], PTBlock):
            raise ValueError("Missing initial 0x0001 pointer block.")
        pointer_block = self.root_items[0]
        if (
            pointer_block.original_offset != 0x14
            or pointer_block.block_type != 0x0001
            or pointer_block.original_size != 4
        ):
            raise ValueError("Invalid initial 0x0001 pointer block.")

        pointer_tables = self._root_blocks(0x0002)
        if len(pointer_tables) != 1:
            raise ValueError("A unique root 0x0002 pointer table is required.")
        pointer_table = pointer_tables[0]
        if self.root_items[-1] is not pointer_table:
            raise ValueError("The 0x0002 pointer table must be the final root block.")
        if pointer_table.original_offset + 7 + pointer_table.original_size != len(self.data):
            raise ValueError("The 0x0002 pointer table does not end at end-of-file.")

        pointer_format = ">I" if self.is_bigendian else "<I"
        declared_pointer = struct.unpack_from(pointer_format, self.data, 0x1B)[0]
        if declared_pointer != pointer_table.original_offset:
            raise ValueError(
                "The 0x0001 block does not point to the root 0x0002 table."
            )

        parsed_offsets = set()

        def collect_offsets(node):
            if not isinstance(node, PTBlock):
                return
            parsed_offsets.add(node.original_offset)
            for child in node.items:
                collect_offsets(child)

        for root in self.root_items:
            collect_offsets(root)

        pointer_payload = bytearray()
        for item in pointer_table.items:
            if not isinstance(item, (bytes, bytearray)):
                raise ValueError("Invalid nested block inside root 0x0002 table.")
            pointer_payload.extend(item)
        for _, pointer_position in self._validate_0002_record_layout(pointer_payload):
            target = struct.unpack_from(
                pointer_format, pointer_payload, pointer_position
            )[0]
            if target not in parsed_offsets:
                raise ValueError(
                    f"0x0002 record references unknown block offset {target}."
                )

    def _collect_offsets_recursive(self, node, out):
        """Recursively collect original_offset (>0) of node and all its
        PTBlock descendants. Used before removing a block from the tree so
        we know which 0x0002 records must be purged at save() time."""
        if isinstance(node, PTBlock):
            if node.original_offset and node.original_offset > 0:
                out.append(node.original_offset)
            for child in node.items:
                self._collect_offsets_recursive(child, out)

    def get_tracks(self):
        """Returns a list of all track names in the session."""
        return [name for _, name, _ in self._validated_main_playlists()]

    def _validated_main_playlists(self):
        """Return direct main playlists as (block, name, events) tuples."""
        track_maps = self._root_blocks(0x1054)
        if not track_maps:
            return []
        if len(track_maps) != 1:
            raise ValueError("Ambiguous session: multiple root 0x1054 track maps.")
        track_map = track_maps[0]
        if (
            not track_map.items
            or not isinstance(track_map.items[0], (bytes, bytearray))
            or len(track_map.items[0]) < 4
        ):
            raise ValueError("Invalid 0x1054 track counter payload.")

        playlists = [
            child for child in track_map.items
            if isinstance(child, PTBlock) and child.content_type == 0x1052
        ]
        declared_tracks = struct.unpack_from("<I", track_map.items[0], 0)[0]
        if declared_tracks != len(playlists):
            raise ValueError(
                f"Inconsistent 0x1054 track count: declared={declared_tracks}, "
                f"actual={len(playlists)}."
            )

        result = []
        for playlist in playlists:
            if (
                not playlist.items
                or not isinstance(playlist.items[0], (bytes, bytearray))
                or len(playlist.items[0]) < 8
            ):
                raise ValueError("Invalid 0x1052 playlist header.")
            header = playlist.items[0]
            name_length = struct.unpack_from("<I", header, 0)[0]
            count_offset = 4 + name_length
            if count_offset + 4 > len(header):
                raise ValueError("Invalid 0x1052 event counter offset.")
            try:
                name = header[4:count_offset].decode("utf-8").strip("\x00")
            except UnicodeDecodeError as exc:
                raise ValueError("Invalid UTF-8 track name.") from exc
            if not name:
                raise ValueError("Track name cannot be empty.")

            events = [
                child for child in playlist.items
                if isinstance(child, PTBlock) and child.content_type == 0x1050
            ]
            declared_events = struct.unpack_from("<I", header, count_offset)[0]
            if declared_events != len(events):
                raise ValueError(
                    f"Inconsistent 0x1052 event count for track '{name}': "
                    f"declared={declared_events}, actual={len(events)}."
                )
            result.append((playlist, name, events))

        return result

    def get_markers(self):
        """Returns a list of dictionaries describing session markers.
        Example: [{'index': 1, 'name': 'Intro', 'timecode': '00:00:10:00'}]
        """
        engine = TimecodeEngine(self.sample_rate, self.frame_rate_enum)
        markers = []
        seen_indices = set()

        for container in self._root_blocks(0x2030):
            layout = self._marker_container_layout(container)
            if layout is None:
                continue

            _, marker_blocks = layout
            for child in marker_blocks:
                if (
                    not child.items
                    or not isinstance(child.items[0], (bytes, bytearray))
                    or len(child.items[0]) < 26
                ):
                    raise ValueError("Invalid 0x2077 marker payload.")

                payload = child.items[0]
                m_idx = struct.unpack_from("<H", payload, 0)[0]
                nlen = struct.unpack_from("<I", payload, 6)[0]
                timestamp_offset = 10 + nlen
                if timestamp_offset + 16 > len(payload):
                    raise ValueError("Invalid 0x2077 marker name length.")
                try:
                    name = payload[10:timestamp_offset].decode('utf-8').strip('\x00')
                except UnicodeDecodeError as exc:
                    raise ValueError("Invalid UTF-8 marker name.") from exc
                tc_samples = struct.unpack_from("<q", payload, timestamp_offset)[0]

                if m_idx in seen_indices:
                    raise ValueError(f"Duplicate marker index: {m_idx}.")
                seen_indices.add(m_idx)
                markers.append({
                    'index': m_idx,
                    'name': name,
                    'timecode': engine.samples_to_timecode(tc_samples),
                })
        return markers

    def _marker_container_layout(self, container):
        """Validate a root 0x2030 marker ruler and return its direct markers.

        Root 0x2030 blocks are generic containers, so a non-marker-shaped block
        returns None. A marker-shaped block with an inconsistent count or layout
        is rejected instead of being mutated into a still-invalid session.
        """
        if not isinstance(container, PTBlock) or container.content_type != 0x2030:
            return None

        items = container.items
        if (
            len(items) == 1
            and isinstance(items[0], (bytes, bytearray))
            and len(items[0]) == 12
        ):
            count = struct.unpack_from("<I", items[0], 0)[0]
            if count != 0:
                raise ValueError("Invalid empty 0x2030 marker counter.")
            return 0, []

        has_marker = any(
            isinstance(item, PTBlock) and item.content_type == 0x2077
            for item in items
        )
        if not has_marker:
            return None

        if (
            len(items) < 3
            or not isinstance(items[0], (bytes, bytearray))
            or len(items[0]) != 4
            or not isinstance(items[-1], (bytes, bytearray))
            or len(items[-1]) != 8
            or any(
                not isinstance(item, PTBlock) or item.content_type != 0x2077
                for item in items[1:-1]
            )
        ):
            raise ValueError("Invalid 0x2030 marker container layout.")

        marker_blocks = items[1:-1]
        count = struct.unpack_from("<I", items[0], 0)[0]
        if count != len(marker_blocks):
            raise ValueError(
                f"Invalid 0x2030 marker count: declared {count}, "
                f"found {len(marker_blocks)}."
            )
        return count, marker_blocks

    def _decode_audio_clip_payload(self, payload):
        """Decode the name, length and source offset of one 0x2628 payload."""
        if not isinstance(payload, (bytes, bytearray)) or len(payload) < 6:
            raise ValueError("Invalid 0x2628 clip payload.")
        name_length = struct.unpack_from("<I", payload, 0)[0]
        attribute_offset = 4 + name_length
        if attribute_offset + 2 > len(payload):
            raise ValueError("Invalid 0x2628 clip name length.")
        try:
            name = payload[4:attribute_offset].decode("utf-8").strip("\x00")
        except UnicodeDecodeError as exc:
            raise ValueError("Invalid UTF-8 clip name.") from exc

        flags = struct.unpack_from("<H", payload, attribute_offset)[0]
        if attribute_offset + 5 > len(payload):
            raise ValueError("Truncated 0x2628 clip layout header.")
        width_selector = payload[attribute_offset + 2]
        width_by_selector = {
            0x10: 1,
            0x20: 2,
            0x30: 3,
            0x40: 4,
        }
        if width_selector not in width_by_selector:
            raise ValueError(
                f"Unsupported 0x2628 width selector: 0x{width_selector:02x}."
            )
        source_width_by_flags = {
            0x0000: 0,
            0x0001: 0,
            0x2000: 2,
            0x2001: 2,
            0x3000: 3,
            0x3001: 3,
            0x4001: 4,
        }
        if flags not in source_width_by_flags:
            raise ValueError(f"Unsupported 0x2628 clip flags: 0x{flags:04x}.")

        source_width = source_width_by_flags[flags]
        length_width = width_by_selector[width_selector]
        fields_offset = attribute_offset + 5
        fields_end = fields_offset + source_width + length_width
        if fields_end > len(payload):
            raise ValueError("Truncated source-offset/length clip payload.")
        source_offset = int.from_bytes(
            payload[fields_offset:fields_offset + source_width], "little"
        ) if source_width else 0
        length_offset = fields_offset + source_width
        length = int.from_bytes(
            payload[length_offset:length_offset + length_width], "little"
        )

        return {
            "name": name,
            "flags": flags,
            "length": length,
            "src_offset": source_offset,
        }

    def _decode_clip_group_payload(self, payload):
        """Decode the verified 0x5000 Clip Group 0x2628 layout."""
        if not isinstance(payload, (bytes, bytearray)) or len(payload) < 6:
            raise ValueError("Invalid Clip Group 0x2628 payload.")
        name_length = struct.unpack_from("<I", payload, 0)[0]
        attribute_offset = 4 + name_length
        if attribute_offset + 13 > len(payload):
            raise ValueError("Truncated Clip Group 0x2628 payload.")
        try:
            name = payload[4:attribute_offset].decode("utf-8").strip("\x00")
        except UnicodeDecodeError as exc:
            raise ValueError("Invalid UTF-8 Clip Group name.") from exc

        flags = struct.unpack_from("<H", payload, attribute_offset)[0]
        if flags != 0x5000:
            raise ValueError(f"Unsupported Clip Group flags: 0x{flags:04x}.")

        # The verified simple-group layout stores its complete span as a
        # 24-bit value at +10. Bytes +13 and +17 are absolute timestamps.
        length = int.from_bytes(
            payload[attribute_offset + 10:attribute_offset + 13], "little"
        )
        return {"name": name, "flags": flags, "length": length, "src_offset": 0}

    def _physical_audio_filenames(self):
        """Return deterministic disk and 0x1004 filename candidates."""
        import os
        import re

        disk_files = []
        file_path = getattr(self, "file_path", "")
        if file_path:
            audio_dir = os.path.join(os.path.dirname(file_path), "Audio Files")
            try:
                disk_files = sorted(
                    (
                        name for name in os.listdir(audio_dir)
                        if name.lower().endswith((".wav", ".aif", ".aiff"))
                    ),
                    key=str.casefold,
                )
            except OSError:
                disk_files = []

        metadata_files = []
        metadata_keys = set()
        for root in self._root_blocks(0x1004):
            for entry in root.items:
                if (
                    not isinstance(entry, PTBlock)
                    or entry.content_type != 0x103a
                    or not entry.items
                    or not isinstance(entry.items[0], (bytes, bytearray))
                ):
                    continue
                decoded = entry.items[0].decode("mac_roman")
                for match in re.finditer(
                    r"([^\x00\\/:]*?\.(?:wav|aif|aiff))", decoded, re.IGNORECASE
                ):
                    filename = "".join(
                        character for character in match.group(1) if ord(character) >= 32
                    ).strip()
                    key = filename.casefold()
                    if filename and key not in metadata_keys:
                        metadata_keys.add(key)
                        metadata_files.append(filename)

        return disk_files, metadata_files

    @staticmethod
    def _decode_1004_filename_payload(payload):
        """Decode the verified ordered WAV records from one 0x103a payload."""
        if not isinstance(payload, (bytes, bytearray)) or len(payload) < 17:
            raise ValueError("Invalid 0x103a physical-filename payload.")
        if payload[4] != 0x01:
            raise ValueError("Unsupported 0x103a physical-filename header.")
        folder_length = struct.unpack_from("<I", payload, 9)[0]
        folder_start = 13
        folder_end = folder_start + folder_length
        records_start = folder_end + 4
        if records_start > len(payload):
            raise ValueError("Truncated 0x103a Audio Files header.")
        try:
            folder_name = payload[folder_start:folder_end].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Invalid UTF-8 0x103a folder name.") from exc
        if folder_name != "Audio Files":
            raise ValueError("Unsupported 0x103a physical-filename folder.")

        names = []
        position = records_start
        record_prefix = b"\x02\x00\x00\x00\x00"
        record_suffix = None
        while position + 13 <= len(payload) and payload[position:position + 5] == record_prefix:
            name_length = struct.unpack_from("<I", payload, position + 5)[0]
            name_start = position + 9
            name_end = name_start + name_length
            suffix_end = name_end + 4
            suffix = bytes(payload[name_end:suffix_end])
            if suffix_end > len(payload) or suffix not in (b"EVAW", b"\x00" * 4):
                raise ValueError("Invalid 0x103a WAV filename record.")
            if record_suffix is None:
                record_suffix = suffix
            elif suffix != record_suffix:
                raise ValueError("Mixed 0x103a WAV filename record suffixes.")
            try:
                name = payload[name_start:name_end].decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError("Invalid UTF-8 physical WAV filename.") from exc
            if not name or "\x00" in name:
                raise ValueError("Invalid physical WAV filename.")
            names.append(name)
            position = suffix_end
        return names, position

    @staticmethod
    def _decode_1004_catalog_trailer(payload, position, physical_count):
        """Validate the structural 0x103a tail and return its counter offsets."""
        if not isinstance(payload, (bytes, bytearray)):
            raise ValueError("Invalid 0x103a catalog trailer payload.")
        if (
            isinstance(position, bool)
            or not isinstance(position, int)
            or position < 0
            or position + 5 > len(payload)
            or payload[position:position + 5] != b"\x00\xff\xff\xff\xff"
        ):
            raise ValueError("Unsupported 0x103a catalog trailer layout.")
        if (
            isinstance(physical_count, bool)
            or not isinstance(physical_count, int)
            or physical_count < 0
            or physical_count > 0xFFFFFFFF
        ):
            raise ValueError("Invalid 0x103a physical-media count.")

        counter_offsets = []
        cursor = position + 5
        expected_counter = physical_count + 1
        while True:
            if cursor + 4 > len(payload):
                raise ValueError("Truncated 0x103a catalog trailer node.")
            label_length = struct.unpack_from("<I", payload, cursor)[0]
            label_start = cursor + 4
            label_end = label_start + label_length
            if label_end + 4 > len(payload):
                raise ValueError("Truncated 0x103a catalog trailer label.")
            try:
                label = payload[label_start:label_end].decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError("Invalid UTF-8 0x103a catalog trailer label.") from exc
            if not label or "\x00" in label:
                raise ValueError("Invalid 0x103a catalog trailer label.")

            if (
                label_end + 4 == len(payload)
                and payload[label_end:label_end + 4] == b"\x00\x00\x00\x00"
            ):
                if not counter_offsets:
                    raise ValueError(
                        "A 0x103a catalog trailer requires at least one parent node."
                    )
                return counter_offsets

            counter_offset = label_end + 5
            if (
                counter_offset + 4 > len(payload)
                or payload[label_end + 4] != 0x01
            ):
                raise ValueError("Unsupported 0x103a catalog trailer node.")
            counter = struct.unpack_from("<I", payload, counter_offset)[0]
            if expected_counter > 0xFFFFFFFF or counter != expected_counter:
                raise ValueError("Inconsistent 0x103a relink counters.")
            counter_offsets.append(counter_offset)
            expected_counter += 1
            cursor = counter_offset + 4

    def _validated_physical_audio_catalog(self):
        """Return the unique verified 0x1004 media catalog used for relinking."""
        roots = self._root_blocks(0x1004)
        if len(roots) != 1:
            raise ValueError("A unique root 0x1004 media catalog is required.")
        root = roots[0]
        if (
            not root.items
            or not isinstance(root.items[0], (bytes, bytearray))
            or len(root.items[0]) < 4
        ):
            raise ValueError("Invalid 0x1004 media-entry counter.")
        entries = [
            item for item in root.items
            if isinstance(item, PTBlock) and item.content_type == 0x1003
        ]
        declared = struct.unpack_from("<I", root.items[0], 0)[0]
        if declared != len(entries):
            raise ValueError(
                f"Inconsistent 0x1004 media count: declared={declared}, "
                f"actual={len(entries)}."
            )
        for index, entry in enumerate(entries):
            if (
                not entry.items
                or not isinstance(entry.items[0], (bytes, bytearray))
                or len(entry.items[0]) != 4
                or struct.unpack_from("<I", entry.items[0], 0)[0] != index + 1
            ):
                raise ValueError("Invalid 0x1003 media-entry ordinal.")

        candidates = []
        for item in root.items:
            if (
                isinstance(item, PTBlock)
                and item.content_type == 0x103a
                and item.items
                and isinstance(item.items[0], (bytes, bytearray))
            ):
                try:
                    names, insertion_offset = self._decode_1004_filename_payload(
                        item.items[0]
                    )
                except ValueError:
                    continue
                if len(names) == len(entries):
                    candidates.append((item, names, insertion_offset))
        if len(candidates) != 1:
            raise ValueError("A unique ordered 0x103a WAV filename catalog is required.")
        name_block, names, insertion_offset = candidates[0]
        ordered_names = names
        if len({name.casefold() for name in ordered_names}) != len(ordered_names):
            raise ValueError("Duplicate physical WAV filename in 0x103a catalog.")
        return root, entries, name_block, ordered_names, insertion_offset

    @staticmethod
    def _match_physical_filename(clip_name, disk_files, metadata_files):
        """Resolve a virtual clip name without inventing a filename."""
        import os
        import re

        base_name = re.sub(r"-\d+(?:\.[A-Za-z0-9]+)?$", "", clip_name)
        base_name = re.sub(r"\.A[1-9]$", "", base_name, flags=re.IGNORECASE)
        base_key = base_name.casefold()

        for candidates in (disk_files, metadata_files):
            exact = [
                filename for filename in candidates
                if os.path.splitext(filename)[0].casefold() == base_key
            ]
            if len(exact) == 1:
                return exact[0]

            compatible = [
                filename for filename in candidates
                if os.path.splitext(filename)[0].casefold().startswith(base_key)
                or base_key.startswith(os.path.splitext(filename)[0].casefold())
            ]
            if len(compatible) == 1:
                return compatible[0]

        return None

    def get_clips(self):
        """Returns a list of dictionaries describing available clips and clip groups.
        Example: [{'name': 'Audio.wav', 'type': 'parent', 'length': '00:01:00:00', 'src_offset': '00:00:00:00'}]
        """
        engine = TimecodeEngine(self.sample_rate, self.frame_rate_enum)
        clips = []

        clip_roots = self._root_blocks(0x262a)
        if len(clip_roots) > 1:
            raise ValueError("Ambiguous session: multiple root 0x262a clip lists.")
        if clip_roots:
            clip_root = clip_roots[0]
            if (
                not clip_root.items
                or not isinstance(clip_root.items[0], (bytes, bytearray))
                or len(clip_root.items[0]) < 4
            ):
                raise ValueError("Invalid 0x262a clip counter payload.")
            definitions = [
                item for item in clip_root.items
                if isinstance(item, PTBlock) and item.content_type == 0x2629
            ]
            declared = struct.unpack_from("<I", clip_root.items[0], 0)[0]
            if declared != len(definitions):
                raise ValueError(
                    f"Inconsistent 0x262a clip count: declared={declared}, "
                    f"actual={len(definitions)}."
                )
            for definition in definitions:
                payload_blocks = [
                    item for item in definition.items
                    if isinstance(item, PTBlock) and item.content_type == 0x2628
                ]
                if (
                    len(payload_blocks) != 1
                    or not payload_blocks[0].items
                    or not isinstance(payload_blocks[0].items[0], (bytes, bytearray))
                ):
                    raise ValueError("Invalid 0x2629 clip definition.")
                info = self._decode_audio_clip_payload(payload_blocks[0].items[0])
                clips.append({
                    "name": info["name"],
                    "type": "parent" if not info["flags"] & 0x0001 else "virtual",
                    "length": engine.samples_to_timecode(info["length"]),
                    "src_offset": engine.samples_to_timecode(info["src_offset"]),
                })

        group_roots = self._root_blocks(0x262c)
        if len(group_roots) > 1:
            raise ValueError("Ambiguous session: multiple root 0x262c group lists.")
        if group_roots:
            group_root = group_roots[0]
            if (
                not group_root.items
                or not isinstance(group_root.items[0], (bytes, bytearray))
                or len(group_root.items[0]) < 4
            ):
                raise ValueError("Invalid 0x262c group counter payload.")
            definitions = [
                item for item in group_root.items
                if isinstance(item, PTBlock) and item.content_type == 0x262b
            ]
            declared = struct.unpack_from("<I", group_root.items[0], 0)[0]
            if declared != len(definitions):
                raise ValueError(
                    f"Inconsistent 0x262c group count: declared={declared}, "
                    f"actual={len(definitions)}."
                )
            for definition in definitions:
                payload_blocks = [
                    item for item in definition.items
                    if isinstance(item, PTBlock) and item.content_type == 0x2628
                ]
                if (
                    len(payload_blocks) != 1
                    or not payload_blocks[0].items
                    or not isinstance(payload_blocks[0].items[0], (bytes, bytearray))
                ):
                    raise ValueError("Invalid 0x262b Clip Group definition.")
                info = self._decode_clip_group_payload(payload_blocks[0].items[0])
                clips.append({
                    "name": info["name"],
                    "type": "group",
                    "length": engine.samples_to_timecode(info["length"]),
                    "src_offset": engine.samples_to_timecode(0),
                })
        return clips

    def get_timeline_clip_groups(self):
        """Return every visible main-timeline Clip Group macro placement.

        Clip Group IDs belong to the independent 0x262c namespace.  This
        reader intentionally does not mix them into ``get_timeline_clips()``,
        whose contract is limited to ordinary audio clips and fades.
        """
        group_roots = self._root_blocks(0x262c)
        if not group_roots:
            return []
        if len(group_roots) != 1:
            raise ValueError("Ambiguous session: multiple root 0x262c group lists.")
        group_root = group_roots[0]
        if (
            not group_root.items
            or not isinstance(group_root.items[0], (bytes, bytearray))
            or len(group_root.items[0]) < 4
        ):
            raise ValueError("Invalid 0x262c group counter payload.")
        definitions = [
            item for item in group_root.items
            if isinstance(item, PTBlock) and item.content_type == 0x262b
        ]
        declared = struct.unpack_from("<I", group_root.items[0], 0)[0]
        if declared != len(definitions):
            raise ValueError(
                f"Inconsistent 0x262c group count: declared={declared}, "
                f"actual={len(definitions)}."
            )

        groups_by_id = {}
        for group_id, definition in enumerate(definitions):
            payload_blocks = [
                item for item in definition.items
                if isinstance(item, PTBlock) and item.content_type == 0x2628
            ]
            if (
                len(payload_blocks) != 1
                or not payload_blocks[0].items
                or not isinstance(payload_blocks[0].items[0], (bytes, bytearray))
            ):
                raise ValueError("Invalid 0x262b Clip Group definition.")
            groups_by_id[group_id] = self._decode_clip_group_payload(
                payload_blocks[0].items[0]
            )

        engine = TimecodeEngine(self.sample_rate, self.frame_rate_enum)
        placements = []
        for _, track_name, events in self._validated_main_playlists():
            for event in events:
                payload_blocks = [
                    child for child in event.items
                    if isinstance(child, PTBlock) and child.content_type == 0x104f
                ]
                if len(payload_blocks) != 1:
                    raise ValueError("Invalid 0x1050 event structure.")
                payload_block = payload_blocks[0]
                if (
                    not payload_block.items
                    or not isinstance(payload_block.items[0], (bytes, bytearray))
                    or len(payload_block.items[0]) < 16
                ):
                    raise ValueError("Invalid 0x104f event payload.")
                payload = payload_block.items[0]
                if payload[15] != 0x03:
                    continue
                if self._audio_timeline_event_kind(event, payload) != "clip_group":
                    continue

                group_id = struct.unpack_from("<I", payload, 2)[0]
                if group_id not in groups_by_id:
                    raise ValueError(
                        "Timeline Clip Group macro references an unknown group ID."
                    )
                group = groups_by_id[group_id]
                start_samples = struct.unpack_from("<Q", payload, 7)[0]
                length_samples = group["length"]
                end_samples = start_samples + length_samples
                if end_samples > 0xFFFFFFFFFFFFFFFF:
                    raise OverflowError("Clip Group placement end exceeds UInt64.")
                placements.append({
                    "group_id": group_id,
                    "group_name": group["name"],
                    "track": track_name,
                    "start_samples": start_samples,
                    "length_samples": length_samples,
                    "end_samples": end_samples,
                    "start_timecode": engine.samples_to_timecode(start_samples),
                    "length_timecode": engine.samples_to_timecode(length_samples),
                    "end_timecode": engine.samples_to_timecode(end_samples),
                })

        placements.sort(key=lambda item: (item["start_samples"], item["track"]))
        return placements

    def get_timeline_clips(self, include_fades=True):
        """Return validated timeline events, optionally limiting the result to audio."""
        if not isinstance(include_fades, bool):
            raise TypeError("include_fades must be a boolean.")
        clip_lists = self._root_blocks(0x262a)
        if not clip_lists:
            return []
        if len(clip_lists) != 1:
            raise ValueError("Ambiguous session: multiple root 0x262a clip lists.")
        clip_list = clip_lists[0]
        if (
            not clip_list.items
            or not isinstance(clip_list.items[0], (bytes, bytearray))
            or len(clip_list.items[0]) < 4
        ):
            raise ValueError("Invalid 0x262a clip counter payload.")

        definitions = [
            item for item in clip_list.items
            if isinstance(item, PTBlock) and item.content_type == 0x2629
        ]
        declared_clips = struct.unpack_from("<I", clip_list.items[0], 0)[0]
        if declared_clips != len(definitions):
            raise ValueError(
                f"Inconsistent 0x262a clip count: declared={declared_clips}, "
                f"actual={len(definitions)}."
            )

        clip_dict = {}
        definition_by_clip = {}
        for clip_id, definition in enumerate(definitions):
            payload_blocks = [
                item for item in definition.items
                if isinstance(item, PTBlock) and item.content_type == 0x2628
            ]
            if (
                len(payload_blocks) != 1
                or not payload_blocks[0].items
                or not isinstance(payload_blocks[0].items[0], (bytes, bytearray))
            ):
                raise ValueError("Invalid 0x2629 clip definition.")
            clip_dict[clip_id] = self._decode_audio_clip_payload(
                payload_blocks[0].items[0]
            )
            definition_by_clip[clip_id] = definition

        events = self._validated_main_timeline_events()
        if include_fades:
            _, _, fade_bindings = self._validated_fade_geometry_bindings(events)
            geometry_by_event = {
                id(entry[1]): geometry
                for entry, _, geometry in fade_bindings
            }
        else:
            geometry_by_event = {}
        disk_files, metadata_files = self._physical_audio_filenames()
        try:
            _, _, _, ordered_physical_names, _ = (
                self._validated_physical_audio_catalog()
            )
        except ValueError:
            ordered_physical_names = []
        physical_by_clip = {}
        for clip_id, info in clip_dict.items():
            exact_name = None
            try:
                _, (_, _, media_record) = self._validated_2629_fixed_records(
                    definition_by_clip[clip_id]
                )
            except ValueError:
                media_record = None
            if media_record is not None:
                media_index = struct.unpack_from("<I", media_record, 96)[0]
                if media_index < len(ordered_physical_names):
                    exact_name = ordered_physical_names[media_index]
            physical_by_clip[clip_id] = exact_name or self._match_physical_filename(
                info["name"], disk_files, metadata_files
            )

        track_names = {
            id(playlist): name
            for playlist, name, _ in self._validated_main_playlists()
        }
        audio_placements = []
        for playlist, event, _, payload in events:
            if self._audio_timeline_event_kind(event, payload) != "audio":
                continue
            clip_id = struct.unpack_from("<I", payload, 2)[0]
            if clip_id not in clip_dict:
                # Premiere can retain an audio-shaped trailing event whose
                # clip-list ID no longer exists.  It is not an editable audio
                # placement, but the original PTX remains valid in Pro Tools.
                # Keep the reader observational: ignore it here and preserve
                # its raw event unchanged for a later no-op save.
                continue
            start = struct.unpack_from("<Q", payload, 7)[0]
            audio_placements.append({
                "playlist": playlist,
                "event": event,
                "clip_id": clip_id,
                "clip_info": clip_dict[clip_id],
                "start": start,
                "end": start + clip_dict[clip_id]["length"],
            })

        audio_by_event = {
            id(placement["event"]): placement for placement in audio_placements
        }
        timeline = []
        for playlist, event, _, payload in events:
            event_type = payload[15]
            if event_type not in (0x01, 0x03):
                continue
            if event_type == 0x01 and not include_fades:
                continue
            # Clip Group macro IDs occupy an independent namespace and can
            # numerically collide with ordinary 0x262a IDs. They are not audio
            # clip events and must not be mislabeled as the colliding clip.
            if (
                event_type == 0x03
                and self._audio_timeline_event_kind(event, payload) == "clip_group"
            ):
                continue
            if event_type == 0x03 and id(event) not in audio_by_event:
                # See the dangling-ID handling in the first event pass above.
                continue
            anchor_timestamp = struct.unpack_from("<Q", payload, 7)[0]

            if event_type == 0x03:
                placement = audio_by_event[id(event)]
                clip_id = placement["clip_id"]
                clip_info = placement["clip_info"]
                start_samples = placement["start"]
                length_samples = clip_info["length"]
                muted = payload[0] == 0x01
            else:
                geometry = geometry_by_event[id(event)]
                if (
                    not geometry.items
                    or not isinstance(geometry.items[0], (bytes, bytearray))
                ):
                    raise ValueError("Invalid 0x262f fade geometry payload.")
                geometry_payload = geometry.items[0]
                geometry_length = len(geometry_payload)
                if geometry_length == 22:
                    length_samples = struct.unpack_from("<H", geometry_payload, 8)[0]
                    start_samples = anchor_timestamp
                    candidates = [
                        placement for placement in audio_placements
                        if placement["playlist"] is playlist
                        and placement["start"] == anchor_timestamp
                    ]
                elif geometry_length in (26, 27):
                    length_samples = struct.unpack_from("<H", geometry_payload, 8)[0]
                    if length_samples > anchor_timestamp:
                        raise ValueError("Fade Out starts before sample zero.")
                    start_samples = anchor_timestamp - length_samples
                    candidates = [
                        placement for placement in audio_placements
                        if placement["playlist"] is playlist
                        and placement["end"] == anchor_timestamp
                    ]
                elif geometry_length == 34:
                    pre_roll = struct.unpack_from("<H", geometry_payload, 8)[0]
                    length_samples = struct.unpack_from("<H", geometry_payload, 10)[0]
                    if pre_roll > anchor_timestamp:
                        raise ValueError("Crossfade starts before sample zero.")
                    start_samples = anchor_timestamp - pre_roll
                    # A native crossfade precedes the right-hand audio event at
                    # the same anchor. Fall back to the left-hand event only
                    # for sessions whose right side is unavailable.
                    candidates = [
                        placement for placement in audio_placements
                        if placement["playlist"] is playlist
                        and placement["start"] == anchor_timestamp
                    ]
                    if not candidates:
                        candidates = [
                            placement for placement in audio_placements
                            if placement["playlist"] is playlist
                            and placement["end"] == anchor_timestamp
                        ]
                else:
                    raise ValueError(
                        f"Unsupported 0x262f fade geometry length: "
                        f"{geometry_length}."
                    )
                if len(candidates) != 1:
                    raise ValueError(
                        "Fade event cannot be associated with one audio placement."
                    )
                clip_id = candidates[0]["clip_id"]
                clip_info = candidates[0]["clip_info"]
                muted = False

            timeline.append({
                "track": track_names[id(playlist)],
                "clip_name": clip_info["name"],
                "start_samples": start_samples,
                "length_samples": length_samples,
                "end_samples": start_samples + length_samples,
                "src_offset_samples": clip_info["src_offset"],
                "physical_filename": physical_by_clip[clip_id],
                "muted": muted,
                "is_fade": event_type == 0x01,
            })

        timeline.sort(key=lambda item: (item["start_samples"], item["track"]))
        return timeline

    def delete_clip_group(self, group_name):
        """Atomically dissolve one supported simple clip group."""
        original_root_items = copy.deepcopy(self.root_items)
        original_removed_offsets = list(getattr(self, "_removed_offsets", []))
        try:
            return self._delete_clip_group_impl(group_name)
        except Exception:
            self.root_items = original_root_items
            self._removed_offsets = original_removed_offsets
            raise

    def _delete_clip_group_impl(self, group_name):
        """Dissolve a simple clip group and restore its audio events.

        The main playlist contains a macro event while the component events
        live in a hidden 0x2428 playlist. Dissolving the group moves those
        events back to the macro's track, rebases their timestamps, and removes
        the now-unused group metadata.

        The verified layout is deliberately narrow: one simple group containing
        audio events on one track. More complex layouts are rejected before
        mutation instead of being guessed at.
        """

        if not isinstance(group_name, str) or not group_name:
            raise ValueError("group_name must be a non-empty string.")

        def root_blocks(content_type):
            return self._root_blocks(content_type)

        def child_blocks(container, content_type):
            return [
                item for item in container.items
                if isinstance(item, PTBlock) and item.content_type == content_type
            ]

        def raw_count(container, label):
            if not container.items or not isinstance(container.items[0], (bytes, bytearray)):
                raise ValueError(f"Invalid {label} counter payload.")
            payload = container.items[0]
            if len(payload) < 4:
                raise ValueError(f"Invalid {label} counter payload.")
            return struct.unpack_from("<I", payload, 0)[0]

        def validate_count(container, child_type, label):
            declared = raw_count(container, label)
            children = child_blocks(container, child_type)
            if declared != len(children):
                raise ValueError(
                    f"Inconsistent {label} count: declared={declared}, actual={len(children)}."
                )
            return declared, children

        def event_info(event, label):
            b104f = next(
                (item for item in event.items
                 if isinstance(item, PTBlock) and item.content_type == 0x104f),
                None
            )
            if (
                b104f is None or not b104f.items
                or not isinstance(b104f.items[0], (bytes, bytearray))
                or len(b104f.items[0]) < 16
            ):
                raise ValueError(f"Invalid {label} 0x104f payload.")
            payload = b104f.items[0]
            raw_tail = b"".join(
                bytes(item) for item in event.items
                if isinstance(item, (bytes, bytearray))
            )
            return {
                'block': b104f,
                'payload': payload,
                'clip_id': struct.unpack_from("<I", payload, 2)[0],
                'timestamp': struct.unpack_from("<Q", payload, 7)[0],
                'event_type': payload[15],
                'tail': raw_tail,
            }

        # Resolve and validate every part of the group before modifying it.
        roots_262c = root_blocks(0x262c)
        if not roots_262c:
            return 0
        if len(roots_262c) != 1:
            raise ValueError("Ambiguous session: multiple root 0x262c blocks.")
        b262c = roots_262c[0]
        group_count, group_blocks = validate_count(b262c, 0x262b, "0x262c group")
        if group_count == 0:
            raise ValueError(f"Clip group '{group_name}' not found.")
        if group_count > 1:
            raise NotImplementedError(
                "delete_clip_group currently supports sessions containing exactly one clip group."
            )

        group_block = None
        group_id = None
        for index, candidate in enumerate(group_blocks):
            b2628 = next(
                (item for item in candidate.items
                 if isinstance(item, PTBlock) and item.content_type == 0x2628),
                None
            )
            if (
                b2628 is None or not b2628.items
                or not isinstance(b2628.items[0], (bytes, bytearray))
                or len(b2628.items[0]) < 4
            ):
                raise ValueError("Invalid 0x262b group name payload.")
            payload = b2628.items[0]
            name_length = struct.unpack_from("<I", payload, 0)[0]
            if 4 + name_length > len(payload):
                raise ValueError("Invalid 0x262b group name length.")
            name = payload[4:4 + name_length].decode('utf-8', 'strict').rstrip('\x00')
            if name == group_name:
                group_block = candidate
                group_id = index
                break

        if group_block is None:
            raise ValueError(f"Clip group '{group_name}' not found.")

        roots_2428 = root_blocks(0x2428)
        if len(roots_2428) != 1:
            raise ValueError("Unsupported clip-group layout: expected one hidden 0x2428 timeline.")
        b2428 = roots_2428[0]
        hidden_roots = child_blocks(b2428, 0x1054)
        if len(hidden_roots) != 1:
            raise ValueError("Unsupported clip-group layout: expected one hidden 0x1054 block.")
        hidden_1054 = hidden_roots[0]
        hidden_track_count, hidden_tracks = validate_count(
            hidden_1054, 0x1052, "hidden 0x1054 track"
        )
        if hidden_track_count != 1:
            raise NotImplementedError(
                "delete_clip_group currently supports a group containing exactly one track."
            )
        hidden_track = hidden_tracks[0]
        hidden_events = child_blocks(hidden_track, 0x1050)
        if not hidden_events:
            raise ValueError("The hidden clip-group playlist contains no events.")
        if not hidden_track.items or not isinstance(hidden_track.items[0], (bytes, bytearray)):
            raise ValueError("Invalid hidden 0x1052 track header.")
        hidden_header = hidden_track.items[0]
        if len(hidden_header) < 4:
            raise ValueError("Invalid hidden 0x1052 track header.")
        hidden_name_length = struct.unpack_from("<I", hidden_header, 0)[0]
        hidden_count_offset = 4 + hidden_name_length
        if hidden_count_offset + 4 > len(hidden_header):
            raise ValueError("Invalid hidden 0x1052 event counter offset.")
        hidden_declared = struct.unpack_from("<I", hidden_header, hidden_count_offset)[0]
        if hidden_declared != len(hidden_events):
            raise ValueError(
                "Inconsistent hidden 0x1052 event count: "
                f"declared={hidden_declared}, actual={len(hidden_events)}."
            )
        hidden_infos = [event_info(event, "hidden group event") for event in hidden_events]
        if any(info['event_type'] != 0x03 or info['tail'] != b"\x00\x01\x01" for info in hidden_infos):
            raise NotImplementedError(
                "Nested groups, fades, and non-audio events inside a clip group are not supported yet."
            )

        roots_1054 = root_blocks(0x1054)
        if len(roots_1054) != 1:
            raise ValueError("Ambiguous session: expected one main 0x1054 timeline.")
        main_1054 = roots_1054[0]
        macro_matches = []
        for track in child_blocks(main_1054, 0x1052):
            for event in child_blocks(track, 0x1050):
                info = event_info(event, "main timeline event")
                # Group and normal clip IDs occupy independent namespaces.
                if info['clip_id'] == group_id and info['tail'] == b"\x00\x00\x01":
                    macro_matches.append((track, event, info))
        if len(macro_matches) != 1:
            raise NotImplementedError(
                "delete_clip_group currently supports exactly one placed instance of the group."
            )
        main_track, macro_event, macro_info = macro_matches[0]

        main_events = child_blocks(main_track, 0x1050)
        if not main_track.items or not isinstance(main_track.items[0], (bytes, bytearray)):
            raise ValueError("Invalid main 0x1052 track header.")
        main_header = main_track.items[0]
        if len(main_header) < 4:
            raise ValueError("Invalid main 0x1052 track header.")
        track_name_length = struct.unpack_from("<I", main_header, 0)[0]
        main_count_offset = 4 + track_name_length
        if main_count_offset + 4 > len(main_header):
            raise ValueError("Invalid main 0x1052 event counter offset.")
        main_declared = struct.unpack_from("<I", main_header, main_count_offset)[0]
        if main_declared != len(main_events):
            raise ValueError(
                f"Inconsistent main 0x1052 event count: declared={main_declared}, "
                f"actual={len(main_events)}."
            )

        # 0x2424 stores the group-name index. 0x2426 stores a parallel
        # metadata record whose ordinal matches the group ID.
        roots_2424 = root_blocks(0x2424)
        roots_2426 = root_blocks(0x2426)
        if len(roots_2424) != 1 or len(roots_2426) != 1:
            raise ValueError("Unsupported clip-group metadata layout (0x2424/0x2426).")
        b2424 = roots_2424[0]
        name_count, name_blocks = validate_count(b2424, 0x2423, "0x2424 group-name")
        b2426 = roots_2426[0]
        metadata_count, metadata_blocks = validate_count(b2426, 0x2425, "0x2426 group-metadata")
        if name_count != group_count or metadata_count != group_count:
            raise ValueError("Clip-group metadata lists do not match the 0x262c group count.")

        matching_name_blocks = []
        for name_block in name_blocks:
            if not name_block.items or not isinstance(name_block.items[0], (bytes, bytearray)):
                raise ValueError("Invalid 0x2423 group-name payload.")
            payload = name_block.items[0]
            if len(payload) < 8:
                raise ValueError("Invalid 0x2423 group-name payload.")
            name_length = struct.unpack_from("<I", payload, 4)[0]
            if 8 + name_length > len(payload):
                raise ValueError("Invalid 0x2423 group-name length.")
            name = payload[8:8 + name_length].decode('utf-8', 'strict').rstrip('\x00')
            if name == group_name:
                matching_name_blocks.append(name_block)
        if len(matching_name_blocks) != 1:
            raise ValueError("The 0x2423 group-name index does not match the requested group.")
        group_name_block = matching_name_blocks[0]
        group_metadata_block = metadata_blocks[group_id]

        # Move the actual hidden event blocks so live pointer records can be
        # mapped to their new offsets by save().
        hidden_origin = min(info['timestamp'] for info in hidden_infos)
        restored_payloads = []
        for info in hidden_infos:
            payload = bytearray(info['payload'])
            restored_timestamp = macro_info['timestamp'] + info['timestamp'] - hidden_origin
            if restored_timestamp > 0xffffffffffffffff:
                raise OverflowError("Restored clip-group timestamp exceeds UInt64.")
            struct.pack_into("<Q", payload, 7, restored_timestamp)
            restored_payloads.append((info, payload))

        for info, payload in restored_payloads:
            info['block'].items[0] = payload

        for event in hidden_events:
            hidden_track.items.remove(event)

        macro_index = main_track.items.index(macro_event)
        self._collect_offsets_recursive(macro_event, self._removed_offsets)
        main_track.items[macro_index:macro_index + 1] = hidden_events

        # Keep event slots chronological without moving the playlist trailer.
        event_slots = [
            index for index, item in enumerate(main_track.items)
            if isinstance(item, PTBlock) and item.content_type == 0x1050
        ]
        sorted_events = sorted(
            (main_track.items[index] for index in event_slots),
            key=lambda event: event_info(event, "restored main event")['timestamp']
        )
        for index, event in zip(event_slots, sorted_events):
            main_track.items[index] = event

        main_header = bytearray(main_header)
        struct.pack_into(
            "<I", main_header, main_count_offset,
            main_declared - 1 + len(hidden_events)
        )
        main_track.items[0] = main_header

        # Retain the empty hidden timeline containers, matching Pro Tools.
        self._collect_offsets_recursive(hidden_track, self._removed_offsets)
        hidden_1054.items.remove(hidden_track)
        hidden_count_payload = bytearray(hidden_1054.items[0])
        struct.pack_into("<I", hidden_count_payload, 0, 0)
        hidden_1054.items[0] = hidden_count_payload

        # Remove the definition and its two parallel metadata records.
        for container, block in (
            (b262c, group_block),
            (b2424, group_name_block),
            (b2426, group_metadata_block),
        ):
            self._collect_offsets_recursive(block, self._removed_offsets)
            container.items.remove(block)
            count_payload = bytearray(container.items[0])
            old_count = struct.unpack_from("<I", count_payload, 0)[0]
            struct.pack_into("<I", count_payload, 0, old_count - 1)
            container.items[0] = count_payload

        return 1


    def _parse_block(self, pos, max_limit, depth=0):
        if depth > MAX_PTX_BLOCK_DEPTH:
            raise ValueError(
                f"PTX block nesting exceeds {MAX_PTX_BLOCK_DEPTH} levels."
            )
        if (
            isinstance(pos, bool) or not isinstance(pos, int)
            or isinstance(max_limit, bool) or not isinstance(max_limit, int)
            or pos < 0 or max_limit < 0 or max_limit > len(self.data)
            or pos + 7 > max_limit
            or self.data[pos] != ZMARK
        ):
            return None, 0

        block_type = u_endian_read2(self.data, pos + 1, self.is_bigendian)
        block_size = u_endian_read4(self.data, pos + 3, self.is_bigendian)

        if pos + 7 + block_size > max_limit:
            return None, 0

        # A non-empty generic block must at least contain its two-byte
        # content_type. Size 1 can only be a false 0x5A hit in raw payload.
        if block_size == 1:
            return None, 0

        if (block_type & 0xff00) != 0:
            return None, 0

        content_type = 0
        if block_size >= 2:
            content_type = u_endian_read2(self.data, pos + 7, self.is_bigendian)
            payload_start = pos + 9
        else:
            payload_start = pos + 7

        block = PTBlock(block_type, content_type, block_size)
        block.original_offset = pos

        payload_end = pos + 7 + block_size

        i = payload_start
        current_bytes = bytearray()

        # Supported fixed-record payloads cannot contain child blocks. Other
        # content types remain context-sensitive because PTX uses many wrapper
        # types whose nesting is not fully reverse-engineered yet.
        allow_children = content_type not in FLAT_PTX_CONTENT_TYPES

        while i < payload_end:
            if allow_children and self.data[i] == ZMARK:
                child, jump = self._parse_block(i, payload_end, depth + 1)
                if child:
                    if current_bytes:
                        block.items.append(current_bytes)
                        current_bytes = bytearray()
                    block.items.append(child)
                    i += jump
                    continue
            current_bytes.append(self.data[i])
            i += 1

        if current_bytes:
            block.items.append(current_bytes)

        return block, 7 + block_size

    def add_marker(self, name, tc_samples, index=None):
        from binascii import unhexlify
        import uuid

        if not self._validated_main_playlists():
            raise ValueError("Cannot add a marker to a session without an audio track.")

        if not isinstance(name, str):
            raise TypeError("Marker name must be a string.")
        if '\x00' in name:
            raise ValueError("Marker name cannot contain a null byte.")
        if isinstance(tc_samples, bool) or not isinstance(tc_samples, int):
            raise TypeError("Marker timestamp must be an integer sample position.")
        if tc_samples < 0 or tc_samples > 0x7FFFFFFFFFFFFFFF:
            raise ValueError("Marker timestamp is outside the signed 64-bit range.")

        marker_containers = []
        for item in self._root_blocks(0x2030):
            layout = self._marker_container_layout(item)
            if layout is not None:
                marker_containers.append((item, layout))
        if not marker_containers:
            raise ValueError("No valid root 0x2030 marker ruler found.")
        if len(marker_containers) != 1:
            raise ValueError("Multiple root 0x2030 marker rulers found.")

        container, (marker_count, _) = marker_containers[0]
        existing = self.get_markers()
        existing_indices = {marker['index'] for marker in existing}
        if index is None:
            index = max(existing_indices, default=0) + 1
        elif isinstance(index, bool) or not isinstance(index, int):
            raise TypeError("Marker index must be an integer.")
        if index < 1 or index > 0xFFFF:
            raise ValueError("Marker index must be between 1 and 65535.")
        if index in existing_indices:
            raise ValueError(f"Marker index {index} already exists.")

        try:
            new_name_bytes = name.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError("Marker name must be valid UTF-8.") from exc
        if len(new_name_bytes) > 0xFFFFFFFF:
            raise ValueError("Marker name is too long for the PTX format.")

        template_bytes = unhexlify("5a05003c0100003020010000005a12002701000077200100030900000c0000004d41524b45525f414c504841a059650a00000000a059650a00000000000000000000f0bfffffffffffffffffffffffffffbfffffffffffffffbf00000000000000400000000000000040ffffffff01000000000100000000000000000000000000000000ffffffff000000000000000000000000ffffffff0000000000000000000000001500010000005a03000a0000000625ffffffff00000000000000000000000000000000000000000000000000ffffb2056497c372490ba13368d4d04e64cfffffffffffffffffffffffff5a010009000000264801003f009a00ff5a0100090000002648000000000000005a01001e000000274800000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000")

        temp_sess = ProToolsSession.__new__(ProToolsSession)
        temp_sess.data = template_bytes
        temp_sess.is_bigendian = False
        block, _ = temp_sess._parse_block(0, len(template_bytes))
        if (
            block is None
            or block.content_type != 0x2030
            or len(block.items) < 2
            or not isinstance(block.items[1], PTBlock)
            or block.items[1].content_type != 0x2077
        ):
            raise ValueError("Invalid built-in marker template.")

        marker_02077 = block.items[1]
        new_len = len(new_name_bytes)

        header = bytearray(struct.pack("<H", index))
        header.extend([0x03, 0x09, 0x00, 0x00])
        header.extend(struct.pack("<I", new_len))
        header.extend(new_name_bytes)
        header.extend(struct.pack("<q", tc_samples))
        header.extend(struct.pack("<q", tc_samples))

        rest = unhexlify("000000000000f0bfffffffffffffffffffffffffffbfffffffffffffffbf00000000000000400000000000000040ffffffff01000000000100000000000000000000000000000000ffffffff000000000000000000000000ffffffff000000000000000000000000150001000000")
        header.extend(rest)
        marker_02077.items[0] = header

        # Bytes 23..38 of the secondary payload are an RFC 4122 UUID. The
        # preceding 00 FF FF bytes and the trailing FF bytes are structural.
        if (
            len(marker_02077.items) < 3
            or not isinstance(marker_02077.items[2], (bytes, bytearray))
            or len(marker_02077.items[2]) < 39
        ):
            raise ValueError("Invalid built-in marker UUID payload.")
        payload2 = bytearray(marker_02077.items[2])
        payload2[23:39] = uuid.uuid4().bytes
        marker_02077.items[2] = payload2

        # Template offsets refer to the standalone byte string, not this
        # session. Keeping them would pollute the old->new pointer mapping.
        self._wipe_offsets_recursive(marker_02077)

        if marker_count == 0:
            empty_payload = container.items[0]
            container.items = [
                bytearray(struct.pack("<I", 1)),
                marker_02077,
                bytearray(empty_payload[4:12]),
            ]
        else:
            count_payload = bytearray(container.items[0])
            struct.pack_into("<I", count_payload, 0, marker_count + 1)
            container.items[0] = count_payload
            container.items.insert(-1, marker_02077)

        return index

    def _parse_session_metadata(self):
        sample_rate_blocks = self._root_blocks(0x1028)
        frame_rate_blocks = self._root_blocks(0x204d)
        if len(sample_rate_blocks) != 1:
            raise ValueError("A unique root 0x1028 sample-rate block is required.")
        if len(frame_rate_blocks) != 1:
            raise ValueError("A unique root 0x204d frame-rate block is required.")

        sample_block = sample_rate_blocks[0]
        if (
            not sample_block.items
            or not isinstance(sample_block.items[0], (bytes, bytearray))
            or len(sample_block.items[0]) < 6
        ):
            raise ValueError("Invalid root 0x1028 sample-rate payload.")
        fmt = ">I" if self.is_bigendian else "<I"
        sample_rate = struct.unpack_from(fmt, sample_block.items[0], 2)[0]
        if sample_rate <= 0:
            raise ValueError("Session sample rate must be positive.")

        frame_block = frame_rate_blocks[0]
        if (
            not frame_block.items
            or not isinstance(frame_block.items[0], (bytes, bytearray))
            or len(frame_block.items[0]) < 1
        ):
            raise ValueError("Invalid root 0x204d frame-rate payload.")

        self.sample_rate = sample_rate
        self.frame_rate_enum = frame_block.items[0][0]

    def _resolve_unique_clip_id(self, clip_name):
        if not isinstance(clip_name, str) or not clip_name:
            raise ValueError("clip_name must be a non-empty string.")

        clip_lists = self._root_blocks(0x262a)
        if not clip_lists:
            raise ValueError("No clips found in session.")
        if len(clip_lists) != 1:
            raise ValueError("Ambiguous session: multiple root 0x262a clip lists.")
        b262a = clip_lists[0]
        if (
            not b262a.items
            or not isinstance(b262a.items[0], (bytes, bytearray))
            or len(b262a.items[0]) < 4
        ):
            raise ValueError("Invalid 0x262a clip counter payload.")

        clip_definitions = [
            child for child in b262a.items
            if isinstance(child, PTBlock) and child.content_type == 0x2629
        ]
        declared_clips = struct.unpack_from("<I", b262a.items[0], 0)[0]
        if declared_clips != len(clip_definitions):
            raise ValueError(
                f"Inconsistent 0x262a clip count: declared={declared_clips}, "
                f"actual={len(clip_definitions)}."
            )

        matching_ids = []
        for region_id, definition in enumerate(clip_definitions):
            names = [
                child for child in definition.items
                if isinstance(child, PTBlock) and child.content_type == 0x2628
            ]
            if len(names) != 1 or not names[0].items or not isinstance(
                names[0].items[0], (bytes, bytearray)
            ):
                raise ValueError("Invalid 0x2629 clip definition.")
            payload = names[0].items[0]
            if len(payload) < 4:
                raise ValueError("Invalid 0x2628 clip name payload.")
            name_length = struct.unpack_from("<I", payload, 0)[0]
            if 4 + name_length > len(payload):
                raise ValueError("Invalid 0x2628 clip name length.")
            try:
                name = payload[4:4 + name_length].decode('utf-8').strip('\x00')
            except UnicodeDecodeError as exc:
                raise ValueError("Invalid UTF-8 clip name.") from exc
            if name == clip_name:
                matching_ids.append(region_id)

        if not matching_ids:
            raise ValueError(f"Clip '{clip_name}' not found in session.")
        if len(matching_ids) != 1:
            raise ValueError(f"Clip name '{clip_name}' is ambiguous in the session.")
        return matching_ids[0]

    def _audio_clip_info_by_id(self, clip_id):
        """Return the validated 0x2628 information for one ordinal clip ID."""
        clip_lists = self._root_blocks(0x262a)
        if len(clip_lists) != 1:
            raise ValueError("A unique root 0x262a clip list is required.")
        clip_list = clip_lists[0]
        if (
            not clip_list.items
            or not isinstance(clip_list.items[0], (bytes, bytearray))
            or len(clip_list.items[0]) < 4
        ):
            raise ValueError("Invalid 0x262a clip counter payload.")
        definitions = [
            item for item in clip_list.items
            if isinstance(item, PTBlock) and item.content_type == 0x2629
        ]
        declared = struct.unpack_from("<I", clip_list.items[0], 0)[0]
        if declared != len(definitions):
            raise ValueError(
                f"Inconsistent 0x262a clip count: declared={declared}, "
                f"actual={len(definitions)}."
            )
        if clip_id < 0 or clip_id >= len(definitions):
            raise ValueError(f"Unknown clip ID {clip_id}.")
        payload_blocks = [
            item for item in definitions[clip_id].items
            if isinstance(item, PTBlock) and item.content_type == 0x2628
        ]
        if (
            len(payload_blocks) != 1
            or not payload_blocks[0].items
            or not isinstance(payload_blocks[0].items[0], (bytes, bytearray))
        ):
            raise ValueError("Invalid 0x2629 clip definition.")
        return self._decode_audio_clip_payload(payload_blocks[0].items[0])

    def _validated_main_timeline_events(self):
        """Return validated direct events from the visible 0x1054 playlists."""
        timeline_events = []
        playlists = self._validated_main_playlists()
        if not playlists:
            raise ValueError("No main track map (0x1054) found.")
        for playlist, _, events in playlists:
            for event in events:
                event_payloads = [
                    child for child in event.items
                    if isinstance(child, PTBlock) and child.content_type == 0x104f
                ]
                if len(event_payloads) != 1:
                    raise ValueError("Invalid 0x1050 event structure.")
                b104f = event_payloads[0]
                if (
                    not b104f.items
                    or not isinstance(b104f.items[0], (bytes, bytearray))
                    or len(b104f.items[0]) < 16
                ):
                    raise ValueError("Invalid 0x104f event payload.")
                payload = b104f.items[0]
                timeline_events.append((playlist, event, b104f, payload))

        return timeline_events

    def _purge_dangling_audio_timeline_events(self, clip_count):
        """Remove audio-shaped events that cannot reference the Clip List.

        Premiere sessions have been observed to retain trailing audio events
        whose IDs are outside the current 0x262a range. A read must preserve
        them, but a relink appends one definition and could otherwise make one
        of those stale IDs point to the new media. This normalizer is private
        to a surrounding mutation transaction and records every removed block
        for the final 0x0002 pointer-table purge.
        """
        if isinstance(clip_count, bool) or not isinstance(clip_count, int):
            raise TypeError("clip_count must be an integer.")
        if clip_count < 0:
            raise ValueError("clip_count cannot be negative.")

        dangling_by_playlist = {}
        for playlist, event, _, payload in self._validated_main_timeline_events():
            if self._audio_timeline_event_kind(event, payload) != "audio":
                continue
            if struct.unpack_from("<I", payload, 2)[0] >= clip_count:
                dangling_by_playlist.setdefault(id(playlist), (playlist, []))[1].append(
                    event
                )

        for playlist, dangling_events in dangling_by_playlist.values():
            header = bytearray(playlist.items[0])
            name_length = struct.unpack_from("<I", header, 0)[0]
            count_offset = 4 + name_length
            declared_events = struct.unpack_from("<I", header, count_offset)[0]
            if declared_events < len(dangling_events):
                raise ValueError("Invalid playlist count while purging dangling events.")
            for event in dangling_events:
                self._collect_offsets_recursive(event, self._removed_offsets)
                playlist.items.remove(event)
            struct.pack_into(
                "<I", header, count_offset, declared_events - len(dangling_events)
            )
            playlist.items[0] = header

    @staticmethod
    def _audio_timeline_event_kind(event, payload):
        """Classify a type-0x03 event as audio or Clip Group macro."""
        if payload[15] != 0x03:
            return None
        secondary_payload = b"".join(
            bytes(item) for item in event.items
            if isinstance(item, (bytes, bytearray))
        )
        if secondary_payload in (b"\x00\x01\x01", b"\x01\x01\x01"):
            return "audio"
        if secondary_payload == b"\x00\x00\x01":
            return "clip_group"
        raise ValueError("Unsupported audio event secondary payload.")

    def _validated_fade_geometry_list(self):
        """Return the unique, internally consistent root 0x2630 list."""
        geometry_roots = self._root_blocks(0x2630)
        if len(geometry_roots) != 1:
            raise ValueError("A unique fade geometry list (0x2630) is required.")
        geometry_root = geometry_roots[0]
        if (
            not geometry_root.items
            or not isinstance(geometry_root.items[0], (bytes, bytearray))
            or len(geometry_root.items[0]) < 4
        ):
            raise ValueError("Invalid 0x2630 fade geometry counter.")

        geometries = [
            item for item in geometry_root.items
            if isinstance(item, PTBlock) and item.content_type == 0x262f
        ]
        declared = struct.unpack_from("<I", geometry_root.items[0], 0)[0]
        if declared != len(geometries):
            raise ValueError(
                f"Inconsistent 0x2630 geometry count: declared={declared}, "
                f"actual={len(geometries)}."
            )
        if any(
            not geometry.items
            or not isinstance(geometry.items[0], (bytes, bytearray))
            for geometry in geometries
        ):
            raise ValueError("Invalid 0x262f fade geometry payload.")
        return geometry_root, geometries

    def _validated_fade_geometry_bindings(self, timeline_events=None):
        """Bind each fade event to the 0x262f index stored in 0x104f +2.

        Fade and audio events reuse the same four-byte field at +2, but they
        use different namespaces: audio values index 0x262a clip definitions,
        while fade values index the global 0x2630 geometry list.
        """
        if timeline_events is None:
            timeline_events = self._validated_main_timeline_events()
        fade_entries = [
            entry for entry in timeline_events if entry[3][15] == 0x01
        ]
        has_geometry_root = bool(self._root_blocks(0x2630))
        if not fade_entries and not has_geometry_root:
            return None, [], []

        geometry_root, geometries = self._validated_fade_geometry_list()
        if len(fade_entries) != len(geometries):
            raise ValueError("Fade event and 0x262f geometry counts do not match.")

        bindings = []
        used_geometry_ids = set()
        for entry in fade_entries:
            geometry_id = struct.unpack_from("<I", entry[3], 2)[0]
            if geometry_id >= len(geometries):
                raise ValueError(
                    f"Fade event references unknown geometry ID {geometry_id}."
                )
            if geometry_id in used_geometry_ids:
                raise ValueError(f"Duplicate fade geometry ID {geometry_id}.")
            used_geometry_ids.add(geometry_id)
            bindings.append((entry, geometry_id, geometries[geometry_id]))

        return geometry_root, geometries, bindings

    def _fade_bindings_for_audio_placement(
        self, start_samples, length_samples, playlist, timeline_events=None
    ):
        """Return native fades touching one exact visible audio placement."""
        _, _, bindings = self._validated_fade_geometry_bindings(timeline_events)
        end_samples = start_samples + length_samples
        related = []
        for entry, geometry_id, geometry in bindings:
            if entry[0] is not playlist:
                continue
            if (
                not geometry.items
                or not isinstance(geometry.items[0], (bytes, bytearray))
            ):
                raise ValueError("Invalid 0x262f fade geometry payload.")
            geometry_length = len(geometry.items[0])
            anchor = struct.unpack_from("<Q", entry[3], 7)[0]
            is_related = (
                (geometry_length == 22 and anchor == start_samples)
                or (geometry_length in (26, 27) and anchor == end_samples)
                or (geometry_length == 34 and anchor in (start_samples, end_samples))
            )
            if is_related:
                related.append((entry, geometry_id, geometry))
        return related

    def mute_clip(self, clip_name, mute=True):
        """Mute or unmute every visible audio placement of a clip.

        Fade geometry IDs can numerically collide with audio clip IDs, so only
        direct audio events (0x1050 -> 0x104f with event type 0x03) in the main
        0x1054 playlists are eligible. Hidden group playlists are untouched.
        """
        if not isinstance(mute, bool):
            raise TypeError("mute must be a boolean.")

        region_id = self._resolve_unique_clip_id(clip_name)
        targets = []
        for _, event, b104f, payload in self._validated_main_timeline_events():
            if self._audio_timeline_event_kind(event, payload) != "audio":
                continue
            placed_id = struct.unpack_from("<I", payload, 2)[0]
            if placed_id == region_id:
                targets.append(b104f)

        if not targets:
            raise ValueError(f"Clip '{clip_name}' has no visible audio placement.")

        for b104f in targets:
            payload = bytearray(b104f.items[0])
            payload[0] = 0x01 if mute else 0x00
            b104f.items[0] = payload

        return len(targets)

    def move_clip(self, clip_name, hh, mm, ss, ff):
        """Move one unambiguous visible audio placement to an absolute TC.

        The public signature cannot identify one occurrence when the same clip
        is placed multiple times. Such sessions, and clips with native fades,
        are rejected instead of collapsing related events onto one timestamp.
        """
        components = (hh, mm, ss, ff)
        if any(isinstance(value, bool) or not isinstance(value, int) for value in components):
            raise TypeError("Timecode components must be integers.")
        if hh < 0 or mm < 0 or mm > 59 or ss < 0 or ss > 59 or ff < 0:
            raise ValueError("Invalid target timecode components.")

        engine = TimecodeEngine(self.sample_rate, self.frame_rate_enum)
        _, is_drop_frame = engine.get_frame_rate()
        nominal_fps = 30 if self.frame_rate_enum == 0x05 else (
            24 if self.frame_rate_enum == 0x09 else int(engine.get_frame_rate()[0])
        )
        if ff >= nominal_fps:
            raise ValueError(f"Frame component must be below {nominal_fps}.")
        if is_drop_frame and mm % 10 != 0 and ss == 0 and ff < 2:
            raise ValueError("Target timecode falls on a dropped frame number.")

        target_samples = engine.timecode_to_samples(hh, mm, ss, ff)
        if target_samples < 0 or target_samples > 0xFFFFFFFFFFFFFFFF:
            raise ValueError("Target sample position is outside the UInt64 range.")

        region_id = self._resolve_unique_clip_id(clip_name)
        timeline_events = self._validated_main_timeline_events()
        placements = []
        for entry in timeline_events:
            _, event, _, payload = entry
            if self._audio_timeline_event_kind(event, payload) != "audio":
                continue
            if struct.unpack_from("<I", payload, 2)[0] == region_id:
                placements.append(entry)
        if not placements:
            raise ValueError(f"Clip '{clip_name}' has no visible audio placement.")
        if len(placements) != 1:
            raise ValueError(
                f"Clip '{clip_name}' has multiple visible placements; "
                "the move target is ambiguous."
            )

        target_playlist, _, target_b104f, target_original_payload = placements[0]
        original_start = struct.unpack_from("<Q", target_original_payload, 7)[0]
        has_fade_events = any(entry[3][15] == 0x01 for entry in timeline_events)
        if has_fade_events:
            clip_length = self._audio_clip_info_by_id(region_id)["length"]
            related_fades = self._fade_bindings_for_audio_placement(
                original_start, clip_length, target_playlist, timeline_events
            )
        else:
            related_fades = []
        if related_fades:
            raise NotImplementedError(
                "Moving a clip with attached fades is not supported safely yet."
            )

        target_payload = bytearray(target_b104f.items[0])
        struct.pack_into("<Q", target_payload, 7, target_samples)
        target_b104f.items[0] = target_payload

        # Keep the direct 0x1050 slots chronological while preserving every
        # raw/non-event item at its exact position in the playlist.
        event_slots = [
            index for index, item in enumerate(target_playlist.items)
            if isinstance(item, PTBlock) and item.content_type == 0x1050
        ]

        def event_timestamp(event):
            b104f = next(
                child for child in event.items
                if isinstance(child, PTBlock) and child.content_type == 0x104f
            )
            return struct.unpack_from("<Q", b104f.items[0], 7)[0]

        sorted_events = sorted(
            (target_playlist.items[index] for index in event_slots),
            key=event_timestamp,
        )
        for index, event in zip(event_slots, sorted_events):
            target_playlist.items[index] = event

        return 1

    def rename_clip(self, old_name, new_name):
        """Rename one uniquely identified audio clip definition."""
        if not isinstance(old_name, str):
            raise TypeError("Old clip name must be a string.")
        if not old_name:
            raise ValueError("Old clip name must be non-empty.")
        if "\x00" in old_name:
            raise ValueError("Old clip name cannot contain a NUL character.")
        if not isinstance(new_name, str):
            raise TypeError("New clip name must be a string.")
        if not new_name:
            raise ValueError("New clip name must be non-empty.")
        if "\x00" in new_name:
            raise ValueError("New clip name cannot contain a NUL character.")

        try:
            name_bytes = new_name.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError("New clip name must be valid UTF-8.") from exc
        if len(name_bytes) > 0xFFFFFFFF:
            raise ValueError("New clip name is too long for the PTX format.")

        target_id = self._resolve_unique_clip_id(old_name)
        b262a = self._root_blocks(0x262a)[0]
        definitions = [
            child for child in b262a.items
            if isinstance(child, PTBlock) and child.content_type == 0x2629
        ]

        target_b2628 = None
        target_payload = None
        target_name_length = None
        for clip_id, definition in enumerate(definitions):
            name_blocks = [
                child for child in definition.items
                if isinstance(child, PTBlock) and child.content_type == 0x2628
            ]
            # _resolve_unique_clip_id() has already validated this structure and
            # every UTF-8 name. Repeat only the extraction needed for mutation.
            b2628 = name_blocks[0]
            payload = b2628.items[0]
            name_length = struct.unpack_from("<I", payload, 0)[0]
            current_name = payload[4:4 + name_length].decode("utf-8").strip("\x00")
            if clip_id != target_id and current_name == new_name:
                raise ValueError(f"Clip name '{new_name}' already exists in the session.")
            if clip_id == target_id:
                target_b2628 = b2628
                target_payload = payload
                target_name_length = name_length

        if target_b2628 is None:
            raise ValueError("Resolved clip definition disappeared before rename.")

        new_payload = bytearray(
            4 + len(name_bytes) + (len(target_payload) - (4 + target_name_length))
        )
        struct.pack_into("<I", new_payload, 0, len(name_bytes))
        new_payload[4:4 + len(name_bytes)] = name_bytes
        new_payload[4 + len(name_bytes):] = target_payload[4 + target_name_length:]
        target_b2628.items[0] = new_payload
        return 1

    def _serialize_fixed_record_items(self, items, error_message):
        """Reassemble raw bytes that may contain a falsely parsed empty block."""
        payload = bytearray()
        for item in items:
            if isinstance(item, PTBlock):
                serialized, _ = item.to_bytes(self.is_bigendian, 0)
                payload.extend(serialized)
            elif isinstance(item, (bytes, bytearray)):
                payload.extend(item)
            else:
                raise ValueError(error_message)
        return payload

    def _validated_2629_fixed_records(self, definition):
        """Reassemble the two fixed records around the 0x4403 child."""
        if not isinstance(definition, PTBlock) or definition.content_type != 0x2629:
            raise ValueError("A 0x2629 clip definition is required.")
        name_positions = [
            index for index, item in enumerate(definition.items)
            if isinstance(item, PTBlock) and item.content_type == 0x2628
        ]
        metadata_positions = [
            index for index, item in enumerate(definition.items)
            if isinstance(item, PTBlock) and item.content_type == 0x4403
        ]
        if len(name_positions) != 1 or len(metadata_positions) != 1:
            raise ValueError("Invalid 0x2629 fixed-record structure.")
        name_position = name_positions[0]
        metadata_position = metadata_positions[0]
        if metadata_position <= name_position + 1:
            raise ValueError("Invalid 0x2629 fixed-record ordering.")

        identity_start = name_position + 1
        identity_end = metadata_position
        media_start = metadata_position + 1
        media_end = len(definition.items)
        identity = self._serialize_fixed_record_items(
            definition.items[identity_start:identity_end],
            "Invalid item in a 0x2629 fixed record.",
        )
        media = self._serialize_fixed_record_items(
            definition.items[media_start:media_end],
            "Invalid item in a 0x2629 fixed record.",
        )
        if len(identity) != 48 or len(media) != 104:
            raise ValueError(
                "A verified 48-byte identity and 104-byte media record are required."
            )
        return (
            (identity_start, identity_end, identity),
            (media_start, media_end, media),
        )

    def _validated_audio_import_template(self):
        """Return the native structures required by template-based audio import."""
        if self.sample_rate != 48_000 or self.frame_rate_enum != 0x09:
            raise ValueError(
                "Audio-import template must use 48 kHz and frame-rate enum "
                "0x09 (23.976)."
            )

        playlists = self._validated_main_playlists()
        playlist_names = [name for _, name, _ in playlists]
        if not playlist_names:
            raise ValueError("Audio-import template requires at least one track.")
        if len(set(playlist_names)) != len(playlist_names):
            raise ValueError("Audio-import template track names must be unique.")
        if any(events for _, _, events in playlists):
            raise ValueError(
                "Audio-import template playlists must contain no timeline events."
            )
        all_timeline_events = [
            event
            for root in self._content_root_items()
            if isinstance(root, PTBlock)
            for event in root.get_all_blocks(0x1050)
        ]
        if all_timeline_events:
            raise ValueError(
                "Audio-import template must contain no visible or hidden "
                "timeline events."
            )

        clip_lists = self._root_blocks(0x262A)
        if len(clip_lists) != 1:
            raise ValueError("Audio-import template requires one 0x262a clip list.")
        clip_list = clip_lists[0]
        definitions = [
            item for item in clip_list.items
            if isinstance(item, PTBlock) and item.content_type == 0x2629
        ]
        if (
            not clip_list.items
            or not isinstance(clip_list.items[0], (bytes, bytearray))
            or len(clip_list.items[0]) < 4
            or struct.unpack_from("<I", clip_list.items[0], 0)[0] != 1
            or len(definitions) != 1
        ):
            raise ValueError(
                "Audio-import template must contain exactly one imported "
                "prototype clip."
            )
        prototype_definition = definitions[0]
        name_blocks = [
            item for item in prototype_definition.items
            if isinstance(item, PTBlock) and item.content_type == 0x2628
        ]
        if (
            len(name_blocks) != 1
            or not name_blocks[0].items
            or not isinstance(name_blocks[0].items[0], (bytes, bytearray))
        ):
            raise ValueError("Invalid audio-import prototype 0x2628 definition.")
        prototype_payload = name_blocks[0].items[0]
        if len(prototype_payload) < 4:
            raise ValueError("Truncated audio-import prototype 0x2628 definition.")
        prototype_name_length = struct.unpack_from("<I", prototype_payload, 0)[0]
        attributes = 4 + prototype_name_length
        profile = next(
            (
                candidate for candidate in _AUDIO_IMPORT_TEMPLATE_PROFILES
                if prototype_payload[attributes:attributes + 5]
                == candidate["clip_prefix"]
            ),
            None,
        )
        if profile is None:
            raise ValueError(
                "Audio-import template uses an unsupported imported-parent "
                "0x2628 profile."
            )
        clip_length_end = (
            attributes + profile["clip_length_offset"]
            + profile["clip_length_width"]
        )
        time_reference_offsets = profile["clip_time_reference_offsets"]
        if (
            clip_length_end > len(prototype_payload)
            or any(
                attributes + offset + 4 > len(prototype_payload)
                for offset in time_reference_offsets
            )
        ):
            raise ValueError(
                "Truncated audio-import prototype imported-parent profile."
            )
        prototype_length = int.from_bytes(
            prototype_payload[
                attributes + profile["clip_length_offset"]:clip_length_end
            ],
            "little",
        )
        prototype_time_references = [
            struct.unpack_from("<I", prototype_payload, attributes + offset)[0]
            for offset in time_reference_offsets
        ]
        if not prototype_length or len(set(prototype_time_references)) != 1:
            raise ValueError(
                "Inconsistent audio-import prototype imported-parent timing."
            )
        _, (_, _, prototype_media_link) = self._validated_2629_fixed_records(
            prototype_definition
        )
        if struct.unpack_from("<I", prototype_media_link, 96)[0] != 0:
            raise ValueError(
                "Audio-import prototype clip must reference media index zero."
            )

        catalog, entries, name_block, names, _ = (
            self._validated_physical_audio_catalog()
        )
        if len(entries) != 1 or len(names) != 1:
            raise ValueError(
                "Audio-import template must contain exactly one imported "
                "prototype medium."
            )
        prototype_media = entries[0]
        media_info_blocks = [
            item for item in prototype_media.items
            if isinstance(item, PTBlock) and item.content_type == 0x1001
        ]
        detail_blocks = [
            item for item in prototype_media.items
            if isinstance(item, PTBlock) and item.content_type == 0x2106
        ]
        if len(media_info_blocks) != 1 or len(detail_blocks) != 1:
            raise ValueError("Invalid audio-import prototype 0x1003 medium.")
        media_info = self._serialize_fixed_record_items(
            media_info_blocks[0].items,
            "Invalid audio-import prototype 0x1001 format record.",
        )
        if (
            len(media_info) != profile["media_info_length"]
            or struct.unpack_from("<I", media_info, 0)[0] != 48_000
            or media_info[4] != 1
            or media_info[5] != 32
            or media_info[
                profile["media_zero_offset"]:
                profile["media_zero_offset"] + profile["media_zero_length"]
            ] != b"\x00" * profile["media_zero_length"]
            or media_info[profile["media_float_code_offset"]] != 0x03
        ):
            raise ValueError(
                "Audio-import template must use the selected mono 48 kHz "
                "float 0x1001 profile."
            )
        media_length_start = profile["media_length_offset"]
        media_length_end = media_length_start + profile["media_length_width"]
        if int.from_bytes(
            media_info[media_length_start:media_length_end], "little"
        ) != prototype_length:
            raise ValueError(
                "Audio-import prototype 0x1001 and 0x2628 lengths differ."
            )
        detail = detail_blocks[0]
        detail_headers = [
            item for item in detail.items
            if (
                isinstance(item, (bytes, bytearray))
                and len(item) == profile["detail_header_length"]
            )
        ]
        detail_tails = [
            item for item in detail.items
            if (
                isinstance(item, (bytes, bytearray))
                and len(item) == profile["detail_tail_length"]
            )
        ]
        if len(detail_headers) != 1 or len(detail_tails) != 1:
            raise ValueError(
                "Audio-import prototype does not match the selected 0x2106 "
                "profile."
            )
        header_time_reference = struct.unpack_from(
            "<I", detail_headers[0], profile["detail_time_reference_offset"]
        )[0]
        if header_time_reference != prototype_time_references[0]:
            raise ValueError(
                "Audio-import prototype 0x2106 and 0x2628 time references "
                "differ."
            )

        return {
            "profile": profile,
            "playlists": {name: playlist for playlist, name, _ in playlists},
            "clip_list": clip_list,
            "prototype_definition": prototype_definition,
            "catalog": catalog,
            "name_block": name_block,
            "prototype_media": prototype_media,
        }

    def validate_audio_import_template(self):
        """Describe a supported builder template without changing the session."""
        template = self._validated_audio_import_template()
        return {
            "profile": template["profile"]["name"],
            "tracks": list(template["playlists"]),
        }

    def _configure_imported_audio_media_entry(
        self, media_entry, metadata, media_index, filetime, profile
    ):
        """Patch one cloned native media record for a validated source WAV."""
        import uuid

        if (
            not media_entry.items
            or not isinstance(media_entry.items[0], (bytes, bytearray))
            or len(media_entry.items[0]) != 4
        ):
            raise ValueError("Invalid audio-import prototype media ordinal.")
        ordinal = bytearray(media_entry.items[0])
        struct.pack_into("<I", ordinal, 0, media_index + 1)
        media_entry.items[0] = ordinal

        media_info_blocks = [
            item for item in media_entry.items
            if isinstance(item, PTBlock) and item.content_type == 0x1001
        ]
        detail_blocks = [
            item for item in media_entry.items
            if isinstance(item, PTBlock) and item.content_type == 0x2106
        ]
        if len(media_info_blocks) != 1 or len(detail_blocks) != 1:
            raise ValueError("Invalid audio-import media clone structure.")

        media_info_block = media_info_blocks[0]
        media_info = self._serialize_fixed_record_items(
            media_info_block.items,
            "Invalid audio-import media-clone 0x1001 record.",
        )
        if len(media_info) != profile["media_info_length"]:
            raise ValueError("Invalid audio-import media-clone 0x1001 size.")
        maximum_length = (1 << (8 * profile["media_length_width"])) - 1
        if metadata["length_samples"] > maximum_length:
            raise ValueError(
                f"Audio-import profile {profile['name']!r} supports clips of "
                f"at most {maximum_length} samples."
            )
        length_start = profile["media_length_offset"]
        length_end = length_start + profile["media_length_width"]
        media_info[length_start:length_end] = metadata["length_samples"].to_bytes(
            profile["media_length_width"], "little"
        )
        media_info_block.items = [media_info]

        detail = detail_blocks[0]
        detail_headers = [
            (index, item) for index, item in enumerate(detail.items)
            if (
                isinstance(item, (bytes, bytearray))
                and len(item) == profile["detail_header_length"]
            )
        ]
        detail_tails = [
            (index, item) for index, item in enumerate(detail.items)
            if (
                isinstance(item, (bytes, bytearray))
                and len(item) == profile["detail_tail_length"]
            )
        ]
        if len(detail_headers) != 1 or len(detail_tails) != 1:
            raise ValueError("Invalid audio-import media-clone 0x2106 layout.")
        header_index, header_raw = detail_headers[0]
        header = bytearray(header_raw)
        struct.pack_into("<Q", header, profile["detail_filetime_offset"], filetime)
        struct.pack_into(
            "<I",
            header,
            profile["detail_time_reference_offset"],
            metadata["bwf_time_reference"],
        )
        struct.pack_into(
            "<Q",
            header,
            profile["detail_previous_filetime_offset"],
            max(0, filetime - 10_000_000),
        )
        uuid_offset = profile["detail_uuid_offset"]
        header[uuid_offset:uuid_offset + 16] = uuid.uuid4().bytes
        detail.items[header_index] = header

        tail_index, tail_raw = detail_tails[0]
        tail = bytearray(tail_raw)
        tail[18:50] = metadata["umid"][:32]
        detail.items[tail_index] = tail

    def _configure_imported_audio_clip_definition(
        self, definition, metadata, clip_id, media_index, profile
    ):
        """Patch one cloned native parent definition and its physical link."""
        import uuid

        identity_span, media_span = self._validated_2629_fixed_records(definition)
        identity_start, identity_end, identity_raw = identity_span
        media_start, media_end, media_raw = media_span

        identity = bytearray(identity_raw)
        struct.pack_into("<I", identity, 0, clip_id)
        identity[23:39] = uuid.uuid4().bytes

        media_link = bytearray(media_raw)
        struct.pack_into("<I", media_link, 96, media_index)
        # Replace the later span first. Either fixed record may contain bytes
        # that the generic parser represented as a false empty PTBlock; in
        # that case normalizing the identity first would shift the saved media
        # indices and append a second 104-byte record instead of replacing it.
        definition.items[media_start:media_end] = [media_link]
        definition.items[identity_start:identity_end] = [identity]

        name_blocks = [
            item for item in definition.items
            if isinstance(item, PTBlock) and item.content_type == 0x2628
        ]
        if (
            len(name_blocks) != 1
            or not name_blocks[0].items
            or not isinstance(name_blocks[0].items[0], (bytes, bytearray))
        ):
            raise ValueError("Invalid audio-import clip-clone 0x2628 definition.")
        name_block = name_blocks[0]
        source_payload = name_block.items[0]
        source_name_length = struct.unpack_from("<I", source_payload, 0)[0]
        source_attributes = 4 + source_name_length
        source_length_end = (
            source_attributes + profile["clip_length_offset"]
            + profile["clip_length_width"]
        )
        if (
            source_length_end > len(source_payload)
            or source_payload[source_attributes:source_attributes + 5]
            != profile["clip_prefix"]
            or any(
                source_attributes + offset + 4 > len(source_payload)
                for offset in profile["clip_time_reference_offsets"]
            )
        ):
            raise ValueError(
                "Invalid audio-import clip-clone imported-parent layout."
            )
        source_tail = source_payload[source_attributes:]
        clip_name = metadata["clip_name"].encode("utf-8")
        payload = bytearray(struct.pack("<I", len(clip_name)) + clip_name)
        payload.extend(source_tail)
        attributes = 4 + len(clip_name)
        maximum_length = (1 << (8 * profile["clip_length_width"])) - 1
        if metadata["length_samples"] > maximum_length:
            raise ValueError(
                f"Audio-import profile {profile['name']!r} supports clips of "
                f"at most {maximum_length} samples."
            )
        length_start = attributes + profile["clip_length_offset"]
        length_end = length_start + profile["clip_length_width"]
        payload[length_start:length_end] = metadata["length_samples"].to_bytes(
            profile["clip_length_width"], "little"
        )
        for offset in profile["clip_time_reference_offsets"]:
            struct.pack_into(
                "<I", payload, attributes + offset, metadata["bwf_time_reference"]
            )
        name_block.items = [payload]

    def _replace_imported_audio_catalog_filenames(self, name_block, filenames):
        """Replace the prototype filename record and rebuild native counters."""
        if not filenames:
            raise ValueError("Audio-import filename catalog cannot be empty.")
        payload = bytearray(name_block.items[0])
        current_names, insertion_offset = self._decode_1004_filename_payload(
            payload
        )
        if len(current_names) != 1:
            raise ValueError(
                "Audio-import template filename catalog must contain one prototype."
            )
        counter_offsets = self._decode_1004_catalog_trailer(
            payload, insertion_offset, 1
        )
        folder_length = struct.unpack_from("<I", payload, 9)[0]
        records_start = 13 + folder_length + 4
        record_suffix = bytes(payload[insertion_offset - 4:insertion_offset])
        if record_suffix not in (b"EVAW", b"\x00" * 4):
            raise ValueError("Invalid audio-import template WAV filename suffix.")

        prefix = bytearray(payload[:records_start])
        trailer = bytearray(payload[insertion_offset:])
        count = len(filenames)
        depth = len(counter_offsets)
        struct.pack_into("<I", prefix, 0, count + depth + 2)
        struct.pack_into("<I", prefix, 5, count + depth + 1)
        for index, absolute_offset in enumerate(counter_offsets, start=1):
            relative_offset = absolute_offset - insertion_offset
            struct.pack_into("<I", trailer, relative_offset, count + index)

        records = bytearray()
        for filename in filenames:
            encoded = filename.encode("utf-8")
            records.extend(b"\x02\x00\x00\x00\x00")
            records.extend(struct.pack("<I", len(encoded)))
            records.extend(encoded)
            records.extend(record_suffix)
        name_block.items = [prefix + records + trailer]

    def _append_imported_audio_event(self, playlist, clip_id, start_samples):
        """Append one native audio event in caller-defined spotting order."""
        if (
            not playlist.items
            or not isinstance(playlist.items[0], (bytes, bytearray))
            or len(playlist.items[0]) < 8
        ):
            raise ValueError("Invalid audio-import playlist header.")
        header = bytearray(playlist.items[0])
        name_length = struct.unpack_from("<I", header, 0)[0]
        count_offset = 4 + name_length
        header_end = count_offset + 4
        if header_end > len(header):
            raise ValueError("Truncated audio-import playlist event counter.")
        current_events = [
            item for item in playlist.items
            if isinstance(item, PTBlock) and item.content_type == 0x1050
        ]
        current_count = struct.unpack_from("<I", header, count_offset)[0]
        if current_count != len(current_events):
            raise ValueError("Inconsistent audio-import playlist event count.")

        inline_trailer = header[header_end:]
        if inline_trailer:
            if current_events:
                raise ValueError(
                    "Unsupported inline trailer in a populated audio-import playlist."
                )
            playlist.items[0] = header[:header_end]
            playlist.items.append(bytearray(inline_trailer))
            header = bytearray(playlist.items[0])

        for item in playlist.items[1:]:
            if isinstance(item, PTBlock) and item.content_type != 0x1050:
                raise ValueError(
                    "Unsupported non-audio block in an audio-import playlist."
                )
            if not isinstance(item, (PTBlock, bytes, bytearray)):
                raise ValueError("Invalid audio-import playlist item.")

        payload = bytearray(35)
        struct.pack_into("<I", payload, 2, clip_id)
        struct.pack_into("<Q", payload, 7, start_samples)
        payload[15] = 0x03
        payload[16:22] = b"\xfe\xff\x00\x00\x00\x00"
        payload[22:30] = b"\xff" * 8
        payload_block = PTBlock(0x0A, 0x104F, 37)
        payload_block.items = [payload]
        event = PTBlock(0x03, 0x1050, 49)
        event.items = [payload_block, bytearray(b"\x00\x01\x01")]

        insert_at = 1
        for index, item in enumerate(playlist.items[1:], start=1):
            if isinstance(item, PTBlock) and item.content_type == 0x1050:
                insert_at = index + 1
            else:
                break
        playlist.items.insert(insert_at, event)
        struct.pack_into("<I", header, count_offset, current_count + 1)
        playlist.items[0] = header

    def _populate_audio_import_template(self, prepared, template=None):
        """Replace one validated prototype with caller-ordered audio media."""
        import time

        if not prepared:
            raise ValueError("At least one prepared audio source is required.")
        if template is None:
            template = self._validated_audio_import_template()
        profile = template["profile"]
        unknown_tracks = [
            item["track"] for item in prepared
            if item["track"] not in template["playlists"]
        ]
        if unknown_tracks:
            raise ValueError(
                f"Audio-import target track not found in template: "
                f"{unknown_tracks[0]!r}."
            )
        media_template = copy.deepcopy(template["prototype_media"])
        definition_template = copy.deepcopy(template["prototype_definition"])
        base_filetime = _WINDOWS_FILETIME_EPOCH + time.time_ns() // 100

        media_entries = []
        definitions = []
        for index, metadata in enumerate(prepared):
            if index == 0:
                media_entry = template["prototype_media"]
                definition = template["prototype_definition"]
            else:
                media_entry = copy.deepcopy(media_template)
                definition = copy.deepcopy(definition_template)
                self._wipe_offsets_recursive(media_entry)
                self._wipe_offsets_recursive(definition)
            self._configure_imported_audio_media_entry(
                media_entry, metadata, index, base_filetime + index, profile
            )
            self._configure_imported_audio_clip_definition(
                definition, metadata, index, index, profile
            )
            media_entries.append(media_entry)
            definitions.append(definition)

        catalog = template["catalog"]
        media_positions = [
            index for index, item in enumerate(catalog.items)
            if isinstance(item, PTBlock) and item.content_type == 0x1003
        ]
        if len(media_positions) != 1:
            raise ValueError(
                "Audio-import template media insertion point is ambiguous."
            )
        media_position = media_positions[0]
        catalog.items[media_position:media_position + 1] = media_entries
        catalog_count = bytearray(catalog.items[0])
        struct.pack_into("<I", catalog_count, 0, len(media_entries))
        catalog.items[0] = catalog_count
        self._replace_imported_audio_catalog_filenames(
            template["name_block"],
            [item["physical_filename"] for item in prepared],
        )

        clip_list = template["clip_list"]
        definition_positions = [
            index for index, item in enumerate(clip_list.items)
            if isinstance(item, PTBlock) and item.content_type == 0x2629
        ]
        if len(definition_positions) != 1:
            raise ValueError(
                "Audio-import template clip insertion point is ambiguous."
            )
        definition_position = definition_positions[0]
        clip_list.items[
            definition_position:definition_position + 1
        ] = definitions
        clip_count = bytearray(clip_list.items[0])
        struct.pack_into("<I", clip_count, 0, len(definitions))
        clip_list.items[0] = clip_count

        playlists = template["playlists"]
        for clip_id, metadata in enumerate(prepared):
            self._append_imported_audio_event(
                playlists[metadata["track"]],
                clip_id,
                metadata["start_samples"],
            )

        return [
            {
                "order": item["order"],
                "source_path": item["source_path"],
                "physical_filename": item["physical_filename"],
                "clip_name": item["clip_name"],
                "track": item["track"],
                "bwf_time_reference": item["bwf_time_reference"],
                "start_samples": item["start_samples"],
                "length_samples": item["length_samples"],
                "end_samples": item["end_samples"],
            }
            for item in prepared
        ]

    def relink_clip(
        self,
        track_name,
        clip_name,
        placement_start_samples,
        new_clip_name,
        source_audio_path,
        new_audio_path,
        replacement_audio_path=None,
    ):
        """Clone one WAV identity and relink one exact placement to it."""
        original_root_items = copy.deepcopy(self.root_items)
        original_removed_offsets = list(getattr(self, "_removed_offsets", []))
        try:
            return self._relink_clip_impl(
                track_name,
                clip_name,
                placement_start_samples,
                new_clip_name,
                source_audio_path,
                new_audio_path,
                replacement_audio_path,
            )
        except Exception:
            self.root_items = original_root_items
            self._removed_offsets = original_removed_offsets
            raise

    def get_relink_write_status(
        self,
        track_name,
        clip_name,
        placement_start_samples,
    ):
        """Return whether a clip is safe for the verified relink writer.

        This is a read-only preflight for callers that need to avoid invoking
        :meth:`relink_clip` on a known unsupported virtual-media layout.  A
        ``supported`` result means that this specific known blocker was not
        found; it is not a substitute for the full validation performed by
        ``relink_clip``.
        """
        for value, label in ((track_name, "Track name"), (clip_name, "Clip name")):
            if not isinstance(value, str):
                raise TypeError(f"{label} must be a string.")
            if not value:
                raise ValueError(f"{label} must be non-empty.")
            if "\x00" in value:
                raise ValueError(f"{label} cannot contain a NUL character.")
        if (
            isinstance(placement_start_samples, bool)
            or not isinstance(placement_start_samples, int)
        ):
            raise TypeError("placement_start_samples must be an integer.")
        if placement_start_samples < 0 or placement_start_samples > 0xFFFFFFFFFFFFFFFF:
            raise ValueError("placement_start_samples must fit UInt64.")

        clip_id = self._resolve_unique_clip_id(clip_name)
        clip_list = self._root_blocks(0x262a)[0]
        definitions = [
            item for item in clip_list.items
            if isinstance(item, PTBlock) and item.content_type == 0x2629
        ]
        source_definition = definitions[clip_id]
        source_clip_info = self._audio_clip_info_by_id(clip_id)

        matching_events = []
        for playlist, visible_track_name, events in self._validated_main_playlists():
            if visible_track_name != track_name:
                continue
            for event in events:
                payload_blocks = [
                    child for child in event.items
                    if isinstance(child, PTBlock) and child.content_type == 0x104f
                ]
                if len(payload_blocks) != 1:
                    raise ValueError("Invalid 0x1050 event structure.")
                payload_block = payload_blocks[0]
                if (
                    not payload_block.items
                    or not isinstance(payload_block.items[0], (bytes, bytearray))
                    or len(payload_block.items[0]) < 16
                ):
                    raise ValueError("Invalid 0x104f event payload.")
                payload = payload_block.items[0]
                if self._audio_timeline_event_kind(event, payload) != "audio":
                    continue
                if (
                    struct.unpack_from("<I", payload, 2)[0] == clip_id
                    and struct.unpack_from("<Q", payload, 7)[0]
                    == placement_start_samples
                ):
                    matching_events.append(payload_block)
        if len(matching_events) != 1:
            raise ValueError(
                "Relink target must resolve exactly one placement on the requested track."
            )

        # Root-audio clips use the fixed layouts already covered by the
        # relink writer.  Production definitions with an embedded reference
        # use either a parent or virtual flag; both variants require the same
        # physical 0x2106 validation before the writer preserves that value.
        if source_clip_info["flags"] not in (
            0x0000, 0x2000, 0x2001, 0x3000, 0x3001, 0x4001,
        ):
            return {"supported": True, "code": "not_virtual_media"}

        _, (_, _, media_record) = self._validated_2629_fixed_records(
            source_definition
        )
        source_media_index = struct.unpack_from("<I", media_record, 96)[0]
        media_roots = self._root_blocks(0x1004)
        if len(media_roots) != 1:
            return {
                "supported": False,
                "code": "unverified_media_catalog",
                "reason": "Le catalogue média 0x1004 ne peut pas être vérifié en lecture seule.",
            }
        media_entries = [
            item for item in media_roots[0].items
            if isinstance(item, PTBlock) and item.content_type == 0x1003
        ]
        if source_media_index >= len(media_entries):
            return {
                "supported": False,
                "code": "unverified_media_catalog",
                "reason": "L'index média du clip est hors du catalogue 0x1004.",
            }
        detail_blocks = [
            child for child in media_entries[source_media_index].items
            if isinstance(child, PTBlock) and child.content_type == 0x2106
        ]
        if len(detail_blocks) != 1:
            return {
                "supported": False,
                "code": "unverified_media_header",
                "reason": "L'en-tête média 0x2106 du clip ne peut pas être vérifié.",
            }
        headers = [
            item for item in detail_blocks[0].items
            if isinstance(item, (bytes, bytearray)) and len(item) >= 142
        ]
        if len(headers) != 1:
            return {
                "supported": False,
                "code": "unverified_media_header",
                "reason": "L'en-tête média 0x2106 du clip est ambigu.",
            }
        header_length = len(headers[0])
        if header_length not in (142, 151):
            return {
                "supported": False,
                "code": "premiere_virtual_media",
                "reason": (
                    "Média virtuel à en-tête 0x2106 variable (layout de production "
                    "non pris en charge par l'écriture de relink)."
                ),
                "detail_header_length": header_length,
            }
        return {"supported": True, "code": "verified_virtual_media"}

    def _relink_clip_impl(
        self,
        track_name,
        clip_name,
        placement_start_samples,
        new_clip_name,
        source_audio_path,
        new_audio_path,
        replacement_audio_path,
    ):
        import tempfile
        import uuid

        for value, label in (
            (track_name, "Track name"),
            (clip_name, "Clip name"),
            (new_clip_name, "New clip name"),
        ):
            if not isinstance(value, str):
                raise TypeError(f"{label} must be a string.")
            if not value:
                raise ValueError(f"{label} must be non-empty.")
            if "\x00" in value:
                raise ValueError(f"{label} cannot contain a NUL character.")
        if (
            isinstance(placement_start_samples, bool)
            or not isinstance(placement_start_samples, int)
        ):
            raise TypeError("placement_start_samples must be an integer.")
        if placement_start_samples < 0 or placement_start_samples > 0xFFFFFFFFFFFFFFFF:
            raise ValueError("placement_start_samples must fit UInt64.")
        if placement_start_samples > 0xFFFFFFFF:
            raise ValueError(
                "The verified relink clip timestamp must fit UInt32."
            )

        try:
            new_clip_name_bytes = new_clip_name.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError("New clip name must be valid UTF-8.") from exc
        if len(new_clip_name_bytes) > 0xFFFFFFFF:
            raise ValueError("New clip name is too long for the PTX format.")

        source_audio_path = os.path.abspath(
            _coerce_string_path(source_audio_path, "source_audio_path")
        )
        new_audio_path = os.path.abspath(
            _coerce_string_path(new_audio_path, "new_audio_path")
        )
        if replacement_audio_path is not None:
            replacement_audio_path = os.path.abspath(
                _coerce_string_path(
                    replacement_audio_path, "replacement_audio_path"
                )
            )
            if not os.path.isfile(replacement_audio_path):
                raise FileNotFoundError(
                    "Replacement WAV does not exist: "
                    f"{replacement_audio_path}"
                )
        if not os.path.isfile(source_audio_path):
            raise FileNotFoundError(f"Source WAV does not exist: {source_audio_path}")
        destination_dir = os.path.dirname(new_audio_path)
        if not os.path.isdir(destination_dir):
            raise FileNotFoundError(
                f"New WAV directory does not exist: {destination_dir}"
            )
        if os.path.normcase(source_audio_path) == os.path.normcase(new_audio_path):
            raise ValueError("New WAV path must differ from the source WAV path.")
        if os.path.exists(new_audio_path):
            raise FileExistsError(f"New WAV already exists: {new_audio_path}")
        source_filename = os.path.basename(source_audio_path)
        new_filename = os.path.basename(new_audio_path)
        if not source_filename.lower().endswith(".wav") or not new_filename.lower().endswith(".wav"):
            raise ValueError("Relinking currently supports WAV files only.")
        source_stem = os.path.splitext(source_filename)[0]
        new_stem = os.path.splitext(new_filename)[0]
        try:
            source_stem_bytes = source_stem.encode("utf-8")
            new_stem_bytes = new_stem.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError("Physical WAV stems must be valid UTF-8.") from exc
        if len(new_stem_bytes) != len(source_stem_bytes):
            raise ValueError(
                "New WAV stem must have the same UTF-8 byte length as the source."
            )

        clip_id = self._resolve_unique_clip_id(clip_name)
        clip_list = self._root_blocks(0x262a)[0]
        definitions = [
            item for item in clip_list.items
            if isinstance(item, PTBlock) and item.content_type == 0x2629
        ]
        if len(definitions) > 0xFFFFFFFF:
            raise OverflowError("New clip ID exceeds UInt32.")
        self._purge_dangling_audio_timeline_events(len(definitions))
        for definition_id, definition in enumerate(definitions):
            info = self._audio_clip_info_by_id(definition_id)
            if definition_id != clip_id and info["name"] == new_clip_name:
                raise ValueError(
                    f"Clip name '{new_clip_name}' already exists in the session."
                )
        source_definition = definitions[clip_id]
        source_name_blocks = [
            child for child in source_definition.items
            if isinstance(child, PTBlock) and child.content_type == 0x2628
        ]
        if (
            len(source_name_blocks) != 1
            or not source_name_blocks[0].items
            or not isinstance(source_name_blocks[0].items[0], (bytes, bytearray))
        ):
            raise ValueError("Invalid source 0x2628 relink payload.")
        source_clip_payload = source_name_blocks[0].items[0]
        source_clip_name_length = struct.unpack_from(
            "<I", source_clip_payload, 0
        )[0]
        source_attributes = 4 + source_clip_name_length
        source_clip_info = self._decode_audio_clip_payload(source_clip_payload)
        source_offset = source_clip_info["src_offset"]
        if placement_start_samples < source_offset:
            raise ValueError(
                "Relink placement cannot precede the clip source offset."
            )
        new_media_time_reference = placement_start_samples - source_offset
        patch_root_timestamps = False
        virtual_embedded_reference = None
        virtual_embedded_tail_offset = None
        if (
            source_attributes + 16 <= len(source_clip_payload)
            and source_clip_payload[source_attributes:source_attributes + 5]
            == b"\x00\x00\x30\x44\x00"
        ):
            source_timestamp_offset = source_attributes + 8
            first_source_timestamp = struct.unpack_from(
                "<I", source_clip_payload, source_timestamp_offset
            )[0]
            second_source_timestamp = struct.unpack_from(
                "<I", source_clip_payload, source_timestamp_offset + 4
            )[0]
            if first_source_timestamp != second_source_timestamp:
                raise ValueError("Invalid root-audio 0x2628 time-reference pair.")
            patch_root_timestamps = True
        else:
            source_width_by_flags = {
                0x0000: 0,
                0x0001: 0,
                0x2000: 2,
                0x2001: 2,
                0x3000: 3,
                0x3001: 3,
                0x4001: 4,
            }
            length_width_by_selector = {
                0x10: 1,
                0x20: 2,
                0x30: 3,
                0x40: 4,
            }
            source_flags = source_clip_info["flags"]
            source_width = source_width_by_flags.get(source_flags)
            width_selector = (
                source_clip_payload[source_attributes + 2]
                if source_attributes + 5 <= len(source_clip_payload)
                else None
            )
            length_width = length_width_by_selector.get(width_selector)
            expected_layout_marker = ((source_flags >> 8) & 0xF0) | 0x04
            observed_premiere_virtual_layout = (
                source_flags == 0x3001
                and width_selector == 0x30
                and source_clip_payload[source_attributes + 3] in (0x04, 0x84)
                and source_clip_payload[source_attributes + 4] == 0x08
            )
            if (
                source_width is None
                or length_width is None
                or (
                    source_clip_payload[source_attributes + 3]
                    != expected_layout_marker
                    and not observed_premiere_virtual_layout
                )
                or source_clip_payload[source_attributes + 4] != 0x08
            ):
                raise ValueError(
                    "Relinking requires a verified root or production-virtual "
                    "0x2628 layout."
                )
            embedded_reference_offset = (
                source_attributes + 5 + source_width + length_width
            )
            if embedded_reference_offset + 4 > len(source_clip_payload):
                raise ValueError(
                    "Truncated production-virtual 0x2628 time reference."
                )
            virtual_embedded_reference = struct.unpack_from(
                "<I", source_clip_payload, embedded_reference_offset
            )[0]
            virtual_embedded_tail_offset = (
                embedded_reference_offset - source_attributes
            )
        _, (_, _, media_record) = self._validated_2629_fixed_records(
            source_definition
        )
        source_media_index = struct.unpack_from("<I", media_record, 96)[0]

        catalog, media_entries, name_block, physical_names, name_insert = (
            self._validated_physical_audio_catalog()
        )
        if source_media_index >= len(media_entries):
            raise ValueError("Clip media index is outside the 0x1004 catalog.")
        source_media_entry = media_entries[source_media_index]
        source_media_detail_blocks = [
            child for child in source_media_entry.items
            if isinstance(child, PTBlock) and child.content_type == 0x2106
        ]
        if len(source_media_detail_blocks) != 1:
            raise ValueError("Invalid source 0x2106 relink media template.")
        source_detail_headers = [
            item for item in source_media_detail_blocks[0].items
            if isinstance(item, (bytes, bytearray)) and len(item) >= 142
        ]
        if len(source_detail_headers) != 1:
            raise ValueError("Invalid source 0x2106 relink media template.")
        source_detail_header = source_detail_headers[0]
        # A virtual 0x2628 may retain an application-specific embedded
        # reference.  The source WAV identity is instead authored by its
        # physical 0x1003/0x2106 entry, which uses the fixed tail geometry.
        source_time_reference = struct.unpack_from(
            "<I", source_detail_header, len(source_detail_header) - 51
        )[0]
        new_virtual_embedded_reference = None
        if virtual_embedded_reference is not None:
            if virtual_embedded_reference < source_time_reference:
                raise ValueError(
                    "Invalid production-virtual 0x2628 time reference."
                )
            virtual_reference_delta = (
                virtual_embedded_reference - source_time_reference
            )
            if virtual_reference_delta > 0xFFFFFFFF - new_media_time_reference:
                raise OverflowError(
                    "Virtual 0x2628 time reference exceeds UInt32."
                )
            new_virtual_embedded_reference = (
                new_media_time_reference + virtual_reference_delta
            )
        if physical_names[source_media_index].casefold() != source_filename.casefold():
            raise ValueError(
                "source_audio_path does not match the clip's physical WAV entry."
            )
        if any(name.casefold() == new_filename.casefold() for name in physical_names):
            raise ValueError(f"Physical WAV '{new_filename}' already exists in the session.")
        new_media_index = len(media_entries)
        if new_media_index > 0xFFFFFFFF:
            raise OverflowError("Verified clip media indexes are limited to UInt32.")
        try:
            new_filename_bytes = new_filename.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError("New physical WAV filename must be valid UTF-8.") from exc
        if len(new_filename_bytes) > 0xFFFFFFFF:
            raise ValueError("New physical WAV filename is too long.")

        matching_events = []
        for playlist, visible_track_name, events in self._validated_main_playlists():
            if visible_track_name != track_name:
                continue
            for event in events:
                payload_block = next(
                    child for child in event.items
                    if isinstance(child, PTBlock) and child.content_type == 0x104f
                )
                payload = payload_block.items[0]
                if self._audio_timeline_event_kind(event, payload) != "audio":
                    continue
                event_clip_id = struct.unpack_from("<I", payload, 2)[0]
                event_start = struct.unpack_from("<Q", payload, 7)[0]
                if event_clip_id == clip_id and event_start == placement_start_samples:
                    matching_events.append(payload_block)
        if len(matching_events) != 1:
            raise ValueError(
                "Relink target must resolve exactly one placement on the requested track."
            )

        descriptor = None
        temporary_path = None
        try:
            descriptor, temporary_path = tempfile.mkstemp(
                prefix=f".{new_filename}.", suffix=".tmp", dir=destination_dir
            )
            os.close(descriptor)
            descriptor = None
            identity = _clone_pro_tools_wave(
                source_audio_path,
                temporary_path,
                new_stem,
                source_time_reference,
                new_media_time_reference,
                replacement_audio_path,
            )

            new_media_entry = copy.deepcopy(source_media_entry)
            self._wipe_offsets_recursive(new_media_entry)
            ordinal_payload = bytearray(new_media_entry.items[0])
            struct.pack_into("<I", ordinal_payload, 0, new_media_index + 1)
            new_media_entry.items[0] = ordinal_payload

            media_info_blocks = [
                child for child in new_media_entry.items
                if isinstance(child, PTBlock) and child.content_type == 0x1001
            ]
            media_detail_blocks = [
                child for child in new_media_entry.items
                if isinstance(child, PTBlock) and child.content_type == 0x2106
            ]
            if len(media_info_blocks) != 1 or len(media_detail_blocks) != 1:
                raise ValueError("Invalid 0x1003 relink media template.")
            media_info = media_info_blocks[0]
            media_info_payload = self._serialize_fixed_record_items(
                media_info.items,
                "Invalid item in a 0x1001 relink media identity.",
            )
            if len(media_info_payload) != 31:
                raise ValueError("Invalid 0x1001 relink media identity.")
            media_info_payload[22:31] = identity["file_identifier"]
            media_info.items = [media_info_payload]

            media_detail = media_detail_blocks[0]
            detail_headers = [
                (index, item) for index, item in enumerate(media_detail.items)
                if isinstance(item, (bytes, bytearray)) and len(item) >= 142
            ]
            detail_tails = [
                (index, item) for index, item in enumerate(media_detail.items)
                if isinstance(item, (bytes, bytearray)) and len(item) == 58
            ]
            if len(detail_headers) != 1 or len(detail_tails) != 1:
                raise ValueError("Invalid 0x2106 relink media template.")
            header_index, detail_header = detail_headers[0]
            detail_header = bytearray(detail_header)
            # The 0x2106 prefix may contain application metadata (Premiere
            # sessions observed at 169/173 bytes).  Its mutable tail retains
            # the same geometry as the native 142- and 151-byte variants.
            time_reference_offset = len(detail_header) - 51
            second_filetime_offset = len(detail_header) - 46
            uuid_offset = len(detail_header) - 16
            struct.pack_into(
                "<Q", detail_header, 29, identity["rounded_filetime"]
            )
            struct.pack_into(
                "<I",
                detail_header,
                time_reference_offset,
                new_media_time_reference,
            )
            struct.pack_into(
                "<Q",
                detail_header,
                second_filetime_offset,
                identity["prior_filetime"],
            )
            detail_header[uuid_offset:uuid_offset + 16] = uuid.uuid4().bytes
            media_detail.items[header_index] = detail_header
            tail_index, detail_tail = detail_tails[0]
            detail_tail = bytearray(detail_tail)
            detail_tail[18:50] = identity["umid"][:32]
            media_detail.items[tail_index] = detail_tail

            name_payload = bytearray(name_block.items[0])
            record_suffix = bytes(name_payload[name_insert - 4:name_insert])
            if record_suffix not in (b"EVAW", b"\x00" * 4):
                raise ValueError("Invalid 0x103a WAV filename record suffix.")
            top_count = struct.unpack_from("<I", name_payload, 0)[0]
            nested_count = struct.unpack_from("<I", name_payload, 5)[0]
            trailer_count_offsets = self._decode_1004_catalog_trailer(
                name_payload, name_insert, new_media_index
            )
            trailer_depth = len(trailer_count_offsets)
            if (
                top_count != new_media_index + trailer_depth + 2
                or nested_count != new_media_index + trailer_depth + 1
            ):
                raise ValueError("Inconsistent 0x103a relink counters.")
            if (
                top_count == 0xFFFFFFFF
                or nested_count == 0xFFFFFFFF
                or any(
                    struct.unpack_from("<I", name_payload, offset)[0]
                    == 0xFFFFFFFF
                    for offset in trailer_count_offsets
                )
            ):
                raise OverflowError("0x103a physical-filename count exceeds UInt32.")
            struct.pack_into("<I", name_payload, 0, top_count + 1)
            struct.pack_into("<I", name_payload, 5, nested_count + 1)
            for offset in trailer_count_offsets:
                trailer_count = struct.unpack_from("<I", name_payload, offset)[0]
                struct.pack_into("<I", name_payload, offset, trailer_count + 1)
            filename_record = bytearray(b"\x02\x00\x00\x00\x00")
            filename_record.extend(struct.pack("<I", len(new_filename_bytes)))
            filename_record.extend(new_filename_bytes)
            filename_record.extend(record_suffix)
            name_payload[name_insert:name_insert] = filename_record
            name_block.items[0] = name_payload

            catalog_count = bytearray(catalog.items[0])
            struct.pack_into("<I", catalog_count, 0, new_media_index + 1)
            catalog.items[0] = catalog_count
            last_media_position = max(
                index for index, item in enumerate(catalog.items)
                if isinstance(item, PTBlock) and item.content_type == 0x1003
            )
            catalog.items.insert(last_media_position + 1, new_media_entry)

            new_definition = copy.deepcopy(source_definition)
            self._wipe_offsets_recursive(new_definition)
            identity_span, media_span = self._validated_2629_fixed_records(
                new_definition
            )
            identity_start, identity_end, clip_identity = identity_span
            media_start, media_end, cloned_record = media_span

            cloned_record = bytearray(cloned_record)
            struct.pack_into("<I", cloned_record, 96, new_media_index)
            new_definition.items[media_start:media_end] = [cloned_record]

            clip_identity = bytearray(clip_identity)
            struct.pack_into("<I", clip_identity, 0, len(definitions))
            clip_identity[23:39] = uuid.uuid4().bytes
            new_definition.items[identity_start:identity_end] = [clip_identity]

            name_blocks = [
                child for child in new_definition.items
                if isinstance(child, PTBlock) and child.content_type == 0x2628
            ]
            if (
                len(name_blocks) != 1
                or not name_blocks[0].items
                or not isinstance(name_blocks[0].items[0], (bytes, bytearray))
            ):
                raise ValueError("Invalid relink 0x2628 clip payload.")
            source_name_payload = name_blocks[0].items[0]
            source_name_length = struct.unpack_from("<I", source_name_payload, 0)[0]
            source_tail = source_name_payload[4 + source_name_length:]
            new_name_payload = bytearray(
                4 + len(new_clip_name_bytes) + len(source_tail)
            )
            struct.pack_into("<I", new_name_payload, 0, len(new_clip_name_bytes))
            new_name_payload[4:4 + len(new_clip_name_bytes)] = new_clip_name_bytes
            new_name_payload[4 + len(new_clip_name_bytes):] = source_tail
            if patch_root_timestamps:
                new_timestamp_offset = 4 + len(new_clip_name_bytes) + 8
                struct.pack_into(
                    "<I",
                    new_name_payload,
                    new_timestamp_offset,
                    placement_start_samples,
                )
                struct.pack_into(
                    "<I",
                    new_name_payload,
                    new_timestamp_offset + 4,
                    placement_start_samples,
                )
            elif new_virtual_embedded_reference is not None:
                new_embedded_reference_offset = (
                    4 + len(new_clip_name_bytes) + virtual_embedded_tail_offset
                )
                struct.pack_into(
                    "<I",
                    new_name_payload,
                    new_embedded_reference_offset,
                    new_virtual_embedded_reference,
                )
            name_blocks[0].items[0] = new_name_payload

            clip_count_payload = bytearray(clip_list.items[0])
            struct.pack_into("<I", clip_count_payload, 0, len(definitions) + 1)
            clip_list.items[0] = clip_count_payload
            last_definition_position = max(
                index for index, item in enumerate(clip_list.items)
                if isinstance(item, PTBlock) and item.content_type == 0x2629
            )
            clip_list.items.insert(last_definition_position + 1, new_definition)

            target_event_payload = bytearray(matching_events[0].items[0])
            struct.pack_into("<I", target_event_payload, 2, len(definitions))
            matching_events[0].items[0] = target_event_payload

            os.replace(temporary_path, new_audio_path)
            temporary_path = None
            return {
                "clip_id": len(definitions),
                "clip_name": new_clip_name,
                "physical_index": new_media_index,
                "physical_filename": new_filename,
            }
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if temporary_path is not None:
                try:
                    os.remove(temporary_path)
                except FileNotFoundError:
                    pass

    def duplicate_clip(self, clip_name, hh, mm, ss, ff, mute=False):
        """Atomically duplicate one unambiguous visible audio placement."""
        original_root_items = copy.deepcopy(self.root_items)
        original_removed_offsets = list(getattr(self, '_removed_offsets', []))
        try:
            return self._duplicate_clip_impl(clip_name, hh, mm, ss, ff, mute)
        except Exception:
            self.root_items = original_root_items
            self._removed_offsets = original_removed_offsets
            raise

    def _duplicate_clip_impl(self, clip_name, hh, mm, ss, ff, mute=False):
        if not isinstance(mute, bool):
            raise TypeError("mute must be a boolean.")

        engine = TimecodeEngine(self.sample_rate, self.frame_rate_enum)
        new_samples = engine.timecode_to_samples(hh, mm, ss, ff)
        if new_samples > 0xFFFFFFFFFFFFFFFF:
            raise ValueError("Target sample position is outside the UInt64 range.")
        clip_id = self._resolve_unique_clip_id(clip_name)
        timeline_events = self._validated_main_timeline_events()
        placements = []
        for entry in timeline_events:
            _, event, _, payload = entry
            if self._audio_timeline_event_kind(event, payload) != "audio":
                continue
            if struct.unpack_from("<I", payload, 2)[0] == clip_id:
                placements.append(entry)

        if not placements:
            raise ValueError(f"Clip '{clip_name}' has no visible audio placement.")
        if len(placements) != 1:
            raise ValueError(
                f"Clip '{clip_name}' has multiple visible placements; "
                "the duplicate source is ambiguous."
            )

        target_b1052, target_event, _, source_payload = placements[0]
        if len(source_payload) != 35:
            raise ValueError("Audio event 0x104f payload must be exactly 35 bytes.")
        source_start = struct.unpack_from("<Q", source_payload, 7)[0]
        source_length = self._audio_clip_info_by_id(clip_id)["length"]
        related_fades = self._fade_bindings_for_audio_placement(
            source_start, source_length, target_b1052, timeline_events
        )
        if related_fades or source_payload[33] != 0:
            raise NotImplementedError(
                "Duplicating a clip placement with attached fades is not supported safely yet."
            )

        header = bytearray(target_b1052.items[0])
        nlen = struct.unpack_from("<I", header, 0)[0]
        count_offset = 4 + nlen
        current_count = struct.unpack_from("<I", header, count_offset)[0]
            
        # Duplicate the event
        new_event = copy.deepcopy(target_event)
        # Clear the original_offset so it is treated as a new block (won't inherit 0002 pointer)
        self._wipe_offsets_recursive(new_event)
        
        b104f = next(c for c in new_event.items if isinstance(c, PTBlock) and c.content_type == 0x104f)
        payload = bytearray(b104f.items[0])
        
        payload[0] = 0x01 if mute else 0x00
        struct.pack_into("<Q", payload, 7, new_samples)
            
        b104f.items[0] = payload
        
        # Insert after events at the same timestamp, but before the first later
        # event. The resulting 0x1052 playlist remains chronological.
        insert_idx = 1
        for i in range(1, len(target_b1052.items)):
            event = target_b1052.items[i]
            if not isinstance(event, PTBlock) or event.content_type != 0x1050:
                continue
            event_104f = next((c for c in event.items if isinstance(c, PTBlock) and c.content_type == 0x104f), None)
            if not event_104f or not event_104f.items or not isinstance(event_104f.items[0], (bytes, bytearray)):
                continue
            event_payload = event_104f.items[0]
            if len(event_payload) < 15:
                continue
            event_timestamp = struct.unpack_from("<Q", event_payload, 7)[0]
            if event_timestamp > new_samples:
                insert_idx = i
                break
            insert_idx = i + 1

        target_b1052.items.insert(insert_idx, new_event)

        struct.pack_into("<I", header, count_offset, current_count + 1)
        target_b1052.items[0] = header
        
        logger.info(
            "Duplicated clip %r to %02d:%02d:%02d:%02d, mute=%s",
            clip_name, hh, mm, ss, ff, mute,
        )
        
        return clip_id

    
    def split_clip(self, track_name, clip_name, cut_hh, cut_mm, cut_ss, cut_ff):
        """Atomically split one audio placement into two virtual sub-clips."""
        components = (cut_hh, cut_mm, cut_ss, cut_ff)
        if any(isinstance(value, bool) or not isinstance(value, int) for value in components):
            raise TypeError("Timecode components must be integers.")
        if (
            cut_hh < 0 or cut_mm < 0 or cut_mm > 59
            or cut_ss < 0 or cut_ss > 59 or cut_ff < 0
        ):
            raise ValueError("Invalid cut timecode components.")

        engine = TimecodeEngine(self.sample_rate, self.frame_rate_enum)
        _, is_drop_frame = engine.get_frame_rate()
        nominal_fps = 30 if self.frame_rate_enum == 0x05 else (
            24 if self.frame_rate_enum == 0x09 else int(engine.get_frame_rate()[0])
        )
        if cut_ff >= nominal_fps:
            raise ValueError(f"Frame component must be below {nominal_fps}.")
        if is_drop_frame and cut_mm % 10 != 0 and cut_ss == 0 and cut_ff < 2:
            raise ValueError("Cut timecode falls on a dropped frame number.")

        original_root_items = copy.deepcopy(self.root_items)
        original_removed_offsets = list(getattr(self, '_removed_offsets', []))
        try:
            return self._split_clip_impl(
                track_name, clip_name, cut_hh, cut_mm, cut_ss, cut_ff
            )
        except Exception:
            self.root_items = original_root_items
            self._removed_offsets = original_removed_offsets
            raise

    def _split_clip_impl(self, track_name, clip_name, cut_hh, cut_mm, cut_ss, cut_ff):
        engine = TimecodeEngine(self.sample_rate, self.frame_rate_enum)
        cut_samples = engine.timecode_to_samples(cut_hh, cut_mm, cut_ss, cut_ff)
        if not isinstance(track_name, str) or not track_name:
            raise ValueError("track_name must be a non-empty string.")
        if not isinstance(clip_name, str) or not clip_name:
            raise ValueError("clip_name must be a non-empty string.")
        
        # 1. Find the clip definition (0x2629)
        clip_lists = self._root_blocks(0x262a)
        if not clip_lists:
            raise ValueError("No clip list (0x262a) found.")
        if len(clip_lists) != 1:
            raise ValueError("Ambiguous session: multiple root 0x262a clip lists.")
        b262a = clip_lists[0]
        if (
            not b262a.items
            or not isinstance(b262a.items[0], (bytes, bytearray))
            or len(b262a.items[0]) < 4
        ):
            raise ValueError("Invalid 0x262a clip counter payload.")

        orig_2629 = None
        orig_clip_id = -1
        clips = [c for c in b262a.items if isinstance(c, PTBlock) and c.content_type == 0x2629]
        declared_clips = struct.unpack_from("<I", b262a.items[0], 0)[0]
        if declared_clips != len(clips):
            raise ValueError(
                f"Inconsistent 0x262a clip count: declared={declared_clips}, "
                f"actual={len(clips)}."
            )

        clip_names = []
        for i, c in enumerate(clips):
            name_blocks = [
                item for item in c.items
                if isinstance(item, PTBlock) and item.content_type == 0x2628
            ]
            if (
                len(name_blocks) != 1
                or not name_blocks[0].items
                or not isinstance(name_blocks[0].items[0], (bytes, bytearray))
                or len(name_blocks[0].items[0]) < 4
            ):
                raise ValueError("Invalid 0x2629 clip definition.")
            pl = name_blocks[0].items[0]
            nlen = struct.unpack_from("<I", pl, 0)[0]
            if 4 + nlen > len(pl):
                raise ValueError("Invalid 0x2628 clip name length.")
            try:
                name = pl[4:4 + nlen].decode('utf-8').strip('\x00')
            except UnicodeDecodeError as exc:
                raise ValueError("Invalid UTF-8 clip name.") from exc
            clip_names.append(name)
            if name == clip_name:
                if orig_2629 is not None:
                    raise ValueError(f"Clip name '{clip_name}' is ambiguous in the session.")
                orig_2629 = c
                orig_clip_id = i

        if not orig_2629:
            raise ValueError(f"Clip '{clip_name}' not found.")
            
        # 2. Extract original length and timestamps
        b2628_orig = next((x for x in orig_2629.items if isinstance(x, PTBlock) and x.content_type == 0x2628), None)
        pl_orig = b2628_orig.items[0]
        nlen = struct.unpack_from("<I", pl_orig, 0)[0]
        offset = 4 + nlen
        if offset + 17 > len(pl_orig):
            raise ValueError("Invalid root 0x2628 clip payload.")
        
        # Pro Tools uses selector 0x30 for ordinary roots and 0x40 when the
        # UInt32 length needs its fourth byte. Both keep the timestamps at +9.
        hdr = pl_orig[offset:offset+5]
        if hdr not in (
            b"\x00\x00\x30\x44\x00",
            b"\x00\x00\x40\x44\x00",
            b"\x01\x00\x30\x44\x00",
        ):
            raise ValueError(
                "Clip is already a split or has an unsupported root layout."
            )
            
        orig_length = struct.unpack_from("<I", pl_orig, offset+5)[0]
        orig_time_1 = bytes(pl_orig[offset+10:offset+13])
        orig_time_2 = bytes(pl_orig[offset+14:offset+17])
        
        # 3. Find the track and event on the timeline
        track_maps = self._root_blocks(0x1054)
        if not track_maps:
            raise ValueError("No track map (0x1054) found.")
        if len(track_maps) != 1:
            raise ValueError("Ambiguous session: multiple root 0x1054 track maps.")
        b1054 = track_maps[0]
        if (
            not b1054.items
            or not isinstance(b1054.items[0], (bytes, bytearray))
            or len(b1054.items[0]) < 4
        ):
            raise ValueError("Invalid 0x1054 track counter payload.")
        tracks = [
            item for item in b1054.items
            if isinstance(item, PTBlock) and item.content_type == 0x1052
        ]
        declared_tracks = struct.unpack_from("<I", b1054.items[0], 0)[0]
        if declared_tracks != len(tracks):
            raise ValueError(
                f"Inconsistent 0x1054 track count: declared={declared_tracks}, "
                f"actual={len(tracks)}."
            )

        matching_tracks = []
        for track in tracks:
            if (
                not track.items
                or not isinstance(track.items[0], (bytes, bytearray))
                or len(track.items[0]) < 8
            ):
                raise ValueError("Invalid 0x1052 playlist header.")
            track_header = track.items[0]
            track_name_length = struct.unpack_from("<I", track_header, 0)[0]
            count_offset = 4 + track_name_length
            if count_offset + 4 > len(track_header):
                raise ValueError("Invalid 0x1052 event counter offset.")
            try:
                parsed_name = track_header[4:count_offset].decode('utf-8').strip('\x00')
            except UnicodeDecodeError as exc:
                raise ValueError("Invalid UTF-8 track name.") from exc
            events = [
                item for item in track.items
                if isinstance(item, PTBlock) and item.content_type == 0x1050
            ]
            declared_events = struct.unpack_from("<I", track_header, count_offset)[0]
            if declared_events != len(events):
                raise ValueError(
                    f"Inconsistent 0x1052 event count: declared={declared_events}, "
                    f"actual={len(events)}."
                )
            if parsed_name == track_name:
                matching_tracks.append(track)

        if not matching_tracks:
            raise ValueError(f"Track '{track_name}' not found.")
        if len(matching_tracks) != 1:
            raise ValueError(f"Track name '{track_name}' is ambiguous in the session.")
        b1052 = matching_tracks[0]
            
        matching_events = []
        for i, ev in enumerate(b1052.items):
            if isinstance(ev, PTBlock) and ev.content_type == 0x1050:
                event_payloads = [
                    item for item in ev.items
                    if isinstance(item, PTBlock) and item.content_type == 0x104f
                ]
                if (
                    len(event_payloads) != 1
                    or not event_payloads[0].items
                    or not isinstance(event_payloads[0].items[0], (bytes, bytearray))
                    or len(event_payloads[0].items[0]) < 16
                ):
                    raise ValueError("Invalid 0x1050 timeline event.")
                payload = event_payloads[0].items[0]
                if self._audio_timeline_event_kind(ev, payload) != "audio":
                    continue
                ev_clip_id = struct.unpack_from("<I", payload, 2)[0]
                if ev_clip_id == orig_clip_id:
                    ts = struct.unpack_from("<Q", payload, 7)[0]
                    if ts < cut_samples < ts + orig_length:
                        matching_events.append((i, ev, event_payloads[0], ts))

        if not matching_events:
            raise ValueError(f"Could not find an instance of clip '{clip_name}' on track '{track_name}' that spans the cut timecode.")
        if len(matching_events) != 1:
            raise ValueError("Multiple clip placements span the requested cut timecode.")

        orig_1050_idx, orig_1050, orig_104f, event_start_samples = matching_events[0]
        orig_abs_ts = event_start_samples
        related_fades = self._fade_bindings_for_audio_placement(
            event_start_samples, orig_length, b1052
        )
        if related_fades:
            raise NotImplementedError(
                "Splitting a clip with attached fades is not supported safely yet."
            )
            
        relative_cut_samples = cut_samples - event_start_samples
        rem_length = orig_length - relative_cut_samples
        if relative_cut_samples > 0xFFFFFF:
            raise ValueError(
                "Split source offset must fit in the verified 24-bit field."
            )
        if cut_samples > 0xFFFFFFFF:
            raise ValueError("Split timestamp exceeds the 32-bit 0x2628 timestamp range.")

        suffix_values = []
        prefix = clip_name + "-"
        for existing_name in clip_names:
            if existing_name.startswith(prefix):
                suffix = existing_name[len(prefix):]
                if suffix.isdigit():
                    suffix_values.append(int(suffix))
        left_suffix = max(suffix_values, default=0) + 1
        right_suffix = left_suffix + 1
        new_clip_name_01 = f"{clip_name}-{left_suffix:02d}"
        new_clip_name_02 = f"{clip_name}-{right_suffix:02d}"
        
        # 4. Build Left Clip (-01)
        import uuid
        clip_01 = copy.deepcopy(orig_2629)
        self._wipe_offsets_recursive(clip_01)
        # Fix UUID
        for i, item in enumerate(clip_01.items):
            if isinstance(item, bytearray) and len(item) == 48:
                new_ba = bytearray(item)
                new_ba[22:38] = uuid.uuid4().bytes
                clip_01.items[i] = bytes(new_ba)
        # Fix 0x2628
        b2628_01 = next(x for x in clip_01.items if isinstance(x, PTBlock) and x.content_type == 0x2628)
        new_name_01 = new_clip_name_01.encode('utf-8')
        pl_01 = bytearray()
        pl_01.extend(struct.pack("<I", len(new_name_01)))
        pl_01.extend(new_name_01)
        pl_01.extend(b"\x01\x00\x30\x44\x08") # Header
        # UInt32 field; the verified source-offset constraint currently keeps
        # this left-fragment length within 24 bits.
        pl_01.append(relative_cut_samples & 0xFF)
        pl_01.append((relative_cut_samples >> 8) & 0xFF)
        pl_01.append((relative_cut_samples >> 16) & 0xFF)
        pl_01.append(0x00)
        pl_01[-1] = (relative_cut_samples >> 24) & 0xFF
        pl_01.extend(orig_time_1 + b"\x00")
        pl_01.extend(orig_time_2 + b"\xff")
        pl_01.extend(b"\xff\xff\xff\xff\xff\xff\xff\xfe") # 8 bytes padding
        # The "rest" after padding must have metadata zeroed out for sub-clips.
        # Parent's rest = pl_orig[offset+17:] contains UUID bytes that must NOT be copied.
        # Build the correct rest: ff 00000000 ffff 0400 0400 + 18 zero bytes + ffffffff 0000
        rest_01 = bytearray()
        rest_01.extend(b"\xff\x00\x00\x00\x00\xff\xff\x04\x00\x04\x00")  # 11 bytes
        rest_01.extend(b"\x00" * 21)  # 21 zero bytes (no UUID metadata for sub-clips)
        rest_01.extend(b"\xff\xff\xff\xff\x00\x00")  # 6 bytes
        pl_01.extend(rest_01)
        b2628_01.items[0] = bytes(pl_01)
        
        # 4. Build Right Clip (-02)
        clip_02 = copy.deepcopy(orig_2629)
        self._wipe_offsets_recursive(clip_02)
        for i, item in enumerate(clip_02.items):
            if isinstance(item, bytearray) and len(item) == 48:
                new_ba = bytearray(item)
                new_ba[22:38] = uuid.uuid4().bytes
                clip_02.items[i] = bytes(new_ba)
        
        b2628_02 = next(x for x in clip_02.items if isinstance(x, PTBlock) and x.content_type == 0x2628)
        new_name_02 = new_clip_name_02.encode('utf-8')
        pl_02 = bytearray()
        pl_02.extend(struct.pack("<I", len(new_name_02)))
        pl_02.extend(new_name_02)
        right_length_width = 3 if rem_length <= 0xFFFFFF else 4
        right_width_selector = b"\x30" if right_length_width == 3 else b"\x40"
        pl_02.extend(b"\x01\x30" + right_width_selector + b"\x44\x08")
        
        # 24-bit offset
        pl_02.append(relative_cut_samples & 0xFF)
        pl_02.append((relative_cut_samples >> 8) & 0xFF)
        pl_02.append((relative_cut_samples >> 16) & 0xFF)
        
        # Selector 0x30 carries a UInt24 length; selector 0x40 carries UInt32.
        pl_02.extend(rem_length.to_bytes(right_length_width, "little"))
        
        # 32-bit TS1 and TS2 — these are TIMELINE ABSOLUTE timestamps,
        # NOT internal clip offsets. Must use orig_abs_ts (from 0x104f) + relative_cut_samples.
        pl_02.extend(b"\x00" * 8)  # placeholder for ts1/ts2, will be patched below
        pl_02.extend(b"\xff\xff\xff\xff\xff\xff\xff\xff")  # 8 bytes ff padding
        # Build the correct rest with zeroed metadata
        rest_02 = bytearray()
        rest_02.extend(b"\xfe\xff\x00\x00\x00\x00\xff\xff\x04\x00\x04\x00")  # 12 bytes
        rest_02.extend(b"\x00" * 21)  # 21 zero bytes
        rest_02.extend(b"\xff\xff\xff\xff\x00\x00")  # 6 bytes
        pl_02.extend(rest_02)
        b2628_02.items[0] = bytes(pl_02)
        
        # 5. Append to 0x262a
        count_ba = bytearray(b262a.items[0])
        old_count = struct.unpack_from("<I", count_ba, 0)[0]
        
        idx_01 = old_count
        idx_02 = old_count + 1
        
        # Patch the new IDs into the 0x2629 payloads
        for i, it in enumerate(clip_01.items):
            if isinstance(it, (bytes, bytearray)) and len(it) >= 4:
                p = bytearray(it)
                struct.pack_into("<I", p, 0, idx_01)
                clip_01.items[i] = p
                break
                
        for i, it in enumerate(clip_02.items):
            if isinstance(it, (bytes, bytearray)) and len(it) >= 4:
                p = bytearray(it)
                struct.pack_into("<I", p, 0, idx_02)
                clip_02.items[i] = p
                break
        
        struct.pack_into("<I", count_ba, 0, old_count + 2)
        b262a.items[0] = count_ba
        last_clip_position = max(
            index for index, item in enumerate(b262a.items)
            if isinstance(item, PTBlock) and item.content_type == 0x2629
        )
        b262a.items.insert(last_clip_position + 1, clip_01)
        b262a.items.insert(last_clip_position + 2, clip_02)
        
        # 6. Update Timeline (0x1052)
        # Variables b1052, orig_1050, orig_1050_idx, and orig_abs_ts were found in step 3.
        
        cut_abs_ts = orig_abs_ts + relative_cut_samples
        
        # Now patch clip -02's ts1/ts2 with the correct TIMELINE absolute timestamp
        pl_02_patched = bytearray(b2628_02.items[0])
        nlen_02 = struct.unpack_from("<I", pl_02_patched, 0)[0]
        ts_offset = 4 + nlen_02 + 5 + 3 + right_length_width
        struct.pack_into("<I", pl_02_patched, ts_offset, cut_abs_ts)
        struct.pack_into("<I", pl_02_patched, ts_offset + 4, cut_abs_ts)
        b2628_02.items[0] = bytes(pl_02_patched)
            
        # Create ev_02 as a deep copy with new clip ID and timestamp
        ev_02 = copy.deepcopy(orig_1050)
        self._wipe_offsets_recursive(ev_02)
        ev_02_104f = next(
            item for item in ev_02.items
            if isinstance(item, PTBlock) and item.content_type == 0x104f
        )
        p_02 = bytearray(ev_02_104f.items[0])
        struct.pack_into("<I", p_02, 2, idx_02)
        struct.pack_into("<Q", p_02, 7, cut_abs_ts)
        ev_02_104f.items[0] = bytearray(p_02)
        
        # Mutate the original event IN PLACE to become ev_01.
        # CRITICAL: We must NOT pop() the original event, because its pointer
        # exists in the 0x0002 table. Removing it creates a dangling pointer
        # that causes "Magic ID does not match".
        p_orig = bytearray(orig_104f.items[0])
        struct.pack_into("<I", p_orig, 2, idx_01)
        orig_104f.items[0] = bytearray(p_orig)
        
        # Insert ev_02 AFTER the mutated orig (now ev_01)
        b1052.items.insert(orig_1050_idx + 1, ev_02)
        
        # Update event counter (+1, not +2, because we only inserted one new event)
        hdr = bytearray(b1052.items[0])
        nlen = struct.unpack_from("<I", hdr, 0)[0]
        count_offset = 4 + nlen
        old_count = struct.unpack_from("<I", hdr, count_offset)[0]
        struct.pack_into("<I", hdr, count_offset, old_count + 1)
        b1052.items[0] = bytearray(hdr)
        
        return orig_clip_id, idx_01, idx_02, cut_abs_ts
        
    def add_crossfade(self, track_name, clip_name, cut_hh, cut_mm, cut_ss, cut_ff, fade_hh, fade_mm, fade_ss, fade_ff):
        """Atomically split a clip and add a centered Equal Power crossfade."""
        engine = TimecodeEngine(self.sample_rate, self.frame_rate_enum)
        fade_samples = engine.duration_to_samples(
            fade_hh, fade_mm, fade_ss, fade_ff
        )
        if fade_samples <= 0:
            raise ValueError("Crossfade duration must be greater than zero.")
        if fade_samples > 0xFFFF:
            raise ValueError("Crossfade duration exceeds the 16-bit geometry limit.")

        self._validated_fade_geometry_list()

        # split_clip() necessarily performs several linked mutations. Keep the
        # composite operation transactional so a late structural error cannot
        # leave a half-split session in memory and later be saved accidentally.
        original_root_items = copy.deepcopy(self.root_items)
        original_removed_offsets = list(getattr(self, '_removed_offsets', []))
        try:
            return self._add_crossfade_impl(
                track_name, clip_name,
                cut_hh, cut_mm, cut_ss, cut_ff,
                fade_hh, fade_mm, fade_ss, fade_ff,
            )
        except Exception:
            self.root_items = original_root_items
            self._removed_offsets = original_removed_offsets
            raise

    def _add_crossfade_impl(self, track_name, clip_name, cut_hh, cut_mm, cut_ss, cut_ff, fade_hh, fade_mm, fade_ss, fade_ff):
        """Splits a clip and injects a crossfade at the split point."""
        # 1. Split the clip
        _, _, idx_02, cut_abs_ts = self.split_clip(
            track_name, clip_name, cut_hh, cut_mm, cut_ss, cut_ff
        )
        
        engine = TimecodeEngine(self.sample_rate, self.frame_rate_enum)
        fade_samples = engine.duration_to_samples(fade_hh, fade_mm, fade_ss, fade_ff)
        half_fade = fade_samples // 2
        
        # 2. Geometry (0x262f)
        b2630, geometries = self._validated_fade_geometry_list()
        count_ba = bytearray(b2630.items[0])
        old_count = len(geometries)
        struct.pack_into("<I", count_ba, 0, old_count + 1)
        b2630.items[0] = count_ba
        
        # Native crossfade geometry is a fixed 34-byte payload. Both pre-roll
        # and total duration are UInt16 LE, followed by the Equal Power flags.
        geom_pl = bytearray(34)
        geom_pl[5] = 0x22
        struct.pack_into("<H", geom_pl, 8, half_fade)
        struct.pack_into("<H", geom_pl, 10, fade_samples)
        geom_pl[12] = 0x01
        geom_pl[13] = 0x01
        
        new_262f = PTBlock(2, 0x262f, 0)
        new_262f.items = [bytes(geom_pl)]
        b2630.items.append(new_262f)
        
        # 3. Fade Event (0x1050). Reuse the strict playlist validator so an
        # invalid UTF-8 name or inconsistent count can never be ignored while
        # resolving the target track.
        matching_tracks = [
            playlist
            for playlist, name, _ in self._validated_main_playlists()
            if name == track_name
        ]
        if not matching_tracks:
            raise ValueError(f"Track '{track_name}' not found.")
        if len(matching_tracks) != 1:
            raise ValueError(f"Track name '{track_name}' is ambiguous.")
        b1052 = matching_tracks[0]
                        
        # Find insertion point (just before ev_02)
        ev_idx = -1
        for i, ev in enumerate(b1052.items):
            if isinstance(ev, PTBlock) and ev.content_type == 0x1050:
                b104f = next((x for x in ev.items if isinstance(x, PTBlock) and x.content_type == 0x104f), None)
                if b104f:
                    p = b104f.items[0]
                    cid = struct.unpack_from("<I", p, 2)[0]
                    if cid == idx_02:
                        ev_idx = i
                        break

        if ev_idx < 0:
            raise ValueError("Could not locate the right-hand split event for the crossfade.")
                        
        # Pro Tools marks both the fade and the right-hand audio event as
        # linked at byte +33. The fade itself is active (mute byte +0 = 0).
        right_104f = next(
            item for item in b1052.items[ev_idx].items
            if isinstance(item, PTBlock) and item.content_type == 0x104f
        )
        right_payload = bytearray(right_104f.items[0])
        right_payload[33] = 0x01
        right_104f.items[0] = right_payload

        p104f = bytearray(35)
        p104f[0] = 0x00
        # Fade events index 0x2630 geometries; old_count is the index of the
        # geometry appended above. It is not an audio clip ID.
        struct.pack_into("<I", p104f, 2, old_count)
        struct.pack_into("<q", p104f, 7, cut_abs_ts)
        p104f[15:35] = bytes.fromhex("01feff00000000ffffffffffffffff0000000000")
        p104f[33] = 0x01
        
        new_104f = PTBlock(10, 0x104f, 0)  # block_type=10, NOT 3
        new_104f.items = [bytes(p104f)]
        
        new_1050 = PTBlock(3, 0x1050, 0)
        new_1050.items = [new_104f, b"\x01\x01\x01"]
        
        # Insert exactly at ev_02's index, pushing ev_02 down
        b1052.items.insert(ev_idx, new_1050)
        
        hdr = bytearray(b1052.items[0])
        nlen = struct.unpack_from("<I", hdr, 0)[0]
        count_offset = 4 + nlen
        old_count = struct.unpack_from("<I", hdr, count_offset)[0]
        struct.pack_into("<I", hdr, count_offset, old_count + 1)
        b1052.items[0] = bytearray(hdr)
                
    def add_fade(self, track_name, clip_name, target_hh, target_mm, target_ss, target_ff, fade_type="in", duration_hh=0, duration_mm=0, duration_ss=0, duration_ff=0, fade_shape="power"):
        """Atomically add one standalone fade with a native geometry ID."""
        original_root_items = copy.deepcopy(self.root_items)
        original_removed_offsets = list(getattr(self, '_removed_offsets', []))
        try:
            return self._add_fade_impl(
                track_name, clip_name,
                target_hh, target_mm, target_ss, target_ff,
                fade_type,
                duration_hh, duration_mm, duration_ss, duration_ff,
                fade_shape,
            )
        except Exception:
            self.root_items = original_root_items
            self._removed_offsets = original_removed_offsets
            raise

    def _add_fade_impl(self, track_name, clip_name, target_hh, target_mm, target_ss, target_ff, fade_type="in", duration_hh=0, duration_mm=0, duration_ss=0, duration_ff=0, fade_shape="power"):

        if not isinstance(track_name, str) or not track_name:
            raise ValueError("track_name must be a non-empty string.")
        if not isinstance(clip_name, str) or not clip_name:
            raise ValueError("clip_name must be a non-empty string.")
        if not isinstance(fade_type, str):
            raise TypeError("fade_type must be a string.")
        if not isinstance(fade_shape, str):
            raise TypeError("fade_shape must be a string.")

        # 1. Convert duration to samples
        engine = TimecodeEngine(self.sample_rate, self.frame_rate_enum)
        fade_length_samples = engine.duration_to_samples(duration_hh, duration_mm, duration_ss, duration_ff)
        if fade_length_samples <= 0:
            fade_length_samples = self.sample_rate
        if fade_length_samples > 0xFFFF:
            raise ValueError("Standalone fade length exceeds the 16-bit geometry limit.")

        fade_type = fade_type.lower()
        if fade_type not in ["in", "out"]:
            raise ValueError("fade_type must be 'in' or 'out'")

        fade_shapes = {"power": 0x01, "linear": 0x02}
        fade_shape = fade_shape.lower()
        if fade_shape not in fade_shapes:
            raise ValueError("fade_shape must be 'power' or 'linear'")
        shape_byte = fade_shapes[fade_shape]
            
        target_samples = engine.timecode_to_samples(target_hh, target_mm, target_ss, target_ff)

        # 2. Resolve the parent clip ID and its duration.
        clip_id = self._resolve_unique_clip_id(clip_name)
        clip_roots = self._root_blocks(0x262a)
        clip_definitions = [
            item for item in clip_roots[0].items
            if isinstance(item, PTBlock) and item.content_type == 0x2629
        ]
        clip_payload_block = next(
            item for item in clip_definitions[clip_id].items
            if isinstance(item, PTBlock) and item.content_type == 0x2628
        )
        clip_info = self._decode_audio_clip_payload(clip_payload_block.items[0])
        clip_length = clip_info["length"]
        if clip_length <= 0:
            raise ValueError(f"Clip '{clip_name}' has no positive duration.")

        # 3. Resolve the requested track and the exact clip instance at target.
        matching_tracks = [
            playlist for playlist, name, _ in self._validated_main_playlists()
            if name == track_name
        ]
        if not matching_tracks:
            raise ValueError(f"Track '{track_name}' not found")
        if len(matching_tracks) != 1:
            raise ValueError(f"Track name '{track_name}' is ambiguous in the session.")
        b1052 = matching_tracks[0]

        matching_instances = []
        timeline_events = self._validated_main_timeline_events()
        for playlist, event, _, payload in timeline_events:
            if playlist is not b1052:
                continue
            if self._audio_timeline_event_kind(event, payload) != "audio":
                continue
            event_clip_id = struct.unpack_from("<I", payload, 2)[0]
            if event_clip_id != clip_id:
                continue
            event_start = struct.unpack_from("<Q", payload, 7)[0]
            event_end = event_start + clip_length
            target_in_instance = (
                event_start <= target_samples < event_end
                if fade_type == "in"
                else event_start < target_samples <= event_end
            )
            if target_in_instance:
                matching_instances.append((event_start, event_end))

        if not matching_instances:
            raise ValueError(
                f"No instance of clip '{clip_name}' on track '{track_name}' contains the requested fade target."
            )
        if len(matching_instances) > 1:
            raise ValueError("Fade target is ambiguous across multiple clip instances.")

        event_start, event_end = matching_instances[0]
        if fade_type == "in" and target_samples + fade_length_samples > event_end:
            raise ValueError("Fade In extends past the end of the clip instance.")
        if fade_type == "out" and target_samples - fade_length_samples < event_start:
            raise ValueError("Fade Out extends before the start of the clip instance.")

        b2630, existing_geometries = self._validated_fade_geometry_list()
        count_ba = bytearray(b2630.items[0])
        geometry_count = len(existing_geometries)
        _, _, existing_fade_bindings = self._validated_fade_geometry_bindings(
            timeline_events
        )
        expected_geometry_lengths = (22,) if fade_type == "in" else (26, 27)
        if any(
            entry[0] is b1052
            and struct.unpack_from("<Q", entry[3], 7)[0] == target_samples
            and len(geometry.items[0]) in expected_geometry_lengths
            for entry, _, geometry in existing_fade_bindings
        ):
            raise ValueError("A fade already exists for this clip at the target timecode.")

        # 4. Build geometry (0x262f)
        if fade_type == "in":
            # 22-byte format
            fade_geom_payload = bytearray(bytes.fromhex("00000000002000000000030100000000000000000000"))
            fade_geom_payload[8:10] = struct.pack("<H", fade_length_samples & 0xFFFF)
            fade_geom_payload[11] = shape_byte
            fade_ts = target_samples
            insert_after = False
        else:
            # Native Fade Out geometry is 27 bytes. The length is repeated at
            # offsets +8 and +10, followed by mode 0x02 and the shape byte.
            fade_geom_payload = bytearray(bytes.fromhex(
                "000000000022000000000000020200000000000000000000000000"
            ))
            length_bytes = struct.pack("<H", fade_length_samples & 0xFFFF)
            fade_geom_payload[8:10] = length_bytes
            fade_geom_payload[10:12] = length_bytes
            fade_geom_payload[13] = shape_byte
            fade_ts = target_samples
            insert_after = True

        new_262f = PTBlock(2, 0x262f)
        new_262f.original_offset = -1
        new_262f.items = [fade_geom_payload]

        # 5. Build fade event (0x1050)
        p104f = bytearray(35)
        p104f[0] = 0x00
        # Fade events index the global 0x2630 geometry list, not 0x262a.
        new_geometry_id = geometry_count
        struct.pack_into("<I", p104f, 2, new_geometry_id)
        struct.pack_into("<Q", p104f, 7, fade_ts)
        p104f[15:35] = bytes.fromhex("01feff00000000ffffffffffffffff0000000000")

        new_104f = PTBlock(10, 0x104f, 0)
        new_104f.original_offset = -1
        new_104f.items = [bytes(p104f)]

        new_1050 = PTBlock(3, 0x1050, 0)
        new_1050.original_offset = -1
        new_1050.items = [new_104f, b"\x01\x01\x01"]

        # Insert safely by chronological order of timestamp!
        # Instead of searching for the clip and inserting next to it, 
        # just find the correct insertion index to maintain chronological sort.
        insert_idx = 1 # Start after header (index 0)
        for i in range(1, len(b1052.items)):
            ev = b1052.items[i]
            if isinstance(ev, PTBlock) and ev.content_type == 0x1050:
                b104f = next((x for x in ev.items if isinstance(x, PTBlock) and x.content_type == 0x104f), None)
                if b104f and len(b104f.items) > 0 and isinstance(b104f.items[0], (bytes, bytearray)) and len(b104f.items[0]) > 14:
                    ev_ts = struct.unpack_from("<Q", b104f.items[0], 7)[0]
                    # If the timestamp of the existing event is strictly greater than ours, we should insert here.
                    # Wait, for Fade In (insert_after=False), we should be BEFORE an event at the exact same timestamp.
                    # For Fade Out (insert_after=True), we should be AFTER an event at the exact same timestamp.
                    if ev_ts > fade_ts:
                        insert_idx = i
                        break
                    elif ev_ts == fade_ts and not insert_after:
                        insert_idx = i
                        break
                    else:
                        insert_idx = i + 1
        
        b1052.items.insert(insert_idx, new_1050)

        hdr = bytearray(b1052.items[0])
        name_len = struct.unpack_from("<I", hdr, 0)[0]
        count_offset = 4 + name_len
        old_count = struct.unpack_from("<I", hdr, count_offset)[0]
        struct.pack_into("<I", hdr, count_offset, old_count + 1)
        b1052.items[0] = bytearray(hdr)

        # Geometry order is independent of chronological event order. Each
        # event carries its geometry index, so new geometries are appended.
        geometry_positions = [
            index for index, item in enumerate(b2630.items)
            if isinstance(item, PTBlock) and item.content_type == 0x262f
        ]
        if geometry_positions:
            geometry_insert_index = geometry_positions[-1] + 1
        else:
            geometry_insert_index = 1
        b2630.items.insert(geometry_insert_index, new_262f)
        struct.pack_into("<I", count_ba, 0, geometry_count + 1)
        b2630.items[0] = count_ba

        return new_geometry_id

    def set_clip_gain(self, clip_name, float_gain_db):
        """Set one uniquely identified clip's static gain without shared writes."""
        negative_infinity_value = struct.unpack(
            "<f", bytes.fromhex("f41a91c3")
        )[0]
        if isinstance(float_gain_db, bool):
            raise TypeError("Clip Gain must be a real number or -inf.")
        if isinstance(float_gain_db, str) and float_gain_db.lower() in (
            "-inf", "-infinity"
        ):
            val = negative_infinity_value
        else:
            try:
                val = float(float_gain_db)
            except (TypeError, ValueError, OverflowError) as exc:
                raise TypeError("Clip Gain must be a real number or -inf.") from exc
            if val == -math.inf:
                val = negative_infinity_value
            elif not math.isfinite(val):
                raise ValueError("Clip Gain must be finite or -inf.")
        try:
            float_bytes = struct.pack("<f", val)
        except OverflowError as exc:
            raise ValueError("Clip Gain exceeds the Float32 range.") from exc

        target_clip_id = self._resolve_unique_clip_id(clip_name)
        b262a = self._root_blocks(0x262a)[0]
        definitions = [
            child for child in b262a.items
            if isinstance(child, PTBlock) and child.content_type == 0x2629
        ]

        clip_gain_entries = []
        for clip_id, definition in enumerate(definitions):
            b2628 = next(
                child for child in definition.items
                if isinstance(child, PTBlock) and child.content_type == 0x2628
            )
            payload = b2628.items[0]
            if len(payload) < 6:
                raise ValueError(
                    f"Clip ID {clip_id} payload is too short for a Clip Gain index."
                )
            automation_index = struct.unpack_from("<i", payload, len(payload) - 6)[0]
            clip_gain_entries.append((b2628, payload, automation_index))

        roots_2637 = self._root_blocks(0x2637)
        if not roots_2637:
            raise ValueError("No Clip Gain dictionary (0x2637) found in session.")
        if len(roots_2637) != 1:
            raise ValueError("Ambiguous session: multiple root 0x2637 Clip Gain dictionaries.")
        b2637 = roots_2637[0]
        if (
            len(b2637.items) != 1
            or not isinstance(b2637.items[0], (bytes, bytearray))
            or len(b2637.items[0]) < 4
        ):
            raise ValueError("Invalid 0x2637 Clip Gain payload.")

        current_payload = bytearray(b2637.items[0])
        num_points = struct.unpack_from("<I", current_payload, 0)[0]
        expected_length = 4 + (num_points * 30)
        if len(current_payload) != expected_length:
            raise ValueError(
                f"Inconsistent 0x2637 point count: declared={num_points}, payload_length={len(current_payload)}."
            )

        for clip_id, (_, _, automation_index) in enumerate(clip_gain_entries):
            if automation_index != -1 and not 0 <= automation_index < num_points:
                raise ValueError(
                    f"Clip ID {clip_id} references invalid Clip Gain index "
                    f"{automation_index}."
                )

        target_b2628, target_payload, automation_index = clip_gain_entries[
            target_clip_id
        ]
        clip_payload = bytearray(target_payload)

        if automation_index == -1:
            point_meta = bytes.fromhex("0146010016000000000001000000040000000000000000000000")
            if num_points > 0x7FFFFFFF:
                raise OverflowError("Clip Gain point index exceeds signed Int32.")
            automation_index = num_points
            current_payload.extend(point_meta + float_bytes)
            struct.pack_into("<I", current_payload, 0, num_points + 1)
            struct.pack_into("<i", clip_payload, len(clip_payload) - 6, automation_index)
        elif sum(
            index == automation_index for _, _, index in clip_gain_entries
        ) > 1:
            # Different clip definitions must not change together merely because
            # they share one global point index. Clone the complete 30-byte point
            # and relink only the requested clip.
            if num_points > 0x7FFFFFFF:
                raise OverflowError("Clip Gain point index exceeds signed Int32.")
            point_offset = 4 + (automation_index * 30)
            cloned_point = bytearray(current_payload[point_offset:point_offset + 30])
            cloned_point[26:30] = float_bytes
            automation_index = num_points
            current_payload.extend(cloned_point)
            struct.pack_into("<I", current_payload, 0, num_points + 1)
            struct.pack_into("<i", clip_payload, len(clip_payload) - 6, automation_index)
        else:
            value_offset = 4 + (automation_index * 30) + 26
            current_payload[value_offset:value_offset + 4] = float_bytes

        b2637.items[0] = current_payload
        target_b2628.items[0] = clip_payload

        logger.info(
            "Applied Clip Gain of %s dB to clip %r; automation index %d.",
            val, clip_name, automation_index,
        )
        return automation_index

    def add_volume_node(self, track_name, hh, mm, ss, ff, db_value):
        if not isinstance(track_name, str):
            raise TypeError("track_name must be a string.")
        if not track_name:
            raise ValueError("track_name must be non-empty.")
        if "\x00" in track_name:
            raise ValueError("track_name cannot contain a NUL character.")

        engine = TimecodeEngine(self.sample_rate, self.frame_rate_enum)
        target_samples = engine.timecode_to_samples(hh, mm, ss, ff)
        if target_samples < 0 or target_samples > 0xFFFFFFFF:
            raise ValueError("Volume automation timestamp exceeds UInt32.")

        if isinstance(db_value, bool):
            raise TypeError("Volume automation value must be a real number.")
        try:
            db_value = float(db_value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise TypeError("Volume automation value must be a real number.") from exc
        if not math.isfinite(db_value):
            raise ValueError("Volume automation value must be finite.")
        scaled_db_value = db_value * 10
        if not math.isfinite(scaled_db_value):
            raise ValueError("Volume automation value exceeds signed Int16 deci-dB.")
        target_val = int(round(scaled_db_value))
        if target_val < -32768 or target_val > 32767:
            raise ValueError("Volume automation value exceeds signed Int16 deci-dB.")

        all_261c = []
        for r in self._content_root_items():
            all_261c.extend(r.get_all_blocks(0x261c) if isinstance(r, PTBlock) else [])

        internal_names = []
        for b261c in all_261c:
            b2619s = b261c.get_all_blocks(0x2619)
            if len(b2619s) != 1:
                raise ValueError("Each 0x261c track definition must contain one 0x2619 name.")
            b2619 = b2619s[0]
            if (
                not b2619.items
                or not isinstance(b2619.items[0], (bytes, bytearray))
                or len(b2619.items[0]) < 4
            ):
                raise ValueError("Invalid 0x2619 track-name payload.")
            payload = b2619.items[0]
            name_len = struct.unpack_from("<I", payload, 0)[0]
            if 4 + name_len > len(payload):
                raise ValueError("Invalid 0x2619 track-name length.")
            try:
                internal_name = payload[4:4 + name_len].decode("utf-8").rstrip("\x00")
            except UnicodeDecodeError as exc:
                raise ValueError("Invalid UTF-8 0x2619 track name.") from exc
            internal_names.append(internal_name)

        # The public name is the 0x1052 timeline name. It must take precedence
        # over stale 0x2619 names retained after track renames.
        visible_playlists = self._validated_main_playlists()
        visible_names = [name for _, name, _ in visible_playlists]
        matching_visible = [
            index for index, name in enumerate(visible_names)
            if name == track_name
        ]
        if len(matching_visible) > 1:
            raise ValueError(f"Visible track name '{track_name}' is ambiguous.")
        if matching_visible:
            if len(visible_names) != len(all_261c):
                raise ValueError(
                    "Cannot safely associate visible tracks with 0x261c definitions by ordinal."
                )
            target_261c = all_261c[matching_visible[0]]
        else:
            matching_internal = [
                index for index, name in enumerate(internal_names)
                if name == track_name
            ]
            if len(matching_internal) > 1:
                raise ValueError(
                    f"Track name '{track_name}' is ambiguous in 0x2619 definitions."
                )
            target_261c = (
                all_261c[matching_internal[0]] if matching_internal else None
            )

        if target_261c is None:
            raise ValueError(f"Track '{track_name}' or its volume automation block not found.")

        b260ds = target_261c.get_all_blocks(0x260d)
        if len(b260ds) != 1:
            raise ValueError("A track must contain one unambiguous 0x260d container.")
        direct_260a = [
            child for child in b260ds[0].items
            if isinstance(child, PTBlock) and child.content_type == 0x260a
        ]
        if not direct_260a:
            raise ValueError(f"Track '{track_name}' has no direct volume 0x260a playlist.")
        found_260a = direct_260a[0]

        # Parse existing 260a payload
        if len(found_260a.items) != 1 or not isinstance(found_260a.items[0], (bytes, bytearray)):
            raise ValueError("Invalid 260a payload format.")

        payload = found_260a.items[0]
        if len(payload) < 24:
            raise ValueError("260a payload too short.")

        magic = payload[0:4]
        if magic != b"\x01\x46\x01\x00":
            raise ValueError("Invalid 0x260a volume automation identifier.")
        declared_payload_size = struct.unpack_from("<I", payload, 4)[0]
        if declared_payload_size != len(payload) - 10:
            raise ValueError(
                "Inconsistent 0x260a payload size: "
                f"declared={declared_payload_size}, actual={len(payload) - 10}."
            )
        if payload[8:10] != b"\x00\x00" or payload[-2:] != b"\x00\x00":
            raise ValueError("Invalid 0x260a fixed padding or terminator.")
        n_nodes = struct.unpack_from("<I", payload, 10)[0]
        flags = bytearray(payload[14:22])
        expected_length = 24 + (n_nodes * 6)
        if len(payload) != expected_length:
            raise ValueError(
                f"Inconsistent 0x260a node count: declared={n_nodes}, "
                f"payload_length={len(payload)}."
            )
        declared_segments = struct.unpack_from("<I", flags, 2)[0]
        expected_segments = max(0, n_nodes - 1)
        if declared_segments != expected_segments:
            raise ValueError(
                f"Inconsistent 0x260a segment count: declared={declared_segments}, "
                f"expected={expected_segments}."
            )

        nodes = []
        for i in range(n_nodes):
            offset = 22 + (i * 6)
            ts = struct.unpack_from("<I", payload, offset)[0]
            val = struct.unpack_from("<h", payload, offset + 4)[0]
            if nodes and ts <= nodes[-1][0]:
                raise ValueError("0x260a volume nodes must have unique increasing timestamps.")
            nodes.append((ts, val))

        # Insert new node or update existing
        nodes = [n for n in nodes if n[0] != target_samples] # Remove if exact ts exists
        nodes.append((target_samples, target_val))
        nodes.sort(key=lambda x: x[0])

        # Update segment count in flags (bytes 2-5 of the 8-byte flag block)
        if len(nodes) > 1:
            struct.pack_into("<I", flags, 2, len(nodes) - 1)
        else:
            struct.pack_into("<I", flags, 2, 0)
            
        # Rebuild payload
        new_payload = bytearray()
        new_payload.extend(magic)
        new_payload.extend(struct.pack("<I", len(nodes) * 6 + 14))
        new_payload.extend(b"\x00\x00")
        new_payload.extend(struct.pack("<I", len(nodes)))
        new_payload.extend(flags)

        for ts, val in nodes:
            new_payload.extend(struct.pack("<I", ts))
            new_payload.extend(struct.pack("<h", val))

        # The automation payload always ends with exactly 2 null bytes, regardless of length
        new_payload.extend(b"\x00\x00")

        found_260a.items[0] = new_payload
        return len(nodes)

    def _wipe_offsets_recursive(self, node):
        if isinstance(node, PTBlock):
            node.original_offset = -1
            for child in node.items:
                self._wipe_offsets_recursive(child)

    def _refresh_original_offsets(self):
        """Replace every block's load-time offset with its current offset.

        The 0x0002 payload is patched in place during save().  Keeping the
        offsets from the initially loaded file after that patch makes a later
        save unable to match the already-updated pointers.  Refreshing the
        tree only after a successful write keeps subsequent edits and saves
        anchored to the file layout represented by the current 0x0002 table.
        """
        fmt_size = ">I" if self.is_bigendian else "<I"

        def _refresh_block(block, base_offset):
            block_bytes, _ = block.to_bytes(self.is_bigendian, base_offset)
            block.original_offset = base_offset

            block_size = struct.unpack_from(fmt_size, block_bytes, 3)[0]
            current_offset = base_offset + (7 if block_size == 0 else 9)

            for child in block.items:
                if isinstance(child, PTBlock):
                    child_size = _refresh_block(child, current_offset)
                    current_offset += child_size
                else:
                    current_offset += len(child)

            return len(block_bytes)

        current_offset = 0x14
        for item in self.root_items:
            if isinstance(item, PTBlock):
                current_offset += _refresh_block(item, current_offset)
            else:
                current_offset += len(item)

    def _decode_0002_records(self, payload):
        """
        Parse the structured records inside block 0x0002.
        Each record has an 8-byte prefix (00 00 00 01 04 00 01 00) followed by
        a 32-bit LE pointer to a block's absolute offset in the file and a
        3-byte zero suffix. The complete record is exactly 15 bytes.
        Returns a list of (prefix_start, pointer_offset_in_payload) tuples.
        """
        PREFIX = bytes.fromhex("0000000104000100")
        records = []
        pos = 0
        while pos < len(payload):
            idx = payload.find(PREFIX, pos)
            if idx == -1:
                break
            ptr_pos = idx + len(PREFIX)  # position of the 32-bit pointer within payload
            records.append((idx, ptr_pos))
            pos = idx + 1
        return records

    def _validate_0002_record_layout(self, payload):
        """Validate every standard record and its preceding big-endian count."""
        records = self._decode_0002_records(payload)
        record_size = 15
        starts = [start for start, _ in records]

        for start, pointer_position in records:
            record_end = start + record_size
            if (
                record_end > len(payload)
                or payload[pointer_position + 4:record_end] != b"\x00\x00\x00"
            ):
                raise ValueError("Invalid standard 0x0002 pointer record.")

        index = 0
        while index < len(starts):
            run_start = starts[index]
            run_length = 1
            while (
                index + run_length < len(starts)
                and starts[index + run_length]
                == run_start + run_length * record_size
            ):
                run_length += 1

            count_position = run_start - 2
            if count_position < 0:
                raise ValueError("Missing 0x0002 pointer-run counter.")
            declared_count = struct.unpack_from(">H", payload, count_position)[0]
            if declared_count != run_length:
                raise ValueError(
                    "Inconsistent 0x0002 pointer-run count: "
                    f"declared={declared_count}, actual={run_length}."
                )
            index += run_length

        return records

    @staticmethod
    def _raw_0002_payload(b0002):
        """Return the flat pointer-table payload without dropping valid bytes."""
        if not isinstance(b0002, PTBlock) or b0002.content_type != 0x0002:
            raise ValueError("Expected a root 0x0002 pointer table.")

        payload = bytearray()
        for item in b0002.items:
            if not isinstance(item, (bytes, bytearray)):
                raise ValueError("The root 0x0002 table must contain only raw bytes.")
            payload.extend(item)
        return payload

    def _purge_0002_records(self, b0002, removed_offsets, is_bigendian):
        """
        Strip 0x0002 records whose pointer targets a block that no longer
        exists in the tree (deleted via e.g. delete_clip_group()).

        _rebuild_0002() only ever *patches* pointer values for blocks still
        present in global_mapping; it never drops a record. If we pop() a
        block without removing its record here first, the record keeps
        pointing at whatever now lives at that stale offset -> "Magic ID
        does not match" in Pro Tools. Must run BEFORE _rebuild_0002().
        """
        if not removed_offsets:
            return 0
        fmt = ">I" if is_bigendian else "<I"
        payload = self._raw_0002_payload(b0002)

        records = self._decode_0002_records(payload)
        if not records:
            b0002.items = [payload]
            return 0

        removed_set = set(removed_offsets)

        # A pointer record is exactly 15 bytes. Bytes between records belong
        # to independent metadata and must not be removed with the preceding
        # record. Pro Tools' grouped/ungrouped files differ by exactly 15 bytes
        # for each removed pointer.
        record_size = 15
        spans_to_remove = []
        removals_by_run = {}
        purged = 0
        record_starts = [start for start, _ in records]
        record_start_set = set(record_starts)
        for start, ptr_pos in records:
            if ptr_pos + 4 > len(payload):
                continue
            ptr_val = struct.unpack_from(fmt, payload, ptr_pos)[0]
            if ptr_val not in removed_set:
                continue
            end = start + record_size
            if end > len(payload) or payload[ptr_pos + 4:end] != b"\x00\x00\x00":
                raise ValueError("Invalid 15-byte 0x0002 pointer record.")
            spans_to_remove.append((start, end))
            run_start = start
            while run_start - record_size in record_start_set:
                run_start -= record_size
            removals_by_run[run_start] = removals_by_run.get(run_start, 0) + 1
            purged += 1

        # Each consecutive run of records is preceded by its UInt16 BE count.
        # Removing records without updating this count makes Pro Tools consume
        # the following metadata as records and fail with "End of stream".
        for run_start, removed_count in removals_by_run.items():
            run_length = 1
            while run_start + run_length * record_size in record_start_set:
                run_length += 1
            count_pos = run_start - 2
            if count_pos < 0:
                raise ValueError("Missing 0x0002 pointer-run counter.")
            declared_count = struct.unpack_from(">H", payload, count_pos)[0]
            if declared_count != run_length or removed_count > declared_count:
                raise ValueError(
                    "Inconsistent 0x0002 pointer-run count: "
                    f"declared={declared_count}, actual={run_length}."
                )
            struct.pack_into(">H", payload, count_pos, declared_count - removed_count)

        for start, end in reversed(spans_to_remove):
            del payload[start:end]

        b0002.items = [payload]
        return purged

    def _rebuild_0002(self, b0002, global_mapping, is_bigendian):
        """
        Relocate every 32-bit block offset stored in the 0x0002 payload.

        Besides the standard 15-byte pointer records, 0x0002 metadata contains
        additional absolute block offsets. All occurrences whose old value is
        an actual PTBlock offset in global_mapping must move with that block.

        The parser deliberately keeps this payload flat. Reassemble every raw
        bytes/bytearray segment before patching, then normalize it to one
        bytearray so no valid immutable segment can be dropped.
        """
        fmt = ">I" if is_bigendian else "<I"
        payload = self._raw_0002_payload(b0002)

        original_payload = bytes(payload)
        records = self._decode_0002_records(original_payload)
        pointer_positions = {ptr_pos for _, ptr_pos in records}
        record_coverage = bytearray(len(original_payload))
        for start, _ in records:
            end = min(start + 15, len(record_coverage))
            record_coverage[start:end] = b"\x01" * (end - start)

        patches = []
        for pos in range(0, len(original_payload) - 3):
            # Metadata offsets may appear at any byte alignment, but a scan
            # must never interpret bytes crossing a standard record boundary
            # as a pointer. Inside a record, only its real +8 field is valid.
            overlaps_record = any(record_coverage[pos:pos + 4])
            if overlaps_record and pos not in pointer_positions:
                continue
            old_ptr = struct.unpack_from(fmt, original_payload, pos)[0]
            new_ptr = global_mapping.get(old_ptr)
            if new_ptr is not None and new_ptr != old_ptr:
                patches.append((pos, new_ptr))

        for previous, current in zip(patches, patches[1:]):
            if current[0] < previous[0] + 4:
                raise ValueError("Overlapping 0x0002 pointer relocations.")

        for pos, new_ptr in patches:
            struct.pack_into(fmt, payload, pos, new_ptr)

        patched_count = len(patches)

        # Replace all items with a single bytearray so to_bytes() does not
        # re-expand the phantom blocks a second time.
        b0002.items = [payload]

        return patched_count

    def _validate_save_structure(self):
        """Return the unique final pointer table after validating save invariants."""
        if not self.root_items or not isinstance(self.root_items[0], PTBlock):
            raise ValueError("Missing initial 0x0001 pointer block before save.")

        pointer_block = self.root_items[0]
        pointer_bytes, _ = pointer_block.to_bytes(self.is_bigendian, 0x14)
        fmt_type = ">H" if self.is_bigendian else "<H"
        fmt_size = ">I" if self.is_bigendian else "<I"
        if (
            len(pointer_bytes) != 11
            or pointer_bytes[0] != ZMARK
            or struct.unpack_from(fmt_type, pointer_bytes, 1)[0] != 0x0001
            or struct.unpack_from(fmt_size, pointer_bytes, 3)[0] != 4
        ):
            raise ValueError("Invalid initial 0x0001 pointer block before save.")

        pointer_tables = self._root_blocks(0x0002)
        if len(pointer_tables) != 1:
            raise ValueError("A unique root 0x0002 pointer table is required before save.")
        pointer_table = pointer_tables[0]
        if self.root_items[-1] is not pointer_table:
            raise ValueError("The 0x0002 pointer table must be the final root block before save.")
        if pointer_table.original_size == 0 and not pointer_table.items:
            raise ValueError("The root 0x0002 pointer table cannot serialize as an empty block.")
        if any(not isinstance(item, (bytes, bytearray)) for item in pointer_table.items):
            raise ValueError("Invalid nested block inside root 0x0002 table before save.")
        self._validate_0002_record_layout(
            self._raw_0002_payload(pointer_table)
        )

        # Keep the public timing attributes synchronized with the exact tree
        # being serialized and reject incomplete metadata before writing.
        self._parse_session_metadata()
        return pointer_table, len(self.root_items) - 1

    def _iter_serialized_root_items(self):
        """Yield each root item as bytes plus its unique-offset mapping."""
        current_offset = 0x14
        for item in self.root_items:
            if isinstance(item, (bytes, bytearray)):
                block_bytes = bytearray(item)
                mapping = {}
            elif isinstance(item, PTBlock):
                block_bytes, mapping = item.to_bytes(
                    self.is_bigendian, current_offset
                )
                block_bytes = bytearray(block_bytes)
            else:
                raise TypeError(
                    "Session root items must be bytes, bytearray, or PTBlock."
                )
            yield block_bytes, mapping
            current_offset += len(block_bytes)

    def save(self, out_path):
        """Atomically serialize the session and restore memory on failure."""
        out_path = _coerce_string_path(out_path, "out_path")

        original_root_items = copy.deepcopy(self.root_items)
        original_removed_offsets = list(getattr(self, '_removed_offsets', []))
        original_file_path = getattr(self, 'file_path', None)
        try:
            return self._save_impl(out_path)
        except Exception:
            self.root_items = original_root_items
            self._removed_offsets = original_removed_offsets
            if original_file_path is not None:
                self.file_path = original_file_path
            raise

    def _save_impl(self, out_path):

        # Pass 1: Compute new absolute offsets for every block.
        # to_bytes() builds a mapping of {old_original_offset -> new_computed_offset}
        # for every block that has original_offset > 0.
        global_mapping = {}
        for _, mapping in self._iter_serialized_root_items():
            _merge_unique_offset_mapping(global_mapping, mapping)

        b0002, block_0002_idx = self._validate_save_structure()

        # Pass 2: Rebuild the 0x0002 pointer table using the computed mapping.
        # This is the ONLY place we modify pointers — targeted and safe.
        removed = getattr(self, '_removed_offsets', [])
        if removed:
            purged = self._purge_0002_records(b0002, removed, self.is_bigendian)
            if purged:
                logger.info(
                    "Purged %d stale 0x0002 record(s) for deleted block(s).",
                    purged,
                )
        self._rebuild_0002(b0002, global_mapping, self.is_bigendian)

        # Pass 3: Fix the 0x0001 block's pointer to 0x0002.
        # 0x0001 is a special 11-byte block: unlike normal non-empty blocks it
        # has no content_type field. Its four bytes after the 7-byte header are
        # directly the absolute pointer to 0x0002.
        # Pass 4: Final serialization — build the output binary
        out_blocks = [
            block_bytes
            for block_bytes, _ in self._iter_serialized_root_items()
        ]

        # Patch 0x0001 -> 0x0002 pointer after we know the exact offset of 0x0002
        new_0002_offset = 0x14 + sum(len(b) for b in out_blocks[:block_0002_idx])
        fmt_ptr = ">I" if self.is_bigendian else "<I"
        fmt_type = ">H" if self.is_bigendian else "<H"
        first_block = out_blocks[0]
        if (
            len(first_block) < 11
            or first_block[0] != ZMARK
            or struct.unpack_from(fmt_type, first_block, 1)[0] != 0x0001
            or struct.unpack_from(fmt_ptr, first_block, 3)[0] != 4
        ):
            raise ValueError("Invalid or missing special 0x0001 pointer block.")
        pointer_bytes = struct.pack(fmt_ptr, new_0002_offset)
        first_block[7:11] = pointer_bytes

        # The parser represents the special pointer's first two bytes in the
        # content_type field and its remaining two bytes as raw payload. Keep
        # that in-memory representation synchronized with the patched output.
        pointer_block = self.root_items[0]
        pointer_block.content_type = struct.unpack_from(fmt_type, pointer_bytes, 0)[0]
        pointer_block.items = [bytearray(pointer_bytes[2:])]

        # Assemble and write
        out_data = bytearray()
        out_data.extend(self.data[:0x14])
        for block_bytes in out_blocks:
            out_data.extend(block_bytes)

        import tempfile

        destination = os.path.abspath(out_path)
        destination_dir = os.path.dirname(destination)
        if not os.path.isdir(destination_dir):
            raise FileNotFoundError(
                f"Output directory does not exist: {destination_dir}"
            )

        temp_path = None
        logger.info("Re-encrypting and saving to %s...", destination)
        try:
            descriptor, temp_path = tempfile.mkstemp(
                prefix=f".{os.path.basename(destination)}.",
                suffix=".tmp",
                dir=destination_dir,
            )
            os.close(descriptor)
            xor_session(out_data, temp_path)

            # Complete all in-memory work before the atomic replacement. If
            # refreshing fails, the destination still contains its old bytes.
            self._refresh_original_offsets()
            self._removed_offsets = []
            os.replace(temp_path, destination)
            temp_path = None
            self.data = out_data
            self.file_path = destination
        finally:
            if temp_path is not None:
                try:
                    os.remove(temp_path)
                except FileNotFoundError:
                    pass

    def create_subclip(self, orig_clip_id, new_name, src_offset, length):
        """Create one validated virtual clip definition."""
        import uuid

        if isinstance(orig_clip_id, bool) or not isinstance(orig_clip_id, int):
            raise TypeError("orig_clip_id must be an integer.")
        if not isinstance(new_name, str) or not new_name:
            raise ValueError("new_name must be a non-empty string.")
        if "\x00" in new_name:
            raise ValueError("new_name cannot contain a NUL character.")
        if any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in (src_offset, length)
        ):
            raise TypeError("Sub-clip source offset and length must be integers.")
        if src_offset < 0 or length <= 0:
            raise ValueError("Sub-clip source offset must be >= 0 and length must be > 0.")
        if src_offset > 0xFFFFFFFF or length > 0xFFFFFFFF:
            raise ValueError("Sub-clip source offset and length must fit UInt32.")
        if src_offset == 0 and length > 0x00FFFFFF:
            raise ValueError(
                "The zero-offset long sub-clip layout has not been verified."
            )
        if src_offset > 0x00FFFFFF and length > 0x00FFFFFF:
            raise ValueError(
                "The combined UInt32 source-offset/length layout has not been verified."
            )

        clip_roots = self._root_blocks(0x262a)
        if len(clip_roots) != 1:
            raise ValueError("A unique root 0x262a clip list is required.")
        b262a = clip_roots[0]
        if not b262a.items or not isinstance(b262a.items[0], (bytes, bytearray)) or len(b262a.items[0]) < 4:
            raise ValueError("Invalid 0x262a clip counter payload.")

        clips = [
            item for item in b262a.items
            if isinstance(item, PTBlock) and item.content_type == 0x2629
        ]

        count_payload = bytearray(b262a.items[0])
        declared_count = struct.unpack_from("<I", count_payload, 0)[0]
        if declared_count != len(clips):
            raise ValueError(
                f"Inconsistent 0x262a clip count: declared={declared_count}, actual={len(clips)}."
            )

        if orig_clip_id < 0 or orig_clip_id >= len(clips):
            raise ValueError(f"Unknown original clip ID {orig_clip_id}.")

        existing_names = {
            self._audio_clip_info_by_id(clip_id)["name"]
            for clip_id in range(len(clips))
        }
        if new_name in existing_names:
            raise ValueError(f"Clip name '{new_name}' already exists.")

        orig_2629 = clips[orig_clip_id]
        payload_blocks = [
            item for item in orig_2629.items
            if isinstance(item, PTBlock) and item.content_type == 0x2628
        ]
        identity_positions = [
            index for index, item in enumerate(orig_2629.items)
            if isinstance(item, (bytes, bytearray)) and len(item) == 48
        ]
        if len(payload_blocks) != 1 or len(identity_positions) != 1:
            raise ValueError("Invalid 0x2629 sub-clip template structure.")
        new_clip_id = len(clips)

        new_clip = copy.deepcopy(orig_2629)
        self._wipe_offsets_recursive(new_clip)
        identity_index = identity_positions[0]
        identity_payload = bytearray(new_clip.items[identity_index])
        struct.pack_into("<I", identity_payload, 0, new_clip_id)
        identity_payload[22:38] = uuid.uuid4().bytes
        new_clip.items[identity_index] = bytes(identity_payload)

        b2628 = next(x for x in new_clip.items if isinstance(x, PTBlock) and x.content_type == 0x2628)
        try:
            new_name_enc = new_name.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError("new_name must be valid UTF-8.") from exc
        pl = bytearray()
        pl.extend(struct.pack("<I", len(new_name_enc)))
        pl.extend(new_name_enc)

        if src_offset > 0:
            source_width = 3 if src_offset <= 0x00FFFFFF else 4
            length_width = 3 if length <= 0x00FFFFFF else 4
            source_flag = b"\x30" if source_width == 3 else b"\x40"
            length_selector = b"\x30" if length_width == 3 else b"\x40"
            pl.extend(b"\x01" + source_flag + length_selector + b"\x44\x08")
            pl.extend(src_offset.to_bytes(source_width, "little"))
            pl.extend(length.to_bytes(length_width, "little"))
            pl.extend(b"\x00" * 8)
            pl.extend(b"\xff\xff\xff\xff\xff\xff\xff\xff")
            rest = bytearray()
            rest.extend(b"\xfe\xff\x00\x00\x00\x00\xff\xff\x04\x00\x04\x00")
            rest.extend(b"\x00" * 21)
            rest.extend(b"\xff\xff\xff\xff\x00\x00")
            pl.extend(rest)
        else:
            pl.extend(b"\x01\x00\x30\x44\x08")
            pl.extend(struct.pack("<I", length))
            pl.extend(b"\x00" * 8)
            pl.extend(b"\xff\xff\xff\xff\xff\xff\xff\xfe")
            rest = bytearray()
            rest.extend(b"\xff\x00\x00\x00\x00\xff\xff\x04\x00\x04\x00")
            rest.extend(b"\x00" * 21)
            rest.extend(b"\xff\xff\xff\xff\x00\x00")
            pl.extend(rest)

        b2628.items[0] = bytes(pl)

        last_clip_idx = len(b262a.items) - 1
        for idx in range(len(b262a.items) - 1, -1, -1):
            if isinstance(b262a.items[idx], PTBlock) and b262a.items[idx].content_type == 0x2629:
                last_clip_idx = idx
                break

        b262a.items.insert(last_clip_idx + 1, new_clip)
        struct.pack_into("<I", count_payload, 0, declared_count + 1)
        b262a.items[0] = count_payload
        return new_clip_id

    def trim_clip_start(self, clip_name, amount_samples):
        """Coupe le début d'un clip de 'amount_samples' échantillons."""
        return self._trim_clip(clip_name, amount_samples, trim_start=True)

    def trim_clip_end(self, clip_name, amount_samples):
        """Coupe la fin d'un clip de 'amount_samples' échantillons."""
        return self._trim_clip(clip_name, amount_samples, trim_start=False)

    def _trim_clip(self, clip_name, amount_samples, *, trim_start):
        if isinstance(amount_samples, bool) or not isinstance(amount_samples, int):
            raise TypeError("amount_samples must be an integer.")
        if amount_samples <= 0:
            raise ValueError("amount_samples must be greater than zero.")

        original_root_items = copy.deepcopy(self.root_items)
        original_removed_offsets = list(getattr(self, '_removed_offsets', []))
        try:
            clip_id = self._resolve_unique_clip_id(clip_name)
            clip_info = self._audio_clip_info_by_id(clip_id)
            if amount_samples >= clip_info["length"]:
                raise ValueError("Trim amount must be smaller than the clip length.")

            timeline_events = self._validated_main_timeline_events()
            placements = []
            for entry in timeline_events:
                _, event, _, payload = entry
                if self._audio_timeline_event_kind(event, payload) != "audio":
                    continue
                if struct.unpack_from("<I", payload, 2)[0] == clip_id:
                    placements.append(entry)

            if not placements:
                raise ValueError(f"Clip '{clip_name}' has no visible audio placement.")
            if len(placements) != 1:
                raise ValueError(
                    f"Clip '{clip_name}' has multiple visible placements; "
                    "the trim target is ambiguous."
                )

            playlist, _, b104f, source_payload = placements[0]
            if len(source_payload) != 35:
                raise ValueError("Audio event 0x104f payload must be exactly 35 bytes.")
            start_samples = struct.unpack_from("<Q", source_payload, 7)[0]
            related_fades = self._fade_bindings_for_audio_placement(
                start_samples, clip_info["length"], playlist, timeline_events
            )
            if related_fades or source_payload[33] != 0:
                raise NotImplementedError(
                    "Trimming a clip placement with attached fades is not supported safely yet."
                )

            new_length = clip_info["length"] - amount_samples
            new_source_offset = clip_info["src_offset"] + (
                amount_samples if trim_start else 0
            )

            clip_root = self._root_blocks(0x262a)[0]
            definitions = [
                item for item in clip_root.items
                if isinstance(item, PTBlock) and item.content_type == 0x2629
            ]
            existing_names = {
                self._audio_clip_info_by_id(index)["name"]
                for index in range(len(definitions))
            }
            suffix = "tS" if trim_start else "tE"
            base_name = f"{clip_name}-{suffix}"
            new_name = base_name
            sequence = 2
            while new_name in existing_names:
                new_name = f"{base_name}-{sequence:02d}"
                sequence += 1

            new_clip_id = self.create_subclip(
                clip_id, new_name, new_source_offset, new_length
            )
            updated_payload = bytearray(source_payload)
            struct.pack_into("<I", updated_payload, 2, new_clip_id)
            if trim_start:
                new_start_samples = start_samples + amount_samples
                if new_start_samples > 0xFFFFFFFF:
                    raise ValueError(
                        "Trim start timestamp exceeds the UInt32 0x2628 range."
                    )
                new_definitions = [
                    item for item in clip_root.items
                    if isinstance(item, PTBlock) and item.content_type == 0x2629
                ]
                new_definition = new_definitions[new_clip_id]
                new_b2628 = next(
                    item for item in new_definition.items
                    if isinstance(item, PTBlock) and item.content_type == 0x2628
                )
                new_clip_payload = bytearray(new_b2628.items[0])
                new_name_length = struct.unpack_from("<I", new_clip_payload, 0)[0]
                attribute_offset = 4 + new_name_length
                flags = struct.unpack_from("<H", new_clip_payload, attribute_offset)[0]
                source_width = 4 if flags == 0x4001 else 3
                length_width = (
                    4 if new_clip_payload[attribute_offset + 2] == 0x40 else 3
                )
                timestamp_offset = (
                    attribute_offset + 5 + source_width + length_width
                )
                struct.pack_into(
                    "<II", new_clip_payload, timestamp_offset,
                    new_start_samples, new_start_samples,
                )
                new_b2628.items[0] = bytes(new_clip_payload)
                struct.pack_into(
                    "<Q", updated_payload, 7, new_start_samples
                )
            b104f.items[0] = bytes(updated_payload)
            direction = "start" if trim_start else "end"
            logger.info(
                "Trim %s applied to %r; new clip: %r.",
                direction, clip_name, new_name,
            )
            return new_clip_id
        except Exception:
            self.root_items = original_root_items
            self._removed_offsets = original_removed_offsets
            raise


def _validate_generated_audio_session(session, prepared, template_tracks):
    """Verify the saved PTX semantically before publishing its directory."""
    if session.get_tracks() != template_tracks:
        raise ValueError("Generated audio session has an unexpected track layout.")
    expected_names = [item["physical_filename"] for item in prepared]
    if session._validated_physical_audio_catalog()[3] != expected_names:
        raise ValueError("Generated physical-media catalog is inconsistent.")

    clips = session.get_clips()
    if [clip["name"] for clip in clips] != [
        item["clip_name"] for item in prepared
    ]:
        raise ValueError("Generated audio Clip List is inconsistent.")
    clip_roots = session._root_blocks(0x262A)
    if len(clip_roots) != 1:
        raise ValueError("Generated audio clip-definition root is inconsistent.")
    definitions = [
        item for item in clip_roots[0].items
        if isinstance(item, PTBlock) and item.content_type == 0x2629
    ]
    if len(definitions) != len(prepared):
        raise ValueError("Generated audio clip-definition count is inconsistent.")
    for expected_index, definition in enumerate(definitions):
        identity_span, media_span = session._validated_2629_fixed_records(
            definition
        )
        identity = identity_span[2]
        media_link = media_span[2]
        if (
            struct.unpack_from("<I", identity, 0)[0] != expected_index
            or struct.unpack_from("<I", media_link, 96)[0] != expected_index
        ):
            raise ValueError(
                "Generated audio clip identity/media index is inconsistent."
            )

    timeline = session.get_timeline_clips()
    if len(timeline) != len(prepared) or any(item["is_fade"] for item in timeline):
        raise ValueError("Generated audio timeline event count is inconsistent.")
    expected = {
        item["clip_name"]: (
            item["track"],
            item["start_samples"],
            item["length_samples"],
            item["physical_filename"],
        )
        for item in prepared
    }
    actual = {
        item["clip_name"]: (
            item["track"],
            item["start_samples"],
            item["length_samples"],
            item["physical_filename"],
        )
        for item in timeline
    }
    if actual != expected:
        raise ValueError("Generated audio timeline placements are inconsistent.")


def build_audio_session(
    template_ptx_path,
    clip_specs,
    output_session_directory,
    session_name=None,
):
    """Build one self-contained PTX/Audio Files directory atomically.

    The source WAV bytes are copied unchanged. The PTX is created from one
    verified template containing exactly one imported prototype in the Clip
    List and empty timeline playlists. Each descriptor supplies audio_path and
    track_name; its input order defines spotting/overlap priority.
    """
    import shutil
    import tempfile

    template_ptx_path = os.path.abspath(
        _coerce_string_path(template_ptx_path, "template_ptx_path")
    )
    if not os.path.isfile(template_ptx_path):
        raise FileNotFoundError(
            f"Audio-session template does not exist: {template_ptx_path}"
        )
    if not template_ptx_path.lower().endswith(".ptx"):
        raise ValueError("Audio-session template path must use the .ptx extension.")

    output_session_directory = os.path.abspath(
        _coerce_string_path(
            output_session_directory, "output_session_directory"
        )
    )
    output_parent = os.path.dirname(output_session_directory)
    if not os.path.isdir(output_parent):
        raise FileNotFoundError(
            f"Audio-session output parent directory does not exist: {output_parent}"
        )
    if os.path.lexists(output_session_directory):
        raise FileExistsError(
            f"Audio-session output directory already exists: "
            f"{output_session_directory}"
        )

    if session_name is None:
        session_filename = os.path.basename(output_session_directory) + ".ptx"
    else:
        if not isinstance(session_name, str):
            raise TypeError("session_name must be a string or None.")
        if not session_name or "\x00" in session_name:
            raise ValueError("session_name must be non-empty and contain no NUL.")
        if os.path.basename(session_name) != session_name or any(
            separator and separator in session_name
            for separator in (os.sep, os.altsep, "/", "\\")
        ):
            raise ValueError("session_name must be a filename, not a path.")
        session_filename = session_name
        if not session_filename.lower().endswith(".ptx"):
            session_filename += ".ptx"
    if session_filename.casefold() == ".ptx":
        raise ValueError("session_name must include a basename before .ptx.")

    session = ProToolsSession(template_ptx_path)
    template = session._validated_audio_import_template()
    profile = template["profile"]
    maximum_length_samples = min(
        (1 << (8 * profile["clip_length_width"])) - 1,
        (1 << (8 * profile["media_length_width"])) - 1,
    )
    prepared, target_tracks = _prepare_template_audio_clips(
        clip_specs, maximum_length_samples
    )
    template_tracks = list(template["playlists"])
    clip_manifest = session._populate_audio_import_template(prepared, template)

    temporary_directory = tempfile.mkdtemp(
        prefix=f".{os.path.basename(output_session_directory)}.",
        suffix=".tmp",
        dir=output_parent,
    )
    try:
        temporary_audio_directory = os.path.join(
            temporary_directory, "Audio Files"
        )
        os.mkdir(temporary_audio_directory)
        for item in prepared:
            destination = os.path.join(
                temporary_audio_directory, item["physical_filename"]
            )
            shutil.copyfile(item["source_path"], destination)

        temporary_session_path = os.path.join(
            temporary_directory, session_filename
        )
        session.save(temporary_session_path)
        verification = ProToolsSession(temporary_session_path)
        _validate_generated_audio_session(
            verification, prepared, template_tracks
        )

        if os.path.lexists(output_session_directory):
            raise FileExistsError(
                f"Audio-session output directory appeared during generation: "
                f"{output_session_directory}"
            )
        os.replace(temporary_directory, output_session_directory)
        temporary_directory = None
    finally:
        if temporary_directory is not None:
            shutil.rmtree(temporary_directory)

    final_audio_directory = os.path.join(
        output_session_directory, "Audio Files"
    )
    for item in clip_manifest:
        item["output_audio_path"] = os.path.join(
            final_audio_directory, item["physical_filename"]
        )
    return {
        "session_path": os.path.join(output_session_directory, session_filename),
        "audio_files_directory": final_audio_directory,
        "track_count": len(target_tracks),
        "tracks": target_tracks,
        "clips": clip_manifest,
    }


if __name__ == "__main__":
    # Test loading and saving a session without modification (bit-perfect test)
    import sys
    if len(sys.argv) > 2:
        sess = ProToolsSession(sys.argv[1])
        sess.save(sys.argv[2])
    else:
        print("Usage: python pt_api.py <input.ptx> <output.ptx>")
