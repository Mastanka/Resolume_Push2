"""SEQ banks: patterns stored as recipes in role space (D / S), realised onto the current rig.

A recipe fixes the rhythm (steps 0-31 at grid 1/16), the envelopes, gate, level and swing. Only
the pads each hit lands on depend on the rig and on the seed. Output = pattern dicts in the
chases.yaml schema of sequencer.py (`_pattern_from_dict` reads them), plus `source`.
Pure Python, 3.9 compatible, deterministic: same recipe + rig + seed → same pattern.
"""
from __future__ import annotations

import copy
import math
import random
from typing import Dict, List, Optional, Sequence

from rig import Rig

STEPS = 32
BEATS = list(range(0, STEPS, 4))
OFF = list(range(2, STEPS, 4))
NONBEAT = [s for s in range(STEPS) if s % 4]
A_STEPS = [s for s in NONBEAT if s % 4 == 3]
# rumba clave 3-2 in bar 1, 2-3 in bar 2: (step, low/high tom)
CLAVE = [(0, "low"), (3, "hi"), (7, "hi"), (10, "hi"), (12, "hi"),
         (18, "low"), (20, "hi"), (24, "hi"), (27, "hi"), (31, "hi")]

ENV = {   # attack / decay / release in beats, sustain 0-1
    "kick":   (0, 0.12, 0.2, 0.2),   "hard":   (0, 0.08, 0.3, 0.12),
    "rumble": (0.2, 0, 1, 0.3),      "tick":   (0, 0.05, 0.4, 0.15),
    "closed": (0, 0.03, 0.3, 0.06),  "open":   (0, 0.05, 0.6, 0.3),
    "ride":   (0, 0.05, 0.5, 0.2),   "clap":   (0, 0.1, 0.35, 0.45),
    "tom":    (0, 0.08, 0.4, 0.3),   "stab":   (0, 0.15, 0.4, 0.5),
    "dub":    (0, 0.1, 0.5, 0.6),    "roll":   (0, 0.04, 0.3, 0.05),
    "bounce": (0, 0.06, 0.3, 0.1),   "strobe": (0, 0.02, 0.1, 0.04),
    "glow":   (0.25, 0, 1, 1.5),     "swell":  (0.25, 0, 1, 0.3),
}
EMPTY_TRACK = ("none", 0.5, 1.0)


# --------------------------------------------------------------------------- #
# Placement vocabulary
# --------------------------------------------------------------------------- #

