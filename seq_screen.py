"""SEQ view (Note): steps, pattern row, pads, groups, the SEQ knobs, the 100 Hz clock and the SEQ part
of the display. A mixin of Bridge: state lives on the Bridge (see _init_seq), methods here."""

from __future__ import annotations

import math
import sys
import threading
import time
import traceback

import paths
from chaser_engine import PluginEngine, pad_key
from resolume_api import text
from sequencer import DIRECTIONS, Sequencer
from constants import (ACCENT_BUTTON, BLINK, BROWSE_BUTTON, DELETE_BUTTON, DOUBLE_LOOP_BUTTON, FIXED_LENGTH_BUTTON, LAYOUT_BUTTON, LOWER_ROW, MAP_BLINK, MAP_FIXTURES, OCTAVE_DOWN, OCTAVE_UP, PASTE_BUTTON, PLAY_BUTTON, REPEAT_BUTTON, SCALE_BUTTON, SCENE_BUTTONS, SELECT_BUTTON, SEQ_HZ, SEQ_PAGES)


class SeqMixin:
    def _release_seq_holds(self):
        """Held steps are forgotten, held pads let go of their voice (or the bar would stay lit)."""
        bt = self.beat_time() or 0.0
        for key, track in list(self.held_bars.items()):
            self.seq.release(track, key, bt)
        self.held_bars.clear()
        self.held_steps.clear()

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

    def seq_step(self):
        """One clock tick: feed beat time to the sequencer, send changed levels, handle Repeat, write a
        due chases.yaml save. seq_loop calls it 100 times a second; tests call it directly."""
        if self.quitting:
            return
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
                if self.seq.landed is not None:            # an Apply landed at the bar: preview it
                    self.seq.landed = None
                    self._preview_reload()
                self.preview_tick(bt)                      # PRESETS draft, display only
        for (track, bar), v in changed.items():
            self.engine.set_level(track, bar, v)
        self.engine.flush()                                # held-back levels, routing changes
        with self.lock:
            data = self.seq.take_dirty()                   # a deferred chases.yaml save that is due
        if data is not None:
            self.seq.write(data)                           # the file write stays outside the lock

    def seq_loop(self):
        """100 Hz clock thread."""
        last_err = 0.0
        while True:
            try:
                self.seq_step()
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

    def _seq_pad(self, ij, velocity, down):
        i, j = ij
        with self.lock:
            bt = self.beat_time() or 0.0
            seq = self.seq
            if not down:                                           # a release ends what the press began, in any menu
                if i < 4:
                    self.held_steps.discard(i * 8 + j)
                elif i > 4:
                    key = pad_key(self.bar_index(i, j))
                    if key in self.held_bars:
                        seq.release(self.held_bars.pop(key), key, bt)
                return
            if self.mapping() and self._map_pad(ij, True):
                return
            if i < 4 and self.presets_menu():                      # PRESETS: the recipe grid
                self._preset_recipe_pad(i, j)
                return
            if i < 4:                                              # steps, for every selected pad
                step = i * 8 + j
                if step >= seq.pattern.length:
                    return
                keys = self.sel_keys()
                if self.delete_held:
                    seq.remove_steps(keys, step)
                    return
                self.held_steps.add(step)
                seq.toggle_steps(keys, step, 1.0 if self.accent else max(0.05, velocity / 127.0))
            elif i == 4:                                           # patterns
                p = self.pattern_index(j)
                if self.presets_menu() and self._preset_pattern_pad(p):
                    return
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
        if name in LOWER_ROW and self.overlay is None and self.presets_menu():
            if down:                                               # presets, not tracks: a preset is all four
                with self.lock:
                    self._preset_button(LOWER_ROW.index(name))
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
        if self.presets_menu():
            self._preset_turn(name, inc)
            return
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
                cur = DIRECTIONS.index(seq.pattern.direction) if seq.pattern.direction in DIRECTIONS else 0
                i = (cur + (1 if acc > 0 else -1)) % len(DIRECTIONS)
                seq.set_direction(DIRECTIONS[i])
                acc = 0
            self.choice_acc["seqdir"] = acc
        elif name == "Length":
            seq.set_length(seq.pattern.length + inc)
        elif name == "Level":
            tr.level = max(0.0, min(1.0, tr.level + inc * 0.01 * fine))
        seq.save_later()

    def turn_swing(self, inc):
        with self.lock:
            p = self.seq.pattern
            p.swing = max(0.0, min(1.0, p.swing + inc * 0.02))
            self.seq.save_later()

    def _seq_pad_colors(self):
        if self.mapping():
            return self._map_pad_colors()
        grid = {}
        seq = self.seq
        pos = seq.position(self.beat_time() or 0.0)
        keys = self.sel_keys()
        track_steps = [seq.pattern.tracks[seq.track].steps.get(key, {}) for key in keys]
        lit = {key for (t, key), v in seq.levels.items()          # pads sounding on this track only
               if t == seq.track and v > 0.02}
        blink = int(time.time() / BLINK) % 2 == 0
        presets = self.presets_menu()
        misfit = set()
        if presets:
            r, _ = self.current_rig()
            misfit = set(seq.mismatched(r.signature())) if r else set()
        for i in range(8):
            for j in range(8):
                color = "black"
                if i < 4 and presets:                                  # PRESETS: the recipe grid
                    color = self._recipe_pad_color(i, j)
                elif i < 4:                                            # steps: shades of red
                    step = i * 8 + j
                    if step < seq.pattern.length:
                        n_on = sum(1 for st in track_steps if step in st)
                        if step == pos:
                            color = "green"
                        elif n_on and n_on == len(track_steps):
                            lv = min(st[step][0] for st in track_steps if step in st)
                            color = "S_red" if lv >= 0.66 else "S_red_mid"
                        elif n_on:
                            color = "S_red_dim"                          # on for some selected pads
                        else:
                            color = "S_red_off"
                elif i == 4:                                           # patterns: shades of orange
                    p = self.pattern_index(j)
                    has = any(t.steps for t in seq.patterns[p].tracks)
                    t0 = self.map_blink.get(("pattern", p))
                    if t0 is not None and time.time() - t0 < 4 * MAP_BLINK:        # stored: blinks twice
                        color = "white" if int((time.time() - t0) / MAP_BLINK) in (0, 2) else "black"
                    elif presets and self._presets_pattern_color(p, misfit):
                        color = self._presets_pattern_color(p, misfit)
                    elif seq.pending == p:
                        color = "S_orange" if blink else "S_orange_off"               # queued
                    else:
                        color = "S_orange" if p == seq.current else ("S_orange_dim" if has else "S_orange_off")
                else:                                                  # pads: shades of yellow
                    k = self.bar_index(i, j)
                    key = pad_key(k)
                    if key in lit:
                        color = "S_yellow"
                    elif not self.engine.pad_assigned(seq.track, k):
                        color = "black"
                    elif k in self.sel_pads:
                        color = "S_yellow_mid"
                    else:
                        color = "S_yellow_dim"
                grid[(i, j)] = color
        return grid

    def _init_seq(self, cfg, rest):
        """SEQ, MAPPING and PRESETS state, kept on the Bridge (called from Bridge.__init__)."""
        sc = cfg.get("sequencer") or {}
        self.seq = Sequencer(paths.data_file("chases.yaml", cfg.get("chases_file")),
                             n_tracks=int(sc.get("tracks", 4)))
        self.engine = PluginEngine(rest, lambda: self.comp, self.refresh_comp, self._send_level,
                                   store=self.seq, send_param=self.sender.param)
        self.engine.fold = bool(sc.get("fold_tracks", True))
        self.sel_pads = {0}        # selected pads (0-based); steps edit all of them
        self.multi = False         # Select latched: pad press adds / removes instead of replacing
        self.select_held = self.select_used = False
        self.side = "groups"       # buttons right of the pads in SEQ: "groups" (Layout) or "grid" (Scale)
        self.cur_group = None      # ("track" | "global", button) last stored / recalled (lit while it matches)
        self.map_armed = None      # MAPPING: fixture index picked with Select, waiting for a pad
        self.map_focus = None      # MAPPING: pad last pressed (its fixture is shown)
        self.map_blink = {}        # MAPPING: ("fx", index) / ("pad", k) -> time of the confirming double blink
        self.map_page = 0          # MAPPING: which 32 fixtures are on the pads
        self.preset_slot = 0       # PRESETS: the slot being edited and previewed (row 5 selects it)
        self.drafts = {}           # PRESETS: slot -> {"recipe", "seed", "macros"} not applied yet (never saved)
        self.load_armed = None     # PRESETS: time of the first Load-bank press (the second within LOAD_DOUBLE loads)
        self.preview = Sequencer(n_tracks=int(sc.get("tracks", 4)))   # plays the draft for the display only
        self.preview_levels = {}   # pad index -> level from the preview sequencer
        self.confirm = None        # PRESETS question: {"kind": "overwrite", "slot", "recipe"}
        self.refit_offer = False   # the rig changed during the show: PRESETS offers a re-fit
        self._rig_cache = (None, None, None)   # (inputs, Rig or None, error text)
        self._rig_checked = (None, 0.0)        # (mapping, time) of the last look at the preset file
        self.rig_sig_seen = None   # rig signature last checked
        self.repeat = False
        self.accent = False
        self.delete_held = self.browse_held = self.fixed_len_held = False
        self.browse_used = False
        self.held_steps = set()    # step indices held on the pads
        self.held_bars = {}        # pad key -> track, while a pad is held
        self.dup_src = None        # Duplicate + pattern: source pattern
        self.seq_note = ""         # last setup / load message (shown on the display)
        self.last_grid_step = None

    def _track_warning(self, t):
        r = self.engine.route(t)
        if self.engine.parked(t):                       # its Bar Chaser sits on a clip that is not playing
            return f"T{t + 1}'s Bar Chaser clip is not playing" + (f" · plays on T{r + 1}'s layer" if r is not None else "")
        if r is not None:
            return f"T{t + 1} plays on T{r + 1}'s layer (no own Bar Chaser) · Shift + Note adds one"
        return f"T{t + 1}: no Bar Chaser — Shift + Note on a layer"

    def _seq_snapshot(self):
        """The display's SEQ part: knobs, pattern, pads, envelope, menu sub-state."""
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
        knobs = [] if self.presets_menu() else [(n,) + every[n] for n in SEQ_PAGES.get(self.menus["seq"], [])]
        pads = [(k + 1, self.engine.short(sq.track, self.engine.pad_name(sq.track, k)) or "—")
                for k in sorted(self.sel_pads)]
        mapping = self._mapping_snapshot() if self.mapping() else None
        ready = self.engine.ready(sq.track)
        presets = self._presets_snapshot() if self.presets_menu() else None
        return {"knobs": knobs, "menu": self.menus["seq"], "mapping": mapping, "presets": presets,
               "track": sq.track, "pattern": sq.pattern.name, "running": sq.running,
               "pos": sq.position(self.beat_time() or 0.0), "length": sq.pattern.length, "grid": sq.grid,
               "pads": pads, "layer": self.engine.layer_name(sq.track), "ready": ready,
               "env": {"attack": e.attack, "decay": e.decay, "sustain": e.sustain, "release": e.release,
                       "gate": tr.gate},
               "pending": sq.pending, "swing": sq.pattern.swing,
               "group": self.group_label(*self.current_group()) if self.current_group() else None,
               "multi": self.multi or self.select_held,
               "select_held": self.select_held, "side": self.side,
               "warning": "" if ready else self._track_warning(sq.track)}
