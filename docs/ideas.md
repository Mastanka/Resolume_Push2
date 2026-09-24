# Feature ideas

IDs are stable: refer to features by them (e.g. "do F2 and F4"). Effort: S ≈ an hour,
M ≈ a session, L = more than one session.

| ID | Feature | What it does | Effort | Status |
|---|---|---|---|---|
| F1 | `--check` mode | Tries each Resolume API call on a spare test layer and prints OK / FAIL | S | done |
| F2 | Blackout | One button: composition master to 0, press again to restore | S | done |
| F3 | Flash / bump | Hold the button right of a pad row: that layer at 100 % until released | S | done |
| F4 | Column launch | Hold Play + button below the display = launch the column above it | S | done |
| F5 | Mute / solo in MIX | Buttons below the display: bypass / solo the layer under each knob | S | done |
| F6 | Pad pressure | Hit harder = brighter launch; held pressure controls the layer master | M | |
| F7 | Touch strip | Crossfader, global speed or composition master | S–M | |
| F8 | Resolume native tap + resync | Tap also aligns the beat phase, not only the BPM | S | done |
| F9 | Beat blink | Pads / Tap button blink on the beat | M | done |
| F10 | Master colour | One colour control for the whole show (colour effect on the composition) | M | done |
| F11 | Save own colours | Shift + palette button stores the current colour there | S | done |
| F12 | Colour many clips | Apply a colour to a whole column or layer at once | M | done |
| F13 | FX menu (BU3) | Clip + layer effects list, on/off on the buttons below the display | M | done |
| F14 | Thumbnails + clip colours | Clip thumbnails on the display, Resolume clip colours on the pads | M | |
| F15 | Deck switching | Switch Resolume decks from the Push | M | |
| F16 | Live updates (WebSocket) | Resolume pushes changes instead of polling 4×/s: faster, lighter | L | done |
| F17 | Autostart + installer | Start with the Mac; simple install script | S–M | |
| F18 | Step sequencer (SEQ) | Drum-rack style chaser for the LED bars with ADSR, patterns, per-bar layers engine — see `docs/specs/2026-09-24-step-sequencer-design.md` | L | spec approved |

Placement of the done features: see the controls table in README.md.
