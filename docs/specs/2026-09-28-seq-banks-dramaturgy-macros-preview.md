# SEQ banks: dramaturgy, macros and preview (handover)

Written 2026-09-28 from a Claude chat with Štefan, for the Claude Code agent implementing it.
Builds on `2026-09-27-seq-banks-design.md` (rig detection, recipes in role space, re-fit). This
document adds three things: the reasoning behind the four banks and sixteen patterns, five
macros that bend a pattern into variations, and a way to preview a pattern before it goes live.
Proposals that need Štefan's OK are marked **(confirm)**.

## Files in this handover

| File | Put it at | What it is |
|---|---|---|
| this file | `docs/specs/2026-09-28-seq-banks-dramaturgy-macros-preview.md` | The design |
| `reference/rig.py` | `rig.py` | Unchanged from the first handover |
| `reference/banks.py` | `banks.py` | **Updated**: macros, tiers, `Mulberry32`, `morph()`, `set_macros()` |
| `reference/test_banks.py` | `tests/test_banks.py` | **Updated**: macro checks. Prints `OK` |
| `reference/preview_page.html` | `docs/preview/` or leave out of the repo | The web preview Štefan approved: same engine in JavaScript, the reference for how the macros should feel |

Both engines are bit-identical: 1344 patterns realised in Python and in the page (7 rigs × 16
recipes × 12 macro settings) matched step for step, because both use the same PRNG
(`Mulberry32`) and the same call order. Keep it that way: any change to a recipe or to `Draw`
must be made in both files, and the comparison in section 8 must still pass.

What changes in the first handover: `source` gains `macros` (section 5 here), section 6 of that
document (the PRESETS menu) is replaced by section 6 here, and the Python reference now has the
macros. Everything else there still holds.

---

## Part A: Dramaturgy

### 1. What the banks are for

Techno on a dance floor is arranged in blocks of 8, 16 and 32 bars: the DJ adds an element,
removes the kick, brings it back, and the room reacts to those changes more than to anything
inside a bar. The lighting should do the same. The bank therefore gives Štefan four **feelings**
(the rows on the Push) and, inside each, four **energy states** (the columns):

```
            Minimal          Groove           Peak             Kick out
Rumble      R1 Pump          R2 3/16          R3 Dub           R4 No kick        Berlin, 130-138
Machine     D1 909           D2 Shuffle       D3 Stabs         D4 No kick        Detroit, 125-132
Hard groove G1 Ride          G2 Tribal        G3 Rolling       G4 No kick        Club, 138-148
Warehouse   W1 Stomp         W2 Bounce        W3 Scatter       W4 Tension        Hard, 150-165
```

- Choose the **row** by the record playing: what its drums do, not its BPM. The BPM numbers
  are only where the envelopes were tuned.
- Move along the **row** with the arrangement: column 1 while a track is stripped or in a
  blend, 2 for its main groove, 3 for the loud part, 4 the moment the kick disappears. Back to 2
  or 3 when it returns. Pattern switches land on the next bar boundary, so pressing a pad on
  the last beat before a drop is fine.
- A **bank change** is a scene change: do it between records or at a breakdown, not inside a
  groove, because the envelope character changes (soft rumble against hard warehouse flashes).

Everything in one row shares a placement grammar, so switching inside a row looks like the
same show changing state, not a new show. That grammar is in section 3.

### 2. The vocabulary every pattern uses

Four tracks with fixed jobs (they are also the four Bar Chaser layers Štefan builds):

| Track | Job | Texture that suits it | Why |
|---|---|---|---|
| T1 kick | The pulse. Always on beats, always on D bars, the only thing that never moves off the grid | Flat white, or a slow gradient | The eye needs one stable reference to read everything else as syncopation |
| T2 hats | Air. 16ths, offbeats, rides, strobes on the S bars | White or a cool tint | Small, fast, high: what hi-hats are in the mix |
| T3 accent | Events. Claps, stabs, toms, dub throws | A colour or a moving texture | It should read as a different instrument, so it gets a different texture |
| T4 movement | Weight. Rumble, glow, rolling bass, swells | A slow texture (clouds, fire) | Long envelopes on the biggest bars |

