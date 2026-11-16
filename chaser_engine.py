"""Engine for the step sequencer: the "Bar Chaser" FFGL effect on each texture layer.

Levels 0-1 become the effect's `Level 1` … `Level 24` parameters; which slice a pad means is
chosen in the effect's own panel (`Pad n` dropdowns) and read back here for the display."""

from __future__ import annotations

import time
from collections import deque

from resolume_api import is_param, text
from rig import parse_geometry

MIN_DT = 0.02          # s between two sends of the same layer (50/s); 30/s when many change
MANY = 16
ROUTING_DT = 0.005     # s: set_level re-checks the routing at most this often (flush checks every tick)
IN_FLIGHT = 0.3        # s: a write younger than this may not show in a composition yet (no resend)
N_TRACKS = 4           # tracks a level can come from (the sequencer's T1-T4)


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


PENDING = 2.0          # s our own pad writes may take to show up before a difference counts as an edit
SEP = " / "            # the effect names a fixture "Lumiverse 3 / 1 - 423 141 RGB"


def fixture_list(options):
    """Fixtures offered by the effect's Pad dropdown, in Arena's list order:
    [{'label': 'L1F2', 'name': option to store, 'screen': 'Lumiverse 1'}].
    A screen (lumiverse) without fixture entries counts as one fixture (older Bar Chaser builds)."""
    opts = [o for o in (options or []) if isinstance(o, str) and o and o != UNASSIGNED]
    order, fixtures_of = [], {}
    for o in opts:
        screen = o.split(SEP, 1)[0] if SEP in o else o
        if screen not in fixtures_of:
            order.append(screen)
            fixtures_of[screen] = []
        if SEP in o:
            fixtures_of[screen].append(o)
    out = []
    for li, screen in enumerate(order, 1):
        for fi, name in enumerate(fixtures_of[screen] or [screen], 1):
            out.append({"label": f"L{li}F{fi}", "name": name, "screen": screen})
    return out


