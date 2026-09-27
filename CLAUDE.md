# Push 2 → Resolume Arena bridge — handover

Python app that turns an **Ableton Push 2** (without Ableton Live) into a clip launcher and
parameter controller for **Resolume Arena**, with a custom UI on the Push display.
Status: **v0.1 working on real hardware** (confirmed by the owner, Štefan). Now in iterative development.

## Owner & rig context

- Owner: Štefan. Prefers **brief, to-the-point explanations**. Works in Czech and English.
- Machine: MacBook (macOS, Apple Silicon), Resolume Arena **7.23**, Webserver on port **8080**.
- Use: concert lighting. Resolume pixel-maps **10 vertical WS2815 LED bars × 141 px** behind the
  band, via GICO-6612PRO Art-Net controllers (one Lumiverse per bar).
- Other controller in use: **Novation Launch Control XL**, mapped in Resolume's own MIDI
  shortcuts (faders = layer masters). The bridge must keep coexisting with it — never grab its ports.
- Typical content: strobes, chases/comets, rolling and odd/even strobes per bar, ambient clouds.

## Files

| File | Purpose |
|---|---|
| `push_resolume_bridge.py` | Entry point: constants, `Bridge` (state + input), `run()`, `--dump`, config |
| `resolume_api.py` | Resolume JSON helpers (`resolve_node`, `walk`, colour helpers), REST client, `Sender` |
| `resolume_check.py` | `--check LAYER`: tries every Resolume call on a spare layer, undoes it, prints OK/FAIL |
| `colors.yaml` | Own COLOR palette (Shift + BD saves), Štefan's show data |
| `display.py` | `render()`: draws a `Bridge.snapshot()` on the 960×160 display, `LAYER_RGB` |
| `sequencer.py` | SEQ logic, pure: bars from the Advanced Output preset XML, `Envelope`, `Track` / `Pattern`, `Sequencer` (editing, `tick()`, `chases.yaml`) |
| `chaser_engine.py` | `PluginEngine`: levels → `Level n` params of the Bar Chaser effect instances (grouped by their `Track`); one shared pad mapping (`sync_pads`, run by `Bridge.set_comp`; `set_pad`) |
| `rig.py` | SEQ presets: the mapped pads in physical order with long (D) / short (S) roles, from the Advanced Output preset (`preset_rects` = Python twin of `plugin/src/Preset.cpp`) |
| `banks.py` | SEQ presets: the 16 Techno recipes in role space, `realize()` onto a rig, `refit()`, `presets()` |
| `plugin/` | The **Bar Chaser** FFGL effect (C++, CMake, vendored FFGL SDK lib + pugixml). `plugin/build.sh` → `plugin/dist/Bar Chaser.bundle`; `ctest` runs the preset-parser test and an offscreen GL host test |
| `chases.yaml` | Sequencer patterns, Štefan's show data |
| `tests/test_sequencer.py` | Pure tests for `sequencer.py` (no mock) |
| `tests/test_engine.py` | `PluginEngine`, shared mapping, PRESETS and screens through a `Bridge`, REST calls, against the mock |
| `tests/test_banks.py` | Rig detection and every recipe on 110 rigs; Python preset reader = plugin names |
| `tests/test_states.py` | Stuck-state regressions (flash / Play / Delete across views, held clip across a scroll, deck switch, held step), engine flush, deferred save, preset `source`, plus a short random walk. Pure |
| `tests/test_helpers.py` | `resolve_node` / `walk` / `fmt_value` / colour helpers, one render per screen, REST retry scope, WebSocket outbox bound. Pure |
| `tests/fuzz_states.py` | State fuzzer: random physically consistent events against a `Bridge` with a synthetic composition, invariants after each; reports (exit 0). `python tests/fuzz_states.py 16 2500` ≈ 15 s |
| `tests/fixtures/rig/mock_rig.xml` | Advanced Output preset matching the mock's Bar Chaser fixtures (tests set `sequencer.preset_folder` here) |
| `tests/fixtures/preset_small.xml` | 4-screen Advanced Output preset for the plugin tests |
| `config.yaml` | Resolume host/port, grid offsets, encoder steps, per-layer parameter slots |
| `pins.yaml` | Param order for auto layers, written by the bridge (Convert move). Štefan's show data |
| `docs/specs/` | Short design specs per feature |
| `requirements.txt` | push2-python (from git), pycairo, numpy, requests, PyYAML |
| `README.md` | User-facing setup and controls |
| `LICENSE` | MIT |
| `docs/promo/` | 5 presentation PNGs + `make_promo.py` (needs `pip install segno`) |
| `.github/ISSUE_TEMPLATE/` | Idea + bug templates |
| `tests/mock_resolume.py` | Fake Resolume REST server (port 8080) with a small composition |
| `tests/test_fake_push.py` | Runs the real `run()` loop with a fake Push object; asserts it works |
| `tests/render_preview.py` | Renders the display to PNG for visual checks |

