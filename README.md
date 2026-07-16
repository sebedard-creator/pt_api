# pt_api

*(Reverse-engineered and tested against **Pro Tools Ultimate 2024.3.1** on sessions at **23.98, 24, and 29.97df fps**)*

A standalone, dependency-free Python API for parsing, manipulating, and re-encrypting Pro Tools (`.ptx`) session files in place. 

> [!NOTE]
> **Language Notice:** While this README is provided in English, please note that the underlying codebase, comments, and deep technical documentation (`architecture.md`, `pt_format_specs.md`, etc.) are written entirely in French.

## Overview
pt_api bypasses the need for the Pro Tools application or SDKs to modify session files. It natively decrypts the `.ptx` proprietary XOR stream, parses the binary block tree, applies targeted edits to the timeline, fades, and automation, and correctly reconstructs the pointer tables (`0x0002` block) before re-encrypting the file. 

For supported sessions, a no-op load/save preserves the encrypted file byte-for-byte. The unencrypted 20-byte header is validated before decryption or block parsing; structural validation then rejects malformed pointer envelopes—including inconsistent `0x0002` record-run counters—and missing or ambiguous timing metadata before any edit is exposed, and revalidates the required root structures before every save.

The library API is silent on standard output. Optional operational diagnostics are emitted through Python's `logging` module at `INFO` level under the `pt_api` logger.

Input and output paths may be strings or `os.PathLike` objects such as `pathlib.Path`. Path-like values must resolve to text paths; raw `bytes` paths are rejected consistently. A loaded session stores its source path as an absolute path so later `Audio Files` resolution does not depend on the process working directory.

## API Capabilities

The supported public surface is divided between the high-level `ProToolsSession` editing API and a small low-level binary/timecode API.

### High-level session API

| Capability | Method | Description |
|---|---|---|
| **I/O & Crypto** | `ProToolsSession(file)` | Validates, decrypts and parses an existing PTX session. |
| | `save(out_path)` | Rebuilds pointer tables and performs a transactional, atomic encrypted save. |
| **Inspection** | `get_tracks()` | Returns the validated visible track names. |
| | `get_markers()` | Returns validated markers with their index, name and timecode. |
| | `get_clips()` | Describes all supported audio clips and Clip Groups in the Clip Bin. |
| | `get_timeline_clips(include_fades=True)` | Returns chronological audio/fade events with track, clip, sample range, source offset, mute/fade state and best-effort physical filename. Passing `False` returns audio only and deliberately skips fade-geometry validation. |
| **Markers** | `add_marker(name, tc_samples, index=None)` | Adds a Memory Location / Marker at an absolute sample position after validating the complete visible track map. |
| **Clip Operations** | `rename_clip(old_name, new_name)` | Renames one uniquely identified audio clip and rejects destination-name collisions. |
| | `mute_clip(clip_name, mute=True)` | Mutes or unmutes every visible audio placement of the uniquely named clip. |
| | `move_clip(clip_name, hh, mm, ss, ff)` | Moves one unambiguous visible placement to an absolute timecode. |
| | `duplicate_clip(clip_name, hh, mm, ss, ff, mute=False)` | Clones one unambiguous visible placement at a new absolute timecode. |
| | `split_clip(track_name, clip_name, cut_hh, cut_mm, cut_ss, cut_ff)` | Splits one placement into two uniquely named virtual sub-clips. |
| | `trim_clip_start(clip_name, amount_samples)` | Non-destructively trims the start of one unambiguous placement. |
| | `trim_clip_end(clip_name, amount_samples)` | Non-destructively trims the end of one unambiguous placement. |
| | `create_subclip(orig_clip_id, new_name, src_offset, length)` | Advanced API that creates a virtual clip definition from an existing clip template using the verified 24/32-bit source-offset and length combinations. The trim methods are preferred for timeline edits. |
| | `relink_clip(track_name, clip_name, placement_start_samples, new_clip_name, source_audio_path, new_audio_path, replacement_audio_path=None)` | Clones one verified Pro Tools WAV with a new BWF/UMID identity, creates matching PTX media and clip definitions, and retargets exactly one placement. An optional compatible rendered WAV can replace the clone's PCM `data` chunk. |
| **Fades & Crossfades** | `add_fade(track_name, clip_name, target_hh, target_mm, target_ss, target_ff, fade_type="in", duration_hh=0, duration_mm=0, duration_ss=0, duration_ff=0, fade_shape="power")` | Adds a native Fade In/Out with a Power or Linear curve. A zero duration requests the one-second default. |
| | `add_crossfade(track_name, clip_name, cut_hh, cut_mm, cut_ss, cut_ff, fade_hh, fade_mm, fade_ss, fade_ff)` | Atomically splits one clip and adds a centered Equal Power crossfade. |
| **Automation** | `set_clip_gain(clip_name, float_gain_db)` | Applies static Clip Gain to one uniquely identified clip. Shared point indexes are cloned; Float32 values and `-inf` are supported. |
| | `add_volume_node(track_name, hh, mm, ss, ff, db_value)` | Adds or replaces a volume node on the resolved track after validating the complete `0x260a` envelope. |
| **Clip Groups** | `delete_clip_group(group_name)` | Transactionally dissolves one supported simple Clip Group, restores its component clips and repairs affected pointers. |

