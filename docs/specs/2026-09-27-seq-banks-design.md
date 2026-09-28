# SEQ banks: patterns that fit any bar layout (handover)

Written 2026-09-27 from a Claude chat with Štefan, for the Claude Code agent that implements it.
Nothing below is in the repo yet. Proposals that need Štefan's OK are marked **(confirm)**.

## Files in this handover

| File | Put it at | What it is |
|---|---|---|
| this file | `docs/specs/2026-09-27-seq-banks-design.md` | The design |
| `reference/rig.py` | `rig.py` | Working reference: preset geometry → pad order → D/S roles → derived pools |
| `reference/banks.py` | `banks.py` | Working reference: the 16 recipes, `realize()`, `realize_bank()`, `refit()` |
| `reference/test_banks.py` | `tests/test_banks.py` | Plain-script tests (prints `OK`), same style as `tests/test_sequencer.py` |

The reference code is pure Python 3.9 and passes its tests: 110 rigs (4–24 pads, uniform and
mixed) × 16 recipes, plus playback through the real `Sequencer.tick()`. Treat it as the starting
implementation, not pseudo-code. Wiring it into the sequencer, the bridge and the Push is the job.

Background: the Techno bank was first made for Štefan's current rig (3 long + 6 short bars, S L S S
L S S L S) as a fixed `techno_banks.yaml` with `merge_banks.py`. That file only fits that one rig.
This spec replaces it with recipes that are realised onto whatever rig is connected.

## Goal

1. A bank = 16 patterns stored as **recipes in role space**: "kick on a dominant bar, rumble on
   the secondary bars beside it", not "kick on pad 5".
2. **Loading** a bank realises the recipes onto the current rig and writes normal patterns
   (steps keyed `pad k`) into P1–P16. The sequencer and the engine stay as they are.
3. Any rig from **4 to 24 pads** works, with any mix of dominant and secondary bars, including a
   rig of only long or only short bars.
4. The rig only changes **which pads** a hit lands on. Timing (steps), envelopes, gate, levels
   and swing come from the recipe and never depend on the rig.
5. Same recipe + same rig + same seed gives the same pattern. Reshuffle = a new seed.

## Terms

- **Pad**: Bar Chaser pad 1–24 (`pad k` in `chases.yaml`). An assigned pad is one physical bar or slice.
- **Rig**: the assigned pads in physical order (left → right), each with a role.
- **D (dominant)**: the long bars. They carry kick, downbeats and big hits.
- **S (secondary)**: the short bars. They carry hats, rides, rumble, sparkle.
- **Uniform rig**: all bars about the same length. Roles are then chosen by position (virtual D).
- **Recipe**: code that places hits using the pools below. **Realise**: run the recipe on a rig.
- **Re-fit**: realise again, same seed, on a changed rig; the user's knob settings are kept.

## What the current code gives us

- `sequencer.py`: `Pattern(name, length, direction, swing, tracks)`, `Track(texture, envelope,
  gate, level, steps)`, `steps = {"pad k": {step: [level, gate|None]}}`. 16 patterns,
  `MAX_STEPS = 32`. `Sequencer.grid` is global (not stored per pattern), default `"1/16"`.
  `save()` rewrites `chases.yaml` on every edit. `_pattern_from_dict` drops keys it doesn't know,
  so a new `source` field needs adding to the dataclass and both dict functions.
- Direction `random` scrambles the step order of all four tracks together, which would throw the
  kick off the beat. The recipes use `forward` and put the randomness into pad placement instead.
- `chaser_engine.py`: `PluginEngine.pads_of(inst)` gives the 24 `Pad n` values of an instance
  (`—` = unassigned); `pad_name(track, k)`; pad memory in `Sequencer.pad_configs[track]`.
- Pad geometry lives only in the Advanced Output preset XML, which the plugin reads
  (`plugin/src/Preset.cpp`). The bridge stopped reading it with the Bar Chaser engine.
  `rig.preset_rects()` is a read-only Python port of that parser: same screen/slice rules, same
  `"Screen / slice"` names, so a pad's dropdown value finds its rectangle. The preset file is the
  one named in the instance's `Preset` text parameter, resolved like the plugin does
  (`rig.resolve_preset`: empty = newest `.xml` in `~/Documents/Resolume Arena/Presets/Advanced Output`).
