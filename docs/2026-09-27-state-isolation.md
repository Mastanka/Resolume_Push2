# State isolation: why the Push gets stuck, and how to prove it cannot

Date: 2026-09-27, on `main` at `2990927`. Companion to
`2026-09-27-refactoring-and-release-analysis.md`.

## Short answer

Yes, both are possible, and both were done here:

1. **Testing.** `tests/fuzz_states.py` drives the real `Bridge` (no Push, no Resolume, no mock)
   with a random walk of *physically consistent* events: a button can only be released if it is
   down, pads and knob touches likewise, plus knob turns, time passing, sequencer ticks, live
   parameter updates, composition refreshes and composition loads of other sizes. After every
   event it runs the LED and display code and checks a list of invariants. 16 seeds × 2,500 events
   take 30 s. Result: **no exception anywhere** (no crash path), but **11 kinds of stuck state**,
   every one reproducible by its seed. Six were then reduced to two- or three-step reproductions,
   below.
2. **Refactoring.** The stuck states share three root causes, and each has a structural fix that
   makes the invariant true by construction instead of by care (section 4).

The overlap is **not** mainly Composition / Layer / Clip. That scoping is in reasonable shape
(section 3). The overlaps are:

- **screen × modifier**: a button press handled by one screen, its release handled by another
  (view or menu switched in between), so the "held" flag never clears;
- **press × composition change**: momentary actions (flash, clip press, column press) remember
  the *target* (layer, clip, column) and recompute it at release, so a scroll or a composition
  load in between restores the wrong target and leaves the real one stuck in Resolume;
- **selection × composition change**: the selected clip and the grid offsets are never
  re-validated when a composition loads.

## 1. What the fuzzer found, reproduced

| # | Steps | Effect | Root cause |
|---|---|---|---|
| T1 | Hold a flash button (right of the pads), press **Up**, release the flash button | **That layer stays at 100 %.** Same with a composition load, or **Note**, while holding | `flash_layer` keys the flash by layer and recomputes the layer from the row at release (`pad_to_cell`); `_seq_button` eats scene-button releases in SEQ |
| T2 | Hold **Play**, press **Note**, release Play, press **Session**, press a pad | **Every plain pad press launches a clip** until Play is pressed and released again in the clip view | `_seq_button` consumes the Play release in SEQ; `play_held` stays True |
| T3 | In SEQ hold **Delete**, press **Session**, release Delete, press **Note**, press a step / pad / pattern / group | **Every press deletes** (steps, pads, patterns, groups; `chases.yaml` is overwritten). Same family: **Browse** (track buttons then re-map layers to tracks, i.e. write the Bar Chaser `Track` param), **Fixed Length** | Delete / Browse / Fixed Length releases are only handled in SEQ (`_seq_button`); `button()` ignores them in the clip view |
| T4 | Hold **Play** + a pad, press **Up** (or Left / Right, Note, or a composition loads), release the pad | **The clip stays pressed in Resolume**: the release is never sent (re-triggering fails; piano clips keep playing). Same with Play + column button and `col_pressed` | `pressed` is keyed by (layer, column) and `pad_released` recomputes the cell at release; in SEQ, `_seq_button` eats lower-row releases |
| T5 | In a 12-layer deck scroll **Up** four times, then a 3-layer composition loads (deck switch) | **All 64 pads black, no parameter rows, knobs dead** until Down is pressed; the selected clip points at a layer that no longer exists | `set_comp` never clamps `layer_offset`, `col_offset` or `sel` |
| T6 | In SEQ hold a step pad, press **MAPPING** (button 4 above the display), release the pad, go back to ENVELOPE | **Gate / Level knobs edit that step instead of the track** | `_map_pad` returns "handled" for releases on rows 1–4, so `held_steps` is never cleared; menu switches do not clear it |

Counts from one run (16 × 2,500 events): flash stuck 14,171 checks, selection outside the
composition 9,361, offsets outside 8,360, Play 1,337, Browse 1,197, Delete 1,058, pressed cells
1,026, held steps 355, columns 121. A count is how long the state stayed stuck, not how often it
started; flash and selection dominate because nothing ever clears them.

**Lost releases.** push2-python drops every MIDI message for one second after a (re)connect
(`on_midi_message`: "ignore the next 1 second"). A release lost in that window produces the same
stuck states without any two-hand gesture, and nothing recovers them: `midi_reset` only clears the
LED caches. The Push's own active-sensing timeout is 0.5 s, so a USB hiccup is enough.

**What does not get stuck** (also from the run): Shift, Duplicate, Mute, Solo and Convert (handled
before the screen dispatch); Select and held pads across a view switch (`set_view` clears them);
`preset_pick`, `confirm`, `map_armed`, `move_src` (cleared on menu and view change); the Mix /
Mute / Solo click-or-hold logic (self-heals on the next press); and no handler, LED or display
exception in 40,000 events.

