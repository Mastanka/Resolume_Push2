"""SEQ PRESETS menu (BU3): the rig, the bank presets and re-fit. A mixin of Bridge."""

from __future__ import annotations

import banks
import os
import random
import rig as rigmod
import sys
import time

from constants import (CONFIRM_NO, CONFIRM_YES, PRESET_BANK)


class PresetsMixin:
    # ---- rig and presets (SEQ PRESETS menu) ------------------------------------ #
    def current_rig(self):
        """(Rig, "") or (None, error) from the shared pad mapping and the Advanced Output preset the
        effect reads. Rebuilt only when the mapping, the preset file or its time, or config change."""
        sc = self.cfg.get("sequencer") or {}
        folder = sc.get("preset_folder") or rigmod.PRESET_FOLDER
        values = self.engine.mapping()
        now = time.time()
        if self._rig_checked[0] == values and now - self._rig_checked[1] < 1.0:
            return self._rig_cache[1], self._rig_cache[2]           # the preset file is looked at once a second
        self._rig_checked = (values, now)
        path = rigmod.resolve_preset(self.engine.preset_text(), folder)
        try:
            mtime = os.path.getmtime(path) if path else None
        except OSError:
            mtime = None
        override = sc.get("rig") or {}
        key = (tuple(values), path, mtime, repr(override))
        if self._rig_cache[0] == key:
            return self._rig_cache[1], self._rig_cache[2]
        rects = {}
        if mtime is not None:
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
        a change during the show (MAPPING, a new Advanced Output preset) is reported and the PRESETS
        menu asks before re-fitting, because a re-fit moves every hit."""
        r, _ = self.current_rig()
        sig = r.signature() if r else None
        if sig == self.rig_sig_seen and not loaded:
            return
        self.rig_sig_seen = sig
        todo = self.seq.mismatched(sig) if r else []
        if not todo:
            self.refit_offer = False
            return
        if loaded:
            done = self.seq.refit(sig, lambda d: banks.refit(d, r))
            self.note_msg(f"Rig changed: {len(done)} preset pattern{'s' * (len(done) != 1)} re-fitted")
        else:
            self.refit_offer = True
            self.note_msg("Rig changed · re-fit in PRESETS")

    def presets_menu(self):
        return self.view == "seq" and self.menus["seq"] == "seq_presets"

    def question(self):
        """The YES / NO question the PRESETS menu shows, or None."""
        if self.confirm:
            return self.confirm
        if self.refit_offer and self.presets_menu() and not self.preset_pick:
            return {"kind": "refit"}
        return None

    def _store_preset(self, slot, recipe):
        """Realise a preset on the current rig and store it as pattern `slot` (a new seed each time)."""
        seq = self.seq
        r, err = self.current_rig()
        self.preset_pick, self.confirm = None, None
        if r is None:
            self.note_msg(err)
            return
        d = banks.realize(recipe, r, random.randrange(1, 1000000), PRESET_BANK)
        seq.store_pattern(slot, d)
        seq.grid = "1/16"                                          # presets are written for 1/16
        self.map_blink = {("pattern", slot): time.time()}
        self.note_msg(f"P{slot + 1} = {d['name']}")

    def _preset_button(self, k):
        """Buttons below the display in PRESETS: a preset (Shift = 9-16), or NO / YES to a question."""
        q = self.question()
        if q:
            if k == CONFIRM_YES and q["kind"] == "overwrite":
                self._store_preset(q["slot"], q["recipe"])
            elif k == CONFIRM_YES:                                 # re-fit
                r, err = self.current_rig()
                if r is None:
                    self.note_msg(err)
                else:
                    done = self.seq.refit(r.signature(), lambda d: banks.refit(d, r))
                    self.note_msg(f"{len(done)} preset pattern{'s' * (len(done) != 1)} re-fitted")
                self.refit_offer = False
            elif k == CONFIRM_NO:
                if q["kind"] == "refit":
                    self.refit_offer = False
                self.confirm = None
                self.note_msg("Kept")
            return
        items = banks.presets(PRESET_BANK)
        i = k + (8 if self.shift else 0)
        if i >= len(items):
            return
        rid = items[i][0]
        if self.preset_pick == rid:
            self.preset_pick = None                                # pressed again = cancel
            return
        r, err = self.current_rig()
        if r is None:
            self.note_msg(err)
            return
        self.preset_pick = rid

    def _preset_pattern_pad(self, slot):
        """A pattern pad while a preset is picked: store it there; a used slot asks first."""
        if self.seq.is_empty(slot):
            self._store_preset(slot, self.preset_pick)
        else:
            self.confirm = {"kind": "overwrite", "slot": slot, "recipe": self.preset_pick}

    def _presets_snapshot(self):
        sq = self.seq
        r, err = self.current_rig()
        items = banks.presets(PRESET_BANK)
        q = self.question()
        names = dict(items)
        def slot_name(i):
            n = sq.patterns[i].name
            return f"P{i + 1}" + ("" if n == f"P{i + 1}" else f" ({n})")
        if q and q["kind"] == "overwrite":
            title = f"Overwrite {slot_name(q['slot'])} with {names[q['recipe']]}?"
        elif q:
            n = len(sq.mismatched(r.signature())) if r else 0
            title = f"Rig changed: re-fit {n} preset pattern{'s' * (n != 1)} to it?"
        elif self.preset_pick:
            title = f"{names[self.preset_pick]}  ·  press a pattern pad to store it"
        else:
            title = "Pick a preset below, then a pattern pad"
        if r is None:
            rig_line = err
        else:
            geo = "long / short from the Advanced Output" if r.source == "preset" else \
                "all bars alike, long ones chosen by position" if r.virtual else f"roles from {r.source}"
            rig_line = f"Rig: {len(r.pads)} fixtures · {len(r.D)} long, {len(r.S)} short · {geo}"
            misfit = sq.mismatched(r.signature())
            if misfit:
                rig_line += "  ·  made for another rig: " + ", ".join(f"P{i + 1}" for i in misfit)
        cur = sq.pattern
        src = cur.source
        info = slot_name(sq.current) + (f" · preset {src.get('recipe')} · seed {src.get('seed')}" if src
                                        else " · own pattern")
        missing = [t + 1 for t in range(sq.n_tracks) if not self.engine.ready(t)]
        if missing:
            info += "  ·  no Bar Chaser layer: " + ", ".join(f"T{t}" for t in missing)
        page = items[8:] if self.shift else items[:8]
        if q:
            labels = [""] * 8
            labels[CONFIRM_NO], labels[CONFIRM_YES] = "NO", "YES"
        else:
            labels = [n for _, n in page] + [""] * (8 - len(page))
        picked = next((k for k, (rid, _) in enumerate(page) if rid == self.preset_pick), None)
        return {"title": title, "rig": rig_line, "info": info, "labels": labels, "picked": picked,
                "question": bool(q)}
