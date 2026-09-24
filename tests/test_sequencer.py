"""Pure-logic tests for sequencer.py (no mock, no hardware).  python tests/test_sequencer.py → OK"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import sequencer as S  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures" / "preset_small.xml"


def test_preset_bars():
    bars, warnings = S.load_preset_bars(FIX)
    assert [b.name for b in bars] == ["Bar A", "Bar A copy", "Bar B", "Bar C"], [b.name for b in bars]
    a = bars[0]
    assert (a.left, a.top, a.right, a.bottom) == (145, 72, 175, 530), a
    c = bars[3]
    assert (c.left, c.top, c.right, c.bottom) == (545, 70, 600, 1009), c   # union of its two slices
    assert any("Bar A copy" in w and "Bar A" in w for w in warnings), warnings

    bars, _ = S.load_preset_bars(FIX, disabled=["Bar A copy"])
    assert [b.name for b in bars] == ["Bar A", "Bar B", "Bar C"]

    bars, _ = S.load_preset_bars(FIX, groups=[{"name": "Left", "screens": ["Bar A", "Bar B"]}],
                                 disabled=["Bar A copy"])
    assert [b.name for b in bars] == ["Left", "Bar C"]
    assert (bars[0].left, bars[0].right, bars[0].screens) == (145, 375, ["Bar A", "Bar B"])

    assert S.newest_preset(FIX.parent) == FIX
    assert S.newest_preset(FIX.parent / "nope") is None


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


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("pass", name)
    print("OK")