### Low-level public API

| Object or function | Methods / behavior |
|---|---|
| `TimecodeEngine(sample_rate, frame_rate_enum)` | `get_frame_rate()`, `samples_to_timecode(samples)`, `timecode_to_samples(hh, mm, ss, ff)` and `duration_to_samples(hh, mm, ss, ff)` provide validated conversions for the supported frame-rate enums. |
| `PTBlock(block_type, content_type, original_size=0)` | `to_bytes(is_bigendian=False, base_offset=0)` serializes a validated block tree and returns its offset mapping; `get_all_blocks(content_type=None)` recursively queries it. |
| `unxor_session(file_path)` | Reads, validates and decrypts a PTX file into a new `bytearray`. |
| `xor_session(data, out_path)` | Validates and encrypts PTX bytes into an atomically replaced output file without mutating the caller's buffer. |

`gen_xor_delta()`, `u_endian_read2()` and `u_endian_read4()` remain module-level implementation helpers. They are not part of the stable public compatibility contract; applications should use the APIs above.

## Current Limitations

### Session and format support

- **Existing sessions only:** `ProToolsSession` edits structurally valid existing PTX files; it does not create a complete session from scratch.
- **Validated application/version scope:** Reference files were produced by Pro Tools Ultimate 2024.3.1. Unknown PTX revisions may contain layouts that this reverse-engineered implementation deliberately rejects.
- **Little-endian payloads only:** A session declaring big-endian mode is rejected before block parsing. Only generic block headers understand both byte orders; editing payloads have been validated only as little-endian.
- **Frame rates:** Timecode conversion supports only enum `0x01` (24 fps), `0x09` (23.976 fps) and `0x05` (29.97 Drop Frame). An intact unknown enum can be preserved by a no-op load/save, but operations requiring time conversion reject it.
- **Required structure:** Loading requires valid `0x0001`, final `0x0002`, `0x1028` and `0x204d` roots. Individual features additionally require their relevant track, clip, marker, geometry or automation containers to be unique and internally consistent. Malformed or ambiguous structures are rejected rather than repaired heuristically.
- **Recognized clip layouts:** Audio definition flags `0x0000`, `0x0001`, `0x2000`, `0x2001`, `0x3000`, `0x3001` and `0x4001`, plus the verified Clip Group flag `0x5000`, are decoded. The flag's low bit classifies a parent (`0`) or virtual (`1`) audio definition; its high nibble selects a zero-, 16-, 24- or 32-bit source offset. The following selector byte `0x10`, `0x20`, `0x30` or `0x40` independently selects an 8-, 16-, 24- or 32-bit length. Thus `01 30 40` is flag `0x3001` plus a UInt32-length selector, whereas `01 40 30` is the genuine flag `0x4001` plus a UInt24-length selector. Unobserved flags, including `0x4000`, and other width selectors are rejected.
- **Physical audio filenames:** `get_timeline_clips()` uses the exact indexed `0x1004`/`0x103a` catalog when the verified layout is present. Otherwise it falls back to matching `.wav`, `.aif` and `.aiff` candidates by name in the session's `Audio Files` folder and then in `0x1004`; `physical_filename` is `None` when no unique fallback match exists.

### Targeting and general editing