Rhythm vocabulary, at grid 1/16, steps 0–31 (two bars):

| Word | Steps | Meaning |
|---|---|---|
| beats | 0 4 8 … 28 | quarter notes; the kick |
| downbeats | 0, 16 | first beat of each bar |
| offbeats | 2 6 10 … 30 | the "and": open hats, rides, offbeat bass |
| e / a | 1, 3, 5, 7 … | the 16ths between: ghost notes |
| 3/16 | 2 5 8 11 14 … | every third 16th: a 3-against-4 cycle that drifts against the beat and comes back after 3 beats |
| clave | 0 3 7 10 12 / 18 20 24 27 31 | rumba clave, 3-2 then 2-3: the tribal-techno tom pattern |
| dotted 8th | +3 steps | delay echoes in dub techno |

Placement vocabulary (from the first handover, unchanged): `D`, `S`, `ALL`, `sat(d)` = the S
bars beside a D bar, `pairs` = mirrored S pairs outside → in, `outer_d`, `s_high` / `s_mid`,
`few()` = 1 pad up to 11 in the pool, 2 up to 19, 3 up to 24, `share(pool, f)`, `kick_n` = 1
while the rig has ≤ 4 D pads else a third of them, `hop()` = random pads that never repeat the
previous pick of the same voice.

Two rules hold everywhere:

1. **Timing is composed, placement is drawn.** A recipe writes down *when* each voice hits and
   how loud. *Where* it lands is random inside a pool that fits the voice's role. That is why
   the show pulses with the music instead of playing a visible chase: the rhythm is always right,
   and the bars stop being predictable.
2. **A voice never lands on the same bar twice in a row** (unless the pool is too small). This
   one rule is most of what makes random placement look alive instead of flickery.

### 3. The four banks

#### R: Rumble (Berlin)

*In the music.* Berlin techno of the Berghain/Ostgut kind, 130–138 BPM: a dry 4/4 kick whose
reverb tail is compressed and distorted into a low "rumble" that swells *between* the kicks.
Percussion is sparse and sits on odd cycles (3/16, Euclidean patterns), and dub-techno delay
throws appear and fade. Almost straight timing (swing 0.1). Nothing announces itself; the
groove is felt in the low end.

*On the bars.* The kick lands on **one D bar at a time** and hops; the rumble (T4, attack 0.2
beat, sustain 1, release 0.3) rises on the **S bars beside that kick** one 16th later, so every
kick has a low shadow that spreads outward. Ticks live on S bars and never repeat a spot. It is
the calmest bank; its identity is the pump.

| P | Pattern | Role | Tracks and their pools |
|---|---|---|---|
| 1 | **R1 Pump** | minimal: opening, blends | T1 beats → hop(D, kick_n). T4 rumble beat+1 → sat() of the kick's bars. T2 ticks at 3 9 14 19 25 30 → hop(S): an uneven 6-hit cycle so the eye can't count it |
| 2 | **R2 3/16** | groove | T1 + T4 as R1. T2 every third 16th from step 2 → hop(S), louder when it lands on an offbeat. The cycle drifts against the kick and realigns every 3 beats: the hypnotic engine of the bank |
| 3 | **R3 Dub** | peak | T1 + T4 as R1. T2 all non-beat 16ths → hop(S) with accents. T3 two dub throws: stab on the and-of-2 (steps 6 and 22), echoes every 3 steps at 60 / 35 / 20 %, each echo on a new bar anywhere |
| 4 | **R4 No kick** | kick out | No T1. T2 keeps the 3/16 turning (the room still has a pulse). T4 glow (attack 0.25, release 1.5) on a D bar every 2 beats, one bar at a time. T3 one dub throw from step 14 |

