"""Checks for rig.py / banks.py (SEQ presets). Plain script like tests/test_sequencer.py: prints OK."""
from __future__ import annotations

import os
import random
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from banks import BANKS, BEATS, RECIPES, STEPS, presets, realize, realize_bank, refit  # noqa: E402
from rig import MAX_PADS, MIN_PADS, PadInfo, RigError, build_rig, preset_rects, rig_from_roles, virtual_positions  # noqa: E402

# ---- the Python preset reader offers exactly the plugin's names (plugin/tests/test_preset.cpp) ---- #
rects = preset_rects(os.path.join(ROOT, "tests", "fixtures", "preset_small.xml"))
assert list(rects) == ["Bar B / 1 - 423 141 RGB", "Bar A / 1 - 423 141 RGB", "Bar A copy / 1 - 423 141 RGB",
                       "Bar C / 1 - 855 h3 2m grb", "Bar C / second slice", "Bar B", "Bar A", "Bar A copy", "Bar C"], list(rects)
assert [round(v) for v in rects["Bar A / 1 - 423 141 RGB"]] == [145, 72, 175, 530]
assert [round(v) for v in rects["Bar C"]] == [545, 70, 600, 1009]
assert [rid for rid, _ in presets()] == BANKS["techno"] and all(rid in RECIPES for rid in BANKS["techno"])

# ---- rig detection ------------------------------------------------------------------ #
assert virtual_positions(4) == [1, 2]
assert virtual_positions(9) == [1, 4, 7]
assert virtual_positions(10) == [1, 3, 6, 8]
assert virtual_positions(12) == [1, 4, 7, 10]
assert virtual_positions(24) == [1, 4, 7, 10, 13, 16, 19, 22]
for n in range(MIN_PADS, MAX_PADS + 1):                     # symmetric, non-empty, at most half
    vp = virtual_positions(n)
    assert vp and len(vp) <= n // 2 and vp == sorted(n - 1 - p for p in vp), (n, vp)

# geometry: 3 long + 6 short, pads assigned out of x order → sorted by x, roles by length
XML = ['<XmlState><ScreenSetup><CurrentCompositionTextureSize width="1920" height="1080"/><screens>']
layout = "S L S S L S S L S".split()
for i, kind in enumerate(layout):
    x, top, bottom = 100 + 150 * i, 70, (1009 if kind == "L" else 532)
    XML.append(f'<DmxScreen name="Lumiverse {i + 1}"><layers><DmxSlice><Params name="Common">'
               f'<Param name="Name" value="s{i}"/></Params><InputRect>'
               f'<v x="{x}" y="{top}"/><v x="{x + 30}" y="{top}"/><v x="{x + 30}" y="{bottom}"/><v x="{x}" y="{bottom}"/>'
               f'</InputRect></DmxSlice></layers></DmxScreen>')
XML.append('</screens></ScreenSetup></XmlState>')
with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False) as f:
    f.write("".join(XML))
rects = preset_rects(f.name)
assign = {1: "Lumiverse 9", 2: "Lumiverse 1", 3: "Lumiverse 2", 4: "Lumiverse 3", 5: "Lumiverse 4",
          6: "Lumiverse 5", 7: "Lumiverse 6", 8: "Lumiverse 7", 9: "Lumiverse 8"}     # pad 1 = rightmost bar
pads = [PadInfo(k, assign.get(k, ""), rects.get(assign.get(k, ""))) for k in range(1, 25)]
rig = build_rig(pads)
assert rig.pads == [2, 3, 4, 5, 6, 7, 8, 9, 1], rig.pads
assert rig.D == [3, 6, 9] and not rig.virtual and rig.source == "preset", rig.signature()
rig = build_rig(pads, {"dominant": [4, 5]})
assert rig.D == [4, 5] and rig.source == "config"
uni = build_rig([PadInfo(k, f"Bar {k}", (10 * k, 0, 10 * k + 5, 400)) for k in range(1, 13)])
assert uni.virtual and uni.D == [2, 5, 8, 11], uni.signature()
nogeo = build_rig([PadInfo(k, f"Bar {k}") for k in range(1, 7)])
assert nogeo.virtual and nogeo.D == [2, 5]
for bad in (3, 25):
    try:
        build_rig([PadInfo(k, f"Bar {k}") for k in range(1, bad + 1)])
        raise AssertionError("expected RigError")
    except RigError:
        pass

