"""SEQ PRESETS menu (BU3): the 16 recipes of the bank on a 4 x 4 pad grid (rows = Rumble / Machine /
Hard groove / Warehouse, columns = Minimal / Groove / Peak / Kick out), five macro knobs and a seed
knob, a draft per pattern slot that plays in a preview sequencer for the display only, and Apply /
Revert / Re-fit / Load bank on the buttons below the display. A mixin of Bridge; the state is set up
in SeqMixin._init_seq. Spec: docs/specs/2026-09-28-seq-banks-dramaturgy-macros-preview.md (as built:
its section 6 with the recipe grid on the pads and the actions on buttons 1-4)."""

from __future__ import annotations

import os
import random
import sys
import time
import traceback

import banks
import rig as rigmod
from chaser_engine import pad_from_key
from sequencer import BAR_BEATS, N_PATTERNS, _pattern_from_dict, _pattern_to_dict, phrase_beats
from constants import (CONFIRM_NO, CONFIRM_YES, LOAD_DOUBLE, MACRO_FINE, MACRO_STEP, PRESET_ACTIONS,
                       PRESET_BANK, SEQ_PAGES)

BD_APPLY, BD_REVERT, BD_REFIT, BD_LOAD = 0, 1, 2, 3
MACRO_LABELS = {"Strobe": "strobe", "Density": "density", "Speed": "speed", "Spread": "spread", "Accent": "accent"}
GRID_COLS = 4                       # recipes per bank row on the pads


