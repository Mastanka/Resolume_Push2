# SEQ — step sequencer for LED bars (design)

Approved by Štefan on 2026-09-24 (tracks added the same day). Feature F18 in `docs/ideas.md`.

## Goal

Flash textures (any Resolume clips: fire, metaballs, clouds…) across the LED bars in a step
sequence, like a lighting-console chaser, from the Push 2, using the pad layout Push owners
already know from the Drum Rack step sequencer. Up to **4 textures ("tracks") play at once**,
each with its own ADSR envelope and its own steps on every bar. Patterns are edited on the
Push, saved, and switched live.

## Research behind the choices

- **Chaser (Hybrid Constructs):** sequences are built in its editor app; inside Resolume the
  plugin only exposes `Step`, `Sequence`, `Scaling`, `Echoes`, `Sustain`, `Release`. Not
  installed in Štefan's Arena; steps would not be editable on the Push. Not used.
- **Slice Transform:** its per-slice opacity is *not* exposed through the REST API (verified
  twice, also with slices added in the UI: only `Opacity`, `Black BG`, `Scaling` appear). Not used.
- **Push 2 Drum Rack sequencer** (Ableton manual): top 32 pads = steps, bottom-left 4×4 = the
  16 sounds, bottom-right 4×4 = loop length / velocities; tap = add/remove step; step brightness
  = velocity; green moving pad = playhead; hold a step + encoders = per-step values; scene
  buttons = grid 1/4…1/32t and Repeat rate; Accent, Delete, Duplicate, Double Loop, Fixed
  Length; Note / Session switch between instrument and clip grid; large kits scroll in banks
  of 16 with Octave up/down. The SEQ layout copies this. Tracks (one sound + envelope + step
  row each, selected with a button row) follow Elektron-style sequencers.
- **Lighting consoles:** a chase has rate, crossfade (snap … continuous), direction (forward /
  reverse / bounce / random) and flash fade in / out. Mapped to grid, envelope and Direction.

## Model

- **Bar** = one LED bar (an Advanced Output screen, or a configured group of screens).
- **Track** (1–4) = one texture + one envelope (A, D, S, R, Gate, Level) + steps per bar.
  Fixed colours: T1 cyan, T2 magenta, T3 orange, T4 green (`LAYER_RGB[0..3]`).
- **Pattern** (1–16) = the 4 tracks' steps and envelopes + Length + Direction + Swing.
- **Engine** = whatever makes a bar show a track's texture at a level 0–1. Interface:
  `setup(bars, tracks)`, `load_texture(track, clip, bars=None)`, `set_level(track, bar, value)`,
  `all_dark()`. Version 1 ships the **layer engine** below; an FFGL "Bars" effect engine (one
  layer per track, per-bar opacity parameters) can replace it later without touching the UI.

## Layer engine: one Resolume layer per (track, bar)

Verified through the REST API on Arena 7.23.2: `POST /composition/layers/add` (text body
`/composition/layers/N` = insert before N; empty = append on top), `POST …/clips/{C}/open`
(text body `source:///video/<name>` or `file:///…`), `POST …/layers/{L}/effects/video/add`
(text body `effect:///video/Crop`; spaces URL-encoded, e.g. `Slice%20Transform`),
`DELETE …/layers/{L}/effects/video/{offset}` (offset is 0-based), Crop params `Left`,
`Right`, `Top`, `Bottom` in composition pixels (0–16384), layer blend mode set by option name
(`video/mixer/Blend Mode` = `"Add"`), and the WebSocket action
`{"action": "set", "parameter": "/parameter/by-id/<id>", "value": v}` (from Resolume's own
example app).

**Bars** come from an Advanced Output preset XML in `~/Documents/Resolume Arena/Presets/Advanced
Output/` (config `sequencer.preset`: `newest` or a preset name). One **screen** = one bar; its
rectangle = the bounding box of its slices' `InputRect` points (composition pixels). Bars are
ordered left → right by rectangle x. Config can group several screens into one bar and disable
screens (Štefan's preset has Lumiverse 10–12 on top of Lumiverse 1; the bridge warns about
screens with identical rectangles). Composition size comes from the composition JSON
(`video/width`, `video/height`).

**Layers:** one per (track, bar), named `CH: T<track> <bar name>`, at the top of the stack,
track 1 lowest, blend mode **Add** (two tracks on one bar add up, as two fixtures would), one
Crop effect set to the bar's rectangle, opacity 0. Layer count = bars × tracks in use:
9 bars × 2 tracks = 18, 25 × 4 = 100. Every layer renders its texture at composition size, so
for an LED-only show with many layers set the composition small (e.g. 640 × 360; bars stay
taller than their 141 LEDs). Comfortable up to ~40 layers at 1920 × 1080 on a MacBook.

