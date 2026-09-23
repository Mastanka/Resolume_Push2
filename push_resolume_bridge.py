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
Tempo     Tap Tempo = Resolume's own tap, Shift + Tap Tempo = resync (beat 1 now).
          Tempo encoder = BPM +-1 (Shift +-0.1). Tap Tempo, the display and playing pads
          blink on the beat; Metronome turns the pad pulse on / off.
FX        Upper Row 3 = effects of the selected clip, its layer and the composition:
          Track 1-8 = effect amount (Opacity), Lower Row = effect on / off, Page < > = more.
Master    Master button = COLOR on the composition's colour effect (Colorize): K7 = amount,
          K8 = on/off. Press again = back to the clip.
          COLOR: Shift + Lower Row = save the current colour there (colors.yaml).
          Hold Duplicate: pad / button right of a row / Lower Row = paste the colour to that
          clip / whole layer / whole column.
Live      Stop Clip = blackout (composition master 0 / back). Buttons right of the pads = flash
          that row's layer to 100 % while held. Play + Lower Row = launch the column above.
          Mute / Solo + pad = mute / solo that layer; in MIX the Lower Row mutes (Solo held = solo).
Buttons   Up/Down scroll layers, Left/Right scroll columns (Shift = jump by 8).
          Page < / Page > flip parameter pages when a layer has more than 8 slots.

Talks to Resolume through its REST API:
    Arena -> Preferences -> Webserver -> Enable Webserver & REST API

