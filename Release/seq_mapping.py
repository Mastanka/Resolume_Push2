"""SEQ MAPPING menu (BU4): which fixture each of the 24 pads lights. A mixin of Bridge."""

from __future__ import annotations

import math
import time

from chaser_engine import UNASSIGNED, pad_key
from constants import (BLINK, MAP_BLINK, MAP_FIXTURES)


class MappingMixin:
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
        if self.shift:                                             # Shift + pad = kick bar on / off
            self._toggle_kick(k)
            return True
        if self.delete_held:
            if eng.set_pad(k, UNASSIGNED):
                self.map_blink = {("pad", k): time.time()}
                self.note_msg(f"Pad {k + 1}: no fixture")
                self.check_rig()
            return True
        if self.map_armed is not None and self.map_armed < len(fx):
            f = self.map_armed
            if eng.set_pad(k, fx[f]["name"]):
                now = time.time()
                self.map_blink = {("fx", f): now, ("pad", k): now}
                self.note_msg(f"Pad {k + 1} = {fx[f]['label']}")
                self.check_rig()
            else:
                self.note_msg("No Bar Chaser to map: Shift + Note on a layer")
            self.map_armed, self.map_focus = None, k
            if self.select_held:
                self.select_used = True
            return True
        self.map_focus = k
        return False                                               # select + flash as usual

    def _toggle_kick(self, k):
        """Pad k (0-based) becomes a kick bar for the bank recipes, or stops being one. The first
        choice replaces the automatic one; removing the last returns to automatic."""
        valid = {p for p in self.seq.kick_pads if self.engine.pad_name(self.seq.track, p - 1)}
        if not self.engine.pad_name(self.seq.track, k):
            if valid != set(self.seq.kick_pads):                   # forget kick bars that lost their fixture
                self.seq.kick_pads = sorted(valid)
                self.seq.save()
            self.note_msg(f"Pad {k + 1} has no fixture")
            return
        kick = valid ^ {k + 1}
        self.seq.kick_pads = sorted(kick)
        self.seq.save()
        self._rig_checked = (None, 0.0)                            # look at the rig again now
        r, _ = self.current_rig()
        if not kick:
            self.note_msg("Kick bars: automatic again")
        elif r is not None and r.shared:
            self.note_msg("Kick bars: every bar")
        else:
            self.note_msg("Kick bars: " + ", ".join(str(p) for p in self.seq.kick_pads))
        self.check_rig()

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
        kick = set()
        if self.shift:
            r, _ = self.current_rig()
            kick = set(r.D) if r is not None else set(self.seq.kick_pads)

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
                    color = "S_orange"                                 # the middle row is orange in every SEQ menu
                elif self.shift:                                       # Shift: the roles the recipes see
                    k = self.bar_index(i, j)
                    color = "black" if not values[k] else ("S_red" if kick and k + 1 in kick else "S_yellow_dim")
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

    def _mapping_snapshot(self):
        """The display's MAPPING part."""
        fxl = self.engine.fixtures(self.seq.track)
        names = [self.engine.pad_name(self.seq.track, k) for k in range(24)]
        pages = max(1, math.ceil(len(fxl) / MAP_FIXTURES))
        if self.map_armed is not None and self.map_armed < len(fxl):
            f = fxl[self.map_armed]
            title, sub = f"{f['label']} picked  ·  press a pad to store it", f["name"]
        elif self.map_focus is not None:
            v = names[self.map_focus]
            title = f"Pad {self.map_focus + 1} = {self.engine.short(self.seq.track, v) or '—'}"
            sub = v or "no fixture"
        else:
            title, sub = "Hold Select + a fixture, then press a pad", \
                f"{len(fxl)} fixtures on the top rows" if fxl else "no fixtures: save the Advanced Output preset"
        return {"title": title, "sub": sub, "assigned": sum(1 for v in names if v),
                   "fixtures": len(fxl), "page": self.map_page, "pages": pages}
