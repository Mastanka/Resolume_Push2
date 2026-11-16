# Test scenarios and robustness pass

Date: 2026-11-16. Branch `claude/refactoring-ux-analysis-56595f`. Asked for by Štefan: "bugs when I switch
between layers, remove effect, add effect, switch composition, create new one, change the fixtures".

## Tools

| Tool | What it does | Run |
|---|---|---|
| `tests/sim_arena.py` | In-process Arena: a composition with Arena's id behaviour (new ids for a new or re-opened composition), the REST calls the bridge makes, what a user does in Arena (`user_*`), delivery like the WebSocket (`deliver_comp`, `deliver_params` for subscribed ids), the Sender queue drained into it (`process`), a virtual clock | library |
| `tests/test_scenarios.py` | 45 deterministic scenarios on the simulator, plus the structural fuzzer as a regression test | `python tests/test_scenarios.py` |
| `tests/fuzz_arena.py` | Structural fuzzer: random Push input mixed with random Arena edits; checkpoints every ~150 events stop everything and check what reached Arena | `python tests/fuzz_arena.py 16 2000` |
| `tests/soak_mock.py` | Every thread running (real `run()`, fake Push) against the HTTP + WebSocket mock while a driver changes the composition | `python tests/soak_mock.py 60` |
| `tests/fuzz_states.py` | UI state fuzzer (held buttons, overlays, selections) from the earlier pass | `python tests/fuzz_states.py 16 2500` |

Invariants checked by the fuzzers after every event: no exception in any handler, LED pass, snapshot or
render; a held flag only while its button is down; selection and grid inside the composition. At every
checkpoint: no Bar Chaser level left lit after the sequencer stopped, every Bar Chaser shows the
remembered mapping (where its dropdown offers the fixture), no write to an id that no longer exists.

## Scenarios

| # | Situation | Checks |
|---|---|---|
| A1 | Every menu of both views and the overlays, on layers with and without Bar Chaser | no error, layer names per track |
| A2 | The layer of the selected clip is deleted | selection moves inside the composition |
| A3 | Layers reordered | levels follow the effect to its new layer |
| A4 | LAYER PARAMS on a Bar Chaser layer | Level / Pad / Track / Preset / Geometry are not knobs; Master, Edge, Outside, Mode are |
| B1 | Bar Chaser removed while a pad is lit | no write to the removed effect; the track folds onto the remaining layer |
| B2 | Bar Chaser added in Arena | it gets the remembered mapping |
| B3 | Shift + Note | Bar Chaser added to the selected layer |
| B4 | Track changed in Arena, only a parameter update arrives | routing follows at once |
| B5 | Track changed while a pad is lit | the old layer goes dark, the fold takes over |
| B6 | A folded track gets its own layer while lit | the partner layer goes dark, the new one lights |
| B7 | Fold off, Track changed while lit | nothing stays lit |
| B8 | Bar Chaser on a clip that is not playing, then launched, then cleared in Arena | counts only while the clip plays: folds onto the partner's layer before and after, own levels while it plays, nothing left lit |
| C1 | Another composition opened while playing | its T1 mapping is taken, the others follow, no writes to the old ids, levels reach the new effects |
| C2 | New empty composition (0 layers), then a layer and a Bar Chaser | every screen works, the mapping is kept and given to the new effect |
| C3 | Composition without Bar Chaser | warning, nothing written |
| C4 | Blackout, then another composition opens at master 1.0 | the new master goes to 0; Stop Clip restores |
| C5 | Flash and held clip across a composition switch | the releases write nothing into the new composition |
| C6 | Arena offline, back with another composition | every screen works, mapping consistent |
| C7 | Play + a button below a column that does not exist | nothing sent |
| D1 | Advanced Output with fewer fixtures | pads that lost their fixture become "—", rig and kick bars follow |
| D2 | Preset not readable (only "—"), then readable again with the plugin's default pads | the remembered mapping is not wiped and comes back |
| D3 | A composition opened whose Bar Chaser cannot read its preset | the remembered mapping is kept |
| D4 | A pad changed in Arena on the T3 effect | it becomes the mapping for all |
| D5 | MAPPING on the Push when one effect lacks the fixture | no error |
| D6 | Newer plugin with `Geometry`, no preset file on this machine | the rig comes from the effect |
| E1 | A PRESETS draft across a composition switch | Apply works on the new rig |
| E2 | Load bank without any Bar Chaser | no error |
| E3 | Rig changes while PRESETS is open | the draft is previewed on the new rig |
| F1 | Subscriptions | the bridge's own Level params are not subscribed |
| F2 | A composition snapshot older than our own Pad write arrives | the write is not undone |
| G1 | Tap Tempo while a pattern plays | the pattern keeps stepping, no voice in the future |
| G2 | Resync | the beat moves at most two beats and lands on beat 1 |
| G3 | BPM halved in Arena | the beat clock stays continuous, nothing stays lit |
| G4 | A clock that goes backwards | a flash still ends |
| H1 | Quit while a bar is lit | every Bar Chaser level 0 after `shutdown()` |
| H2 | A bar lit before the bridge started (a crash, or saved lit in the composition) | it goes dark on the first composition |
| H3 | A 0 lost on the way (socket hiccup) | the next composition shows the difference and the 0 goes out again |
| H4 | Knob + Apply while an earlier Apply waits for the bar | the waiting pattern is what the knob edits; it lands with the new macros; the preview follows |
| H5 | A direct write to a slot whose older Apply still waits | the newer write wins |
| H6 | A composition loads while an Apply waits | the waiting pattern is re-fitted too; Re-fit stops blinking |
| H7 | A fixture only one Bar Chaser offers, then an edit on the other | the mapping keeps it; only the edited pad changes |
| H8 | Kick bars that lost their fixture | dropped at the next kick-bar change |
| H9 | The preset file vanishes between listing and reading | no crash in the LED pass |
| H10 | Hand-edited chases.yaml: unknown recipe, unknown direction | no crash |
| Z | Structural random walk, 4 seeds × 600 events | 0 crashes, 0 violations |
| (test_sequencer) | A broken chases.yaml | copied aside to `chases.yaml.bad-<time>` before anything can overwrite it |
| (test_sequencer) | An older save snapshot written after a newer save | skipped; files are written through a temporary file and a rename |
| (test_sequencer) | Stop with a pattern queued | the queue is dropped; Play starts the current pattern |
| (test_sequencer) | 2-bar patterns queued, switched now, Applied while running; a 1/8 pattern; a tick that jumps a bar | each starts on its own phrase counted from Play; "now" joins at the phrase's step |
| (test_sequencer) | Grid changed on one pattern; save, copy, preset dict, old file | every pattern keeps its own grid; presets and old files 1/16 |

