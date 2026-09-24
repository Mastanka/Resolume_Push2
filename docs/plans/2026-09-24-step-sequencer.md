# SEQ Step Sequencer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A drum-rack style step sequencer on the Push 2 that flashes up to 4 clip textures across the LED bars with ADSR envelopes, using one Resolume layer per (track, bar).

**Architecture:** Pure sequencer logic (bars from the Advanced Output preset, patterns, envelopes, playback clock) lives in `sequencer.py` with no Resolume or Push dependency. `chaser_engine.py` turns levels into Resolume layer opacities and builds the bar layers through the REST API. `push_resolume_bridge.py` gets a `seq` menu (pads, buttons, knobs), a 100 Hz sequencer thread, and the `--setup-chaser` command; `display.py` draws the SEQ screen. The spec is `docs/specs/2026-09-24-step-sequencer-design.md`.

**Tech Stack:** Python 3.9-compatible, push2-python, requests, websocket-client, PyYAML, cairo; tests are plain scripts run against `tests/mock_resolume.py` (no pytest).

## Global Constraints

- Python 3.9 syntax only: keep `from __future__ import annotations`; no `match`, no runtime `X | Y` types, no 3.10+ stdlib APIs. Check every module with `python -c "import ast; ast.parse(open('FILE').read(), feature_version=(3,9))"`.
- All MIDI output (pad / button colours) happens in the main thread's run loop; callbacks only change state.
- Every Resolume write goes through `Sender` / `ResolumeWS`; never block the MIDI callback thread on HTTP (run setup and texture loading in a worker thread).
- Tests are scripts that must end by printing `OK`; run them with the mock: `python tests/mock_resolume.py &` first (kill it between runs: `pkill -f mock_resolume.py`). Run both `python tests/test_fake_push.py` and `TEST_POLL=1 python tests/test_fake_push.py`.
- Lint: `python -m pyflakes *.py tests/*.py` must be clean.
- Bar layers are identified by their **Crop effect's display name** `CH:T<track>:<bar name>` (marker), because renaming a layer through the API is unverified; the layer name `CH: T<track> <bar name>` is set best-effort.
- Commit after every task with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` as the last line of the message.
- Never delete layers. Never change Štefan's composition from tests (tests only talk to the mock on port 8080; stop the real Arena's webserver is NOT required because the mock binds the same port only when Arena is not running — if `tests/mock_resolume.py` fails with "address in use", Arena is running: do not run the tests then).

---

## File structure

| File | Responsibility |
|---|---|
| `sequencer.py` (new) | `Bar` + preset parsing, `Envelope` / `env_value`, `Voice`, `Track`, `Pattern`, `Sequencer` (editing, playback tick, `chases.yaml`) |
| `chaser_engine.py` (new) | `LayerEngine`: find / build bar layers, load textures, set opacity levels with rate limiting |
| `resolume_api.py` | REST calls `add_layer`, `add_effect`, `delete_effect`, `set_effect_display_name`, `open_clip`, `clear_clip`; `ResolumeWS.set()` outbox |
| `push_resolume_bridge.py` | `seq` mode: constants, visible-layer list, pads / buttons / knobs, `seq_loop` thread, `_send_level`, snapshot data, `--setup-chaser` |
| `display.py` | `render()` branch for `mode == "seq"` |
| `resolume_check.py` | checks for WebSocket `set`, clip open / clear, Crop add / delete, effect display name, layer name string |
| `tests/mock_resolume.py` | new endpoints + opacity log |
| `tests/test_sequencer.py` (new) | pure logic tests (no mock needed) |
| `tests/test_engine.py` (new) | `LayerEngine` against the mock |
| `tests/test_fake_push.py` | SEQ block: end-to-end through fake Push events |
| `tests/render_preview.py` | `preview_seq.png` |
| `tests/fixtures/preset_small.xml` (new) | 3-screen Advanced Output preset for tests |
| `README.md`, `CLAUDE.md`, `config.yaml`, `docs/ideas.md` | docs |

---

### Task 1: Bars from the Advanced Output preset

**Files:**
- Create: `sequencer.py`
- Create: `tests/fixtures/preset_small.xml`
- Create: `tests/test_sequencer.py`

**Interfaces:**
- Produces: `Bar(name, left, top, right, bottom, screens)` dataclass; `newest_preset(folder) -> Path | None`; `load_preset_bars(path, groups=None, disabled=()) -> (list[Bar], list[str])` (bars sorted by `left`, warnings for identical rectangles).

- [ ] **Step 1: Create the fixture** `tests/fixtures/preset_small.xml` (same structure as Arena 7.23's preset: `XmlState > ScreenSetup > screens > DmxScreen > layers > DmxSlice > Params[Common]/Param[Name] + InputRect/v`):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<XmlState name="preset_small">
  <ScreenSetup>
    <CurrentCompositionTextureSize width="1920" height="1080"/>
    <screens>
      <DmxScreen name="Bar B" uniqueId="2">
        <layers>
          <DmxSlice uniqueId="20">
            <Params name="Common"><Param name="Name" T="STRING" value="1 - 423 141 RGB"/></Params>
            <InputRect orientation="1.5707">
              <v x="345.0" y="70.0"/><v x="375.0" y="70.0"/><v x="375.0" y="532.0"/><v x="345.0" y="532.0"/>
            </InputRect>
          </DmxSlice>
        </layers>
      </DmxScreen>
      <DmxScreen name="Bar A" uniqueId="1">
        <layers>
          <DmxSlice uniqueId="10">
            <Params name="Common"><Param name="Name" T="STRING" value="1 - 423 141 RGB"/></Params>
            <InputRect orientation="1.5707">
              <v x="174.99" y="72.2"/><v x="175.0" y="529.8"/><v x="145.0" y="529.8"/><v x="144.99" y="72.2"/>
            </InputRect>
          </DmxSlice>
        </layers>
      </DmxScreen>
      <DmxScreen name="Bar A copy" uniqueId="3">
        <layers>
          <DmxSlice uniqueId="30">
            <Params name="Common"><Param name="Name" T="STRING" value="1 - 423 141 RGB"/></Params>
            <InputRect orientation="1.5707">
              <v x="174.99" y="72.2"/><v x="175.0" y="529.8"/><v x="145.0" y="529.8"/><v x="144.99" y="72.2"/>
            </InputRect>
          </DmxSlice>
        </layers>
      </DmxScreen>
      <DmxScreen name="Bar C" uniqueId="4">
        <layers>
          <DmxSlice uniqueId="40">
            <Params name="Common"><Param name="Name" T="STRING" value="1 - 855 h3 2m grb"/></Params>
            <InputRect orientation="1.5707">
              <v x="545.0" y="70.0"/><v x="575.0" y="70.0"/><v x="575.0" y="1009.0"/><v x="545.0" y="1009.0"/>
            </InputRect>
          </DmxSlice>
          <DmxSlice uniqueId="41">
            <Params name="Common"><Param name="Name" T="STRING" value="second slice"/></Params>
            <InputRect orientation="0">
              <v x="580.0" y="70.0"/><v x="600.0" y="70.0"/><v x="600.0" y="200.0"/><v x="580.0" y="200.0"/>
            </InputRect>
          </DmxSlice>
        </layers>
      </DmxScreen>
    </screens>
  </ScreenSetup>
</XmlState>
```

- [ ] **Step 2: Write the failing test** `tests/test_sequencer.py`:

```python
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
```

- [ ] **Step 3: Run it to see it fail**

Run: `cd /Users/stefanbittmann/Projects/RESOLUME_PUSH2 && source .venv/bin/activate && python tests/test_sequencer.py`
Expected: `ModuleNotFoundError: No module named 'sequencer'`

- [ ] **Step 4: Create `sequencer.py`** with the bar parsing:

```python
"""Step sequencer for the LED bars: bars from an Advanced Output preset, patterns with up to
four texture tracks, ADSR envelopes and the playback clock. Pure Python: nothing here talks to
Resolume or the Push; the bridge feeds it beat time and sends the levels it returns."""

from __future__ import annotations

import math
import random
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

import yaml

GRIDS = {"1/4": 1.0, "1/4t": 2 / 3, "1/8": 0.5, "1/8t": 1 / 3,
         "1/16": 0.25, "1/16t": 1 / 6, "1/32": 0.125, "1/32t": 1 / 12}   # beats per step
DIRECTIONS = ["forward", "reverse", "bounce", "random"]
MAX_STEPS = 32
BAR_BEATS = 4          # pattern switches happen on this boundary
N_PATTERNS = 16
N_TRACKS = 4


# --------------------------------------------------------------------------- #
# Bars from the Advanced Output preset
# --------------------------------------------------------------------------- #

@dataclass
class Bar:
    name: str
    left: int
    top: int
    right: int
    bottom: int
    screens: list = field(default_factory=list)


def newest_preset(folder):
    """Most recently saved .xml in the Advanced Output presets folder, or None."""
    folder = Path(folder)
    files = sorted(folder.glob("*.xml"), key=lambda p: p.stat().st_mtime, reverse=True) if folder.is_dir() else []
    return files[0] if files else None


def _screen_rect(screen):
    """Bounding box of every slice's InputRect in a screen element, or None without slices."""
    xs, ys = [], []
    for el in screen.iter():
        if not el.tag.endswith("Slice"):
            continue
        rect = el.find("InputRect")
        for v in (rect.findall("v") if rect is not None else []):
            xs.append(float(v.get("x")))
            ys.append(float(v.get("y")))
    if not xs:
        return None
    return round(min(xs)), round(min(ys)), round(max(xs)), round(max(ys))


def load_preset_bars(path, groups=None, disabled=()):
    """(bars sorted left→right, warnings). One screen = one bar; `groups` = [{name, screens}]
    merge screens into one bar; `disabled` screen names are skipped."""
    root = ET.parse(str(path)).getroot()
    rects = {}                                   # screen name -> rect
    for el in root.iter():
        if el.tag.endswith("Screen") and el.get("name") and el.get("name") not in disabled:
            r = _screen_rect(el)
            if r:
                rects[el.get("name")] = r
    warnings = []
    seen = {}
    for name, r in rects.items():
        if r in seen:
            warnings.append(f"screen '{name}' has the same rectangle as '{seen[r]}' — disable one of them?")
        else:
            seen[r] = name
    bars, used = [], set()
    for g in groups or []:
        members = [s for s in g.get("screens", []) if s in rects]
        if not members:
            warnings.append(f"group '{g.get('name')}': none of its screens are in the preset")
            continue
        rs = [rects[m] for m in members]
        bars.append(Bar(g["name"], min(r[0] for r in rs), min(r[1] for r in rs),
                        max(r[2] for r in rs), max(r[3] for r in rs), members))
        used.update(members)
    for name, r in rects.items():
        if name not in used:
            bars.append(Bar(name, *r, [name]))
    bars.sort(key=lambda b: (b.left, b.top))
    return bars, warnings
```

- [ ] **Step 5: Run the test**

Run: `python tests/test_sequencer.py`
Expected: `pass test_preset_bars` then `OK`

- [ ] **Step 6: Lint + 3.9 check, commit**

```bash
python -m pyflakes sequencer.py tests/test_sequencer.py
python -c "import ast; ast.parse(open('sequencer.py').read(), feature_version=(3,9))"
git add sequencer.py tests/fixtures/preset_small.xml tests/test_sequencer.py
git commit -m "SEQ: bars from the Advanced Output preset

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Envelope and voices

**Files:**
- Modify: `sequencer.py` (append after the bar section)
- Modify: `tests/test_sequencer.py`

**Interfaces:**
- Produces: `Envelope(attack, decay, sustain, release)` (beats / fraction); `env_value(env, t, gate, from_level=0.0) -> float | None` (None = finished); `Voice(track, bar, start, level, gate, env, from_level)` with `env_value(bt)`.

- [ ] **Step 1: Add the failing test** to `tests/test_sequencer.py` (before `if __name__`):

```python
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
```

- [ ] **Step 2: Run to see it fail** — `python tests/test_sequencer.py` → `AttributeError: module 'sequencer' has no attribute 'Envelope'`

- [ ] **Step 3: Append to `sequencer.py`:**

```python
# --------------------------------------------------------------------------- #
# Envelope
# --------------------------------------------------------------------------- #

