"""Stuck-state and data-loss bugs, reproduced against a Bridge with a synthetic composition (no
Push, no Resolume, no mock). See docs/2026-09-27-state-isolation.md.   python tests/test_states.py"""
from __future__ import annotations

import random
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
import fuzz_states as F  # noqa: E402  (synthetic composition, REST stub, virtual clock)
import push_resolume_bridge as B  # noqa: E402
import banks  # noqa: E402
from chaser_engine import PluginEngine  # noqa: E402
from rig import rig_from_roles  # noqa: E402
from sequencer import Sequencer  # noqa: E402


def fresh(n_layers=12, n_cols=8):
    br = F.new_bridge()
    br.set_comp(F.make_comp(random.Random(1), n_layers, n_cols, {1: 1, 2: 2}))
    drain(br)
    return br


def drain(br):
    """(call name, args) queued for Resolume since the last drain, plus the coalesced param PUTs."""
    calls = []
    while not br.sender.triggers.empty():
        fn, args = br.sender.triggers.get_nowait()
        calls.append((fn.__name__, args))
    with br.sender.lock:
        params, br.sender.params = br.sender.params, {}
    return calls, params


def tap(br, name):
    br.button(name, True)
    br.button(name, False)


# ---- T1 flash: the button, not the layer, owns the flash ------------------------------------- #
def test_flash_restores_the_layer_after_a_scroll():
    br = fresh()
    pid = B.master_param(br.layer_json(1))["id"]
    br.button("1/4", True)                                    # bottom row = layer 1 → 100 %
    tap(br, "Up")                                             # rows shift by one layer
    br.button("1/4", False)
    _, params = drain(br)
    assert not br.flash, br.flash
    assert params.get(pid) == {"value": 0.8}, "layer 1 must go back to 0.8, not stay at 100 %"


def test_flash_released_in_the_seq_view():
    br = fresh()
    pid = B.master_param(br.layer_json(1))["id"]
    br.button("1/4", True)
    tap(br, "Note")
    br.button("1/4", False)
    _, params = drain(br)
    assert not br.flash and params.get(pid) == {"value": 0.8}


# ---- T2 / T3 modifiers: a flag is true only while the button is down, in every view ------------ #
def test_play_released_in_the_seq_view_does_not_stick():
    br = fresh()
    br.button("Play", True)
    tap(br, "Note")
    br.button("Play", False)
    tap(br, "Session")
    assert not br.play_held
    br.pad_pressed((7, 0), 100)
    calls, _ = drain(br)
    assert not [c for c in calls if c[0] == "connect_clip"], "a plain pad press must not launch"


def test_seq_modifiers_released_in_the_clip_view_do_not_stick():
    for name, flag in (("Delete", "delete_held"), ("Browse", "browse_held"), ("Fixed Length", "fixed_len_held")):
        br = fresh()
        tap(br, "Note")
        br.button(name, True)
        tap(br, "Session")
        br.button(name, False)
        assert not getattr(br, flag), f"{flag} stuck after a release in the clip view"
    br = fresh()
    tap(br, "Note")
    br.seq._steps("pad 1")[3] = [1.0, None]
    br.button("Delete", True); tap(br, "Session"); br.button("Delete", False); tap(br, "Note")
    br.pad_pressed((0, 5), 100); br.pad_released((0, 5))
    assert 5 in br.seq._steps("pad 1") and 3 in br.seq._steps("pad 1"), "a plain step press must toggle, not delete"


def test_browse_pressed_outside_seq_does_not_join_a_track_on_release():
    br = fresh()
    br.button("Browse", True)
    tap(br, "Note")
    joined = []
    br.join_track = lambda t: joined.append(t)
    br.button("Browse", False)
    assert not joined and not br.browse_held


# ---- T4 held clip / column: the pad or button owns the press --------------------------------- #
def test_clip_release_is_sent_after_a_scroll():
    br = fresh()
    br.button("Play", True)
    br.pad_pressed((7, 0), 100)                               # layer 1, column 1 down
    tap(br, "Up")
    br.pad_released((7, 0))
    br.button("Play", False)
    calls, _ = drain(br)
    assert ("connect_clip", (1, 1, True)) in calls and ("connect_clip", (1, 1, False)) in calls, calls
    assert not br.pressed


def test_clip_release_is_sent_from_the_seq_view():
    br = fresh()
    br.button("Play", True)
    br.pad_pressed((7, 0), 100)
    tap(br, "Note")
    br.pad_released((7, 0))
    calls, _ = drain(br)
    assert ("connect_clip", (1, 1, False)) in calls and not br.pressed


def test_column_release_is_sent_after_a_scroll_and_from_seq():
    br = fresh(12, 16)                                         # 16 columns: Right can scroll
    br.button("Play", True)
    br.button("Lower Row 1", True)                            # column 1 down
    tap(br, "Right")
    br.button("Lower Row 1", False)
    calls, _ = drain(br)
    assert ("connect_column", (1, False)) in calls and not br.col_pressed, (calls, br.col_pressed)
    br.button("Lower Row 2", True)
    tap(br, "Note")
    br.button("Lower Row 2", False)
    calls, _ = drain(br)
    assert ("connect_column", (3, False)) in calls and not br.col_pressed, (calls, br.col_pressed)


# ---- T5 a smaller composition loads (deck switch) ------------------------------------------- #
def test_deck_switch_keeps_the_grid_and_selection_inside_the_composition():
    br = fresh(12, 8)
    for _ in range(4):
        tap(br, "Up")
    br.pad_pressed((7, 2), 100); br.pad_released((7, 2))
    assert br.layer_offset == 4 and br.sel == (5, 3)
    br.set_comp(F.make_comp(random.Random(2), 3, 4, {}))
    assert br.layer_offset == 0 and br.col_offset == 0
    assert br.layer_json(br.sel[0]) is not None and br.clip_json(*br.sel) is not None, br.sel
    assert any(c != "black" for c in br.pad_colors().values()), "the new deck must be visible at once"


