# Push 2 → Resolume Arena bridge

Turns an Ableton Push 2 into a Resolume clip launcher with a live parameter display.

| Control | Does |
|---|---|
| Pads | 8×8 clip grid, bottom row = lowest layer. Press = trigger, release = release (Piano clips work). Pressing also selects the layer/clip for the display. |
| Encoders 1–8 | Edit the 8 parameters shown on the display. Hold **Shift** for fine steps. Touch an encoder to see its full path. |
| Master encoder | Opacity of the selected layer |
| Mix | **MIX** on/off: encoders 1–8 = layer masters (1 = top visible layer), Master encoder = composition master |
| ▲ ▼ ◀ ▶ | Scroll layers / columns (Shift = jump 8) |
| Page < / > | Next page of parameters (if a layer has more than 8) |

Pad colours: dim = clip loaded, bright = playing, white/grey = selected. Each layer row has its own colour.

## Setup

1. **Resolume:** Preferences → Webserver → *Enable Webserver & REST API* (port 8080).
2. **Quit Ableton Live** — it takes over the Push.
3. **libusb** (needed for the display): macOS `brew install libusb`. On Windows, see the push2-python README if the display stays blank.
4. Install and run:
   ```
   pip install -r requirements.txt
   python push_resolume_bridge.py
   ```
   No Push to hand? `--sim` opens a browser simulator at http://localhost:6128.

## Choosing what the encoders control

Out of the box every layer is `auto`: the slots fill with the layer opacity plus the selected clip's generator/effect parameters.

To pick your own, list the available paths for a layer:
```
python push_resolume_bridge.py --dump 1
```
and copy them into `config.yaml`:
```yaml
layers:
  default: auto
  1:
    - {label: Opacity, path: video/opacity}
    - {label: Rate,    path: video/sourceparams/Frequency, scope: clip, range: [0, 0.6]}
```
`scope: clip` means "the clip I last pressed on this layer". `range` limits the knob, `step` sets its resolution — useful for big ranges like Transform Position X.

## Notes

- Resolume's own MIDI mappings (Launch Control XL etc.) keep working alongside this.
- State is polled 4× per second; the display shows your own turns immediately.
- Choice parameters (e.g. blend mode) are sent as index + name. If one doesn't change in your Arena version, tell me which and I'll adjust it.