@dataclass
class Envelope:
    attack: float = 0.0      # beats
    decay: float = 0.0       # beats
    sustain: float = 1.0     # 0..1
    release: float = 0.1     # beats


def env_value(env, t, gate, from_level=0.0):
    """Envelope level at t beats after the trigger. `gate` = beats the gate stays open
    (None = still held). Returns None once the release has finished."""
    def held(t):
        if t < env.attack:
            return from_level + (1.0 - from_level) * t / env.attack
        t2 = t - env.attack
        if t2 < env.decay:
            return 1.0 + (env.sustain - 1.0) * t2 / env.decay
        return env.sustain

    if gate is None or t < gate:
        return held(t)
    if env.release <= 0:
        return None
    r = (t - gate) / env.release
    return None if r >= 1.0 else held(gate) * (1.0 - r)


@dataclass
class Voice:
    """One flash of a track on a bar."""
    track: int
    bar: str
    start: float           # beat time of the trigger
    level: float           # step level 0..1 (pad velocity)
    gate: float            # beats, or None while a pad is held
    env: Envelope
    from_level: float = 0.0

    def env_value(self, bt):
        return env_value(self.env, bt - self.start, self.gate, self.from_level)
```

- [ ] **Step 4: Run the test** — `python tests/test_sequencer.py` → `pass test_envelope`, `OK`

- [ ] **Step 5: Commit**

```bash
git add sequencer.py tests/test_sequencer.py
git commit -m "SEQ: ADSR envelope and voices

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Patterns, tracks, editing and chases.yaml

**Files:**
- Modify: `sequencer.py` (append)
- Modify: `tests/test_sequencer.py`

**Interfaces:**
- Produces: `Track(texture, envelope, gate, level, steps)` where `steps = {bar name: {step int: [level, gate-or-None]}}`; `Pattern(name, length, direction, swing, tracks)`; `Sequencer(path=None, n_tracks=4)` with `patterns`, `current`, `track`, `bar`, `bank`, `grid`, property `pattern`, `toggle_step(bar, step, level=1.0, gate=None, track=None) -> bool`, `set_step_values(bar, steps, level=None, gate=None, track=None)`, `clear_steps(bar=None, track=None)`, `clear_pattern(p=None)`, `copy_pattern(src, dst)`, `double_loop()`, `set_length(n)`, `set_direction(d)`, `save()`, `load()`, `to_dict()`, `from_dict(d)`.

- [ ] **Step 1: Add the failing test:**

```python
def test_patterns_and_yaml(tmp=None):
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
```

- [ ] **Step 2: Run to see it fail** — `AttributeError: module 'sequencer' has no attribute 'Sequencer'`

- [ ] **Step 3: Append to `sequencer.py`:**

```python
# --------------------------------------------------------------------------- #
# Patterns
# --------------------------------------------------------------------------- #

@dataclass
class Track:
    texture: dict = None                       # {"source": name, "params": {...}} or {"file": url} or None
    envelope: Envelope = field(default_factory=Envelope)
    gate: float = 0.5                          # fraction of a step
    level: float = 1.0
    steps: dict = field(default_factory=dict)  # bar name -> {step: [level, gate or None]}


@dataclass
class Pattern:
    name: str = "P1"
    length: int = 16
    direction: str = "forward"
    swing: float = 0.0
    tracks: list = field(default_factory=lambda: [Track() for _ in range(N_TRACKS)])


def _track_to_dict(t):
    return {"texture": t.texture,
            "envelope": {"attack": t.envelope.attack, "decay": t.envelope.decay,
                         "sustain": t.envelope.sustain, "release": t.envelope.release},
            "gate": t.gate, "level": t.level,
            "steps": {bar: [[s, lv, g] for s, (lv, g) in sorted(st.items())] for bar, st in t.steps.items() if st}}


def _track_from_dict(d):
    e = d.get("envelope") or {}
    return Track(texture=d.get("texture"), envelope=Envelope(**{k: float(e.get(k, getattr(Envelope(), k)))
                                                                for k in ("attack", "decay", "sustain", "release")}),
                 gate=float(d.get("gate", 0.5)), level=float(d.get("level", 1.0)),
                 steps={bar: {int(s): [float(lv), (None if g is None else float(g))] for s, lv, g in lst}
                        for bar, lst in (d.get("steps") or {}).items()})


class Sequencer:
    def __init__(self, path=None, n_tracks=N_TRACKS):
        self.path = Path(path) if path else None
        self.n_tracks = n_tracks
        self.patterns = [Pattern(name=f"P{i + 1}") for i in range(N_PATTERNS)]
        self.current = 0
        self.pending = None        # pattern index waiting for the next bar boundary
        self.track = 0             # selected track
        self.bar = 0               # selected bar index (into the bridge's bar list)
        self.bank = 0              # bars 16*bank .. on the pads
        self.grid = "1/16"
        self.running = False
        self.start_beat = 0.0
        self.last_bt = None
        self.last_step = None
        self.last_random = None
        self.voices = {}           # (track, bar name) -> Voice
        self.levels = {}           # (track, bar name) -> last level returned by tick()
        if self.path and self.path.exists():
            self.load()

    # ---- editing ------------------------------------------------------------ #
    @property
    def pattern(self):
        return self.patterns[self.current]

    def _steps(self, bar, track=None):
        t = self.pattern.tracks[self.track if track is None else track]
        return t.steps.setdefault(bar, {})

    def toggle_step(self, bar, step, level=1.0, gate=None, track=None):
        """Returns True when the step is on afterwards."""
        st = self._steps(bar, track)
        if step in st:
            del st[step]
            on = False
        else:
            st[step] = [float(level), gate]
            on = True
        self.save()
        return on

    def set_step_values(self, bar, steps, level=None, gate=None, track=None):
        st = self._steps(bar, track)
        for s in steps:
            if s in st:
                if level is not None:
                    st[s][0] = max(0.0, min(1.0, float(level)))
                if gate is not None:
                    st[s][1] = max(0.1, min(1.0, float(gate)))
        self.save()

    def clear_steps(self, bar=None, track=None):
        t = self.pattern.tracks[self.track if track is None else track]
        if bar is None:
            t.steps = {}
        else:
            t.steps.pop(bar, None)
        self.save()

    def clear_pattern(self, p=None):
        p = self.current if p is None else p
        self.patterns[p] = Pattern(name=f"P{p + 1}")
        self.save()

    def copy_pattern(self, src, dst):
        d = _pattern_from_dict(_pattern_to_dict(self.patterns[src]))
        d.name = f"P{dst + 1}"
        self.patterns[dst] = d
        self.save()

    def double_loop(self):
        p = self.pattern
        if p.length * 2 > MAX_STEPS:
            return
        for t in p.tracks:
            for st in t.steps.values():
                for s, v in list(st.items()):
                    st[s + p.length] = list(v)
        p.length *= 2
        self.save()

    def set_length(self, n):
        self.pattern.length = max(1, min(MAX_STEPS, int(n)))
        self.save()

    def set_direction(self, d):
        if d in DIRECTIONS:
            self.pattern.direction = d
            self.save()

    # ---- storage ------------------------------------------------------------ #
    def to_dict(self):
        return {"patterns": [_pattern_to_dict(p) for p in self.patterns]}

    def from_dict(self, d):
        pats = [_pattern_from_dict(x) for x in (d.get("patterns") or [])][:N_PATTERNS]
        while len(pats) < N_PATTERNS:
            pats.append(Pattern(name=f"P{len(pats) + 1}"))
        self.patterns = pats

    def save(self):
        if not self.path:
            return
        try:
            self.path.write_text("# Step sequencer patterns, written by the bridge. Safe to edit or delete.\n"
                                 + yaml.safe_dump(self.to_dict(), sort_keys=False), encoding="utf-8")
        except Exception as e:
            print(f"[seq] can't write {self.path}: {e}", file=sys.stderr)

    def load(self):
        try:
            self.from_dict(yaml.safe_load(self.path.read_text(encoding="utf-8")) or {})
        except Exception as e:
            print(f"[seq] can't read {self.path}: {e}", file=sys.stderr)


def _pattern_to_dict(p):
    return {"name": p.name, "length": p.length, "direction": p.direction, "swing": p.swing,
            "tracks": [_track_to_dict(t) for t in p.tracks]}


def _pattern_from_dict(d):
    tracks = [_track_from_dict(t) for t in (d.get("tracks") or [])][:N_TRACKS]
    while len(tracks) < N_TRACKS:
        tracks.append(Track())
    return Pattern(name=str(d.get("name", "P?")), length=int(d.get("length", 16)),
                   direction=d.get("direction", "forward"), swing=float(d.get("swing", 0.0)), tracks=tracks)
```

- [ ] **Step 4: Run the test** — `python tests/test_sequencer.py` → `pass test_patterns_and_yaml`, `OK`

- [ ] **Step 5: Lint, commit**

```bash
python -m pyflakes sequencer.py tests/test_sequencer.py
git add sequencer.py tests/test_sequencer.py
git commit -m "SEQ: patterns, tracks, editing, chases.yaml

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: Playback clock

**Files:**
- Modify: `sequencer.py` (add methods to `Sequencer`)
- Modify: `tests/test_sequencer.py`

**Interfaces:**
- Produces on `Sequencer`: `step_beats()`, `start(bt)`, `stop()`, `switch_pattern(p, bt, now=False)`, `abs_step(bt) -> int`, `pattern_step(k) -> int`, `position(bt) -> int | None`, `trigger(track, bar, level, gate_beats, bt)`, `release(track, bar, bt)`, `tick(bt) -> dict[(track, bar name)] = level`. `bt` is continuous beat time (beats since the anchor, float).

- [ ] **Step 1: Add the failing test:**

```python
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
    assert sq.tick(4.85) == {(0, "A"): 0.0}              # 0.5 gate + 0.1 release → off by 4.6; edge sent once
    out = sq.tick(5.1)
    assert out == {(1, "A"): 1.0}, out                   # track 1's step 1
    assert sq.tick(6.05) == {(1, "A"): 0.0, (0, "B"): 0.5}
    assert sq.position(6.05) == 2
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
```

- [ ] **Step 2: Run to see it fail** — `AttributeError: 'Sequencer' object has no attribute 'start'`

- [ ] **Step 3: Add the playback methods** inside `class Sequencer` (after `set_direction`):

```python
    # ---- playback (pure: the bridge passes beat time in, gets levels out) ------- #
    def step_beats(self):
        return GRIDS[self.grid]

    def start(self, bt):
        self.running = True
        self.start_beat = math.floor(bt / BAR_BEATS) * BAR_BEATS
        self.last_step = None
        self.last_bt = bt

    def stop(self):
        self.running = False        # voices keep ticking so releases finish

    def switch_pattern(self, p, bt, now=False):
        if now or not self.running:
            self.current, self.pending = p, None
            self.start_beat = math.floor(bt / BAR_BEATS) * BAR_BEATS
            self.last_step = None
        else:
            self.pending = p

    def abs_step(self, bt):
        """Grid steps since the pattern start, with swing (steps 2, 4, 6 … start later)."""
        sb = self.step_beats()
        k = int(math.floor((bt - self.start_beat) / sb))
        if k % 2 == 1 and bt < self.start_beat + k * sb + self.pattern.swing * sb / 2:
            k -= 1
        return max(0, k)

    def pattern_step(self, k):
        L = max(1, self.pattern.length)
        d = self.pattern.direction
        if d == "reverse":
            return L - 1 - k % L
        if d == "bounce":
            period = max(1, 2 * L - 2)
            m = k % period
            return m if m < L else period - m
        if d == "random":
            choices = [s for s in range(L) if s != self.last_random] or [0]
            self.last_random = random.choice(choices)
            return self.last_random
        return k % L

    def position(self, bt):
        return self.pattern_step(self.abs_step(bt)) if self.running else None

    def trigger(self, track, bar, level, gate_beats, bt):
        t = self.pattern.tracks[track]
        old = self.voices.get((track, bar))
        frm = (old.env_value(bt) or 0.0) if old else 0.0
        self.voices[(track, bar)] = Voice(track, bar, bt, float(level), gate_beats, t.envelope, min(1.0, frm))

    def release(self, track, bar, bt):
        v = self.voices.get((track, bar))
        if v and v.gate is None:
            v.gate = max(0.0, bt - v.start)

    def tick(self, bt):
        """Advance to beat time bt. Returns {(track, bar): level} for every level that changed."""
        if self.running:
            if self.pending is not None and self.last_bt is not None \
                    and math.floor(bt / BAR_BEATS) > math.floor(self.last_bt / BAR_BEATS):
                self.switch_pattern(self.pending, bt, now=True)
            k = self.abs_step(bt)
            if k != self.last_step:
                self.last_step = k
                s = self.pattern_step(k)
                sb = self.step_beats()
                for ti, tr in enumerate(self.pattern.tracks[:self.n_tracks]):
                    for bar, steps in tr.steps.items():
                        if s in steps:
                            level, gate = steps[s]
                            self.trigger(ti, bar, level, (tr.gate if gate is None else gate) * sb, bt)
        self.last_bt = bt
        out = {}
        for key, v in list(self.voices.items()):
            e = v.env_value(bt)
            if e is None:
                del self.voices[key]
                value = 0.0
            else:
                value = e * v.level * self.pattern.tracks[key[0]].level
            value = round(value, 4)
            if self.levels.get(key) != value:
                self.levels[key] = value
                out[key] = value
        return out