# ---- T6 held step across a menu change, held pad across a view change ------------------------- #
def test_step_release_reaches_held_steps_in_mapping():
    br = fresh()
    tap(br, "Note")
    br.pad_pressed((0, 4), 100)
    tap(br, "Upper Row 4")                                    # MAPPING
    br.pad_released((0, 4))
    tap(br, "Upper Row 1")
    assert not br.held_steps, br.held_steps


def test_view_switch_releases_a_held_pad_voice():
    br = fresh()
    tap(br, "Note")
    br.pad_pressed((7, 0), 100)                               # pad 1 held: a voice with an open gate
    assert any(v.gate is None for v in br.seq.voices.values())
    tap(br, "Session")
    assert not br.held_bars and all(v.gate is not None for v in br.seq.voices.values()), "the bar would stay lit"


# ---- lost releases: a MIDI reconnect lets everything go --------------------------------------- #
def test_reconnect_releases_everything():
    br = fresh()
    pid = B.master_param(br.layer_json(1))["id"]
    br.button("Play", True); br.pad_pressed((7, 0), 100); br.button("1/4", True); br.button("Delete", True)
    br.button("Lower Row 2", True); br.touch(3)
    drain(br)
    br.release_all()
    calls, params = drain(br)
    assert not (br.play_held or br.delete_held or br.pressed or br.col_pressed or br.flash or br.held)
    assert br.touched is None
    assert ("connect_clip", (1, 1, False)) in calls and ("connect_column", (2, False)) in calls, calls
    assert params.get(pid) == {"value": 0.8}


# ---- B1 the chaser engine never drops a level ------------------------------------------------- #
def test_engine_delays_instead_of_dropping_a_fast_level():
    comp = F.make_comp(random.Random(3), 1, 1, {1: 1})
    sent = []
    eng = PluginEngine(None, lambda: comp, lambda: None, lambda pid, v: sent.append(v))
    eng.set_level(0, "pad 1", 1.0, now=100.000)              # flash
    eng.set_level(0, "pad 1", 0.0, now=100.112)              # release finished
    eng.set_level(0, "pad 1", 1.0, now=100.125)              # next step 13 ms later: too soon to send
    eng.flush(now=100.126)
    assert sent == [1.0, 0.0], sent
    eng.flush(now=100.140)                                    # window over: the value goes out
    assert sent == [1.0, 0.0, 1.0], "the second flash must not be lost"


def test_engine_pending_keeps_only_the_newest_value():
    comp = F.make_comp(random.Random(3), 1, 1, {1: 1})
    sent = []
    eng = PluginEngine(None, lambda: comp, lambda: None, lambda pid, v: sent.append(v))
    eng.set_level(0, "pad 1", 1.0, now=100.000)
    eng.set_level(0, "pad 1", 0.4, now=100.005)
    eng.set_level(0, "pad 1", 0.6, now=100.010)
    eng.flush(now=100.030)
    assert sent == [1.0, round(0.6 * 255) / 255], sent


# ---- B2 chases.yaml is not written on every knob tick / step press ----------------------------- #
def test_knob_ticks_and_step_presses_write_the_file_later():
    br = fresh()
    path = Path(br.seq.path)
    path.unlink(missing_ok=True)                              # the pad-mapping sync wrote it once at start
    tap(br, "Note")
    for _ in range(20):
        br.turn(0, 1)                                         # Attack knob in ENVELOPE
    br.pad_pressed((0, 0), 100); br.pad_released((0, 0))
    assert not path.exists(), "20 knob ticks and a step press must not write 21 files"
    br.seq.flush(force=True)
    assert path.exists() and "patterns" in path.read_text()


def test_deferred_save_flushes_after_the_quiet_time():
    seq = Sequencer(Path(tempfile.mkdtemp()) / "chases.yaml")
    seq.set_length(12)
    seq.save_later(now=100.0)
    seq.flush(now=100.1)
    assert not seq.path.exists()
    seq.flush(now=100.7)
    assert Sequencer(seq.path).patterns[0].length == 12


# ---- B4 a step edited on the Push clears the preset's source --------------------------------- #
def test_step_edit_on_the_push_clears_the_preset_source():
    br = fresh()
    tap(br, "Note")
    br.seq.store_pattern(2, banks.realize("R1", rig_from_roles("S D S S D S S D S"), 4711))
    br.seq.current = 2
    br.pad_pressed((0, 0), 100); br.pad_released((0, 0))
    assert br.seq.pattern.source is None, "toggling a step must make it the user's pattern"
    br.seq.store_pattern(3, banks.realize("R2", rig_from_roles("S D S S D S S D S"), 4712))
    br.seq.current = 3
    br.button("Delete", True); br.pad_pressed((0, 0), 100); br.pad_released((0, 0)); br.button("Delete", False)
    assert br.seq.pattern.source is None, "Delete + step is an edit too"


# ---- the fuzzer as a regression test ------------------------------------------------------------ #
def test_random_walk_finds_no_stuck_state():
    problems, crashes = {}, {}
    for seed in range(4):
        F.run(seed, 600, problems, crashes)
    problems.pop("_count", None)
    assert not crashes, crashes
    assert not problems, "\n".join(f"{k}: {v[0]}" for k, v in problems.items())


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            try:
                fn()
                print("pass", name)
            except Exception as e:
                failed += 1
                print("FAIL", name, "→", f"{type(e).__name__}: {e}")
    print("OK" if not failed else f"{failed} FAILED")
    sys.exit(1 if failed else 0)
