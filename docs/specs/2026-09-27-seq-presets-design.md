# SEQ presets: patterns that fit any bar layout

Design from a Claude chat with Štefan (handover, 2026-09-27), changed by his answers the same day. The rig and recipe code is `rig.py` and `banks.py`; tests in
`tests/test_banks.py`, `tests/test_sequencer.py`, `tests/test_engine.py`, `tests/test_fake_push.py`.

## Goal

1. A **preset** = a recipe in role space: "kick on a long bar, rumble on the short bars beside it",
   not "kick on pad 5". The Techno bank has 16.
2. **Using a preset** realises its recipe on the current rig and stores the result as a normal
   pattern (steps keyed `pad k`) in **one pattern slot the user picks**. The sequencer and the engine
   play it like any other pattern. (Changed: the handover loaded a whole bank into P1–P16.)
3. Any rig from **4 to 24 pads** works, with any mix of long and short bars.
4. The rig only changes **which pads** a hit lands on. Steps, envelopes, gate, levels and swing come
   from the recipe.
5. Every use draws a new random seed; a pattern keeps its seed, so a re-fit is deterministic.

## Terms

- **Pad**: Bar Chaser pad 1–24 (`pad k` in `chases.yaml`). An assigned pad is one physical bar or slice.
- **Rig**: the assigned pads in physical order (left → right), each with a role.
- **D (dominant)**: the long bars. They carry kick, downbeats and big hits.
- **S (secondary)**: the short bars. They carry hats, rides, rumble, sparkle.
- **Uniform rig**: all bars about the same length. Roles are then chosen by position (virtual D).
- **Recipe**: code that places hits using the pools below. **Realise**: run the recipe on a rig.
- **Re-fit**: realise again, same seed, on a changed rig; the user's knob settings are kept.

## 1. Rig detection (`rig.py`)

Input: the 24 pad values of the **reference track**, the preset rectangles, and an optional
config override.