## Environment

```
brew install libusb cairo pkg-config git
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python push_resolume_bridge.py            # real Push
python push_resolume_bridge.py --sim      # push2-python browser simulator, http://localhost:6128
python push_resolume_bridge.py --dump 3   # list parameter paths for layer 3 (for config.yaml)
```

**Hard constraints**
- **Must stay Python 3.9-compatible** (macOS system python3 is 3.9; the current venv uses python.org 3.14). Keep
  `from __future__ import annotations`; no `match`, no runtime `X | Y` types, no 3.10+ stdlib APIs.
  This already caused one crash-on-start bug.
- Ableton Live must be closed (it takes the Push USB display and Live MIDI port).
- `run()` sets `DYLD_FALLBACK_LIBRARY_PATH` to Homebrew's lib dirs before importing push2_python,
  so pyusb finds libusb on Apple Silicon. Keep this.

## Control map (Štefan's labels → push2-python names)

Štefan refers to controls by these labels (`PUSH2_LAYOUT.png`):

| Label | push2-python name | Current use |
|---|---|---|
| K1–K8 | `Track1 Encoder`…`Track8 Encoder` | Menu knobs (params / colour / FX amount / layer masters / SEQ) |
| K9 | `Swing Encoder` | – |
| K10 | `Tempo Encoder` | BPM ±1 (Shift ±0.1) |
| K11 | `Master Encoder` | Selected layer opacity / composition master in MIX (ends blackout) |
| BU1–BU8 | `Upper Row 1..8` (above display) | Menus of the current view. CLIP: BU1 CLIP PARAMS, BU2 CLIP COLOR, BU3 CLIP EFFECTS, BU6 LAYER PARAMS, BU7 LAYER EFFECTS (white). SEQ: BU1 ENVELOPE, BU2 SETTINGS, BU3 PRESETS, BU4 MAPPING (red = `L6`) |
| BD1–BD8 | `Lower Row 1..8` (below display) | Context row: PARAMS pages, COLOR palette (Shift = save), MIX mute (Solo held = solo), FX on/off. Play held: launch column above |
| B_1 | `Play` (bottom-left) | Hold + pad = launch clip; hold + BD = launch column (lit green) |
| B_2 | `Record` (above B_1) | Hold + pad = stop layer (lit red) |
| B_3 | `Mix` (right of display) | MIX screen: click = open / click again = back, hold = while held |
| B_4 | `Convert` (left column) | Hold + touch knob = pick param to move |
| B_5 | `Tap Tempo` (top-left) | Resolume's tap; Shift = resync. Flashes on the beat |
| – | `Metronome` | Pad pulse on/off |
| – | `Stop` ("Stop Clip") | Blackout toggle (blinks red) |
| – | `Mute` / `Solo` | MUTE / SOLO screen (click / hold like Mix). Hold + pad = mute (layer `bypassed`) / solo that layer |
| – | `Master` (right of BD row) | COLOR on the composition's Colorize (master colour) |
| – | `Duplicate` | COLOR: hold + pad / scene button / BD = paste colour to clip / layer / column |
| – | `1/32t` … `1/4` (right of pads) | Flash: hold = that row's layer master 100 %. SEQ: pad groups 1–8 (Layout) or grid (Scale) |
| – | `Select` | SEQ: tap = multi-select latch (dim / lit), hold + pads = momentary, hold + group button = store group |
| – | `Layout` / `Scale` | SEQ: buttons right of the pads = pad groups / grid |
| – | `Note` / `Session` | The two views: SEQUENCER / CLIP. Shift + Note = add Bar Chaser to the layer |
| – | `Browse`, `Repeat`, `Accent`, `Delete`, `Double Loop`, `Fixed Length`, `Octave Up/Down`, `Swing Encoder` | SEQ only, see `docs/specs/2026-09-24-step-sequencer-design.md` |

