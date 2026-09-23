#!/usr/bin/env python3
"""
Push 2 -> Resolume Arena bridge
===============================

Pads      8x8 clip grid. Bottom pad row = lowest visible layer (same as Resolume).
          Press = select layer/clip on the display and in Resolume (no trigger).
          Hold Play + pad = connect clip, release = release (Piano clips work).
          Hold Record + pad = stop (clear) that layer.
Display   Selected layer + clip, and 8 parameter slots (one above each encoder).
Encoders  Track 1-8 edit the 8 slots. Hold Shift for fine steps.
          Master encoder (far right) = selected layer opacity.
Mix       Mix button toggles the mixer: Track 1-8 = layer masters (1 = top visible
          layer), Master encoder = composition master.
Menus     Upper Row 1 = PARAMS view. Lower Row 1-8 = jump to parameter page 1-8.
Color     Upper Row 2 = COLOR view for the selected clip: Track 1-3 = R/G/B, 4-6 = Hue/Sat/
          Brightness, 8 = which colour param. Lower Row 1-8 = that param's palette colours.
Order     Hold Convert + touch a knob, go to any page, touch the target knob -> the two
          params swap. The order is saved per param type in pins.yaml.
Tempo     Tap Tempo = tap BPM, Tempo encoder = BPM +-1 (Shift +-0.1).
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
import colorsys
import json
import os
import platform
import math
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests
import yaml

from display import LAYER_RGB, render  # noqa: F401  (render re-exported for tests/tools)
from resolume_api import (  # noqa: F401
    Resolume, Sender, clip_state, color_label, fmt_value, hex_to_rgba, is_param,
    master_param, resolve_node, rgba_to_hex, text, walk,
)

# push2_python, cairo and numpy are imported inside run()/render() so that
# `--dump` also works on a machine without the Push libraries installed.

TRACK_ENCODERS = [f"Track{i} Encoder" for i in range(1, 9)]
MASTER_ENCODER = "Master Encoder"
NAV_BUTTONS = ["Up", "Down", "Left", "Right", "Page Left", "Page Right"]
MIX_BUTTON = "Mix"                          # B_3 on the control map
PLAY_BUTTON = "Play"                        # B_1: hold + pad = launch clip
STOP_BUTTON = "Record"                      # B_2: hold + pad = stop layer
MOVE_BUTTON = "Convert"                     # B_4: hold + touch knob = move param
TAP_BUTTON = "Tap Tempo"                    # B_5
TEMPO_ENCODER = "Tempo Encoder"             # K10
PARAMS_BUTTON = "Upper Row 1"               # BU1
COLOR_BUTTON = "Upper Row 2"                # BU2
MENU_BUTTONS = {PARAMS_BUTTON: "params", COLOR_BUTTON: "color"}
UPPER_ROW = [f"Upper Row {i}" for i in range(1, 9)]
LOWER_ROW = [f"Lower Row {i}" for i in range(1, 9)]
TEMPO_PATH = "tempocontroller/tempo"
TAP_RESET = 2.0       # s without a tap starts a new tap count
TAP_FLASH = 0.1       # s the Tap Tempo button stays lit per tap
DIM = 0.15            # brightness of loaded-but-not-playing pads
PALETTE_BASE = 64     # Push palette slots 64..79 are overwritten with the layer colours
SWATCH_BASE = 80      # slots 80..87 = the COLOR menu's palette swatches on Lower Row 1-8
COLOR_SOURCES = [("clip", "video"), ("layer", "video/effects")]   # where colour params are searched
# COLOR menu knobs: (label, kind, coarse step, fine step). kind r/g/b 0..255, h 0..360, s/v 0..100
COLOR_KNOBS = [("Red", "r", 3, 1), ("Green", "g", 3, 1), ("Blue", "b", 3, 1),
               ("Hue", "h", 2, 0.5), ("Saturation", "s", 1, 0.2), ("Brightness", "v", 1, 0.2)]

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
    "stop_column": None,   # None = stop via /clear; N = trigger column N instead
    "pins_file": None,     # param order file; None = pins.yaml next to this script
    "layers": {"default": "auto"},
}



# --------------------------------------------------------------------------- #
# Bridge state
# --------------------------------------------------------------------------- #

@dataclass
class Slot:
    label: str
    path: str            # "layer:video/opacity" — shown when the encoder is touched
    param: dict | None   # None = path not found
    spec: dict = field(default_factory=dict)

    @property
    def key(self):
        """Path without scope: the same param type on any layer/clip has the same key."""
        return self.path.split(":", 1)[-1].lower()


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
        self.mode = "params"       # "params", "color" or "mix"
        self.color_idx = 0         # which colour param the COLOR menu edits
        self.hsv_cache = {}        # param id -> (rgb, [h, s, v]) keeps hue while saturation is 0
        self.page = 0
        self.shift = False
        self.play_held = False     # B_1
        self.stop_held = False     # B_2
        self.stop_column = cfg.get("stop_column")
        self.convert_held = False  # B_4
        self.move_src = None       # absolute slot index being moved
        self.taps = []
        self.tap_flash = 0.0
        self.pins_path = Path(cfg.get("pins_file") or Path(__file__).with_name("pins.yaml"))
        self.order = self._load_order()
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

    def _load_order(self):
        try:
            data = yaml.safe_load(self.pins_path.read_text(encoding="utf-8")) or {}
            return [str(k).lower() for k in data.get("order") or []]
        except FileNotFoundError:
            return []
        except Exception as e:
            print(f"[pins] can't read {self.pins_path}: {e}", file=sys.stderr)
            return []

    def _save_order(self):
        try:
            self.pins_path.write_text(
                "# Param order for auto layers (first = K1 on page 1). Written by the bridge;\n"
                "# safe to edit or delete.\n" + yaml.safe_dump({"order": self.order}, sort_keys=False),
                encoding="utf-8")
        except Exception as e:
            print(f"[pins] can't write {self.pins_path}: {e}", file=sys.stderr)

    def swap(self, a, b):
        """Swap two auto slots (absolute indices) and remember the new order."""
        keys = [s.key for s in self.slots()]
        if not (0 <= a < len(keys) and 0 <= b < len(keys)) or keys[a] == keys[b]:
            return
        keys[a], keys[b] = keys[b], keys[a]
        prefix = list(dict.fromkeys(keys[:max(a, b) + 1]))
        self.order = prefix + [k for k in self.order if k not in prefix]
        self._save_order()

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
            rank = {k: i for i, k in enumerate(self.order)}
            out.sort(key=lambda sl: rank.get(sl.key, len(rank)))   # stable: rest keeps natural order
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
            if self.mode == "color":
                self._turn_color(idx, inc)
                return
            if self.move_src is not None:
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

    def color_params(self):
        """[(label, param)] colour params of the selected clip, then of its layer's effects."""
        L, C = self.sel
        roots = {"layer": self.layer_json(L), "clip": self.clip_json(L, C)}
        out, seen = [], set()
        for scope, path in COLOR_SOURCES:
            node = resolve_node(roots[scope], path) if roots[scope] else None
            for p, param in (walk(node, path, types={"ParamColor"}) if node is not None else []):
                if param["id"] not in seen:
                    seen.add(param["id"])
                    out.append((("Layer " if scope == "layer" else "") + color_label(p), param))
        return out

    def color_param(self):
        cps = self.color_params()
        if not cps:
            return None, None, 0
        self.color_idx = min(self.color_idx, len(cps) - 1)
        label, p = cps[self.color_idx]
        return label, p, len(cps)

    def color_hsv(self, p):
        rgb = hex_to_rgba(self.value_of(p))[:3]
        cached = self.hsv_cache.get(p["id"])
        if cached and cached[0] == rgb:
            return list(cached[1])
        h, sat, v = colorsys.rgb_to_hsv(*(c / 255 for c in rgb))
        return [h * 360, sat * 100, v * 100]

    def _turn_color(self, idx, inc):
        if idx == 7:                                   # K8: which colour param
            acc = self.choice_acc.get("color", 0) + inc
            n = len(self.color_params())
            if abs(acc) < 4 or not n:
                self.choice_acc["color"] = acc
                return
            self.choice_acc["color"] = 0
            self.color_idx = min(n - 1, max(0, self.color_idx + (1 if acc > 0 else -1)))
            return
        _, p, _ = self.color_param()
        if p is None or idx >= len(COLOR_KNOBS):
            return
        _, kind, coarse, fine = COLOR_KNOBS[idx]
        step = inc * (fine if self.shift else coarse)
        rgba = hex_to_rgba(self.value_of(p))
        if kind in "rgb":
            i = "rgb".index(kind)
            rgba[i] = int(min(255, max(0, rgba[i] + step)))
            self.hsv_cache.pop(p["id"], None)
        else:
            hsv = self.color_hsv(p)
            i = "hsv".index(kind)
            hsv[i] = (hsv[i] + step) % 360 if kind == "h" else min(100.0, max(0.0, hsv[i] + step))
            rgb = [round(c * 255) for c in colorsys.hsv_to_rgb(hsv[0] / 360, hsv[1] / 100, hsv[2] / 100)]
            rgba[:3] = rgb
            self.hsv_cache[p["id"]] = (rgb, hsv)
        value = rgba_to_hex(rgba)
        self._set(p["id"], value, {"value": value})

    def set_palette_color(self, k):
        _, p, _ = self.color_param()
        palette = (p or {}).get("palette") or []
        if k < len(palette):
            self.hsv_cache.pop(p["id"], None)
            self._set(p["id"], palette[k], {"value": palette[k]})

    def swatches(self):
        """RGB of the Lower Row buttons in the COLOR menu (the param's palette)."""
        with self.lock:
            if self.mode != "color":
                return []
            _, p, _ = self.color_param()
            return [hex_to_rgba(c)[:3] for c in ((p or {}).get("palette") or [])[:8]]

    def tempo_param(self):
        p = resolve_node(self.comp, TEMPO_PATH) if self.comp else None
        return p if is_param(p) else None

    def _set_bpm(self, p, bpm):
        bpm = round(min(p.get("max", 500.0), max(p.get("min", 20.0), bpm)), 2)
        self._set(p["id"], bpm, {"value": bpm})

    def turn_tempo(self, inc):
        with self.lock:
            p = self.tempo_param()
            if p:
                self._set_bpm(p, float(self.value_of(p) or 120.0) + inc * (0.1 if self.shift else 1.0))

    def tap(self):
        now = time.time()
        with self.lock:
            self.tap_flash = now
            if self.taps and now - self.taps[-1] > TAP_RESET:
                self.taps = []
            self.taps = (self.taps + [now])[-8:]
            p = self.tempo_param()
            if p and len(self.taps) >= 2:
                self._set_bpm(p, 60.0 * (len(self.taps) - 1) / (self.taps[-1] - self.taps[0]))

    def touch(self, idx):
        with self.lock:
            self.touched = idx
            if self.mode != "params":
                return
            n = len(self.slots())
            i = self.page * 8 + idx
            if self.move_src is None:
                if self.convert_held and i < n and self.layer_spec(self.sel[0]) == "auto":
                    self.move_src = i
            elif i == self.move_src:
                self.move_src = None               # touched the source again = cancel
            elif i < n:
                self.swap(self.move_src, i)
                self.move_src = None

    def untouch(self, idx):
        if self.touched == idx:
            self.touched = None

    def pad_to_cell(self, i, j):
        return self.layer_offset + (8 - i), self.col_offset + j + 1

    def pad_pressed(self, ij):
        L, C = self.pad_to_cell(*ij)
        with self.lock:
            clip = self.clip_json(L, C)
            if clip is None:
                return
            if self.stop_held:
                if self.stop_column:
                    self.sender.trigger(L, int(self.stop_column), True)
                    self.sender.trigger(L, int(self.stop_column), False)
                else:
                    self.sender.clear(L)
                return
            if L != self.sel[0]:
                self.page = 0
            if (L, C) != self.sel:
                self.move_src = None
                self.color_idx = 0
            self.sel = (L, C)
            self.sender.select(L, C)               # show it in Resolume's clip panel too
            if not self.play_held or clip_state(clip) == "Empty":
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
        if name == PLAY_BUTTON:
            self.play_held = down
            return
        if name == STOP_BUTTON:
            self.stop_held = down
            return
        if name == MOVE_BUTTON:
            self.convert_held = down
            if down and self.move_src is not None:
                self.move_src = None               # Convert again = cancel
            return
        if not down:
            return
        if name == TAP_BUTTON:
            self.tap()
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
                self.move_src = None
            elif name in MENU_BUTTONS:
                self.mode = MENU_BUTTONS[name]
                self.move_src = None
            elif name in LOWER_ROW and self.mode == "color":
                self.set_palette_color(LOWER_ROW.index(name))
            elif name in LOWER_ROW and self.mode == "params":
                _, pages = self.page_slots()
                k = LOWER_ROW.index(name)
                if k < pages:
                    self.page = k

    # ---- output state ----------------------------------------------------- #
    def button_colors(self):
        with self.lock:
            params = self.mode == "params"
            pages = self.page_slots()[1] if params else 0
            out = {MIX_BUTTON: "dark_gray" if params else "white",
                   PLAY_BUTTON: "green" if self.play_held else "dark_gray",
                   STOP_BUTTON: "red" if self.stop_held else "dark_gray",
                   MOVE_BUTTON: "white" if self.move_src is not None or self.convert_held else "dark_gray",
                   TAP_BUTTON: "white" if time.time() - self.tap_flash < TAP_FLASH else "dark_gray"}
            for b in UPPER_ROW:
                out[b] = "black"
            for b, m in MENU_BUTTONS.items():
                out[b] = "white" if self.mode == m else "dark_gray"
            n_sw = len(self.swatches())
            for k, b in enumerate(LOWER_ROW):
                if self.mode == "color":
                    out[b] = f"P{k}" if k < n_sw else "black"
                else:
                    out[b] = "black" if k >= pages else ("white" if k == self.page else "dark_gray")
            return out

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
            tp = self.tempo_param()
            move = None
            if self.move_src is not None and self.move_src < len(all_slots):
                src_page, src_col = divmod(self.move_src, 8)
                move = {"label": all_slots[self.move_src].label, "page": src_page, "col": src_col,
                        "here": src_page == self.page}
            color = None
            if self.mode == "color":
                label, p, n = self.color_param()
                if p is not None:
                    rgba = hex_to_rgba(self.value_of(p))
                    hsv = self.color_hsv(p)
                    vals = rgba[:3] + hsv
                    color = {"label": label, "idx": self.color_idx, "n": n, "rgb": rgba[:3],
                             "hex": rgba_to_hex(rgba)[:7].upper(),
                             "knobs": [(lbl, vals[i]) for i, (lbl, *_rest) in enumerate(COLOR_KNOBS)]}
            return {
                "color": color,
                "bpm": f"{float(self.value_of(tp) or 0):.1f} BPM" if tp else "",
                "move": move,
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
        elif name == TEMPO_ENCODER:
            bridge.turn_tempo(inc)

    @push2_python.on_encoder_touched()
    def _enc_touch(push, name):
        if name in TRACK_ENCODERS:
            bridge.touch(TRACK_ENCODERS.index(name))

    @push2_python.on_encoder_released()
    def _enc_release(push, name):
        if name in TRACK_ENCODERS:
            bridge.untouch(TRACK_ENCODERS.index(name))

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
    swatch_cache = None
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
                swatch_cache = None
            if push.midi_is_configured():
                if not palette_ok:
                    apply_palette(push)
                    for b in NAV_BUTTONS:
                        push.buttons.set_button_color(b, "white")
                    palette_ok = True
                swatches = bridge.swatches()
                if swatches and swatches != swatch_cache:     # palette colours of the COLOR menu
                    for k, rgb in enumerate(swatches):
                        push.set_color_palette_entry(SWATCH_BASE + k, f"P{k}", rgb=rgb, allow_overwrite=True)
                    push.reapply_color_palette()
                    swatch_cache = swatches
                    for b in LOWER_ROW:
                        btn_cache.pop(b, None)
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
