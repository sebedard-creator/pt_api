# pt_api

*(Reverse-engineered and tested against **Pro Tools Ultimate 2024.3.1** on sessions at **23.98, 24, and 29.97df fps**)*

A standalone, dependency-free Python API for parsing, manipulating, and re-encrypting Pro Tools (`.ptx`) session files in place. 

> [!NOTE]
> **Language Notice:** While this README is provided in English, please note that the underlying codebase, comments, and deep technical documentation (`architecture.md`, `pt_format_specs.md`, etc.) are written entirely in French.

## Overview
pt_api bypasses the need for the Pro Tools application or SDKs to modify session files. It natively decrypts the `.ptx` proprietary XOR stream, parses the binary block tree, applies targeted edits to the timeline, fades, and automation, and correctly reconstructs the pointer tables (`0x0002` block) before re-encrypting the file. 

The API guarantees a bit-perfect roundtrip for untouched data, preventing the dreaded "Magic ID does not match" corruption error.

## API Capabilities

The `ProToolsSession` class in `pt_api.py` exposes a high-level API to manipulate the session programmatically.

| Capability | Method | Description |
|---|---|---|
| **I/O & Crypto** | `__init__(file)`, `save(file)` | Decrypts and parses the session on load. Rebuilds pointer tables and encrypts on save. |
| **Markers** | `add_marker(name, timecode)` | Injects a new Memory Location / Marker at a specific timecode. |
| **Clip Operations** | `rename_clip(name, new_name)` | Renames an audio clip directly in its definition block. |
| | `mute_clip(clip_name, mute)` | Toggles the mute state of a clip on the timeline. |
| | `move_clip(clip_name, timecode)` | Moves a clip to a new absolute timestamp on the timeline. |
| | `duplicate_clip(name, timecode)`| Clones a timeline event to create a duplicate clip at a new location. |
| | `split_clip(name, timecode)` | Splits a clip into two virtual sub-clips (`-01`, `-02`), managing 24-bit internal trimming offsets. |
| **Fades & Crossfades** | `add_fade(clip_name, type, len)` | Injects a native Equal Power Fade In or Fade Out. Generates fade geometries and track caches. |
| | `add_crossfade(c1, c2, len)` | Injects a native Equal Power Crossfade between two adjacent clips. |
| **Automation** | `set_clip_gain(clip_name, dB)` | Applies static Clip Gain (Float32). Supports precise dB values (e.g., `6.0`, `-10.0`) and `-inf`. |
| | `add_volume_node(track, tc, val)`| Adds volume automation nodes (deci-dB) to a track's playlist. |
| **Clip Groups** | `delete_clip_group(group_name)` | Safely purges a Clip Group, its hidden timeline, and repairs all affected memory pointers. |

## Current Limitations
- **Split Clip Duration Limit:** The `split_clip` logic currently relies on Pro Tools' standard 24-bit internal source offset flag (`01 30`). This mathematically limits splitting to clips that are shorter than ~5.8 minutes at 48kHz (16,777,215 samples). Support for 32-bit or 64-bit flagged long-clip trimming is not yet reverse-engineered.
- **Clip Group Creation:** While the API can safely read and *delete* Clip Groups, *creating* them from scratch is disabled pending further reverse-engineering of the internal pointer link between `0x2428` and `0x2501` blocks.
- **Trimming:** Native `trim_clip_start` and `trim_clip_end` functions are planned but not yet implemented.
- **Unsupported Features:** The API currently focuses on Audio clips and Volume/Clip Gain automation. Editing MIDI data, Inserts, Sends, and other automation playlists (Pan, Mute, etc.) is not yet supported and remains to be reverse-engineered.

## License

This project is licensed under the MIT License. See the [LICENSE](file:///Y:/pt_api/LICENSE) file for details.

If you use this project or significant portions of its code, keeping the original copyright notice is required by the license. Attribution in your documentation or acknowledgments is appreciated.

---

Conçu par Sébastien Bédard

*Legal Disclaimer: This tool is the result of independent reverse engineering. No official technical documentation, proprietary source code, or confidential information from Avid Technology or any other third party was used or referenced in the creation of this project. It is an independent endeavor created solely for educational and interoperability purposes.*
