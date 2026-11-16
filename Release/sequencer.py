"""Step sequencer for the LED bars: patterns with up to four texture tracks, ADSR envelopes and
the playback clock. Bars are opaque names (the bridge uses 'pad 1' … 'pad 24'). Pure Python:
nothing here talks to Resolume or the Push; the bridge feeds it beat time and sends the levels
it returns."""

from __future__ import annotations

import math
import os
import random
import shutil
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

GRIDS = {"1/4": 1.0, "1/4t": 2 / 3, "1/8": 0.5, "1/8t": 1 / 3,
         "1/16": 0.25, "1/16t": 1 / 6, "1/32": 0.125, "1/32t": 1 / 12}   # beats per step
DIRECTIONS = ["forward", "reverse", "bounce", "random"]
MAX_STEPS = 32
BAR_BEATS = 4          # pattern switches happen on a bar boundary (a phrase boundary for longer patterns)
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
    grid: str = "1/16"         # step length (GRIDS); every pattern keeps its own


def phrase_beats(p):
    """Beats between the places pattern p may start: its length when that is a whole number of bars
    (a 2-bar pattern starts on every other bar, counted from Play), else one bar."""
    bars = p.length * GRIDS.get(p.grid, 0.25) / BAR_BEATS
    n = round(bars)
    return n * BAR_BEATS if n >= 1 and abs(bars - n) < 1e-6 else BAR_BEATS


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
    d = {"name": p.name, "length": p.length, "direction": p.direction, "swing": p.swing, "grid": p.grid}
    if p.source:
        d["source"] = dict(p.source)
    d["tracks"] = [_track_to_dict(t) for t in p.tracks]
    return d


