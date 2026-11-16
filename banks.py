"""SEQ banks: patterns stored as recipes in role space (D / S), realised onto the current rig,
bent by five macros (strobe, density, speed, spread, accent).

A recipe fixes the rhythm (steps 0-31 at grid 1/16) and the tier of every hit. The rig and the
seed decide which pads a hit lands on; the macros decide envelope shape, how many hits survive
and how wide they are, how often and how far a voice moves, and the level contrast.
Output = pattern dicts in the chases.yaml schema of sequencer.py (`_pattern_from_dict` reads
them), plus `source` (bank, recipe, seed, macros, and the rig signature the placement was made
for). Pure Python, 3.9 compatible, deterministic: same recipe + rig + seed + macros → same
pattern, and identical to the web preview page (same RNG, same call order).
"""
from __future__ import annotations

import copy
import math
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

ENV = {   # attack / decay / release in beats, sustain 0-1, at strobe 0.5
    "kick":   (0, 0.12, 0.2, 0.2),   "hard":   (0, 0.08, 0.3, 0.12),
    "rumble": (0.2, 0, 1, 0.3),      "tick":   (0, 0.05, 0.4, 0.15),
    "closed": (0, 0.03, 0.3, 0.06),  "open":   (0, 0.05, 0.6, 0.3),
    "ride":   (0, 0.05, 0.5, 0.2),   "clap":   (0, 0.1, 0.35, 0.45),
    "tom":    (0, 0.08, 0.4, 0.3),   "stab":   (0, 0.15, 0.4, 0.5),
    "dub":    (0, 0.1, 0.5, 0.6),    "roll":   (0, 0.04, 0.3, 0.05),
    "bounce": (0, 0.06, 0.3, 0.1),   "strobe": (0, 0.02, 0.1, 0.04),
    "glow":   (0.25, 0, 1, 1.5),     "swell":  (0.25, 0, 1, 0.3),
    "none":   (0, 0, 1, 0.1),
}
MACROS = {"strobe": 0.5, "density": 0.5, "speed": 0.5, "spread": 0.5, "accent": 0.5}
MACRO_NAMES = ["strobe", "density", "speed", "spread", "accent"]
TIER_BACKBONE, TIER_GROOVE, TIER_ORNAMENT = 1, 2, 3


def rnd(x: float, places: int) -> float:
    """Round half up, like JavaScript's Math.round (Python's round() is banker's rounding)."""
    f = 10 ** places
    return math.floor(x * f + 0.5) / f


# --------------------------------------------------------------------------- #
# Placement vocabulary
# --------------------------------------------------------------------------- #

class Mulberry32:
    """The web page's PRNG, so a seed gives the same placement in the browser and in the bridge."""

    def __init__(self, seed: int):
        self.a = int(seed) & 0xFFFFFFFF

    def __call__(self) -> float:
        self.a = (self.a + 0x6D2B79F5) & 0xFFFFFFFF
        t = self.a
        t = ((t ^ (t >> 15)) * (1 | t)) & 0xFFFFFFFF
        t = (t + (((t ^ (t >> 7)) * (61 | t)) & 0xFFFFFFFF) ^ t) & 0xFFFFFFFF
        return ((t ^ (t >> 14)) & 0xFFFFFFFF) / 4294967296