## Pads

Plain press = select only (also `POST …/clips/{C}/select` so Resolume's clip panel follows).
Modifiers: Play = launch, Record = stop layer, Mute / Solo = toggle layer, Duplicate (COLOR) = paste.
Playing pads pulse between full and `L{k}_mid` on the beat; muted / non-solo layers turn grey.

## Modes

Screen = `Bridge.view` ("clip" = Session, "seq" = Note: what the pads show) + that view's menu
(`Bridge.menus[view]`, from `CLIP_MENUS` / `SEQ_MENUS`) + optionally an overlay (`Bridge.overlay`:
"mix" / "mute" / "solo"). `Bridge.mode` (property) = overlay or menu; its setter still accepts the old
names "params" / "fx" / "seq". Overlays: `overlay_button()` — press opens; release before `HOLD_TIME`
with nothing else touched = latched (stays), else back; pressing a latched overlay's button = back
(`ov_stack` returns to a latched overlay underneath). Menu buttons, Session, Note and Master close
overlays. Parameter page is per menu (`Bridge.page` property over `_pages`).

- **clip_params / layer_params** (BU1 / BU6): K1–K8 = parameter slots (`slots(scope)`: `AUTO_SOURCES`
  entries of that scope, or config slots with that `scope`). BD1–BD8 = page.
  **Move:** Convert + touch knob picks a slot (`move_src`, absolute index), touch another knob on any
  page → swap. Order = priority list of scope-less keys (`Slot.key`) in `pins.yaml`, applied to
  auto layers only (`Bridge.swap`, sort in `slots()`). Turns are ignored while moving.
- **color** (BU2): selected clip's `ParamColor`s (clip `video/**`, then layer `video/effects`).
  K1–K3 R/G/B, K4–K6 H/S/B (`hsv_cache` keeps hue at 0 saturation), K8 = which colour param.
  BD1–BD8 = own palette from `colors.yaml` (Shift + BD saves), else the param's Resolume `palette`;
  LEDs via palette slots 80–87. Values are `#rrggbbaa`; writes keep alpha.
  `color_target = "master"` (Master button): composition `video/effects` colour; K7 = that effect's
  Opacity, K8 = on/off (`bypassed`). Paste (`paste_color`) matches the param by `color_label()`.
- **clip_fx / layer_fx** (BU3 / BU7): `fx_list()` = the clip's / the layer's effects; K = effect
  `Opacity` param, BD = `bypassed`. Page ◀▶ = `fx_page`. Composition effects are in no menu (Master
  button reaches the composition colour).
- **seq_env / seq_settings / seq_presets / seq_mapping** (Note view, BU1–4): knobs from `SEQ_PAGES` (hold
  step + Gate / Level knob = that step). **PRESETS** (`Bridge.presets_menu()`, spec
  `docs/specs/2026-09-27-seq-presets-design.md`): BD1–8 = presets of `banks.presets()` (Shift 9–16)
  instead of tracks; `preset_pick` blinks with the pattern row; a pattern pad stores it
  (`_store_preset`: `banks.realize` on `current_rig()`, new seed, `Sequencer.store_pattern`, grid 1/16)
  or asks (`confirm`, BD7 NO / BD8 YES). `Pattern.source` = {bank, recipe, seed, rig}; step edits
  clear it (`mark_edited`, via `Sequencer.toggle_steps` / `remove_steps`, which `_seq_pad` uses). `check_rig()` after every composition / MAPPING change:
  loaded composition → `Sequencer.refit` at once; otherwise `refit_offer` → PRESETS asks. Misfit
  pattern pads orange in PRESETS. **MAPPING** (`Bridge.mapping()`,
  `_map_pad`, `_map_pad_colors`): rows 1–4 = the track's fixtures from the effect's Pad options
  (`chaser_engine.fixture_list`: "Screen / slice" entries grouped by screen → L#F#, a screen without
  fixture entries = one fixture), row 5 orange, rows 6–8 = pads. Select + fixture = `map_armed` (blinks),
  then pad = `PluginEngine.set_pad` on every instance, all tracks (shared mapping), double blink
  (`map_blink`); Delete + pad = "—"; Octave = `map_page` (32 fixtures per page). Otherwise rows 1–4 steps, row 5 patterns 1–8 (Shift 9–16), rows 6–8 pads 1–24
  (bottom-left = 1). `Bridge.sel_pads` = multi-selection (`Bridge.multi`: Select latch, or Select held →
  pad toggles); steps act on all selected pads. Buttons right of the pads: `Bridge.side` = "groups"
  (Layout, default) → pad groups: `Sequencer.track_groups[track]` (G, Select + button, track colour)
  shadow `Sequencer.groups` (GG, global, Select + Shift + button, white); `Sequencer.group(g, track)`;
  Delete (+ Shift) clears; tap recalls; `current_group()` = ("track" | "global", g) lit fully. Or "grid"
  (Scale). Groups only select pads: steps stay on their track (isolation). Pads flash only for the
  selected track. **SEQ pad colours by section** (`SECTION_RGB`, palette slots 96–107): rows 1–4 steps = shades of red
  (`S_red` on, `_mid` low level / on for some, `_off` off; green playhead), row 5 patterns = orange (`S_orange` current,
  `_dim` has steps, `_off` empty, blinking queued, `pink` = made for another rig), rows 6–8 pads = yellow (`S_yellow`
  sounding, `_mid` selected, `_dim` has a fixture, black none). **Pad mapping (shared by all tracks):** `Sequencer.pad_config` = 24 fixture names,
  saved in `chases.yaml`; `PluginEngine.sync_pads()` keeps it on every instance: a pad changed in
  Arena on any instance becomes the mapping (own writes pending `PENDING` s), new instances get it, a
  new composition master id = a loaded composition → T1's mapping (else the first instance's) is
  written to the others. Returns True for a loaded composition. BD1–4 = texture tracks = layers carrying a **Bar Chaser** effect with that `Track`.
  Knobs = the selected track's ADSR + Gate + Level and the pattern's Direction + Length. `Bridge.seq`
  (`Sequencer`, bars named `pad 1`…`pad 24`), `Bridge.engine` (`PluginEngine`), `seq_loop` thread at
  100 Hz → `engine.set_level` → WebSocket `set` of the instance's `Level n`. Shift + Note = add the
  effect to the selected clip's layer; Browse (+ BDn) = that layer's `Track`. Specs + plans in `docs/`.
  **The effect must be the last effect on the layer** (it masks in composition space; a Transform after
  it moves the bars).