Usage
    python push_resolume_bridge.py                 run the bridge
    python push_resolume_bridge.py --sim           run with push2-python's browser simulator
    python push_resolume_bridge.py --dump 3        list parameter paths for layer 3 (for config.yaml)
    python push_resolume_bridge.py --dump 3 --clip 2
    python push_resolume_bridge.py --check 8       test every Resolume call on layer 8 (undone after)
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
    Resolume, Sender, clip_state, color_label, fmt_value, hex_to_rgba, is_param, label_of,
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
BLACKOUT_BUTTON = "Stop"                    # "Stop Clip": composition master 0 / back
MUTE_BUTTON = "Mute"                        # hold + pad = mute (bypass) that layer
SOLO_BUTTON = "Solo"                        # hold + pad = solo that layer
SCENE_BUTTONS = ["1/32t", "1/32", "1/16t", "1/16", "1/8t", "1/8", "1/4t", "1/4"]  # = pad rows, top first
BLINK = 0.25          # s on / off for blinking LEDs
METRONOME_BUTTON = "Metronome"              # pad pulse on / off
TAP_PATH = "tempocontroller/tempo_tap"
RESYNC_PATH = "tempocontroller/resync"
BEAT_ON = 0.12        # s a beat stays lit (Tap Tempo button, pad pulse)
LED_HZ = 50           # LED updates per second (beat edges); the display runs at display_fps
MASTER_COLOR_BUTTON = "Master"              # COLOR on the composition's colour effect
PASTE_BUTTON = "Duplicate"                  # COLOR: hold + pad / row / column = paste colour
NOTE_TIME = 1.8       # s a short message stays on the display
PARAMS_BUTTON = "Upper Row 1"               # BU1
COLOR_BUTTON = "Upper Row 2"                # BU2
FX_BUTTON = "Upper Row 3"                   # BU3
MENU_BUTTONS = {PARAMS_BUTTON: "params", COLOR_BUTTON: "color", FX_BUTTON: "fx"}
FX_SOURCES = [("Clip", "clip"), ("Layer", "layer"), ("Comp", "comp")]   # FX menu order
UPPER_ROW = [f"Upper Row {i}" for i in range(1, 9)]
LOWER_ROW = [f"Lower Row {i}" for i in range(1, 9)]
TEMPO_PATH = "tempocontroller/tempo"
TAP_RESET = 2.0       # s without a tap starts a new tap count
TAP_FLASH = 0.1       # s the Tap Tempo button stays lit per tap
DIM = 0.15            # brightness of loaded-but-not-playing pads
MID = 0.45            # brightness of playing pads between beats (pulse)
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
    "colors_file": None,   # own palette (Shift + Lower Row in COLOR); None = colors.yaml here
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
        self.color_target = "clip"  # "clip" or "master" (composition colour effect)
        self.paste_held = False    # Duplicate
        self.note = ("", 0.0)      # short message on the display (text, time)
        self.colors_path = Path(cfg.get("colors_file") or Path(__file__).with_name("colors.yaml"))
        self.own_palette = self._load_palette()
        self.hsv_cache = {}        # param id -> (rgb, [h, s, v]) keeps hue while saturation is 0
        self.page = 0
        self.fx_page = 0
        self.shift = False
        self.play_held = False     # B_1
        self.stop_held = False     # B_2
        self.stop_column = cfg.get("stop_column")
        self.convert_held = False  # B_4
        self.mute_held = False
        self.solo_held = False
        self.blackout = None       # composition master before blackout, None = not blacked out
        self.blackout_t = 0.0
        self.flash = {}            # layer -> master value before the flash button was pressed
        self.col_pressed = set()   # columns we sent a "down" for
        self.move_src = None       # absolute slot index being moved
        self.taps = []
        self.tap_flash = 0.0
        self.tap_count = 0
        self.beat_anchor = (time.time(), 0)   # (time of a known beat, its index in the bar 0-3)
        self.pulse = True          # playing pads pulse on the beat (Metronome toggles)
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
                    self._check_blackout()
            except Exception as e:
                if self.online or not getattr(self, "_warned_offline", False):
                    print(f"[resolume] not reachable at {self.rest.url} ({type(e).__name__})")
                    self._warned_offline = True
                with self.lock:
                    self.online = False
            time.sleep(self.poll_interval)

    def _check_blackout(self):
        """Master raised by someone else (Launch Control, mouse) = no longer blacked out."""
        p = master_param(self.comp)
        if self.blackout is not None and p and time.time() - self.blackout_t > 1.0 \
                and float(self.value_of(p) or 0) > 0.01:
            self.blackout = None

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
            if self.mode == "fx":
                items, _ = self.fx_page_items()
                if idx < len(items) and items[idx][3]:
                    self._nudge(items[idx][3], {}, inc)
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
                self.blackout = None
            else:
                p = resolve_node(self.layer_json(self.sel[0]), "video/opacity")
            if is_param(p):
                self._nudge(p, {}, inc)

    def color_params(self):
        """[(label, param)] colour params of the selected clip, then of its layer's effects.
        With the master target: the composition's effects (e.g. Colorize)."""
        if self.color_target == "master":
            node = resolve_node(self.comp, "video/effects") if self.comp else None
            return [(color_label(p), prm) for p, prm in
                    (walk(node, "video/effects", types={"ParamColor"}) if node is not None else [])]
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

    def master_effect(self, p):
        """The composition effect that owns colour param p: (effect, amount param, bypassed param)."""
        for fx in resolve_node(self.comp, "video/effects") or []:
            params = fx.get("params") or {}
            if any(isinstance(v, dict) and v.get("id") == p["id"] for v in params.values()):
                amount = next((v for k, v in params.items() if k.lower() == "opacity" and is_param(v)), None)
                byp = fx.get("bypassed")
                return fx, amount, byp if is_param(byp) else None
        return None, None, None

    def note_msg(self, msg):
        self.note = (msg, time.time())

    # ---- own palette (colors.yaml) ------------------------------------------ #
    def _load_palette(self):
        try:
            data = yaml.safe_load(self.colors_path.read_text(encoding="utf-8")) or {}
            pal = [str(c) for c in data.get("palette") or []][:8]
            return pal or None
        except FileNotFoundError:
            return None
        except Exception as e:
            print(f"[colors] can't read {self.colors_path}: {e}", file=sys.stderr)
            return None

    def palette(self):
        """Own palette if saved, otherwise Resolume's palette of the current colour param."""
        if self.own_palette:
            return self.own_palette
        _, p, _ = self.color_param()
        return ((p or {}).get("palette") or [])[:8]

    def save_palette_color(self, k):
        _, p, _ = self.color_param()
        if p is None:
            return
        pal = (list(self.palette()) + ["#000000ff"] * 8)[:8]
        pal[k] = self.value_of(p)
        self.own_palette = pal
        try:
            self.colors_path.write_text(
                "# Own colours for the COLOR menu (Shift + button below the display saves one).\n"
                "# Written by the bridge; safe to edit or delete (= back to Resolume's palette).\n"
                + yaml.safe_dump({"palette": pal}, sort_keys=False), encoding="utf-8")
            self.note_msg(f"Saved to palette slot {k + 1}")
        except Exception as e:
            print(f"[colors] can't write {self.colors_path}: {e}", file=sys.stderr)

    def paste_color(self, cells):
        """Write the current colour into the same-named colour param of each clip."""
        label, p, _ = self.color_param()
        if p is None:
            return
        if label.startswith("Layer "):
            self.note_msg("Only clip colours can be pasted")
            return
        value, done, skipped = self.value_of(p), 0, 0
        for L, C in cells:
            clip = self.clip_json(L, C)
            if clip is None or clip_state(clip) == "Empty":
                continue
            target = next((prm for path, prm in walk(clip.get("video") or {}, "video", types={"ParamColor"})
                           if color_label(path) == label), None)
            if target is None:
                skipped += 1
            elif target["id"] != p["id"]:
                self._set(target["id"], value, {"value": value})
                done += 1
        self.note_msg(f"{label} → {done} clip{'s' * (done != 1)}" + (f"  ({skipped} skipped)" if skipped else ""))

    # ---- FX menu ----------------------------------------------------------- #
    def fx_list(self):
        """[(tag, name, bypassed param or None, amount param or None)] clip, layer, composition effects."""
        L, C = self.sel
        roots = {"clip": self.clip_json(L, C), "layer": self.layer_json(L), "comp": self.comp}
        out = []
        for tag, scope in FX_SOURCES:
            for fx in resolve_node(roots[scope], "video/effects") or [] if roots[scope] else []:
                if not isinstance(fx, dict):
                    continue
                params = fx.get("params") or {}
                amount = next((v for k, v in params.items() if k.lower() == "opacity" and is_param(v)), None)
                byp = fx.get("bypassed")
                out.append((tag, label_of(fx) or "Effect", byp if is_param(byp) else None, amount))
        return out

    def fx_page_items(self):
        items = self.fx_list()
        pages = max(1, math.ceil(len(items) / 8))
        self.fx_page = min(self.fx_page, pages - 1)
        return items[self.fx_page * 8:(self.fx_page + 1) * 8], pages

    def color_hsv(self, p):
        rgb = hex_to_rgba(self.value_of(p))[:3]
        cached = self.hsv_cache.get(p["id"])
        if cached and cached[0] == rgb:
            return list(cached[1])
        h, sat, v = colorsys.rgb_to_hsv(*(c / 255 for c in rgb))
        return [h * 360, sat * 100, v * 100]

    def _turn_color(self, idx, inc):
        if self.color_target == "master" and idx in (6, 7):
            _, p, _ = self.color_param()
            _, amount, byp = self.master_effect(p) if p else (None, None, None)
            if idx == 6 and amount:                    # K7: effect amount
                self._nudge(amount, {}, inc)
            elif idx == 7 and byp:                     # K8: effect on (right) / off (left)
                self._set(byp["id"], inc < 0, {"value": inc < 0})
            return
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
        palette = self.palette()
        if p is not None and k < len(palette):
            self.hsv_cache.pop(p["id"], None)
            self._set(p["id"], palette[k], {"value": palette[k]})

    def swatches(self):
        """RGB of the Lower Row buttons in the COLOR menu (the param's palette)."""
        with self.lock:
            if self.mode != "color":
                return []
            return [hex_to_rgba(c)[:3] for c in self.palette()]

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
        """Every tap is a beat; the first tap of a sequence is beat 1 of the bar.
        Resolume's own tap sets its BPM and phase; without it we compute the BPM here."""
        now = time.time()
        with self.lock:
            self.tap_flash = now
            if self.taps and now - self.taps[-1] > TAP_RESET:
                self.taps = []
            self.taps = (self.taps + [now])[-8:]
            self.tap_count = 1 if len(self.taps) == 1 else self.tap_count + 1
            self.beat_anchor = (now, (self.tap_count - 1) % 4)
            ev = resolve_node(self.comp, TAP_PATH) if self.comp else None
            if is_param(ev):
                self.sender.event(ev["id"])
                return
            p = self.tempo_param()
            if p and len(self.taps) >= 2:
                self._set_bpm(p, 60.0 * (len(self.taps) - 1) / (self.taps[-1] - self.taps[0]))

    def resync(self):
        with self.lock:
            self.beat_anchor, self.taps, self.tap_flash = (time.time(), 0), [], time.time()
            ev = resolve_node(self.comp, RESYNC_PATH) if self.comp else None
            if is_param(ev):
                self.sender.event(ev["id"])

    def beat(self, now=None):
        """(beat index in the bar 0-3, seconds since that beat) or None without a tempo."""
        p = self.tempo_param()
        bpm = float(self.value_of(p) or 0) if p else 0
        if bpm <= 0:
            return None
        now = now or time.time()
        period = 60.0 / bpm
        t0, b0 = self.beat_anchor
        n = math.floor((now - t0) / period)
        return (b0 + n) % 4, now - t0 - n * period

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

    # ---- live controls: blackout, flash, mute / solo, columns ------------------ #
    def toggle_blackout(self):
        with self.lock:
            p = master_param(self.comp)
            if p is None:
                return
            if self.blackout is None:
                self.blackout, self.blackout_t = float(self.value_of(p) or 0), time.time()
                self._set(p["id"], 0.0, {"value": 0.0})
            else:
                self._set(p["id"], self.blackout, {"value": self.blackout})
                self.blackout = None

    def flash_layer(self, row, down):
        L = self.layer_offset + (8 - row)
        with self.lock:
            p = master_param(self.layer_json(L))
            if p is None:
                return
            if down and L not in self.flash:
                self.flash[L] = float(self.value_of(p) or 0)
                self._set(p["id"], 1.0, {"value": 1.0})
            elif not down and L in self.flash:
                v = self.flash.pop(L)
                self._set(p["id"], v, {"value": v})

    def layer_flag(self, L, key):
        """layer.bypassed / layer.solo param (or None)."""
        p = (self.layer_json(L) or {}).get(key)
        return p if is_param(p) else None

    def toggle_layer(self, L, key):
        p = self.layer_flag(L, key)
        if p:
            v = not bool(self.value_of(p))
            self._set(p["id"], v, {"value": v})

    def layer_hidden(self, L):
        """Muted, or another layer is soloed."""
        mute = self.layer_flag(L, "bypassed")
        if mute and self.value_of(mute):
            return True
        solos = [l for l in range(1, len(self.layers()) + 1)
                 if self.layer_flag(l, "solo") and self.value_of(self.layer_flag(l, "solo"))]
        return bool(solos) and L not in solos

    def column_state(self, n):
        cols = (self.comp or {}).get("columns") or []
        return text(cols[n - 1].get("connected"), "Empty") if 1 <= n <= len(cols) else "Empty"

    def pad_to_cell(self, i, j):
        return self.layer_offset + (8 - i), self.col_offset + j + 1

    def pad_pressed(self, ij):
        L, C = self.pad_to_cell(*ij)
        with self.lock:
            clip = self.clip_json(L, C)
            if clip is None:
                return
            if self.mute_held or self.solo_held:
                self.toggle_layer(L, "bypassed" if self.mute_held else "solo")
                return
            if self.paste_held and self.mode == "color":
                self.paste_color([(L, C)])
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
                self.fx_page = 0
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
        if name == MUTE_BUTTON:
            self.mute_held = down
            return
        if name == SOLO_BUTTON:
            self.solo_held = down
            return
        if name == PASTE_BUTTON:
            self.paste_held = down
            return
        if self.paste_held and self.mode == "color" and name in SCENE_BUTTONS + LOWER_ROW:
            if down:
                with self.lock:
                    if name in SCENE_BUTTONS:          # whole layer of that pad row
                        L = self.layer_offset + (8 - SCENE_BUTTONS.index(name))
                        cells = [(L, c) for c in range(1, len((self.layer_json(L) or {}).get("clips") or []) + 1)]
                    else:                              # whole column above that button
                        C = self.col_offset + LOWER_ROW.index(name) + 1
                        cells = [(l, C) for l in range(1, len(self.layers()) + 1)]
                    self.paste_color(cells)
            return
        if name in SCENE_BUTTONS:
            self.flash_layer(SCENE_BUTTONS.index(name), down)
            return
        if name in LOWER_ROW and (self.play_held or not down):
            n = self.col_offset + LOWER_ROW.index(name) + 1
            if down:
                self.col_pressed.add(n)
                self.sender.column(n, True)
                return
            if n in self.col_pressed:
                self.col_pressed.discard(n)
                self.sender.column(n, False)
            return
        if name == MOVE_BUTTON:
            self.convert_held = down
            if down and self.move_src is not None:
                self.move_src = None               # Convert again = cancel
            return
        if not down:
            return
        if name == TAP_BUTTON:
            self.resync() if self.shift else self.tap()
            return
        if name == METRONOME_BUTTON:
            self.pulse = not self.pulse
            return
        if name == BLACKOUT_BUTTON:
            self.toggle_blackout()
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
            elif name == "Page Right" and self.mode == "fx":
                _, pages = self.fx_page_items()
                self.fx_page = min(pages - 1, self.fx_page + 1)
            elif name == "Page Left" and self.mode == "fx":
                self.fx_page = max(0, self.fx_page - 1)
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
                self.color_target = "clip"
            elif name == MASTER_COLOR_BUTTON:
                master = self.mode == "color" and self.color_target == "master"
                self.mode, self.color_target, self.color_idx = "color", "clip" if master else "master", 0
                self.move_src = None
            elif name in LOWER_ROW and self.mode == "fx":
                items, _ = self.fx_page_items()
                k = LOWER_ROW.index(name)
                if k < len(items) and items[k][2]:
                    byp = items[k][2]
                    v = not bool(self.value_of(byp))
                    self._set(byp["id"], v, {"value": v})
            elif name in LOWER_ROW and self.mode == "color" and self.shift:
                self.save_palette_color(LOWER_ROW.index(name))
            elif name in LOWER_ROW and self.mode == "mix":
                layers = self.mix_layers()
                k = LOWER_ROW.index(name)
                if k < len(layers):
                    self.toggle_layer(layers[k], "solo" if self.solo_held else "bypassed")
            elif name in LOWER_ROW and self.mode == "color":
                self.set_palette_color(LOWER_ROW.index(name))
            elif name in LOWER_ROW and self.mode == "params":
                _, pages = self.page_slots()
                k = LOWER_ROW.index(name)
                if k < pages:
                    self.page = k

    # ---- output state ----------------------------------------------------- #
    def on_beat(self):
        b = self.beat()
        return b is not None and b[1] < BEAT_ON

    def button_colors(self):
        with self.lock:
            params = self.mode == "params"
            pages = self.page_slots()[1] if params else 0
            out = {MIX_BUTTON: "dark_gray" if params else "white",
                   PLAY_BUTTON: "green" if self.play_held else "dark_gray",
                   STOP_BUTTON: "red" if self.stop_held else "dark_gray",
                   MOVE_BUTTON: "white" if self.move_src is not None or self.convert_held else "dark_gray",
                   TAP_BUTTON: "white" if self.on_beat() or time.time() - self.tap_flash < TAP_FLASH
                   else "dark_gray",
                   METRONOME_BUTTON: "white" if self.pulse else "dark_gray",
                   MASTER_COLOR_BUTTON: "white" if self.mode == "color" and self.color_target == "master"
                   else "dark_gray",
                   PASTE_BUTTON: ("white" if self.paste_held else "dark_gray") if self.mode == "color"
                   else "black"}
            for b in UPPER_ROW:
                out[b] = "black"
            for b, m in MENU_BUTTONS.items():
                active = self.mode == m and not (m == "color" and self.color_target == "master")
                out[b] = "white" if active else "dark_gray"
            n_sw = len(self.swatches())
            blink = int(time.time() / BLINK) % 2 == 0
            mix_layers = self.mix_layers()
            fx_items = self.fx_page_items()[0] if self.mode == "fx" else []
            for k, b in enumerate(LOWER_ROW):
                if self.play_held:                          # column launch view
                    st = self.column_state(self.col_offset + k + 1)
                    out[b] = {"Connected": "green", "Disconnected": "dark_gray"}.get(st, "black")
                elif self.mode == "color":
                    out[b] = f"P{k}" if k < n_sw else "black"
                elif self.mode == "fx":
                    byp = fx_items[k][2] if k < len(fx_items) else None
                    out[b] = "black" if byp is None else ("dark_gray" if self.value_of(byp) else "white")
                elif self.mode == "mix":
                    if k >= len(mix_layers):
                        out[b] = "black"
                        continue
                    L = mix_layers[k]
                    solo, mute = self.layer_flag(L, "solo"), self.layer_flag(L, "bypassed")
                    if self.solo_held:
                        out[b] = "yellow" if solo and self.value_of(solo) else "dark_gray"
                    elif mute and self.value_of(mute):
                        out[b] = "red"
                    else:
                        out[b] = "yellow" if solo and self.value_of(solo) else f"L{(L - 1) % 8}"
                else:
                    out[b] = "black" if k >= pages else ("white" if k == self.page else "dark_gray")
            for i, b in enumerate(SCENE_BUTTONS):
                L = self.layer_offset + (8 - i)
                out[b] = ("black" if self.layer_json(L) is None
                          else f"L{(L - 1) % 8}" if L in self.flash else f"L{(L - 1) % 8}_dim")
            all_l = range(1, len(self.layers()) + 1)
            any_mute = any(self.layer_flag(l, "bypassed") and self.value_of(self.layer_flag(l, "bypassed"))
                           for l in all_l)
            any_solo = any(self.layer_flag(l, "solo") and self.value_of(self.layer_flag(l, "solo"))
                           for l in all_l)
            out[MUTE_BUTTON] = "white" if self.mute_held else ("red" if any_mute else "dark_gray")
            out[SOLO_BUTTON] = "white" if self.solo_held else ("yellow" if any_solo else "dark_gray")
            out[BLACKOUT_BUTTON] = ("red" if blink else "black") if self.blackout is not None else "dark_gray"
            return out

    def pad_colors(self):
        grid = {}
        with self.lock:
            dim_live = self.pulse and self.online and not self.on_beat()
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
                        elif state != "Empty" and self.layer_hidden(L):   # muted / not soloed
                            color = "light_gray" if live else "dark_gray"
                        elif live:
                            color = f"L{k}_mid" if dim_live else f"L{k}"
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
                mute, solo = self.layer_flag(ml, "bypassed"), self.layer_flag(ml, "solo")
                mix.append((ml, text(lj.get("name")) or f"Layer {ml}", txt, frac, p is None,
                            bool(mute and self.value_of(mute)), bool(solo and self.value_of(solo))))
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
                    if self.color_target == "master":
                        fx, amount, byp = self.master_effect(p)
                        color["fx"] = text((fx or {}).get("display_name")) or text((fx or {}).get("name"))
                        color["amount"] = fmt_value(amount, self.value_of(amount), {}) if amount else None
                        color["enabled"] = None if byp is None else not self.value_of(byp)
            b = self.beat()
            note = self.note[0] if time.time() - self.note[1] < NOTE_TIME else ""
            fx = None
            if self.mode == "fx":
                items, fx_pages = self.fx_page_items()
                fx = {"page": self.fx_page, "pages": fx_pages, "items": [
                    {"tag": tag, "name": name,
                     "on": None if byp is None else not self.value_of(byp),
                     "amount": fmt_value(amt, self.value_of(amt), {}) if amt else None}
                    for tag, name, byp, amt in items]}
            return {
                "fx": fx,
                "master_color": self.color_target == "master",
                "paste": self.paste_held and self.mode == "color",
                "note": note,
                "beat": None if b is None else (b[0], b[1] < BEAT_ON),
                "blackout": self.blackout is not None,
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
        push.set_color_palette_entry(PALETTE_BASE + 24 + k, f"L{k}_mid",          # slots 88..95
                                     rgb=[int(c * MID) for c in rgb], allow_overwrite=True)
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
    next_frame, next_midi_try = 0.0, 0.0
    try:
        while True:
            t0 = time.time()
            # All MIDI output happens here, in the main thread (push2-python/mido requirement).
            if not push.midi_is_configured() and t0 >= next_midi_try:
                next_midi_try = t0 + 0.5
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
            if t0 >= next_frame:
                next_frame = t0 + dt
                try:
                    frame, _ = render(bridge.snapshot())
                    push.display.display_frame(frame, input_format=FRAME_FORMAT_BGR565)
                except Exception as e:  # display issues shouldn't stop pads/encoders
                    if not display_warned:
                        print(f"[display] {e}", file=sys.stderr)
                        display_warned = True
            time.sleep(max(0.0, 1.0 / LED_HZ - (time.time() - t0)))
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
    ap.add_argument("--check", type=int, metavar="LAYER",
                    help="try every Resolume call on this (spare) layer, undo each, report OK/FAIL")
    ap.add_argument("--check-columns", action="store_true", help="with --check: also launch a column")
    args = ap.parse_args()

    cfg = load_config(args.config)
    rest = Resolume(cfg["resolume"]["host"], cfg["resolume"]["port"])
    if args.check:
        from resolume_check import Checker
        ok = Checker(cfg["resolume"]["host"], cfg["resolume"]["port"], args.check).run(args.check_columns)
        sys.exit(0 if ok else 1)
    elif args.dump:
        dump(rest, args.dump, args.clip)
    else:
        run(cfg, rest, args.sim)


if __name__ == "__main__":
    main()
