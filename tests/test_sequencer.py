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


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("pass", name)
    print("OK")