```

Note: `position()` calls `pattern_step`, which for `random` would draw a new number; for the display use `self.last_pattern_step` instead — add `self.last_pattern_step = s` inside `tick()` right after `s = self.pattern_step(k)` (initialise `self.last_pattern_step = None` in `__init__`) and make `position()` return `self.last_pattern_step if self.running else None`. Adjust the test line to `assert sq.position(6.05) == 2` (still true).

- [ ] **Step 4: Run the test** — `python tests/test_sequencer.py` → `pass test_playback`, `OK`. If the release assertion at 4.85 fails, check that `tick` at 4.4 was called (it seeds `levels`) and that `env_value` returns None at `t = 0.6` for gate 0.5, release 0.1.

- [ ] **Step 5: Lint, 3.9 check, commit**

```bash
python -m pyflakes sequencer.py tests/test_sequencer.py
python -c "import ast; ast.parse(open('sequencer.py').read(), feature_version=(3,9))"
git add sequencer.py tests/test_sequencer.py
git commit -m "SEQ: playback clock, directions, swing, pattern switching

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Resolume API additions + mock endpoints

**Files:**
- Modify: `resolume_api.py` (`Resolume` class ~line 151, `ResolumeWS` ~line 247)
- Modify: `tests/mock_resolume.py`
- Create: `tests/test_engine.py` (first part)

**Interfaces:**
- Produces on `Resolume`: `add_layer(before=None) -> int` (HTTP status), `add_effect(layer, name) -> int`, `delete_effect(layer, offset) -> int`, `set_effect_display_name(layer, index, name) -> int`, `open_clip(layer, column, uri) -> int`, `clear_clip(layer, column) -> int`. On `ResolumeWS`: `set(pid, value)` (queued; sent by the socket thread). Mock: `POST /composition/layers/add`, `POST …/layers/{L}/effects/video/add` (`Crop` or `Slice%20Transform`, else 400), `DELETE …/layers/{L}/effects/video/{offset}`, `POST …/layers/{L}/effects/video/{i}/set-display-name`, `POST …/clips/{C}/open`, `POST …/clips/{C}/clear`, WebSocket `set`, `GET /api/v1/_opacity_log` → `[[t, pid, value], …]` of every PUT / set on a `video/opacity` param.

- [ ] **Step 1: Write the failing test** `tests/test_engine.py`:

```python
"""LayerEngine + new REST calls against the mock.   python tests/mock_resolume.py &   then
python tests/test_engine.py → OK"""
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from resolume_api import Resolume, resolve_node  # noqa: E402

API = "http://127.0.0.1:8080/api/v1"
rest = Resolume("127.0.0.1", 8080)


def comp():
    return requests.get(API + "/composition", timeout=3).json()


def test_rest_additions():
    n0 = len(comp()["layers"])
    assert rest.add_layer() == 204
    c = comp()
    assert len(c["layers"]) == n0 + 1 and c["layers"][-1]["name"]["value"].startswith("Layer")
    L = n0 + 1
    assert rest.add_effect(L, "Crop") == 204
    assert rest.add_effect(L, "No Such Effect") == 400
    fx = comp()["layers"][L - 1]["video"]["effects"]
    assert [e["name"] for e in fx] == ["Crop"] and fx[0]["params"]["Right"]["value"] == 1920.0
    assert rest.set_effect_display_name(L, 0, "CH:T1:Bar A") == 204
    assert comp()["layers"][L - 1]["video"]["effects"][0]["display_name"] == "CH:T1:Bar A"
    assert rest.open_clip(L, 1, "source:///video/Metaballs") == 204
    clip = comp()["layers"][L - 1]["clips"][0]
    assert clip["name"]["value"] == "Metaballs" and clip["video"]["description"] == "Metaballs"
    assert "Color" in clip["video"]["sourceparams"]
    assert rest.open_clip(L, 2, "file:///Users/x/fire%201.mov") == 204
    assert comp()["layers"][L - 1]["clips"][1]["video"]["fileinfo"]["path"] == "/Users/x/fire 1.mov"
    assert rest.clear_clip(L, 2) == 204
    assert comp()["layers"][L - 1]["clips"][1]["connected"]["value"] == "Empty"
    op = resolve_node(comp()["layers"][L - 1], "video/opacity")
    before = len(requests.get(API + "/_opacity_log", timeout=3).json())
    rest.set_param(op["id"], {"value": 0.25})
    log = requests.get(API + "/_opacity_log", timeout=3).json()
    assert len(log) == before + 1 and log[-1][1] == op["id"] and log[-1][2] == 0.25
    assert rest.delete_effect(L, 0) == 204 and comp()["layers"][L - 1]["video"]["effects"] == []
    assert rest.delete_effect(L, 5) == 404
    assert rest.add_layer(before=1) == 204 and comp()["layers"][0]["name"]["value"].startswith("Layer")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("pass", name)
    print("OK")
```

- [ ] **Step 2: Run to see it fail** — `python tests/mock_resolume.py &` then `python tests/test_engine.py` → `AttributeError: 'Resolume' object has no attribute 'add_layer'`

- [ ] **Step 3: Add the REST calls** to `class Resolume` in `resolume_api.py` (after `set_param`):

```python
    # ---- composition editing (used by the step sequencer's layer engine) ---------- #
    def _post_text(self, path, body=""):
        r = self.session.post(self.api + path, data=body.encode("utf-8"),
                              headers={"Content-Type": "text/plain"}, timeout=3)
        return r.status_code

    def add_layer(self, before=None):
        """Append a layer on top, or insert before the 1-based layer index `before`."""
        return self._post_text("/composition/layers/add", f"/composition/layers/{before}" if before else "")

    def add_effect(self, layer, name):
        return self._post_text(f"/composition/layers/{layer}/effects/video/add",
                               "effect:///video/" + name.replace(" ", "%20"))

    def delete_effect(self, layer, offset):
        return self.session.delete(f"{self.api}/composition/layers/{layer}/effects/video/{offset}", timeout=3).status_code

    def set_effect_display_name(self, layer, index, name):
        return self._post_text(f"/composition/layers/{layer}/effects/video/{index}/set-display-name", name)

    def open_clip(self, layer, column, uri):
        """uri: 'source:///video/Metaballs' or 'file:///path/with%20spaces.mov'."""
        return self._post_text(f"/composition/layers/{layer}/clips/{column}/open", uri)

    def clear_clip(self, layer, column):
        return self._post_text(f"/composition/layers/{layer}/clips/{column}/clear")
```

Add `import queue` at the top of `resolume_api.py` if missing (it is already imported for `Sender`). In `ResolumeWS.__init__` add `self.outbox = queue.Queue()` and the method:

```python
    def set(self, pid, value):
        """Set a parameter over the socket (cheaper than a PUT). Dropped when not live."""
        if self.live:
            self.outbox.put({"action": "set", "parameter": f"/parameter/by-id/{pid}", "value": value})
```

In `ResolumeWS.run` change `ws.settimeout(0.5)` to `ws.settimeout(0.02)` and, right after the `except websocket.WebSocketTimeoutException: pass` block (still inside the inner `while True`), drain the outbox:

```python
                    while True:
                        try:
                            ws.send(json.dumps(self.outbox.get_nowait()))
                        except queue.Empty:
                            break
```

- [ ] **Step 4: Extend the mock.** In `tests/mock_resolume.py` add after `def clip(...)`:

```python
SOURCE_PARAMS = {"Metaballs": lambda: {"Grid": rng(20, 1, 20), "Color": color("#ff0000ff")},
                 "Checkered": lambda: {"Size": rng(0.5)},
                 "Solid Color": lambda: {"Color": color("#ffffffff")}}
def source_clip(name):
    c = clip(name)
    c["video"]["description"] = name
    c["video"]["fileinfo"] = None
    c["video"]["sourceparams"] = (SOURCE_PARAMS.get(name) or (lambda: {"Opacity": rng(1.0)}))()
    return c
def file_clip(path):
    c = clip(path.rsplit("/", 1)[-1])
    c["video"]["fileinfo"] = {"path": path}
    return c
def crop_effect():
    return {"name": "Crop", "display_name": "Crop", "id": next(ids), "bypassed": b(False),
            "params": {"Opacity": rng(1.0), "Left": rng(0.0, 0, 16384), "Right": rng(1920.0, 0, 16384),
                       "Top": rng(0.0, 0, 16384), "Bottom": rng(1080.0, 0, 16384), "Invert": b(False), "Black BG": b(False)}}
def new_layer(n):
    return {"name": s(f"Layer {n}"), "bypassed": b(False), "solo": b(False), "master": rng(1.0),
            "video": {"opacity": rng(1.0), "mixer": {"Blend Mode": {"id": next(ids), "valuetype": "ParamChoice", "value": "Alpha", "index": 0, "options": ["Alpha", "Add", "Multiply", "Screen"]}}, "effects": []},
            "clips": [clip(None) for _ in range(4)]}
OPACITY_LOG = []   # [t, pid, value] for every write to a video/opacity param (GET /api/v1/_opacity_log)
def note_opacity(pid, value):
    for layer in COMP["layers"]:
        if layer["video"]["opacity"]["id"] == pid:
            OPACITY_LOG.append([time.time(), pid, value])
```

Add `import time` and `from urllib.parse import unquote` to the mock's imports. In `do_GET` add before the composition fallback:

```python
        if self.path.endswith("/_opacity_log"):
            d = json.dumps(OPACITY_LOG).encode(); self.send_response(200); self.send_header("Content-Type","application/json"); self.end_headers(); self.wfile.write(d); return
```

In `do_POST`, right after `parts = self.path.split("/")` (path parts: `['', 'api', 'v1', 'composition', 'layers', L, ...]`), add:

```python
        if parts[3:] == ["composition", "layers", "add"]:
            L = new_layer(len(COMP["layers"]) + 1); index(L)
            if b.strip().startswith("/composition/layers/"):
                COMP["layers"].insert(int(b.strip().rsplit("/", 1)[-1]) - 1, L)
            else:
                COMP["layers"].append(L)
            self.send_response(204); self.end_headers(); return
        if len(parts) > 8 and parts[6] == "effects" and parts[7] == "video":
            layer = COMP["layers"][int(parts[5]) - 1]
            if parts[8] == "add":
                name = unquote(b.strip().rsplit("/", 1)[-1])
                if name == "Crop": fx = crop_effect()
                elif name == "Slice Transform": fx = {"name": "ScreenLayerTransform", "display_name": "Slice Transform", "id": next(ids), "bypassed": b_(False), "params": {"Opacity": rng(1.0)}}
                else: self.send_response(400); self.end_headers(); return
                index(fx); layer["video"]["effects"].append(fx)
            elif len(parts) > 9 and parts[9] == "set-display-name":
                layer["video"]["effects"][int(parts[8])]["display_name"] = b.strip()
            self.send_response(204); self.end_headers(); return
        if len(parts) > 8 and parts[6] == "clips" and parts[8] in ("open", "clear"):
            layer, C = COMP["layers"][int(parts[5]) - 1], int(parts[7]) - 1
            if parts[8] == "clear": c = clip(None)
            elif b.startswith("source:///video/"): c = source_clip(unquote(b[len("source:///video/"):].strip()))
            else: c = file_clip(unquote(b[len("file://"):].strip()))
            index(c); layer["clips"][C] = c
            self.send_response(204); self.end_headers(); ws_broadcast(); return
```

