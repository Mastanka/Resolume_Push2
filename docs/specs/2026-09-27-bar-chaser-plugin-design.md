# Bar Chaser — FFGL effect engine for SEQ (design)

Approved by Štefan on 2026-09-27. Replaces the per-bar-layer engine of
`2026-09-24-step-sequencer-design.md`; everything not mentioned here stays as in that spec.

## Why

The layer engine needed one Resolume layer per (track, bar): 36+ layers, copies of every texture,
and a composition that looked like a workaround. With an effect on the texture layer itself there
is nothing to copy: the layer's clip *is* the texture, editing it changes every bar, launching
another clip on that layer swaps the texture live. One effect per track, at most four layers, all
the user's own.

## The effect: "Bar Chaser"

FFGL 2.1 effect (`FF_EFFECT`), universal macOS bundle (arm64 + x86_64), built from `plugin/` with
CMake; the FFGL SDK library (`source/lib`, BSD licence) and pugixml (MIT) are vendored. Installed
into `~/Documents/Resolume Arena/Extra Effects/Bar Chaser.bundle` (or the folder set in Arena →
Preferences → Video). Arena loads it at start.

**Parameters, in panel order** (names are the REST names the bridge uses):

| # | Name | Type | Meaning |
|---|---|---|---|
| 0 | `Preset` | text | Advanced Output preset name (without `.xml`) in `~/Documents/Resolume Arena/Presets/Advanced Output/`, or a full path; empty = newest file there |
| 1 | `Reload` | event | Re-read the preset and refill the pad dropdowns |
| 2 | `Track` | option `1`–`4` | Which Push track this instance answers to (default 1) |
| 3 | `Master` | 0–1, default 1 | Scales every level |
| 4 | `Edge` | 0–20 px, default 0 | Soft edge inside each bar rectangle |
| 5 | `Outside` | option `Transparent` / `Black` / `Pass through` | What the effect outputs outside every assigned bar |
| 6 | `Show pads` | bool | Setup overlay: every assigned rectangle is filled and shows its pad number |
| 7–30 | `Pad 1` … `Pad 24` | option | Which screen / slice this pad is. Elements: `—`, then every screen of the preset (bounding box of its slices) sorted by x, then, for screens with several slices, `Screen / slice`. Default: pad *k* = *k*-th screen |
| 31–54 | `Level 1` … `Level 24` | 0–1, default 0 | Live level per pad. The bridge writes these; dragging one in Arena is the manual test |

Dropdown elements are rebuilt with `SetParamElementInfo` + `RaiseParamEvent(…, FF_EVENT_FLAG_ELEMENTS)`
after a (re)load; current choices are kept by name when the preset changes.

**Rendering:** one fragment shader. Each pad has a rectangle in preset composition space
(`CurrentCompositionTextureSize` in the XML), converted to texture UV (y flipped, FFGL origin is
bottom-left). For a pixel: factor = max over the pads whose rectangle contains it of
`Level × Master × edge softness`; output = input × factor (rgb and alpha). Pixels outside every
rectangle follow `Outside`. `Show pads` ignores the levels and paints each rectangle with a pad
colour plus its number (3×5 bitmap digits laid along the rectangle's longer axis).

**Not in this version:** envelopes inside the plugin (the host's `SetBeatInfo` bpm / bar phase is
available for that later), Windows build, per-pad colour.

## The bridge

- `chaser_engine.py` becomes `PluginEngine`, same interface as before: `ready(track)`,
  `set_level(track, pad, value)`, `all_dark()`, plus `pad_name(track, k)` (the dropdown's current
  value, `—` = unassigned) and `layers_of(track)` (layer indices carrying an instance with that
  `Track`). Instances are found by effect `name == "Bar Chaser"` on layers and clips; the level
  parameter ids are cached per composition object and sent over the WebSocket `set`, REST fallback,
  same rate limiting as before. The layer engine, its `CH:` layers, layer hiding, `--setup-chaser`
  and `load_texture` are removed.
- **Setup on the Push:** **Shift + Note** adds Bar Chaser to the selected clip's layer (REST
  `effect:///video/Bar%20Chaser`) if the layer has none. **Browse + BD*n*** sets that layer's
  instance `Track` to *n* (adds the effect first if missing). **Browse** alone = Track of the
  selected track's number, i.e. "this layer joins the selected track".
- `--install-plugin [PATH]` copies the bundle (default `plugin/dist/Bar Chaser.bundle`) into the
  Extra Effects folder and reminds the user to restart Arena. The bridge no longer reads the
  preset XML; `sequencer.preset`, `bars`, `disabled`, `layer_prefix`, `clip_column` leave
  `config.yaml`.
- Steps are keyed by pad: bar names in `chases.yaml` are `pad 1` … `pad 24`. Re-assigning a pad in
  Arena keeps the pattern.

## The Push

```
rows 1–4   32 steps of the selected track (unchanged)
row 5      patterns 1–8, left → right; Shift + pad = patterns 9–16
rows 6–8   pads 1–24: bottom row = 1–8 left → right, then 9–16, top of the block = 17–24
```

- **Pad press** = select only that pad and flash it (release = release). **Shift + pad press** =
  add to / remove from the selection (also flashes). The selection is a set (`Bridge.sel_pads`),
  never empty after a plain press.
- **Steps** act on every selected pad: if the step is on for all of them → off for all, else → on
  for all (level = tap velocity, Accent = 1). Step pad colour: full = on for all selected, dim =
  on for some, dark grey = off. Hold step + knob 8 / 5 edits that step on all selected pads.
  Delete + step removes it from all selected.
- **Pad LEDs:** unassigned (`—`) = off · assigned idle = dark grey · selected = light grey · lit =
  colour of the track flashing it · two tracks = white.
- **Patterns** on row 5: white = current, grey = has steps, off = empty, blinking = queued.
  Shift + pattern pad = pattern 9–16 (Shift + pad no longer means "switch now"; switching is
  always at the next bar boundary — press the same pad twice to switch now).
- Octave ▲ / ▼ unused (unlit). Everything else as in the SEQ spec.
- **Display bottom line:** `T1 · FIRE  ·  pads 2, 5 (Lumiverse 2, Lumiverse 5)`; with no instance
  for the track: `T2: no Bar Chaser — Shift + Note on a layer`.

## Tests

- `plugin/tests/test_preset.cpp`: parses `tests/fixtures/preset_small.xml` → screens sorted by x,
  bounding boxes, `Screen / slice` entries, newest-file lookup. Built and run by CMake (`ctest`).
- `tests/mock_resolume.py`: `effect:///video/Bar%20Chaser` adds a fake instance with the 55
  parameters above (`Pad k` options `—`, `Bar A`, `Bar B`, `Bar C`; defaults pad 1–3 assigned).
- `tests/test_engine.py`: `PluginEngine` finds instances by Track, names pads, rate-limits levels,
  `all_dark`.
- `tests/test_fake_push.py`: Shift + Note adds the effect to L1; Browse + BD2 makes L2 track 2;
  Shift + pad multi-select; step toggles on two pads; run → `Level 1` then `Level 2` rise on L1's
  instance two steps apart; release reaches 0; pattern row 5; Session leaves SEQ.
- Štefan: install the bundle, restart Arena, add Bar Chaser to a layer, drag a Level slider (the
  bar must light), tick Show pads (numbers on the preview), then the Push.