- The SEQ **PRESETS** menu (BU3) is an empty placeholder: `SEQ_PAGES["seq_presets"] = []`.
  That is where banks go.
- Patterns on the Push: row 5 = P1–P8, Shift + row 5 = P9–P16.
- Hard constraint from `CLAUDE.md`: Python 3.9 compatible, `from __future__ import annotations`.

## 1. Rig detection (`rig.py`)

Input: the 24 pad values of the **reference track**, the preset rectangles, and an optional
config override.

- **Reference track**: T1's first Bar Chaser instance; if T1 has none, the first track that has
  one; with no instance at all (bridge offline, `--load-bank` from the shell), T1's pad memory
  `pad_configs[0]`. If another track's pad assignment differs from the reference, show
  `banks follow T1's pads` once. Steps are keyed by pad, so all tracks share one placement.
- **Pads used**: every pad whose value isn't `—`.
- **Order**: by rectangle centre x, then y, when every used pad has a rectangle. Otherwise by pad
  number (Bar Chaser's default is pad k = k-th screen by x, so that is usually the same).
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
- **Override** in `config.yaml` (optional; add it to `DEFAULT_CONFIG["sequencer"]` as `rig: {}`):

  ```yaml
  sequencer:
    tracks: 4
    rig:
      dominant: [2, 5, 8]      # pads that play D; leave out to detect from the preset
      order: [1, 2, 3, 4]      # pads left → right, only when the preset can't tell
  ```

Cache the rig by (reference pad values, preset path, preset mtime, override). Building it reads a
file, so never rebuild it in the 100 Hz sequencer loop.

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

`chases.yaml` gains a `bank` block and a `source` per pattern:

```yaml
bank:                                   # present while any pattern comes from a bank
  id: techno
  rig: "1S 2D 3S 4S 5D 6S 7S 8D 9S"     # signature the placement was made for
patterns:
  - name: R1 Pump
    source: {bank: techno, recipe: R1, seed: 11}   # removed once the steps are edited by hand
    length: 32
    direction: forward
    swing: 0.1
    tracks: [...]
```

- `Pattern` gets `source: dict = None`; `_pattern_to_dict` writes it only when set;
  `_pattern_from_dict` reads it. `Sequencer` gets `bank: dict = None`, saved in `to_dict()` /
  read in `from_dict()`.
- **Placement belongs to the bank, knobs belong to the user.** Step edits clear the pattern's
  `source`: `toggle_step`, `set_step_values`, `clear_steps`, and `double_loop` when it copies
  steps. Knob edits don't: envelope, gate, level, direction, length, swing. `copy_pattern` keeps
  `source` (the copy is still a bank pattern). `clear_pattern` creates a fresh `Pattern`, so no
  `source`.
- **Load** (`Sequencer.load_bank(patterns, rig)`): replaces all 16 patterns with
  `banks.realize_bank(rig)`, sets `bank = {id, rig: rig.signature()}`, sets `grid = "1/16"`, and
  keeps `groups`, `track_groups` and `pads`. If the sequencer is running, the current pattern
  index stays and the new content starts at the next step.
- **Re-fit** (`Sequencer.refit(rig)`): for every pattern with a `source`, `banks.refit(pattern,
  rig)` swaps in new steps and keeps name, length, direction, swing and each track's envelope,
  gate and level. Patterns without a `source` are left alone. Then store the new signature.
- Old steps that point at pads no longer assigned are harmless; the engine has no instance
  parameter to send to. Re-fit removes them.

## 5. When it runs

- **Load**: from the PRESETS menu (below) or `python push_resolume_bridge.py --load-bank techno`.
  From the shell the bridge must not be running, because it would overwrite `chases.yaml`. That
  mode builds the rig from pad memory plus the preset and prints `rig.describe()` and the signature.
- **`--rig`**: prints the detected rig (order, roles, source, signature, pools) and exits. Good
  for checking a new venue before the show.
- **Automatic re-fit**: after the first composition arrives at startup, and when a new
  composition loads (`PluginEngine` already notices that), build the rig. If the signature
  differs from `bank.rig`, re-fit, save, and show e.g. `rig changed 9 → 12 pads: 16 patterns
  re-fitted`.
- **Pad edits during the show** (a `Pad n` changed in Arena; `sync_pads` sees it): don't re-fit on
  your own. Show `rig changed · Re-fit in PRESETS` once. Re-fitting mid-song moves every hit.

## 6. Push: PRESETS menu (BU3) **(confirm)**

The SEQ view's lower row is BD1–BD4 = tracks, BD5–BD8 unused, so the menu can use BD5–BD8:

| Control | Action |
|---|---|
| BD5 | Load the Techno bank into P1–P16. Needs a second press within 2 s (`press again to replace P1–P16`) |
| BD6 | Reshuffle the current pattern: new random seed, same recipe, realised on the current rig. Shift + BD6 = all bank patterns |
| BD7 | Re-fit to the current rig now |
| BD8 | Show the rig on the display for 3 s |
| K1 | Bank (only `Techno` for now; the list comes from `banks.BANKS`) |

Display in PRESETS: a rig line (`RIG 9 pads · 3 D / 6 S · preset Main`, or `· alike bars, D by
position`), the bank name, the current pattern's recipe and seed, and `edited` for patterns
without a `source`. BD5–BD8 LEDs: dim white when available, off when there's no rig (`RigError`).

## 7. Steps for the implementer

1. Copy `reference/rig.py` and `reference/banks.py` to the repo root and
   `reference/test_banks.py` to `tests/`; change its `sys.path.insert` to the repo root (the parent
   of `tests/`); run it and see `OK`.
2. `sequencer.py`: `Pattern.source`, `Sequencer.bank`, dict round-trip, clear `source` on step
   edits, `load_bank()`, `refit()`. Extend `tests/test_sequencer.py`.
3. Bridge: `current_rig()` (reference track → `pads_of` → the instance's `Preset` param →
   `rig.preset_rects` → `rig.build_rig` with `cfg["sequencer"].get("rig")`), cached as described
   in section 1. Errors go to `note_msg`.
4. The PRESETS menu and LEDs (section 6), the automatic re-fit (section 5), and the `--load-bank`
   and `--rig` flags.
5. `tests/test_fake_push.py`: open PRESETS, BD5 twice → P1 is named `R1 Pump` and has steps on the
   mock's pads; Shift + BD6 changes the seeds. Give the mock composition fewer than 4 assigned pads
   once to check the error message.
6. Docs: `README.md` (SEQ presets), `CLAUDE.md` (file table: `rig.py`, `banks.py`; PRESETS is no
   longer a placeholder). Delete `merge_banks.py` / `techno_banks.yaml` if they were copied into
   the repo.

## 8. Tests that must pass

Already in `reference/test_banks.py`:
- the virtual split table above, symmetry for every N 4–24, the 3 + 6 preset example (pads
  assigned out of x order still sort by x), config override, errors for 3 and 25 pads;
- the pools on the current rig (`sat`, `pairs`, `outer_d`, `s_high`, `s_mid`);
- every recipe on every test rig: only pads of the rig, steps 0–31, levels 0.05–1, deterministic,
  kick only on D and on every beat (all kick recipes except W1, whose kick uses every pad), rumble
  only on S, at least min(N, 3) pads lit;
- timing doesn't depend on the rig (all tracks except the random steps of W2 T3 and W4 T2);
- `refit` keeps envelope and swing edits, and leaves patterns without a `source` alone;
- all 16 patterns play through `Sequencer.tick()` on 9, 4 and 24 pads.

Add:
- round-trip through `chases.yaml` keeps `source` and `bank`; `toggle_step` clears `source`;
- `load_bank` keeps `groups` / `track_groups` / `pads` and sets grid 1/16;
- automatic re-fit only when the signature changes.

## Later (not in this job)

- Real randomness on every loop: a per-step chance, or a "random pad from pool" step resolved at
  playback. Today a pattern repeats every 2 bars (32 steps), and Reshuffle only helps between loops.
- More banks, and user-written recipes as YAML (the vocabulary in section 2 is the natural format).
- Separate placements per track when tracks use different pad assignments.
- Rigs laid out in several rows or in a circle (position is by x today; `order` overrides it).
