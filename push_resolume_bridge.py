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
Views     Session = CLIP view (pads = clips), Note = SEQUENCER view (pads = step sequencer).
          Each view has its own menus on Upper Row (CLIP: white, SEQUENCER: red).
Mix       Mix / Mute / Solo: click = MIX / MUTE / SOLO screen (click again = back), hold = only
          while held. Track 1-8 = layer masters (1 = top visible layer), Master encoder =
          composition master, Lower Row = mute (SOLO: solo).
Menus     CLIP: Upper Row 1 CLIP PARAMS, 6 LAYER PARAMS (Lower Row 1-8 = parameter page 1-8),
          3 CLIP EFFECTS, 7 LAYER EFFECTS. SEQUENCER: 1 ENVELOPE, 2 SETTINGS, 3 PRESETS.
Color     Upper Row 2 = CLIP COLOR for the selected clip: Track 1-3 = R/G/B, 4-6 = Hue/Sat/
          Brightness, 8 = which colour param. Lower Row 1-8 = that param's palette colours.
Order     Hold Convert + touch a knob, go to any page, touch the target knob -> the two
          params swap. The order is saved per param type in pins.yaml.
Tempo     Tap Tempo = Resolume's own tap, Shift + Tap Tempo = resync (beat 1 now).
          Tempo encoder = BPM +-1 (Shift +-0.1). Tap Tempo, the display and playing pads
          blink on the beat; Metronome turns the pad pulse on / off.
FX        CLIP / LAYER EFFECTS = effects of the selected clip / its layer:
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

Talks to Resolume through its REST API, with live updates over its WebSocket
(falls back to polling the REST API 4x per second when the WebSocket is not available):
    Arena -> Preferences -> Webserver -> Enable Webserver & REST API

Usage
    python push_resolume_bridge.py                 run the bridge
    python push_resolume_bridge.py --sim           run with push2-python's browser simulator
    python push_resolume_bridge.py --dump 3        list parameter paths for layer 3 (for config.yaml)
    python push_resolume_bridge.py --dump 3 --clip 2
    python push_resolume_bridge.py --check 8       test every Resolume call on layer 8 (undone after)
    python push_resolume_bridge.py --install-plugin  copy the Bar Chaser effect into Resolume's Extra Effects