## Bugs found and fixed

| Found by | Bug | Effect on stage | Fix |
|---|---|---|---|
| B5, B6, B7, B1 | A level that stopped being routed to an effect (Track changed, fold ended, effect removed) was never set back to 0 | a bar stays lit until that pad is played again | levels are a desired state per (track, pad); every routing change re-pushes all Level params |
| B4 | The instance cache was keyed on the composition object; a Track change arrives as a parameter update in the same object | wrong layer flashes for up to 5 s | where the effects are is cached, Track is read live; the cache is one tuple, safe across threads |
| D2, D3 | An effect that cannot read its preset offers only "—"; it was taken as the mapping (opened composition) or as an edit | the remembered pad mapping is wiped | effects without fixtures never become or donate the mapping; when their preset is back their default pads are not taken for an edit |
| C4 | Opening a composition during a blackout ended it (the new master was "raised elsewhere") | the show comes back on after switching | blackout re-armed on the new master |
| C5 | Flash restore and clip release after a composition switch went to the old ids / into the new composition | stray writes, a release into a clip never pressed | momentary actions of the old composition end without writes |
| F1 | The selected layer's Bar Chaser Level params were subscribed | every flash echoed back over the WebSocket | own Level params excluded |
| G1–G4 (fuzzer) | Tap, resync and BPM changes reset or rescaled the beat clock; the sequencer clamped to step 0 and voices froze in their attack | after one tap the pattern stalls and flashes stay lit for as long as the show had run | continuous clock, taps move it to the nearest beat (≤ 2 beats); the sequencer moves envelopes along with a backwards clock |
| E3 | The PRESETS preview kept the old rig after a mapping change | the strip shows the wrong bars | preview re-realised on a rig change |
| soak | Play + a button below a missing column sent a launch | a failed request | ignored |
| fuzz_arena | LAYER PARAMS offered Bar Chaser's 50 internal params as knobs | a knob fights the sequencer, useful knobs on page 7 | hidden |
| profile | The rate limiter summed over every parameter on every send | clock ticks up to 50 ms under heavy patterns | O(1) busy test, routing checked at most every 5 ms in a tick: worst tick 0.6 ms |

## Found by the independent review (agent, reproduced, then fixed with H1–H10)

| Bug | Effect on stage | Fix |
|---|---|---|
| Quit did not stop the sequencer or darken anything; a level never written this session was never set to 0 | bars stay lit after quitting, after a restart, or in a composition saved lit | `Bridge.shutdown()` stops, sets every level to 0 and writes chases.yaml; every full composition is compared with what each Level should show (`PluginEngine.observe` / `_reconcile`) |
| The WebSocket outbox dropped the oldest set when full and replayed stale sets after a reconnect | a lost 0 leaves a bar lit until that pad plays again | one waiting value per parameter, cleared on a drop; the reconnect's full composition repairs the rest |
| PRESETS read the slot, not the Apply waiting for the bar; nothing reloaded the preview when it landed; an older waiting Apply could overwrite a newer write | a knob + Apply in that bar brought the old recipe back | `Sequencer.slot_pattern`, `landed` → preview reload, direct writes cancel a waiting Apply, re-fit includes it |
| A broken chases.yaml was replaced by empty patterns on the next save | the show's patterns are gone | the file is copied aside first; the display says where |
| Two threads could write chases.yaml at once, the older snapshot last; `write_text` is not atomic | a newer change undone, or a torn file (then: the line above) | versioned snapshots, temporary file + rename |
| A pad edit on one Bar Chaser took all 24 of its pads; `set_pad` stored the reference effect's pads | a fixture only one effect offers was undone | edits merge pad by pad; `set_pad` stores the chosen fixture |
| Re-fit kept blinking after the automatic re-fit of a loaded composition | a false alarm | cleared |
| Kick bars that lost their fixture could not be removed | they came back with the fixtures | dropped at the next kick-bar change |
| Preset lookup outside its guard; LED pass without one | one vanished file could stop the run loop | guarded; the LED pass logs and continues |

## Decisions after the pass (Štefan, 2026-11-16)

Blackout stays on across a composition switch (kept). Stop drops a queued pattern. Patterns (and a waiting
Apply) start on their own phrase: a 2-bar preset on every other bar counted from Play. A Bar Chaser on a clip
counts only while that clip plays. Every pattern keeps its own grid. The fuzzer now also launches clips and
clears layers in Arena.

## Not covered

- The real Arena: the simulator follows Arena's verified JSON shapes; that a new or re-opened composition
  gets new parameter ids (how the bridge detects "another composition") is assumed. The `Geometry`
  parameter is not yet verified on Arena.
- The real Push hardware (USB display, MIDI timing).
