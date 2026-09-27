"""Rig: the pads SEQ plays on, in physical order, each with a role.

D = dominant bar (long), S = secondary bar (short). Pure Python, no Resolume or Push access:
the bridge passes in the pad assignment (Bar Chaser `Pad n` dropdown values) and, when it can,
the Advanced Output preset the plugin reads. Python 3.9 compatible.
"""
from __future__ import annotations

import glob
import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

MIN_PADS, MAX_PADS = 4, 24
UNIFORM_RATIO = 1.3          # longest / shortest bar below this = all bars count as the same kind
PRESET_FOLDER = os.path.expanduser("~/Documents/Resolume Arena/Presets/Advanced Output")

Rect = Tuple[float, float, float, float]       # left, top, right, bottom in composition pixels


class RigError(ValueError):
    pass


@dataclass
class PadInfo:
    pad: int                     # 1-based Bar Chaser pad number
    name: str = ""               # dropdown value: screen name or "Screen / slice"; "" = unassigned
    rect: Optional[Rect] = None  # from the preset, None when unknown


# --------------------------------------------------------------------------- #
# Advanced Output preset → rectangles (port of plugin/src/Preset.cpp)
# --------------------------------------------------------------------------- #

def resolve_preset(text: str = "", folder: str = PRESET_FOLDER) -> str:
    """Same rules as the plugin's Preset parameter: "" = newest .xml, a name, or a path."""
    t = (text or "").strip()
    if not t:
        files = [f for f in glob.glob(os.path.join(folder, "*.xml")) if not os.path.basename(f).startswith(".")]
        return max(files, key=os.path.getmtime) if files else ""
    if t.endswith(".xml"):
        return t if "/" in t else os.path.join(folder, t)
    return os.path.join(folder, t + ".xml")