class PluginEngine:
    """Levels go to the 'Level n' parameters of every Bar Chaser instance whose Track matches.

    Pad mapping: one mapping (pad 1-24 -> fixture) shared by every instance and track, remembered in
    `store.pad_config` (chases.yaml). A loaded composition takes T1's mapping (else the first
    instance's) and gives it to the others; a pad changed in Arena on any instance becomes the
    mapping for all; a new instance gets it."""

    def __init__(self, rest, get_comp, refresh, send_level, store=None, send_param=None):
        self.rest, self.get_comp, self.refresh, self.send_level = rest, get_comp, refresh, send_level
        self.store = store             # has .pad_config [24 names] and .save()
        self.send_param = send_param   # (param id, body) -> None
        self._last = {}            # level param id -> (time, value) last sent
        self._pending = {}         # level param id -> value held back by the rate limit, sent by flush()
        self._levels = {}          # (track, pad index) -> level the sequencer wants now (> 0 only)
        self._feeds = {}           # level param id -> (pad index, tracks whose levels it shows)
        self._routing = None       # what _feeds was built for: ((effect id, track), …), fold
        self._routing_t = -1.0     # when routing was last checked (set_level checks at most every ROUTING_DT)
        self._by_source = {}       # (track, pad index) -> [level param ids it reaches]
        self._sends = deque()      # times of recent sends, for the rate limit's "busy" test
        self._reported = None      # {level param id: value} from the newest full composition, checked in flush
        self.fold = True           # a track without a Bar Chaser layer plays on its partner's (config sequencer.fold_tracks)
        self._cache = (None, [])   # (composition, [(layer, clip, effect)]): one tuple, so threads see a matching pair
        self._known = {}           # effect id -> (pads we expect, our writes pending until)
        self._comp_id = object()   # composition identity (its master param id): new id = loaded composition
        self._fx_cache = (None, None, [])   # (composition, track, fixture list)

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
        """[{'track': 0-based, 'layer': L, 'clip': C or None, 'fx': effect json}] for every Bar Chaser.
        Where the effects are is cached per composition; the Track is read live, because Arena reports
        a Track change as a parameter update patched into the same composition."""
        comp = comp or self.get_comp() or {}
        cached, found = self._cache
        if cached is not comp:
            found = [(L, C, fx) for L, C, fx in self._effects_of(comp) if fx.get("name") == EFFECT]
            self._cache = (comp, found)
        out = []
        for L, C, fx in found:
            v = ((fx.get("params") or {}).get("Track") or {}).get("value", "1")
            t = str(text(v) if isinstance(v, dict) else v)
            out.append({"track": int(t) - 1 if t.isdigit() else 0, "layer": L, "clip": C, "fx": fx})
        return out

    MANAGED = ("Track", "Preset", "Reload", "Geometry")

    def managed_ids(self):
        """Ids of the Bar Chaser params the bridge drives (Level n, Pad n, Track, Preset, Reload, Geometry):
        kept off the knobs, where a turn would fight the sequencer and bury Master / Edge / Outside / Mode."""
        out = set()
        for i in self.instances():
            for name, p in (i["fx"].get("params") or {}).items():
                if is_param(p) and (name in self.MANAGED or name.startswith(("Level ", "Pad "))):
                    out.add(p["id"])
        return out

    @staticmethod
    def has_fixtures(inst):
        """False when the effect could not read its Advanced Output preset: only "—" on offer."""
        opts = (((inst["fx"].get("params") or {}).get("Pad 1") or {}).get("options")) or []
        return any(o != UNASSIGNED for o in opts)

    def ready(self, track):
        return any(i["track"] == track for i in self.instances())

    def layers_of(self, track):
        return sorted({i["layer"] for i in self.instances() if i["track"] == track})

    def _first(self, track):
        return next((i for i in self.instances() if i["track"] == track), None)

    def _reference(self, comp=None):
        """The instance whose mapping counts when instances disagree: T1's first, else the first;
        instances whose effect offers fixtures go first (one that lost its preset knows no mapping)."""
        insts = self.instances(comp)
        good = [i for i in insts if self.has_fixtures(i)] or insts
        return next((i for i in good if i["track"] == 0), good[0] if good else None)

    def mapping(self):
        """The shared pad mapping: 24 fixture names, '' = no fixture."""
        inst = self._reference()
        vals = self.pads_of(inst) if inst else list((self.store.pad_config if self.store else None) or [])
        vals = (vals + [UNASSIGNED] * NPADS)[:NPADS]
        return ["" if v == UNASSIGNED else v for v in vals]

    def preset_text(self):
        """The reference instance's Preset parameter ('' = the newest Advanced Output preset)."""
        inst = self._reference()
        p = ((inst["fx"].get("params") or {}).get("Preset") or {}) if inst else {}
        v = p.get("value", "")
        return text(v) if isinstance(v, dict) else str(v or "")

    def geometry(self):
        """(width, height, {name: rect}) from the reference instance's `Geometry` parameter, which the
        Bar Chaser effect (2026-09-28 or later) fills from the Advanced Output preset *it* reads, on
        Arena's machine. None with an older effect: the bridge then reads the preset file itself."""
        inst = self._reference()
        p = ((inst["fx"].get("params") or {}).get("Geometry") or {}) if inst else {}
        v = p.get("value", "")
        return parse_geometry(text(v) if isinstance(v, dict) else str(v or ""))

    def pad_name(self, track, k):
        """The fixture pad k lights ('' = none). The mapping is shared; the track's own instance is
        read when it has one."""
        inst = self._first(track) or self._reference()
        if inst is None:
            return ""
        p = (inst["fx"].get("params") or {}).get(f"Pad {k + 1}") or {}
        v = text(p.get("value")) if isinstance(p.get("value"), dict) else str(p.get("value", ""))
        return "" if v in ("", UNASSIGNED) else v

    def pad_assigned(self, track, k):
        return bool(self.pad_name(track, k))

    # ---- fixtures (MAPPING menu) -------------------------------------------- #
    def fixtures(self, track=0):
        """Fixtures the effect offers (see fixture_list), cached per composition."""
        comp = self.get_comp()
        if self._fx_cache[0] is comp and self._fx_cache[1] == track:
            return self._fx_cache[2]
        inst = self._first(track) or self._reference()
        opts = ((inst["fx"].get("params") or {}).get("Pad 1") or {}).get("options") if inst else None
        fx = fixture_list(opts)
        self._fx_cache = (comp, track, fx)
        return fx

    def fixtures_of_value(self, track, value):
        """Indices of the fixtures a pad value lights: one fixture, or all of a whole screen."""
        if not value or value == UNASSIGNED:
            return []
        fx = self.fixtures(track)
        hit = [i for i, f in enumerate(fx) if f["name"] == value]
        return hit or [i for i, f in enumerate(fx) if f["screen"] == value]

    def short(self, track, value):
        """'L3F2' for a fixture, the screen name for a whole screen, '' when unassigned."""
        if not value or value == UNASSIGNED:
            return ""
        idx = self.fixtures_of_value(track, value)
        if len(idx) == 1:
            return self.fixtures(track)[idx[0]]["label"]
        return value

    def set_pad(self, k, name, now=None):
        """Map pad k (0-based) to a fixture ('—' = none) on every Bar Chaser: all tracks share it."""
        now = now or time.time()
        done = False
        for inst in self.instances():
            p = (inst["fx"].get("params") or {}).get(f"Pad {k + 1}")
            opts = (p or {}).get("options")
            if not is_param(p) or (opts and name not in opts):
                continue
            if self.send_param:
                self.send_param(p["id"], {"value": name})
            p["value"] = name                                   # shown at once; Resolume confirms later
            if inst["fx"].get("id") is not None:
                self._known[inst["fx"]["id"]] = (self.pads_of(inst), now + PENDING)
            done = True
        if done and self.store is not None:
            cfg = list(self.store.pad_config or self.pads_of(self._reference()))
            cfg = (cfg + [UNASSIGNED] * NPADS)[:NPADS]
            cfg[k] = name                                       # even when the reference effect lacks it
            self.store.pad_config = cfg
            self.store.save()
        return done

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
    # The sequencer reports a level per (track, pad) when it changes. What a Level parameter shows is
    # the maximum of the tracks routed to its instance (its own track, plus tracks without a layer
    # folded onto it). Routing changes (an effect added or removed, a Track changed, a composition
    # opened) re-push every parameter, so nothing stays lit on a layer that no longer plays it, and
    # parameters that no longer exist are never written.
    def route(self, track):
        """The track whose layers show `track`: its own when it has a Bar Chaser, else (fold on) its
        partner (T1 ↔ T2, T3 ↔ T4), else the lowest track that has one. None = nowhere."""
        return self._route(track, {i["track"] for i in self.instances()})

    def _route(self, track, have):
        if track in have:
            return track
        if not self.fold or not have:
            return None
        partner = track ^ 1
        return partner if partner in have else min(have)

    def _sync_routing(self, now):
        self._routing_t = now
        insts = self.instances()
        key = (tuple((i["fx"].get("id"), i["track"]) for i in insts), self.fold)
        if key == self._routing:
            return False
        have = {i["track"] for i in insts}
        tracks = set(range(N_TRACKS)) | {t for t, _ in self._levels}
        feeds_of = {t: [s for s in tracks if self._route(s, have) == t] for t in have}
        feeds = {}
        for inst in insts:
            params = inst["fx"].get("params") or {}
            for k in range(NPADS):
                p = params.get(f"Level {k + 1}")
                if is_param(p):
                    feeds[p["id"]] = (k, feeds_of[inst["track"]])
        for pid in [p for p in self._last if p not in feeds]:     # gone with its effect or composition
            self._last.pop(pid, None)
            self._pending.pop(pid, None)
        by_source = {}
        for pid, (k, srcs) in feeds.items():
            for t in srcs:
                by_source.setdefault((t, k), []).append(pid)
        self._feeds, self._by_source, self._routing = feeds, by_source, key
        for pid in feeds:
            self._push(pid, now, quiet=True)
        return True

    def _push(self, pid, now, quiet=False):
        """Send what parameter `pid` should show now, within the rate limit (held back, never dropped).
        quiet: a parameter we never wrote is left alone when it should show 0."""
        k, tracks = self._feeds[pid]
        out = max((self._levels.get((t, k), 0.0) for t in tracks), default=0.0)
        last = self._last.get(pid)
        if (last is None and quiet and out == 0.0) or (last is not None and last[1] == out):
            self._pending.pop(pid, None)
            return
        if last is not None and out != 0.0 and now - last[0] < self._window(now):
            self._pending[pid] = out
            return
        self._pending.pop(pid, None)
        self._send(pid, out, now)

    def _send(self, pid, value, now):
        self._last[pid] = (now, value)
        self._sends.append(now)
        self.send_level(pid, value)

    def set_level(self, track, pad, value, now=None):
        k = pad_from_key(pad)
        if k is None or not 0 <= k < NPADS:
            return
        now = now or time.time()
        q = round(max(0.0, min(1.0, float(value))) * 255) / 255
        if q > 0:
            self._levels[(track, k)] = q
        else:
            self._levels.pop((track, k), None)
        if now - self._routing_t >= ROUTING_DT or (track, k) not in self._by_source:
            self._sync_routing(now)
        for pid in self._by_source.get((track, k), ()):
            self._push(pid, now)

    def _window(self, now):
        while self._sends and now - self._sends[0] > 0.1:
            self._sends.popleft()
        return 0.033 if len(self._sends) > MANY else MIN_DT

    def observe(self, comp):
        """A full composition arrived (connect, refresh, another composition): note what Arena shows for
        every Level param. The next flush sends again where that differs from what it should show: a
        set lost with a dropped socket, a bar left lit by a crash or saved lit in the composition."""
        rep = {}
        for inst in self.instances(comp):
            for name, p in (inst["fx"].get("params") or {}).items():
                if name.startswith("Level ") and is_param(p):
                    rep[p["id"]] = p.get("value")
        self._reported = rep

    def _reconcile(self, now):
        rep, self._reported = self._reported, None
        if not rep:
            return
        for pid, v in rep.items():
            if pid not in self._feeds:
                continue
            k, tracks = self._feeds[pid]
            out = max((self._levels.get((t, k), 0.0) for t in tracks), default=0.0)
            try:
                v = float(v or 0.0)
            except (TypeError, ValueError):
                v = 0.0
            last = self._last.get(pid)
            if abs(v - out) <= 0.5 / 255 or (last is not None and now - last[0] < IN_FLIGHT):
                continue
            self._pending.pop(pid, None)
            self._send(pid, out, now)

    def flush(self, now=None):
        """Every clock tick: follow routing changes, repair what a full composition showed wrong, and
        send the levels set_level held back because their parameter was written too recently (a flash
        13 ms after the last one is delayed, not lost)."""
        now = now or time.time()
        self._sync_routing(now)
        self._reconcile(now)
        if not self._pending:
            return
        window = self._window(now)
        for pid, q in list(self._pending.items()):
            last = self._last.get(pid)
            if pid not in self._feeds:
                del self._pending[pid]
                continue
            if last and now - last[0] < window:
                continue
            del self._pending[pid]
            if last and last[1] == q:
                continue
            self._send(pid, q, now)

    # ---- pad memory --------------------------------------------------------- #
    @staticmethod
    def pads_of(inst):
        """The instance's 24 pad assignments (slice names, '—' = unassigned)."""
        params = inst["fx"].get("params") or {}
        out = []
        for k in range(NPADS):
            v = (params.get(f"Pad {k + 1}") or {}).get("value", "")
            v = text(v) if isinstance(v, dict) else str(v)
            out.append(v or UNASSIGNED)
        return out

    def _apply_pads(self, inst, want):
        """Write the wanted assignment into the instance; returns what its pads should become."""
        params = inst["fx"].get("params") or {}
        now = self.pads_of(inst)
        for k in range(min(NPADS, len(want))):
            p = params.get(f"Pad {k + 1}")
            opts = (p or {}).get("options")
            if not is_param(p) or want[k] == now[k] or (opts and want[k] not in opts):
                continue                                   # slice not in this instance's preset: keep
            self.send_param(p["id"], {"value": want[k]})
            now[k] = want[k]
        return now

    def sync_pads(self, comp=None, now=None):
        """Call with every new composition: keeps one pad mapping on every Bar Chaser. Returns True
        when a different composition was loaded (its mapping, T1's first, becomes the mapping)."""
        comp = comp or self.get_comp()
        if not comp or self.store is None:
            return False
        now = now or time.time()
        cid = (comp.get("master") or {}).get("id")
        loaded = cid != self._comp_id
        if loaded:
            self._comp_id, self._known = cid, {}
        insts = [i for i in self.instances(comp) if i["fx"].get("id") is not None]
        sources = [i for i in insts if self.has_fixtures(i)]     # one that lost its preset knows no mapping
        want, new = self.store.pad_config, None
        if loaded and sources:
            new = self.pads_of(self._reference(comp))
        elif not loaded:
            for inst in sources:                                   # a pad changed in Arena on any instance:
                prev = self._known.get(inst["fx"]["id"])           # only the pads that changed are taken
                if prev is None or now < prev[1]:
                    continue
                pads = self.pads_of(inst)
                changed = [k for k in range(NPADS) if pads[k] != prev[0][k]]
                if changed:
                    new = list(want) if want else list(pads)
                    for k in changed:
                        new[k] = pads[k]
                    break
            if new is None and want is None and sources:
                new = self.pads_of(sources[0])
        if new is not None and new != want:
            self.store.pad_config = want = list(new)
            self.store.save()
        seen = set()
        for inst in insts:                                         # every instance gets the mapping
            fid = inst["fx"]["id"]
            seen.add(fid)
            if not self.has_fixtures(inst):                        # forgotten: when its preset is back, its
                self._known.pop(fid, None)                         # default pads are not taken for an edit
                continue
            pads, prev = self.pads_of(inst), self._known.get(fid)
            if not want or pads == want:
                self._known[fid] = (pads, 0.0)
            elif prev is not None and now < prev[1]:
                continue                                           # our writes are on their way
            elif self.send_param:
                self._known[fid] = (self._apply_pads(inst, want), now + PENDING)
        for fid in [f for f in self._known if f not in seen]:
            del self._known[fid]
        return loaded

    def all_dark(self):
        self._levels.clear()
        self._pending.clear()
        for inst in self.instances():
            for k in range(NPADS):
                p = (inst["fx"].get("params") or {}).get(f"Level {k + 1}")
                if is_param(p):
                    self.send_level(p["id"], 0.0)
                    self._last[p["id"]] = (time.time(), 0.0)
