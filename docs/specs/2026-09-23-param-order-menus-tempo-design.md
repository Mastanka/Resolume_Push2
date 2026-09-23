# Param order, menu rows, tempo — design

## 1. Moving params (Convert = B_4)

- Hold **Convert** + touch knob K1–K8 → that slot becomes the *source*. The column is highlighted,
  the bottom line shows **SELECT NEW POSITION**, Convert is lit. Convert may be released.
- Change page (BD1–BD8, Page < / >), touch the target knob → source and target **swap**.
- Touching the source knob again or pressing Convert again cancels.
- While moving, encoder turns do not change values.
- Only for `auto` layers. Layers with an explicit list in `config.yaml` ignore it.

**Keys.** A slot's key = its path without the `layer:`/`clip:` scope, e.g.
`video/effects/Transform/params/Scale`, `video/sourceparams/Frequency`, `video/opacity`.
So the order applies per effect / source param type, on every clip and layer.

**Storage.** `pins.yaml` holds one ordered list `order: [key, ...]` (priority list).
Sorting auto slots: keys in `order` first (by their index), then the rest in natural order (stable).

**Swap.** With the current sorted key list `K` and absolute positions `a`, `b`:
swap `K[a]`, `K[b]`; `P = K[:max(a, b) + 1]`; `order = P + [k for k in order if k not in P]`.
Only the positions up to the moved ones are stored; everything after keeps its natural order,
and the current view changes only by the swap. Saved immediately.

## 2. Menu rows

- **BU1–BU8** = main menus. BU1 = PARAMS (lit when active). B_3 (Mix) still toggles MIX; BU1
  returns to PARAMS.
- **BD1–BD8** = sub-menu of the current menu. In PARAMS: jump to page 1–8. Current page white,
  existing pages dim, others off. Off in MIX.

## 3. Tempo

- Resolume `composition/tempocontroller/tempo` (ParamRange, BPM).
- **B_5 (Tap Tempo):** local tap detection. Taps more than 2 s apart restart. From the 2nd tap,
  BPM = 60 / mean of the last ≤ 7 intervals, sent as the tempo value. Button flashes per tap.
  Beat phase is not resynced (possible later: trigger `resync`).
- **K10 (Tempo Encoder):** ±1 BPM per tick, Shift ±0.1, clamped to the param's min/max.
- BPM shown bottom-right on both views.

## 4. Tests

Mock: `tempocontroller/tempo`, a second clip on another layer that also has Transform.
Fake Push: swap across pages, the order carries over to the other clip, `pins.yaml` written,
BD page jump, tap → BPM, K10 nudge. Preview PNG of move mode.