- **mix / mute / solo** (overlays, either view): K1–K8 = `layer.master` (fallback `video/opacity`),
  K1 = top visible layer, going down; K11 = `composition.master`. BD = mute (mix: Solo held = solo;
  solo: solo). In the SEQ view the pads stay the sequencer; the SEQ track buttons return with the menu.
- Always: blackout (`Bridge.blackout` = saved master), flash (`Bridge.flash`), beat clock
  (`beat_anchor` from taps / resync + Resolume BPM → `beat()`), short messages (`note_msg`).

## Architecture

Five threads around one `Bridge` object guarded by `Bridge.lock` (RLock):

```
push2-python MIDI thread ──► Bridge.pad_pressed / turn / button   (mutate state only, no MIDI out)
WebSocket thread         ──► composition on connect → set_comp; parameter_update → on_param (patch by id)
poll thread              ──► GET /composition → set_comp; every 0.25 s, or every ws_refresh (5 s) while WS is live
Sender thread            ──► ordered queue (clip/column triggers, events) + param PUTs (coalesced, latest wins)
main thread (run loop)   ──► LEDs at 50 Hz (diffed vs cache, beat edges) + display frame at display_fps
```

- `ResolumeWS` subscribes by id to `Bridge.desired_ids()` (states, masters, tempo, selected clip/layer,
  composition effects), re-synced every 0.5 s. `Bridge.index` = id → node, rebuilt by `set_comp`.