- **Exact names:** Clip and track operations use exact names. Duplicate matching names are treated as ambiguous and rejected.
- **Audio focus:** Timeline editing supports audio clips and fades. MIDI regions/controllers, video tracks/clips, Inserts, Sends, I/O routing, Pan automation, Mute automation and plugin automation are not supported. `mute_clip()` changes the static event mute flag; it does not write Mute automation.
- **No general session authoring:** The API does not create, delete, rename or reorder tracks; perform general import/export; or arbitrarily delete Clip Bin definitions and timeline events. Relinking is limited to the exact WAV-clone operation documented below. Only the editing operations listed in the capability table are implemented.
- **Clip Group macros are not audio placements:** Group macros use a separate ID namespace and are ignored by ordinary audio read/mute/move/split/duplicate/trim/fade operations even when their numeric ID collides with a clip ID.
- **Attached fades:** `move_clip()`, `duplicate_clip()`, `split_clip()`, `trim_clip_start()` and `trim_clip_end()` reject a placement with attached fades. Duplication does not clone fade geometry.
- **Ambiguous placements:** Move, duplicate and trim require exactly one visible placement of the named clip. Split also requires one placement on the named track spanning the cut. `mute_clip()` is the exception: it intentionally updates all visible audio placements of the uniquely named clip.

### Splitting, trimming and virtual clips

- **Supported split source:** `split_clip()` accepts the verified short and long root layouts documented in `pt_format_specs.md`; an already split or otherwise unsupported source layout is rejected.
- **Long clips and the remaining split limit:** Parent and left-fragment lengths are decoded according to their selector, up to UInt32. A right fragment can carry a 24-bit source offset and either a 24-bit (`01 30 30`) or UInt32 (`01 30 40`) length. `split_clip()` therefore supports a six-minute 48 kHz clip when the cut is near its beginning, but the cut's source-relative position must still be at most 16,777,215 samples (about `5:49.525` at 48 kHz). A later split would also require the still-unobserved long left-fragment layout, so the writer does not extrapolate from the separately verified `0x4001` trim layout.
- **Long trims and direct sub-clips:** `create_subclip()` and the trim methods can write a UInt24 source offset with a UInt32 length (`01 30 40`) or a UInt32 source offset with a UInt24 length (`01 40 30`). The latter was validated by Start Trimming a six-minute 48 kHz clip to `10:05:50:00`. Two combinations remain deliberately unsupported because no Pro Tools reference exists: a zero-source virtual clip longer than `0xFFFFFF` samples, and a clip whose source offset and length both exceed `0xFFFFFF`. Consequently, an End Trim that would leave a zero-offset clip longer than about `5:49.525` at 48 kHz is rejected.
- **Split timestamps:** The cut timestamp stored in the generated `0x2628` layout must fit UInt32.
- **Sub-clip templates:** `create_subclip()` requires one valid source definition with the verified 48-byte identity record, a unique UTF-8 destination name, a non-negative source offset and a positive length; each numeric value must fit UInt32 and one of the verified combinations above. It creates a Clip Bin definition only, not a timeline placement.
- **Composed trims:** Start/end trims can be composed by targeting the generated virtual clip name from the previous call; they still require one visible placement and no attached fade.

### Physical WAV relinking

- **Exact placement only:** `relink_clip()` targets one visible audio placement by track name, unique clip-definition name and absolute start sample. It clones the definition and changes only that event's clip ID; other placements remain linked to the original media.
- **Verified root and production-virtual clips:** The source definition may use the observed parent/root layout `00 00 30 44 00` or the production virtual layouts using flags `0x0001`, `0x2001`, `0x3001` or `0x4001` and their verified length selectors. Root relinks assign the targeted placement start as the new media time reference. Virtual relinks preserve the source media time reference and the complete virtual geometry; their embedded reference must equal the BWF time reference plus the source offset.
- **Verified catalog layout:** Relinking requires one internally consistent `0x1004` catalog, one ordered UTF-8 `Audio Files` filename list, a structurally valid hierarchical trailer whose counters follow the documented `N+1..N+K` relationships, a reassemblable 104-byte clip media-link record and a physical-media index that fits UInt32. Incidental byte sequences that parse as empty child blocks inside the fixed 48/104-byte records are reassembled before validation. Internal labels such as `SHARE TO NETWORK`, `VIDEO` and `Exports` are preserved metadata, not filesystem paths; unsupported trailer structures are rejected.
- **Verified Pro Tools WAV only:** The source must contain unique `bext`, `minf`, `regn` and `umid` chunks in one of the observed Pro Tools layouts. Its basename must match the indexed PTX catalog entry. A short `regn` stem is replaced when it matches the physical stem; a production abbreviation such as `Rdy` versus `Ready` is deliberately preserved. The BWF and duplicated `regn` time references must agree with the PTX definition. The caller supplies the source and new `.wav` paths explicitly; for a self-contained Pro Tools session they should be built from the session's sibling `Audio Files` folder, never from internal catalog labels. The destination folder must exist. The new filename stem must differ from the source while retaining the same UTF-8 byte length because the verified physical records are patched in place.
- **Binary clone with optional rendered PCM:** By default, the complete source WAV is copied atomically and its PCM `data` chunk remains identical. If `replacement_audio_path` is supplied, that WAV must contain unique `fmt ` and `data` chunks, use PCM or WAVE_EXTENSIBLE PCM, have the same channel count, sample rate, byte rate, block alignment, bit depth and exact `data` size, and its PCM bytes replace only the clone's `data` payload. In either mode, filename metadata, BWF/`regn` references, media tokens and UMID identity are renewed and synchronized with the new PTX definition and `0x1003` entry. The method validates and installs rendered samples but performs no DSP itself.
- **Caller-owned save:** A successful call creates the new WAV and mutates the session object. The caller must subsequently call `save()` for the PTX destination; if that later save fails, the caller is responsible for removing the already-created WAV.

