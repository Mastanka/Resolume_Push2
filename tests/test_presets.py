"""SEQ PRESETS: recipe grid, macros, drafts, preview, Apply / Revert / Re-fit / Load bank. Pure: a
Bridge with a synthetic composition (Bar Chaser on two layers, the rig fixture in tests/fixtures/rig).
python tests/test_presets.py"""
from __future__ import annotations

import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
import fuzz_states as F  # noqa: E402
import banks  # noqa: E402
from display import render  # noqa: E402
from sequencer import Sequencer  # noqa: E402

PAD = {"R1": (0, 0), "R2": (0, 1), "D1": (1, 0), "G3": (2, 2), "W4": (3, 3)}   # recipe grid: row = bank, column = energy


def tap(br, name):
    br.button(name, True)
    br.button(name, False)


def pad(br, ij, velocity=100):
    br.pad_pressed(ij, velocity)
    br.pad_released(ij)


def presets():
    """A bridge in SEQ PRESETS with a 6-pad rig and nothing queued for Resolume."""
    br = F.new_bridge()
    br.set_comp(F.make_comp(random.Random(1), 6, 8, {1: 1, 2: 2}))
    tap(br, "Note")
    tap(br, "Upper Row 3")
    with br.sender.lock:
        br.sender.params.clear()
    r, err = br.current_rig()
    assert r is not None and len(r.pads) == 6, err
    return br


def test_recipe_pad_makes_a_draft_for_the_selected_slot_and_previews_it():
    br = presets()
    pad(br, (4, 2))                                          # select P3 to edit: live pattern stays P1
    assert br.preset_slot == 2 and br.seq.current == 0
    pad(br, PAD["R1"])
    d = br.drafts[2]
    assert d["recipe"] == "R1" and d["macros"] == banks.MACROS and 1 <= d["seed"] < 1000000
    assert br.seq.is_empty(2), "a draft never touches the slot"
    assert br.preview.patterns[0].name == "R1 Pump" and br.preview.patterns[0].source["seed"] == d["seed"]
    pad(br, PAD["D1"])                                       # another recipe: same slot, macros carried over
    assert br.drafts[2]["recipe"] == "D1" and br.preview.patterns[0].name == "D1 909"


def test_macro_knobs_and_the_seed_knob_edit_the_draft():
    br = presets()
    pad(br, (4, 2)); pad(br, PAD["R1"])
    seed = br.drafts[2]["seed"]
    br.turn(0, 5)                                            # K1 Strobe +5 ticks
    assert abs(br.drafts[2]["macros"]["strobe"] - 0.6) < 1e-9
    br.button("Shift", True); br.turn(1, 2); br.button("Shift", False)
    assert abs(br.drafts[2]["macros"]["density"] - 0.51) < 1e-9, "Shift = fine step"
    br.turn(4, -40)
    assert br.drafts[2]["macros"]["accent"] == 0.0, "clamped"
    br.turn(5, 1)
    assert br.drafts[2]["seed"] != seed, "K6 = reshuffle"
    seed = br.drafts[2]["seed"]
    br.button("Shift", True); br.turn(5, 1); br.button("Shift", False)
    assert br.drafts[2]["seed"] == seed + 1, "Shift + K6 = next seed"
    assert br.preview.patterns[0].source["macros"]["strobe"] == 0.6 and br.preview.patterns[0].source["seed"] == seed + 1


def test_a_knob_on_a_preset_slot_without_a_draft_starts_one_from_its_source():
    br = presets()
    pad(br, (4, 2)); pad(br, PAD["R1"]); tap(br, "Lower Row 1")      # P3 = R1 Pump, applied
    assert 2 not in br.drafts and br.seq.patterns[2].source["recipe"] == "R1"
    br.turn(2, 3)                                            # Speed on the applied pattern: a draft appears
    assert br.drafts[2]["recipe"] == "R1" and br.drafts[2]["seed"] == br.seq.patterns[2].source["seed"]
    assert abs(br.drafts[2]["macros"]["speed"] - 0.56) < 1e-9
    assert br.seq.patterns[2].source["macros"]["speed"] == 0.5, "the slot itself is untouched until Apply"


def test_a_knob_on_an_own_pattern_does_nothing():
    br = presets()
    br.seq.patterns[3].tracks[0].steps["pad 1"] = {0: [1.0, None]}     # hand-made P4
    pad(br, (4, 3)); br.turn(0, 5)
    assert 3 not in br.drafts and "own pattern" in br.note[0]


