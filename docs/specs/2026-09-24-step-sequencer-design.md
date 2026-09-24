# SEQ — step sequencer for LED bars (design)

Approved by Štefan on 2026-09-24. Feature F18 in `docs/ideas.md`.

## Goal

Flash a texture (any Resolume clip: fire, metaballs, clouds…) across the LED bars in a step
sequence, like a lighting-console chaser, from the Push 2, using the pad layout Push owners
already know from the Drum Rack step sequencer. Each flash has an ADSR envelope. Patterns are
edited on the Push, saved, and switched live.

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
  of 16 with Octave up/down. The SEQ layout copies this.
- **Lighting consoles:** a chase has rate, crossfade (snap … continuous), direction (forward /
  reverse / bounce / random) and flash fade in / out. Mapped to grid, envelope and Direction.

## Engine: one Resolume layer per bar

Verified through the REST API on Arena 7.23.2: `POST /composition/layers/add` (text body
`/composition/layers/N` = insert before N; empty = append on top), `POST …/clips/{C}/open`
(text body `source:///video/<name>` or `file:///…`), `POST …/layers/{L}/effects/video/add`
(text body `effect:///video/Crop`; spaces URL-encoded, e.g. `Slice%20Transform`),
`DELETE …/layers/{L}/effects/video/{offset}` (offset is 0-based), Crop params `Left`,
`Right`, `Top`, `Bottom` in composition pixels (0–16384), and the WebSocket action
`{"action": "set", "parameter": "/parameter/by-id/<id>", "value": v}` (from Resolume's own
example app).

**Bars** come from an Advanced Output preset XML in `~/Documents/Resolume Arena/Presets/Advanced
Output/` (config `sequencer.preset`: `newest` or a preset name). One **screen** = one bar; its
rectangle = the bounding box of its slices' `InputRect` points (composition pixels). Bars are
ordered left → right by rectangle x. Config can group several screens into one bar and disable
screens (Štefan's preset has Lumiverse 10–12 on top of Lumiverse 1; the bridge warns about
screens with identical rectangles). Composition size comes from the composition JSON
(`video/width`, `video/height`).

**Setup** (`python push_resolume_bridge.py --setup-chaser`, or **Shift + Note** on the Push):
for each bar, ensure a layer named `CH: <bar name>` exists at the top of the stack with one
Crop effect set to the bar's rectangle and opacity 0. Idempotent: existing `CH:` layers are
updated (crop rectangle, order), never duplicated; layers whose bar no longer exists are left
alone and reported. `--setup-chaser --dry-run` prints the plan. The bridge never deletes layers.

**Texture:** **Browse** loads the clip currently selected in the grid (`Bridge.sel`) into
column `sequencer.clip_column` (default 1) of every bar layer; **Browse + bar pad** = that bar
only. Generator clips: `open` with `source:///video/<video/description>`, then copy every
`video/sourceparams` value by name via `PUT`. File clips: `open` with the file URL from
`video/fileinfo` (exact key confirmed at implementation with a file clip). After loading, the
bar clips are connected (they play; the layer opacity is what flashes). With nothing selected
the display says "select a clip first".

**Flashing:** bar level 0–1 → layer `video/opacity` (or `master`, whichever `master_param`
resolves) over the WebSocket `set`, REST `PUT` as fallback. Only changed values are sent, at
most 50/s per bar, 30/s when more than 16 bars change at once. Sequencer stopped = every bar
layer at 0 (dark); the rest of the show is untouched. `CH:` layers are hidden from the pad
grid and from MIX (visible-layer list replaces `layer_offset` arithmetic).

## Push layout (Push's Loop Selector layout)

**Enter / leave:** **Note** or BU4 = SEQ; **Session** = back to the clip grid. The sequencer
keeps running whichever menu is shown.

```
rows 1–4   steps 1–32 of the selected bar   off = dark · on = bar colour, brighter = higher level
                                            green pad = playhead · steps beyond Length unlit
rows 5–8   left 4×4  = bars 1–16 (bottom-left = bar 1, rising, as on Push)
                       dim = idle · bright = lit right now · white ring = selected
           right 4×4 = patterns 1–16   white = current, dim = has steps, off = empty
```

