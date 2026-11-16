"""Scenario tests: what happens in Arena while the bridge runs. Each test builds a simulated Arena
(tests/sim_arena.py) and a real Bridge on it, does what a user does on the Push and in Arena, lets time
pass, and checks what reaches Arena. Pure: no Push, no network.   python tests/test_scenarios.py

Scenarios: A selection and layers · B Bar Chaser effects (add, remove, Track, folding) · C composition
switch / new composition / blackout / held controls · D fixtures and the pad mapping · E presets ·
F network and subscriptions."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
import sim_arena as S  # noqa: E402  (installs the virtual clock first)
from display import render  # noqa: E402

UN = S.UN
MENUS = ["Upper Row 1", "Upper Row 2", "Upper Row 3", "Upper Row 6", "Upper Row 7"]
SEQ_MENUS = ["Upper Row 1", "Upper Row 2", "Upper Row 3", "Upper Row 4"]


def tap(br, name):
    br.button(name, True)
    br.button(name, False)


def run(a, br, seconds=1.0):
    S.settle(a, br, seconds, max(1, int(seconds * 100)))


def hold_pad(br, track, pad="pad 2"):
    """A held voice: the pad stays lit on `track` until released (like holding a pad on the Push)."""
    with br.lock:
        br.seq.trigger(track, pad, 1.0, None, br.beat_time())


def release_pad(br, track, pad="pad 2"):
    with br.lock:
        br.seq.release(track, pad, br.beat_time())


def level(a, fx, pad=2):
    return fx["params"][f"Level {pad}"]["value"]


def fx_on(a, L):
    return next(fx for l_, c, fx in a.chasers() if l_ == L and c is None)


def every_screen(br):
    """Every menu of both views and the overlays render, and the LEDs compute, without an error."""
    for view in ("Session", "Note"):
        tap(br, view)
        for m in (MENUS if view == "Session" else SEQ_MENUS):
            tap(br, m)
            br.button_colors(); br.pad_colors(); render(br.snapshot())
            for k in range(8):
                br.turn(k, 1)
            br.pad_pressed((7, 0)); br.pad_released((7, 0))
            br.pad_pressed((4, 1)); br.pad_released((4, 1))
        for ov in ("Mix", "Mute", "Solo"):
            tap(br, ov)
            br.button_colors(); br.pad_colors(); render(br.snapshot())
            tap(br, ov)
    tap(br, "Session")


def mapping_consistent(a, br):
    """Every Bar Chaser shows the remembered mapping, as far as its dropdown offers the fixture."""
    want = br.seq.pad_config or []
    for _, _, fx in a.chasers():
        for k, v in enumerate(want):
            opts = fx["params"][f"Pad {k + 1}"]["options"]
            if v in opts:
                assert fx["params"][f"Pad {k + 1}"]["value"] == v, (k + 1, v, fx["params"][f"Pad {k + 1}"]["value"])


# ---- A: selection and layers ------------------------------------------------------------------ #
def test_a1_every_screen_on_layers_with_and_without_bar_chaser():
    a = S.Arena(layers=4, chasers={2: 1, 3: 3})
    br = S.make_bridge(a)
    for L in (1, 2, 3, 4):
        br.sel = (L, 1)
        every_screen(br)
    assert br.engine.layer_name(0) == "Layer 2" and br.engine.layer_name(2) == "Layer 3"


def test_a2_the_selected_layer_is_deleted():
    a = S.Arena(layers=4, chasers={2: 1})
    br = S.make_bridge(a)
    br.sel = (4, 3)
    a.user_remove_layer(4); a.user_remove_layer(3)
    a.deliver_comp(br)
    assert br.layer_json(br.sel[0]) is not None and br.clip_json(*br.sel) is not None, br.sel
    every_screen(br)


def test_a3_layers_reordered_levels_follow_the_effect():
    a = S.Arena(layers=3, chasers={2: 1})
    br = S.make_bridge(a)
    fx = fx_on(a, 2)
    a.user_move_layer(2, 3)                                   # the Bar Chaser layer moves to the top
    a.deliver_comp(br)
    assert br.engine.layer_name(0) == "Layer 2" and br.engine.layers_of(0) == [3]
    hold_pad(br, 0); run(a, br, 0.3)
    assert level(a, fx) == 1.0


# ---- B: Bar Chaser effects ------------------------------------------------------------------- #
def test_b1_bar_chaser_removed_while_playing():
    a = S.Arena(layers=3, chasers={2: 1, 3: 3})
    br = S.make_bridge(a)
    t3 = fx_on(a, 3)
    hold_pad(br, 0); run(a, br, 0.3)
    a.process(br)
    a.user_remove_chaser(2)
    a.deliver_comp(br)
    n0 = len(a.unknown)
    run(a, br, 1.0)
    assert len(a.unknown) == n0, a.unknown[n0:]
    assert level(a, t3) == 1.0, "T1 folds onto the remaining (T3) layer"
    every_screen(br)


def test_b2_bar_chaser_added_in_arena_gets_the_mapping():
    a = S.Arena(layers=3, chasers={2: 1})
    br = S.make_bridge(a)
    a.user_set_pad(fx_on(a, 2), 0, UN)                        # the user's mapping: pad 1 empty
    run(a, br, 3.0)
    assert br.seq.pad_config[0] == UN
    new = a.user_add_chaser(3, track=2)
    a.deliver_comp(br)
    run(a, br, 3.0)
    assert a.pads(new)[0] == UN, "a new Bar Chaser gets the remembered mapping"
    mapping_consistent(a, br)


def test_b3_shift_note_adds_bar_chaser_on_the_selected_layer():
    a = S.Arena(layers=3)
    br = S.make_bridge(a)
    br.sel = (1, 1)
    br.button("Shift", True); tap(br, "Note"); br.button("Shift", False)
    import threading
    for t in threading.enumerate():                           # add_chaser runs in a worker thread
        if t is not threading.current_thread() and t.daemon:
            t.join(timeout=2)
    run(a, br, 3.0)
    assert [l_ for l_, _, _ in a.chasers()] == [1] and br.engine.ready(0)


def test_b4_track_changed_in_arena_routes_at_once():
    a = S.Arena(layers=3, chasers={2: 1, 3: 3})
    br = S.make_bridge(a)
    br.sel = (3, 1)                                           # layer 3 selected: its params are subscribed
    a.user_set_track(fx_on(a, 3), 2)
    a.deliver_params(br)                                      # only a parameter_update, no new composition
    assert br.engine.ready(1) and not br.engine.ready(2), "the bridge must see T2 now"


def test_b5_track_changed_while_a_pad_is_lit_leaves_no_bar_lit():
    a = S.Arena(layers=3, chasers={2: 1, 3: 2})
    br = S.make_bridge(a)
    t1, t2 = fx_on(a, 2), fx_on(a, 3)
    hold_pad(br, 1); run(a, br, 0.3)
    assert level(a, t2) == 1.0 and level(a, t1) == 0.0
    a.user_set_track(t2, 3)                                   # layer 3 now answers to T3
    a.deliver_comp(br)
    run(a, br, 0.5)
    assert level(a, t2) == 0.0, "layer 3 must not stay lit"
    assert level(a, t1) == 1.0, "T2 now folds onto T1's layer"
    release_pad(br, 1); run(a, br, 1.0)
    assert not a.lit(), a.lit()


def test_b6_folded_track_gets_its_own_layer_while_lit():
    a = S.Arena(layers=3, chasers={2: 1})
    br = S.make_bridge(a)
    t1 = fx_on(a, 2)
    hold_pad(br, 1); run(a, br, 0.3)
    assert level(a, t1) == 1.0, "T2 plays on T1's layer"
    t2 = a.user_add_chaser(3, track=2)
    a.deliver_comp(br)
    run(a, br, 0.5)
    assert level(a, t1) == 0.0 and level(a, t2) == 1.0, (level(a, t1), level(a, t2))


def test_b7_fold_off_track_without_layer_sends_nothing_and_leaves_nothing_lit():
    a = S.Arena(layers=3, chasers={2: 1, 3: 2})
    br = S.make_bridge(a, fold_tracks=False)
    t2 = fx_on(a, 3)
    hold_pad(br, 1); run(a, br, 0.3)
    assert level(a, t2) == 1.0
    a.user_set_track(t2, 3); a.deliver_comp(br); run(a, br, 0.5)
    assert not a.lit(), a.lit()


def test_b8_bar_chaser_on_a_clip_counts():
    a = S.Arena(layers=2)
    br = S.make_bridge(a)
    fx = a.user_add_chaser(1, track=1, clip=1)
    a.deliver_comp(br)
    assert br.engine.ready(0)
    hold_pad(br, 0); run(a, br, 0.3)
    assert level(a, fx) == 1.0


# ---- C: compositions -------------------------------------------------------------------------- #
def test_c1_another_composition_opened_while_playing():
    a = S.Arena(layers=3, chasers={2: 1, 3: 3})
    br = S.make_bridge(a)
    with br.lock:
        br.seq.patterns[0].tracks[0].steps["pad 2"] = {s: [1.0, None] for s in range(0, 32, 2)}
        br.seq.start(br.beat_time())
    run(a, br, 1.0)
    a.process(br)
    a.user_new_composition(layers=4, chasers={1: 1, 4: 3})
    t1 = fx_on(a, 1)
    a.user_set_pad(t1, 0, UN)                                 # this composition's own mapping
    a.deliver_comp(br)
    n0 = len(a.unknown)
    run(a, br, 3.0)
    assert len(a.unknown) == n0, ("writes to the old composition", a.unknown[n0:])
    assert br.seq.pad_config[0] == UN, "the opened composition's T1 mapping is taken"
    mapping_consistent(a, br)
    assert any(pid in a.levels() for pid, _ in a.writes[-20:]), "levels reach the new instances"
    every_screen(br)


def test_c2_new_empty_composition():
    a = S.Arena(layers=3, chasers={2: 1})
    br = S.make_bridge(a)
    mapping = list(br.seq.pad_config)
    a.user_new_composition(layers=0)
    a.deliver_comp(br)
    every_screen(br)
    with br.lock:
        br.seq.patterns[0].tracks[0].steps["pad 2"] = {0: [1.0, None]}
        br.seq.start(br.beat_time())
    run(a, br, 1.0)
    assert br.seq.pad_config == mapping, "an empty composition keeps the remembered mapping"
    a.user_add_layer(); a.user_add_chaser(1, track=1)
    a.deliver_comp(br); run(a, br, 3.0)
    mapping_consistent(a, br)


def test_c3_composition_without_bar_chaser():
    a = S.Arena(layers=3)
    br = S.make_bridge(a)
    tap(br, "Note")
    assert "no Bar Chaser" in br.snapshot()["seq"]["warning"]
    with br.lock:
        br.seq.patterns[0].tracks[0].steps["pad 2"] = {0: [1.0, None]}
        br.seq.start(br.beat_time())
    run(a, br, 1.0)
    assert not a.writes and not a.unknown
    every_screen(br)


def test_c4_blackout_survives_opening_another_composition():
    a = S.Arena(layers=2)
    br = S.make_bridge(a)
    tap(br, "Stop"); run(a, br, 0.5)
    assert a.comp["master"]["value"] == 0.0
    a.user_new_composition(layers=2)                          # opens at master 1.0
    a.deliver_comp(br); run(a, br, 2.0)
    assert br.blackout is not None and a.comp["master"]["value"] == 0.0, "still blacked out"
    tap(br, "Stop"); run(a, br, 2.0)
    assert br.blackout is None and a.comp["master"]["value"] == 1.0


def test_c5_flash_and_held_clip_across_a_composition_switch():
    a = S.Arena(layers=3, cols=4)
    br = S.make_bridge(a)
    br.button("1/4", True)                                    # flash layer 1
    br.button("Play", True); br.pad_pressed((7, 0))           # hold clip 1.1
    run(a, br, 0.2)
    a.user_new_composition(layers=3)
    a.deliver_comp(br)
    n_trig, n_unknown = len(a.triggers), len(a.unknown)
    br.pad_released((7, 0)); br.button("Play", False); br.button("1/4", False)
    run(a, br, 0.3)
    assert len(a.unknown) == n_unknown, a.unknown[n_unknown:]
    assert not [t for t in a.triggers[n_trig:] if t[0] == "clip"], "no release into the new composition"
    assert not br.flash and not br.pressed


def test_c6_arena_offline_then_back_with_another_composition():
    a = S.Arena(layers=3, chasers={2: 1})
    br = S.make_bridge(a)
    with br.lock:
        br.online = False
    every_screen(br)
    a.user_new_composition(layers=2, chasers={2: 3})
    a.deliver_comp(br); run(a, br, 3.0)
    mapping_consistent(a, br)
    every_screen(br)


# ---- D: fixtures and the pad mapping ------------------------------------------------------------ #
def test_d1_fewer_fixtures_after_a_reload():
    a = S.Arena(layers=3, chasers={2: 1, 3: 3})
    br = S.make_bridge(a)
    tap(br, "Note"); tap(br, "Upper Row 4")
    br.button("Shift", True)
    for ij in ((7, 4), (7, 5)):                               # kick bars: pads 5 and 6
        br.pad_pressed(ij); br.pad_released(ij)
    br.button("Shift", False)
    assert br.seq.kick_pads == [5, 6]
    a.user_set_fixtures(S.fixtures(4))
    a.write_preset(br.preset_folder)
    S.advance(2.0)
    a.deliver_comp(br); run(a, br, 3.0)
    assert br.seq.pad_config[4] == UN and br.seq.pad_config[3] == S.fixtures(4)[3]
    r, err = br.current_rig()
    assert r is not None and r.pads == [1, 2, 3, 4] and r.source != "push", (err, r and r.signature())
    every_screen(br)


def test_d2_a_broken_preset_does_not_wipe_the_mapping():
    a = S.Arena(layers=3, chasers={2: 1, 3: 3})
    br = S.make_bridge(a)
    a.user_set_pad(fx_on(a, 2), 0, UN)                         # the user's own mapping, not the default
    run(a, br, 3.0)
    mapping = list(br.seq.pad_config)
    assert mapping[0] == UN
    a.user_break_preset()
    a.deliver_comp(br); run(a, br, 3.0)
    assert br.seq.pad_config == mapping, "only '—' on offer must not become the mapping"
    every_screen(br)
    a.user_set_fixtures(S.fixtures(6))                         # readable again: the plugin's default pads
    a.deliver_comp(br); run(a, br, 3.0)
    assert br.seq.pad_config == mapping
    mapping_consistent(a, br)


def test_d3_broken_preset_in_an_opened_composition_keeps_the_mapping():
    a = S.Arena(layers=3, chasers={2: 1})
    br = S.make_bridge(a)
    mapping = list(br.seq.pad_config)
    a.user_new_composition(layers=2, chasers={1: 1})
    a.user_break_preset()
    a.deliver_comp(br); run(a, br, 3.0)
    assert br.seq.pad_config == mapping


def test_d4_pad_changed_in_arena_on_any_instance_becomes_the_mapping():
    a = S.Arena(layers=3, chasers={2: 1, 3: 3})
    br = S.make_bridge(a)
    run(a, br, 3.0)
    a.user_set_pad(fx_on(a, 3), 1, S.fixtures(6)[5])
    a.deliver_comp(br); run(a, br, 3.0)
    assert br.seq.pad_config[1] == S.fixtures(6)[5]
    mapping_consistent(a, br)


def test_d5_mapping_on_the_push_when_one_instance_lacks_the_fixture():
    a = S.Arena(layers=3, chasers={2: 1, 3: 3})
    br = S.make_bridge(a)
    extra = "Lumiverse 9 / 1 - 423 141 RGB"
    fx_on(a, 2)["params"]["Pad 1"]["options"].append(extra)   # only the T1 instance offers it
    a.deliver_comp(br)
    tap(br, "Note"); tap(br, "Upper Row 4")
    assert br.engine.set_pad(0, extra)
    run(a, br, 3.0)
    every_screen(br)


def test_d6_geometry_from_a_newer_plugin():
    a = S.Arena(layers=3, chasers={2: 1}, lengths=[900, 460, 460, 900, 460, 460], geometry=True)
    br = S.make_bridge(a)
    for f in br.preset_folder.iterdir():
        f.unlink()                                             # no preset file on this machine
    br._rig_checked = (None, 0.0)
    r, err = br.current_rig()
    assert r is not None and r.D == [1, 4] and not r.virtual, (err, r and r.signature())


# ---- E: presets --------------------------------------------------------------------------------- #
def test_e1_draft_survives_a_composition_switch_and_applies_on_the_new_rig():
    a = S.Arena(layers=3, chasers={2: 1})
    br = S.make_bridge(a)
    tap(br, "Note"); tap(br, "Upper Row 3")
    br.pad_pressed((4, 2)); br.pad_released((4, 2))
    br.pad_pressed((0, 0)); br.pad_released((0, 0))           # draft R1 for P3
    run(a, br, 0.5)
    a.user_new_composition(layers=2, chasers={1: 1})
    a.deliver_comp(br); run(a, br, 1.0)
    tap(br, "Lower Row 1")                                    # Apply
    assert br.seq.patterns[2].name == "R1 Pump"
    render(br.snapshot())


def test_e2_load_bank_without_any_bar_chaser():
    a = S.Arena(layers=2)
    br = S.make_bridge(a)
    tap(br, "Note"); tap(br, "Upper Row 3")
    tap(br, "Lower Row 4"); tap(br, "Lower Row 4")
    render(br.snapshot()); br.pad_colors(); br.button_colors()


def test_e3_preview_follows_a_rig_change():
    a = S.Arena(layers=3, chasers={2: 1})
    br = S.make_bridge(a)
    tap(br, "Note"); tap(br, "Upper Row 3")
    br.pad_pressed((0, 0)); br.pad_released((0, 0))
    run(a, br, 1.0)
    a.user_set_fixtures(S.fixtures(4))                         # pads 5 and 6 lose their fixture
    a.write_preset(br.preset_folder)
    S.advance(2.0)
    a.deliver_comp(br); run(a, br, 2.0)
    pr = br.snapshot()["seq"]["presets"]
    r = br.current_rig()[0]
    assert len(pr["strip"]) == len(r.pads) == 4
    assert br.preview.patterns[0].source["rig"] == r.signature(), "the draft is previewed on the new rig"


# ---- G: tempo while playing ---------------------------------------------------------------------- #
def playing(seconds=30.0):
    """A pattern on T1 that has played for a while: the beat clock is far from 0."""
    a = S.Arena(layers=3, chasers={2: 1})
    br = S.make_bridge(a)
    run(a, br, seconds)                                       # the show has been on for a while
    with br.lock:
        br.seq.patterns[0].tracks[0].steps["pad 2"] = {s: [1.0, None] for s in range(32)}
        br.seq.start(br.beat_time())                          # then the pattern starts
    run(a, br, 2.0)
    return a, br


def positions(a, br, seconds):
    seen = []
    for _ in range(int(seconds * 100)):
        S.advance(0.01); br.seq_step(); a.process(br)
        seen.append(br.seq.position(br.beat_time()))
    return seen


def test_g1_tap_tempo_while_playing_keeps_the_sequencer_moving():
    a, br = playing()
    tap(br, "Tap Tempo")
    seen = positions(a, br, 2.0)
    assert len(set(seen)) > 8, f"the pattern must keep stepping after a tap: {sorted(set(seen))}"
    bt0 = br.beat_time()
    with br.lock:
        assert all(v.start <= bt0 + 1e-6 for v in br.seq.voices.values()), "no voice may start in the future"


def test_g2_resync_moves_the_beat_by_at_most_two_beats_and_lands_on_beat_one():
    a, br = playing()
    before = br.beat_time()
    br.button("Shift", True); tap(br, "Tap Tempo"); br.button("Shift", False)
    after = br.beat_time()
    assert abs(after - before) <= 2.0 + 1e-6, (before, after)
    assert abs(after % 4) < 1e-6, "resync = beat 1 now"
    assert len(set(positions(a, br, 2.0))) > 8


def test_g3_bpm_change_keeps_the_beat_clock_continuous():
    a, br = playing()
    before = br.beat_time()
    a.comp["tempocontroller"]["tempo"]["value"] = 60.0          # half the tempo in Arena
    a.changed.add(a.comp["tempocontroller"]["tempo"]["id"]); a.deliver_params(br)
    assert abs(br.beat_time() - before) < 0.05, (before, br.beat_time())
    assert len(set(positions(a, br, 3.0))) > 8
    with br.lock:
        br.seq.stop()
    run(a, br, 4.0)
    assert not a.lit(), a.lit()


def test_g4_a_backwards_clock_never_freezes_voices():
    from sequencer import Sequencer
    seq = Sequencer()
    seq.trigger(0, "pad 1", 1.0, 0.5, 100.0)
    seq.tick(100.1)
    out = {}
    for bt in [10.0 + i * 0.05 for i in range(100)]:          # the clock jumped back 90 beats
        out.update(seq.tick(bt))
    assert out.get((0, "pad 1")) == 0.0 and not seq.voices, "the flash must end"


def test_c7_play_plus_a_button_below_an_empty_column_sends_nothing():
    a = S.Arena(layers=2, cols=4)
    br = S.make_bridge(a)
    br.button("Play", True); tap(br, "Lower Row 6"); br.button("Play", False)   # column 6 of 4
    a.process(br)
    assert not [t for t in a.triggers if t[0] == "column"], a.triggers


# ---- F: network and subscriptions --------------------------------------------------------------- #
def test_f1_own_level_params_are_not_subscribed():
    a = S.Arena(layers=3, chasers={2: 1})
    br = S.make_bridge(a)
    br.sel = (2, 1)
    levels = set(a.levels())
    assert not levels & set(br.desired_ids()), "the bridge's own Level writes would echo back"


def test_f2_a_stale_composition_after_our_own_pad_write_does_not_undo_it():
    a = S.Arena(layers=3, chasers={2: 1, 3: 3})
    br = S.make_bridge(a)
    run(a, br, 3.0)
    stale = a.composition()                                   # fetched before our write arrives
    tap(br, "Note"); tap(br, "Upper Row 4")
    assert br.engine.set_pad(0, UN)
    br.set_comp(stale)                                        # the old snapshot lands after the write
    run(a, br, 3.0)
    assert br.seq.pad_config[0] == UN
    mapping_consistent(a, br)


# ---- Z: the structural fuzzer as a regression test --------------------------------------------- #
def test_z_structural_random_walk():
    import fuzz_arena
    problems, crashes = {}, {}
    for seed in range(4):
        fuzz_arena.run(seed, 600, problems, crashes)
    problems.pop("_count", None)
    assert not crashes, {k: v[:2] for k, v in crashes.items()}
    assert not problems, {k: v[0] for k, v in problems.items()}


if __name__ == "__main__":
    import traceback
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            try:
                fn()
                print("pass", name)
            except Exception as e:
                failed += 1
                print("FAIL", name, "→", f"{type(e).__name__}: {e}")
                if "-v" in sys.argv:
                    traceback.print_exc()
    print("OK" if not failed else f"{failed} FAILED")
    sys.exit(1 if failed else 0)
