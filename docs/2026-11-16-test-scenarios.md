# Test scenarios and robustness pass

Date: 2026-11-16. Branch `claude/refactoring-ux-analysis-56595f`. Asked for by Štefan: "bugs when I switch
between layers, remove effect, add effect, switch composition, create new one, change the fixtures".

## Tools

| Tool | What it does | Run |
|---|---|---|
| `tests/sim_arena.py` | In-process Arena: a composition with Arena's id behaviour (new ids for a new or re-opened composition), the REST calls the bridge makes, what a user does in Arena (`user_*`), delivery like the WebSocket (`deliver_comp`, `deliver_params` for subscribed ids), the Sender queue drained into it (`process`), a virtual clock | library |
| `tests/test_scenarios.py` | 35 deterministic scenarios on the simulator, plus the structural fuzzer as a regression test | `python tests/test_scenarios.py` |
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
| B8 | Bar Chaser on a clip | counts as an instance, gets levels |
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
| Z | Structural random walk, 4 seeds × 600 events | 0 crashes, 0 violations |

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

## Not covered

- The real Arena: the simulator follows Arena's verified JSON shapes; that a new or re-opened composition
  gets new parameter ids (how the bridge detects "another composition") is assumed. The `Geometry`
  parameter is not yet verified on Arena.
- The real Push hardware (USB display, MIDI timing).