class PresetsMixin:
    # ---- rig -------------------------------------------------------------------- #
    def current_rig(self):
        """(Rig, "") or (None, error) from the shared pad mapping and the bar geometry: the effect's
        `Geometry` parameter when it has one (then no file is read here, and a remote Arena works), else
        the Advanced Output preset file. Rebuilt only when the mapping, the geometry or the config change."""
        sc = self.cfg.get("sequencer") or {}
        folder = sc.get("preset_folder") or rigmod.PRESET_FOLDER
        values = self.engine.mapping()
        now = time.time()
        if self._rig_checked[0] == values and now - self._rig_checked[1] < 1.0:
            return self._rig_cache[1], self._rig_cache[2]           # the preset file is looked at once a second
        self._rig_checked = (values, now)
        geo = self.engine.geometry()                           # published by the effect (Bar Chaser 2026-09-28+)
        path = mtime = None
        if geo is None:                                        # older effect: read the preset file here
            try:
                path = rigmod.resolve_preset(self.engine.preset_text(), folder)
                mtime = os.path.getmtime(path) if path else None
            except OSError as e:                               # a preset file vanished between listing and reading
                self._rig_cache = (None, None, f"Advanced Output preset not readable: {e}")
                return None, self._rig_cache[2]
        override = dict(sc.get("rig") or {})
        if self.seq.kick_pads:
            override["kick"] = list(self.seq.kick_pads)
        key = (tuple(values), path, mtime, repr(override), repr(sorted(geo[2].items())) if geo else None)
        if self._rig_cache[0] == key:
            return self._rig_cache[1], self._rig_cache[2]
        rects = dict(geo[2]) if geo else {}
        if geo is None and mtime is not None:
            try:
                rects = rigmod.preset_rects(path)
            except Exception as e:                                 # unreadable preset: roles by position
                print(f"[presets] can't read {path}: {e}", file=sys.stderr)
        pads = [rigmod.PadInfo(k + 1, v, rects.get(v)) for k, v in enumerate(values) if v]
        try:
            r, err = rigmod.build_rig(pads, override), ""
        except rigmod.RigError as e:
            r, err = None, str(e)
        self._rig_cache = (key, r, err)
        return r, err

    def check_rig(self, loaded=False):
        """Do the preset patterns still fit the rig? A newly loaded composition re-fits them at once;
        a change during the show (MAPPING, a new Advanced Output preset) is reported and Re-fit
        (button 3 in PRESETS) blinks, because a re-fit moves every hit."""
        r, _ = self.current_rig()
        sig = r.signature() if r else None
        if sig == self.rig_sig_seen and not loaded:
            return
        self.rig_sig_seen = sig
        self._preview_reload()                                     # the draft on the new bars
        todo = self.seq.mismatched(sig) if r else []
        if not todo:
            self.refit_offer = False
            return
        if loaded:
            done = self.seq.refit(sig, lambda d: banks.refit(d, r))
            self.refit_offer = False
            self.note_msg(f"Rig changed: {len(done)} preset pattern{'s' * (len(done) != 1)} re-fitted")
        else:
            self.refit_offer = True
            self.note_msg("Rig changed · Re-fit = button 3 in PRESETS")

    # ---- state helpers ------------------------------------------------------------ #
    def presets_menu(self):
        return self.view == "seq" and self.menus["seq"] == "seq_presets"

    def question(self):
        """The NO / YES question the PRESETS menu shows, or None."""
        return self.confirm

    @staticmethod
    def _new_seed():
        return random.randrange(1, 1000000)

    def _ensure_draft(self, slot):
        """The slot's draft; a preset pattern without one gets a draft made from its source. None for
        an own (hand-made) pattern: those have no recipe to bend."""
        d = self.drafts.get(slot)
        if d:
            return d
        src = self.seq.slot_pattern(slot).source
        if not src or "recipe" not in src:
            return None
        d = {"recipe": src["recipe"], "seed": int(src.get("seed", 1)), "macros": banks.clean_macros(src.get("macros"))}
        self.drafts[slot] = d
        return d

    def _slot_name(self, i):
        n = self.seq.slot_pattern(i).name
        return f"P{i + 1}" + ("" if n == f"P{i + 1}" else f" ({n})")

    # ---- pads ----------------------------------------------------------------------- #
    def _preset_recipe_pad(self, i, j):
        """Rows 1-4, columns 1-4: recipe (bank row i, energy column j) becomes the draft of the
        selected slot, with a fresh seed; the macros of the previous draft (or of the slot's preset)
        carry over so the knobs keep their meaning while recipes are tried."""
        if j >= GRID_COLS:
            return
        recipes = banks.BANKS[PRESET_BANK]
        n = i * GRID_COLS + j
        if n >= len(recipes):
            return
        r, err = self.current_rig()
        if r is None:
            self.note_msg(err)
            return
        slot = self.preset_slot
        prev, src = self.drafts.get(slot), self.seq.slot_pattern(slot).source
        macros = dict(prev["macros"]) if prev else banks.clean_macros(src.get("macros") if src else None)
        self.drafts[slot] = {"recipe": recipes[n], "seed": self._new_seed(), "macros": macros}
        self.confirm = self.load_armed = None
        self._preview_reload()
        self.note_msg(f"P{slot + 1} ← {banks.RECIPES[recipes[n]][0]}  ·  Apply = button 1")

    def _preset_pattern_pad(self, p):
        """Row 5 in PRESETS: select the slot to edit and preview, without switching the live pattern.
        The selected pad again = the normal switch. True = handled."""
        if self.delete_held or self.paste_held or p == self.preset_slot:
            return False
        self.preset_slot = p
        self.confirm = self.load_armed = None
        self._preview_reload()
        return True

    # ---- knobs ---------------------------------------------------------------------- #
    def _preset_turn(self, name, inc):
        """K1-K5 macros, K6 seed (turn = reshuffle, Shift = next / previous seed), K7 bank.
        Select held + a macro knob = the same macro on every preset slot."""
        slot = self.preset_slot
        if name == "Bank":
            self.note_msg(f"Bank: {PRESET_BANK.capitalize()} (the only one so far)")
            return
        if name not in MACRO_LABELS and name != "Seed":
            return
        if self.select_held and name in MACRO_LABELS:
            self.select_used = True
            targets = [i for i in range(N_PATTERNS) if i in self.drafts or self.seq.slot_pattern(i).source]
        else:
            targets = [slot]
        done = []
        for s in targets:
            d = self._ensure_draft(s)
            if d is None:
                continue
            if name == "Seed":
                d["seed"] = (d["seed"] + inc - 1) % 999999 + 1 if self.shift else self._new_seed()
            else:
                key = MACRO_LABELS[name]
                step = MACRO_FINE if self.shift else MACRO_STEP
                d["macros"][key] = max(0.0, min(1.0, round(d["macros"][key] + inc * step, 4)))
            done.append(s)
        if not done:
            self.note_msg("own pattern · pick a recipe on the top-left pads first")
            return
        self.confirm = self.load_armed = None
        self._preview_reload()
        if len(done) > 1:
            d = self.drafts[slot if slot in done else done[0]]
            self.note_msg(f"{name} {d['macros'][MACRO_LABELS[name]]:.2f} → all ({len(done)} slots)")

    # ---- buttons below the display ----------------------------------------------------- #
    def _preset_button(self, k):
        q = self.confirm
        if q:
            if k == CONFIRM_YES:
                self.confirm = None
                self._apply(q["slot"], force=True)
            elif k == CONFIRM_NO:
                self.confirm = None
                self.note_msg("Kept")
            return
        if k != BD_LOAD:
            self.load_armed = None
        if k == BD_APPLY:
            self._apply(self.preset_slot)
        elif k == BD_REVERT:
            self._revert()
        elif k == BD_REFIT:
            self._refit_now()
        elif k == BD_LOAD:
            self._load_bank()

    def _apply(self, slot, force=False):
        """The draft becomes the slot's pattern: the same recipe keeps the user's track Levels
        (banks.set_macros), another recipe is realised fresh. An own pattern with steps asks first.
        The live, running pattern changes at the next bar."""
        d = self.drafts.get(slot)
        if not d:
            self.note_msg("nothing to apply: pick a recipe or turn a knob first")
            return
        r, err = self.current_rig()
        if r is None:
            self.note_msg(err)
            return
        pat = self.seq.slot_pattern(slot)                       # counting an Apply that waits for the bar
        if not force and pat.source is None and not self.seq.is_empty(slot):
            self.confirm = {"kind": "overwrite", "slot": slot}
            return
        new = self._draft_pattern(slot, d, r)
        when = self.seq.apply(slot, new)
        self.drafts.pop(slot, None)
        self.map_blink = {("pattern", slot): time.time()}
        bars = phrase_beats(self.seq.slot_pattern(slot)) // BAR_BEATS
        wait = "  ·  at the next bar" if bars == 1 else f"  ·  at the next {bars}-bar phrase"
        self.note_msg(f"P{slot + 1} = {new['name']}" + (wait if when == "bar" else ""))
        self._preview_reload()

    def _draft_pattern(self, slot, d, r):
        """What applying draft d to the slot gives (the preview plays the same): the slot's own recipe
        keeps its track Levels, length and grid (banks.set_macros); another recipe is realised fresh
        (1/16, 32 steps)."""
        pat = self.seq.slot_pattern(slot)
        src = pat.source
        if src and src.get("recipe") == d["recipe"]:
            return banks.set_macros(_pattern_to_dict(pat), r, d["macros"], seed=d["seed"])
        return banks.realize(d["recipe"], r, d["seed"], PRESET_BANK, d["macros"])

    def _revert(self):
        if self.drafts.pop(self.preset_slot, None) is None:
            self.note_msg("no draft to revert")
            return
        self.note_msg(f"P{self.preset_slot + 1}: draft dropped")
        self._preview_reload()

    def _refit_now(self):
        r, err = self.current_rig()
        if r is None:
            self.note_msg(err)
            return
        done = self.seq.refit(r.signature(), lambda d: banks.refit(d, r))
        self.refit_offer = False
        self.note_msg(f"{len(done)} preset pattern{'s' * (len(done) != 1)} re-fitted" if done else "every preset pattern fits this rig")
        self._preview_reload()

    def _load_bank(self):
        """Twice within LOAD_DOUBLE s: the whole bank into P1-P16. Own patterns (hand-made, with
        steps) are kept, and named."""
        now = time.time()
        if self.load_armed is None or now - self.load_armed > LOAD_DOUBLE:
            self.load_armed = now
            self.note_msg("Load bank: press again to fill P1–P16 (own patterns are kept)")
            return
        self.load_armed = None
        r, err = self.current_rig()
        if r is None:
            self.note_msg(err)
            return
        kept = self.seq.load_bank(banks.realize_bank(r, PRESET_BANK))
        self.drafts.clear()
        msg = f"{PRESET_BANK.capitalize()} bank loaded into P1–P16"
        if kept:
            msg += "  ·  kept: " + ", ".join(f"P{i + 1}" for i in kept)
        self.note_msg(msg)
        self._preview_reload()

    # ---- preview: the draft plays in its own sequencer, for the display only ---------------- #
    def _preview_pattern_dict(self):
        d = self.drafts.get(self.preset_slot)
        if d:
            r, _ = self.current_rig()
            if r is not None:
                return self._draft_pattern(self.preset_slot, d, r)
        return _pattern_to_dict(self.seq.slot_pattern(self.preset_slot))

    def _preview_reload(self):
        if not self.presets_menu():
            return
        try:
            d = self._preview_pattern_dict()
        except Exception:
            traceback.print_exc()
            return
        pv = self.preview
        pv.patterns[0] = _pattern_from_dict(d)
        pv.voices.clear()
        pv.levels.clear()
        self.preview_levels = {}
        pv.switch_pattern(0, self.beat_time() or 0.0, now=True)

    def preview_tick(self, bt):
        """From seq_loop: while PRESETS is open the preview runs on the beat clock and fills
        preview_levels (pad index -> level, max over tracks). Nothing is sent anywhere. Leaving
        the menu stops and clears it."""
        pv = self.preview
        if self.presets_menu():
            if bt is None:
                return
            if not pv.running:
                self._preview_reload()
                pv.start(bt)
            if pv.tick(bt):
                levels = {}
                for (_t, key), v in pv.levels.items():
                    k = pad_from_key(key)
                    if k is not None and v > 0:
                        levels[k] = max(levels.get(k, 0.0), v)
                self.preview_levels = levels
        elif pv.running or pv.voices or self.preview_levels:
            pv.stop()
            pv.voices.clear()
            pv.levels.clear()
            self.preview_levels = {}

    # ---- LEDs ------------------------------------------------------------------------ #
    def _recipe_pad_color(self, i, j):
        recipes = banks.BANKS[PRESET_BANK]
        n = i * GRID_COLS + j
        if j >= GRID_COLS or n >= len(recipes):
            return "black"
        d, src = self.drafts.get(self.preset_slot), self.seq.slot_pattern(self.preset_slot).source
        if d and d["recipe"] == recipes[n]:
            return "white"
        if not d and src and src.get("recipe") == recipes[n]:
            return "light_gray"
        return "dark_gray"

    def _presets_pattern_color(self, p, misfit):
        """Row 5 in PRESETS: the edited slot white, other slots with a draft light grey, patterns made
        for another rig pink; None = the usual pattern-row colour."""
        if p == self.preset_slot:
            return "white"
        if p in self.drafts:
            return "light_gray"
        if p in misfit:
            return "pink"
        return None

    def _presets_lower_color(self, k, blink):
        if self.confirm:
            return "red" if k == CONFIRM_NO else "green" if k == CONFIRM_YES else "black"
        draft = self.preset_slot in self.drafts
        rig_ok = self.current_rig()[0] is not None
        if k == BD_APPLY:
            return "green" if draft and rig_ok else "black"
        if k == BD_REVERT:
            return "dark_gray" if draft else "black"
        if k == BD_REFIT:
            if self.refit_offer:
                return "white" if blink else "black"
            return "dark_gray" if rig_ok else "black"
        if k == BD_LOAD:
            if self.load_armed is not None and time.time() - self.load_armed < LOAD_DOUBLE:
                return "white" if blink else "black"
            return "dark_gray" if rig_ok else "black"
        return "black"

    @staticmethod
    def _rig_line(r):
        """What the recipes see: which bars carry the kick, and why."""
        n, nums = len(r.pads), ", ".join(str(p) for p in r.D)
        if r.shared:
            return f"Rig: {n} bars · every bar takes the kick (your choice)"
        if r.source == "push":
            return f"Rig: {n} bars · kick bars you chose: {nums}"
        if r.virtual:
            return f"Rig: {n} alike bars · kick bars by position: {nums} · Shift + pad in MAPPING to choose"
        if r.source == "preset":
            return f"Rig: {n} fixtures · {len(r.D)} long, {len(r.S)} short · from the Advanced Output"
        return f"Rig: {n} bars · kick bars from config.yaml: {nums}"

    # ---- display ---------------------------------------------------------------------- #
    def _presets_snapshot(self):
        sq, slot = self.seq, self.preset_slot
        pat = sq.slot_pattern(slot)
        r, err = self.current_rig()
        d, src = self.drafts.get(slot), pat.source
        macros = d["macros"] if d else (banks.clean_macros(src.get("macros")) if src else None)
        seed = d["seed"] if d else (src.get("seed") if src else None)
        recipe = d["recipe"] if d else (src.get("recipe") if src else None)
        knobs = []
        for label in SEQ_PAGES["seq_presets"]:
            if label in MACRO_LABELS:
                v = macros[MACRO_LABELS[label]] if macros else None
                knobs.append((label, f"{v:.2f}" if v is not None else "—", v))
            elif label == "Seed":
                knobs.append((label, str(seed) if seed is not None else "—", None))
            elif label == "Bank":
                knobs.append((label, PRESET_BANK.capitalize(), None))
        knobs += [("", "", None)] * (8 - len(knobs))
        name = banks.RECIPES[recipe][0] if recipe in banks.RECIPES else None
        q = self.confirm
        if q:
            dq = self.drafts.get(q["slot"]) or {}
            title = f"Overwrite {self._slot_name(q['slot'])} (own pattern) with {banks.RECIPES.get(dq.get('recipe'), ('?',))[0]}?"
        elif d:
            title = f"{self._slot_name(slot)} · {name} · seed {seed} · draft"
        elif src:
            title = f"{self._slot_name(slot)} · {name} · seed {seed}"
        else:
            title = f"{self._slot_name(slot)} · own pattern · pick a recipe on the top-left pads"
        misfit = set()
        if r is None:
            rig_line = err
        else:
            rig_line = self._rig_line(r)
            misfit = set(sq.mismatched(r.signature()))
            if misfit:
                rig_line += "  ·  made for another rig: " + ", ".join(f"P{i + 1}" for i in sorted(misfit))
        if self.load_armed is not None and time.time() - self.load_armed < LOAD_DOUBLE:
            info = "Load bank: press button 4 again to fill P1–P16 (own patterns are kept)"
        elif self.refit_offer:
            info = "Rig changed: Re-fit (button 3) moves every preset hit onto the new bars"
        else:
            missing = [t for t in range(sq.n_tracks) if not self.engine.ready(t)]
            parts = []
            for t in missing:
                r_t = self.engine.route(t)
                parts.append(f"T{t + 1} plays on T{r_t + 1}'s layer" if r_t is not None else f"T{t + 1} has no layer")
            info = ("no own Bar Chaser: " + ", ".join(parts)) if missing else \
                f"live: {self._slot_name(sq.current)}" + ("  ·  RUN" if sq.running else "")
        if q:
            labels = [""] * 8
            labels[CONFIRM_NO], labels[CONFIRM_YES] = "NO", "YES"
            active = [False] * 8
        else:
            labels = list(PRESET_ACTIONS) + [""] * (8 - len(PRESET_ACTIONS))
            active = [bool(d), bool(d), bool(misfit) or self.refit_offer, r is not None] + [False] * 4
        strip = [(role, self.preview_levels.get(p - 1, 0.0)) for p, role in zip(r.pads, r.roles)] if r else []
        return {"knobs": knobs, "title": title, "rig": rig_line, "info": info, "labels": labels, "active": active,
                "question": bool(q), "strip": strip, "draft": bool(d),
                "pos": self.preview.position(0.0) if self.preview.running else None}