#### D: Machine funk (Detroit)

*In the music.* Detroit techno from Juan Atkins to Robert Hood and Jeff Mills, 125–132 BPM: a
TR-909 kick, clap on 2 and 4, open hat on every offbeat, closed 16th hats with shuffle around
55–60 % and 909-style accents, toms answering at the end of a phrase, chord stabs that push
ahead of the beat. It is funk played by machines: the swing and the syncopation are the point.

*On the bars.* The downbeat hits **all D bars** (the accented 909 kick), the other beats hop.
Claps sit on the **outer D bars** like a backbeat in a stereo mix. Open hats on **mirrored S
pairs**, so the offbeat opens the rig symmetrically. Swing 0.3 is saved in the pattern.

| P | Pattern | Role | Tracks and their pools |
|---|---|---|---|
| 5 | **D1 909** | minimal | T1 downbeats → all D, beats 2-4 → hop(D, kick_n). T2 open hat on every offbeat → pair_pick (a new mirrored pair each time). T3 clap at 4 12 20 28 → outer_d |
| 6 | **D2 Shuffle** | groove | T1 as D1. T2 all non-beat 16ths → hop(S) with 909 accents: offbeat 0.9, "e" 0.35, "a" 0.55. T3 claps + a ghost clap at step 31 on one S bar: the hiccup before the loop turns |
| 7 | **D3 Stabs** | peak | D2 plus T4 stabs at 3 6 14 / 19 22 26 29: a call in bar 1, a longer answer in bar 2, each on a third of the rig at once |
| 8 | **D4 No kick** | kick out | T2 hats and T3 claps carry on. T4 toms at 3 6 10 / 19 22 26 then a roll 28 29 30 31: low tom → D, mid → s_mid, high → s_high, so the roll runs from the outside in |

#### G: Hard groove (club / tribal)

*In the music.* Loop-driven, percussive techno (Ben Sims, Surgeon's groove side, today's
hardgroove revival), 138–148 BPM: ride on the offbeat, toms and congas in clave-type cycles, a
rolling 16th bassline that fills the space between kicks, light shuffle (swing 0.2). The kick
is steady; everything above it dances.

*On the bars.* The kick is on **all D bars** (steady), the ride shimmers over a **share of the S
bars** (a different set each offbeat), toms hop on the clave, and the rolling bass flickers on
the D bars between kicks so the big bars never go fully dark.

| P | Pattern | Role | Tracks and their pools |
|---|---|---|---|
| 9 | **G1 Ride** | minimal | T1 beats → all D (downbeat brighter). T2 ride on every offbeat → hop(S, two thirds of S); a faint ping on every "a" → hop(S) at 0.3 |
| 10 | **G2 Tribal** | groove | T1 beats → hop(D, kick_n). T3 toms on the clave: low tom → hop(D) avoiding the bar the kick uses on that step, high → hop(S). T2 ride → hop(S, a third) at 0.6 |
| 11 | **G3 Rolling** | peak | T1 beats → all D. T4 rolling bass on all non-beat 16ths → all D at 0.35–0.6 (the D bars pulse continuously). T2 ride → hop(S, half). T3 clave toms → hop(S) |
| 12 | **G4 No kick** | kick out | T2 shuffled 16ths → hop(S). T3 clave → hop(ALL) (with the kick gone the toms may use the D bars), plus a 4-hit roll 28–31 rising 0.5 → 1 into the next loop |

#### W: Warehouse (hard / industrial)

*In the music.* Hard techno of 2024–2026 (Sara Landry, I Hate Models, the Schranz revival),
150–165 BPM: a distorted kick that carries the bass, an offbeat bounce, 16th percussion loops,
and silence used as pressure: pulling the ride four bars before a drop, muting the rumble for
one beat. Straight timing. The eye should get the same violence and the same negative space.