def test_select_plus_knob_moves_the_macro_on_every_preset_slot():
    br = presets()
    for slot, rid in ((0, "R1"), (1, "D1")):
        pad(br, (4, slot)); pad(br, PAD[rid]); tap(br, "Lower Row 1")
    br.seq.patterns[3].tracks[0].steps["pad 1"] = {0: [1.0, None]}     # own P4 stays alone
    br.button("Select", True); br.turn(0, 10); br.button("Select", False)
    assert set(br.drafts) == {0, 1} and all(abs(d["macros"]["strobe"] - 0.7) < 1e-9 for d in br.drafts.values())
    assert "all" in br.note[0]


def test_apply_stores_the_draft_with_its_seed_and_macros_and_saves():
    br = presets()
    pad(br, (4, 2)); pad(br, PAD["R1"]); br.turn(0, 5)
    seed = br.drafts[2]["seed"]
    tap(br, "Lower Row 1")                                   # Apply
    p = br.seq.patterns[2]
    assert p.name == "R1 Pump" and p.source["seed"] == seed and p.source["macros"]["strobe"] == 0.6
    assert 2 not in br.drafts and br.seq.grid == "1/16"
    assert Sequencer(br.seq.path).patterns[2].source["macros"]["strobe"] == 0.6, "chases.yaml keeps the macros"
    assert br.preview.patterns[0].source["seed"] == seed, "the preview now shows the applied pattern"


def test_apply_with_the_same_recipe_keeps_the_users_track_level():
    br = presets()
    pad(br, (4, 2)); pad(br, PAD["R1"]); tap(br, "Lower Row 1")
    br.seq.patterns[2].tracks[1].level = 0.33
    br.turn(0, 10); tap(br, "Lower Row 1")                   # Strobe 0.7, same recipe
    assert br.seq.patterns[2].tracks[1].level == 0.33 and br.seq.patterns[2].source["macros"]["strobe"] == 0.7
    assert br.seq.patterns[2].tracks[0].envelope.release < 0.2, "Strobe above 0.5 shortens the release"


def test_apply_on_an_own_pattern_asks_first():
    br = presets()
    br.seq.patterns[3].tracks[0].steps["pad 1"] = {0: [1.0, None]}     # hand-made P4
    pad(br, (4, 3)); pad(br, PAD["G3"])
    tap(br, "Lower Row 1")
    assert br.confirm == {"kind": "overwrite", "slot": 3} and br.seq.patterns[3].source is None
    leds = br.button_colors()
    assert leds["Lower Row 7"] == "red" and leds["Lower Row 8"] == "green"
    tap(br, "Lower Row 7")                                   # NO
    assert br.confirm is None and br.seq.patterns[3].source is None and 3 in br.drafts
    tap(br, "Lower Row 1"); tap(br, "Lower Row 8")           # Apply, YES
    assert br.seq.patterns[3].name == "G3 Rolling" and 3 not in br.drafts


def test_apply_on_the_live_running_pattern_waits_for_the_bar():
    br = presets()
    br.seq.start(0.0)                                        # P1 is live and running
    pad(br, PAD["W4"])                                       # draft for the selected slot = P1
    tap(br, "Lower Row 1")
    assert br.seq.patterns[0].name == "P1" and br.seq.pending_apply is not None, "not before the bar"
    br.seq.tick(3.9)
    assert br.seq.patterns[0].name == "P1"
    br.seq.tick(4.0)
    assert br.seq.patterns[0].name == "W4 Tension" and br.seq.pending_apply is None


def test_revert_drops_the_draft():
    br = presets()
    pad(br, (4, 2)); pad(br, PAD["R1"]); br.turn(0, 5)
    tap(br, "Lower Row 2")
    assert 2 not in br.drafts and br.preview.patterns[0].name == "P3"


def test_load_bank_needs_two_presses_and_keeps_own_patterns():
    br = presets()
    br.seq.patterns[3].tracks[0].steps["pad 1"] = {0: [1.0, None]}     # own P4
    br.seq.current = 5
    tap(br, "Lower Row 4")
    assert br.load_armed is not None and br.seq.is_empty(0), "first press only arms"
    tap(br, "Lower Row 4")
    names = [p.name for p in br.seq.patterns]
    assert names[0] == "R1 Pump" and names[15] == "W4 Tension" and names[3] == "P4", names
    assert br.seq.current == 5 and not br.drafts and br.load_armed is None
    assert "P4" in br.note[0], "the operator is told which slots were kept"