- **All MIDI output (pad/button colours, palette) happens in the main thread** — push2-python/mido
  requirement. Callbacks only change state.
- `Bridge.overrides` holds values we just sent for 0.8 s so the display doesn't jump back to stale
  polled values while an encoder is turning.
- **Physical layer** (top of `Bridge.button()`): `Bridge.held` + the `HELD_FLAGS` flags are set for every press and
  release before any screen sees the event, so a screen can react to a release but never swallow it. **Momentary
  actions** are keyed by the control that started them, target captured at the press: `flash[row] = (master id,
  value, layer)`, `pressed[pad] = (layer, column)`, `col_pressed[button] = column`; `_button_up` / `_end_clip` end them
  whatever the grid shows now. `release_all()` on MIDI (re)connect and at quit (push2-python drops the first second of
  MIDI after a connect). `_revalidate()` in `set_comp` clamps the grid offsets and the selection to the composition.
  `tests/fuzz_states.py` checks these invariants on random event walks; `tests/test_states.py` holds the reproductions.
- **Deferred saves**: knob ticks and step presses call `Sequencer.save_later()`; `seq_loop` writes `chases.yaml`
  `SAVE_DELAY` (0.2 s) after the last change, outside the lock (`take_dirty()` + `write()`); single actions (`store_pattern`,
  groups, pad mapping) still `save()` at once; quit flushes. `PluginEngine.flush()` (each `seq_loop` tick) sends the
  levels the rate limiter held back, so no flash is ever dropped.
- Parameter slots are recomputed from the polled JSON every frame (`Bridge.slots()`), so
  renames/reorders in Resolume are picked up automatically.

**Grid mapping:** pad row `i` (0 = top) → layer `layer_offset + (8 - i)`; column `j` → clip
`col_offset + j + 1`. Bottom row = lowest layer, like Resolume's UI. Pad colours: layer colour
(8 hues, repeating), dim = loaded, bright = connected, white/grey = selected.

**Parameter paths** (`resolve_node` / `walk`): slash-separated walk through Resolume's JSON.
Dict keys match case-insensitively; list items match by `display_name`/`name`, or by index when
unnamed/duplicated. `walk()` produces paths that `resolve_node()` accepts — keep them in sync.
Editable types: `ParamRange`, `ParamChoice`, `ParamBoolean`. Slot `scope: clip` = the clip last
pressed on that layer. `layers.<n>: auto` fills slots from `AUTO_SOURCES`.

## Verified APIs

**Resolume REST** (`/api/v1`, enable in Preferences → Webserver):
- `GET /composition` — whole state. Layer 1 = `layers[0]`, column 1 = `clips[0]`. Names are `{"value": "..."}`.
- `POST /composition/layers/{L}/clips/{C}/connect` with JSON body `true` (press) / `false` (release).
  The release is required, otherwise re-triggering the same clip fails; also makes Piano clips work.
- `PUT /parameter/by-id/{id}` with `{"value": x}`.
- Clip state: `clip.connected.value` ∈ `Empty, Disconnected, Previewing, Connected, Connected & previewing`.
- WebSocket `ws://host:8080/api/v1` (checked on Arena 7.23): on connect sends the full composition
  (no `type`), then `sources_update`, `effects_update`. `{"action": "subscribe", "parameter":
  "/parameter/by-id/<id>"}` → `parameter_subscribed` then `parameter_update` messages (full param +
  `path`). Subscribing by path returns `"Invalid parameter path"`.