"""

from __future__ import annotations

import argparse
import colorsys
import importlib.util
import json
import os
import platform
import math
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

import requests
import yaml

from chaser_engine import UNASSIGNED, PluginEngine, pad_key
from sequencer import DIRECTIONS, Sequencer
from display import LAYER_RGB, render  # noqa: F401  (render re-exported for tests/tools)
from resolume_api import (  # noqa: F401
    Resolume, ResolumeWS, Sender, clip_state, color_label, fmt_value, hex_to_rgba, is_param, label_of,
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
BLACKOUT_RESEND = 0.5  # s: blackout / restore not reported back by Resolume yet → send it again
BLACKOUT_TRIES = 10    # resends before giving up (with a message on the display)
METRONOME_BUTTON = "Metronome"              # pad pulse on / off
TAP_PATH = "tempocontroller/tempo_tap"
RESYNC_PATH = "tempocontroller/resync"
BEAT_ON = 0.12        # s a beat stays lit (Tap Tempo button, pad pulse)
LED_HZ = 50           # LED updates per second (beat edges); the display runs at display_fps
MASTER_COLOR_BUTTON = "Master"              # COLOR on the composition's colour effect
PASTE_BUTTON = "Duplicate"                  # COLOR: hold + pad / row / column = paste colour
NOTE_TIME = 1.8       # s a short message stays on the display
NOTE_BUTTON, SESSION_BUTTON = "Note", "Session"   # the two views: sequencer / clips
BROWSE_BUTTON, REPEAT_BUTTON, ACCENT_BUTTON = "Browse", "Repeat", "Accent"
DELETE_BUTTON, DOUBLE_LOOP_BUTTON, FIXED_LENGTH_BUTTON = "Delete", "Double Loop", "Fixed Length"
OCTAVE_UP, OCTAVE_DOWN = "Octave Up", "Octave Down"
SELECT_BUTTON = "Select"                    # SEQ: tap = multi-select on / off; hold + group button = store
LAYOUT_BUTTON, SCALE_BUTTON = "Layout", "Scale"   # SEQ: buttons right of the pads = pad groups / grid
SWING_ENCODER = "Swing Encoder"
SEQ_HZ = 100          # sequencer clock ticks per second
# Menus on the buttons above the display, per view. CLIP view (Session): white; SEQUENCER view (Note): red.
CLIP_MENUS = {"Upper Row 1": "clip_params", "Upper Row 2": "color", "Upper Row 3": "clip_fx",
              "Upper Row 6": "layer_params", "Upper Row 7": "layer_fx"}
SEQ_MENUS = {"Upper Row 1": "seq_env", "Upper Row 2": "seq_settings", "Upper Row 3": "seq_presets",
             "Upper Row 4": "seq_mapping"}
SEQ_PAGES = {"seq_env": ["Attack", "Decay", "Sustain", "Release", "Gate"],     # knobs per SEQ menu
             "seq_settings": ["Direction", "Length", "Level"], "seq_presets": [], "seq_mapping": []}
MAP_FIXTURES = 32     # MAPPING: fixtures on the top four pad rows (Octave ▲ ▼ = next 32)
MAP_BLINK = 0.08      # s per phase of the double blink that confirms a stored mapping
PARAM_MODES = ("clip_params", "layer_params")
FX_MODES = ("clip_fx", "layer_fx")
MIX_MODES = ("mix", "mute", "solo")       # screens on top of either view (Mix / Mute / Solo button)
OVERLAY_BUTTONS = {MIX_BUTTON: "mix", MUTE_BUTTON: "mute", SOLO_BUTTON: "solo"}
HOLD_TIME = 0.4       # s: shorter press = click (the screen stays), longer = hold (back on release)
LEGACY_MODES = {"params": "clip_params", "fx": "clip_fx", "seq": "seq_env"}
SEQ_RED, SEQ_RED_DIM = "L6", "L6_dim"     # layer colour 7 is red: the SEQUENCER menu buttons
FX_SOURCES = [("Clip", "clip"), ("Layer", "layer")]   # FX menus: CLIP EFFECTS / LAYER EFFECTS
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
    "resolume": {"host": "127.0.0.1", "port": 8080, "poll_interval": 0.25,
                 "websocket": True,     # live updates; False = always poll
                 "ws_refresh": 5.0},    # s between full REST refreshes while the WebSocket is live
    "grid": {"layer_offset": 0, "column_offset": 0},
    "encoders": {"coarse": 0.01, "fine": 0.001},
    "display_fps": 20,
    "stop_column": None,   # None = stop via /clear; N = trigger column N instead
    "pins_file": None,     # param order file; None = pins.yaml next to this script
    "colors_file": None,   # own palette (Shift + Lower Row in COLOR); None = colors.yaml here
    "chases_file": None,   # step sequencer patterns; None = chases.yaml next to this script
    "sequencer": {"tracks": 4},
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
        self.ws_refresh = float(cfg["resolume"].get("ws_refresh", 5.0))
        self.ws = None             # ResolumeWS when live updates are enabled
        self.index = {}            # param id -> node in self.comp (for WebSocket updates)
        self.layer_offset = int(cfg["grid"]["layer_offset"])
        self.col_offset = int(cfg["grid"]["column_offset"])
        self.coarse = float(cfg["encoders"]["coarse"])
        self.fine = float(cfg["encoders"]["fine"])
        self.sel = (1, 1)          # (layer, column), 1-based, as in Resolume
        self.view = "clip"         # what the pads show: "clip" (Session) or "seq" (Note)
        self.menus = {"clip": "clip_params", "seq": "seq_env"}   # current menu of each view
        self.overlay = None        # "mix" / "mute" / "solo" screen on top of the view, or None
        self.ov_latched = False    # overlay opened by a click (stays) rather than a hold
        self.ov_stack = []         # latched overlays underneath the current one (click again = back)
        self.ov_press = {}         # overlay -> [press time, used] while its button is down
        self._pages = {}           # menu -> parameter page
        self.color_idx = 0         # which colour param the COLOR menu edits
        self.color_target = "clip"  # "clip" or "master" (composition colour effect)
        self.paste_held = False    # Duplicate
        self.note = ("", 0.0)      # short message on the display (text, time)
        self.colors_path = Path(cfg.get("colors_file") or Path(__file__).with_name("colors.yaml"))
        self.own_palette = self._load_palette()
        self.hsv_cache = {}        # param id -> (rgb, [h, s, v]) keeps hue while saturation is 0
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
        self.blackout_seen = False # Resolume has reported the master at 0 since this blackout began
        self.restore = None        # master value being restored, until Resolume reports it
        self.bo_sent, self.bo_tries = 0.0, 0   # last (re)send of the blackout / restore value
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
        # ---- step sequencer (SEQ) ----
        sc = cfg.get("sequencer") or {}
        self.seq = Sequencer(cfg.get("chases_file") or Path(__file__).with_name("chases.yaml"),
                             n_tracks=int(sc.get("tracks", 4)))
        self.engine = PluginEngine(rest, lambda: self.comp, self.refresh_comp, self._send_level,
                                   store=self.seq, send_param=self.sender.param)
        self.sel_pads = {0}        # selected pads (0-based); steps edit all of them
        self.multi = False         # Select latched: pad press adds / removes instead of replacing
        self.select_held = self.select_used = False
        self.side = "groups"       # buttons right of the pads in SEQ: "groups" (Layout) or "grid" (Scale)
        self.cur_group = None      # ("track" | "global", button) last stored / recalled (lit while it matches)
        self.map_armed = None      # MAPPING: fixture index picked with Select, waiting for a pad
        self.map_focus = None      # MAPPING: pad last pressed (its fixture is shown)
        self.map_blink = {}        # MAPPING: ("fx", index) / ("pad", k) -> time of the confirming double blink
        self.map_page = 0          # MAPPING: which 32 fixtures are on the pads
        self.repeat = False
        self.accent = False
        self.delete_held = self.browse_held = self.fixed_len_held = False
        self.browse_used = False
        self.held_steps = set()    # step indices held on the pads
        self.held_bars = {}        # pad key -> track, while a pad is held
        self.dup_src = None        # Duplicate + pattern: source pattern
        self.seq_note = ""         # last setup / load message (shown on the display)
        self.last_grid_step = None

    # ---- screens: view (pads) + menu (display, knobs, buttons below) + MIX / MUTE / SOLO ---- #
    @property
    def mode(self):
        """The screen shown now: the overlay if one is open, else the view's menu."""
        return self.overlay or self.menus[self.view]

    @mode.setter
    def mode(self, m):
        m = LEGACY_MODES.get(m, m)
        if m in MIX_MODES:
            self.overlay, self.ov_latched = m, True
            return
        self._clear_overlay()
        self.view = "seq" if m in SEQ_PAGES else "clip"
        self.menus[self.view] = m

    @property
    def page(self):
        return self._pages.get(self.mode, 0)

    @page.setter
    def page(self, v):
        self._pages[self.mode] = v

    def _clear_overlay(self):
        self.overlay, self.ov_latched, self.ov_stack = None, False, []

    def _overlay_back(self):
        self.overlay = self.ov_stack.pop() if self.ov_stack else None
        self.ov_latched = self.overlay is not None

    def _ov_used(self):
        """Something was pressed / turned while an overlay button is down: it is a hold."""
        for press in self.ov_press.values():
            press[1] = True

    def overlay_button(self, ov, down):
        """Mix / Mute / Solo: click = open (click again = back), hold = open until released."""
        now = time.time()
        with self.lock:
            if down:
                if self.overlay == ov and self.ov_latched:            # second click = previous screen
                    self._overlay_back()
                    return
                if self.overlay and self.ov_latched:
                    self.ov_stack = [o for o in self.ov_stack if o != self.overlay] + [self.overlay]
                self.ov_stack = [o for o in self.ov_stack if o != ov]
                self.overlay, self.ov_latched = ov, False
                self.ov_press[ov] = [now, False]
                self.move_src = None
                return
            press = self.ov_press.pop(ov, None)
            if press is None or self.overlay != ov:
                return
            if now - press[0] < HOLD_TIME and not press[1]:
                self.ov_latched = True                                 # click: stays open
            else:
                self._overlay_back()                                   # hold: back on release

    def set_view(self, view):
        self._clear_overlay()
        self.view = view
        self.move_src = None
        self.held_steps.clear()
        self.held_bars.clear()
        self.select_held = False
        self.map_armed = None

    # ---- composition access --------------------------------------------- #
    def layers(self):
        return (self.comp or {}).get("layers") or []

    def visible_layers(self):
        """1-based indices of the layers on the pads and in MIX (all of them)."""
        return list(range(1, len(self.layers()) + 1))

    def layer_json(self, L):
        layers = self.layers()
        return layers[L - 1] if 1 <= L <= len(layers) else None

    def clip_json(self, L, C):
        layer = self.layer_json(L)
        clips = (layer or {}).get("clips") or []
        return clips[C - 1] if 1 <= C <= len(clips) else None

    def mix_layers(self):
        """Layers on encoders 1-8 in mix mode: top visible layer first."""
        vis = self.visible_layers()
        top = min(self.layer_offset + 8, len(vis))
        return [vis[k] for k in range(top - 1, self.layer_offset - 1, -1)][:8]

    def max_cols(self):
        return max((len(l.get("clips") or []) for l in self.layers()), default=0)

    def live(self):
        return bool(self.ws and self.ws.live)

    def set_comp(self, comp):
        """New full composition (REST poll or WebSocket)."""
        index, stack = {}, [comp]
        while stack:
            n = stack.pop()
            if isinstance(n, dict):
                if "valuetype" in n and "id" in n:
                    index[n["id"]] = n
                stack.extend(v for v in n.values() if isinstance(v, (dict, list)))
            elif isinstance(n, list):
                stack.extend(n)
        if not self.online:
            print(f"[resolume] connected — {len(comp.get('layers') or [])} layers")
        with self.lock:
            self.comp, self.index, self.online = comp, index, True
            self._check_blackout()
            try:
                self.engine.sync_pads(comp)             # pad memory: tracks keep their pad assignment
            except Exception:
                traceback.print_exc()

    def on_param(self, pid, value):
        """WebSocket parameter_update: patch the value in place."""
        with self.lock:
            n = self.index.get(pid)
            if n is not None and value is not None:
                n["value"] = value
                if pid == (master_param(self.comp) or {}).get("id"):
                    self._check_blackout()

    def desired_ids(self):
        """Params to receive live: everything the pads, LEDs and the current menu show."""
        ids = []

        def add(node):
            if is_param(node):
                ids.append(node["id"])

        def add_all(node):
            stack = [node]
            while stack:
                n = stack.pop()
                if isinstance(n, dict):
                    add(n) if "valuetype" in n else stack.extend(n.values())
                elif isinstance(n, list):
                    stack.extend(n)

        with self.lock:
            comp = self.comp
            if not comp:
                return []
            add(master_param(comp))
            add(resolve_node(comp, TEMPO_PATH))
            for col in comp.get("columns") or []:
                add(col.get("connected"))
            for layer in self.layers():
                add(master_param(layer))
                add(layer.get("bypassed"))
                add(layer.get("solo"))
                for clip in layer.get("clips") or []:
                    add(clip.get("connected"))
            L, C = self.sel
            clip, layer = self.clip_json(L, C), self.layer_json(L)
            if clip:
                add_all(clip.get("video"))
                add_all((clip.get("transport") or {}).get("controls"))   # empty clips: transport = null
            if layer:
                add_all(layer.get("video"))
            add_all(resolve_node(comp, "video/effects"))
        return ids

    # ---- step sequencer ---------------------------------------------------------- #
    def refresh_comp(self):
        try:
            self.set_comp(self.rest.composition())
        except Exception as e:
            print(f"[seq] refresh failed: {e}", file=sys.stderr)

    def _send_level(self, pid, value):
        with self.lock:
            n = self.index.get(pid)
            if n is not None:
                n["value"] = value
        if self.live():
            self.ws.set(pid, value)
        else:
            self.sender.param(pid, {"value": value})

    def beat_time(self, now=None):
        """Continuous beats since the beat anchor, or None without a tempo."""
        p = self.tempo_param()
        bpm = float(self.value_of(p) or 0) if p else 0.0
        if bpm <= 0:
            return None
        t0, b0 = self.beat_anchor
        return b0 + ((now or time.time()) - t0) * bpm / 60.0

    def add_chaser(self):
        """Shift + Note: put a Bar Chaser on the selected clip's layer (runs in a worker thread)."""
        L = self.sel[0]
        layer = self.layer_json(L)
        if layer is None:
            self.note_msg("select a clip on the layer first")
            return

        def work():
            code = self.engine.add_to_layer(L)
            name = text(layer.get("name")) or f"layer {L}"
            self.note_msg(f"Bar Chaser is on {name}" if code == 200 else
                          f"Bar Chaser added to {name}" if code == 204 else f"can't add Bar Chaser: HTTP {code}")
        threading.Thread(target=work, daemon=True).start()

    def join_track(self, track):
        """Browse: the selected clip's layer answers to track `track` (adds the effect if missing)."""
        L = self.sel[0]
        layer = self.layer_json(L)
        if layer is None:
            self.note_msg("select a clip on the layer first")
            return

        def work():
            ok = self.engine.set_track(L, track + 1)
            name = text(layer.get("name")) or f"layer {L}"
            self.note_msg(f"T{track + 1} = {name}" if ok else f"can't set track on {name}")
        threading.Thread(target=work, daemon=True).start()

    def seq_loop(self):
        """100 Hz: feed beat time to the sequencer, send changed levels, handle Repeat."""
        last_err = 0.0
        while True:
            try:
                bt = self.beat_time()
                changed = {}
                if bt is not None:
                    with self.lock:
                        sb = self.seq.step_beats()
                        g = math.floor(bt / sb)
                        if self.repeat and self.held_bars and g != self.last_grid_step:
                            for bar, track in list(self.held_bars.items()):
                                tr = self.seq.pattern.tracks[track]
                                self.seq.trigger(track, bar, 1.0, tr.gate * sb, bt)
                        self.last_grid_step = g
                        changed = self.seq.tick(bt)
                for (track, bar), v in changed.items():
                    self.engine.set_level(track, bar, v)
            except Exception:                              # never let a bug stop the clock
                if time.time() - last_err > 5:
                    traceback.print_exc()
                    last_err = time.time()
            time.sleep(1.0 / SEQ_HZ)

    def bar_index(self, i, j):
        """Pad (row i, col j) in the pad block (rows 5-7) → pad index 0-23, bottom-left = 0."""
        return (7 - i) * 8 + j if 5 <= i <= 7 else None

    def pattern_index(self, j):
        """Row 4: patterns 1-8, with Shift 9-16."""
        return j + (8 if self.shift else 0)

    def sel_keys(self):
        return [pad_key(k) for k in sorted(self.sel_pads)]

    def current_group(self):
        """("track" | "global", button) of the group the selection came from, while it still equals it."""
        if self.cur_group is None:
            return None
        scope, g = self.cur_group
        pads, shown = self.seq.group(g, self.seq.track)
        return self.cur_group if pads and shown == scope and set(pads) == self.sel_pads else None

    def group_label(self, scope, g):
        return f"GG{g + 1}" if scope == "global" else f"G{g + 1}"

    def _group_button(self, g):
        """Button g right of the pads (Layout). Select + button = store for the selected track,
        Select + Shift + button = store for all tracks (GG); Delete (+ Shift) = clear; tap = select."""
        seq, t = self.seq, self.seq.track
        scope = "global" if self.shift else "track"
        owner = None if self.shift else t
        tag = self.group_label(scope, g) + ("" if self.shift else f" of T{t + 1}")
        if self.select_held:
            self.select_used = True
            seq.store_group(g, self.sel_pads, owner)
            self.cur_group = (scope, g)
            self.note_msg(f"{tag} = pad{'s' * (len(self.sel_pads) != 1)} "
                          + ", ".join(str(k + 1) for k in sorted(self.sel_pads)))
        elif self.delete_held:
            seq.clear_group(g, owner)
            self.note_msg(f"{tag} cleared")
        else:
            pads, shown = seq.group(g, t)
            if pads:
                self.sel_pads = set(pads)
                self.cur_group = (shown, g)
            else:
                self.note_msg(f"G{g + 1} empty · Select + button = store (+ Shift = all tracks)")

    def mapping(self):
        return self.view == "seq" and self.menus["seq"] == "seq_mapping"

    def _map_pad(self, ij, down):
        """MAPPING: top four rows = fixtures, row 5 = red divider, rows 6-8 = pads 1-24.
        Select + fixture = pick it (blinks), then a pad = store it there (both blink twice).
        Delete + pad = no fixture. True = handled, False = let the normal pad code run."""
        i, j = ij
        seq, eng = self.seq, self.engine
        fx = eng.fixtures(seq.track)
        if i < 4:
            f = self.map_page * MAP_FIXTURES + i * 8 + j
            if down and f < len(fx):
                if self.select_held:
                    self.select_used = True
                    self.map_armed = None if self.map_armed == f else f
                elif self.map_armed == f:
                    self.map_armed = None                          # pressed again = cancel
                self.note_msg(f"{fx[f]['label']} = {fx[f]['name']}")
            return True
        if i == 4:
            return True
        k = self.bar_index(i, j)
        if not down:
            return False
        if self.delete_held:
            if eng.set_pad(seq.track, k, UNASSIGNED):
                self.map_blink = {("pad", k): time.time()}
                self.note_msg(f"Pad {k + 1}: no fixture")
            return True
        if self.map_armed is not None and self.map_armed < len(fx):
            f = self.map_armed
            if eng.set_pad(seq.track, k, fx[f]["name"]):
                now = time.time()
                self.map_blink = {("fx", f): now, ("pad", k): now}
                self.note_msg(f"Pad {k + 1} = {fx[f]['label']}")
            else:
                self.note_msg(f"T{seq.track + 1}: no Bar Chaser to map")
            self.map_armed, self.map_focus = None, k
            if self.select_held:
                self.select_used = True
            return True
        self.map_focus = k
        return False                                               # select + flash as usual

    def _seq_pad(self, ij, velocity, down):
        i, j = ij
        with self.lock:
            bt = self.beat_time() or 0.0
            seq = self.seq
            if self.mapping() and self._map_pad(ij, down):
                return
            if i < 4:                                              # steps, for every selected pad
                step = i * 8 + j
                if not down:
                    self.held_steps.discard(step)
                    return
                if step >= seq.pattern.length:
                    return
                keys = self.sel_keys()
                if self.delete_held:
                    for key in keys:
                        seq._steps(key).pop(step, None)
                    seq.save()
                    return
                self.held_steps.add(step)
                all_on = all(step in seq._steps(key) for key in keys)
                level = 1.0 if self.accent else max(0.05, velocity / 127.0)
                for key in keys:
                    st = seq._steps(key)
                    if all_on:
                        st.pop(step, None)
                    elif step not in st:
                        st[step] = [level, None]
                seq.save()
            elif i == 4:                                           # patterns
                if not down:
                    return
                p = self.pattern_index(j)
                if self.delete_held:
                    seq.clear_pattern(p)
                elif self.paste_held:
                    if self.dup_src is None:
                        self.dup_src = p
                    else:
                        src, self.dup_src = self.dup_src, None
                        seq.copy_pattern(src, p)
                        self.note_msg(f"P{src + 1} copied to P{p + 1}")
                else:
                    seq.switch_pattern(p, bt, now=(seq.pending == p))   # same pad again = now
            else:                                                  # pads 1-24
                k = self.bar_index(i, j)
                key = pad_key(k)
                track = seq.track
                if not down:
                    if key in self.held_bars:
                        seq.release(self.held_bars.pop(key), key, bt)
                    return
                if self.delete_held:
                    seq.clear_steps(key)
                    return
                if self.select_held:
                    self.select_used = True                    # no latch toggle on release
                if self.multi or self.select_held:
                    if k in self.sel_pads and len(self.sel_pads) > 1:
                        self.sel_pads.discard(k)
                    else:
                        self.sel_pads.add(k)
                else:
                    self.sel_pads = {k}
                self.held_bars[key] = track
                seq.trigger(track, key, 1.0, None, bt)

    def _seq_button(self, name, down):
        """Buttons that mean something else while the pads are the sequencer. True = consumed."""
        bt = self.beat_time() or 0.0
        if name == DELETE_BUTTON:
            self.delete_held = down
            return True
        if name == FIXED_LENGTH_BUTTON:
            self.fixed_len_held = down
            return True
        if name == BROWSE_BUTTON:
            if down:
                self.browse_held, self.browse_used = True, False
            else:
                self.browse_held = False
                if not self.browse_used:
                    self.join_track(self.seq.track)
            return True
        if name == SELECT_BUTTON:                                  # tap = latch, hold = momentary
            with self.lock:
                if down:
                    self.select_held, self.select_used = True, False
                else:
                    self.select_held = False
                    if not self.select_used:
                        self.multi = not self.multi
            return True
        if name in (LAYOUT_BUTTON, SCALE_BUTTON):
            if down:
                self.side = "groups" if name == LAYOUT_BUTTON else "grid"
            return True
        if name == PASTE_BUTTON:                                   # Duplicate: copy pattern
            self.paste_held = down
            if not down:
                self.dup_src = None
            return True
        seq_row = name in LOWER_ROW and self.overlay is None
        if not down:
            return name in (PLAY_BUTTON, REPEAT_BUTTON, ACCENT_BUTTON, DOUBLE_LOOP_BUTTON,
                            OCTAVE_UP, OCTAVE_DOWN) or name in SCENE_BUTTONS or seq_row
        with self.lock:
            if name == PLAY_BUTTON:
                if self.seq.running:
                    self.seq.stop()
                else:
                    self.seq.start(bt)
                return True
            if name == REPEAT_BUTTON:
                self.repeat = not self.repeat
                return True
            if name == ACCENT_BUTTON:
                self.accent = not self.accent
                return True
            if name == DOUBLE_LOOP_BUTTON:
                self.seq.double_loop()
                return True
            if name in (OCTAVE_UP, OCTAVE_DOWN):                   # MAPPING: fixture pages
                if self.mapping():
                    pages = max(1, math.ceil(len(self.engine.fixtures(self.seq.track)) / MAP_FIXTURES))
                    step = 1 if name == OCTAVE_DOWN else -1
                    self.map_page = max(0, min(pages - 1, self.map_page + step))
                return True
            if name in SCENE_BUTTONS:                              # top button = 1/32t / group 1
                if self.side == "grid":
                    self.seq.grid = name
                else:
                    self._group_button(SCENE_BUTTONS.index(name))
                return True
            if seq_row:
                k = LOWER_ROW.index(name)
                if self.fixed_len_held:
                    self.seq.set_length((k + 1) * 4)
                elif k < self.seq.n_tracks:
                    if self.delete_held:
                        self.seq.clear_steps(track=k)
                    elif self.browse_held:
                        self.browse_used = True
                        self.join_track(k)
                    else:
                        self.seq.track = k
                return True
        return False

    def _turn_seq(self, idx, inc):
        names = SEQ_PAGES.get(self.mode, [])
        if idx >= len(names):
            return
        name = names[idx]
        seq, tr = self.seq, self.seq.pattern.tracks[self.seq.track]
        fine = 0.2 if self.shift else 1.0
        if self.held_steps and name in ("Gate", "Level"):         # hold step + knob, all selected pads
            for key in self.sel_keys():
                st = seq._steps(key)
                for s_ in self.held_steps:
                    if s_ in st:
                        if name == "Level":
                            seq.set_step_values(key, [s_], level=st[s_][0] + inc * 0.02 * fine)
                        else:
                            gate = st[s_][1] if st[s_][1] is not None else tr.gate
                            seq.set_step_values(key, [s_], gate=gate + inc * 0.02 * fine)
            return
        e = tr.envelope
        if name == "Attack":
            e.attack = max(0.0, min(4.0, e.attack + inc * 0.05 * fine))
        elif name == "Decay":
            e.decay = max(0.0, min(4.0, e.decay + inc * 0.05 * fine))
        elif name == "Sustain":
            e.sustain = max(0.0, min(1.0, e.sustain + inc * 0.01 * fine))
        elif name == "Release":
            e.release = max(0.0, min(4.0, e.release + inc * 0.05 * fine))
        elif name == "Gate":
            tr.gate = max(0.1, min(1.0, tr.gate + inc * 0.02 * fine))
        elif name == "Direction":
            acc = self.choice_acc.get("seqdir", 0) + inc
            if abs(acc) >= 4:
                i = (DIRECTIONS.index(seq.pattern.direction) + (1 if acc > 0 else -1)) % len(DIRECTIONS)
                seq.set_direction(DIRECTIONS[i])
                acc = 0
            self.choice_acc["seqdir"] = acc
        elif name == "Length":
            seq.set_length(seq.pattern.length + inc)
        elif name == "Level":
            tr.level = max(0.0, min(1.0, tr.level + inc * 0.01 * fine))
        seq.save()

    def turn_swing(self, inc):
        with self.lock:
            p = self.seq.pattern
            p.swing = max(0.0, min(1.0, p.swing + inc * 0.02))
            self.seq.save()

    def _map_pad_colors(self):
        """MAPPING lights: fixtures dim white, mapped ones in the track colour, the picked one blinking;
        row 5 red; pads with a fixture in the track colour; a stored mapping blinks twice."""
        seq, eng, now = self.seq, self.engine, time.time()
        tc = seq.track % 8
        fx = eng.fixtures(seq.track)
        values = [eng.pad_name(seq.track, k) for k in range(24)]
        used = set()
        for v in values:
            used.update(eng.fixtures_of_value(seq.track, v))
        focus = set(eng.fixtures_of_value(seq.track, values[self.map_focus])) if self.map_focus is not None else set()
        armed_pads = {k for k, v in enumerate(values)
                      if self.map_armed is not None and self.map_armed in eng.fixtures_of_value(seq.track, v)}
        lit = {key for (t, key), v in seq.levels.items() if t == seq.track and v > 0.02}
        blink = int(now / BLINK) % 2 == 0

        def double(key):
            t0 = self.map_blink.get(key)
            if t0 is None or now - t0 >= 4 * MAP_BLINK:
                return None
            return "white" if int((now - t0) / MAP_BLINK) in (0, 2) else "black"

        grid = {}
        for i in range(8):
            for j in range(8):
                if i < 4:
                    f = self.map_page * MAP_FIXTURES + i * 8 + j
                    if f >= len(fx):
                        color = "black"
                    else:
                        color = double(("fx", f))
                        if color is None:
                            if self.map_armed == f:
                                color = "white" if blink else "black"
                            elif f in focus:
                                color = f"L{tc}"
                            else:
                                color = f"L{tc}_dim" if f in used else "dark_gray"
                elif i == 4:
                    color = "red"
                else:
                    k = self.bar_index(i, j)
                    color = double(("pad", k))
                    if color is None:
                        if pad_key(k) in lit or k == self.map_focus or k in armed_pads:
                            color = f"L{tc}" if values[k] else "white"
                        else:
                            color = f"L{tc}_dim" if values[k] else "black"
                grid[(i, j)] = color
        return grid

    def _seq_pad_colors(self):
        if self.mapping():
            return self._map_pad_colors()
        grid = {}
        seq = self.seq
        pos = seq.position(self.beat_time() or 0.0)
        keys = self.sel_keys()
        tc = seq.track % 8
        track_steps = [seq.pattern.tracks[seq.track].steps.get(key, {}) for key in keys]
        lit = {key for (t, key), v in seq.levels.items()          # pads sounding on this track only
               if t == seq.track and v > 0.02}
        blink = int(time.time() / BLINK) % 2 == 0
        for i in range(8):
            for j in range(8):
                color = "black"
                if i < 4:
                    step = i * 8 + j
                    if step < seq.pattern.length:
                        n_on = sum(1 for st in track_steps if step in st)
                        if step == pos:
                            color = "green"
                        elif n_on and n_on == len(track_steps):
                            lv = min(st[step][0] for st in track_steps if step in st)
                            color = f"L{tc}" if lv >= 0.66 else f"L{tc}_mid"
                        elif n_on:
                            color = f"L{tc}_dim"
                        else:
                            color = "dark_gray"
                elif i == 4:
                    p = self.pattern_index(j)
                    has = any(t.steps for t in seq.patterns[p].tracks)
                    if seq.pending == p:
                        color = "white" if blink else "dark_gray"
                    else:
                        color = "white" if p == seq.current else ("dark_gray" if has else "black")
                else:
                    k = self.bar_index(i, j)
                    key = pad_key(k)
                    if key in lit:
                        color = f"L{tc}"
                    elif not self.engine.pad_assigned(seq.track, k):
                        color = "black"
                    elif k in self.sel_pads:
                        color = "light_gray"
                    else:
                        color = "dark_gray"
                grid[(i, j)] = color
        return grid

    def poll_loop(self):
        session = requests.Session()
        while True:
            try:
                self.set_comp(self.rest.composition(session))
            except Exception as e:
                if self.online or not getattr(self, "_warned_offline", False):
                    print(f"[resolume] not reachable at {self.rest.url} ({type(e).__name__})")
                    self._warned_offline = True
                with self.lock:
                    self.online = False
            time.sleep(self.ws_refresh if self.live() else self.poll_interval)

    def _check_blackout(self, now=None):
        """Hold the blackout (and the restore after it) until Resolume reports the new master value,
        sending it again when the change got lost on the way. Once the blackout has arrived, a master
        raised by someone else (Launch Control, mouse) ends it. Uses the value Resolume reported,
        never our own display override."""
        p = master_param(self.comp)
        if p is None or (self.blackout is None and self.restore is None):
            return
        now = now or time.time()
        try:
            v = float(p.get("value") or 0)
        except (TypeError, ValueError):
            return
        if self.blackout is not None:
            if v <= 0.01:
                self.blackout_seen = True
            elif self.blackout_seen:
                if now - self.blackout_t > 1.0:                # raised elsewhere after it arrived
                    self.blackout = None
            elif now - self.bo_sent > BLACKOUT_RESEND:         # our 0 has not arrived: send again
                if self.bo_tries >= BLACKOUT_TRIES:
                    self.blackout = None
                    self.note_msg("Blackout failed: Resolume did not take the master change")
                    return
                self.bo_sent, self.bo_tries = now, self.bo_tries + 1
                self._set(p["id"], 0.0, {"value": 0.0})
            return
        target = self.restore
        if abs(v - target) <= 0.01 or v > 0.01:                # restored, or moved elsewhere meanwhile
            self.restore = None
        elif now - self.bo_sent > BLACKOUT_RESEND:             # still black: send the restore again
            if self.bo_tries >= BLACKOUT_TRIES:
                self.restore = None
                self.note_msg("Restore failed: Resolume did not take the master change")
                return
            self.bo_sent, self.bo_tries = now, self.bo_tries + 1
            self._set(p["id"], target, {"value": target})

    def blackout_watchdog(self):
        """Main loop: keep checking while a blackout or restore is waiting for Resolume."""
        if self.blackout is not None or self.restore is not None:
            with self.lock:
                self._check_blackout()

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

    def slots(self, scope=None):
        """Parameter slots of the CLIP PARAMS (scope "clip") or LAYER PARAMS (scope "layer") menu."""
        scope_wanted = scope or ("layer" if self.mode == "layer_params" else "clip")
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
                if scope != scope_wanted:
                    continue
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
                if scope != scope_wanted:
                    continue
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
            self._set(pid, opts[i], {"value": opts[i]})    # Arena 7.23 rejects {"index": …} (HTTP 400)

    def turn(self, idx, inc):
        with self.lock:
            self._ov_used()
            if self.mode in SEQ_PAGES:
                self._turn_seq(idx, inc)
                return
            if self.mode in MIX_MODES:
                layers = self.mix_layers()
                p = master_param(self.layer_json(layers[idx])) if idx < len(layers) else None
                if p:
                    self._nudge(p, {}, inc)
                return
            if self.mode == "color":
                self._turn_color(idx, inc)
                return
            if self.mode in FX_MODES:
                items, _ = self.fx_page_items()
                if idx < len(items) and items[idx][3]:
                    self._nudge(items[idx][3], {}, inc)
                return
            if self.move_src is not None or self.mode not in PARAM_MODES:
                return
            slots, _ = self.page_slots()
            if idx < len(slots) and slots[idx].param is not None:
                self._nudge(slots[idx].param, slots[idx].spec, inc)

    def turn_master(self, inc):
        with self.lock:
            self._ov_used()
            if self.mode in MIX_MODES:
                p = master_param(self.comp)
                self.blackout = self.restore = None            # the knob takes over
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
        """[(tag, name, bypassed param or None, amount param or None)]: the clip's effects in CLIP
        EFFECTS, the layer's in LAYER EFFECTS."""
        L, C = self.sel
        roots = {"clip": self.clip_json(L, C), "layer": self.layer_json(L)}
        wanted = "layer" if self.mode == "layer_fx" else "clip"
        out = []
        for tag, scope in FX_SOURCES:
            if scope != wanted:
                continue
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
            if self.mode not in PARAM_MODES:
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
            now = time.time()
            if self.blackout is None:
                self.blackout, self.blackout_t = float(self.value_of(p) or 0), now
                self.blackout_seen, self.restore = False, None
                self._set(p["id"], 0.0, {"value": 0.0})
            else:
                self._set(p["id"], self.blackout, {"value": self.blackout})
                self.blackout, self.restore = None, self.blackout
            self.bo_sent, self.bo_tries = now, 0

    def flash_layer(self, row, down):
        with self.lock:
            L = self.pad_to_cell(row, 0)[0]
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
        vis = self.visible_layers()
        k = self.layer_offset + (7 - i)
        return (vis[k] if 0 <= k < len(vis) else 0), self.col_offset + j + 1

    def pad_pressed(self, ij, velocity=100):
        self._ov_used()
        if self.view == "seq":
            return self._seq_pad(ij, velocity, True)
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
                self._pages.clear()
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
        if self.view == "seq":
            return self._seq_pad(ij, 0, False)
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
        if name in OVERLAY_BUTTONS:                        # Mix / Mute / Solo: click = open, hold = peek
            if name == MUTE_BUTTON:
                self.mute_held = down                      # hold + pad = mute that layer
            elif name == SOLO_BUTTON:
                self.solo_held = down
            self.overlay_button(OVERLAY_BUTTONS[name], down)
            return
        if down:
            self._ov_used()
        if self.view == "seq" and self._seq_button(name, down):
            return
        if name == PLAY_BUTTON:
            self.play_held = down
            return
        if name == STOP_BUTTON:
            self.stop_held = down
            return
        if name == PASTE_BUTTON:
            self.paste_held = down
            return
        if self.paste_held and self.mode == "color" and name in SCENE_BUTTONS + LOWER_ROW:
            if down:
                with self.lock:
                    if name in SCENE_BUTTONS:          # whole layer of that pad row
                        L = self.pad_to_cell(SCENE_BUTTONS.index(name), 0)[0]
                        cells = [(L, c) for c in range(1, len((self.layer_json(L) or {}).get("clips") or []) + 1)]
                    else:                              # whole column above that button
                        C = self.col_offset + LOWER_ROW.index(name) + 1
                        cells = [(l, C) for l in self.visible_layers()]
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
            max_l = max(0, len(self.visible_layers()) - 8)
            max_c = max(0, self.max_cols() - 8)
            if name == "Up":
                self.layer_offset = min(max_l, self.layer_offset + jump)
            elif name == "Down":
                self.layer_offset = max(0, self.layer_offset - jump)
            elif name == "Right":
                self.col_offset = min(max_c, self.col_offset + jump)
            elif name == "Left":
                self.col_offset = max(0, self.col_offset - jump)
            elif name == "Page Right" and self.mode in FX_MODES:
                _, pages = self.fx_page_items()
                self.fx_page = min(pages - 1, self.fx_page + 1)
            elif name == "Page Left" and self.mode in FX_MODES:
                self.fx_page = max(0, self.fx_page - 1)
            elif name == "Page Right":
                _, pages = self.page_slots()
                self.page = min(pages - 1, self.page + 1)
            elif name == "Page Left":
                self.page = max(0, self.page - 1)
            elif name in (SEQ_MENUS if self.view == "seq" else CLIP_MENUS):
                self.menus[self.view] = (SEQ_MENUS if self.view == "seq" else CLIP_MENUS)[name]
                self._clear_overlay()
                self.move_src = None
                self.map_armed = None
                self.color_target = "clip"
            elif name == NOTE_BUTTON:
                if self.shift:
                    self.add_chaser()
                else:
                    self.set_view("seq")
            elif name == SESSION_BUTTON:
                self.set_view("clip")
            elif name == MASTER_COLOR_BUTTON:
                master = self.mode == "color" and self.color_target == "master"
                if self.view != "clip":
                    self.set_view("clip")
                self._clear_overlay()
                self.menus["clip"] = "color"
                self.color_target, self.color_idx = "clip" if master else "master", 0
                self.move_src = None
            elif name in LOWER_ROW and self.mode in FX_MODES:
                items, _ = self.fx_page_items()
                k = LOWER_ROW.index(name)
                if k < len(items) and items[k][2]:
                    byp = items[k][2]
                    v = not bool(self.value_of(byp))
                    self._set(byp["id"], v, {"value": v})
            elif name in LOWER_ROW and self.mode == "color" and self.shift:
                self.save_palette_color(LOWER_ROW.index(name))
            elif name in LOWER_ROW and self.mode in MIX_MODES:
                layers = self.mix_layers()
                k = LOWER_ROW.index(name)
                if k < len(layers):
                    solo = self.mode == "solo" or (self.mode == "mix" and self.solo_held)
                    self.toggle_layer(layers[k], "solo" if solo else "bypassed")
            elif name in LOWER_ROW and self.mode == "color":
                self.set_palette_color(LOWER_ROW.index(name))
            elif name in LOWER_ROW and self.mode in PARAM_MODES:
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
            params = self.mode in PARAM_MODES
            pages = self.page_slots()[1] if params else 0
            out = {MIX_BUTTON: "white" if self.overlay == "mix" else "dark_gray",
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
            seq = self.view == "seq"
            out[NOTE_BUTTON] = "white" if seq else "dark_gray"
            out[SESSION_BUTTON] = "dark_gray" if seq else "white"
            for b_, on in ((REPEAT_BUTTON, self.repeat), (ACCENT_BUTTON, self.accent), (DELETE_BUTTON, self.delete_held),
                           (BROWSE_BUTTON, self.browse_held), (DOUBLE_LOOP_BUTTON, False),
                           (FIXED_LENGTH_BUTTON, self.fixed_len_held),
                           (OCTAVE_UP, False), (OCTAVE_DOWN, False)):
                out[b_] = ("white" if on else "dark_gray") if seq else "black"
            if seq and self.mapping():
                pages = max(1, math.ceil(len(self.engine.fixtures(self.seq.track)) / MAP_FIXTURES))
                out[OCTAVE_UP] = "white" if self.map_page > 0 else "black"
                out[OCTAVE_DOWN] = "white" if self.map_page < pages - 1 else "black"
            out[SELECT_BUTTON] = ("white" if self.multi or self.select_held else "dark_gray") if seq else "black"
            out[LAYOUT_BUTTON] = ("white" if self.side == "groups" else "dark_gray") if seq else "black"
            out[SCALE_BUTTON] = ("white" if self.side == "grid" else "dark_gray") if seq else "black"
            if seq:
                out[PLAY_BUTTON] = "green" if self.seq.running else "dark_gray"
                out[PASTE_BUTTON] = "white" if self.paste_held else "dark_gray"
            menus = SEQ_MENUS if seq else CLIP_MENUS
            on, off = (SEQ_RED, SEQ_RED_DIM) if seq else ("white", "dark_gray")
            for b in UPPER_ROW:
                m = menus.get(b)
                active = m == self.mode and not (m == "color" and self.color_target == "master")
                out[b] = "black" if m is None else (on if active else off)
            n_sw = len(self.swatches())
            blink = int(time.time() / BLINK) % 2 == 0
            mix_layers = self.mix_layers()
            fx_items = self.fx_page_items()[0] if self.mode in FX_MODES else []
            for k, b in enumerate(LOWER_ROW):
                if seq and self.overlay is None:
                    if k < self.seq.n_tracks:
                        has = self.engine.ready(k)
                        out[b] = f"L{k}" if k == self.seq.track else (f"L{k}_dim" if has else "dark_gray")
                    else:
                        out[b] = "black"
                elif self.play_held:                        # column launch view
                    st = self.column_state(self.col_offset + k + 1)
                    out[b] = {"Connected": "green", "Disconnected": "dark_gray"}.get(st, "black")
                elif self.mode == "color":
                    out[b] = f"P{k}" if k < n_sw else "black"
                elif self.mode in FX_MODES:
                    byp = fx_items[k][2] if k < len(fx_items) else None
                    out[b] = "black" if byp is None else ("dark_gray" if self.value_of(byp) else "white")
                elif self.mode in MIX_MODES:
                    if k >= len(mix_layers):
                        out[b] = "black"
                        continue
                    L = mix_layers[k]
                    solo, mute = self.layer_flag(L, "solo"), self.layer_flag(L, "bypassed")
                    if self.solo_held or self.mode == "solo":
                        out[b] = "yellow" if solo and self.value_of(solo) else "dark_gray"
                    elif mute and self.value_of(mute):
                        out[b] = "red"
                    else:
                        out[b] = "yellow" if solo and self.value_of(solo) else f"L{(L - 1) % 8}"
                else:
                    out[b] = "black" if k >= pages else ("white" if k == self.page else "dark_gray")
            cur_g, tc = self.current_group(), self.seq.track % 8
            for i, b in enumerate(SCENE_BUTTONS):
                if seq and self.side == "grid":             # Scale: grid selection
                    out[b] = "white" if b == self.seq.grid else "dark_gray"
                    continue
                if seq:                                     # Layout: track groups in its colour, global white
                    pads, scope = self.seq.group(i, self.seq.track)
                    cur = cur_g == (scope, i)
                    if not pads:
                        out[b] = "black"
                    elif scope == "track":
                        out[b] = f"L{tc}" if cur else f"L{tc}_dim"
                    else:
                        out[b] = "white" if cur else "dark_gray"
                    continue
                L = self.pad_to_cell(i, 0)[0]
                out[b] = ("black" if self.layer_json(L) is None
                          else f"L{(L - 1) % 8}" if L in self.flash else f"L{(L - 1) % 8}_dim")
            all_l = range(1, len(self.layers()) + 1)
            any_mute = any(self.layer_flag(l, "bypassed") and self.value_of(self.layer_flag(l, "bypassed"))
                           for l in all_l)
            any_solo = any(self.layer_flag(l, "solo") and self.value_of(self.layer_flag(l, "solo"))
                           for l in all_l)
            out[MUTE_BUTTON] = "white" if self.overlay == "mute" else ("red" if any_mute else "dark_gray")
            out[SOLO_BUTTON] = "white" if self.overlay == "solo" else ("yellow" if any_solo else "dark_gray")
            out[BLACKOUT_BUTTON] = ("red" if blink else "black") if self.blackout is not None else "dark_gray"
            return out

    def pad_colors(self):
        grid = {}
        with self.lock:
            if self.view == "seq":
                return self._seq_pad_colors()
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
            if self.mode in FX_MODES:
                items, fx_pages = self.fx_page_items()
                fx = {"scope": "layer" if self.mode == "layer_fx" else "clip",
                      "page": self.fx_page, "pages": fx_pages, "items": [
                    {"tag": tag, "name": name,
                     "on": None if byp is None else not self.value_of(byp),
                     "amount": fmt_value(amt, self.value_of(amt), {}) if amt else None}
                    for tag, name, byp, amt in items]}
            seq = None
            if self.view == "seq":
                sq, tr = self.seq, self.seq.pattern.tracks[self.seq.track]
                e = tr.envelope

                def beats(v):
                    return f"{v:.2f} b", min(1.0, v / 4.0)

                def pct(v):
                    return f"{v * 100:.0f}%", v

                every = {"Attack": beats(e.attack), "Decay": beats(e.decay), "Sustain": pct(e.sustain),
                         "Release": beats(e.release), "Gate": pct(tr.gate),
                         "Direction": (sq.pattern.direction, 0.0),
                         "Length": (f"{sq.pattern.length} steps", sq.pattern.length / 32),
                         "Level": pct(tr.level)}
                knobs = [(n,) + every[n] for n in SEQ_PAGES.get(self.menus["seq"], [])]
                pads = [(k + 1, self.engine.short(sq.track, self.engine.pad_name(sq.track, k)) or "—")
                        for k in sorted(self.sel_pads)]
                mapping = None
                if self.mapping():
                    fxl = self.engine.fixtures(sq.track)
                    names = [self.engine.pad_name(sq.track, k) for k in range(24)]
                    pages = max(1, math.ceil(len(fxl) / MAP_FIXTURES))
                    if self.map_armed is not None and self.map_armed < len(fxl):
                        f = fxl[self.map_armed]
                        title, sub = f"{f['label']} picked  ·  press a pad to store it", f["name"]
                    elif self.map_focus is not None:
                        v = names[self.map_focus]
                        title = f"Pad {self.map_focus + 1} = {self.engine.short(sq.track, v) or '—'}"
                        sub = v or "no fixture"
                    else:
                        title, sub = "Hold Select + a fixture, then press a pad", \
                            f"{len(fxl)} fixtures on the top rows" if fxl else "no fixtures: save the Advanced Output preset"
                    mapping = {"title": title, "sub": sub, "assigned": sum(1 for v in names if v),
                               "fixtures": len(fxl), "page": self.map_page, "pages": pages}
                ready = self.engine.ready(sq.track)
                seq = {"knobs": knobs, "menu": self.menus["seq"], "mapping": mapping, "track": sq.track, "pattern": sq.pattern.name, "running": sq.running,
                       "pos": sq.position(self.beat_time() or 0.0), "length": sq.pattern.length, "grid": sq.grid,
                       "pads": pads, "layer": self.engine.layer_name(sq.track), "ready": ready,
                       "env": {"attack": e.attack, "decay": e.decay, "sustain": e.sustain, "release": e.release,
                               "gate": tr.gate},
                       "pending": sq.pending, "swing": sq.pattern.swing,
                       "group": self.group_label(*self.current_group()) if self.current_group() else None,
                       "multi": self.multi or self.select_held,
                       "select_held": self.select_held, "side": self.side,
                       "warning": "" if ready else f"T{sq.track + 1}: no Bar Chaser — Shift + Note on a layer"}
            return {
                "seq": seq,
                "fx": fx,
                "link": "LIVE" if self.live() else "POLL",
                "master_color": self.color_target == "master",
                "paste": self.paste_held and self.mode == "color",
                "note": note,
                "beat": None if b is None else (b[0], b[1] < BEAT_ON),
                "blackout": self.blackout is not None,
                "color": color,
                "bpm": f"{float(self.value_of(tp) or 0):.1f} BPM" if tp else "",
                "move": move,
                "mode": self.mode, "view": self.view, "mix": mix,
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
        bridge.pad_pressed(pad_ij, velocity)

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
        elif name == SWING_ENCODER:
            bridge.turn_swing(inc)

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
    if cfg["resolume"].get("websocket", True):
        if importlib.util.find_spec("websocket"):      # websocket-client
            bridge.ws = ResolumeWS(cfg["resolume"]["host"], cfg["resolume"]["port"],
                                   bridge.set_comp, bridge.on_param, bridge.desired_ids)
            bridge.ws.start()
        else:
            print("[resolume] pip install websocket-client for live updates; polling instead")
    bridge.sender.start()
    threading.Thread(target=bridge.seq_loop, daemon=True).start()

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
            bridge.blackout_watchdog()
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


def install_plugin(bundle):
    """Copy the built Bar Chaser bundle into ~/Documents/Resolume Arena/Extra Effects."""
    import shutil
    src = Path(bundle)
    if not (src / "Contents" / "MacOS").is_dir():
        sys.exit(f"{src} is not a plugin bundle — build it first: plugin/build.sh")
    folder = Path.home() / "Documents" / "Resolume Arena" / "Extra Effects"
    folder.mkdir(parents=True, exist_ok=True)
    dst = folder / src.name
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    print(f"installed {dst}\nRestart Resolume Arena, then add the effect 'Bar Chaser' to a layer.")


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
    ap.add_argument("--install-plugin", nargs="?", const="", metavar="BUNDLE",
                    help="copy the Bar Chaser effect into Resolume's Extra Effects folder (default: plugin/dist/Bar Chaser.bundle)")
    args = ap.parse_args()

    cfg = load_config(args.config)
    rest = Resolume(cfg["resolume"]["host"], cfg["resolume"]["port"])
    if args.install_plugin is not None:
        install_plugin(args.install_plugin or str(Path(__file__).with_name("plugin") / "dist" / "Bar Chaser.bundle"))
        return
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
