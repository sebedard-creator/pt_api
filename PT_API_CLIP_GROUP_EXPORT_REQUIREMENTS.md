# PT API: Clip Group Note Export Requirements

## Objective

Extend `pt_api` so Playback Notes Sync can generate a Pro Tools `.ptx` session
from a native template and place one named, fixed-duration Clip Group per
exported note on an existing notes track.

The desired Pro Tools result is a dedicated track containing visible Clip Group
regions such as:

```text
10:14:46:12 - SFX - Sonoriser le cheval
```

The Clip Group name must contain the department prefix and the note text. The
group's timeline start comes from the exported note time code; its duration is a
single configurable constant for the whole export.

## Current API status

`pt_api` 1.4.0 can read timeline Clip Group occurrences through
`get_timeline_clip_groups()` and can dissolve one simple group through
`delete_clip_group()`. It does **not** create Clip Groups.

This is the missing capability. The current documentation explicitly leaves
Clip Group creation disabled because the relationships around the hidden
component timeline (`0x2428`) and its associated links (`0x2501`) have not yet
been sufficiently reverse-engineered and validated.

The existing template audio-session builder is not enough on its own:

- it populates existing tracks with audio clips, not Clip Groups;
- it requires source mono float BWF WAV files for every generated clip;
- it does not create, rename, or place Clip Group macros.

## Required public capability

The preferred public API is a narrow, template-driven operation rather than a
general-purpose Clip Group authoring engine.

Suggested shape (final naming is up to `pt_api`):

```python
result = build_clip_group_note_session(
    template_ptx_path="Notes_Template.ptx",
    note_track_name="PLAYBACK NOTES",
    notes=[
        {
            "name": "SFX - Sonoriser le cheval",
            "start_samples": 1_234_567_890,
        },
        {
            "name": "MIX - Raise music under dialogue",
            "start_samples": 1_235_678_901,
        },
    ],
    output_session_directory="Delivery/Notes",
    session_name="Episode_101_Notes",
)
```

The API should:

1. Start from a Pro Tools-authored template containing a verified single-track
   Clip Group prototype.
2. Duplicate that prototype once for each note.
3. Give every generated Clip Group a unique UTF-8 name.
4. Place each group on the requested existing track at the requested absolute
   sample position.
5. Preserve the template's fixed Clip Group duration and component structure.
6. Write a new `.ptx` atomically, then reload and semantically validate it
   before publishing the output.
7. Reject ambiguous layouts, unsupported group structures, duplicate names,
   unknown tracks, invalid time positions, and incomplete templates.

It does **not** need to create tracks, import arbitrary audio, alter the group
duration, create nested groups, or edit a group after creation.

## Template and reference material needed

Please provide native Pro Tools reference material made with the exact Pro
Tools version that will consume the generated sessions.

### Baseline template

Create and save a minimal session containing:

- The intended sample rate and frame rate.
- One clearly named target track, for example `PLAYBACK NOTES`.
- One short silent audio clip on that track.
- One Clip Group made from that clip, with the intended fixed duration.
- No unrelated tracks, clips, fades, routing, automation, or groups if they can
  be avoided.

Keep the source audio file(s) and the full session folder with the `.ptx`.

### Native before/after pairs

Provide several *complete session folders* (not only `.ptx` files). For each
case, include a pristine `before` session and an `after` session created by
performing exactly one native Pro Tools operation.

Required cases:

1. Create one new Clip Group from the prototype on the same track, at a later
   time code, with a different group name.
2. Create a second group from the same prototype at another time code. This
   establishes IDs, counters, name indexes, and repeated placements.
3. Create two groups at the exact same time code. This establishes ordering for
   coincident events.
4. Create a group whose name contains department syntax and punctuation, e.g.
   `SFX - Sonoriser le cheval`.
5. Create a group with non-ASCII UTF-8 text, e.g. `DIAL - Réplique à corriger`.
6. Create a group near a session boundary or sufficiently late in the timeline
   to validate timestamp widths.

For every pair, record:

- exact start time code;
- exact group duration;
- track name;
- session sample rate and frame rate;
- Pro Tools version;
- the exact UI actions used to create and name the group.

Do not open and re-save the `after` file after making the operation. The direct
before/after binary difference is the reference needed by the writer.

## Required validation

The implementation should add automated tests that verify:

- generated groups appear in `get_clips()` and `get_timeline_clip_groups()`;
- group IDs, names, track names, start samples, lengths, and end samples are
  correct;
- multiple generated groups retain distinct IDs and names;
- repeated placement and same-timestamp ordering match native Pro Tools;
- the original template group remains valid and unchanged unless intentionally
  reused by design;
- save/reload succeeds without a structural error;
- output opens successfully in the target Pro Tools version;
- after Pro Tools saves the output, the generated groups still exist with their
  names and placements intact.

The implementation should use the existing transactional model: on any
validation or write failure, it must not publish a partial session directory.

## Playback Notes Sync data needed for export

Playback Notes currently stores note positions as elapsed `MM:SS` seconds. It
does not store a full SMPTE value with hours and frames. To create precise PTX
placements, the export layer must receive or configure:

| Setting | Why it is required |
| --- | --- |
| Session sample rate | Converts a note position to absolute samples. |
| Frame rate / drop-frame mode | Converts between displayed SMPTE and samples. |
| Session start time code | Maps the app's elapsed `MM:SS` note time to the Pro Tools timeline. |
| Constant Clip Group duration | Defines the fixed duration of every generated note group. |
| Template path and target track name | Selects the approved native structure and destination track. |

The recommended text assembled by Playback Notes is:

```text
<DEPARTMENT> - <OCR or corrected note text>
```

For example:

```text
SFX - Sonoriser le cheval
```

The displayed Pro Tools time code comes from the placement, not necessarily
from the Clip Group name. If the user wants the full time code embedded in the
name as well, Playback Notes can instead emit:

```text
10:14:46:12 - SFX - Sonoriser le cheval
```

## Interim fallback

Until Clip Group creation is implemented, `pt_api.add_marker()` can create
named point markers at note positions in an existing compatible session. This
is useful for proof-of-workflow, but it is not an acceptable substitute for the
requested visible Clip Group regions on a notes track.
