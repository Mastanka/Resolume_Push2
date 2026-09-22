#!/usr/bin/env python3
"""
Push 2 -> Resolume Arena bridge
===============================

Pads      8x8 clip grid. Bottom pad row = lowest visible layer (same as Resolume).
          Press = connect clip, release = release (so Piano trigger style works).
          Pressing a pad also selects that layer/clip for the display.
Display   Selected layer + clip, and 8 parameter slots (one above each encoder).
Encoders  Track 1-8 edit the 8 slots. Hold Shift for fine steps.
          Master encoder (far right) = selected layer opacity.
Mix       Mix button toggles the mixer: Track 1-8 = layer masters (1 = top visible
          layer), Master encoder = composition master.
Buttons   Up/Down scroll layers, Left/Right scroll columns (Shift = jump by 8).
          Page < / Page > flip parameter pages when a layer has more than 8 slots.

Talks to Resolume through its REST API:
    Arena -> Preferences -> Webserver -> Enable Webserver & REST API

Usage
    python push_resolume_bridge.py                 run the bridge
    python push_resolume_bridge.py --sim           run with push2-python's browser simulator
    python push_resolume_bridge.py --dump 3        list parameter paths for layer 3 (for config.yaml)
    python push_resolume_bridge.py --dump 3 --clip 2
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import math
import queue
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests
import yaml

# push2_python, cairo and numpy are imported inside run()/render() so that
# `--dump` also works on a machine without the Push libraries installed.

TRACK_ENCODERS = [f"Track{i} Encoder" for i in range(1, 9)]
MASTER_ENCODER = "Master Encoder"
NAV_BUTTONS = ["Up", "Down", "Left", "Right", "Page Left", "Page Right"]
MIX_BUTTON = "Mix"                          # B_3 on the control map
MASTER_PATHS = ("master", "video/opacity")  # layer/composition master fader, fallback opacity

# One colour per layer (repeats every 8 layers). Used for pads and the display.
LAYER_RGB = [
    (0, 190, 255),   # cyan
    (255, 0, 150),   # magenta
    (255, 100, 0),   # orange
    (0, 255, 90),    # green
    (150, 60, 255),  # purple
    (255, 210, 0),   # yellow
    (255, 25, 25),   # red
    (40, 90, 255),   # blue
]
DIM = 0.15            # brightness of loaded-but-not-playing pads
PALETTE_BASE = 64     # Push palette slots 64..79 are overwritten with the layer colours

EDITABLE = {"ParamRange", "ParamChoice", "ParamBoolean"}
WALK_SKIP = {"clips", "name", "connected", "selected", "thumbnail", "audio"}
AUTO_SKIP_LAST = {"bypassed", "position", "duration"}
AUTO_SOURCES = [                     # where "auto" layers look for parameters, in order
    ("layer", "video/opacity"),
    ("clip", "video/sourceparams"),
    ("clip", "video/effects"),
    ("layer", "video/effects"),
    ("clip", "transport"),
]
OVERRIDE_HOLD = 0.8   # s to trust our own sent value over polled state (prevents jitter)

DEFAULT_CONFIG = {
    "resolume": {"host": "127.0.0.1", "port": 8080, "poll_interval": 0.25},
    "grid": {"layer_offset": 0, "column_offset": 0},
    "encoders": {"coarse": 0.01, "fine": 0.001},
    "display_fps": 20,
    "layers": {"default": "auto"},
}


# --------------------------------------------------------------------------- #
# Resolume JSON helpers
# --------------------------------------------------------------------------- #

def text(v, default=""):
    """Resolume wraps most strings as {"value": "..."}; unwrap either form."""
    if isinstance(v, dict):
        v = v.get("value")
    return v if isinstance(v, str) else default


def is_param(node):
    return isinstance(node, dict) and "valuetype" in node and "id" in node


def names_of(item):
    return [n for n in (text(item.get("display_name")), text(item.get("name"))) if n]


def label_of(item):
    names = names_of(item)
    return names[0] if names else None


def master_param(node):
    """The master fader of a layer or the composition."""
    for path in MASTER_PATHS:
        p = resolve_node(node, path) if node else None
        if is_param(p):
            return p
    return None


def clip_state(clip):
    """'Empty', 'Disconnected', 'Previewing', 'Connected', 'Connected & previewing'."""
    return text(clip.get("connected"), "Empty") if clip else "Empty"


def resolve_node(node, path):
    """Follow a path like 'video/effects/Transform/params/Scale' through the JSON.
    Dict keys match case-insensitively; list items match by name or by index."""
    for seg in (s for s in path.split("/") if s):
        s = seg.lower()
        if isinstance(node, dict):
            key = next((k for k in node if k.lower() == s), None)
            if key is None:
                return None
            node = node[key]
        elif isinstance(node, list):
            match = next((it for it in node if isinstance(it, dict)
                          and s in (n.lower() for n in names_of(it))), None)
            if match is None and seg.isdigit() and int(seg) < len(node):
                match = node[int(seg)]
            if match is None:
                return None
            node = match
        else:
            return None
    return node


def walk(node, prefix="", skip=WALK_SKIP):
    """Yield (path, param) for every editable parameter below node.
    Paths are built so resolve_node() can find them again."""
    if is_param(node):
        if node.get("valuetype") in EDITABLE:
            yield prefix, node
        return
    if isinstance(node, dict):
        for k, v in node.items():
            if k.lower() in skip:
                continue
            yield from walk(v, f"{prefix}/{k}" if prefix else k, skip)
    elif isinstance(node, list):
        seen = set()
        for i, item in enumerate(node):
            label = label_of(item) if isinstance(item, dict) else None
            if not label or "/" in label or label.lower() in seen:
                label = str(i)
            seen.add(label.lower())
            yield from walk(item, f"{prefix}/{label}" if prefix else label, skip)


def fmt_value(p, v, spec):
    """Return (display text, bar fraction 0..1)."""
    vt = p.get("valuetype")
    if vt == "ParamBoolean":
        return ("ON" if v else "OFF"), (1.0 if v else 0.0)
    if vt == "ParamChoice":
        opts = p.get("options") or []
        i = opts.index(v) if v in opts else 0
        return str(v), (i / (len(opts) - 1) if len(opts) > 1 else 0.0)
    lo, hi = spec.get("range") or (p.get("min", 0.0), p.get("max", 1.0))
    v = float(v or 0.0)
    span = (hi - lo) or 1.0
    frac = min(1.0, max(0.0, (v - lo) / span))
    if (p.get("min"), p.get("max")) == (0, 1):
        txt = f"{v * 100:.0f}%"
    elif abs(hi - lo) >= 20:
        txt = f"{v:.0f}"
    else:
        txt = f"{v:.2f}"
    return txt, frac


# --------------------------------------------------------------------------- #
# Resolume REST client + background sender
# --------------------------------------------------------------------------- #

class Resolume:
    def __init__(self, host, port):
        self.url = f"http://{host}:{port}"
        self.api = self.url + "/api/v1"
        self.session = requests.Session()   # used by the sender thread only

    def composition(self, session=None):
        r = (session or self.session).get(self.api + "/composition", timeout=2)
        r.raise_for_status()
        return r.json()

    def connect_clip(self, layer, column, down):
        # true = press, false = release (same as mouse down/up on the clip)
        self.session.post(f"{self.api}/composition/layers/{layer}/clips/{column}/connect",
                          data=json.dumps(bool(down)),
                          headers={"Content-Type": "application/json"}, timeout=1)

    def set_param(self, param_id, body):
        self.session.put(f"{self.api}/parameter/by-id/{param_id}", json=body, timeout=1)


class Sender(threading.Thread):
    """Sends clip triggers in order and parameter changes coalesced (latest value
    wins), so fast encoder turns never queue up behind the network."""

    def __init__(self, rest):
        super().__init__(daemon=True)
        self.rest = rest
        self.triggers = queue.Queue()
        self.params = {}
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self._last_err = 0.0

    def trigger(self, layer, column, down):
        self.triggers.put((layer, column, down))
        self.wake.set()

    def param(self, param_id, body):
        with self.lock:
            self.params[param_id] = body
        self.wake.set()

    def _err(self, e):
        if time.time() - self._last_err > 2:
            print(f"[resolume] {e}", file=sys.stderr)
            self._last_err = time.time()

    def run(self):
        while True:
            self.wake.wait(0.5)
            self.wake.clear()
            while not self.triggers.empty():
                try:
                    self.rest.connect_clip(*self.triggers.get_nowait())
                except Exception as e:
                    self._err(e)
            with self.lock:
                batch, self.params = self.params, {}
            for pid, body in batch.items():
                try:
                    self.rest.set_param(pid, body)
                except Exception as e:
                    self._err(e)


# --------------------------------------------------------------------------- #
# Bridge state
# --------------------------------------------------------------------------- #

@dataclass
class Slot:
    label: str
    path: str            # "layer:video/opacity" — shown when the encoder is touched
    param: dict | None   # None = path not found
    spec: dict = field(default_factory=dict)


class Bridge:
    def __init__(self, cfg, rest):
        self.cfg = cfg
        self.rest = rest
        self.sender = Sender(rest)
        self.lock = threading.RLock()
        self.comp = None
        self.online = False
        self.poll_interval = float(cfg["resolume"]["poll_interval"])
        self.layer_offset = int(cfg["grid"]["layer_offset"])
        self.col_offset = int(cfg["grid"]["column_offset"])
        self.coarse = float(cfg["encoders"]["coarse"])
        self.fine = float(cfg["encoders"]["fine"])
        self.sel = (1, 1)          # (layer, column), 1-based, as in Resolume
        self.mode = "params"       # "params" or "mix"
        self.page = 0
        self.shift = False
        self.touched = None        # encoder slot index being touched
        self.pressed = set()       # cells we sent a "down" for
        self.overrides = {}        # param id -> (value, time sent)
        self.choice_acc = {}
        self.midi_reset = False

    # ---- composition access --------------------------------------------- #
    def layers(self):
        return (self.comp or {}).get("layers") or []

    def layer_json(self, L):
        layers = self.layers()
        return layers[L - 1] if 1 <= L <= len(layers) else None

    def clip_json(self, L, C):
        layer = self.layer_json(L)
        clips = (layer or {}).get("clips") or []
        return clips[C - 1] if 1 <= C <= len(clips) else None

    def mix_layers(self):
        """Layers on encoders 1-8 in mix mode: top visible layer first."""
        top = min(self.layer_offset + 8, len(self.layers()))
        return list(range(top, max(self.layer_offset, 0), -1))[:8]

    def max_cols(self):
        return max((len(l.get("clips") or []) for l in self.layers()), default=0)

    def poll_loop(self):
        session = requests.Session()
        while True:
            try:
                comp = self.rest.composition(session)
                if not self.online:
                    print(f"[resolume] connected — {len(comp.get('layers') or [])} layers")
                with self.lock:
                    self.comp, self.online = comp, True
            except Exception as e:
                if self.online or not getattr(self, "_warned_offline", False):
                    print(f"[resolume] not reachable at {self.rest.url} ({type(e).__name__})")
                    self._warned_offline = True
                with self.lock:
                    self.online = False
            time.sleep(self.poll_interval)

    # ---- parameter slots ------------------------------------------------ #
    def layer_spec(self, L):
        lc = self.cfg.get("layers") or {}
        spec = lc.get(L, lc.get(str(L), lc.get("default", "auto")))
        return spec or "auto"

    def slots(self):
        L, C = self.sel
        layer, clip = self.layer_json(L), self.clip_json(L, C)
        if layer is None:
            return []
        roots = {"layer": layer, "clip": clip}
        spec = self.layer_spec(L)
        out = []
        if spec == "auto":
            seen = set()
            for scope, path in AUTO_SOURCES:
                node = resolve_node(roots[scope], path) if roots[scope] else None
                if node is None:
                    continue
                for p, param in walk(node, path):
                    last = p.rsplit("/", 1)[-1]
                    if last.lower() in AUTO_SKIP_LAST or param["id"] in seen:
                        continue
                    if param["valuetype"] == "ParamRange" and param.get("min") == param.get("max"):
                        continue
                    seen.add(param["id"])
                    out.append(Slot(last[:1].upper() + last[1:], f"{scope}:{p}", param))
        else:
            for s in spec:
                scope = s.get("scope", "layer")
                root = roots.get(scope)
                node = resolve_node(root, s["path"]) if root else None
                out.append(Slot(s.get("label") or s["path"].rsplit("/", 1)[-1],
                                f"{scope}:{s['path']}", node if is_param(node) else None, s))
        return out

    def page_slots(self, all_slots=None):
        all_slots = self.slots() if all_slots is None else all_slots
        pages = max(1, math.ceil(len(all_slots) / 8))
        self.page = min(self.page, pages - 1)
        return all_slots[self.page * 8:(self.page + 1) * 8], pages

    def value_of(self, p):
        ov = self.overrides.get(p["id"])
        if ov and time.time() - ov[1] < OVERRIDE_HOLD:
            return ov[0]
        return p.get("value")

    # ---- input handlers (called from push2-python's MIDI thread) --------- #
    def _set(self, pid, shown, body):
        self.overrides[pid] = (shown, time.time())
        self.sender.param(pid, body)

    def _nudge(self, p, spec, inc):
        vt, pid = p["valuetype"], p["id"]
        if vt == "ParamRange":
            pmin, pmax = p.get("min", 0.0), p.get("max", 1.0)
            lo, hi = spec.get("range") or (pmin, pmax)
            lo, hi = max(lo, pmin), min(hi, pmax)
            step = spec.get("step") or self.coarse
            if self.shift:
                step = spec["step"] / 10 if spec.get("step") else self.fine
            cur = float(self.value_of(p) or 0.0)
            new = round(min(hi, max(lo, cur + inc * step * (hi - lo))), 6)
            self._set(pid, new, {"value": new})
        elif vt == "ParamBoolean":
            self._set(pid, inc > 0, {"value": inc > 0})
        elif vt == "ParamChoice":
            opts = p.get("options") or []
            acc = self.choice_acc.get(pid, 0) + inc
            if not opts or abs(acc) < 4:          # 4 ticks per option = less twitchy
                self.choice_acc[pid] = acc
                return
            self.choice_acc[pid] = 0
            cur = self.value_of(p)
            i = opts.index(cur) if cur in opts else int(p.get("index", 0))
            i = min(len(opts) - 1, max(0, i + (1 if acc > 0 else -1)))
            self._set(pid, opts[i], {"index": i, "value": opts[i]})

    def turn(self, idx, inc):
        with self.lock:
            if self.mode == "mix":
                layers = self.mix_layers()
                p = master_param(self.layer_json(layers[idx])) if idx < len(layers) else None
                if p:
                    self._nudge(p, {}, inc)
                return
            slots, _ = self.page_slots()
            if idx < len(slots) and slots[idx].param is not None:
                self._nudge(slots[idx].param, slots[idx].spec, inc)

    def turn_master(self, inc):
        with self.lock:
            if self.mode == "mix":
                p = master_param(self.comp)
            else:
                p = resolve_node(self.layer_json(self.sel[0]), "video/opacity")
            if is_param(p):
                self._nudge(p, {}, inc)

    def pad_to_cell(self, i, j):
        return self.layer_offset + (8 - i), self.col_offset + j + 1

    def pad_pressed(self, ij):
        L, C = self.pad_to_cell(*ij)
        with self.lock:
            clip = self.clip_json(L, C)
            if clip is None:
                return
            if L != self.sel[0]:
                self.page = 0
            self.sel = (L, C)
            if clip_state(clip) == "Empty":
                return
            self.pressed.add((L, C))
        self.sender.trigger(L, C, True)

    def pad_released(self, ij):
        cell = self.pad_to_cell(*ij)
        with self.lock:
            if cell not in self.pressed:
                return
            self.pressed.discard(cell)
        self.sender.trigger(*cell, False)

    def button(self, name, down):
        if name == "Shift":
            self.shift = down
            return
        if not down:
            return
        jump = 8 if self.shift else 1
        with self.lock:
            max_l = max(0, len(self.layers()) - 8)
            max_c = max(0, self.max_cols() - 8)
            if name == "Up":
                self.layer_offset = min(max_l, self.layer_offset + jump)
            elif name == "Down":
                self.layer_offset = max(0, self.layer_offset - jump)
            elif name == "Right":
                self.col_offset = min(max_c, self.col_offset + jump)
            elif name == "Left":
                self.col_offset = max(0, self.col_offset - jump)
            elif name == "Page Right":
                _, pages = self.page_slots()
                self.page = min(pages - 1, self.page + 1)
            elif name == "Page Left":
                self.page = max(0, self.page - 1)
            elif name == MIX_BUTTON:
                self.mode = "params" if self.mode == "mix" else "mix"

    # ---- output state ----------------------------------------------------- #
    def button_colors(self):
        return {MIX_BUTTON: "white" if self.mode == "mix" else "dark_gray"}

    def pad_colors(self):
        grid = {}
        with self.lock:
            for i in range(8):
                for j in range(8):
                    L, C = self.pad_to_cell(i, j)
                    clip = self.clip_json(L, C) if self.online else None
                    color = "black"
                    if clip is not None:
                        state = clip_state(clip)
                        live = state.startswith("Connected")
                        k = (L - 1) % 8
                        if (L, C) == self.sel:
                            color = "white" if live else ("light_gray" if state != "Empty" else "dark_gray")
                        elif live:
                            color = f"L{k}"
                        elif state != "Empty":
                            color = f"L{k}_dim"
                    grid[(i, j)] = color
        return grid

    def snapshot(self):
        with self.lock:
            L, C = self.sel
            layer, clip = self.layer_json(L), self.clip_json(L, C)
            all_slots = self.slots()
            slots, pages = self.page_slots(all_slots)
            rows = []
            for s in slots:
                if s.param is None:
                    rows.append((s.label, "n/a", 0.0, True))
                else:
                    txt, frac = fmt_value(s.param, self.value_of(s.param), s.spec)
                    rows.append((s.label, txt, frac, False))
            touched_path = None
            if self.touched is not None and self.touched < len(slots):
                touched_path = slots[self.touched].path
            mix = []
            for ml in self.mix_layers():
                lj = self.layer_json(ml)
                p = master_param(lj)
                txt, frac = fmt_value(p, self.value_of(p), {}) if p else ("n/a", 0.0)
                mix.append((ml, text(lj.get("name")) or f"Layer {ml}", txt, frac, p is None))
            cp = master_param(self.comp)
            return {
                "mode": self.mode, "mix": mix,
                "comp_master": fmt_value(cp, self.value_of(cp), {}) if cp else None,
                "online": self.online and self.comp is not None,
                "url": self.rest.url,
                "L": L, "C": C,
                "layer_name": text((layer or {}).get("name")),
                "clip_name": text((clip or {}).get("name")),
                "rows": rows, "page": self.page, "pages": pages,
                "shift": self.shift, "touched": self.touched, "touched_path": touched_path,
                "layers": (self.layer_offset + 1, self.layer_offset + 8),
                "cols": (self.col_offset + 1, self.col_offset + 8),
            }


# --------------------------------------------------------------------------- #
# Display (960 x 160)
# --------------------------------------------------------------------------- #

def render(snap, bgr=True):
    """Draw the display. Push 2 wants BGR565; cairo draws RGB565, so with bgr=True
    red and blue are swapped at draw time and the frame needs no conversion."""
    import cairo
    import numpy as np

    W, H = 960, 160
    surf = cairo.ImageSurface(cairo.FORMAT_RGB16_565, W, H)
    ctx = cairo.Context(surf)

    def col(rgb):
        r, g, b = (c / 255 for c in rgb)
        ctx.set_source_rgb(b, g, r) if bgr else ctx.set_source_rgb(r, g, b)

    def font(size, bold=False):
        ctx.select_font_face("Sans", cairo.FONT_SLANT_NORMAL,
                             cairo.FONT_WEIGHT_BOLD if bold else cairo.FONT_WEIGHT_NORMAL)
        ctx.set_font_size(size)

    def fit(s, maxw):
        if ctx.text_extents(s).x_advance <= maxw:
            return s
        while s and ctx.text_extents(s + "…").x_advance > maxw:
            s = s[:-1]
        return s + "…"

    def say(x, y, s, maxw=None, right=False):
        s = fit(s, maxw) if maxw else s
        if right:
            x -= ctx.text_extents(s).x_advance
        ctx.move_to(x, y)
        ctx.show_text(s)

    col((0, 0, 0))
    ctx.paint()

    if not snap["online"]:
        col((255, 255, 255)); font(22, True)
        say(24, 70, f"Waiting for Resolume at {snap['url']}")
        col((150, 150, 150)); font(16)
        say(24, 104, "Arena → Preferences → Webserver → Enable Webserver & REST API")
    elif snap["mode"] == "mix":
        for k in range(8):
            x = k * 120
            if snap["touched"] == k:
                col((45, 45, 45)); ctx.rectangle(x, 0, 120, 98); ctx.fill()
            if k:
                col((35, 35, 35)); ctx.rectangle(x, 6, 1, 86); ctx.fill()
            if k >= len(snap["mix"]):
                continue
            L, name, value, frac, missing = snap["mix"][k]
            accent = LAYER_RGB[(L - 1) % 8]
            col(accent); ctx.rectangle(x + 8, 6, 104, 3); ctx.fill()
            col((255, 90, 90) if missing else (170, 170, 170)); font(15)
            say(x + 8, 28, name, 104)
            col((255, 255, 255)); font(22, True)
            say(x + 8, 60, value, 104)
            col((45, 45, 45)); ctx.rectangle(x + 8, 72, 104, 8); ctx.fill()
            col(accent); ctx.rectangle(x + 8, 72, 104 * frac, 8); ctx.fill()
            col((110, 110, 110)); font(12)
            say(x + 8, 93, f"L{L}")

        col((60, 60, 60)); ctx.rectangle(0, 100, W, 1); ctx.fill()
        col((255, 255, 255)); font(18, True)
        say(24, 137, "MIX")
        if snap["comp_master"]:
            value, frac = snap["comp_master"]
            col((150, 150, 150)); font(14)
            say(300, 124, "COMPOSITION MASTER")
            col((255, 255, 255)); font(20, True)
            say(660, 126, value, right=True)
            col((45, 45, 45)); ctx.rectangle(300, 134, 360, 12); ctx.fill()
            col((255, 255, 255)); ctx.rectangle(300, 134, 360 * frac, 12); ctx.fill()
        col((140, 140, 140)); font(13)
        say(950, 137, "FINE" if snap["shift"] else f"LAYERS {snap['layers'][0]}–{snap['layers'][1]}",
            right=True)
    else:
        accent = LAYER_RGB[(snap["L"] - 1) % 8]
        for k in range(8):
            x = k * 120
            if snap["touched"] == k:
                col((45, 45, 45)); ctx.rectangle(x, 0, 120, 98); ctx.fill()
            if k:
                col((35, 35, 35)); ctx.rectangle(x, 6, 1, 86); ctx.fill()
            if k >= len(snap["rows"]):
                continue
            label, value, frac, missing = snap["rows"][k]
            col((255, 90, 90) if missing else (150, 150, 150)); font(15)
            say(x + 8, 24, label, 104)
            col((255, 255, 255)); font(22, True)
            say(x + 8, 58, value, 104)
            col((45, 45, 45)); ctx.rectangle(x + 8, 72, 104, 8); ctx.fill()
            col(accent); ctx.rectangle(x + 8, 72, 104 * frac, 8); ctx.fill()

        col((60, 60, 60)); ctx.rectangle(0, 100, W, 1); ctx.fill()
        col(accent); ctx.rectangle(10, 110, 8, 42); ctx.fill()
        col((255, 255, 255)); font(18, True)
        say(28, 128, f"L{snap['L']}   {snap['layer_name']}", 560)
        col((200, 200, 200)); font(16)
        say(28, 151, f"C{snap['C']}   {snap['clip_name'] or '—'}", 560)

        col((140, 140, 140)); font(13)
        top = f"PAGE {snap['page'] + 1}/{snap['pages']}" + ("   ·   FINE" if snap["shift"] else "")
        say(950, 127, top, right=True)
        bottom = snap["touched_path"] or (f"LAYERS {snap['layers'][0]}–{snap['layers'][1]}   ·   "
                                          f"COLS {snap['cols'][0]}–{snap['cols'][1]}")
        say(950, 150, bottom, 360, right=True)

    surf.flush()
    stride = surf.get_stride() // 2
    frame = np.ndarray(shape=(H, stride), dtype=np.uint16, buffer=surf.get_data())[:, :W]
    return frame.transpose(), surf


# --------------------------------------------------------------------------- #
# Main loop
# --------------------------------------------------------------------------- #

def apply_palette(push):
    for k, rgb in enumerate(LAYER_RGB):
        push.set_color_palette_entry(PALETTE_BASE + k, f"L{k}", rgb=list(rgb), allow_overwrite=True)
        push.set_color_palette_entry(PALETTE_BASE + 8 + k, f"L{k}_dim",
                                     rgb=[int(c * DIM) for c in rgb], allow_overwrite=True)
    push.reapply_color_palette()


def run(cfg, rest, sim=False):
    if platform.system() == "Darwin":
        # pyusb doesn't search Homebrew's lib folder on its own
        libdirs = [d for d in ("/opt/homebrew/lib", "/usr/local/lib") if os.path.isdir(d)]
        os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = ":".join(
            libdirs + [os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "")])

    import mido
    import push2_python
    from push2_python.constants import FRAME_FORMAT_BGR565

    bridge = Bridge(cfg, rest)
    push = push2_python.Push2(run_simulator=sim)

    @push2_python.on_midi_connected()
    def _midi_connected(push):
        print("[push] MIDI connected")
        bridge.midi_reset = True

    @push2_python.on_display_connected()
    def _display_connected(push):
        print("[push] display connected")

    @push2_python.on_pad_pressed()
    def _pad_down(push, pad_n, pad_ij, velocity):
        bridge.pad_pressed(pad_ij)

    @push2_python.on_pad_released()
    def _pad_up(push, pad_n, pad_ij, velocity):
        bridge.pad_released(pad_ij)

    @push2_python.on_encoder_rotated()
    def _enc_turn(push, name, inc):
        if name in TRACK_ENCODERS:
            bridge.turn(TRACK_ENCODERS.index(name), inc)
        elif name == MASTER_ENCODER:
            bridge.turn_master(inc)

    @push2_python.on_encoder_touched()
    def _enc_touch(push, name):
        if name in TRACK_ENCODERS:
            bridge.touched = TRACK_ENCODERS.index(name)

    @push2_python.on_encoder_released()
    def _enc_release(push, name):
        if name in TRACK_ENCODERS and bridge.touched == TRACK_ENCODERS.index(name):
            bridge.touched = None

    @push2_python.on_button_pressed()
    def _btn_down(push, name):
        bridge.button(name, True)

    @push2_python.on_button_released()
    def _btn_up(push, name):
        bridge.button(name, False)

    threading.Thread(target=bridge.poll_loop, daemon=True).start()
    bridge.sender.start()

    print(f"Bridge running → {rest.url}   (Ctrl+C to quit)")
    if sim:
        print("Simulator: http://localhost:6128")

    pad_cache, btn_cache, palette_ok, display_warned = {}, {}, False, False
    started, midi_warned = time.time(), False
    dt = 1.0 / float(cfg.get("display_fps", 20))
    try:
        while True:
            t0 = time.time()
            # All MIDI output happens here, in the main thread (push2-python/mido requirement).
            if not push.midi_is_configured():
                push.configure_midi()
                if not midi_warned and time.time() - started > 3:
                    print("[push] Push 2 MIDI port not found. Is it powered on and is Ableton Live closed?")
                    print("       MIDI inputs seen:", mido.get_input_names() or "none")
                    midi_warned = True
            if bridge.midi_reset:
                palette_ok, pad_cache, btn_cache, bridge.midi_reset = False, {}, {}, False
            if push.midi_is_configured():
                if not palette_ok:
                    apply_palette(push)
                    for b in NAV_BUTTONS:
                        push.buttons.set_button_color(b, "white")
                    palette_ok = True
                for name, color in bridge.button_colors().items():
                    if btn_cache.get(name) != color:
                        push.buttons.set_button_color(name, color)
                        btn_cache[name] = color
                for ij, color in bridge.pad_colors().items():
                    if pad_cache.get(ij) != color:
                        push.pads.set_pad_color(ij, color)
                        pad_cache[ij] = color
            try:
                frame, _ = render(bridge.snapshot())
                push.display.display_frame(frame, input_format=FRAME_FORMAT_BGR565)
            except Exception as e:  # display issues shouldn't stop pads/encoders
                if not display_warned:
                    print(f"[display] {e}", file=sys.stderr)
                    display_warned = True
            time.sleep(max(0.0, dt - (time.time() - t0)))
    except KeyboardInterrupt:
        print("\nBye.")
        try:
            push.pads.set_all_pads_to_color("black")
        finally:
            push.stop_active_sensing_thread()


def dump(rest, layer_no, col_no=None):
    comp = rest.composition()
    layers = comp.get("layers") or []
    if not 1 <= layer_no <= len(layers):
        sys.exit(f"Composition has {len(layers)} layers.")
    layer = layers[layer_no - 1]

    def show(node):
        for path, p in walk(node):
            vt = p["valuetype"]
            if vt == "ParamRange":
                extra = f"{p.get('min')} … {p.get('max')}"
            elif vt == "ParamChoice":
                extra = ", ".join(map(str, p.get("options") or []))[:60]
            else:
                extra = ""
            print(f"    {path:<58} {vt:<13} {extra}")

    print(f'\nLayer {layer_no} · "{text(layer.get("name"))}"')
    print("  scope: layer")
    show(layer)
    clips = layer.get("clips") or []
    if col_no is None:
        col_no = next((i + 1 for i, c in enumerate(clips) if clip_state(c) != "Empty"), None)
    if col_no and 1 <= col_no <= len(clips):
        print(f'\n  scope: clip   (column {col_no} · "{text(clips[col_no - 1].get("name"))}")')
        show(clips[col_no - 1])
    else:
        print("\n  (no loaded clip on this layer — use --clip N)")
    print("\nCopy a path into config.yaml, e.g.  - {label: Scale, path: <path>, scope: clip}\n")


def load_config(path):
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
    p = Path(path)
    if p.exists():
        user = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        for key, val in user.items():
            if isinstance(val, dict) and isinstance(cfg.get(key), dict) and key != "layers":
                cfg[key].update(val)
            else:
                cfg[key] = val
    for name, spec in (cfg.get("layers") or {}).items():
        if spec != "auto" and not (isinstance(spec, list) and all("path" in s for s in spec)):
            sys.exit(f"config: layers.{name} must be 'auto' or a list of {{label, path}} entries")
    return cfg


def main():
    ap = argparse.ArgumentParser(description="Ableton Push 2 → Resolume Arena bridge")
    ap.add_argument("--config", default=str(Path(__file__).with_name("config.yaml")))
    ap.add_argument("--dump", type=int, metavar="LAYER", help="list parameter paths for a layer and exit")
    ap.add_argument("--clip", type=int, metavar="COLUMN", help="clip column to list with --dump")
    ap.add_argument("--sim", action="store_true", help="run push2-python's browser simulator")
    args = ap.parse_args()

    cfg = load_config(args.config)
    rest = Resolume(cfg["resolume"]["host"], cfg["resolume"]["port"])
    if args.dump:
        dump(rest, args.dump, args.clip)
    else:
        run(cfg, rest, args.sim)


if __name__ == "__main__":
    main()