*On the bars.* This is the only bank that uses the **whole rig as one instrument**: full-rig
slams with a moving dark hole, a hard D/S bounce, strobes anywhere, and a blackout that rolls
into the drop. Envelopes are the hardest (`hard`, `bounce`, `strobe`).

| P | Pattern | Role | Tracks and their pools |
|---|---|---|---|
| 13 | **W1 Stomp** | minimal | T1 every beat → ALL minus a hole of 2/9 of the rig (1 … N−1 pads), a new hole each beat. T2 one glint per offbeat → hop(ALL) at 0.35 |
| 14 | **W2 Bounce** | groove | T1 beats → all D. T2 offbeats → all S (kick / bounce, kick / bounce), except one random offbeat per bar that shrinks to a mirrored pair. T3 two random "a" steps per bar → hop(ALL): the stutter |
| 15 | **W3 Scatter** | peak | T1 beats → all D. T2 every non-beat 16th → hop(ALL) at a random level 0.5–1. Chaos on a grid |
| 16 | **W4 Tension** | kick out (the build) | No T1. Bar 1: 5 random strays → hop(ALL) at 0.3–0.6. Bar 2: a roll at 16 20 24 26 28 29 30 31 lighting 1 1 1 2 2 3 3 4 ninths of the rig, brightening 0.4 → 1. T4 swell on all D from step 16, 0.05 → 0.7. Drop into W1 or W3 |

### 4. Why "kick out" is column 4 and not a separate bank

The kick-out moment is the most important cue in the set and the one Štefan has to hit fast.
Keeping it in the same row means the pad is always the fourth of the four he is already
using, and the return is the pad to its left. The four kick-out patterns are also composed to
keep *something* of their bank going (the 3/16 ticks, the hats and claps, the clave, the strays)
so the room reads "the kick is gone" and not "the lights are off". Their backbone (what
survives Density 0) is the element that carries the arc: R4's glow, D4's claps, G4's clave,
W4's roll.

---

## Part B: Macros

### 5. Definitions

Five numbers 0–1, default 0.5 = the pattern as composed. They are stored per pattern in
`source.macros` and re-realise the pattern from its recipe and seed. Štefan's brainstorm gave
Strobe, Complex (here Density) and Speed; Spread and Accent were added because motion distance
and dynamics are the other two things a lighting operator reaches for. All formulas are in
`banks.py`; this section is the contract.

| Macro | Low (0) | High (1) | What it changes |
|---|---|---|---|
| **Strobe** | soft, fluent | hard, blinking | Envelope shape of every track: attack, decay, sustain, release, gate |
| **Density** | only the backbone | everything, wider hits | Below 0.5 removes hits by tier; above 0.5 each hit lights more bars |
| **Speed** | hits stay put | every hit moves | How often a voice moves to another bar. Never the tempo or the rhythm |
| **Spread** | walks to neighbours | jumps across | How far the next bar is from the last one |
| **Accent** | flat | strong dynamics | Contrast between loud and soft hits, and the level jitter |

**Strobe** (`morph(env, gate, strobe)`, t = (strobe − 0.5) × 2):
- t ≤ 0, u = −t: attack += 0.25u · decay ×= (1 + u) · sustain += (1 − sustain)u · release ×= (1 + 3u)
  · gate += (1 − gate) × 0.5u
- t > 0: attack ×= (1 − t) · decay ×= (1 − 0.7t) · sustain ×= (1 − 0.8t) · release = max(0.05, release × (1 − 0.85t))
  · gate = max(0.15, gate × (1 − 0.6t))
- Floors keep a flash visible: 0.05 beat release and 15 % gate ≈ 90 ms at 134 BPM. Each track's
  base envelope is the recipe's `ENV` entry (at 0.5 the values equal the first handover).