- `python push_resolume_bridge.py --check LAYER` (`resolume_check.py`) tests every call above on a spare
  layer and undoes it. **Arena 7.23.2 result (2026-09-24), all OK:** PUT range / boolean (layer
  `bypassed`, `solo`) / colour `#rrggbbaa` / tempo, event trigger `PUT {"value": true}` (204), clip
  `select`, clip connect true/false, layer `/clear`, WebSocket subscribe by id.
  **ParamChoice: only `{"value": "<option name>"}` works; `{"index": i}` → HTTP 400.**
  Column launch `POST /composition/columns/{n}/connect` true/false works too (`--check-columns`).
- Composition editing (verified on the mock, on Arena via `--check` steps "WebSocket set", "Open source
  into a clip", "Add Crop + display name + delete", "Set string (layer name)"): `POST /composition/layers/add`
  (text body `/composition/layers/N` or empty = on top), `POST …/layers/{L}/effects/video/add` (text
  `effect:///video/Crop`, spaces as `%20` — verified on Arena), `DELETE …/effects/video/{offset}`
  (0-based, verified), `POST …/effects/video/{i}/set-display-name` (text), `POST …/clips/{C}/open`
  (text `source:///video/<name>` or `file:///…`), `POST …/clips/{C}/clear`, WebSocket
  `{"action": "set", "parameter": "/parameter/by-id/<id>", "value": v}`.

**push2-python** (ffont/push2-python):
- Only the **first** registered handler per action is called (`trigger_action` calls `func[0]`).
  Register one generic handler per action type.
- Ignores incoming MIDI for ~1 s after connecting. Display blanks if no frame for 2 s.
- Display frames: numpy uint16 (960×160 transposed). `FRAME_FORMAT_BGR565` is fastest; `render()`
  draws with cairo RGB16_565 and swaps R/B at draw time so no conversion is needed.
- Custom pad colours: `set_color_palette_entry(idx, name, rgb=..., allow_overwrite=True)` then
  `reapply_color_palette()`. We use 64–79 (`L0..L7`, `L0_dim..L7_dim`), 80–87 (`P0..P7` palette
  swatches), 88–95 (`L0_mid..L7_mid` beat pulse). Re-apply after every
  MIDI reconnect (`on_midi_connected` sets `bridge.midi_reset`).
- Button names: `'Up'`, `'Down'`, `'Left'`, `'Right'`, `'Page Left'`, `'Page Right'`, `'Shift'`,
  `'Upper Row 1..8'`, `'Lower Row 1..8'`, `'1/32t'…'1/4'` (scene column), `'Tap Tempo'`, etc.
  Encoders: `'Track1 Encoder'…'Track8 Encoder'`, `'Tempo Encoder'`, `'Swing Encoder'`, `'Master Encoder'`.

## Not yet verified on hardware / known gaps

- Composition JSON shapes (`video/sourceparams`, `transport/controls/speed`, effect `params`) were
  checked against the mock and `--dump`, not every Resolume source/effect type.
- Big ranges (Transform Position X ±16384) are too coarse at 1 %/tick — handled per slot with `step`/`range`.
  `tempo_tap` uses the same event call as `resync` (verified), its effect on BPM is untested.
- Beat phase on the Push comes from the taps / resync (`beat_anchor`); Resolume doesn't expose its phase.
- A new full composition only arrives on WS connect (and maybe on structure changes); the REST
  refresh every `ws_refresh` s catches added/removed clips.
- `stop_column: N` in config.yaml is a fallback for stopping; not needed on 7.23 (`/clear` works).
- Only the active deck is visible through the API.
- Tempo uses `composition/tempocontroller/tempo` (ParamRange 20–500, BPM) — path confirmed in the live
  JSON; `tempo_tap` / `resync` ParamEvents are triggered by Tap / Shift+Tap.
- Arena 7.23.2 sometimes closes an HTTP connection without answering (`RemoteDisconnected`, seen by
  Štefan on the blackout PUT, 2026-09-27). Not reproduced in isolation: idle keep-alive up to 330 s,
  parallel composition GETs, 404s and select-then-PUT all worked. So `Resolume._req` resends a request
  up to twice on `requests.ConnectionError` (never on timeouts), and the blackout / restore is held
  until Resolume reports the master value (`_check_blackout`, `blackout_watchdog` in the main loop;
  resend every `BLACKOUT_RESEND` s, give up after `BLACKOUT_TRIES`). The mock simulates it:
  `POST /api/v1/_drop_puts N`.

## Bar Chaser plugin (plugin/)

FFGL 2.1 effect, universal bundle. 55 params: `Preset` (text), `Reload` (event), `Track` (1–4),
`Master`, `Edge`, `Outside`, `Mode` (Texture / Solid / Show pads), `Pad 1..24` (option, elements from the
Advanced Output preset via `SetParamElements`), `Level 1..24`. Hosts reset every param to its declared
default after creation, so defaults must carry the initial pad assignment. `plugin/tests/host_test.cpp`
is a tiny CGL host: loads the bundle, feeds a red/green picture, checks pass-through, masking, solid,
show pads. Build needs Xcode CLT + `brew install cmake`. Verified on Arena 7.23.2 (2026-09-27): loads
from `~/Documents/Resolume Arena/Extra Effects`, preset dropdowns fill (pads pre-assigned), Texture /
Solid / Show pads all render on a layer. Arena's log (`~/Library/Logs/Resolume Arena/Resolume Arena
log.txt`) shows the plugin's `LogToHost` lines; a layer input texture there is 1920×1080 in a
1920×1088 RGBA8 texture, linear filters, no sampler object, host FBO 1. Pitfalls met: a `Transform`
effect *after* Bar Chaser (or on the layer when the effect is on the clip) moves the bars off the
slices — keep Bar Chaser last on the layer; `SetOptionParamInfo` appends a parameter, never call it
twice for one index (set `FindParamInfo(i)->defaultFloatVal` instead).

## Testing workflow

Always run before handing changes back:
```
python tests/mock_resolume.py &     # listens on 127.0.0.1:18080 (MOCK_PORT), never 8080: see below
python tests/test_sequencer.py      # pure logic, no mock needed
python tests/test_states.py         # stuck-state regressions + random walk, no mock needed
python tests/test_helpers.py        # JSON helpers, render per screen, retry / outbox, no mock needed
python tests/test_banks.py          # presets: rigs × recipes, no mock needed
plugin/build.sh                     # C++: builds the bundle and runs ctest (preset parser + GL host test)
python tests/test_engine.py         # restart the mock before each script: they change its composition
python tests/test_fake_push.py      # must print OK (runs on the mock's WebSocket)
TEST_POLL=1 python tests/test_fake_push.py   # same with the WebSocket off (polling fallback)
python tests/render_preview.py      # then look at tests/preview_*.png for display changes
python -c "import ast; ast.parse(open('push_resolume_bridge.py').read(), feature_version=(3,9))"
```
Extend `tests/mock_resolume.py` when touching new parts of the JSON. The test scripts refuse to run
unless `GET /product` says "Mock Resolume". **The mock and the tests use port 18080** (`MOCK_PORT` overrides): a mock
on 127.0.0.1:8080 would answer every loopback client of a running Arena, including a live bridge. Final check is always on the
real Push + Arena, done by Štefan.

## Backlog (ideas, not commitments)

Current numbered list (F1…F17, Štefan picks by ID): **`docs/ideas.md`**. Mark Status there when done.
Older notes:

1. WebSocket subscriptions instead of polling (lower latency, less load).
2. Column/scene launch on the 8 buttons right of the pads (`1/32t…1/4`).
3. ~~Tap Tempo / BPM nudge~~ — done (B_5, K10). Next: resync beat phase on tap.
4. Clip thumbnails on the display (REST provides them); Resolume clip colours on pads.
5. ~~Stop / clear layer~~ — done (Record + pad).
6. Touchstrip → composition master or crossfader.
7. Upper/Lower Row buttons: layer bypass/solo, effect bypass toggles.
8. Pad blink on BPM (send MIDI clock so Push animations sync to Resolume tempo).
9. Deck switching.
10. ~~Publish on GitHub~~ — public at github.com/Mastanka/Resolume_Push2 (MIT). `main` protected
    by ruleset (no deletion / force push). Only Mastanka has write access.