## 2. Why this is not visible in the existing tests

`test_fake_push.py` presses and releases in matched pairs inside one screen, and never loads a
smaller composition after scrolling. The invariants above are never stated anywhere, so nothing
could check them. That is the general answer to "how do I know states don't overlap": write the
invariants down, then generate the event sequences you would never think of by hand.

## 3. State inventory by scope

| Scope | State | Reset by |
|---|---|---|
| Composition | `comp`, `index`, `online`, `blackout` / `restore` + watchdog, `beat_anchor`, `taps`, `layer_offset`, `col_offset`, `color_target` | new composition (except **offsets: never**) |
| Layer | `sel[0]`, `_pages` (per menu), `flash`, mute / solo flags in the JSON, SEQ track ↔ layer | pad press; **`flash`: only by the same row** |
| Clip | `sel[1]`, `color_idx`, `fx_page`, `move_src`, `hsv_cache`, `choice_acc`, `overrides` | selection change (`pad_pressed`); **`sel` itself: never validated** |
| Screen | `view`, `menus[view]`, `overlay` + `ov_stack` / `ov_latched` / `ov_press`, `page`, `preset_pick`, `confirm`, `refit_offer`, `map_armed`, `map_focus`, `map_page`, `dup_src`, `multi`, `side`, `sel_pads`, `cur_group` | menu / view change (good), except `map_focus`, `dup_src`, `multi`, `sel_pads` (by design) |
| Physical | `shift`, `play_held`, `stop_held`, `paste_held`, `mute_held`, `solo_held`, `convert_held`, `delete_held`, `browse_held`, `fixed_len_held`, `select_held`, `touched`, `pressed`, `col_pressed`, `held_steps`, `held_bars` | **the release, if the current screen handles it** |

The Composition / Layer / Clip rows are mostly right: clip-scoped state is reset when the
selection changes, the pages are per menu, caches are keyed by parameter id. The two gaps there
are `sel` / offsets (T5) and `flash` (T1). The physical row is where the isolation is missing.

## 4. The refactoring that makes it structural

Four changes, each small, in this order:

1. **A physical layer that no screen can bypass.** One `Held` set of control names, updated for
   *every* press and release before any screen sees the event. The eleven `*_held` flags become
   `held("Play")`, `held("Delete")`, … and a screen can only *react* to a release, never swallow
   it. Fixes T2, T3 and the whole class. `release_all()` on MIDI reconnect and at shutdown fixes
   the lost-release case.
2. **Momentary actions keyed by the control, target captured at press.** A `Momentary` registry:
   `start(control, undo)` at press stores the undo (restore layer 3's master to 0.8; send
   connect-false to clip (1, 1); send column 4 up), `end(control)` at release runs it whatever the
   mapping is now, `end_all()` on reconnect, shutdown and, for those that should not survive it,
   on a view switch. Fixes T1 and T4 and makes "the Push is unplugged mid-flash" safe.
3. **A composition change is an event, not a field assignment.** `on_composition()` clamps
   `layer_offset` and `col_offset`, moves `sel` to the nearest existing clip (same layer name if
   it moved, else the same numbers clamped), drops `move_src`, and calls each screen's
   `on_comp()` to re-validate its own references (`map_focus`, a `confirm` slot). Fixes T5 and
   makes deck switching show the new deck at once.
4. **Screens own their sub-state, with `enter()` / `exit()`** (already the plan in the analysis,
   section 4.2). `held_steps` / `held_bars` belong to the SEQ steps screen and its `exit()`
   releases them (T6). A new screen then cannot leave state behind, because it has nowhere else
   to put it.

Every one of the six can also be patched on its own today (S each): set the flag before the screen
dispatch in `button()`; key `flash` / `pressed` / `col_pressed` by row / pad / button and store the
target; clamp in `set_comp`; make `_map_pad` return False on releases. The four changes above are
what stops the seventh from appearing.

## 5. Keeping it that way

- `tests/fuzz_states.py` reports today (exit code 0, so it can go in as is). Once the six are fixed,
  turn its report into an assertion (`0 violations for 16 seeds`) and run it in CI. It is
  deterministic per seed, so a failure is a reproduction, not a flake.
- Add the six reproductions from section 1 as plain unit tests (each is 4–6 lines against a
  `Bridge` with a synthetic composition; `fuzz_states.make_comp` builds one).
- The invariant list in the fuzzer *is* the contract. When a screen gets a new flag or a new
  momentary action, it gets one line there. That is the whole discipline.

Order of fixing, by damage: T3 (overwrites the user's patterns and Resolume's Track params), T4
(Resolume left in a pressed state), T1 (a layer at 100 % on stage), T2, T5, T6.