The root, zero-offset virtual and nonzero-offset virtual relink paths were opened and played successfully in Pro Tools. The production OttoAlign2 validation then relinked 388 placements to independently rendered WAVs in a 42-minute session; all resulting media were online and the complete session opened and played successfully.

### Fades and crossfades

- **Geometry container:** Fade operations and the default `get_timeline_clips()` mode require one valid `0x2630` geometry list whose count and existing event bindings are consistent. Reading recognizes the verified `0x262f` payload lengths 22 (Fade In), 26/27 (Fade Out) and 34 bytes (crossfade); other geometry layouts are rejected. Applications needing audio placements only may call `get_timeline_clips(include_fades=False)`, which excludes fades and does not interpret or validate their geometry.
- **Standalone fades:** Only Fade In and Fade Out are supported, with `power` and `linear` shapes. The requested target must resolve one clip instance, the fade must remain inside its bounds and an equivalent fade cannot already exist at that target.
- **Addition only:** Existing fades cannot be reshaped, moved or deleted through the API.
- **16-bit duration:** Standalone fade and crossfade lengths are limited to 65,535 samples by the verified geometry fields. The zero-duration standalone default is one second, so it is not representable at sample rates above 65,535 Hz and must be replaced with an explicit shorter duration.
- **Crossfades:** `add_crossfade()` creates centered Equal Power crossfades only, requires a strictly positive duration and inherits all restrictions of `split_clip()`.

### Clip Groups

- **Dissolve only:** Clip Groups can be read and dissolved, but not created. Creation remains disabled pending reverse-engineering of the `0x2428`/`0x2501` link.
- **Simple layout only:** `delete_clip_group()` supports a session containing exactly one simple audio group, containing one track, placed exactly once. Multiple groups, multiple placements, nested groups, internal fades and non-audio events are rejected before mutation.

### Markers and automation

- **Simple point markers only:** `add_marker()` requires at least one valid audio track and exactly one valid root marker ruler. Timestamps must fit signed Int64; explicit marker indexes must be unique and between 1 and 65,535. Existing markers cannot be edited or deleted, and selection ranges or custom Memory Location properties are not created.
- **Static Clip Gain only:** `set_clip_gain()` requires one uniquely named clip and one valid global `0x2637` dictionary. Values must fit Float32; `-inf` is represented by Pro Tools' verified finite sentinel. The value belongs to the clip definition and therefore affects all of its placements; Clip Gain breakpoints or envelopes are not supported.
- **Volume nodes only:** `add_volume_node()` supports adding or replacing Track Volume nodes, but not deleting them. The timestamp must fit UInt32 and the value is stored as signed Int16 deci-dB. The visible track must map safely by ordinal to one `0x261c` definition with one unambiguous `0x260d` and a direct `0x260a` playlist.

### Low-level API bounds

- **Block range:** `PTBlock` currently accepts the observed `block_type` range `0x00..0xFF`, although the physical field is 16-bit. Content types and serialized offsets must fit UInt16 and UInt32 respectively.
- **Tree safety:** Parsed and in-memory block trees are limited to 128 levels. Cycles, duplicate positive `original_offset` values, unknown item types and files exceeding the UInt32 address space are rejected.

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.

If you use this project or significant portions of its code, keeping the original copyright notice is required by the license. Attribution in your documentation or acknowledgments is appreciated.

---

Conçu par Sébastien Bédard

*Legal Disclaimer: This tool is the result of independent reverse engineering. No official technical documentation, proprietary source code, or confidential information from Avid Technology or any other third party was used or referenced in the creation of this project. It is an independent endeavor created solely for educational and interoperability purposes.*