**Density** (`Draw.allow`, `Draw.mult`):
- Every hit has a tier: **1 backbone** (kick on beats, claps, W1 slams, R4 glow, G4 clave, W4
  roll), **2 groove** (rumble, offbeat hats, ride, clave toms, stabs, the first hit of a dub
  throw, W2 bounce), **3 ornament** (e/a 16ths, ghost notes, echoes, glints, strays, rolls at the
  end of a loop). Tier 2 needs density ≥ 0.15, tier 3 needs ≥ 0.4. Tier 1 always plays, so no
  pattern is ever empty.
- Pads per hit are multiplied by `mult` = 0.5 + d below 0.5, 1 + 2(d − 0.5) above (so 1 at
  0.5, 2 at 1.0), rounded, at least 1, at most the pool. "Block" hits (all D, all S, everything)
  and W1's hole are not scaled.

**Speed** (`Draw.hold`, `Draw.block`):
- A voice keeps its previous pads for `hold` consecutive hits: 1 at speed ≥ 0.5, 2 at ≥ 0.35,
  4 at ≥ 0.2, else 8. At 0 the kick stays on one D bar for the whole loop.
- Above 0.5, a block hit (all D / all S / ALL) becomes a moving part of its pool: at 1.0 half
  of the pool, a new half each time. So "kick on all long bars" turns into a kick that walks.

**Spread** (`Draw.pick`): the next pad is drawn with weights by distance from the previous
pick, dist = |pos − pos_prev| / (N − 1). k = |spread − 0.5| × 6. Below 0.5 weight = (1.15 −
dist)^k (near wins), above weight = (0.15 + dist)^k (far wins), 0.5 = uniform. Only pads are
weighted; picks of steps or pair indexes stay uniform.

**Accent** (`Draw.accent_level`, `Draw.hum`): a ≤ 0.5: level = 0.85 + (level − 0.85) × 2a (0 →
every hit at 0.85); a > 0.5: level = level^(1 + 4(a − 0.5)) (1 → level cubed: 0.35 becomes
0.04, 0.9 becomes 0.73). The jitter `hum` scales with 2a. Clamped to 0.05–1.

Determinism: `hum` always draws a random number even at accent 0, so Accent and Strobe never
change *where* hits land. Density, Speed and Spread change how many random numbers are drawn,
so placement changes with them (by design: they are about placement).

### 6. Macros on the Push: the PRESETS menu (BU3) **(confirm)**

This replaces section 6 of the first handover. PRESETS edits a **draft** of the selected
pattern slot and previews it (Part C); nothing reaches the live pattern until Apply.

```
K1 Strobe   K2 Density   K3 Speed   K4 Spread   K5 Accent   K6 Seed   K7 Bank   K8 —
Shift + K1–K5 = the same macro on all 16 drafts (message: "Strobe 0.70 → all")
K6 any turn = new random seed (Reshuffle); Shift + K6 = step through seeds ±1 (repeatable)
K7 = bank to load (only "Techno" for now; the list comes from banks.BANKS)

BD5 Load bank      realise K7's bank into P1–P16 (press twice within 2 s; clears drafts)
BD6 Apply          the draft becomes the slot's pattern (at the next bar if it's the live one)
BD7 Re-fit         re-fit every bank pattern to the current rig now
BD8 Revert         throw the draft away

Row 5 pattern pads (Shift = 9–16): in PRESETS they select which slot is drafted/previewed and
do NOT switch the live pattern; pressing the selected pad again queues it live at the next bar
(the normal SEQ behaviour). White = live, white ring = selected for preview, grey = has steps,
green dot in the display = has a draft.
```

Rules:
- The draft is `{recipe, seed, macros}` per slot, kept in memory until Apply, Revert, Load or
  the bridge quits (drafts are not saved). Slots without a `source` (hand-edited patterns) show
  `edited by hand · no macros` and K1–K6 do nothing.
- Apply = `Sequencer.set_pattern(slot, banks.set_macros(pattern, rig, draft.macros, draft.seed))`
  and save. Applying to the live slot: like a pattern switch, at the next bar boundary, the
  outgoing voices finish their release.