def _union(a: Optional[Rect], b: Rect) -> Rect:
    return b if a is None else (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def preset_rects(path: str) -> Dict[str, Rect]:
    """{dropdown name: rect} for exactly the names Bar Chaser offers in `Pad n` (plugin/src/Preset.cpp):
    every fixture as "Screen / slice", then every whole screen (bounding box of its slices). A pad is
    one fixture; the whole-screen names only keep older shows working."""
    root = ET.parse(path).getroot()
    out: Dict[str, Rect] = {}
    screens = []
    for el in root.iter():
        if not el.tag.endswith("Screen") or "name" not in el.attrib:
            continue
        box, slices = None, []
        for sl in el.iter():
            if not sl.tag.endswith("Slice"):
                continue
            ir = sl.find("InputRect")
            pts = [(float(v.get("x", 0)), float(v.get("y", 0))) for v in ir.findall("v")] if ir is not None else []
            if not pts:
                continue
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            r = (min(xs), min(ys), max(xs), max(ys))
            name = "slice"
            for prm in sl.iter("Param"):
                if prm.get("name") == "Name":
                    name = prm.get("value", "slice")
                    break
            slices.append((name, r))
            box = _union(box, r)
        if box is not None:
            screens.append((el.get("name"), box, slices))
    for name, _box, slices in screens:                  # the first of two equal names wins, as in the plugin
        for n, r in slices:
            out.setdefault(f"{name} / {n}", r)
    for name, box, _slices in screens:
        out.setdefault(name, box)
    return out


# --------------------------------------------------------------------------- #
# Roles
# --------------------------------------------------------------------------- #

def length_of(r: Rect) -> float:
    return max(r[2] - r[0], r[3] - r[1])


def split_by_length(lengths: Sequence[float]) -> Optional[List[str]]:
    """'D' / 'S' per bar from bar lengths, or None when all bars are alike (uniform rig).
    Splits at the biggest ratio jump between neighbouring sorted lengths; the longer side is D."""
    if not lengths or min(lengths) <= 0 or max(lengths) / min(lengths) < UNIFORM_RATIO:
        return None
    srt = sorted(lengths)
    jumps = [(srt[i + 1] / srt[i], i) for i in range(len(srt) - 1)]
    _, i = max(jumps)
    cut = srt[i + 1]
    return ["D" if x >= cut else "S" for x in lengths]


def virtual_count(n: int) -> int:
    """How many pads play the D role on a uniform rig: nearest to n/3, same parity as n (so the
    D set can be mirror-symmetric), at most n/2; ties go to the smaller count."""
    cands = [k for k in range(1, n // 2 + 1) if k % 2 == n % 2] or [1]
    return min(cands, key=lambda k: (abs(k - n / 3.0), k))


def virtual_positions(n: int) -> List[int]:
    """0-based positions of the virtual D pads: evenly spaced, mirrored around the centre.
    n=4 → [1, 2], n=9 → [1, 4, 7], n=10 → [1, 3, 6, 8], n=12 → [1, 4, 7, 10]."""
    k = virtual_count(n)
    pos = set()
    for i in range(k // 2):
        p = int(((i + 0.5) * n) // k)
        pos.update((p, n - 1 - p))
    if k % 2:
        pos.add((n - 1) // 2)
    return sorted(pos)


# --------------------------------------------------------------------------- #
# Rig
# --------------------------------------------------------------------------- #

@dataclass
class Rig:
    pads: List[int]                    # pad numbers, left → right
    roles: List[str]                   # "D" / "S", parallel to pads
    virtual: bool = False              # True = all bars alike, roles chosen by position
    source: str = ""                   # "config" / "preset" / "pad order"
    D: List[int] = field(init=False)
    S: List[int] = field(init=False)

    def __post_init__(self):
        if not MIN_PADS <= len(self.pads) <= MAX_PADS:
            raise RigError(f"SEQ banks need {MIN_PADS}–{MAX_PADS} pads, found {len(self.pads)}")
        self.D = [p for p, r in zip(self.pads, self.roles) if r == "D"]
        self.S = [p for p, r in zip(self.pads, self.roles) if r == "S"]
        if not self.D or not self.S:
            raise RigError("a rig needs at least one D and one S pad")

    # ---- derived pools (all lists are left → right) --------------------------------- #
    @property
    def ALL(self) -> List[int]:
        return list(self.pads)

    def pos(self, pad: int) -> int:
        return self.pads.index(pad)

    def sat(self, d: int) -> List[int]:
        """Satellites of dominant pad d: the S pad directly left and directly right of it. When
        both neighbours are D (or the rig edge), the nearest S pad anywhere (ties: left)."""
        i, out = self.pos(d), []
        for j in (i - 1, i + 1):
            if 0 <= j < len(self.pads) and self.roles[j] == "S":
                out.append(self.pads[j])
        if not out:
            out = [min(self.S, key=lambda s: (abs(self.pos(s) - i), self.pos(s)))]
        return out

    @property
    def pairs(self) -> List[List[int]]:
        """S pads mirrored by rank, outside → in: [S0, S-1], [S1, S-2] …; an odd middle pad alone."""
        s, out = self.S, []
        for i in range((len(s) + 1) // 2):
            j = len(s) - 1 - i
            out.append([s[i]] if i == j else [s[i], s[j]])
        return out

    @property
    def outer_d(self) -> List[int]:
        return sorted({self.D[0], self.D[-1]}, key=self.pos)

    def _pair_pads(self, prs) -> List[int]:
        return sorted({p for pr in prs for p in pr}, key=self.pos)

    @property
    def s_high(self) -> List[int]:
        """Outer half of the mirrored S pairs (high toms)."""
        prs = self.pairs
        return self._pair_pads(prs[:(len(prs) + 1) // 2])

    @property
    def s_mid(self) -> List[int]:
        """Inner half of the mirrored S pairs (mid toms). Overlaps s_high when the count is odd."""
        prs = self.pairs
        return self._pair_pads(prs[len(prs) // 2:])

    def signature(self) -> str:
        """Pads and roles in physical order, e.g. "1S 2D 3S 4S 5D 6S 7S 8D 9S". Changes = re-fit."""
        return " ".join(f"{p}{r}" for p, r in zip(self.pads, self.roles)) + (" v" if self.virtual else "")

    def describe(self) -> str:
        how = "all bars alike, D chosen by position" if self.virtual else f"roles from {self.source}"
        return f"{len(self.pads)} pads: {len(self.D)} D / {len(self.S)} S ({how})"


def build_rig(pads: Sequence[PadInfo], override: Optional[dict] = None) -> Rig:
    """Rig from the assigned pads.

    override (config.yaml → sequencer.rig), optional:
      dominant: [2, 5, 8]      pad numbers that play the D role
      order:    [3, 1, 2, …]   pad numbers left → right, when the preset can't tell
    Without an override the preset rectangles decide order (centre x, then y) and roles (length).
    """
    override = override or {}
    used = [p for p in pads if p.name or p.rect is not None]
    if override.get("order"):
        by_pad = {p.pad: p for p in used}
        used = [by_pad[n] for n in override["order"] if n in by_pad]
    elif used and all(p.rect is not None for p in used):
        used = sorted(used, key=lambda p: ((p.rect[0] + p.rect[2]) / 2, (p.rect[1] + p.rect[3]) / 2, p.pad))
    else:
        used = sorted(used, key=lambda p: p.pad)
    n = len(used)
    if not MIN_PADS <= n <= MAX_PADS:
        raise RigError(f"SEQ banks need {MIN_PADS}–{MAX_PADS} assigned pads, found {n}")
    order = [p.pad for p in used]

    roles, virtual, source = None, False, "pad order"
    if override.get("dominant"):
        dom = set(override["dominant"])
        roles, source = ["D" if p in dom else "S" for p in order], "config"
        if all(r == "D" for r in roles) or all(r == "S" for r in roles):
            roles = None                                   # nothing to contrast: split by position
    elif all(p.rect is not None for p in used):
        roles, source = split_by_length([length_of(p.rect) for p in used]), "preset"
    if roles is None:
        vp = set(virtual_positions(n))
        roles, virtual = ["D" if i in vp else "S" for i in range(n)], True
    return Rig(order, roles, virtual, source)


def rig_from_roles(text: str) -> Rig:
    """Test helper: "S D S S D S S D S" → pads 1..n in that order. "U9" = 9 alike bars."""
    t = text.strip()
    if t.upper().startswith("U"):
        n = int(t[1:])
        return build_rig([PadInfo(i + 1, f"Bar {i + 1}") for i in range(n)])
    roles = t.split()
    return Rig(list(range(1, len(roles) + 1)), roles, False, "test")