(The mock's boolean helper is named `b` and the request body variable is also `b`: rename the helper to `b_` throughout the mock — `def b_(v)` — and update its three existing uses (`"bypassed": b_(True)` in the Colorize effect, `"bypassed": b_(False), "solo": b_(False)` in each layer) and the `new_layer` / `crop_effect` helpers above.)

Add `do_DELETE`:

```python
    def do_DELETE(self):
        parts = self.path.split("/")
        if len(parts) > 8 and parts[6] == "effects" and parts[7] == "video":
            fx = COMP["layers"][int(parts[5]) - 1]["video"]["effects"]; i = int(parts[8])
            if i >= len(fx): self.send_response(404); self.end_headers(); return
            fx.pop(i); self.send_response(204); self.end_headers(); return
        self.send_response(404); self.end_headers()
```

In `do_PUT` after `elif "value" in body: BYID[pid]["value"] = body["value"]` add `; note_opacity(pid, body["value"])` (same line, after the assignment). In `ws_session` add the `set` action:

```python
                elif msg.get("action") == "set" and pid in BYID:
                    BYID[pid]["value"] = msg.get("value"); note_opacity(pid, msg.get("value")); ws_broadcast()
```

- [ ] **Step 5: Run the test** — restart the mock (`pkill -f mock_resolume.py; python tests/mock_resolume.py &`), `python tests/test_engine.py` → `pass test_rest_additions`, `OK`. Then run the existing suites to be sure nothing broke: `python tests/test_fake_push.py` → `OK`.

- [ ] **Step 6: Lint, commit**

```bash
python -m pyflakes resolume_api.py tests/mock_resolume.py tests/test_engine.py
git add resolume_api.py tests/mock_resolume.py tests/test_engine.py
git commit -m "Resolume API: add layer/effect, open/clear clip, effect display name, WebSocket set; mock endpoints + opacity log

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: LayerEngine

**Files:**
- Create: `chaser_engine.py`
- Modify: `tests/test_engine.py`

**Interfaces:**
- Consumes: `Bar` (Task 1); `Resolume` calls (Task 5); `resolume_api.resolve_node`, `text`, `is_param`.
- Produces: `MARKER = "CH:"`; `LayerEngine(rest, get_comp, refresh, send_level, prefix="CH: ", clip_column=1)` with `marker(track, bar_name)`, staticmethod `is_bar_layer(layer_json) -> bool`, `find_layers(comp=None) -> {(track, bar name): (layer index 1-based, layer json)}`, `ready(track) -> bool`, `setup(bars, tracks, dry_run=False) -> list[str]`, `load_texture(track, clip_json, bars=None) -> str`, `set_level(track, bar_name, value)`, `all_dark()`. `get_comp()` returns the bridge's current composition, `refresh()` fetches a fresh one from Resolume and stores it in the bridge, `send_level(pid, value)` sends an opacity.

- [ ] **Step 1: Add the failing test** to `tests/test_engine.py`:

```python
def test_layer_engine():
    from chaser_engine import LayerEngine
    from sequencer import Bar
    state = {"comp": comp()}
    sent = []
    eng = LayerEngine(rest, lambda: state["comp"], lambda: state.update(comp=comp()),
                      lambda pid, v: (sent.append((pid, v)), rest.set_param(pid, {"value": v})))
    bars = [Bar("Bar A", 145, 72, 175, 530, ["Bar A"]), Bar("Bar B", 345, 70, 375, 532, ["Bar B"])]
    n0 = len(state["comp"]["layers"])
    plan = eng.setup(bars, 2, dry_run=True)
    assert len(state["comp"]["layers"]) == n0 and any("create" in p for p in plan), plan
    report = eng.setup(bars, 2)
    c = state["comp"]
    assert len(c["layers"]) == n0 + 4, report
    found = eng.find_layers()
    assert set(found) == {(0, "Bar A"), (0, "Bar B"), (1, "Bar A"), (1, "Bar B")}
    L, layer = found[(1, "Bar B")]
    crop = layer["video"]["effects"][0]
    assert crop["display_name"] == "CH:T2:Bar B" and crop["params"]["Left"]["value"] == 345
    assert crop["params"]["Bottom"]["value"] == 532 and layer["video"]["mixer"]["Blend Mode"]["value"] == "Add"
    assert layer["video"]["opacity"]["value"] == 0.0 and layer["name"]["value"] == "CH: T2 Bar B"
    assert LayerEngine.is_bar_layer(layer) and not LayerEngine.is_bar_layer(c["layers"][0])
    assert eng.ready(1) and not eng.ready(2)
    bars[1].right = 380
    eng.setup(bars, 2)                                             # idempotent: updates, no new layers
    assert len(state["comp"]["layers"]) == n0 + 4
    assert eng.find_layers()[(1, "Bar B")][1]["video"]["effects"][0]["params"]["Right"]["value"] == 380
    # texture: the mock's Strobe/Stroboscope clip is a generator with sourceparams
    src = state["comp"]["layers"][0]["clips"][0]
    src["video"]["description"] = "Metaballs"
    msg = eng.load_texture(0, src)
    assert "2 bars" in msg, msg
    for key in ((0, "Bar A"), (0, "Bar B")):
        L, layer = eng.find_layers()[key]
        cl = layer["clips"][0]
        assert cl["video"]["description"] == "Metaballs" and cl["connected"]["value"].startswith("Connected")
        assert cl["video"]["sourceparams"]["Color"]["value"] == src["video"]["sourceparams"]["Color"]["value"]
    assert "select a clip" in eng.load_texture(0, None)
    # levels: rate limited, edges always sent, quantised to 1/255
    eng.set_level(0, "Bar A", 0.5); eng.set_level(0, "Bar A", 0.5); eng.set_level(0, "Bar A", 0.501)
    assert len(sent) == 1 and abs(sent[0][1] - 0.5) < 0.01
    eng.set_level(0, "Bar A", 0.7)                                 # too soon after the last send → skipped
    assert len(sent) == 1
    eng.set_level(0, "Bar A", 0.0)                                 # edge → always sent
    assert len(sent) == 2 and sent[-1][1] == 0.0
    time.sleep(0.03)
    eng.set_level(0, "Bar A", 0.7); assert len(sent) == 3
    eng.all_dark()
    assert sent[-1][1] == 0.0 and len([1 for p, v in sent if v == 0.0]) >= 4
```

- [ ] **Step 2: Run to see it fail** — `ModuleNotFoundError: No module named 'chaser_engine'`

- [ ] **Step 3: Create `chaser_engine.py`:**

```python
"""Layer engine for the step sequencer: one Resolume layer per (track, bar).

A bar layer is recognised by its Crop effect's display name `CH:T<track>:<bar name>` (the
marker; effect display names are settable through the API). The layer itself is named
`CH: T<track> <bar name>` when Resolume accepts the rename. Levels 0-1 become the layer's
video/opacity."""

from __future__ import annotations

import time
from urllib.parse import quote

from resolume_api import is_param, resolve_node, text, walk

MARKER = "CH:"
MIN_DT = 0.02          # s between two sends of the same layer (50/s); 30/s when many change
MANY = 16


class LayerEngine:
    def __init__(self, rest, get_comp, refresh, send_level, prefix="CH: ", clip_column=1):
        self.rest, self.get_comp, self.refresh, self.send_level = rest, get_comp, refresh, send_level
        self.prefix, self.clip_column = prefix, int(clip_column)
        self._last = {}            # opacity param id -> (time, quantised value)
        self._pids = {}            # (track, bar) -> opacity param id
        self._pids_comp = None

    def marker(self, track, bar):
        return f"{MARKER}T{track + 1}:{bar}"

    @staticmethod
    def _marker_of(layer):
        for fx in (layer.get("video") or {}).get("effects") or []:
            dn = text(fx.get("display_name"))
            if dn.startswith(MARKER + "T") and ":" in dn[len(MARKER) + 1:]:
                return dn
        return None

    @staticmethod
    def is_bar_layer(layer):
        return LayerEngine._marker_of(layer) is not None

    def find_layers(self, comp=None):
        comp = comp or self.get_comp() or {}
        out = {}
        for i, layer in enumerate(comp.get("layers") or [], 1):
            m = self._marker_of(layer)
            if m:
                t, bar = m[len(MARKER) + 1:].split(":", 1)
                if t.isdigit():
                    out[(int(t) - 1, bar)] = (i, layer)
        return out

    def ready(self, track):
        return any(t == track for t, _ in self.find_layers())

    # ---- setup ------------------------------------------------------------ #
    def _crop_of(self, layer):
        for k, fx in enumerate((layer.get("video") or {}).get("effects") or []):
            if fx.get("name") == "Crop" and self._marker_of({"video": {"effects": [fx]}}):
                return k, fx
        return None, None

    def _set_rect(self, crop, bar):
        for key, val in (("Left", bar.left), ("Right", bar.right), ("Top", bar.top), ("Bottom", bar.bottom)):
            p = (crop.get("params") or {}).get(key)
            if is_param(p) and p.get("value") != val:
                self.rest.set_param(p["id"], {"value": val})

    def setup(self, bars, tracks, dry_run=False):
        """Ensure a layer per (track, bar). Returns report lines."""
        report = []
        found = self.find_layers()
        for t in range(tracks):
            for bar in bars:
                key = (t, bar.name)
                if key in found:
                    L, layer = found[key]
                    if dry_run:
                        report.append(f"keep   layer {L} {self.marker(t, bar.name)}")
                        continue
                    _, crop = self._crop_of(layer)
                    self._set_rect(crop, bar)
                    self._finish_layer(L, layer, t, bar)
                    report.append(f"update layer {L} {self.marker(t, bar.name)}")
                    continue
                if dry_run:
                    report.append(f"create layer {self.marker(t, bar.name)} crop {bar.left},{bar.top}-{bar.right},{bar.bottom}")
                    continue
                code = self.rest.add_layer()
                if code != 204:
                    report.append(f"FAIL add layer for {bar.name}: HTTP {code}")
                    continue
                self.refresh()
                L = len(self.get_comp()["layers"])
                code = self.rest.add_effect(L, "Crop")
                if code != 204:
                    report.append(f"FAIL add Crop on layer {L}: HTTP {code}")
                    continue
                self.refresh()
                layer = self.get_comp()["layers"][L - 1]
                idx = len(layer["video"]["effects"]) - 1
                self.rest.set_effect_display_name(L, idx, self.marker(t, bar.name))
                self.refresh()
                layer = self.get_comp()["layers"][L - 1]
                self._set_rect(layer["video"]["effects"][idx], bar)
                self._finish_layer(L, layer, t, bar)
                report.append(f"create layer {L} {self.marker(t, bar.name)}")
        if not dry_run:
            self.refresh()
            self._pids_comp = None
        return report

    def _finish_layer(self, L, layer, t, bar):
        """Blend Add, opacity 0, best-effort name."""
        bm = resolve_node(layer, "video/mixer/Blend Mode")
        if is_param(bm) and bm.get("value") != "Add" and "Add" in (bm.get("options") or []):
            self.rest.set_param(bm["id"], {"value": "Add"})
        op = resolve_node(layer, "video/opacity")
        if is_param(op) and op.get("value") != 0.0:
            self.rest.set_param(op["id"], {"value": 0.0})
        name = layer.get("name")
        want = f"{self.prefix}T{t + 1} {bar.name}"
        if is_param(name) and text(name) != want:
            try:
                self.rest.set_param(name["id"], {"value": want})
            except Exception:
                pass

    # ---- texture ---------------------------------------------------------- #
    def load_texture(self, track, clip_json, bars=None):
        if not clip_json or not (clip_json.get("video") or {}):
            return "select a clip first"
        video = clip_json["video"]
        desc = text(video.get("description"))
        info = video.get("fileinfo") if isinstance(video.get("fileinfo"), dict) else {}
        path = info.get("path") if info else None
        if path:
            uri = "file://" + quote(path)
        elif desc:
            uri = "source:///video/" + quote(desc)
        else:
            return "file clips: not supported yet"
        targets = [(k, v) for k, v in self.find_layers().items()
                   if k[0] == track and (bars is None or k[1] in bars)]
        if not targets:
            return f"T{track + 1}: no bar layers — press Shift + Note"
        for _, (L, _) in targets:
            self.rest.open_clip(L, self.clip_column, uri)
        self.refresh()
        src_params = {p: prm for p, prm in walk(video.get("sourceparams") or {}, "", types={"ParamRange", "ParamChoice", "ParamBoolean", "ParamColor"})}
        for key, (L, _) in targets:
            layer = self.get_comp()["layers"][L - 1]
            clip = layer["clips"][self.clip_column - 1]
            dst = {p: prm for p, prm in walk((clip.get("video") or {}).get("sourceparams") or {}, "", types={"ParamRange", "ParamChoice", "ParamBoolean", "ParamColor"})}
            for p, prm in src_params.items():
                if p in dst and dst[p].get("value") != prm.get("value"):
                    self.rest.set_param(dst[p]["id"], {"value": prm.get("value")})
            self.rest.connect_clip(L, self.clip_column, True)
            self.rest.connect_clip(L, self.clip_column, False)
        self.refresh()
        return f"T{track + 1}: {text(clip_json.get('name')) or desc} → {len(targets)} bar{'s' * (len(targets) != 1)}"

    # ---- levels ------------------------------------------------------------ #
    def _pid(self, track, bar):
        comp = self.get_comp()
        if comp is not self._pids_comp:
            self._pids, self._pids_comp = {}, comp
            for key, (_, layer) in self.find_layers(comp).items():
                op = resolve_node(layer, "video/opacity")
                if is_param(op):
                    self._pids[key] = op["id"]
        return self._pids.get((track, bar))

    def set_level(self, track, bar, value, now=None):
        pid = self._pid(track, bar)
        if pid is None:
            return
        now = now or time.time()
        q = round(max(0.0, min(1.0, float(value))) * 255) / 255
        last = self._last.get(pid)
        if last and last[1] == q:
            return
        busy = sum(1 for t, _ in self._last.values() if now - t < 0.1) > MANY
        if last and q != 0.0 and now - last[0] < (0.033 if busy else MIN_DT):
            return
        self._last[pid] = (now, q)
        self.send_level(pid, q)

    def all_dark(self):
        for key in list(self.find_layers()):
            self._last.pop(self._pid(*key), None)
            self.set_level(key[0], key[1], 0.0)
```

- [ ] **Step 4: Run the test** — restart the mock, `python tests/test_engine.py` → `pass test_layer_engine`, `OK`. If `find_layers` returns nothing after setup, check that the mock's `set-display-name` handler stored `b.strip()` and that `refresh()` in the test replaces `state["comp"]`.

- [ ] **Step 5: Lint, 3.9 check, commit**

```bash
python -m pyflakes chaser_engine.py tests/test_engine.py
python -c "import ast; ast.parse(open('chaser_engine.py').read(), feature_version=(3,9))"
git add chaser_engine.py tests/test_engine.py
git commit -m "SEQ: LayerEngine builds bar layers, loads textures, sets levels

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: Hide bar layers from the pad grid and MIX

**Files:**
- Modify: `push_resolume_bridge.py` (`mix_layers` ~line 223, `pad_to_cell` ~line 764, `button()` Up/Down limits ~line 868, `flash_layer` ~line 727, paste column cells ~line 834, `button_colors` scene buttons ~line 973)
- Modify: `tests/test_fake_push.py`

**Interfaces:**
- Produces on `Bridge`: `visible_layers() -> list[int]` (1-based indices of layers that are not bar layers); `pad_to_cell(i, j)` returns `(0, C)` for rows beyond the visible list (`clip_json(0, C)` is `None`).

- [ ] **Step 1: Add the failing test** to `tests/test_fake_push.py`, just before `counts = {}` (the test already has `rest` and `fire`; it uses the API to build one bar layer):

```python
# --- bar layers (CH:) are hidden from the pad grid and MIX
n_before = len(rest.composition()["layers"])
assert rest.add_layer() == 204
assert rest.add_effect(n_before + 1, "Crop") == 204
assert rest.set_effect_display_name(n_before + 1, 0, "CH:T1:Test bar") == 204
time.sleep(1.2)                                   # WebSocket / poll picks the new layer up
n0 = len(calls)
fire("on_button_pressed", "Up"); fire("on_button_pressed", "Down")
time.sleep(0.3)
top_pads = [a for n, a in calls[n0:] if n == "pad" and a[0][0] == 0]
assert all(a[1] == "black" for a in top_pads), "hidden bar layer must not appear on the top pad row"
fire("on_button_pressed", "Mix"); time.sleep(0.3)
assert ("btn", ("Lower Row 4", "black")) in calls[n0:], "MIX must show only the 3 visible layers"
fire("on_button_pressed", "Mix")
```

- [ ] **Step 2: Run to see it fail** — restart the mock, `python tests/test_fake_push.py` → the assertion about the top pad row (the new layer 4 shows up as the top row) or the MIX assertion.

- [ ] **Step 3: Implement.** In `push_resolume_bridge.py` add near the top: `from chaser_engine import LayerEngine`. Add to `Bridge` after `layers()`:

```python
    def visible_layers(self):
        """1-based indices of layers shown on the pads and in MIX (bar layers are hidden)."""
        return [i for i, l in enumerate(self.layers(), 1) if not LayerEngine.is_bar_layer(l)]
```

Replace `mix_layers`:

```python
    def mix_layers(self):
        """Layers on encoders 1-8 in mix mode: top visible layer first."""
        vis = self.visible_layers()
        top = min(self.layer_offset + 8, len(vis))
        return [vis[k] for k in range(top - 1, self.layer_offset - 1, -1)][:8]
```

Replace `pad_to_cell`:

```python
    def pad_to_cell(self, i, j):
        vis = self.visible_layers()
        k = self.layer_offset + (7 - i)
        return (vis[k] if k < len(vis) else 0), self.col_offset + j + 1
```

In `flash_layer` replace `L = self.layer_offset + (8 - row)` with `L = self.pad_to_cell(row, 0)[0]`. In `button()`'s paste branch replace `L = self.layer_offset + (8 - SCENE_BUTTONS.index(name))` with `L = self.pad_to_cell(SCENE_BUTTONS.index(name), 0)[0]` and `cells = [(l, C) for l in range(1, len(self.layers()) + 1)]` with `cells = [(l, C) for l in self.visible_layers()]`. In `button()` replace `max_l = max(0, len(self.layers()) - 8)` with `max_l = max(0, len(self.visible_layers()) - 8)`. In `button_colors` replace `L = self.layer_offset + (8 - i)` (scene buttons loop) with `L = self.pad_to_cell(i, 0)[0]`. In `paste_color` nothing changes (it takes cells).

- [ ] **Step 4: Run the tests** — restart the mock; `python tests/test_fake_push.py` → `OK`; `TEST_POLL=1 python tests/test_fake_push.py` → `OK`.

- [ ] **Step 5: Lint, commit**

```bash
python -m pyflakes push_resolume_bridge.py tests/test_fake_push.py
git add push_resolume_bridge.py tests/test_fake_push.py
git commit -m "Hide bar layers (CH:) from the pad grid and MIX

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: SEQ mode — state, pads, tracks, patterns, LEDs, clock

**Files:**
- Modify: `push_resolume_bridge.py` (constants ~line 75-125, `DEFAULT_CONFIG` ~line 126, `Bridge.__init__` ~line 159, `pad_pressed` / `pad_released` ~line 767, `button()` ~line 807, `button_colors` ~line 926, `pad_colors` ~line 987, `run()` ~line 1107)
- Modify: `tests/test_fake_push.py`

**Interfaces:**
- Consumes: `Sequencer` (Tasks 3-4), `LayerEngine` (Task 6), `load_preset_bars` / `newest_preset` (Task 1), `ResolumeWS.set` (Task 5).
- Produces on `Bridge`: `seq` (Sequencer), `engine` (LayerEngine), `bars` (list[Bar]), `bar_warnings`, `beat_time(now=None) -> float | None`, `_send_level(pid, value)`, `refresh_comp()`, `seq_loop()` (thread), `_seq_pad(ij, velocity, down)`, `_seq_button(name, down) -> bool`, `run_setup(dry_run=False) -> list[str]`, `bar_index(i, j) -> int | None`, `pattern_index(i, j) -> int`, state flags `repeat`, `accent`, `delete_held`, `browse_held`, `browse_used`, `fixed_len_held`, `held_steps`, `held_bars`, `dup_src`, `seq_note`. `pad_pressed(ij, velocity=100)`.

- [ ] **Step 1: Add the failing test** to `tests/test_fake_push.py` before `counts = {}`. It needs the preset fixture and a temp chases file — add to the config block near the top of the test (after `cfg["colors_file"] = str(colors_file)`):

```python
cfg["sequencer"] = {"preset": str(Path(__file__).resolve().parent / "fixtures" / "preset_small.xml"),
                    "bars": "auto", "disabled": ["Bar A copy"], "layer_prefix": "CH: ", "clip_column": 1, "tracks": 4}
cfg["chases_file"] = str(pins.with_name("chases.yaml"))
```

and the block:

```python
# --- F18 SEQ: setup, steps, run, LEDs
fire("on_pad_pressed", 60, (7, 0), 100); fire("on_pad_released", 60, (7, 0), 0)     # select L1 C1 (Stroboscope)
fire("on_button_pressed", "Shift"); fire("on_button_pressed", "Note")               # Shift + Note = setup
fire("on_button_released", "Note"); fire("on_button_released", "Shift")
time.sleep(2.5)
names = [e["display_name"] for l in rest.composition()["layers"] for e in l["video"]["effects"] if e["name"] == "Crop"]
assert "CH:T1:Bar A" in names and "CH:T1:Bar C" in names and "CH:T1:Bar A copy" not in names, names
fire("on_button_pressed", "Note")                                                    # SEQ mode
time.sleep(0.3)
assert ("btn", ("Note", "white")) in calls and ("btn", ("Lower Row 1", "L0")) in calls, "SEQ LEDs"
fire("on_button_pressed", "Browse"); fire("on_button_released", "Browse")           # texture → track 1
time.sleep(1.5)
ch = [l for l in rest.composition()["layers"] if l["name"]["value"].startswith("CH: T1")]
assert ch and all(l["clips"][0]["video"]["description"] == "Stroboscope" for l in ch), "Browse must load the texture"
fire("on_button_pressed", "1/16"); fire("on_button_released", "1/16")               # grid 1/16
fire("on_pad_pressed", 60, (7, 0), 100); fire("on_pad_released", 60, (7, 0), 0)     # select bar 1 (Bar A) + audition
fire("on_pad_pressed", 60, (0, 0), 127); fire("on_pad_released", 60, (0, 0), 0)     # step 1 on
fire("on_pad_pressed", 60, (0, 4), 64); fire("on_pad_released", 60, (0, 4), 0)      # step 5 on, half level
fire("on_pad_pressed", 60, (7, 1), 100); fire("on_pad_released", 60, (7, 1), 0)     # select bar 2 (Bar B)
fire("on_pad_pressed", 60, (0, 2), 127); fire("on_pad_released", 60, (0, 2), 0)     # step 3 on
time.sleep(0.3)
assert ("pad", ((0, 2), "L0")) in calls, "step pads show the track colour"
chases = yaml.safe_load(cfg["chases_file"] and open(cfg["chases_file"]).read())
assert chases["patterns"][0]["tracks"][0]["steps"]["Bar A"][1][1] < 0.6, "step level = pad velocity"
log0 = len(requests.get("http://127.0.0.1:8080/api/v1/_opacity_log").json())
fire("on_button_pressed", "Play")                                                    # run
time.sleep(2.6)                                                                      # > one 16-step pattern at 120 BPM
fire("on_button_pressed", "Play")                                                    # stop
time.sleep(0.5)
log = requests.get("http://127.0.0.1:8080/api/v1/_opacity_log").json()[log0:]
ids = {l["name"]["value"]: l["video"]["opacity"]["id"] for l in rest.composition()["layers"] if l["name"]["value"].startswith("CH: T1")}
a_on = [t for t, pid, v in log if pid == ids["CH: T1 Bar A"] and v > 0.9]
b_on = [t for t, pid, v in log if pid == ids["CH: T1 Bar B"] and v > 0.9]
assert a_on and b_on and a_on[0] < b_on[0], "Bar A (step 1) must flash before Bar B (step 3)"
assert any(pid == ids["CH: T1 Bar A"] and v == 0.0 for _, pid, v in log), "release must reach 0"
assert ("btn", ("Play", "green")) in calls
fire("on_button_pressed", "Session"); time.sleep(0.3)
assert ("btn", ("Note", "dark_gray")) in calls[-400:], "Session leaves SEQ"
```

- [ ] **Step 2: Run to see it fail** — `python tests/test_fake_push.py` → the first assertion (no `CH:T1:Bar A` layer) fails.

- [ ] **Step 3: Constants and config.** In `push_resolume_bridge.py` add after `FX_SOURCES`:

```python
SEQ_BUTTON = "Upper Row 4"                  # BU4 (also Note)
NOTE_BUTTON, SESSION_BUTTON = "Note", "Session"
BROWSE_BUTTON, REPEAT_BUTTON, ACCENT_BUTTON = "Browse", "Repeat", "Accent"
DELETE_BUTTON, DOUBLE_LOOP_BUTTON, FIXED_LENGTH_BUTTON = "Delete", "Double Loop", "Fixed Length"
OCTAVE_UP, OCTAVE_DOWN = "Octave Up", "Octave Down"
SWING_ENCODER = "Swing Encoder"
SEQ_KNOBS = ["Attack", "Decay", "Sustain", "Release", "Gate", "Direction", "Length", "Level"]
SEQ_HZ = 100          # sequencer clock ticks per second
PRESET_FOLDER = Path.home() / "Documents" / "Resolume Arena" / "Presets" / "Advanced Output"
```

Change `MENU_BUTTONS = {PARAMS_BUTTON: "params", COLOR_BUTTON: "color", FX_BUTTON: "fx"}` to include `SEQ_BUTTON: "seq"`. In `DEFAULT_CONFIG` add:

```python
    "chases_file": None,   # step sequencer patterns; None = chases.yaml next to this script
    "sequencer": {"preset": "newest", "bars": "auto", "disabled": [], "layer_prefix": "CH: ",
                  "clip_column": 1, "tracks": 4},
```

Add imports: `from sequencer import GRIDS, Sequencer, load_preset_bars, newest_preset` (and `LayerEngine` is imported since Task 7).

- [ ] **Step 4: Bridge state.** In `Bridge.__init__` (after `self.midi_reset = False`):

```python
        # ---- step sequencer (SEQ) ----
        sc = cfg.get("sequencer") or {}
        self.seq = Sequencer(cfg.get("chases_file") or Path(__file__).with_name("chases.yaml"),
                             n_tracks=int(sc.get("tracks", 4)))
        self.bars, self.bar_warnings = self.load_bars()
        self.engine = LayerEngine(rest, lambda: self.comp, self.refresh_comp, self._send_level,
                                  sc.get("layer_prefix", "CH: "), sc.get("clip_column", 1))
        self.repeat = False
        self.accent = False
        self.delete_held = self.browse_held = self.fixed_len_held = False
        self.browse_used = False
        self.held_steps = set()    # step indices held on the pads
        self.held_bars = {}        # bar name -> track, while a bar pad is held
        self.dup_src = None        # Duplicate + pattern: source pattern
        self.seq_note = ""         # last setup / load message (shown on the display)
        self.last_grid_step = None
```

Add these methods to `Bridge` (after `desired_ids`):

```python
    def load_bars(self):
        sc = self.cfg.get("sequencer") or {}
        preset = sc.get("preset") or "newest"
        path = Path(preset) if str(preset).endswith(".xml") else None
        if path is None:
            path = newest_preset(PRESET_FOLDER) if preset == "newest" else PRESET_FOLDER / f"{preset}.xml"
        if not path or not Path(path).exists():
            return [], [f"no Advanced Output preset ({preset}) — save one in Arena"]
        groups = sc.get("bars") if isinstance(sc.get("bars"), list) else None
        try:
            bars, warnings = load_preset_bars(path, groups, sc.get("disabled") or [])
        except Exception as e:
            return [], [f"can't read {path}: {e}"]
        for w in warnings:
            print(f"[seq] {w}")
        return bars, warnings

    def refresh_comp(self):
        try:
            self.set_comp(self.rest.composition())
        except Exception as e:
            print(f"[seq] refresh failed: {e}", file=sys.stderr)

    def _send_level(self, pid, value):
        with self.lock:
            n = self.index.get(pid)
            if n is not None:
                n["value"] = value
        if self.live():
            self.ws.set(pid, value)
        else:
            self.sender.param(pid, {"value": value})

    def beat_time(self, now=None):
        """Continuous beats since the beat anchor, or None without a tempo."""
        p = self.tempo_param()
        bpm = float(self.value_of(p) or 0) if p else 0.0
        if bpm <= 0:
            return None
        t0, b0 = self.beat_anchor
        return b0 + ((now or time.time()) - t0) * bpm / 60.0

    def run_setup(self, dry_run=False):
        if not self.bars:
            msg = self.bar_warnings[0] if self.bar_warnings else "no bars"
            self.seq_note = msg
            return [msg]
        tracks = max(1, len([t for t in self.seq.pattern.tracks if t.texture]) or 1)
        report = self.engine.setup(self.bars, tracks, dry_run)
        self.seq_note = f"setup: {len(report)} layers" + (" (dry run)" if dry_run else "")
        self.note_msg(self.seq_note)
        return report

    def seq_loop(self):
        """100 Hz: feed beat time to the sequencer, send changed levels, handle Repeat."""
        while True:
            bt = self.beat_time()
            changed = {}
            if bt is not None:
                with self.lock:
                    sb = self.seq.step_beats()
                    g = math.floor(bt / sb)
                    if self.repeat and self.held_bars and g != self.last_grid_step:
                        for bar, track in self.held_bars.items():
                            tr = self.seq.pattern.tracks[track]
                            self.seq.trigger(track, bar, 1.0, tr.gate * sb, bt)
                    self.last_grid_step = g
                    changed = self.seq.tick(bt)
            for (track, bar), v in changed.items():
                self.engine.set_level(track, bar, v)
            time.sleep(1.0 / SEQ_HZ)

    # ---- SEQ pads --------------------------------------------------------------- #
    def bar_index(self, i, j):
        """Pad (row i, col j) in the bar block → index into self.bars, or None."""
        k = self.seq.bank * 16 + (7 - i) * 4 + j
        return k if k < len(self.bars) else None

    def pattern_index(self, i, j):
        return (7 - i) * 4 + (j - 4)

    def _seq_pad(self, ij, velocity, down):
        i, j = ij
        with self.lock:
            bt = self.beat_time() or 0.0
            if i < 4:                                              # steps
                step = i * 8 + j
                if not down:
                    self.held_steps.discard(step)
                    return
                bar = self.bars[self.seq.bar].name if self.seq.bar < len(self.bars) else None
                if bar is None or step >= self.seq.pattern.length:
                    return
                if self.delete_held:
                    st = self.seq._steps(bar)
                    st.pop(step, None)
                    self.seq.save()
                    return
                self.held_steps.add(step)
                self.seq.toggle_step(bar, step, 1.0 if self.accent else max(0.05, velocity / 127.0))
            elif j < 4:                                            # bars
                k = self.bar_index(i, j)
                if k is None:
                    return
                bar = self.bars[k].name
                track = self.seq.track
                if not down:
                    if bar in self.held_bars:
                        self.seq.release(self.held_bars.pop(bar), bar, bt)
                    return
                if self.delete_held:
                    self.seq.clear_steps(bar)
                    return
                if self.browse_held:
                    self.browse_used = True
                    self._load_texture(track, [bar])
                    return
                self.seq.bar = k
                self.held_bars[bar] = track
                self.seq.trigger(track, bar, 1.0, None, bt)
            else:                                                  # patterns
                if not down:
                    return
                p = self.pattern_index(i, j)
                if self.delete_held:
                    self.seq.clear_pattern(p)
                elif self.paste_held:
                    if self.dup_src is None:
                        self.dup_src = p
                    else:
                        src, self.dup_src = self.dup_src, None
                        self.seq.copy_pattern(src, p)
                        self.note_msg(f"P{src + 1} copied to P{p + 1}")
                else:
                    self.seq.switch_pattern(p, bt, now=self.shift)

    def _load_texture(self, track, bars=None):
        clip = self.clip_json(*self.sel)
        def work():
            if not self.engine.ready(track) and self.bars:
                self.engine.setup(self.bars, track + 1)
            msg = self.engine.load_texture(track, clip, bars)
            with self.lock:
                if clip and (clip.get("video") or {}):
                    v = clip["video"]
                    tex = ({"file": (v.get("fileinfo") or {}).get("path")} if isinstance(v.get("fileinfo"), dict) and (v.get("fileinfo") or {}).get("path")
                           else {"source": text(v.get("description"))})
                    self.seq.pattern.tracks[track].texture = tex
                    self.seq.save()
                self.note_msg(msg)
        threading.Thread(target=work, daemon=True).start()
```

- [ ] **Step 5: Wire pads and buttons.** Change `def pad_pressed(self, ij):` to `def pad_pressed(self, ij, velocity=100):` and insert as its first lines:

```python
        if self.mode == "seq":
            return self._seq_pad(ij, velocity, True)
```

In `pad_released` insert first: `if self.mode == "seq": return self._seq_pad(ij, 0, False)`. In `run()` change `_pad_down` to call `bridge.pad_pressed(pad_ij, velocity)`.

Add `_seq_button` to `Bridge` and call it at the top of `button()` (right after the `Shift` handling): `if self.mode == "seq" and self._seq_button(name, down): return`:

```python
    def _seq_button(self, name, down):
        """Buttons that mean something else while the pads are the sequencer. True = consumed."""
        bt = self.beat_time() or 0.0
        if name == DELETE_BUTTON:
            self.delete_held = down
            return True
        if name == FIXED_LENGTH_BUTTON:
            self.fixed_len_held = down
            return True
        if name == BROWSE_BUTTON:
            if down:
                self.browse_held, self.browse_used = True, False
            else:
                self.browse_held = False
                if not self.browse_used:
                    self._load_texture(self.seq.track)
            return True
        if name == PASTE_BUTTON:                                   # Duplicate: copy pattern
            self.paste_held = down
            if not down:
                self.dup_src = None
            return True
        if not down:
            return name in (PLAY_BUTTON, REPEAT_BUTTON, ACCENT_BUTTON, DOUBLE_LOOP_BUTTON,
                            OCTAVE_UP, OCTAVE_DOWN) or name in SCENE_BUTTONS or name in LOWER_ROW
        with self.lock:
            if name == PLAY_BUTTON:
                if self.seq.running:
                    self.seq.stop()
                else:
                    self.seq.start(bt)
                return True
            if name == REPEAT_BUTTON:
                self.repeat = not self.repeat
                return True
            if name == ACCENT_BUTTON:
                self.accent = not self.accent
                return True
            if name == DOUBLE_LOOP_BUTTON:
                self.seq.double_loop()
                return True
            if name in (OCTAVE_UP, OCTAVE_DOWN):
                banks = max(1, math.ceil(len(self.bars) / 16))
                self.seq.bank = min(banks - 1, max(0, self.seq.bank + (1 if name == OCTAVE_UP else -1)))
                return True
            if name in SCENE_BUTTONS:                              # grid, top button = 1/32t
                self.seq.grid = name
                return True
            if name in LOWER_ROW:
                k = LOWER_ROW.index(name)
                if self.fixed_len_held:
                    self.seq.set_length((k + 1) * 4)
                elif k < self.seq.n_tracks:
                    if self.delete_held:
                        self.seq.clear_steps(track=k)
                    elif self.browse_held:
                        self.browse_used = True
                        self._load_texture(k)
                    else:
                        self.seq.track = k
                return True
        return False
```

In the generic part of `button()` (inside `with self.lock:` where `MIX_BUTTON` etc. are handled) add:

```python
            elif name == NOTE_BUTTON:
                if self.shift:
                    threading.Thread(target=self.run_setup, daemon=True).start()
                else:
                    self.mode = "seq"
                    self.move_src = None
            elif name == SESSION_BUTTON:
                self.mode = "params"
```

and make sure `MENU_BUTTONS` entering `"seq"` also clears `held_steps` / `held_bars` (add `self.held_steps.clear(); self.held_bars.clear()` in the `MENU_BUTTONS` branch and in the `SESSION_BUTTON` branch).

- [ ] **Step 6: LEDs.** In `button_colors`, after the `out = {...}` dict, add:

```python
            seq = self.mode == "seq"
            out[NOTE_BUTTON] = "white" if seq else "dark_gray"
            out[SESSION_BUTTON] = "dark_gray" if seq else "white"
            out[SEQ_BUTTON] = "white" if seq else "dark_gray"
            for b_, on in ((REPEAT_BUTTON, self.repeat), (ACCENT_BUTTON, self.accent), (DELETE_BUTTON, self.delete_held),
                           (BROWSE_BUTTON, self.browse_held), (DOUBLE_LOOP_BUTTON, False), (FIXED_LENGTH_BUTTON, self.fixed_len_held),
                           (OCTAVE_UP, len(self.bars) > 16), (OCTAVE_DOWN, len(self.bars) > 16)):
                out[b_] = ("white" if on else "dark_gray") if seq else "black"
            if seq:
                out[PLAY_BUTTON] = "green" if self.seq.running else "dark_gray"
                out[PASTE_BUTTON] = "white" if self.paste_held else "dark_gray"
```

In the `for k, b in enumerate(LOWER_ROW):` loop add a first branch:

```python
                if seq:
                    if k < self.seq.n_tracks:
                        has = self.seq.pattern.tracks[k].texture is not None
                        out[b] = f"L{k}" if k == self.seq.track else (f"L{k}_dim" if has else "dark_gray")
                    else:
                        out[b] = "black"
                    continue
```

and for the scene buttons loop add before it: `if seq: for b_ in SCENE_BUTTONS: out[b_] = "white" if b_ == self.seq.grid else "dark_gray"` and skip the layer-based loop in seq mode (`else:` around the existing loop). Keep `MENU_BUTTONS` lighting as is (SEQ_BUTTON is in it).

In `pad_colors` add at the start of the locked block:

```python
            if self.mode == "seq":
                return self._seq_pad_colors()
```

and the method:

```python
    def _seq_pad_colors(self):
        grid = {}
        seq = self.seq
        pos = seq.position(self.beat_time() or 0.0)
        bar_name = self.bars[seq.bar].name if seq.bar < len(self.bars) else None
        steps = seq.pattern.tracks[seq.track].steps.get(bar_name, {}) if bar_name else {}
        tc = seq.track % 8
        lit = {}                                                   # bar name -> set of tracks with level > 0
        for (t, bar), v in seq.levels.items():
            if v > 0.02:
                lit.setdefault(bar, set()).add(t)
        for i in range(8):
            for j in range(8):
                color = "black"
                if i < 4:
                    step = i * 8 + j
                    if step < seq.pattern.length:
                        if step == pos:
                            color = "green"
                        elif step in steps:
                            lv = steps[step][0]
                            color = f"L{tc}" if lv >= 0.66 else (f"L{tc}_mid" if lv >= 0.33 else f"L{tc}_dim")
                        else:
                            color = "dark_gray"
                elif j < 4:
                    k = self.bar_index(i, j)
                    if k is not None:
                        name = self.bars[k].name
                        tracks = lit.get(name, set())
                        if len(tracks) > 1:
                            color = "white"
                        elif tracks:
                            color = f"L{next(iter(tracks)) % 8}"
                        elif k == seq.bar:
                            color = "light_gray"
                        else:
                            color = "dark_gray"
                else:
                    p = self.pattern_index(i, j)
                    has = any(t.steps for t in seq.patterns[p].tracks)
                    blink = int(time.time() / BLINK) % 2 == 0
                    color = ("white" if blink else "dark_gray") if seq.pending == p else \
                            "white" if p == seq.current else ("dark_gray" if has else "black")
                grid[(i, j)] = color
        return grid
```

- [ ] **Step 7: Start the clock thread** in `run()` after `bridge.sender.start()`: `threading.Thread(target=bridge.seq_loop, daemon=True).start()`. Also, in `turn()` before the mode checks add the SEQ knobs:

```python
            if self.mode == "seq":
                self._turn_seq(idx, inc)
                return
```

with

```python
    def _turn_seq(self, idx, inc):
        seq, tr = self.seq, self.seq.pattern.tracks[self.seq.track]
        fine = 0.2 if self.shift else 1.0
        bar = self.bars[seq.bar].name if seq.bar < len(self.bars) else None
        if self.held_steps and bar and idx in (4, 7):              # hold step + knob
            st = seq._steps(bar)
            for s in self.held_steps:
                if s in st:
                    if idx == 7:
                        seq.set_step_values(bar, [s], level=st[s][0] + inc * 0.02 * fine)
                    else:
                        seq.set_step_values(bar, [s], gate=(st[s][1] if st[s][1] is not None else tr.gate) + inc * 0.02 * fine)
            return
        e = tr.envelope
        if idx == 0:
            e.attack = max(0.0, min(4.0, e.attack + inc * 0.05 * fine))
        elif idx == 1:
            e.decay = max(0.0, min(4.0, e.decay + inc * 0.05 * fine))
        elif idx == 2:
            e.sustain = max(0.0, min(1.0, e.sustain + inc * 0.01 * fine))
        elif idx == 3:
            e.release = max(0.0, min(4.0, e.release + inc * 0.05 * fine))
        elif idx == 4:
            tr.gate = max(0.1, min(1.0, tr.gate + inc * 0.02 * fine))
        elif idx == 5:
            acc = self.choice_acc.get("seqdir", 0) + inc
            if abs(acc) >= 4:
                from sequencer import DIRECTIONS
                i = (DIRECTIONS.index(seq.pattern.direction) + (1 if acc > 0 else -1)) % len(DIRECTIONS)
                seq.set_direction(DIRECTIONS[i])
                acc = 0
            self.choice_acc["seqdir"] = acc
        elif idx == 6:
            seq.set_length(seq.pattern.length + inc)
        elif idx == 7:
            tr.level = max(0.0, min(1.0, tr.level + inc * 0.01 * fine))
        seq.save()
```

Add to `_enc_turn` in `run()`: `elif name == SWING_ENCODER: bridge.turn_swing(inc)` with

```python
    def turn_swing(self, inc):
        with self.lock:
            p = self.seq.pattern
            p.swing = max(0.0, min(1.0, p.swing + inc * 0.02))
            self.seq.save()
```

- [ ] **Step 8: Run the test** — restart the mock, `python tests/test_fake_push.py` → `OK`, and `TEST_POLL=1 python tests/test_fake_push.py` → `OK`. Also `python tests/test_engine.py` and `python tests/test_sequencer.py` → `OK`. Common failures: the setup thread not finished within 2.5 s (the mock is fast; if it is, check `run_setup` prints in the mock log); the `Browse` release loading nothing because `browse_used` was left True.

- [ ] **Step 9: Lint, 3.9 check, commit**

```bash
python -m pyflakes push_resolume_bridge.py tests/test_fake_push.py
python -c "import ast; ast.parse(open('push_resolume_bridge.py').read(), feature_version=(3,9))"
git add push_resolume_bridge.py tests/test_fake_push.py
git commit -m "SEQ mode on the Push: pads, tracks, patterns, knobs, LEDs, clock thread, setup

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: SEQ display

**Files:**
- Modify: `push_resolume_bridge.py` (`snapshot()` ~line 1011)
- Modify: `display.py` (`render()`, add a branch before `elif snap["mode"] == "fx":`)
- Modify: `tests/render_preview.py`

**Interfaces:**
- Produces: `snapshot()["seq"]` = `{"knobs": [(label, text, frac)] × 8, "track": int, "pattern": str, "running": bool, "pos": int|None, "length": int, "grid": str, "bars": (first, last), "bar": str, "texture": str, "env": Envelope-as-dict, "pending": int|None, "note": str}` when `mode == "seq"`, else `None`.

- [ ] **Step 1: Add the preview** to `tests/render_preview.py` before the `br.online = False` block:

```python
br.mode = "seq"
br.bars = [B.Bar("Lumiverse 1", 145, 72, 175, 530), B.Bar("Lumiverse 2", 345, 70, 375, 532)]
br.seq.toggle_step("Lumiverse 1", 0); br.seq.toggle_step("Lumiverse 1", 8)
br.seq.pattern.tracks[0].texture = {"source": "Metaballs"}
br.seq.pattern.tracks[0].envelope = B.Envelope(attack=0.2, decay=0.3, sustain=0.6, release=0.5)
br.seq.start(br.beat_time() or 0.0)
shot("seq")
br.seq.stop(); br.mode = "params"
```

and re-export `Bar` and `Envelope` in the bridge (`from sequencer import Bar, Envelope, GRIDS, Sequencer, load_preset_bars, newest_preset  # noqa: F401`).

- [ ] **Step 2: Run it to see it fail** — `python tests/render_preview.py` → `KeyError: 'seq'` (or the render draws the params view). We assert visually afterwards.

- [ ] **Step 3: Snapshot data.** In `snapshot()` add before `return {`:

```python
            seq = None
            if self.mode == "seq":
                sq, tr = self.seq, self.seq.pattern.tracks[self.seq.track]
                e = tr.envelope
                beats = lambda v: (f"{v:.2f} b", min(1.0, v / 4.0))
                pct = lambda v: (f"{v * 100:.0f}%", v)
                knobs = [("Attack",) + beats(e.attack), ("Decay",) + beats(e.decay), ("Sustain",) + pct(e.sustain),
                         ("Release",) + beats(e.release), ("Gate",) + pct(tr.gate),
                         ("Direction", sq.pattern.direction, 0.0), ("Length", f"{sq.pattern.length} steps", sq.pattern.length / 32),
                         ("Level",) + pct(tr.level)]
                first = sq.bank * 16 + 1
                seq = {"knobs": knobs, "track": sq.track, "pattern": sq.pattern.name, "running": sq.running,
                       "pos": sq.position(self.beat_time() or 0.0), "length": sq.pattern.length, "grid": sq.grid,
                       "bars": (first, min(first + 15, len(self.bars))),
                       "bar": self.bars[sq.bar].name if sq.bar < len(self.bars) else "—",
                       "texture": (tr.texture or {}).get("source") or (tr.texture or {}).get("file", "").rsplit("/", 1)[-1] or "no texture — Browse",
                       "env": {"attack": e.attack, "decay": e.decay, "sustain": e.sustain, "release": e.release, "gate": tr.gate},
                       "pending": sq.pending, "swing": sq.pattern.swing, "n_bars": len(self.bars),
                       "warning": self.bar_warnings[0] if not self.bars and self.bar_warnings else ""}
```

and `"seq": seq,` in the returned dict.

- [ ] **Step 4: Draw it.** In `display.py` add before `elif snap["mode"] == "fx":`:

```python
    elif snap["mode"] == "seq":
        sq = snap["seq"]
        tcol = LAYER_RGB[sq["track"] % 8]
        for k in range(8):
            x = k * 120
            if snap["touched"] == k:
                col((45, 45, 45)); ctx.rectangle(x, 0, 120, 98); ctx.fill()
            if k:
                col((35, 35, 35)); ctx.rectangle(x, 6, 1, 86); ctx.fill()
            label, value, frac = sq["knobs"][k]
            col((150, 150, 150)); font(15)
            say(x + 8, 24, label, 104)
            col((255, 255, 255)); font(20, True)
            say(x + 8, 58, str(value), 104)
            col((45, 45, 45)); ctx.rectangle(x + 8, 72, 104, 8); ctx.fill()
            col(tcol if k in (0, 1, 2, 3, 4, 7) else (200, 200, 200)); ctx.rectangle(x + 8, 72, 104 * frac, 8); ctx.fill()
        col((60, 60, 60)); ctx.rectangle(0, 100, W, 1); ctx.fill()
        # envelope sketch, bottom left
        e = sq["env"]
        total = max(0.25, e["attack"] + e["decay"] + 0.5 + e["release"])
        x0, y0, w, h = 10, 108, 120, 44
        pts = [(0, 0), (e["attack"], 1.0), (e["attack"] + e["decay"], e["sustain"]),
               (e["attack"] + e["decay"] + 0.5, e["sustain"]), (total, 0)]
        col((40, 40, 40)); ctx.rectangle(x0, y0, w, h); ctx.fill()
        col(tcol); ctx.set_line_width(2)
        for n, (t, v) in enumerate(pts):
            (ctx.move_to if n == 0 else ctx.line_to)(x0 + w * t / total, y0 + h - h * v)
        ctx.stroke()
        col((255, 255, 255)); font(18, True)
        pos = "" if sq["pos"] is None else f"  step {sq['pos'] + 1}/{sq['length']}"
        say(144, 128, f"SEQ  {sq['pattern']}  {'▶' if sq['running'] else '■'}{pos}   {sq['grid']}", 520)
        col((200, 200, 200)); font(15)
        say(144, 151, f"T{sq['track'] + 1} {sq['texture']}   ·   bar: {sq['bar']}"
                      + (f"   ·   next: P{sq['pending'] + 1}" if sq["pending"] is not None else ""), 560)
        col((140, 140, 140)); font(13)
        say(950, 127, snap["bpm"], right=True)
        say(950, 150, sq["warning"] or ("FINE" if snap["shift"] else
            f"bars {sq['bars'][0]}–{sq['bars'][1]} of {sq['n_bars']}" if sq["n_bars"] else "no bars — save an output preset"),
            right=True)
```

- [ ] **Step 5: Render and look** — restart the mock, `python tests/render_preview.py`, open `tests/preview_seq.png` (Read tool). Expect 8 columns (Attack … Level), the envelope sketch bottom-left, `SEQ  P1 ▶ step n/16 1/16`, `T1 Metaballs · bar: Lumiverse 1`. Run `python tests/test_fake_push.py` → `OK` (the display renders during it).

- [ ] **Step 6: Lint, commit**

```bash
python -m pyflakes display.py push_resolume_bridge.py tests/render_preview.py
git add display.py push_resolume_bridge.py tests/render_preview.py
git commit -m "SEQ display: knobs, envelope sketch, transport line

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 10: CLI `--setup-chaser`, `--check` additions, docs

**Files:**
- Modify: `push_resolume_bridge.py` (`main()` ~line 1286)
- Modify: `resolume_check.py`
- Modify: `config.yaml`, `README.md`, `CLAUDE.md`, `docs/ideas.md`

**Interfaces:**
- Produces: `python push_resolume_bridge.py --setup-chaser [--dry-run]`; `--check` steps "WebSocket set", "Open source into a clip", "Add Crop + display name + delete", "Set string (layer name)".

- [ ] **Step 1: CLI.** In `main()` add arguments:

```python
    ap.add_argument("--setup-chaser", action="store_true",
                    help="build / update the step sequencer's bar layers from the Advanced Output preset")
    ap.add_argument("--dry-run", action="store_true", help="with --setup-chaser: only print what would change")
```

and before `elif args.dump:`:

```python
    elif args.setup_chaser:
        bridge = Bridge(cfg, rest)
        for w in bridge.bar_warnings:
            print("warning:", w)
        if not bridge.bars:
            sys.exit(1)
        print(f"{len(bridge.bars)} bars:", ", ".join(b.name for b in bridge.bars))
        bridge.refresh_comp()
        if bridge.comp is None:
            sys.exit(f"Resolume not reachable at {rest.url}")
        tracks = max(1, len([t for t in bridge.seq.pattern.tracks if t.texture]) or 1)
        for line in bridge.engine.setup(bridge.bars, tracks, args.dry_run):
            print(" ", line)
```

Test by hand against the mock: `python push_resolume_bridge.py --setup-chaser --dry-run --config /dev/null` won't find a preset; instead run with a temp config that sets `sequencer.preset` to the fixture path (write it to the scratchpad) and check it prints `create layer CH:T1:Bar A …` lines, then without `--dry-run` and confirm `curl -s localhost:8080/api/v1/composition | grep -c 'CH:T1'` ≥ 1.

- [ ] **Step 2: `--check` additions.** In `resolume_check.py` add steps (call them from `run()` after "Launch column"):

```python
    def s_ws_set(self, name):
        try:
            import websocket
        except ImportError:
            return self.add(name, None, "pip install websocket-client")
        p = master_param(self.layer())
        old = p["value"]; new = 0.37 if abs(old - 0.37) > 0.01 else 0.61
        ws = websocket.create_connection(self.ws_url, timeout=3)
        ws.send(json.dumps({"action": "set", "parameter": f"/parameter/by-id/{p['id']}", "value": new}))
        time.sleep(WAIT)
        got = (self.param_by_id(p["id"]) or {}).get("value")
        ws.send(json.dumps({"action": "set", "parameter": f"/parameter/by-id/{p['id']}", "value": old}))
        ws.close()
        self.add(name, got == new, f"sent {new}, read {got}")

    def s_open_clip(self, name):
        layer = self.layer()
        C = next((i + 1 for i, c in enumerate(layer.get("clips") or []) if clip_state(c) == "Empty"), None)
        if not C:
            return self.add(name, None, "no empty clip slot on this layer")
        code = self.s.post(f"{self.base}/composition/layers/{self.L}/clips/{C}/open", data=b"source:///video/Checkered",
                           headers={"Content-Type": "text/plain"}, timeout=3).status_code
        time.sleep(WAIT)
        clip = self.layer()["clips"][C - 1]
        desc = ((clip.get("video") or {}).get("description"))
        code2 = self.req("POST", f"/composition/layers/{self.L}/clips/{C}/clear")[0]
        time.sleep(WAIT)
        cleared = clip_state(self.layer()["clips"][C - 1]) == "Empty"
        self.add(name, code == 204 and desc == "Checkered" and cleared, f"open HTTP {code}, description {desc!r}, clear HTTP {code2}, empty again {cleared}")

    def s_crop(self, name):
        before = len(self.layer()["video"].get("effects") or [])
        code = self.s.post(f"{self.base}/composition/layers/{self.L}/effects/video/add", data=b"effect:///video/Crop",
                           headers={"Content-Type": "text/plain"}, timeout=3).status_code
        time.sleep(WAIT)
        fx = self.layer()["video"].get("effects") or []
        if len(fx) != before + 1:
            return self.add(name, False, f"add HTTP {code}, effects {before} → {len(fx)}")
        idx = len(fx) - 1
        code2 = self.s.post(f"{self.base}/composition/layers/{self.L}/effects/video/{idx}/set-display-name",
                            data=b"CH:T9:check", headers={"Content-Type": "text/plain"}, timeout=3).status_code
        time.sleep(WAIT)
        dn = text(self.layer()["video"]["effects"][idx].get("display_name"))
        left = self.layer()["video"]["effects"][idx]["params"].get("Left")
        code3 = self.put(left["id"], {"value": 100.0}) if left else None
        time.sleep(WAIT)
        lv = (self.param_by_id(left["id"]) or {}).get("value") if left else None
        code4 = self.s.delete(f"{self.base}/composition/layers/{self.L}/effects/video/{idx}", timeout=3).status_code
        time.sleep(WAIT)
        gone = len(self.layer()["video"].get("effects") or []) == before
        self.add(name, dn == "CH:T9:check" and lv == 100.0 and gone,
                 f"add {code}, display name {code2} → {dn!r}, Left {code3} → {lv}, delete {code4}, removed {gone}")

    def s_layer_name(self, name):
        p = self.layer().get("name")
        old = p["value"]
        code = self.put(p["id"], {"value": old + " ✓"})
        time.sleep(WAIT)
        got = (self.param_by_id(p["id"]) or {}).get("value")
        self.put(p["id"], {"value": old})
        self.add(name, got == old + " ✓", f"HTTP {code}, read {got!r} (informational: the bridge only names layers best-effort)")
```

(`text` is already imported from `resolume_api` in `resolume_check.py`.) Register them in `run()`:

```python
        self.step("WebSocket set", self.s_ws_set)
        self.step("Open source into a clip", self.s_open_clip)
        self.step("Add Crop + display name + delete", self.s_crop)
        self.step("Set string (layer name)", self.s_layer_name)
```

Verify against the mock: `python push_resolume_bridge.py --check 1` → `All OK.`

- [ ] **Step 3: Docs.** `config.yaml`: add after `colors_file: null`:

```yaml
# Step sequencer (Note button / 4th button above the display)
sequencer:
  preset: newest          # Advanced Output preset name (Arena → Output → Advanced → Presets → Save), or newest
  bars: auto              # or a list of groups: [{name: Left, screens: [Lumiverse 1, Lumiverse 2]}]
  disabled: []            # screen names to ignore (e.g. spares sitting on top of another bar)
  layer_prefix: "CH: "    # bar layers are named "<prefix>T<track> <bar>"
  clip_column: 1          # which column of a bar layer holds the texture
  tracks: 4               # textures at once (1-4)

# Patterns are saved here (default: chases.yaml next to the script).
chases_file: null
```

`README.md`: add a **SEQ** row group to the Controls section (Note / Session, pads layout in 3 lines, BD1–4 tracks, Browse, Play, Delete / Duplicate / Double Loop / Fixed Length, Repeat / Accent, knobs, Octave ▲▼), a "What it does" bullet `**Step sequencer:** …`, the `--setup-chaser` row in the command-line table, and `chases.yaml` next to `pins.yaml` in Configuration. `CLAUDE.md`: files table rows for `sequencer.py`, `chaser_engine.py`, `chases.yaml`, `tests/test_sequencer.py`, `tests/test_engine.py`, `tests/fixtures/`; control map rows for Note / Session / Browse / Repeat / Accent / Delete / Double Loop / Fixed Length / Octave / Swing; a **seq** entry under Modes (pointing at the spec); the six new verified API calls under Verified APIs (marked "verified on the mock; run `--check` on Arena"); the testing workflow gains `python tests/test_sequencer.py` and `python tests/test_engine.py`. `docs/ideas.md`: F18 status `done (v1)`.

- [ ] **Step 4: Full verification**

```bash
pkill -f mock_resolume.py; python tests/mock_resolume.py > /dev/null 2>&1 &
sleep 1
python tests/test_sequencer.py && python tests/test_engine.py && python tests/test_fake_push.py && TEST_POLL=1 python tests/test_fake_push.py && python tests/render_preview.py && python push_resolume_bridge.py --check 1 --check-columns
python -m pyflakes *.py tests/*.py
for f in push_resolume_bridge.py resolume_api.py display.py resolume_check.py sequencer.py chaser_engine.py; do python -c "import ast; ast.parse(open('$f').read(), feature_version=(3,9))" && echo "py39 ok $f"; done
```

Expected: every script prints `OK` (the check prints `All OK.`), pyflakes silent, six `py39 ok` lines.

- [ ] **Step 5: Commit and push**

```bash
git add push_resolume_bridge.py resolume_check.py config.yaml README.md CLAUDE.md docs/ideas.md
git commit -m "SEQ: --setup-chaser, --check steps for the new calls, docs

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
git push
```

Then hand over to Štefan: run `python push_resolume_bridge.py --check 3` on Arena (new steps), then `python push_resolume_bridge.py --setup-chaser --dry-run`, then without `--dry-run`, then the Push.

---

## Self-review notes

- Spec coverage: bars/preset (T1), envelope (T2), patterns + `chases.yaml` (T3), timing / direction / swing / pattern switch (T4), API + WebSocket `set` (T5), layer engine incl. blend Add, naming, texture copy, rate limit (T6), hidden layers (T7), every pad / button / knob in the spec's tables incl. Repeat, Accent, Delete, Duplicate, Double Loop, Fixed Length, Octave, Browse, Shift + Note, Swing (T8), display (T9), CLI + `--check` + docs + config (T10). Errors from the spec: no preset → `bar_warnings` shown on the display bottom-right and by `--setup-chaser`; setup HTTP failures → `FAIL …` report lines and `seq_note`; no layers → `load_texture` returns "press Shift + Note"; WebSocket down → `_send_level` falls back to REST.
- Not in v1 (as the spec says): per-track texture display beyond the name, Record, probability, Video Router, layer group, other engines.
- Type consistency: `Bar.name` is the key everywhere (`steps`, `find_layers`, `set_level`, `held_bars`); tracks are 0-based in code (`seq.track`, `Voice.track`, marker `T<track+1>`), 1-based only in text; `Sequencer.levels` keys `(track, bar name)` match `LayerEngine.set_level(track, bar, value)`.