**Setup** (`python push_resolume_bridge.py --setup-chaser`, or **Shift + Note** on the Push):
builds / updates the layers for every track that has a texture (at least track 1). Idempotent by
layer name: existing layers get their crop rectangle, blend mode and order updated, never
duplicated; layers whose bar no longer exists are left alone and reported.
`--setup-chaser --dry-run` prints the plan. The bridge never deletes layers.

**Texture:** **Browse** loads the clip currently selected in the grid (`Bridge.sel`) into the
selected track; **Browse + BD*n*** = into track *n* (this is also how a track gets its first
texture: its layers are built on the spot if missing); **Browse + bar pad** = only that bar of
the selected track. The clip goes into column `sequencer.clip_column` (default 1) of each bar
layer. Generator clips: `open` with `source:///video/<video/description>`, then copy every
`video/sourceparams` value by name via `PUT`. File clips: `open` with the file URL from
`video/fileinfo` (its exact shape is read from a real file clip during implementation; if no
path can be found the display says "file clips: not supported yet"). Clip effects are not
copied; the bar layer's own Crop does the masking. After loading, the bar clips are connected
(they play; the layer opacity is what flashes). With nothing selected the display says
"select a clip first".

**Flashing:** `set_level(track, bar, v)` → that layer's `video/opacity` (its `master` stays at
100 % as a manual override) over the WebSocket `set`, REST `PUT` as fallback. Only changed
values are sent, at most 50/s per layer, 30/s when more than 16 layers change at once.
Sequencer stopped = every bar layer at 0 (dark); the rest of the show is untouched. `CH:` layers
are hidden from the pad grid and from MIX (a visible-layer list replaces `layer_offset`
arithmetic).

## Push layout (Push's Loop Selector layout)

**Enter / leave:** **Note** or BU4 = SEQ; **Session** = back to the clip grid. The sequencer
keeps running whichever menu is shown.

```
rows 1–4   steps 1–32 of the selected track on the selected bar
           off = dark · on = track colour, brighter = higher level · green pad = playhead
           steps beyond Length unlit
rows 5–8   left 4×4  = bars 1–16: bottom row = bars 1–4 left → right, the row above = 5–8,
                       … top row of the block = 13–16 (Push's drum-pad numbering)
                       dim grey = idle · lit in the colour of the track flashing it now
                       (white when two tracks overlap) · white ring = selected
           right 4×4 = patterns 1–16, numbered the same way   white = current, dim = has steps, off = empty
steps      row 1 = steps 1–8 left → right, row 2 = 9–16, row 3 = 17–24, row 4 = 25–32
BD1–BD4    tracks 1–4: bright = selected, dim = has a texture, off = empty.  BD5–BD8 unused
```