- The ENVELOPE menu still edits the realised envelope directly. Those edits survive a re-fit
  (placement only) but are replaced when Strobe is applied, because Strobe re-derives the
  envelope from the recipe. Show `envelope from Strobe` for a second when that happens.
- Display in PRESETS (960 × 160): top row = the 8 knob cells as in the other SEQ menus (macro
  bars in the track colour of T1, seed and bank as text). Below the rule: the preview strip on
  the left (Part C), then `P3 D3 Stabs · seed 71 · draft` and the rig line `RIG 9 pads · 3 D / 6 S`
  on the right, messages on the bottom line as today.

---

## Part C: Preview

### 7. Level 1: the web page

`reference/preview_page.html` is the standalone preview: any rig (typed as `S D S S D S S D S`
or `U12`), all 16 patterns, the five macros, reshuffle, and Copy YAML. It runs the same engine
and the same seeds, so a pattern previewed there and loaded through `merge_banks.py` (or later
re-fitted by the bridge) is the same pattern. It is a design reference, not a runtime dependency.
Optional later: the bridge serves it at `localhost:6129` with the detected rig pre-filled, the
way push2-python serves its simulator (`--preview`).

### 8. Level 2: preview on the Push display (build this)

**What it is.** A second `Sequencer` instance in the bridge, `Bridge.preview`, that runs the
selected slot's draft and is drawn as a small rig on the Push display while PRESETS is open.
It never sends levels to the engine, so the show is untouched, and it runs whether or not the
live sequencer runs, off the same beat clock (`Bridge.beat_time()`, which follows Resolume's
BPM even when SEQ is stopped).

**Model.**
- `Bridge.preview = Sequencer()` (no path: never saves). `preview.patterns[0]` = the realised
  draft; `preview.grid = "1/16"`; `preview.running = True` while PRESETS is open, `False` and
  `voices.clear()` when the menu closes.
- On every draft change (knob, seed, rig): `preview.patterns[0] = _pattern_from_dict(banks.realize(...))`,
  `preview.switch_pattern(0, bt, now=True)`. Keep the sequencer's own `last_step` logic; a
  change lands at the next grid step, which is what the operator expects.
- `seq_loop` (100 Hz, already there) ticks `preview.tick(bt)` under the same lock right after
  the live one, when the menu is open. Its returned levels go into `Bridge.preview_levels[pad]`
  (max over tracks per pad for white, or per-track values for coloured), nothing else.
- Cost: one more `tick()` per 10 ms with at most a few dozen voices; negligible next to the
  WebSocket sends the live sequencer already makes.

**Drawing** (`display.py`, `seq_presets` branch, bottom-left where the envelope sketch is in
the other menus): a strip 300 × 44 px. Each rig pad is a vertical bar; x from the rig order (left
→ right), height from the preset rectangle's length relative to the longest bar when geometry is
known, else D = full height / S = half (uniform rigs: all 70 %). Brightness = level (0 = dark grey
30, 1 = white 255); with `Shift` held the bars take the track colours (`LAYER_RGB[0..3]`, added
like the engine adds). Under the strip, 32 dots for the steps with the playhead in green. This
is a 20 fps redraw of ≤ 24 rectangles; `render()` already redraws the whole display every frame.

**Rig for the preview** = `Bridge.current_rig()` from the first handover. If it raises `RigError`,
the strip shows the error text and K1–K6 do nothing.

**Pad LEDs.** Untouched: rows 6–8 keep showing the live track, so the operator can compare the
preview strip with what the room gets.

