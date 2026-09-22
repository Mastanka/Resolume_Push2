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
| `push_resolume_bridge.py` | Whole app, single file (~750 lines) |
| `config.yaml` | Resolume host/port, grid offsets, encoder steps, per-layer parameter slots |
| `requirements.txt` | push2-python (from git), pycairo, numpy, requests, PyYAML |
| `README.md` | User-facing setup and controls |
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
| K1–K8 | `Track1 Encoder`…`Track8 Encoder` | Param slots / layer masters in MIX |
| K9 | `Swing Encoder` | – |
| K10 | `Tempo Encoder` | – |
| K11 | `Master Encoder` | Selected layer opacity / composition master in MIX |
| BU1–BU8 | `Upper Row 1..8` (above display) | – |
| BD1–BD8 | `Lower Row 1..8` (below display) | – |
| B_1 | `Play` (bottom-left) | – |
| B_2 | `Record` (above B_1) | – |
| B_3 | `Mix` (right of display) | MIX menu toggle |

## Modes

- **params** (default): K1–K8 = parameter slots of the selected layer/clip.
- **mix** (B_3 toggles, B_3 lit white): K1–K8 = `layer.master` (fallback `video/opacity`),
  K1 = top visible layer, going down; K11 = `composition.master`. Display shows layer names +
  values above, composition master bar centred below the line.

## Architecture

Four threads around one `Bridge` object guarded by `Bridge.lock` (RLock):

```
push2-python MIDI thread ──► Bridge.pad_pressed / turn / button   (mutate state only, no MIDI out)
poll thread               ──► GET /composition every 0.25 s → Bridge.comp
Sender thread             ──► clip triggers (ordered queue) + param PUTs (coalesced, latest wins)
main thread (run loop)    ──► pad LEDs (diffed vs cache) + display frame at 20 fps
```

- **All MIDI output (pad/button colours, palette) happens in the main thread** — push2-python/mido
  requirement. Callbacks only change state.
- `Bridge.overrides` holds values we just sent for 0.8 s so the display doesn't jump back to stale
  polled values while an encoder is turning.
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
- WebSocket at `ws://host:8080/api/v1` supports subscriptions — **not used yet** (see backlog).

**push2-python** (ffont/push2-python):
- Only the **first** registered handler per action is called (`trigger_action` calls `func[0]`).
  Register one generic handler per action type.
- Ignores incoming MIDI for ~1 s after connecting. Display blanks if no frame for 2 s.
- Display frames: numpy uint16 (960×160 transposed). `FRAME_FORMAT_BGR565` is fastest; `render()`
  draws with cairo RGB16_565 and swaps R/B at draw time so no conversion is needed.
- Custom pad colours: `set_color_palette_entry(idx, name, rgb=..., allow_overwrite=True)` then
  `reapply_color_palette()`. We use indices 64–79 (`L0..L7`, `L0_dim..L7_dim`). Re-apply after every
  MIDI reconnect (`on_midi_connected` sets `bridge.midi_reset`).
- Button names: `'Up'`, `'Down'`, `'Left'`, `'Right'`, `'Page Left'`, `'Page Right'`, `'Shift'`,
  `'Upper Row 1..8'`, `'Lower Row 1..8'`, `'1/32t'…'1/4'` (scene column), `'Tap Tempo'`, etc.
  Encoders: `'Track1 Encoder'…'Track8 Encoder'`, `'Tempo Encoder'`, `'Swing Encoder'`, `'Master Encoder'`.

## Not yet verified on hardware / known gaps

- **ParamChoice** writes send `{"index": i, "value": name}` — unconfirmed which field Arena honours.
- Composition JSON shapes (`video/sourceparams`, `transport/controls/speed`, effect `params`) were
  checked against the mock and `--dump`, not every Resolume source/effect type.
- Big ranges (Transform Position X ±16384) are too coarse at 1 %/tick — handled per slot with `step`/`range`.
- Polling fetches the full composition 4×/s; fine now, may be heavy with large decks.
- Empty pads only select; they don't stop/clear the layer.
- Only the active deck is visible through the API.

## Testing workflow

Always run before handing changes back:
```
python tests/mock_resolume.py &
python tests/test_fake_push.py      # must print OK
python tests/render_preview.py      # then look at tests/preview_*.png for display changes
python -c "import ast; ast.parse(open('push_resolume_bridge.py').read(), feature_version=(3,9))"
```
Extend `tests/mock_resolume.py` when touching new parts of the JSON. Final check is always on the
real Push + Arena, done by Štefan.

## Backlog (ideas, not commitments)

1. WebSocket subscriptions instead of polling (lower latency, less load).
2. Column/scene launch on the 8 buttons right of the pads (`1/32t…1/4`).
3. Tap Tempo button → Resolume tempo; Tempo encoder → BPM nudge.
4. Clip thumbnails on the display (REST provides them); Resolume clip colours on pads.
5. Stop / clear layer (e.g. Delete + pad, or Mute row).
6. Touchstrip → composition master or crossfader.
7. Upper/Lower Row buttons: layer bypass/solo, effect bypass toggles.
8. Pad blink on BPM (send MIDI clock so Push animations sync to Resolume tempo).
9. Deck switching.
10. Package and publish on GitHub (no comparable public project exists).