def _pattern_from_dict(d):
    tracks = [_track_from_dict(t) for t in (d.get("tracks") or [])][:N_TRACKS]
    while len(tracks) < N_TRACKS:
        tracks.append(Track())
    src = d.get("source")
    grid = str(d.get("grid", "1/16"))                  # older files and preset dicts: 1/16
    return Pattern(name=str(d.get("name", "P?")), length=int(d.get("length", 16)),
                   direction=d.get("direction", "forward"), swing=float(d.get("swing", 0.0)), tracks=tracks,
                   source=dict(src) if isinstance(src, dict) else None, grid=grid if grid in GRIDS else "1/16")


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
        self.running = False
        self.start_beat = 0.0      # where the playing pattern started (its step 0)
        self.origin = 0.0          # where Play started: phrases are counted from here
        self.last_bt = None
        self.last_step = None
        self.last_pattern_step = None
        self.last_random = None
        self.groups = [None] * N_GROUPS   # global pad groups GG1-8: sorted pad indices (0-based) or None
        self.track_groups = {}     # track -> [8] pad groups G1-8 of that track (shown before the global one)
        self.pad_config = None     # 24 fixture names shared by every track: the last Bar Chaser pad mapping
        self.kick_pads = []        # pad numbers (1-based) chosen as kick bars on the Push; [] = automatic
        self.voices = {}           # (track, bar name) -> Voice
        self.levels = {}           # (track, bar name) -> last level returned by tick()
        self.dirty, self.dirty_t = False, 0.0   # a deferred save is waiting (save_later / flush)
        self.pending_apply = None  # (slot, Pattern) replacing the live pattern at the next bar
        self.landed = None         # slot whose waiting pattern just landed (the bridge reloads its preview)
        self.load_error = None     # why chases.yaml could not be read (the file was kept aside)
        self._wlock = threading.Lock()
        self._version = self._written = 0   # snapshot counter: an older snapshot never overwrites a newer
        if self.path and self.path.exists():
            self.load()

    # ---- editing ------------------------------------------------------------ #
    @property
    def pattern(self):
        return self.patterns[self.current]

    @property
    def grid(self):
        """The playing pattern's step length (every pattern keeps its own)."""
        return self.pattern.grid

    @grid.setter
    def grid(self, g):
        if g in GRIDS and g != self.pattern.grid:
            self.pattern.grid = g
            self.save_later()

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
        self._drop_pending(p)
        self.patterns[p] = Pattern(name=f"P{p + 1}")
        self.save()

    def copy_pattern(self, src, dst):
        d = _pattern_from_dict(_pattern_to_dict(self.slot_pattern(src)))
        d.name = f"P{dst + 1}"
        self._drop_pending(dst)
        self.patterns[dst] = d
        self.save()

    def slot_pattern(self, p):
        """What slot p holds, counting an Apply that waits for the bar: what the PRESETS menu edits."""
        if self.pending_apply is not None and self.pending_apply[0] == p:
            return self.pending_apply[1]
        return self.patterns[p]

    def _drop_pending(self, p):
        """A direct write to slot p wins over an older Apply still waiting for the bar."""
        if self.pending_apply is not None and self.pending_apply[0] == p:
            self.pending_apply = None

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
        self._drop_pending(p)
        self.patterns[p] = _pattern_from_dict(d)
        self.save()

    def apply(self, p, d):
        """Replace pattern p with a dict: the live, running pattern at the next bar boundary (the
        outgoing voices finish their release), any other slot at once. Returns "bar" or "now"."""
        if self.running and p == self.current:
            self.pending_apply = (p, _pattern_from_dict(d))
            return "bar"
        self.store_pattern(p, d)
        return "now"

    def load_bank(self, dicts, keep_own=True):
        """A whole bank into P1-P16. Own patterns (no preset source, with steps) are kept when
        keep_own; returns their slots."""
        kept = []
        for i, d in enumerate(dicts[:N_PATTERNS]):
            p = self.patterns[i]
            if keep_own and p.source is None and any(st for t in p.tracks for st in t.steps.values()):
                kept.append(i)
                continue
            self.patterns[i] = _pattern_from_dict(d)
        self.pending_apply = None
        self.save()
        return kept

    def mismatched(self, signature):
        """Slots whose preset placement (or the Apply waiting for the bar) was made for another rig."""
        return [i for i in range(len(self.patterns))
                if self.slot_pattern(i).source and self.slot_pattern(i).source.get("rig") != signature]

    def refit(self, signature, refit_fn):
        """Re-place every preset pattern made for another rig; knobs, length and swing stay. An Apply
        waiting for the bar is re-placed too. refit_fn(pattern dict) -> pattern dict (banks.refit with
        the current rig). Returns the slots."""
        done = self.mismatched(signature)
        for i in done:
            pa = self.pending_apply
            if pa is not None and pa[0] == i:
                self.pending_apply = (i, _pattern_from_dict(refit_fn(_pattern_to_dict(pa[1]))))
            p = self.patterns[i]
            if p.source and p.source.get("rig") != signature:
                self.patterns[i] = _pattern_from_dict(refit_fn(_pattern_to_dict(p)))
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
        self.start_beat = self.origin = math.floor(bt / BAR_BEATS) * BAR_BEATS
        self.last_step = None
        self.last_bt = bt

    def stop(self):
        self.running = False        # voices keep ticking so releases finish
        self.pending = None         # a queued pattern is dropped: Play starts what is shown as current
        if self.pending_apply:      # nothing to wait for any more
            p, pat = self.pending_apply
            self.pending_apply = None
            self.patterns[p] = pat
            self.landed = p
            self.save()

    def switch_pattern(self, p, bt, now=False):
        """Running: queued until its phrase boundary (see phrase_beats); now = at once, joining at the
        step its phrase has reached. Not running: at once."""
        if not self.running:
            self._begin(p, math.floor(bt / BAR_BEATS) * BAR_BEATS)
        elif now:
            P = phrase_beats(self.patterns[p])
            self._begin(p, self.origin + math.floor((bt - self.origin) / P) * P)
        else:
            self.pending = p

    def _begin(self, p, at):
        self.current, self.pending = p, None
        self.start_beat = at
        self.last_step = None

    def _boundary(self, pat, bt):
        """The phrase boundary of pattern pat (counted from Play) passed since the last tick, else None."""
        if self.last_bt is None:
            return None
        P = phrase_beats(pat)
        b = self.origin + (math.floor((self.last_bt - self.origin) / P) + 1) * P
        return b if b <= bt else None

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
        """Advance to beat time bt. Returns {(track, bar): level} for every level that changed.
        A clock that goes backwards (a tap, a resync) moves the running envelopes along with it, so a
        flash never freezes, and a pattern ahead of the clock starts again at the current bar."""
        if self.last_bt is not None and bt < self.last_bt:
            d = bt - self.last_bt
            for v in self.voices.values():
                v.start += d
            if self.start_beat > bt:
                self.start_beat = math.floor(bt / BAR_BEATS) * BAR_BEATS
                self.last_step = None
            self.origin = min(self.origin, self.start_beat)
        if self.running:
            if self.pending_apply is not None:                 # lands on the new pattern's phrase
                b = self._boundary(self.pending_apply[1], bt)
                if b is not None:
                    p, pat = self.pending_apply
                    self.pending_apply = None
                    self.patterns[p] = pat
                    self.landed = p
                    self.save_later()
                    if p == self.current:
                        self._begin(p, b)
            if self.pending is not None:
                b = self._boundary(self.patterns[self.pending], bt)
                if b is not None:
                    self._begin(self.pending, b)
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
                "kick_pads": list(self.kick_pads),
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
        self.kick_pads = sorted(int(k) for k in (d.get("kick_pads") or []))
        pats = [_pattern_from_dict(x) for x in (d.get("patterns") or [])][:N_PATTERNS]
        while len(pats) < N_PATTERNS:
            pats.append(Pattern(name=f"P{len(pats) + 1}"))
        self.patterns = pats

    def save(self):
        """Write the file now (single actions: a stored preset, a group, the pad mapping)."""
        self.dirty = False
        self.write(self._snapshot())

    def save_later(self, now=None):
        """Write the file SAVE_DELAY after the last change. Knob ticks and step presses come many
        times a second and a full write takes tens of ms on the MIDI thread."""
        self.dirty, self.dirty_t = True, (now or time.time())

    def _snapshot(self):
        self._version += 1
        return self._version, self.to_dict()

    def take_dirty(self, now=None, force=False):
        """The snapshot of a due deferred save (None when nothing is due). Take it under the bridge
        lock, then write() it outside."""
        if not self.dirty or (not force and (now or time.time()) - self.dirty_t < SAVE_DELAY):
            return None
        self.dirty = False
        return self._snapshot()

    def flush(self, now=None, force=False):
        snap = self.take_dirty(now, force)
        if snap is not None:
            self.write(snap)

    def write(self, snap):
        """Write a snapshot. Two threads may write; a snapshot older than the one on disk is skipped,
        and the file is replaced in one step (a temporary file, then a rename), so it is never torn."""
        if not self.path:
            return
        version, data = snap
        text = "# Step sequencer patterns, written by the bridge. Safe to edit or delete.\n" \
               + yaml.safe_dump(data, sort_keys=False)
        with self._wlock:
            if version < self._written:
                return
            tmp = self.path.with_name(self.path.name + ".tmp")
            try:
                tmp.write_text(text, encoding="utf-8")
                os.replace(tmp, self.path)
                self._written = version
            except Exception as e:
                print(f"[seq] can't write {self.path}: {e}", file=sys.stderr)

    def load(self):
        """Read chases.yaml. A file that can't be read is copied aside (chases.yaml.bad-<time>) before
        anything can overwrite it, and the sequencer starts empty; load_error says where it went."""
        try:
            self.from_dict(yaml.safe_load(self.path.read_text(encoding="utf-8")) or {})
        except Exception as e:
            backup = self.path.with_name(f"{self.path.name}.bad-{time.strftime('%Y%m%d-%H%M%S')}")
            n = 1
            while backup.exists():
                backup = self.path.with_name(f"{self.path.name}.bad-{time.strftime('%Y%m%d-%H%M%S')}-{n}")
                n += 1
            try:
                shutil.copy2(self.path, backup)
            except OSError:
                backup = None
            fresh = Sequencer(n_tracks=self.n_tracks)               # nothing half-read stays
            self.patterns, self.groups, self.track_groups = fresh.patterns, fresh.groups, fresh.track_groups
            self.pad_config, self.kick_pads = None, []
            self.load_error = (f"chases.yaml could not be read ({type(e).__name__}); "
                               + (f"kept as {backup.name}" if backup else "and could not be copied"))
            print(f"[seq] {self.load_error}: {e}", file=sys.stderr)