| Control | SEQ meaning |
|---|---|
| Tap a step | Toggle. New step level = tap velocity (Accent on = 100 %), gate = pattern Gate |
| Hold step(s) + knob | Edit those steps' Level / Gate (knobs 8 / 5) |
| Bar pad press / release | Select the bar; flash it (attack → sustain while held, release on release) |
| Repeat + hold bar pad | Strobe that bar at the grid rate |
| Pattern pad | Switch at the start of the next bar (4 beats); Shift + pad = now |
| Buttons right of the pads | Grid 1/4 … 1/32t (step length = that note at Resolume's BPM); Flash is off in SEQ |
| Play | Run / stop (green while running). Stop lets every bar finish its release |
| Delete + step / bar pad / pattern pad | Clear that step / that bar's steps / the pattern |
| Duplicate + pattern → pattern | Copy |
| Double Loop | Double Length (max 32), copying steps |
| Fixed Length + BD1–4 | Length 8 / 16 / 24 / 32 |
| Octave ▲ / ▼ | Next / previous bank of 16 bars (only with more than 16 bars) |
| Browse, Browse + bar pad | Load the selected clip as texture into all bars / that bar |
| Shift + Note | Run setup (build / update the bar layers) |
| Swing encoder | Swing 0–100 % (delays every second grid step) |
| Tap Tempo, Tempo encoder | As today; the chase follows Resolume's BPM and resync |

**Knobs** (per pattern, Shift = fine):

| K | Name | Range / default |
|---|---|---|
| 1 | Attack | 0 – 4 beats, 0 |
| 2 | Decay | 0 – 4 beats, 0 |
| 3 | Sustain | 0 – 100 %, 100 % |
| 4 | Release | 0 – 4 beats, 0.1 |
| 5 | Gate | 10 – 100 % of the step, 50 % |
| 6 | Direction | forward / reverse / bounce / random |
| 7 | Length | 1 – 32 steps, 16 |
| 8 | Level | 0 – 100 %, 100 % (scales every flash) |

**Display:** the 8 values with a drawn envelope; bottom line
`SEQ  P3  ▶ step 5/16  1/16  bars 1–16   bar: Lumiverse 3   texture: Metaballs`, beat dots,
LIVE / POLL, and short messages (`note_msg`) for setup and load results.

## Timing and envelope

- A sequencer thread ticks at 100 Hz off `Bridge.beat()` (Resolume BPM + tap / resync anchor).
  Step k starts at grid step k from the pattern start; swing delays odd steps by
  `swing × half a step`.
- A step triggers its bar: level(t) = attack ramp 0 → 1 over A, decay to Sustain over D, hold
  while the gate is open (Gate × step length), then release to 0 over R. Value = envelope ×
  step level × pattern Level. A retrigger restarts the envelope from the current value (no jump
  to 0). Bar pad presses use the same envelope with the gate held while the pad is down.
- Direction changes the step order only; Length caps it. Random never repeats the same step
  twice in a row.
- Pattern switches take effect at the next 4-beat boundary; the outgoing pattern's bars finish
  their release.

## Storage: `chases.yaml` (next to the script, config `chases_file`)

```yaml
patterns:
  - name: P1
    length: 16
    envelope: {attack: 0.0, decay: 0.0, sustain: 1.0, release: 0.1}   # beats / fraction
    gate: 0.5
    direction: forward
    level: 1.0
    swing: 0.0
    steps:                      # bar name → [step (0-based), level 0–1, gate or null = pattern gate]
      "Lumiverse 1": [[0, 1.0, null], [8, 0.8, null]]
```
Bars are keyed by screen name so patterns survive layer renumbering. Saved on every edit.

## Config (`config.yaml`)

```yaml
sequencer:
  preset: newest          # Advanced Output preset name, or newest
  bars: auto              # or: [{name: Left, screens: [Lumiverse 1, Lumiverse 2]}, …]
  disabled: []            # screen names to skip
  layer_prefix: "CH: "
  clip_column: 1
chases_file: null         # default chases.yaml next to the script
```

## Errors

- No preset file → display "no Advanced Output preset — save one in Arena" (console too).
- Setup refused (HTTP 412 composition locked, 400) → message with the HTTP code; nothing partial
  is retried automatically.
- No `CH:` layers yet → SEQ shows "press Shift + Note to build bar layers"; pads stay dark.
- WebSocket down → opacities go over REST; display shows POLL.

## Tests

- `tests/mock_resolume.py`: `layers/add` (append / insert), `clips/{C}/open` (source and file,
  sets `video/description` and `sourceparams`), effect add for `Crop` with its params, layer
  effect delete, WebSocket `set` action, and a log of opacity values with timestamps
  (`GET /api/v1/_opacity_log`).
- `tests/test_fake_push.py`: run setup against the mock with a small preset XML in a temp folder
  (3 bars, one duplicate screen to test the warning) → layers, crops and idempotency; enter
  SEQ; program steps on two bars; run at 240 BPM, 1/4 grid → assert the flash order, that a
  1-beat attack ramps up over ~4 ticks, that release reaches 0; switch pattern; Browse loads
  the selected generator into all bar clips with its params; hidden layers don't appear in the
  pad grid or MIX; `TEST_POLL=1` variant sends over REST.
- `tests/render_preview.py`: SEQ display PNG (running, with envelope).
- `--check` gains: WebSocket `set`, `clips/open` of `Checkered` into an empty slot (undone with
  `clip/clear`), Crop add + delete. Layer creation is not checked automatically (no delete
  endpoint); `--setup-chaser --dry-run` shows the plan instead.

## Out of scope for this version (later ideas)

Recording pad hits into steps (Record), step probability, per-bar textures beyond Browse + pad,
a Video Router shared texture (one render for all bars), Resolume layer group for the `CH:`
layers, the MIDI → Slice Transform engine, the Chaser-plugin engine.
