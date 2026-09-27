"""Step sequencer for the LED bars: patterns with up to four texture tracks, ADSR envelopes and
the playback clock. Bars are opaque names (the bridge uses 'pad 1' … 'pad 24'). Pure Python:
nothing here talks to Resolume or the Push; the bridge feeds it beat time and sends the levels
it returns."""

from __future__ import annotations

import math
import random
import sys
import time
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
SAVE_DELAY = 0.2       # s after the last knob tick / step press before chases.yaml is written


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
    source: dict = None        # made by a preset: {bank, recipe, seed, rig}; None = own or hand-edited


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
    d = {"name": p.name, "length": p.length, "direction": p.direction, "swing": p.swing}
    if p.source:
        d["source"] = dict(p.source)
    d["tracks"] = [_track_to_dict(t) for t in p.tracks]
    return d


def _pattern_from_dict(d):
    tracks = [_track_from_dict(t) for t in (d.get("tracks") or [])][:N_TRACKS]
    while len(tracks) < N_TRACKS:
        tracks.append(Track())
    src = d.get("source")
    return Pattern(name=str(d.get("name", "P?")), length=int(d.get("length", 16)),
                   direction=d.get("direction", "forward"), swing=float(d.get("swing", 0.0)), tracks=tracks,
                   source=dict(src) if isinstance(src, dict) else None)


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
        self.groups = [None] * N_GROUPS   # global pad groups GG1-8: sorted pad indices (0-based) or None
        self.track_groups = {}     # track -> [8] pad groups G1-8 of that track (shown before the global one)
        self.pad_config = None     # 24 fixture names shared by every track: the last Bar Chaser pad mapping
        self.voices = {}           # (track, bar name) -> Voice
        self.levels = {}           # (track, bar name) -> last level returned by tick()
        self.dirty, self.dirty_t = False, 0.0   # a deferred save is waiting (save_later / flush)
        if self.path and self.path.exists():
            self.load()

    # ---- editing ------------------------------------------------------------ #
    @property
    def pattern(self):
        return self.patterns[self.current]

    def _steps(self, bar, track=None):
        t = self.pattern.tracks[self.track if track is None else track]
        return t.steps.setdefault(bar, {})

    def mark_edited(self, p=None):
        """Steps changed by hand: the pattern no longer follows its preset (a re-fit leaves it alone)."""
        self.patterns[self.current if p is None else p].source = None

    def toggle_step(self, bar, step, level=1.0, gate=None, track=None):
        """Returns True when the step is on afterwards."""
        self.mark_edited()
        st = self._steps(bar, track)
        if step in st:
            del st[step]
            on = False
        else:
            st[step] = [float(level), gate]
            on = True
        self.save()
        return on

    def toggle_steps(self, bars, step, level=1.0):
        """A step pressed on the Push for every selected pad: on for all when any is off, else off
        for all. Returns True when the step is on afterwards. An edit: the pattern stops following
        its preset."""
        self.mark_edited()
        sts = [self._steps(b) for b in bars]
        all_on = all(step in st for st in sts)
        for st in sts:
            if all_on:
                st.pop(step, None)
            elif step not in st:
                st[step] = [float(level), None]
        self.save_later()
        return not all_on

    def remove_steps(self, bars, step):
        """Delete + step on the Push, for every selected pad."""
        self.mark_edited()
        for b in bars:
            self._steps(b).pop(step, None)
        self.save_later()

    def set_step_values(self, bar, steps, level=None, gate=None, track=None):
        self.mark_edited()
        st = self._steps(bar, track)
        for s in steps:
            if s in st:
                if level is not None:
                    st[s][0] = max(0.0, min(1.0, float(level)))
                if gate is not None:
                    st[s][1] = max(0.1, min(1.0, float(gate)))
        self.save_later()

    def clear_steps(self, bar=None, track=None):
        self.mark_edited()
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
        self.mark_edited()
        self.save()

    # ---- presets ------------------------------------------------------------- #
    def is_empty(self, p):
        return not any(st for t in self.patterns[p].tracks for st in t.steps.values())

    def store_pattern(self, p, d):
        """Put a pattern dict (a realised preset) into slot p. A running pattern plays on with it."""
        self.patterns[p] = _pattern_from_dict(d)
        self.save()

    def mismatched(self, signature):
        """Slots whose preset placement was made for a different rig than `signature`."""
        return [i for i, p in enumerate(self.patterns) if p.source and p.source.get("rig") != signature]

    def refit(self, signature, refit_fn):
        """Re-place every preset pattern made for another rig; knobs, length and swing stay.
        refit_fn(pattern dict) -> pattern dict (banks.refit with the current rig). Returns the slots."""
        done = self.mismatched(signature)
        for i in done:
            self.patterns[i] = _pattern_from_dict(refit_fn(_pattern_to_dict(self.patterns[i])))
        if done:
            self.save()
        return done

    def set_length(self, n):
        self.pattern.length = max(1, min(MAX_STEPS, int(n)))
        self.save_later()

    def set_direction(self, d):
        if d in DIRECTIONS:
            self.pattern.direction = d
            self.save_later()

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

    # ---- pad groups: saved pad selections, per track (G) or for all tracks (GG) ---- #
    def group(self, g, track):
        """(pads, "track" | "global") on group button g for this track: its own group first, else the
        global one; (None, None) when both are empty. A group only selects pads: steps made with it
        belong to the track they were made on, so one track never plays another track's layers."""
        own = self.track_groups.get(track)
        if own and own[g]:
            return own[g], "track"
        if self.groups[g]:
            return self.groups[g], "global"
        return None, None

    def store_group(self, g, pads, track=None):
        """Store a pad selection on group button g for one track, or for all tracks (track None)."""
        target = self.groups if track is None else self.track_groups.setdefault(track, [None] * N_GROUPS)
        target[g] = sorted(pads) or None
        self.save()

    def clear_group(self, g, track=None):
        target = self.groups if track is None else self.track_groups.get(track)
        if target:
            target[g] = None
        self.save()

    # ---- storage ------------------------------------------------------------ #
    def to_dict(self):
        def groups(gs):                                           # 1-based pads in the file
            return [None if g is None else [k + 1 for k in g] for g in gs]
        return {"groups": groups(self.groups),
                "track_groups": {t + 1: groups(gs) for t, gs in sorted(self.track_groups.items()) if any(gs)},
                "pads": list(self.pad_config) if self.pad_config else None,
                "patterns": [_pattern_to_dict(p) for p in self.patterns]}

    def from_dict(self, d):
        def groups(raw):
            gs = [sorted(int(k) - 1 for k in g) if g else None for g in (raw or [])][:N_GROUPS]
            return gs + [None] * (N_GROUPS - len(gs))
        self.groups = groups(d.get("groups"))
        self.track_groups = {int(t) - 1: groups(gs) for t, gs in (d.get("track_groups") or {}).items()}
        pads = d.get("pads")
        if isinstance(pads, dict):                                  # older file: one list per track, take T1's
            pads = pads.get(min(pads, key=lambda t: int(t))) if pads else None
        self.pad_config = [str(n) for n in pads] if pads else None
        pats = [_pattern_from_dict(x) for x in (d.get("patterns") or [])][:N_PATTERNS]
        while len(pats) < N_PATTERNS:
            pats.append(Pattern(name=f"P{len(pats) + 1}"))
        self.patterns = pats

    def save(self):
        """Write the file now (single actions: a stored preset, a group, the pad mapping)."""
        self.dirty = False
        self.write(self.to_dict())

    def save_later(self, now=None):
        """Write the file SAVE_DELAY after the last change. Knob ticks and step presses come many
        times a second and a full write takes tens of ms on the MIDI thread."""
        self.dirty, self.dirty_t = True, (now or time.time())

    def take_dirty(self, now=None, force=False):
        """The data of a due deferred save (None when nothing is due). Take it under the bridge lock,
        then write() outside it."""
        if not self.dirty or (not force and (now or time.time()) - self.dirty_t < SAVE_DELAY):
            return None
        self.dirty = False
        return self.to_dict()

    def flush(self, now=None, force=False):
        data = self.take_dirty(now, force)
        if data is not None:
            self.write(data)

    def write(self, data):
        if not self.path:
            return
        try:
            self.path.write_text("# Step sequencer patterns, written by the bridge. Safe to edit or delete.\n"
                                 + yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        except Exception as e:
            print(f"[seq] can't write {self.path}: {e}", file=sys.stderr)

    def load(self):
        try:
            self.from_dict(yaml.safe_load(self.path.read_text(encoding="utf-8")) or {})
        except Exception as e:
            print(f"[seq] can't read {self.path}: {e}", file=sys.stderr)