# derived pools on the reference rig
r9 = rig_from_roles("S D S S D S S D S")
assert r9.sat(2) == [1, 3] and r9.sat(5) == [4, 6] and r9.sat(8) == [7, 9]
assert r9.pairs == [[1, 9], [3, 7], [4, 6]] and r9.outer_d == [2, 8]
assert r9.s_high == [1, 3, 7, 9] and r9.s_mid == [3, 4, 6, 7]
assert rig_from_roles("D D S S").sat(1) == [3]              # both neighbours D/edge → nearest S

# ---- realise every recipe on many rigs ------------------------------------------------- #
rigs = [r9, rig_from_roles("D S S S"), rig_from_roles("S S S D"), rig_from_roles("S D D S"),
        rig_from_roles("S D S D S D S D S D S D S D S D S D S D S D S D")]
rng = random.Random(5)
for n in range(MIN_PADS, MAX_PADS + 1):
    rigs.append(rig_from_roles(f"U{n}"))
    for _ in range(4):                                       # random role mixes with ≥1 D and ≥1 S
        roles = ["D" if rng.random() < rng.choice([0.15, 0.4, 0.8]) else "S" for _ in range(n)]
        roles[rng.randrange(n)], roles[rng.randrange(n)] = "D", "S"
        if "D" in roles and "S" in roles:
            rigs.append(rig_from_roles(" ".join(roles)))

KICK_ON_D = {"R1", "R2", "R3", "D1", "D2", "D3", "G1", "G2", "G3", "W2", "W3"}
checked = 0
for rig in rigs:
    keys = {f"pad {p}" for p in rig.pads}
    dkeys = {f"pad {p}" for p in rig.D}
    skeys = {f"pad {p}" for p in rig.S}
    for i, rid in enumerate(BANKS["techno"]):
        pat = realize(rid, rig, 1000 + i)
        assert pat == realize(rid, rig, 1000 + i), (rid, "not deterministic")
        lit = set()
        for t, tr in enumerate(pat["tracks"]):
            for key, steps in tr["steps"].items():
                assert key in keys, (rid, rig.signature(), key)
                for s, lv, g in steps:
                    assert 0 <= s < STEPS and 0.05 <= lv <= 1 and g is None
                lit.add(key)
        kick = pat["tracks"][0]["steps"]
        if rid in KICK_ON_D:
            assert set(kick) <= dkeys, (rid, rig.signature())
            assert {s for st in kick.values() for s, _, _ in st} == set(BEATS), rid
        if rid in ("R1", "R2", "R3"):
            assert set(pat["tracks"][3]["steps"]) <= skeys, (rid, "rumble off S")
        assert len(lit) >= min(len(rig.pads), 3), (rid, rig.signature(), len(lit))
        checked += 1

# timing never depends on the rig (except the steps W2 T3 / W4 T2 draw at random)
def timing(p, skip):
    return [sorted({s for st in tr["steps"].values() for s, _, _ in st}) if (p["source"]["recipe"], t) not in skip else None
            for t, tr in enumerate(p["tracks"])]
skip = {("W2", 2), ("W4", 1)}
for i, rid in enumerate(BANKS["techno"]):
    ref = timing(realize(rid, r9, 7), skip)
    for rig in rigs[:12]:
        assert timing(realize(rid, rig, 7), skip) == ref, (rid, rig.signature())

# re-fit keeps the user's knobs, replaces placement; edited patterns stay
p = realize("D2", r9, 67)
p["tracks"][1]["envelope"]["release"] = 0.5
p["swing"] = 0.4
big = rig_from_roles("U16")
q = refit(p, big)
assert q["tracks"][1]["envelope"]["release"] == 0.5 and q["swing"] == 0.4
assert set(q["tracks"][1]["steps"]) <= {f"pad {k}" for k in big.pads}
assert p["source"]["rig"] == r9.signature() and q["source"]["rig"] == big.signature()
p2 = dict(p, source=None)
assert refit(p2, big) is p2

# ---- through the real sequencer (when run inside the repo) -------------------------- #
try:
    from sequencer import Sequencer, _pattern_from_dict
except ImportError:
    Sequencer = None
if Sequencer:
    for rig in (r9, rig_from_roles("U4"), rig_from_roles("U24")):
        sq = Sequencer()
        sq.patterns = [_pattern_from_dict(d) for d in realize_bank(rig)]
        for i in range(16):
            sq.current, sq.voices, sq.levels = i, {}, {}
            sq.start(0.0)
            bt, seen = 0.0, set()
            while bt < 8.0:
                seen.update(k[1] for k, v in sq.tick(bt).items() if v > 0)
                bt += 0.01
            assert seen, (rig.signature(), i)

print(f"OK  {len(rigs)} rigs x 16 recipes = {checked} patterns" + ("" if Sequencer else "  (sequencer.py not found: playback check skipped)"))