def test_refit_button_refits_the_mismatched_patterns():
    br = presets()
    pad(br, (4, 2)); pad(br, PAD["R1"]); tap(br, "Lower Row 1")
    br.seq.patterns[2].source["rig"] = "1S 2D 3S 4D"          # made for another rig
    br.rig_sig_seen = None
    br.check_rig()
    assert br.refit_offer and br.button_colors()["Lower Row 3"] in ("white", "black")
    tap(br, "Lower Row 3")
    r, _ = br.current_rig()
    assert br.seq.patterns[2].source["rig"] == r.signature() and not br.refit_offer


def test_preview_plays_only_in_presets_and_never_reaches_the_engine():
    br = presets()
    pad(br, (4, 2)); pad(br, PAD["R1"])
    with br.sender.lock:
        br.sender.params.clear()
    lit = False
    for k in range(400):                                     # 4 beats at 100 ticks per beat
        br.preview_tick(k / 100)
        lit = lit or any(v > 0 for v in br.preview_levels.values())
    assert br.preview.running and lit, "the draft must sound in the preview"
    assert not br.sender.params and not br.seq.levels, "nothing goes to Resolume or the live sequencer"
    tap(br, "Upper Row 1")                                   # leave PRESETS
    br.preview_tick(4.1)
    assert not br.preview.running and not br.preview_levels and not br.preview.voices


def test_pattern_pad_selects_the_slot_and_the_same_pad_again_switches_live():
    br = presets()
    pad(br, (4, 2))
    assert br.preset_slot == 2 and br.seq.current == 0
    pad(br, (4, 2))
    assert br.seq.current == 2, "second press = the normal pattern switch"
    br.button("Shift", True); pad(br, (4, 1)); br.button("Shift", False)
    assert br.preset_slot == 9


def test_presets_leds():
    br = presets()
    pad(br, (4, 2))
    g = br.pad_colors()
    assert g[(4, 2)] == "white" and g[(4, 0)] == "S_orange", "selected slot white, live slot orange"
    assert g[(0, 0)] == "dark_gray" and g[(0, 4)] == "black", "recipe grid on the left, nothing on the right"
    assert br.button_colors()["Lower Row 1"] == "black", "nothing to apply yet"
    pad(br, PAD["R2"])
    g = br.pad_colors()
    assert g[(0, 1)] == "white" and g[(0, 0)] == "dark_gray"
    leds = br.button_colors()
    assert leds["Lower Row 1"] == "green" and leds["Lower Row 2"] == "dark_gray"
    tap(br, "Lower Row 1")
    assert br.pad_colors()[(0, 1)] == "light_gray", "the slot's applied recipe"
    tap(br, "Lower Row 4")
    assert br.button_colors()["Lower Row 4"] in ("white", "black"), "armed Load bank blinks"


def test_presets_snapshot_and_display():
    br = presets()
    pad(br, (4, 2)); pad(br, PAD["R1"]); br.turn(0, 5)
    br.preview_tick(0.0)
    pr = br.snapshot()["seq"]["presets"]
    assert len(pr["knobs"]) == 8 and pr["knobs"][0][0] == "Strobe" and pr["knobs"][0][1] == "0.60"
    assert pr["knobs"][5][0] == "Seed" and pr["knobs"][6][1] == "Techno"
    assert pr["labels"][:4] == ["Apply", "Revert", "Re-fit", "Load bank"]
    assert len(pr["strip"]) == 6 and all(role in ("D", "S") for role, _lv in pr["strip"])
    assert "P3" in pr["title"] and "R1 Pump" in pr["title"] and "draft" in pr["title"]
    frame, _ = render(br.snapshot())
    assert frame.shape == (960, 160)
    br.seq.patterns[3].tracks[0].steps["pad 1"] = {0: [1.0, None]}
    pad(br, (4, 3))
    pr = br.snapshot()["seq"]["presets"]
    assert "own pattern" in pr["title"] and pr["knobs"][5][1] == "—"
    render(br.snapshot())


def test_old_files_without_macros_still_load_and_apply():
    br = presets()
    pad(br, (4, 2)); pad(br, PAD["R1"]); tap(br, "Lower Row 1")
    del br.seq.patterns[2].source["macros"]
    br.turn(1, -5); tap(br, "Lower Row 1")
    assert br.seq.patterns[2].source["macros"]["density"] == 0.4 and br.seq.patterns[2].source["macros"]["strobe"] == 0.5


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