**Audition on the pads (optional, confirm):** hold **Select** + a row-5 pattern pad in PRESETS =
the draft plays on the *pad LEDs* of rows 6–8 (in the draft's track colours) while held, in place
of the live view. No output either.

### 9. Level 3: preview inside Arena with the real textures (later, confirm)

Only Arena can show the chase with the actual textures. Two ways, both without touching the
output:

1. **Preview instances.** Bar Chaser's `Track` option gets a fifth element, `Preview` (elements
   are declared in `BarChaser.cpp`; `PluginEngine.instances()` maps `"Preview"` to track −1). The
   bridge routes `Bridge.preview` levels to Preview instances the same way it routes live levels
   to tracks 1–4. The operator puts a Preview instance on a **clip** in a column that is never
   launched (a copy of the texture); Arena's Preview monitor renders the *selected* clip with its
   clip effects, so selecting that clip (the bridge already `select`s clips) shows the chase in
   the monitor while the output stays dark. One preview clip per texture; four to see all tracks.
2. **A muted preview layer.** Bar Chaser (`Track = Preview`) on a dedicated layer whose master
   is 0 and which is excluded from the Advanced Output. Arena's layer thumbnail shows it. No
   plugin change, but the thumbnail is small and the layer costs a render.

Recommend 1. The plugin change is ~20 lines plus `tests/mock_resolume.py` (`Track` options gain
`Preview`) and `tests/test_engine.py`.

---

## Part D: Implementation

### 10. Storage

`chases.yaml`, per pattern (extends section 4 of the first handover):

```yaml
  - name: D3 Stabs
    source: {bank: techno, recipe: D3, seed: 71, macros: {strobe: 0.5, density: 0.5, speed: 0.5, spread: 0.5, accent: 0.5}}
```

- `source.macros` is written by `banks.realize` and read by `banks.refit` / `banks.set_macros`.
  Old files without `macros` mean all 0.5 (`clean_macros` fills them).
- Step edits clear `source` (first handover). Knob edits in ENVELOPE / SETTINGS do not.
- Drafts are never stored.

### 11. Steps for the implementer

1. Replace `banks.py` with the updated reference; copy `tests/test_banks.py`; run it (`OK`).
   Then run the cross-check against the page's engine once (section 12) to be sure nothing
   was lost in the copy.
2. `sequencer.py`: `set_pattern(i, dict)` (replace one pattern from a dict, keep `current` /
   `pending`), `load_bank()`, `refit()` from the first handover if not done yet.
3. Bridge: drafts (`Bridge.drafts: {slot: {recipe, seed, macros}}`), the PRESETS knob table
   (`SEQ_PAGES["seq_presets"] = ["Strobe", "Density", "Speed", "Spread", "Accent", "Seed", "Bank"]`),
   BD5–BD8 in PRESETS, row-5 behaviour in PRESETS, `Bridge.preview` and its tick in `seq_loop`,
   `preview_levels` in `snapshot()`.
4. `display.py`: the PRESETS layout of section 6 and the preview strip of section 8.
5. `tests/test_fake_push.py`: open PRESETS → turn K1 → the live pattern's envelope is unchanged
   and the preview pattern's is; BD6 → the slot's envelope changed and `source.macros.strobe`
   updated; K6 → new seed in the draft, unchanged in the slot until BD6; BD8 → draft gone;
   Shift + K2 → all 16 drafts; BD5 twice → 16 bank patterns; the preview's `tick()` produces
   levels while PRESETS is open and none reach the engine (assert the mock got no `Level` sets
   from the preview run).
6. Docs: README (SEQ presets and macros), `CLAUDE.md` (PRESETS no longer a placeholder; the
   `banks.py` / `rig.py` rows; "keep banks.py and preview_page.html engines identical").

### 12. Tests that must pass

