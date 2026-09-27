"""Pure-logic tests for sequencer.py (no mock, no hardware).  python tests/test_sequencer.py → OK"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import sequencer as S  # noqa: E402

def test_envelope():
    e = S.Envelope(attack=1.0, decay=1.0, sustain=0.5, release=1.0)
    assert S.env_value(e, 0.0, None) == 0.0
    assert abs(S.env_value(e, 0.5, None) - 0.5) < 1e-9          # attack ramp
    assert abs(S.env_value(e, 1.0, None) - 1.0) < 1e-9
    assert abs(S.env_value(e, 1.5, None) - 0.75) < 1e-9         # decay towards sustain
    assert abs(S.env_value(e, 3.0, None) - 0.5) < 1e-9          # sustain while held
    assert abs(S.env_value(e, 3.5, gate=3.0) - 0.25) < 1e-9     # release from the value at the gate
    assert S.env_value(e, 4.0, gate=3.0) is None                # finished
    assert abs(S.env_value(e, 0.6, gate=0.5) - 0.5 * 0.9) < 1e-9   # gate cuts the attack short
    snap = S.Envelope()                                         # defaults: instant on, 0.1 beat release
    assert S.env_value(snap, 0.0, None) == 1.0
    assert S.env_value(snap, 0.25, gate=0.25) == 1.0 and S.env_value(snap, 0.36, gate=0.25) is None
    zero = S.Envelope(release=0.0)
    assert S.env_value(zero, 0.3, gate=0.25) is None
    # retrigger from a current value: attack starts at from_level
    assert abs(S.env_value(e, 0.5, None, from_level=0.5) - 0.75) < 1e-9
    v = S.Voice(track=0, bar="Bar A", start=10.0, level=0.8, gate=None, env=e)
    assert abs(v.env_value(10.5) - 0.5) < 1e-9


def test_patterns_and_yaml():
    import tempfile
    path = Path(tempfile.mkdtemp()) / "chases.yaml"
    sq = S.Sequencer(path)
    assert len(sq.patterns) == 16 and sq.pattern.name == "P1" and sq.pattern.length == 16
    assert sq.toggle_step("Bar A", 0, level=0.8) is True
    assert sq.toggle_step("Bar A", 4) is True
    assert sq.toggle_step("Bar A", 0) is False                       # toggled off
    assert sq.pattern.tracks[0].steps == {"Bar A": {4: [1.0, None]}}
    sq.track = 1
    sq.toggle_step("Bar B", 2, level=0.5, gate=0.9)
    sq.set_step_values("Bar B", [2], level=0.6)
    assert sq.pattern.tracks[1].steps["Bar B"][2] == [0.6, 0.9]
    sq.pattern.tracks[1].envelope.attack = 0.5
    sq.set_length(8)
    sq.double_loop()
    assert sq.pattern.length == 16 and sq.pattern.tracks[0].steps["Bar A"] == {4: [1.0, None], 12: [1.0, None]}
    sq.set_direction("bounce")
    sq.copy_pattern(0, 3)
    assert sq.patterns[3].direction == "bounce" and sq.patterns[3].tracks[1].steps["Bar B"][2] == [0.6, 0.9]
    assert sq.patterns[3].tracks[1].envelope.attack == 0.5
    sq.clear_steps("Bar A", track=0)
    assert "Bar A" not in sq.pattern.tracks[0].steps
    sq.clear_pattern(3)
    assert sq.patterns[3].tracks[1].steps == {} and sq.patterns[3].direction == "forward"
    sq.pattern.tracks[0].texture = {"source": "Metaballs", "params": {"Grid": 20}}
    sq.save()
    sq2 = S.Sequencer(path)
    assert sq2.pattern.tracks[1].steps["Bar B"][2] == [0.6, 0.9]
    assert sq2.pattern.tracks[1].envelope.attack == 0.5 and sq2.pattern.direction == "bounce"
    assert sq2.pattern.tracks[0].texture == {"source": "Metaballs", "params": {"Grid": 20}}
    assert sq2.pattern.length == 16


def test_playback():
    sq = S.Sequencer()
    sq.grid = "1/4"                                     # 1 beat per step
    sq.set_length(4)
    sq.toggle_step("A", 0); sq.toggle_step("B", 2, level=0.5)
    sq.track = 1
    sq.pattern.tracks[1].envelope = S.Envelope(release=0.0)
    sq.pattern.tracks[1].gate = 1.0
    sq.toggle_step("A", 1)
    sq.start(4.3)                                        # aligned to the last bar boundary (4.0)
    assert sq.start_beat == 4.0
    out = sq.tick(4.3)
    assert out == {(0, "A"): 1.0}, out                   # step 0 fires for track 0, gate 0.5 beat
    assert sq.tick(4.4) == {}                            # nothing changed
    assert sq.tick(4.95) == {(0, "A"): 0.0}              # gate 0.5 + release 0.1 → off after 4.9; edge sent once
    out = sq.tick(5.1)
    assert out == {(1, "A"): 1.0}, out                   # track 1's step 1
    out = sq.tick(6.15)
    assert out == {(1, "A"): 0.0, (0, "B"): 0.5}, out    # track 1's 1-beat gate closed at 6.1, step 2 fires
    assert sq.position(6.15) == 2
    assert sq.tick(7.0) == {(0, "B"): 0.0}
    sq.set_direction("reverse")
    assert [sq.pattern_step(k) for k in range(5)] == [3, 2, 1, 0, 3]
    sq.set_direction("bounce")
    assert [sq.pattern_step(k) for k in range(7)] == [0, 1, 2, 3, 2, 1, 0]
    sq.set_direction("random")
    seen = {sq.pattern_step(k) for k in range(50)}
    assert seen <= {0, 1, 2, 3} and len(seen) > 1
    # swing: odd steps start later
    sq.set_direction("forward"); sq.pattern.swing = 0.5
    assert sq.abs_step(5.2) == 0 and sq.abs_step(5.3) == 1
    # manual trigger / release (pad held)
    sq.stop()
    sq.trigger(0, "C", 1.0, None, 20.0)
    assert sq.tick(21.0) == {(0, "C"): 1.0}
    sq.release(0, "C", 21.0)
    assert sq.tick(21.2) == {(0, "C"): 0.0}                # 0.1 beat release finished
    # pattern switch waits for the bar boundary
    sq.start(8.0)
    sq.copy_pattern(0, 1); sq.switch_pattern(1, 8.5)
    sq.tick(8.5); assert sq.current == 0 and sq.pending == 1
    sq.tick(12.01); assert sq.current == 1 and sq.pending is None and sq.start_beat == 12.0
    sq.switch_pattern(2, 13.0, now=True); assert sq.current == 2


def test_groups():
    import tempfile
    path = Path(tempfile.mkdtemp()) / "chases.yaml"
    sq = S.Sequencer(path)
    assert sq.groups == [None] * 8
    sq.store_group(0, {4, 0, 1}); sq.store_group(7, {23})
    assert sq.groups[0] == [0, 1, 4]
    sq2 = S.Sequencer(path)                                     # saved 1-based, loaded 0-based
    assert sq2.groups[0] == [0, 1, 4] and sq2.groups[7] == [23] and sq2.groups[1] is None
    assert "- - 1\n" in path.read_text() or "[1, 2, 5]" in path.read_text()
    sq2.clear_group(0)
    assert S.Sequencer(path).groups[0] is None
    sq3 = S.Sequencer(path)                                      # track groups shadow the global ones
    sq3.store_group(0, {1}, track=1); sq3.store_group(2, {5, 6}, track=0)
    sq3.store_group(0, {9})                                      # global GG1
    assert sq3.group(0, 1) == ([1], "track") and sq3.group(0, 0) == ([9], "global")
    assert sq3.group(2, 0) == ([5, 6], "track") and sq3.group(2, 1) == (None, None)
    sq3.pad_config = ["Bar A"] + ["\u2014"] * 23
    sq3.save()
    sq4 = S.Sequencer(path)
    assert sq4.track_groups[1][0] == [1] and sq4.track_groups[0][2] == [5, 6] and sq4.groups[0] == [9]
    assert sq4.pad_config == ["Bar A"] + ["\u2014"] * 23
    sq4.clear_group(0, track=1); assert sq4.group(0, 1) == ([9], "global")
    path.write_text("patterns: []\n")                            # old file without groups
    assert S.Sequencer(path).groups == [None] * 8 and S.Sequencer(path).track_groups == {}


def test_presets():
    """A pattern made by a preset keeps its source through chases.yaml, loses it on a step edit
    (not on knob edits), and only preset patterns made for another rig are re-fitted."""
    import tempfile
    path = Path(tempfile.mkdtemp()) / "chases.yaml"
    sq = S.Sequencer(path)
    made = {"name": "R1 Pump", "length": 32, "direction": "forward", "swing": 0.1,
            "source": {"bank": "techno", "recipe": "R1", "seed": 5, "rig": "1S 2D 3S 4D"},
            "tracks": [{"envelope": {"attack": 0, "decay": 0.1, "sustain": 0.2, "release": 0.2}, "gate": 0.5,
                        "level": 1.0, "steps": {"pad 2": [[0, 1.0, None]], "pad 4": [[4, 1.0, None]]}}]}
    assert sq.is_empty(2)
    sq.store_pattern(2, made)
    assert not sq.is_empty(2) and sq.patterns[2].source["recipe"] == "R1"
    sq2 = S.Sequencer(path)
    assert sq2.patterns[2].source == made["source"] and sq2.patterns[2].name == "R1 Pump"
    assert sq2.patterns[2].tracks[0].steps["pad 4"] == {4: [1.0, None]}
    sq2.current = 2
    sq2.pattern.tracks[0].envelope.release = 0.9                  # knob edit: still the preset's pattern
    sq2.set_length(16)
    assert sq2.pattern.source
    sq2.copy_pattern(2, 5)
    assert sq2.patterns[5].source["recipe"] == "R1"               # a copy is still a preset pattern
    assert sq2.mismatched("1S 2D 3S 4D") == [] and sq2.mismatched("1S 2D 3S 4D 5S") == [2, 5]

    def fake_refit(d):                                             # what banks.refit does to a dict
        d = dict(d, source=dict(d["source"], rig="1S 2D 3S 4D 5S"))
        d["tracks"][0]["steps"] = {"pad 5": [[0, 1.0, None]]}
        return d
    assert sq2.refit("1S 2D 3S 4D 5S", fake_refit) == [2, 5]
    assert sq2.patterns[2].tracks[0].steps == {"pad 5": {0: [1.0, None]}}
    assert sq2.patterns[2].tracks[0].envelope.release == 0.9 and sq2.patterns[2].length == 16
    assert sq2.refit("1S 2D 3S 4D 5S", fake_refit) == []          # nothing left to do
    sq2.toggle_step("pad 1", 3)                                    # a step edit: no longer the preset's
    assert sq2.pattern.source is None and S.Sequencer(path).patterns[2].source is None
    sq2.current = 5
    sq2.clear_steps("pad 5")
    assert sq2.patterns[5].source is None
    sq2.pad_config = ["Bar A"] + ["\u2014"] * 23
    sq2.save()
    assert S.Sequencer(path).pad_config[0] == "Bar A"
    path.write_text("pads:\n  2: [x]\n  1: [Bar B]\npatterns: []\n")       # older file: one list per track
    assert S.Sequencer(path).pad_config == ["Bar B"]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("pass", name)
    print("OK")
