"""Step sequencer for the LED bars: patterns with up to four texture tracks, ADSR envelopes and
the playback clock. Bars are opaque names (the bridge uses 'pad 1' … 'pad 24'). Pure Python:
nothing here talks to Resolume or the Push; the bridge feeds it beat time and sends the levels
it returns."""

from __future__ import annotations

import math
import random
import sys
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
N_GROUPS = 8           # pad groups on the 8 buttons right of the pads


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
    t = max(0.0, t)              # a beat-clock resync can move time backwards: treat as "just triggered"

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
    defaults = Envelope()
    return Track(texture=d.get("texture"),
                 envelope=Envelope(**{k: float(e.get(k, getattr(defaults, k)))
                                      for k in ("attack", "decay", "sustain", "release")}),
                 gate=float(d.get("gate", 0.5)), level=float(d.get("level", 1.0)),
                 steps={bar: {int(s): [float(lv), (None if g is None else float(g))] for s, lv, g in lst}
                        for bar, lst in (d.get("steps") or {}).items()})


def _pattern_to_dict(p):
    return {"name": p.name, "length": p.length, "direction": p.direction, "swing": p.swing,
            "tracks": [_track_to_dict(t) for t in p.tracks]}


def _pattern_from_dict(d):
    tracks = [_track_from_dict(t) for t in (d.get("tracks") or [])][:N_TRACKS]
    while len(tracks) < N_TRACKS:
        tracks.append(Track())
    return Pattern(name=str(d.get("name", "P?")), length=int(d.get("length", 16)),
                   direction=d.get("direction", "forward"), swing=float(d.get("swing", 0.0)), tracks=tracks)


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
        self.last_pattern_step = None
        self.last_random = None
        self.groups = [None] * N_GROUPS   # pad groups: sorted pad indices (0-based) or None = empty
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
        return self.last_pattern_step if self.running else None

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
                self.last_pattern_step = s
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

    # ---- pad groups (a saved pad selection, shared by all tracks) ------------ #
    def store_group(self, g, pads):
        self.groups[g] = sorted(pads) or None
        self.save()

    def clear_group(self, g):
        self.groups[g] = None
        self.save()

    # ---- storage ------------------------------------------------------------ #
    def to_dict(self):
        return {"groups": [None if g is None else [k + 1 for k in g] for g in self.groups],   # 1-based pads
                "patterns": [_pattern_to_dict(p) for p in self.patterns]}

    def from_dict(self, d):
        groups = [sorted(int(k) - 1 for k in g) if g else None for g in (d.get("groups") or [])][:N_GROUPS]
        self.groups = groups + [None] * (N_GROUPS - len(groups))
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