Already in `reference/test_banks.py` (on top of the first handover's checks):
- every macro at 0 / 0.25 / 0.75 / 1 on every recipe: deterministic, never empty, stored in
  `source.macros`; Density only removes hits below 0.5 and keeps the step set above; Strobe
  never moves a step and is the only macro that changes envelopes; Density 0 keeps the kick on
  every beat for the kick recipes;
- `morph`: identity at 0.5, monotone in both directions, floors at 0.05 beat release and 15 % gate;
- Speed 0 holds a voice on one pad, Speed 1 splits block hits; Spread 0 walks, Spread 1 jumps
  (mean distance between consecutive hits);
- `set_macros` keeps name / swing / track Level, re-derives the envelope, keeps the seed unless
  given one, leaves hand-edited patterns alone.

Cross-check with the page (run once after any change to a recipe or `Draw`): in the browser
console of `preview_page.html`, `JSON.stringify(TechnoBanks.realize(6, TechnoBanks.makeRig('S D S S D S S D S'), 71, {spread: 0.8}).tracks)`
must equal, step for step, `banks.realize("D3", rig_from_roles("S D S S D S S D S"), 71, macros={"spread": 0.8})["tracks"]`
(levels to 2 decimals, envelopes to 3). The handover's own run compared 1344 such pairs.

Add:
- `chases.yaml` round-trip keeps `macros`; a file without `macros` loads as all 0.5;
- drafts: Apply / Revert / Load semantics from section 6 (in `test_fake_push.py`);
- preview: no engine sends while previewing; preview stops and clears when PRESETS closes.

### 13. Later, not in this job

- Per-step randomness at playback (a "random pad from pool" step) so a pattern stops repeating
  every 2 bars; the tiers and pools here are the natural vocabulary for it.
- Preview level 3 (Arena, real textures), section 9.
- Serving the web preview from the bridge with the detected rig (`--preview`).
- More banks (house, breaks, ambient); recipes as YAML so Štefan can write his own without code.


---

## As built (2026-09-28, Štefan: "decide the best UX and go for it")

Sections 5, 7, 8, 10 and 12 are implemented as written. Section 6 (the PRESETS menu) was built with
these changes, because the buttons below the display could not hold both sixteen presets and the
four actions, and Shift already means "fine step" on every knob of the bridge:

- **The recipe grid is on the pads.** In PRESETS the top four pad rows show the sixteen recipes as
  the table of section 1: row = bank (Rumble, Machine funk, Hard groove, Warehouse), column = energy
  (Minimal, Groove, Peak, Kick out). Pressing one makes the draft of the selected slot (fresh seed, the
  previous draft's macros carried over). Columns 5–8 are dark. Rows 6–8 stay the live pads.
- **Row 5 selects the slot** to edit and preview without switching the live pattern; the same pad
  again switches as usual. The edited slot is white, other slots with a draft light grey, a pattern
  made for another rig pink (the row's own colour is orange now, see the views spec).
- **Knobs:** K1–K5 macros, K6 seed (turn = reshuffle, Shift = ±1), K7 bank (one bank so far), K8
  shows the BPM. **Select held + a macro knob** = the same macro on every preset slot (the handover's
  Shift + knob). Shift = fine step, as everywhere.
- **Buttons below:** 1 Apply, 2 Revert, 3 Re-fit, 4 Load bank. Apply onto an own (hand-made) pattern
  with steps asks NO / YES on buttons 7 / 8; onto a preset pattern with the same recipe it keeps the
  user's track Levels (`banks.set_macros`), with another recipe it realises fresh. Applying to the live,
  running pattern happens at the next bar (`Sequencer.apply` / `pending_apply`). Load bank needs two
  presses within 2 s and **keeps own patterns** (the handover overwrote P1–P16); the kept slots are named.
  Re-fit blinks when the rig changed during the show (the earlier NO / YES question is gone; the
  loaded-composition re-fit stays automatic).
- **Preview** (section 8, level 2): the strip sits at the right of the buttons' labels, one bar per rig
  pad (long bars taller), brightness = level, redrawn at 20 fps. The audition on the pads (optional in
  section 8) and level 3 (Arena) are not built.
- `source.rig` (the signature the placement was made for) is kept next to `macros`; the page's YAML
  does not have it and `banks.refit` / `set_macros` write it.