class Draw:
    """Random placement on a rig under the macros. Every `stream` (one voice of one recipe)
    remembers its last pick: a stream never lands on the same pad (n = 1) or the same set (n > 1)
    twice in a row, unless the pool is too small."""

    def __init__(self, rig: Rig, seed: int, macros: Optional[dict] = None):
        self.rig, self.r = rig, Mulberry32(seed)
        self.m = dict(MACROS, **(macros or {}))
        self.prev: Dict[str, Optional[List[int]]] = {}
        self.cnt: Dict[str, int] = {}

    def rand(self) -> float:
        return self.r()

    # ---- macro helpers --------------------------------------------------------------- #
    def hold(self) -> int:
        """Speed: how many consecutive hits of a stream keep the same pads."""
        s = self.m["speed"]
        return 1 if s >= 0.5 else 2 if s >= 0.35 else 4 if s >= 0.2 else 8

    def mult(self) -> float:
        """Density: scale for the number of pads per hit (0.5 … 1 … 2)."""
        d = self.m["density"]
        return 0.5 + d if d < 0.5 else 1 + (d - 0.5) * 2

    def allow(self, tier: int) -> bool:
        """Density: backbone always, grooves from 0.15, ornaments from 0.4."""
        d = self.m["density"]
        return tier <= 1 or (tier == 2 and d >= 0.15) or (tier == 3 and d >= 0.4)

    def accent_level(self, lv: float) -> float:
        """Accent: 0 = every hit at 0.85, 0.5 = recipe levels, 1 = recipe level cubed."""
        a = self.m["accent"]
        v = 0.85 + (lv - 0.85) * a * 2 if a <= 0.5 else lv ** (1 + (a - 0.5) * 4)
        return max(0.05, min(1.0, v))

    def hum(self, lv: float, amt: float = 0.15) -> float:
        """Level jitter, scaled by accent. Always draws one random number (determinism)."""
        return lv * (1 - amt * 2 * self.m["accent"] * self.rand())

    @staticmethod
    def few(pool: Sequence[int]) -> int:
        """Pads a "single" hit lights: 1 up to 11 pads in the pool, 2 up to 19, then 3."""
        return max(1, (len(pool) + 4) // 8)

    @staticmethod
    def share(pool: Sequence[int], frac: float, lo: int = 1, hi: Optional[int] = None) -> int:
        hi = len(pool) if hi is None else hi
        return max(lo, min(hi, int(math.floor(frac * len(pool) + 0.5))))

    # ---- picking ------------------------------------------------------------------- #
    def pick(self, cands: List[int], anchor: Optional[int], pads: bool) -> int:
        """One item. Spread biases pads by distance from the anchor (previous pick)."""
        s = self.m["spread"]
        k = abs(s - 0.5) * 2 * 3
        if not pads or k == 0 or anchor is None:
            return cands[int(self.rand() * len(cands))]
        n, p0 = len(self.rig.pads), self.rig.pos(anchor)
        w = []
        for c in cands:
            dist = abs(self.rig.pos(c) - p0) / max(1, n - 1)
            w.append((0.15 + dist if s > 0.5 else 1.15 - dist) ** k)
        x = self.rand() * sum(w)
        for c, wi in zip(cands, w):
            x -= wi
            if x <= 0:
                return c
        return cands[-1]

    def subset(self, cands: List[int], n: int, anchor: Optional[int], pads: bool) -> List[int]:
        pool, out, a = list(cands), [], anchor
        while len(out) < n and pool:
            c = self.pick(pool, a, pads)
            pool.remove(c)
            out.append(c)
            if a is None:
                a = c
        return sorted(out)

    def hop(self, pool: Sequence[int], n: int = 1, stream: str = "", avoid: Sequence[int] = (),
            pads: bool = True, raw: bool = False) -> List[int]:
        """n random items from pool, not in avoid, not the previous pick of this stream.
        Speed holds the previous pick for several calls; density scales n (pads only, unless raw);
        spread biases the distance from the previous pick. A constraint the pool is too small for
        is dropped ("not previous" first, then avoid). pads=False: items are steps or pair indices."""
        pool = list(pool)
        if pads and not raw:
            n = max(1, min(len(pool), int(math.floor(n * self.mult() + 0.5))))
        else:
            n = max(1, min(n, len(pool)))
        prev = self.prev.get(stream)
        cnt = self.cnt[stream] = self.cnt.get(stream, 0) + 1
        if prev and (cnt - 1) % self.hold() != 0 and len(prev) == n \
                and not any(p in avoid for p in prev) and all(p in pool for p in prev):
            return prev
        anchor = prev[len(prev) // 2] if prev else None
        choice = pool[:n]
        for cands in ([p for p in pool if p not in avoid and (n > 1 or p not in (prev or []))],
                      [p for p in pool if p not in avoid], pool):
            if len(cands) >= n:
                choice = self.subset(cands, n, anchor, pads)
                for _ in range(8):
                    if not (n > 1 and prev and choice == prev and len(cands) > n):
                        break
                    choice = self.subset(cands, n, anchor, pads)
                break
        self.prev[stream] = choice
        return choice

    def block(self, pool: Sequence[int], stream: str) -> List[int]:
        """A hit on a whole pool (all D, all S, everything). Speed above 0.5 turns it into a
        moving part of the pool: at 1.0 half of it, a new half each time."""
        s = self.m["speed"]
        if s <= 0.5 or len(pool) < 2:
            return list(pool)
        return self.hop(pool, max(1, int(math.floor(len(pool) * (1 - (s - 0.5)) + 0.5))), stream, raw=True)


class Pat:
    def __init__(self, d: Draw):
        self.d = d
        self.tracks = [{"env": "none", "gate": 0.5, "level": 1.0, "steps": {}} for _ in range(4)]

    def track(self, t: int, env: str, gate: float, level: float):
        self.tracks[t].update(env=env, gate=gate, level=level)

    def hit(self, t: int, pads: Sequence[int], step: int, lv: float, tier: int = TIER_GROOVE):
        if not self.d.allow(tier):
            return
        v = rnd(self.d.accent_level(lv), 2)
        for p in pads:
            st = self.tracks[t]["steps"].setdefault(p, {})
            st[step] = max(st.get(step, 0.0), v)


def uniq(pads: Sequence[int]) -> List[int]:
    return sorted(set(pads))


def kick_n(rig: Rig) -> int:
    """Pads a hopping kick lights: one on small rigs, a third of the D pads from 5 D up."""
    return 1 if len(rig.D) <= 4 else Draw.share(rig.D, 1 / 3)


def pair_pick(d: Draw, stream: str) -> List[int]:
    """Random mirrored S pair(s), not the same choice twice in a row. Big rigs light several."""
    prs = d.rig.pairs
    idx = d.hop(list(range(len(prs))), max(1, int(math.floor(len(prs) / 3 + 0.5))), stream, pads=False)
    return uniq([p for i in idx for p in prs[i]])


# ---- building blocks shared by several recipes -------------------------------- #

def kick_rumble(P: Pat, d: Draw):
    rig = d.rig
    P.track(0, "kick", 0.5, 1.0)
    P.track(3, "rumble", 1.0, 0.45)
    for b in BEATS:
        pads = d.hop(rig.D, kick_n(rig), "kick")
        P.hit(0, pads, b, 1.0, TIER_BACKBONE)
        P.hit(3, uniq([s for p in pads for s in rig.sat(p)]), b + 1, d.hum(0.9, 0.1), TIER_GROOVE)


def perc_316(P: Pat, d: Draw, t: int):
    S = d.rig.S
    for s in range(2, STEPS, 3):
        P.hit(t, d.hop(S, d.few(S), "p316"), s, d.hum(1, 0.1) if s % 4 == 2 else d.hum(0.55), TIER_GROOVE)


def dub_throw(P: Pat, d: Draw, t: int, start: int, n: int):
    A = d.rig.ALL
    for i, lv in enumerate([1, 0.6, 0.35, 0.2][:n]):
        P.hit(t, d.hop(A, d.few(A), "dub"), start + 3 * i, lv, TIER_ORNAMENT if i else TIER_GROOVE)


def kick_909(P: Pat, d: Draw):
    rig = d.rig
    for b in BEATS:
        if b % 16 == 0:
            P.hit(0, d.block(rig.D, "kick-block"), b, 1.0, TIER_BACKBONE)
            d.prev["kick"] = None
        else:
            P.hit(0, d.hop(rig.D, kick_n(rig), "kick"), b, 0.85, TIER_BACKBONE)


def hats_16(P: Pat, d: Draw, t: int, scale: float = 1.0, pool: Optional[Sequence[int]] = None):
    pool = list(pool or d.rig.S)
    for s in NONBEAT:
        base = 0.9 if s % 4 == 2 else 0.35 if s % 4 == 1 else 0.55
        P.hit(t, d.hop(pool, d.few(pool), "hats"), s, d.hum(base * scale),
              TIER_GROOVE if s % 4 == 2 else TIER_ORNAMENT)


def claps(P: Pat, d: Draw, t: int):
    for s in (4, 12, 20, 28):
        P.hit(t, d.rig.outer_d, s, 0.9, TIER_BACKBONE)


def clave(P: Pat, d: Draw, t: int, kick_at: Optional[Dict[int, List[int]]] = None,
          pool: Optional[Sequence[int]] = None, tier: int = TIER_GROOVE):
    kick_at = kick_at or {}
    for s, kind in CLAVE:
        pl = list(pool) if pool else (d.rig.D if kind == "low" else d.rig.S)
        P.hit(t, d.hop(pl, d.few(pl), "clave", avoid=kick_at.get(s, [])), s,
              d.hum(0.95 if kind == "low" else 0.8), tier)


def ride(P: Pat, d: Draw, t: int, frac: float, lv: float):
    S = d.rig.S
    for s in OFF:
        P.hit(t, d.hop(S, d.share(S, frac), "ride"), s, d.hum(lv), TIER_GROOVE)


# --------------------------------------------------------------------------- #
# The 16 recipes (Techno bank). Track roles: 0 kick, 1 hats, 2 accent, 3 movement.
# --------------------------------------------------------------------------- #

def r1(P, d):
    kick_rumble(P, d)
    P.track(1, "tick", 0.3, 0.7)
    S = d.rig.S
    for s in (3, 9, 14, 19, 25, 30):
        P.hit(1, d.hop(S, d.few(S), "tick"), s, d.hum(0.7), TIER_GROOVE)


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
        P.hit(3, d.hop(d.rig.D, kick_n(d.rig), "glow"), s, d.hum(0.95, 0.1), TIER_BACKBONE)
    P.track(2, "dub", 0.5, 0.8)
    dub_throw(P, d, 2, 14, 4)


def d1(P, d):
    P.track(0, "kick", 0.5, 1.0)
    kick_909(P, d)
    P.track(1, "open", 0.5, 0.75)
    for s in OFF:
        P.hit(1, pair_pick(d, "open"), s, d.hum(0.85), TIER_GROOVE)
    P.track(2, "clap", 0.5, 0.9)
    claps(P, d, 2)


def d2(P, d):
    P.track(0, "kick", 0.5, 1.0)
    kick_909(P, d)
    P.track(1, "closed", 0.3, 0.7)
    hats_16(P, d, 1)
    P.track(2, "clap", 0.5, 0.9)
    claps(P, d, 2)
    P.hit(2, d.hop(d.rig.S, 1, "ghost"), 31, 0.4, TIER_ORNAMENT)


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
        P.hit(3, d.hop(A, d.share(A, 1 / 3), "stab"), s, d.hum(0.85), TIER_GROOVE)


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
        P.hit(3, d.hop(pl, d.few(pl), "tom"), s, d.hum(0.85), TIER_ORNAMENT if s >= 28 else TIER_GROOVE)


def g1(P, d):
    rig = d.rig
    P.track(0, "kick", 0.5, 1.0)
    for b in BEATS:
        P.hit(0, d.block(rig.D, "kick-block"), b, 1.0 if b % 16 == 0 else 0.85, TIER_BACKBONE)
    P.track(1, "ride", 0.4, 0.75)
    ride(P, d, 1, 2 / 3, 0.8)
    for s in A_STEPS:
        P.hit(1, d.hop(rig.S, d.few(rig.S), "ping"), s, 0.3, TIER_ORNAMENT)


def g2(P, d):
    rig = d.rig
    P.track(0, "kick", 0.5, 1.0)
    kick_at = {}
    for b in BEATS:
        kick_at[b] = d.hop(rig.D, kick_n(rig), "kick")
        P.hit(0, kick_at[b], b, 1.0, TIER_BACKBONE)
    P.track(2, "tom", 0.5, 0.85)
    clave(P, d, 2, kick_at)
    P.track(1, "ride", 0.4, 0.7)
    ride(P, d, 1, 1 / 3, 0.6)


def g3(P, d):
    rig = d.rig
    P.track(0, "kick", 0.5, 1.0)
    for b in BEATS:
        P.hit(0, d.block(rig.D, "kick-block"), b, 1.0, TIER_BACKBONE)
    P.track(3, "roll", 0.4, 0.5)
    for s in NONBEAT:
        P.hit(3, d.block(rig.D, "roll"), s, d.hum(0.6 if s % 4 == 2 else 0.35 if s % 4 == 1 else 0.45, 0.1),
              TIER_GROOVE if s % 4 == 2 else TIER_ORNAMENT)
    P.track(1, "ride", 0.4, 0.75)
    ride(P, d, 1, 1 / 2, 0.75)
    P.track(2, "tom", 0.5, 0.7)
    clave(P, d, 2, pool=rig.S)


def g4(P, d):
    A = d.rig.ALL
    P.track(1, "closed", 0.3, 0.7)
    hats_16(P, d, 1)
    P.track(2, "tom", 0.5, 0.9)
    clave(P, d, 2, pool=A, tier=TIER_BACKBONE)
    for s, lv in ((28, 0.5), (29, 0.65), (30, 0.8), (31, 1.0)):
        P.hit(2, d.hop(A, d.few(A), "clave"), s, lv, TIER_ORNAMENT)


def w1(P, d):
    A = d.rig.ALL
    P.track(0, "hard", 0.5, 1.0)
    size = Draw.share(A, 2 / 9, 1, len(A) - 1)
    for b in BEATS:
        hole = d.hop(A, size, "hole", raw=True)
        P.hit(0, [p for p in A if p not in hole], b, 1.0, TIER_BACKBONE)
    P.track(1, "strobe", 0.25, 1.0)
    for s in OFF:
        P.hit(1, d.hop(A, d.few(A), "glint"), s, 0.35, TIER_ORNAMENT)


def w2(P, d):
    rig = d.rig
    P.track(0, "hard", 0.5, 1.0)
    for b in BEATS:
        P.hit(0, d.block(rig.D, "kick-block"), b, 1.0, TIER_BACKBONE)
    P.track(1, "bounce", 0.4, 0.9)
    for half in (0, 16):
        odd = half + 2 + 4 * int(d.rand() * 4)
        for s in [x for x in OFF if half <= x < half + 16]:
            P.hit(1, pair_pick(d, "bounce") if s == odd else d.block(rig.S, "bounce-block"), s, 0.95, TIER_GROOVE)
    P.track(2, "strobe", 0.25, 0.9)
    for half in (0, 16):
        for s in d.hop([x + half for x in (3, 7, 11, 15)], 2, "stutter-steps", pads=False):
            P.hit(2, d.hop(rig.ALL, d.few(rig.ALL), "stutter"), s, 0.7, TIER_ORNAMENT)


def w3(P, d):
    rig = d.rig
    P.track(0, "hard", 0.5, 1.0)
    for b in BEATS:
        P.hit(0, d.block(rig.D, "kick-block"), b, 1.0, TIER_BACKBONE)
    P.track(1, "strobe", 0.25, 1.0)
    for s in NONBEAT:
        P.hit(1, d.hop(rig.ALL, d.few(rig.ALL), "scatter"), s, 0.5 + 0.5 * d.rand(),
              TIER_GROOVE if s % 4 == 2 else TIER_ORNAMENT)


def w4(P, d):
    rig, A = d.rig, d.rig.ALL
    P.track(1, "strobe", 0.25, 1.0)
    for s in d.hop([x for x in range(16) if x % 4], 5, "stray-steps", pads=False):
        P.hit(1, d.hop(A, d.few(A), "stray"), s, 0.3 + 0.3 * d.rand(), TIER_ORNAMENT)
    roll = [(16, 1), (20, 1), (24, 1), (26, 2), (28, 2), (29, 3), (30, 3), (31, 4)]
    for i, (s, n9) in enumerate(roll):
        P.hit(1, d.hop(A, d.share(A, n9 / 9), "roll"), s, 0.4 + 0.6 * i / (len(roll) - 1), TIER_BACKBONE)
    P.track(3, "swell", 1.0, 0.8)
    for s in range(16, 32):
        P.hit(3, d.block(rig.D, "swell"), s, 0.05 + 0.65 * (s - 16) / 15, TIER_GROOVE)


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
# Envelope morph (Strobe)
# --------------------------------------------------------------------------- #

def morph(env_name: str, gate: float, strobe: float) -> dict:
    """The recipe's envelope and gate bent by Strobe. 0.5 = as written; below: slower attack,
    longer decay and release, higher sustain, longer gate; above: shorter everything, lower
    sustain, with floors so a flash stays visible (release ≥ 0.05 beat, gate ≥ 15 %)."""
    A, D, S, R = ENV.get(env_name, ENV["none"])
    G = gate
    t = (strobe - 0.5) * 2
    if t <= 0:
        u = -t
        A, D, S, R, G = A + 0.25 * u, D * (1 + u), S + (1 - S) * u, R * (1 + 3 * u), G + (1 - G) * u * 0.5
    else:
        A, D, S = A * (1 - t), D * (1 - 0.7 * t), S * (1 - 0.8 * t)
        R, G = max(0.05, R * (1 - 0.85 * t)), max(0.15, G * (1 - 0.6 * t))
    return {"attack": rnd(A, 3), "decay": rnd(D, 3), "sustain": rnd(S, 3), "release": rnd(R, 3), "gate": rnd(G, 3)}


# --------------------------------------------------------------------------- #
# Realise / re-fit / macros
# --------------------------------------------------------------------------- #

def clean_macros(macros: Optional[dict]) -> dict:
    out = dict(MACROS)
    for k, v in (macros or {}).items():
        if k in out:
            out[k] = max(0.0, min(1.0, float(v)))
    return out


def realize(recipe: str, rig: Rig, seed: int, bank: str = "techno", macros: Optional[dict] = None) -> dict:
    """One pattern dict (chases.yaml schema) for this rig, seed and macros."""
    name, _bank, _col, swing, _bpm, fn = RECIPES[recipe]
    m = clean_macros(macros)
    d = Draw(rig, seed, m)
    P = Pat(d)
    fn(P, d)
    tracks = []
    for tr in P.tracks:
        e = morph(tr["env"], tr["gate"], m["strobe"])
        tracks.append({
            "texture": None,
            "envelope": {k: e[k] for k in ("attack", "decay", "sustain", "release")},
            "gate": e["gate"], "level": float(tr["level"]),
            "steps": {f"pad {p}": [[s, lv, None] for s, lv in sorted(st.items())]
                      for p, st in sorted(tr["steps"].items(), key=lambda kv: rig.pos(kv[0]))},
        })
    return {"name": name, "length": STEPS, "direction": "forward", "swing": float(swing), "tracks": tracks,
            "source": {"bank": bank, "recipe": recipe, "seed": int(seed), "macros": m, "rig": rig.signature()}}


def realize_bank(rig: Rig, bank: str = "techno", seeds: Optional[Sequence[int]] = None,
                 macros: Optional[Sequence[Optional[dict]]] = None) -> List[dict]:
    seeds = list(seeds or DEFAULT_SEEDS)
    return [realize(rid, rig, seeds[i], bank, macros[i] if macros else None) for i, rid in enumerate(BANKS[bank])]


def refit(pattern: dict, rig: Rig) -> dict:
    """New placement for a realised pattern on a changed rig. Keeps what the user owns: name,
    length, direction, swing and every track's envelope, gate and level. Patterns without a
    `source` (edited by hand) come back unchanged."""
    src = pattern.get("source")
    if not src or src.get("recipe") not in RECIPES:              # own pattern, or a recipe this version lacks
        return pattern
    fresh = realize(src["recipe"], rig, src["seed"], src.get("bank", "techno"), src.get("macros"))
    out = copy.deepcopy(pattern)
    for old, new in zip(out["tracks"], fresh["tracks"]):
        old["steps"] = new["steps"]
    out["source"]["rig"] = rig.signature()
    return out


def set_macros(pattern: dict, rig: Rig, macros: dict, seed: Optional[int] = None) -> dict:
    """Re-realise a bank pattern with new macros (and optionally a new seed): placement, hit set
    and levels follow the macros; the envelope and gate come from Strobe again (a user's own
    ENVELOPE-knob edits are replaced). Name, length, direction, swing and track Level are kept.
    Patterns without a `source` come back unchanged."""
    src = pattern.get("source")
    if not src or src.get("recipe") not in RECIPES:              # own pattern, or a recipe this version lacks
        return pattern
    m = clean_macros(dict(src.get("macros") or {}, **macros))
    fresh = realize(src["recipe"], rig, src["seed"] if seed is None else seed, src.get("bank", "techno"), m)
    out = copy.deepcopy(pattern)
    for old, new in zip(out["tracks"], fresh["tracks"]):
        old["steps"], old["envelope"], old["gate"] = new["steps"], new["envelope"], new["gate"]
    out["source"] = fresh["source"]
    return out


def presets(bank: str = "techno") -> List[tuple]:
    """[(recipe id, name)] in the order the Push shows them."""
    return [(rid, RECIPES[rid][0]) for rid in BANKS[bank]]
