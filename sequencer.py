"""Step sequencer for the LED bars: bars from an Advanced Output preset, patterns with up to
four texture tracks, ADSR envelopes and the playback clock. Pure Python: nothing here talks to
Resolume or the Push; the bridge feeds it beat time and sends the levels it returns."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

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