- **Pad mapping**: one mapping shared by every track (Štefan, 2026-09-27: "all tracks share the
  same mapping"). `PluginEngine.mapping()`: T1's first Bar Chaser, else the first one, else the
  remembered `Sequencer.pad_config`. Steps are keyed by pad, so all tracks share one placement.
- **Pads used**: every pad whose value isn't `—`.
- **Order**: by rectangle centre x, then y, when every used pad has a rectangle. Otherwise by pad
  number (Bar Chaser's default is pad k = k-th fixture in Arena's list).
- **A pad is one DMX fixture** (one bar), never a whole lumiverse: one lumiverse can hold several
  fixtures. The effect's dropdown still lists whole lumiverses after the fixtures, only so older
  shows keep loading.
- **Roles**, first rule that applies:
  1. `config.yaml → sequencer.rig.dominant: [pad numbers]` (explicit).
  2. Preset lengths (longer side of each rectangle). Sort them; if longest / shortest < **1.3**,
     the rig is uniform; otherwise split at the biggest ratio jump between neighbouring
     lengths, and the longer side is D. Three sizes (0.5 m, 1 m, 2 m) split at the biggest jump.
  3. No geometry, or uniform, or an override that marks every pad the same: **virtual split**.
- **Virtual split**: k D pads, k nearest to N/3, same parity as N so the set can mirror around
  the centre, at most N/2; ties go to the smaller k. Positions: `p = floor((i + 0.5) · N / k)` for
  i < k/2, each mirrored to `N − 1 − p`, plus the centre when k is odd. Result for 4–24 alike bars
  (D = dominant, s = secondary, left → right):

  ```
   4 sDDs                     12 sDssDssDssDs              20 sDsssDssDssDssDsssDs
   5 ssDss                    13 sDsDssDssDsDs             21 sDssDssDssDssDssDssDs
   6 sDssDs                   14 sDsssDssDsssDs            22 sDssDsDssDssDssDsDssDs
   7 sDsDsDs                  15 sDssDssDssDssDs           23 sDssDsssDssDssDsssDssDs
   8 ssDssDss                 16 sDssDsDssDsDssDs          24 sDssDssDssDssDssDssDssDs
   9 sDssDssDs                17 sDsssDssDssDsssDs
  10 sDsDssDsDs               18 sDssDssDssDssDssDs
  11 sDsssDsssDs              19 sDssDsDssDssDsDssDs
  ```
- **Limits**: fewer than 4 or more than 24 used pads → `RigError("SEQ banks need 4–24 pads, found
  N")`, shown as a display message; nothing is loaded.
- **Signature**: pads and roles in order, e.g. `1S 2D 3S 4S 5D 6S 7S 8D 9S`, with ` v` appended
  for a virtual split. Stored with the bank; a different signature means the rig changed.
- **Geometry**: `rig.preset_rects()` reads the Advanced Output preset the effect reads (its `Preset`
  parameter, resolved like the plugin: empty = newest `.xml`) from `sequencer.preset_folder`
  (default Resolume's own folder; tests point it at `tests/fixtures/rig`). It offers exactly the
  plugin's names: every fixture as `Screen / slice` in Arena's order, then every whole screen
  (`tests/test_banks.py` checks this against the fixture `plugin/tests/test_preset.cpp` uses).
- **Override** in `config.yaml` (optional; `DEFAULT_CONFIG["sequencer"]["rig"] = {}`):

  ```yaml
  sequencer:
    tracks: 4
    rig:
      dominant: [2, 5, 8]      # pads that play D; leave out to detect from the preset
      order: [1, 2, 3, 4]      # pads left → right, only when the preset can't tell
  ```

`Bridge.current_rig()` caches the rig by (mapping, preset path, preset mtime, override) and looks at
the preset file at most once a second, or at once when the mapping changes.

## 2. Placement vocabulary

Every pool is a list of pads in physical order. The examples are Štefan's current rig,
S L S S L S S L S = pads 1–9.

| Name | Meaning | Current rig | Small / odd rigs |
|---|---|---|---|
| `ALL` | every used pad | 1–9 | |
| `D`, `S` | dominant / secondary pads | 2 5 8 / 1 3 4 6 7 9 | never empty (virtual split) |
| `sat(d)` | the S pad directly left and right of d | sat(5) = 4 6 | both neighbours D or the edge → the nearest S anywhere, ties to the left |
| `pairs` | S pads mirrored by rank, outside → in | [1 9] [3 7] [4 6] | odd count: the middle pad is a pair of one. Rank-based, so asymmetric rigs still pair |
| `outer_d` | first and last D | 2 8 | one D → just that pad |
| `s_high`, `s_mid` | pads of the outer / inner half of `pairs` | 1 3 7 9 / 3 4 6 7 | odd pair count → the middle pair is in both; one pair → both are that pair |
| `few(pool)` | how many pads a "single" hit lights | 1 | 1 for pools up to 11 pads, 2 for 12–19, 3 for 20–24 |
| `share(pool, f)` | round(f × pool size), at least 1, at most the pool | share(S, 2/3) = 4 | |
| `kick_n` | pads a hopping kick lights | 1 | 1 while the rig has ≤ 4 D, then share(D, 1/3) |
| `hop(pool, n, stream)` | n random pads, never the previous pick of the same stream (n = 1: not the same pad; n > 1: not the same set), optional `avoid` | | too small a pool drops "not previous" first, then `avoid` |
| `pair_pick` | random mirrored pair(s), not the same as last time | | round(pairs / 3) pairs at once, at least 1 |

Why these rules: on bigger rigs a single pad per hit gets lost, so `few` and `share` grow with
the pool; on small rigs "never the same pad twice" can't be honoured, so it quietly gives way. A
stream is one musical voice inside one recipe ("kick", "hats", "dub"), so the kick's last pad
doesn't restrict the hats.

## 3. The Techno bank (16 recipes)

`banks.py` is authoritative; this table is the summary. Step numbers are 0–31 at grid 1/16:
beats = 0 4 … 28, offbeats = 2 6 … 30, "non-beat" = every step that isn't a beat, "a" = 3 7 … 31.
Tracks: T1 kick, T2 hats, T3 accent, T4 movement. On the Push, left → right in each bank:
minimal, groove, peak, kick out (for when the DJ pulls the kick).

| P | Recipe | Swing | Track: steps → placement |
|---|---|---|---|
| 1 | R1 Pump | 0.1 | T1 beats → hop(D, kick_n). T4 rumble beat+1 → sat() of the pads the kick just lit. T2 3 9 14 19 25 30 → hop(S, few) |
| 2 | R2 3/16 | 0.1 | T1 + T4 as R1. T2 every 3rd step from 2 → hop(S, few), louder when it lands on an offbeat |
| 3 | R3 Dub | 0.1 | T1 + T4 as R1. T2 non-beat 16ths → hop(S, few). T3 dub throws 6 9 12 15 and 22 25 28 31 → hop(ALL, few), levels 1 / .6 / .35 / .2 |
| 4 | R4 No kick | 0.1 | T2 as R2. T4 glow 0 8 16 24 → hop(D, kick_n). T3 dub throw 14 17 20 23 |
| 5 | D1 909 | 0.3 | T1 downbeats 0 16 → all D, other beats → hop(D, kick_n). T2 open hat on offbeats → pair_pick. T3 clap 4 12 20 28 → outer_d |
| 6 | D2 Shuffle | 0.3 | T1 as D1. T2 non-beat 16ths → hop(S, few) with 909 accents (offbeat loud). T3 claps + ghost at 31 → hop(S, 1) |
| 7 | D3 Stabs | 0.3 | As D2, plus T4 stabs 3 6 14 19 22 26 29 → hop(ALL, share 1/3) |
| 8 | D4 No kick | 0.3 | T2 hats, T3 claps as D2. T4 toms 3 6 10 19 22 26 28 29 30 31: low → hop(D), mid → hop(s_mid), high → hop(s_high) |
| 9 | G1 Ride | 0.2 | T1 beats → all D. T2 ride on offbeats → hop(S, share 2/3); "a" steps → hop(S, few) at 0.3 |
| 10 | G2 Tribal | 0.2 | T1 beats → hop(D, kick_n). T3 toms on rumba clave (0 3 7 10 12 / 18 20 24 27 31): low → hop(D) avoiding the kick's pads, high → hop(S). T2 ride → hop(S, share 1/3) |
| 11 | G3 Rolling | 0.2 | T1 beats → all D. T4 rolling bass on non-beat 16ths → all D. T2 ride → hop(S, share 1/2). T3 clave toms → hop(S) |
| 12 | G4 No kick | 0.2 | T2 non-beat hats → hop(S, few). T3 clave toms → hop(ALL), roll 28–31 → hop(ALL) |
| 13 | W1 Stomp | 0 | T1 beats → ALL minus a hole of share(ALL, 2/9) pads (1 … N−1), a new hole every beat. T2 offbeats → hop(ALL, few) at 0.35 |
| 14 | W2 Bounce | 0 | T1 beats → all D. T2 offbeats → all S, except one random offbeat per bar → pair_pick. T3 two random "a" steps per bar → hop(ALL, few) |
| 15 | W3 Scatter | 0 | T1 beats → all D. T2 non-beat 16ths → hop(ALL, few), level 0.5–1 random |
| 16 | W4 Tension | 0 | T2 bar 1: 5 random non-beat steps → hop(ALL, few); bar 2 roll 16 20 24 26 28 29 30 31 → hop(ALL, share n/9) with n = 1 1 1 2 2 3 3 4, rising. T4 16–31 → all D, swelling |

All patterns: length 32, direction forward, texture null. Envelopes, gates and levels per track are
in `banks.py` (`ENV` and the `P.track(...)` calls). They were tuned for 125–160 BPM and don't
scale with the rig.

What changes on other rigs, for example:
- **4 alike bars** (`sDDs`): the kick hops between the two middle bars, the rumble sits on the
  outer bar beside it, the hats alternate between the two outer bars.
- **12 alike bars**: 4 virtual D; the kick still lights one pad per beat, the ride lights 5 of 8
  S pads, a hat hit lights 1 pad (the S pool has 8).
- **24 pads with 8 long**: the kick lights 3 of 8 D per beat, a scatter hit lights 3 pads, a W1
  hole is 5 pads wide.
- **2 long on the left, 2 short on the right** (`D D S S`): both rumbles fall on pad 3, the nearest
  S; claps sit on pads 1 and 2.

## 4. Storage and edit rules

- `Pattern.source = {bank, recipe, seed, rig}` (rig = the signature the placement was made for);
  written to `chases.yaml` only when set. No bank-wide block.
- **Placement belongs to the preset, knobs belong to the user.** Step edits clear `source`
  (`Sequencer.mark_edited()`: `toggle_step`, `set_step_values`, `clear_steps`, `double_loop`, and
  the bridge's own step toggles and Delete + step). Knob edits keep it: envelope, gate, level,
  direction, length, swing. `copy_pattern` keeps it; `clear_pattern` makes a fresh pattern.
- **Store** (`Sequencer.store_pattern(slot, dict)`): replaces that slot only; groups, track groups
  and the pad mapping stay. Sets the grid to 1/16 (the recipes are written for it). No backup: a
  preset only creates a pattern (Štefan).
- **Re-fit** (`Sequencer.refit(signature, fn)` with `banks.refit`): every preset pattern whose
  `source.rig` differs from the current rig gets new steps; name, length, direction, swing and each
  track's envelope, gate and level stay. Own and hand-edited patterns are never touched.

## 5. The check: do the patterns still fit the Advanced Output?

No command line (Štefan: not user-friendly). The bridge checks after every composition update and
after every MAPPING change (`Bridge.check_rig()`):

- **Composition loaded** (the bridge starts, or another composition opens): preset patterns made for
  another rig are re-fitted at once: `Rig changed: 3 preset patterns re-fitted`.
- **Rig changed during the show** (MAPPING, a new Advanced Output preset): not re-fitted on its own,
  because a re-fit moves every hit. The display says `Rig changed · re-fit in PRESETS` once, and the
  PRESETS menu asks `Rig changed: re-fit 3 preset patterns to it?` with NO / YES.
- In PRESETS, pattern pads made for another rig light **pink** (the pattern row itself is orange), and the rig line lists them.

## 6. Push: PRESETS menu (SEQ, button 3 above the display)

The buttons below the display are the **presets**, not the tracks (a preset writes all four tracks):

| Control | Action |
|---|---|
| Button 1–8 below the display | Pick preset 1–8 (Shift: 9–16). It blinks, and so does the whole pattern row. Press it again to cancel |
| Pattern pad (row 5, Shift = 9–16) while a preset is picked | Empty slot: store the preset there (the pad blinks twice). Used slot: the display asks `Overwrite P3 (R1 Pump) with D2 Shuffle?`, buttons 7 = NO, 8 = YES. After NO the preset stays picked, so another slot can be chosen |
| Pattern pad, nothing picked | Switch pattern as usual |

Display: the question or the next step on the first line, then the rig line (`Rig: 9 fixtures ·
3 long, 6 short · long / short from the Advanced Output`, or why presets can't be used, e.g. `SEQ
banks need 4–24 assigned pads, found 3`), then the current pattern (`P3 (R1 Pump) · preset R1 · seed
4711` or `own pattern`) and tracks that have no Bar Chaser layer. The bottom strip labels the eight
buttons (preset names, or NO / YES). Button lights: dim = a preset, blinking = picked, off = presets
can't be used (fewer than 4 or more than 24 mapped pads); NO red, YES green.

Tracks: the Techno bank uses all four (T1 kick, T2 hats, T3 accents, T4 movement); a track plays
only where a layer carries Bar Chaser on it (Štefan runs four layers).

## 7. Pad mapping is shared

All tracks share one mapping (`Sequencer.pad_config`, `PluginEngine.sync_pads` / `set_pad`):
MAPPING writes every Bar Chaser; a pad changed in Arena on any layer becomes the mapping for all; a
new Bar Chaser gets it; a loaded composition takes T1's (else the first instance's) and gives it
to the others. Older `chases.yaml` files with one mapping per track load T1's.

## Later

- Real randomness on every loop: a per-step chance, or a "random pad from pool" step resolved at
  playback. Today a pattern repeats every 2 bars (32 steps), and Reshuffle only helps between loops.
- More banks, and user-written recipes as YAML (the vocabulary in section 2 is the natural format).
- Separate placements per track when tracks use different pad assignments.
- Rigs laid out in several rows or in a circle (position is by x today; `order` overrides it).