| Control | SEQ meaning |
|---|---|
| BD1–BD4 | Select the track to edit (steps, envelope knobs) |
| Tap a step | Toggle. New step level = tap velocity (Accent on = 100 %), gate = track Gate |
| Hold step(s) + knob | Edit those steps' Level / Gate (knobs 8 / 5) |
| Bar pad press / release | Select the bar; flash it with the selected track (attack → sustain while held, release on release). Works whether or not the sequencer runs |
| Repeat + hold bar pad | Strobe that bar at the grid rate |
| Pattern pad | Switch at the start of the next bar (4 beats); Shift + pad = now |
| Buttons right of the pads | Grid 1/4 … 1/32t (step length = that note at Resolume's BPM); Flash is off in SEQ |
| Play | Run / stop (green while running). Stop lets every bar finish its release |
| Delete + step / bar pad / BD*n* / pattern pad | Clear that step / the selected track's steps on that bar / track *n*'s steps / the whole pattern |
| Duplicate + pattern → pattern | Copy the whole pattern |
| Double Loop | Double Length (max 32), copying every track's steps |
| Fixed Length + BD*n* | Length = *n* × 4 steps (4 … 32) |
| Octave ▲ / ▼ | Next / previous bank of 16 bars (only with more than 16 bars) |
| Browse / Browse + BD*n* / Browse + bar pad | Load the selected clip into the selected track / track *n* / one bar |
| Shift + Note | Run setup (build / update the bar layers) |
| Swing encoder | Swing 0–100 % (delays every second grid step) |
| Tap Tempo, Tempo encoder | As today; the chase follows Resolume's BPM and resync |

**Knobs** (Shift = fine). K1–K5 and K8 belong to the selected **track**, K6–K7 to the **pattern**:

| K | Name | Scope | Range / default |
|---|---|---|---|
| 1 | Attack | track | 0 – 4 beats, 0 |
| 2 | Decay | track | 0 – 4 beats, 0 |
| 3 | Sustain | track | 0 – 100 %, 100 % |
| 4 | Release | track | 0 – 4 beats, 0.1 |
| 5 | Gate | track | 10 – 100 % of the step, 50 % |
| 6 | Direction | pattern | forward / reverse / bounce / random |
| 7 | Length | pattern | 1 – 32 steps, 16 |
| 8 | Level | track | 0 – 100 %, 100 % (scales every flash of the track) |

**Display:** the 8 values with the selected track's envelope drawn in its colour; bottom line
`SEQ  P3  ▶ step 5/16  1/16  bars 1–16   T2 Clouds   bar: Lumiverse 3`, beat dots,
LIVE / POLL, and short messages (`note_msg`) for setup and load results.

## Timing and envelope

- A sequencer thread ticks at 100 Hz off `Bridge.beat()` (Resolume BPM + tap / resync anchor).
  Step k starts at grid step k from the pattern start. "Swing delays every second grid step":
  steps 2, 4, 6 … (1-based) start later by `swing × half a step`.
- A step triggers (track, bar): level(t) = attack ramp 0 → 1 over A, decay to Sustain over D,
  hold while the gate is open (Gate × step length), then release to 0 over R. Value = envelope
  × step level × track Level. A retrigger restarts the envelope from the current value (no jump
  to 0). Bar pad presses use the same envelope with the gate held while the pad is down.
- Direction changes the step order only; Length caps it; both apply to all tracks of the
  pattern. Random never repeats the same step twice in a row.
- Pattern switches take effect at the next bar boundary (every 4 beats counted from the beat
  anchor, i.e. beat index 0 of `Bridge.beat()`); the outgoing pattern's bars finish their release.

## Storage: `chases.yaml` (next to the script, config `chases_file`)

```yaml
patterns:
  - name: P1
    length: 16
    direction: forward
    swing: 0.0
    tracks:
      - texture: {source: Metaballs, params: {Grid: 20, Color: "#ff8b58ff"}}   # or {file: file:///…}
        envelope: {attack: 0.0, decay: 0.0, sustain: 1.0, release: 0.1}         # beats / fraction
        gate: 0.5
        level: 1.0
        steps:                  # bar name → [step (0-based), level 0–1, gate or null = track gate]
          "Lumiverse 1": [[0, 1.0, null], [8, 0.8, null]]
      - texture: null           # empty track
```
Bars are keyed by screen name so patterns survive layer renumbering. Saved on every edit.
Without the file the bridge starts with 16 empty patterns P1–P16 (4 empty tracks each) using the
defaults above. Textures are per pattern; Duplicate copies them with the pattern.

## Config (`config.yaml`)

```yaml
sequencer:
  preset: newest          # Advanced Output preset name, or newest
  bars: auto              # or: [{name: Left, screens: [Lumiverse 1, Lumiverse 2]}, …]
  disabled: []            # screen names to skip
  layer_prefix: "CH: "
  clip_column: 1
  tracks: 4               # 1–4
chases_file: null         # default chases.yaml next to the script
```

## Errors

- No preset file → display "no Advanced Output preset — save one in Arena" (console too).
- Setup refused (HTTP 412 composition locked, 400) → message with the HTTP code; nothing partial
  is retried automatically.
- No `CH:` layers yet → SEQ shows "press Shift + Note to build bar layers"; pads stay dark.
- A track without texture: its steps can be edited, but nothing flashes and the display says
  "T3: no texture — Browse + BD3".
- WebSocket down → opacities go over REST; display shows POLL.

## Tests

- `tests/mock_resolume.py`: `layers/add` (append / insert), `clips/{C}/open` (source and file,
  sets `video/description` and `sourceparams`), effect add for `Crop` with its params, layer
  effect delete, layer blend mode, WebSocket `set` action, and a log of opacity values with
  timestamps (`GET /api/v1/_opacity_log`).
- `tests/test_fake_push.py`: run setup against the mock with a small preset XML in a temp folder
  (3 bars, one duplicate screen to test the warning) → layers named per (track, bar), crops,
  blend mode, idempotency; enter SEQ; load two textures (Browse + BD1, Browse + BD2); program
  steps on two bars per track; run at 240 BPM, 1/4 grid → assert the flash order per track, that
  a 1-beat attack ramps up over ~4 ticks, that release reaches 0, that overlapping tracks light
  the bar pad white; switch pattern; hidden layers don't appear in the pad grid or MIX;
  `TEST_POLL=1` variant sends over REST.
- `tests/render_preview.py`: SEQ display PNG (running, with envelope).
- `--check` gains: WebSocket `set`, `clips/open` of `Checkered` into an empty slot (undone with
  `clip/clear`), Crop add + delete, layer blend mode. Layer creation is not checked
  automatically (no delete endpoint); `--setup-chaser --dry-run` shows the plan instead.

## Out of scope for this version (later ideas)

The FFGL "Bars" effect engine (one layer per track; same `Engine` interface), recording pad hits
into steps (Record), step probability, a Video Router shared texture, a Resolume layer group for
the `CH:` layers, the MIDI → Slice Transform engine, the Chaser-plugin engine.
