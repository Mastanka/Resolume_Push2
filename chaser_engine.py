"""Engine for the step sequencer: the "Bar Chaser" FFGL effect on each texture layer.

Levels 0-1 become the effect's `Level 1` … `Level 24` parameters; which slice a pad means is
chosen in the effect's own panel (`Pad n` dropdowns) and read back here for the display."""

from __future__ import annotations

import time
from resolume_api import is_param, text

MIN_DT = 0.02          # s between two sends of the same layer (50/s); 30/s when many change
MANY = 16


# --------------------------------------------------------------------------- #
# Plugin engine: the "Bar Chaser" FFGL effect on the texture layer itself
# --------------------------------------------------------------------------- #

EFFECT = "Bar Chaser"
NPADS = 24
UNASSIGNED = "—"          # the effect's "—" dropdown entry


def pad_key(k):
    """Bar name used in patterns for pad index k (0-based): 'pad 1' … 'pad 24'."""
    return f"pad {k + 1}"


def pad_from_key(name):
    """'pad 5' → 4, or an int passed through; None when it isn't a pad key."""
    if isinstance(name, int):
        return name
    if isinstance(name, str) and name.startswith("pad ") and name[4:].isdigit():
        return int(name[4:]) - 1
    return None


class PluginEngine:
    """Levels go to the 'Level n' parameters of every Bar Chaser instance whose Track matches."""

    def __init__(self, rest, get_comp, refresh, send_level):
        self.rest, self.get_comp, self.refresh, self.send_level = rest, get_comp, refresh, send_level
        self._last = {}            # level param id -> (time, quantised value)
        self._cache_comp = None
        self._cache = []

    # ---- finding instances ------------------------------------------------ #
    @staticmethod
    def _effects_of(comp):
        """(layer index, clip index or None, effect json) for every effect in the composition."""
        for L, layer in enumerate(comp.get("layers") or [], 1):
            for fx in (layer.get("video") or {}).get("effects") or []:
                yield L, None, fx
            for C, clip in enumerate(layer.get("clips") or [], 1):
                for fx in ((clip or {}).get("video") or {}).get("effects") or []:
                    yield L, C, fx

    def instances(self, comp=None):
        """[{'track': 0-based, 'layer': L, 'clip': C or None, 'fx': effect json}] for every Bar Chaser."""
        comp = comp or self.get_comp() or {}
        if comp is self._cache_comp:
            return self._cache
        out = []
        for L, C, fx in self._effects_of(comp):
            if fx.get("name") != EFFECT:
                continue
            track = (fx.get("params") or {}).get("Track") or {}
            t = str(track.get("value", "1"))
            out.append({"track": int(t) - 1 if t.isdigit() else 0, "layer": L, "clip": C, "fx": fx})
        self._cache_comp, self._cache = comp, out
        return out

    def ready(self, track):
        return any(i["track"] == track for i in self.instances())

    def layers_of(self, track):
        return sorted({i["layer"] for i in self.instances() if i["track"] == track})

    def _first(self, track):
        return next((i for i in self.instances() if i["track"] == track), None)

    def pad_name(self, track, k):
        """The slice assigned to pad k on the track's first instance, '' when unassigned or no instance."""
        inst = self._first(track)
        if inst is None:
            return ""
        p = (inst["fx"].get("params") or {}).get(f"Pad {k + 1}") or {}
        v = text(p.get("value")) if isinstance(p.get("value"), dict) else str(p.get("value", ""))
        return "" if v in ("", UNASSIGNED) else v

    def pad_assigned(self, track, k):
        return bool(self.pad_name(track, k))

    def layer_name(self, track):
        inst = self._first(track)
        if inst is None:
            return ""
        layer = (self.get_comp() or {}).get("layers", [])[inst["layer"] - 1]
        return text(layer.get("name"))

    # ---- setup ------------------------------------------------------------ #
    def add_to_layer(self, L):
        """Add a Bar Chaser to layer L unless it has one already. Returns the HTTP status (204 = added)."""
        if any(i["layer"] == L and i["clip"] is None for i in self.instances()):
            return 200
        code = self.rest.add_effect(L, EFFECT)
        self.refresh()
        return code

    def set_track(self, L, n):
        """Make layer L's instance answer to track n (1-based); adds the effect first if needed."""
        if not any(i["layer"] == L and i["clip"] is None for i in self.instances()):
            if self.add_to_layer(L) not in (200, 204):
                return False
        inst = next((i for i in self.instances() if i["layer"] == L and i["clip"] is None), None)
        p = (inst["fx"].get("params") or {}).get("Track") if inst else None
        if not is_param(p):
            return False
        self.rest.set_param(p["id"], {"value": str(n)})
        self.refresh()
        return True

    # ---- levels ------------------------------------------------------------ #
    def _pids(self, track, k):
        return [p["id"] for i in self.instances() if i["track"] == track
                for p in [((i["fx"].get("params") or {}).get(f"Level {k + 1}") or {})] if is_param(p)]

    def set_level(self, track, pad, value, now=None):
        k = pad_from_key(pad)
        if k is None or not 0 <= k < NPADS:
            return
        now = now or time.time()
        q = round(max(0.0, min(1.0, float(value))) * 255) / 255
        busy = sum(1 for t, _ in self._last.values() if now - t < 0.1) > MANY
        for pid in self._pids(track, k):
            last = self._last.get(pid)
            if last and last[1] == q:
                continue
            if last and q != 0.0 and now - last[0] < (0.033 if busy else MIN_DT):
                continue
            self._last[pid] = (now, q)
            self.send_level(pid, q)

    def all_dark(self):
        for inst in self.instances():
            for k in range(NPADS):
                p = (inst["fx"].get("params") or {}).get(f"Level {k + 1}")
                if is_param(p):
                    self._last.pop(p["id"], None)
                    self.send_level(p["id"], 0.0)
                    self._last[p["id"]] = (time.time(), 0.0)