class Draw:
    """Random placement on a rig. Every `stream` remembers its last pick so a stream never lands
    on the same pad (or the same pad set) twice in a row."""

    def __init__(self, rig: Rig, seed: int):
        self.rig, self.r, self.prev = rig, random.Random(seed), {}

    def rand(self) -> float:
        return self.r.random()

    def _subset(self, pool: Sequence[int], n: int, pads: bool = True) -> List[int]:
        pool, out = list(pool), []
        while len(out) < n and pool:
            out.append(pool.pop(int(self.rand() * len(pool))))
        return sorted(out, key=self.rig.pos) if pads else sorted(out)

    @staticmethod
    def few(pool: Sequence[int]) -> int:
        """How many pads a "single" hit lights: 1 up to 11 pads in the pool, 2 up to 19, then 3."""
        return max(1, (len(pool) + 4) // 8)

    @staticmethod
    def share(pool: Sequence[int], frac: float, lo: int = 1, hi: Optional[int] = None) -> int:
        hi = len(pool) if hi is None else hi
        return max(lo, min(hi, int(math.floor(frac * len(pool) + 0.5))))

    def hop(self, pool: Sequence[int], n: int = 1, stream: str = "", avoid: Sequence[int] = (),
            pads: bool = True) -> List[int]:
        """n random items from pool, not in avoid, and not the previous pick of this stream (n = 1:
        not the same pad; n > 1: not the same set). A constraint the pool is too small for is
        dropped (the "not previous" rule first, then avoid). pads=False: items are not pads
        (steps, pair indices) and come back sorted by value."""
        n = max(1, min(n, len(pool)))
        prev = self.prev.get(stream, [])
        choice: List[int] = list(pool)[:n]
        for cands in ([p for p in pool if p not in avoid and (n > 1 or p not in prev)],
                      [p for p in pool if p not in avoid], list(pool)):
            if len(cands) >= n:
                choice = self._subset(cands, n, pads)
                for _ in range(8):                                   # n > 1: avoid repeating the set
                    if n == 1 or choice != prev or len(cands) == n:
                        break
                    choice = self._subset(cands, n, pads)
                break
        self.prev[stream] = choice
        return choice

    def hum(self, lv: float, amt: float = 0.15) -> float:
        return lv * (1 - amt * self.rand())


class Pat:
    def __init__(self):
        self.tracks = [{"env": EMPTY_TRACK[0], "gate": EMPTY_TRACK[1], "level": EMPTY_TRACK[2], "steps": {}}
                       for _ in range(4)]

    def track(self, t: int, env: str, gate: float, level: float):
        self.tracks[t].update(env=env, gate=gate, level=level)

    def hit(self, t: int, pads: Sequence[int], step: int, lv: float):
        v = round(max(0.05, min(1.0, lv)), 2)
        for p in pads:
            st = self.tracks[t]["steps"].setdefault(p, {})
            st[step] = max(st.get(step, 0.0), v)


def kick_n(rig: Rig) -> int:
    """Pads a hopping kick lights: one on small rigs, a third of the D pads from 5 D up."""
    return 1 if len(rig.D) <= 4 else Draw.share(rig.D, 1 / 3)


def pair_pick(d: Draw, stream: str) -> List[int]:
    """Random mirrored S pair(s), never the same choice twice in a row. Big rigs light several."""
    prs = d.rig.pairs
    idx = d.hop(list(range(len(prs))), max(1, int(len(prs) / 3 + 0.5)), stream, pads=False)
    return sorted({p for i in idx for p in prs[i]}, key=d.rig.pos)


# ---- building blocks shared by several recipes -------------------------------- #

def kick_rumble(P: Pat, d: Draw):
    rig = d.rig
    P.track(0, "kick", 0.5, 1.0)
    P.track(3, "rumble", 1.0, 0.45)
    for b in BEATS:
        pads = d.hop(rig.D, kick_n(rig), "kick")
        P.hit(0, pads, b, 1.0)
        sats = sorted({s for p in pads for s in rig.sat(p)}, key=rig.pos)
        P.hit(3, sats, b + 1, d.hum(0.9, 0.1))


def perc_316(P: Pat, d: Draw, t: int):
    S = d.rig.S
    for s in range(2, STEPS, 3):
        P.hit(t, d.hop(S, d.few(S), "p316"), s, d.hum(1, 0.1) if s % 4 == 2 else d.hum(0.55))


def dub_throw(P: Pat, d: Draw, t: int, start: int, n: int):
    A = d.rig.ALL
    for i, lv in enumerate([1, 0.6, 0.35, 0.2][:n]):
        P.hit(t, d.hop(A, d.few(A), "dub"), start + 3 * i, lv)


def kick_909(P: Pat, d: Draw):
    rig = d.rig
    for b in BEATS:
        if b % 16 == 0:
            P.hit(0, rig.D, b, 1.0)
            d.prev["kick"] = []
        else:
            P.hit(0, d.hop(rig.D, kick_n(rig), "kick"), b, 0.85)


def hats_16(P: Pat, d: Draw, t: int, scale: float = 1.0, pool: Optional[Sequence[int]] = None):
    pool = list(pool or d.rig.S)
    for s in NONBEAT:
        base = 0.9 if s % 4 == 2 else 0.35 if s % 4 == 1 else 0.55
        P.hit(t, d.hop(pool, d.few(pool), "hats"), s, d.hum(base * scale))


def claps(P: Pat, d: Draw, t: int):
    for s in (4, 12, 20, 28):
        P.hit(t, d.rig.outer_d, s, 0.9)


def clave(P: Pat, d: Draw, t: int, kick_at: Optional[Dict[int, List[int]]] = None,
          pool: Optional[Sequence[int]] = None):
    kick_at = kick_at or {}
    for s, kind in CLAVE:
        pl = list(pool) if pool else (d.rig.D if kind == "low" else d.rig.S)
        P.hit(t, d.hop(pl, d.few(pl), "clave", avoid=kick_at.get(s, [])), s,
              d.hum(0.95 if kind == "low" else 0.8))


def ride(P: Pat, d: Draw, t: int, frac: float, lv: float):
    S = d.rig.S
    for s in OFF:
        P.hit(t, d.hop(S, d.share(S, frac), "ride"), s, d.hum(lv))


# --------------------------------------------------------------------------- #
# The 16 recipes (Techno bank). Track roles: 0 kick, 1 hats, 2 accent, 3 movement.
# --------------------------------------------------------------------------- #

def r1(P, d):
    kick_rumble(P, d)
    P.track(1, "tick", 0.3, 0.7)
    S = d.rig.S
    for s in (3, 9, 14, 19, 25, 30):
        P.hit(1, d.hop(S, d.few(S), "tick"), s, d.hum(0.7))


def r2(P, d):
    kick_rumble(P, d)
    P.track(1, "tick", 0.3, 0.8)
    perc_316(P, d, 1)


def r3(P, d):
    kick_rumble(P, d)
    P.track(1, "closed", 0.3, 0.65)
    hats_16(P, d, 1, 0.9)
    P.track(2, "dub", 0.5, 0.9)
    dub_throw(P, d, 2, 6, 4)
    dub_throw(P, d, 2, 22, 4)


def r4(P, d):
    P.track(1, "tick", 0.3, 0.8)
    perc_316(P, d, 1)
    P.track(3, "glow", 1.0, 0.6)
    for s in (0, 8, 16, 24):
        P.hit(3, d.hop(d.rig.D, kick_n(d.rig), "glow"), s, d.hum(0.95, 0.1))
    P.track(2, "dub", 0.5, 0.8)
    dub_throw(P, d, 2, 14, 4)


def d1(P, d):
    P.track(0, "kick", 0.5, 1.0)
    kick_909(P, d)
    P.track(1, "open", 0.5, 0.75)
    for s in OFF:
        P.hit(1, pair_pick(d, "open"), s, d.hum(0.85))
    P.track(2, "clap", 0.5, 0.9)
    claps(P, d, 2)


def d2(P, d):
    P.track(0, "kick", 0.5, 1.0)
    kick_909(P, d)
    P.track(1, "closed", 0.3, 0.7)
    hats_16(P, d, 1)
    P.track(2, "clap", 0.5, 0.9)
    claps(P, d, 2)
    P.hit(2, d.hop(d.rig.S, 1, "ghost"), 31, 0.4)


def d3(P, d):
    P.track(0, "kick", 0.5, 1.0)
    kick_909(P, d)
    P.track(1, "closed", 0.3, 0.6)
    hats_16(P, d, 1)
    P.track(2, "clap", 0.5, 0.9)
    claps(P, d, 2)
    P.track(3, "stab", 0.5, 0.8)
    A = d.rig.ALL
    for s in (3, 6, 14, 19, 22, 26, 29):
        P.hit(3, d.hop(A, d.share(A, 1 / 3), "stab"), s, d.hum(0.85))


def d4(P, d):
    rig = d.rig
    P.track(1, "closed", 0.3, 0.55)
    hats_16(P, d, 1)
    P.track(2, "clap", 0.5, 0.9)
    claps(P, d, 2)
    P.track(3, "tom", 0.5, 0.85)
    pools = {"low": rig.D, "mid": rig.s_mid, "high": rig.s_high}
    for s, kind in ((3, "low"), (6, "mid"), (10, "high"), (19, "low"), (22, "mid"), (26, "high"),
                    (28, "high"), (29, "high"), (30, "mid"), (31, "low")):
        pl = pools[kind]
        P.hit(3, d.hop(pl, d.few(pl), "tom"), s, d.hum(0.85))


def g1(P, d):
    rig = d.rig
    P.track(0, "kick", 0.5, 1.0)
    for b in BEATS:
        P.hit(0, rig.D, b, 1.0 if b % 16 == 0 else 0.85)
    P.track(1, "ride", 0.4, 0.75)
    ride(P, d, 1, 2 / 3, 0.8)
    for s in A_STEPS:
        P.hit(1, d.hop(rig.S, d.few(rig.S), "ping"), s, 0.3)


def g2(P, d):
    rig = d.rig
    P.track(0, "kick", 0.5, 1.0)
    kick_at = {}
    for b in BEATS:
        kick_at[b] = d.hop(rig.D, kick_n(rig), "kick")
        P.hit(0, kick_at[b], b, 1.0)
    P.track(2, "tom", 0.5, 0.85)
    clave(P, d, 2, kick_at)
    P.track(1, "ride", 0.4, 0.7)
    ride(P, d, 1, 1 / 3, 0.6)


def g3(P, d):
    rig = d.rig
    P.track(0, "kick", 0.5, 1.0)
    for b in BEATS:
        P.hit(0, rig.D, b, 1.0)
    P.track(3, "roll", 0.4, 0.5)
    for s in NONBEAT:
        P.hit(3, rig.D, s, d.hum(0.6 if s % 4 == 2 else 0.35 if s % 4 == 1 else 0.45, 0.1))
    P.track(1, "ride", 0.4, 0.75)
    ride(P, d, 1, 1 / 2, 0.75)
    P.track(2, "tom", 0.5, 0.7)
    clave(P, d, 2, pool=rig.S)


def g4(P, d):
    A = d.rig.ALL
    P.track(1, "closed", 0.3, 0.7)
    hats_16(P, d, 1)
    P.track(2, "tom", 0.5, 0.9)
    clave(P, d, 2, pool=A)
    for s, lv in ((28, 0.5), (29, 0.65), (30, 0.8), (31, 1.0)):
        P.hit(2, d.hop(A, d.few(A), "clave"), s, lv)


def w1(P, d):
    A = d.rig.ALL
    P.track(0, "hard", 0.5, 1.0)
    size = d.share(A, 2 / 9, 1, len(A) - 1)
    for b in BEATS:
        hole = d.hop(A, size, "hole")
        P.hit(0, [p for p in A if p not in hole], b, 1.0)
    P.track(1, "strobe", 0.25, 1.0)
    for s in OFF:
        P.hit(1, d.hop(A, d.few(A), "glint"), s, 0.35)


def w2(P, d):
    rig = d.rig
    P.track(0, "hard", 0.5, 1.0)
    for b in BEATS:
        P.hit(0, rig.D, b, 1.0)
    P.track(1, "bounce", 0.4, 0.9)
    for half in (0, 16):
        odd = half + 2 + 4 * int(d.rand() * 4)
        for s in [x for x in OFF if half <= x < half + 16]:
            P.hit(1, pair_pick(d, "bounce") if s == odd else rig.S, s, 0.95)
    P.track(2, "strobe", 0.25, 0.9)
    for half in (0, 16):
        for s in d.hop([x + half for x in (3, 7, 11, 15)], 2, "stutter-steps", pads=False):
            P.hit(2, d.hop(rig.ALL, d.few(rig.ALL), "stutter"), s, 0.7)


def w3(P, d):
    rig = d.rig
    P.track(0, "hard", 0.5, 1.0)
    for b in BEATS:
        P.hit(0, rig.D, b, 1.0)
    P.track(1, "strobe", 0.25, 1.0)
    for s in NONBEAT:
        P.hit(1, d.hop(rig.ALL, d.few(rig.ALL), "scatter"), s, 0.5 + 0.5 * d.rand())


def w4(P, d):
    rig, A = d.rig, d.rig.ALL
    P.track(1, "strobe", 0.25, 1.0)
    for s in d.hop([x for x in range(1, 16) if x % 4], 5, "stray-steps", pads=False):
        P.hit(1, d.hop(A, d.few(A), "stray"), s, 0.3 + 0.3 * d.rand())
    roll = [(16, 1), (20, 1), (24, 1), (26, 2), (28, 2), (29, 3), (30, 3), (31, 4)]
    for i, (s, n9) in enumerate(roll):
        P.hit(1, d.hop(A, d.share(A, n9 / 9), "roll"), s, 0.4 + 0.6 * i / (len(roll) - 1))
    P.track(3, "swell", 1.0, 0.8)
    for s in range(16, 32):
        P.hit(3, rig.D, s, 0.05 + 0.65 * (s - 16) / 15)


# id: (name, bank, column, swing, bpm hint, recipe)
RECIPES = {
    "R1": ("R1 Pump", "Rumble", "Minimal", 0.1, 134, r1),
    "R2": ("R2 3/16", "Rumble", "Groove", 0.1, 134, r2),
    "R3": ("R3 Dub", "Rumble", "Peak", 0.1, 134, r3),
    "R4": ("R4 No kick", "Rumble", "Kick out", 0.1, 134, r4),
    "D1": ("D1 909", "Machine funk", "Minimal", 0.3, 128, d1),
    "D2": ("D2 Shuffle", "Machine funk", "Groove", 0.3, 128, d2),
    "D3": ("D3 Stabs", "Machine funk", "Peak", 0.3, 128, d3),
    "D4": ("D4 No kick", "Machine funk", "Kick out", 0.3, 128, d4),
    "G1": ("G1 Ride", "Hard groove", "Minimal", 0.2, 142, g1),
    "G2": ("G2 Tribal", "Hard groove", "Groove", 0.2, 142, g2),
    "G3": ("G3 Rolling", "Hard groove", "Peak", 0.2, 142, g3),
    "G4": ("G4 No kick", "Hard groove", "Kick out", 0.2, 142, g4),
    "W1": ("W1 Stomp", "Warehouse", "Minimal", 0.0, 155, w1),
    "W2": ("W2 Bounce", "Warehouse", "Groove", 0.0, 155, w2),
    "W3": ("W3 Scatter", "Warehouse", "Peak", 0.0, 155, w3),
    "W4": ("W4 Tension", "Warehouse", "Kick out", 0.0, 155, w4),
}
BANKS = {"techno": ["R1", "R2", "R3", "R4", "D1", "D2", "D3", "D4",
                    "G1", "G2", "G3", "G4", "W1", "W2", "W3", "W4"]}
DEFAULT_SEEDS = [11, 23, 37, 41, 53, 67, 71, 83, 97, 101, 113, 127, 131, 149, 157, 163]


# --------------------------------------------------------------------------- #
# Realise / re-fit
# --------------------------------------------------------------------------- #

def _env_dict(name: str) -> dict:
    a, dcy, s, r = ENV.get(name, (0, 0, 1, 0.1))
    return {"attack": float(a), "decay": float(dcy), "sustain": float(s), "release": float(r)}


def realize(recipe: str, rig: Rig, seed: int, bank: str = "techno") -> dict:
    """One pattern dict (chases.yaml schema) for this rig."""
    name, _bank, _col, swing, _bpm, fn = RECIPES[recipe]
    P = Pat()
    fn(P, Draw(rig, seed))
    tracks = []
    for tr in P.tracks:
        tracks.append({
            "texture": None,
            "envelope": _env_dict(tr["env"]),
            "gate": float(tr["gate"]), "level": float(tr["level"]),
            "steps": {f"pad {p}": [[s, lv, None] for s, lv in sorted(st.items())]
                      for p, st in sorted(tr["steps"].items(), key=lambda kv: rig.pos(kv[0]))},
        })
    return {"name": name, "length": STEPS, "direction": "forward", "swing": float(swing), "tracks": tracks,
            "source": {"bank": bank, "recipe": recipe, "seed": int(seed), "rig": rig.signature()}}


def realize_bank(rig: Rig, bank: str = "techno", seeds: Optional[Sequence[int]] = None) -> List[dict]:
    seeds = list(seeds or DEFAULT_SEEDS)
    return [realize(rid, rig, seeds[i], bank) for i, rid in enumerate(BANKS[bank])]


def refit(pattern: dict, rig: Rig) -> dict:
    """New placement for a realised pattern on a changed rig. Keeps what the user owns: name,
    length, direction, swing and every track's envelope, gate and level. Patterns without a
    `source` (edited by hand) come back unchanged."""
    src = pattern.get("source")
    if not src:
        return pattern
    fresh = realize(src["recipe"], rig, src["seed"], src.get("bank", "techno"))
    out = copy.deepcopy(pattern)
    for old, new in zip(out["tracks"], fresh["tracks"]):
        old["steps"] = new["steps"]
    out["source"]["rig"] = rig.signature()
    return out


def presets(bank: str = "techno") -> List[tuple]:
    """[(recipe id, name)] in the order the Push shows them (buttons 1-8, Shift = 9-16)."""
    return [(rid, RECIPES[rid][0]) for rid in BANKS[bank]]
