# Two views, per-view menus, click / hold screens

Requested by Štefan, 2026-09-27. Replaces the flat menu row (BU1 PARAMS, BU2 COLOR, BU3 FX, BU4 SEQ).

## Views

| View | Button | Pads | Menus above the display (LED colour) |
|---|---|---|---|
| CLIP | Session | Clip grid | 1 CLIP PARAMS · 2 CLIP COLOR · 3 CLIP EFFECTS · 4, 5 empty · 6 LAYER PARAMS · 7 LAYER EFFECTS · 8 empty (white / dim grey) |
| SEQUENCER | Note | Step sequencer | 1 ENVELOPE · 2 SETTINGS · 3 PRESETS · 4 MAPPING (red / dim red; red = palette `L6`, the 7th layer colour) |

Each view remembers its last menu (Session → back to the CLIP menu you left, Note → the SEQ menu).

- **CLIP PARAMS** = auto slots of scope `clip` (source params, clip effect params, transport).
  **LAYER PARAMS** = scope `layer` (layer opacity, layer effect params). Config slots go by their
  `scope` (default `layer`). Convert-move works in both; `pins.yaml` stays one priority list of
  scope-less keys. The parameter page is remembered per menu.
- **CLIP EFFECTS / LAYER EFFECTS** = the old FX menu, restricted to the clip's / the layer's effects.
  Composition effects are no longer listed (the Master button still edits the composition colour).
- **ENVELOPE** knobs: Attack, Decay, Sustain, Release, Gate. **SETTINGS**: Direction, Length, Level.
  Hold a step + Gate / Level knob = that step's gate / level. **PRESETS**: see
  `2026-09-27-seq-presets-design.md` (buttons below the display = presets, not tracks).
- **MAPPING** (added 2026-09-27): which DMX fixture each of the 24 pads lights; one mapping for all tracks.
  Pads: rows 1–4 = the fixtures the Bar Chaser offers, left → right, top → bottom, labelled L#F#
  (lumiverse # in Arena's order, fixture # inside it): dim white = free, dim track colour = used by a
  pad, bright = used by the pad last pressed, blinking white = picked. Row 5 = orange (the middle row is orange in every SEQ menu).
  Rows 6–8 = pads 1–24: dim track colour = has a fixture, off = none. **Select + fixture** = pick it;
  then **a pad** = store (the fixture pad and the pad blink twice, fast). Pressing the picked fixture
  again cancels. **Delete + pad** = no fixture. A plain pad press selects and flashes the pad and shows
  its fixture. **Octave ▲ ▼** = next / previous 32 fixtures. Writes go to every Bar Chaser (all tracks)
  and into the remembered mapping.

## MIX / MUTE / SOLO screens

Mix, Mute and Solo open a screen on top of either view (display, knobs, buttons below the display;
the pads keep showing the view).

- **Click** (released within 0.4 s, nothing else touched) → the screen stays; the button is lit.
  Click the same button again → the previous screen (a latched screen underneath comes back, e.g.
  MIX → click MUTE → click MUTE = MIX again).
- **Hold** (longer, or a pad / knob / button used meanwhile) → back on release.
- MIX: knobs = layer masters, Master knob = composition master, buttons below = mute (Solo held =
  solo). MUTE: buttons below = mute. SOLO: buttons below = solo.
- Hold Mute / Solo + pad = mute / solo that pad's layer, as before.
- A menu button, Session, Note or Master closes the screen.
