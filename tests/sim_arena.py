"""In-process Resolume Arena for scenario tests and the structural fuzzer.

Arena keeps the composition as JSON with a unique id per parameter; a new or re-opened composition
gets new ids. This model does the same and offers three kinds of calls:

- the REST client the bridge uses (`composition`, `set_param`, `add_effect`, …), so a Bridge can be
  built with `rest=arena` and its Sender queue drained into the model (`process`);
- what a user does in Arena (`user_*`: add / remove / move layers, add / remove Bar Chaser, change
  its Track or Pads, reload the fixtures, break the preset, open or create a composition, …);
- delivery to the bridge like Arena's WebSocket: `deliver_comp` (the whole composition, as on a
  structure change or the 5 s refresh) and `deliver_params` (parameter_update for subscribed ids).

The module installs a virtual clock (`CLOCK`, `advance`): the bridge's time.time() is the test's.
No Push, no network, no threads.
"""
from __future__ import annotations

import copy
import itertools
import os
import sys
import tempfile
import time as _time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CLOCK = [1000.0]
_time.time = lambda: CLOCK[0]


def advance(seconds):
    CLOCK[0] += seconds


UN = "—"
EFFECT = "Bar Chaser"


def fixtures(n, start=1):
    return [f"Lumiverse {i} / 1 - 423 141 RGB" for i in range(start, start + n)]


class Arena:
    url = "sim://arena"

    def __init__(self, layers=3, cols=4, chasers=None, fixtures_=None, lengths=None, geometry=False):
        self.ids = itertools.count(1)
        self.fixtures = list(fixtures_ if fixtures_ is not None else fixtures(6))
        self.lengths = list(lengths or [460] * len(self.fixtures))     # bar lengths in the preset, px
        self.geometry = geometry                                         # newer plugin: Geometry param
        self.writes, self.unknown, self.triggers = [], [], []
        self.changed = set()
        self.comp = self._composition(layers, cols, chasers or {})
        self.reindex()

    # ---- builders ------------------------------------------------------------------------- #
    def _p(self, vt, value=None, **extra):
        d = {"id": next(self.ids), "valuetype": vt, **extra}
        if vt != "ParamEvent":
            d["value"] = value
        return d

    def _rng(self, v, lo=0.0, hi=1.0):
        return self._p("ParamRange", v, min=lo, max=hi)

    def _clip(self, name):
        if name is None:
            return {"name": {"value": ""}, "connected": self._p("ParamState", "Empty"),
                    "selected": self._p("ParamBoolean", False), "video": None, "transport": None}
        return {"name": {"value": name}, "connected": self._p("ParamState", "Disconnected"),
                "selected": self._p("ParamBoolean", False),
                "video": {"opacity": self._rng(1.0), "sourceparams": {"Speed": self._rng(0.5)},
                          "effects": [{"name": "Transform", "display_name": {"value": "Transform"},
                                       "id": next(self.ids), "bypassed": self._p("ParamBoolean", False),
                                       "params": {"Opacity": self._rng(1.0), "Scale": self._rng(100, 0, 1000)}}]},
                "transport": {"controls": {"speed": self._rng(1.0, 0, 10)}}}

    def _layer(self, n, cols):
        return {"name": {"value": f"Layer {n}"}, "master": self._rng(1.0),
                "bypassed": self._p("ParamBoolean", False), "solo": self._p("ParamBoolean", False),
                "video": {"opacity": self._rng(1.0), "effects": []},
                "clips": [self._clip(f"Clip {n}.{c}" if c % 2 else None) for c in range(1, cols + 1)]}

    def _options(self):
        screens = []
        for f in self.fixtures:
            s = f.split(" / ", 1)[0]
            if s not in screens:
                screens.append(s)
        return [UN] + list(self.fixtures) + screens

    def _geometry_text(self):
        parts = [f"{f}:{100 + 150 * i},70,30,{self.lengths[i]}" for i, f in enumerate(self.fixtures)]
        return "1920x1080|" + "|".join(parts)

    def _chaser(self, track):
        opts = self._options()
        params = {"Preset": self._p("ParamString", ""), "Reload": self._p("ParamEvent"),
                  "Track": self._p("ParamChoice", str(track), options=["1", "2", "3", "4"]),
                  "Master": self._rng(1.0), "Edge": self._rng(0.0, 0, 20),
                  "Outside": self._p("ParamChoice", "Transparent", options=["Transparent", "Black", "Pass through"]),
                  "Mode": self._p("ParamChoice", "Texture", options=["Texture", "Solid", "Show pads"])}
        for k in range(24):                                  # the plugin's default: pad k = k-th fixture
            params[f"Pad {k + 1}"] = self._p("ParamChoice", self.fixtures[k] if k < len(self.fixtures) else UN,
                                             options=list(opts))
        for k in range(24):
            params[f"Level {k + 1}"] = self._rng(0.0)
        if self.geometry:
            params["Geometry"] = self._p("ParamString", self._geometry_text())
        return {"name": EFFECT, "display_name": {"value": EFFECT}, "id": next(self.ids),
                "bypassed": self._p("ParamBoolean", False), "params": params}

    def _composition(self, layers, cols, chasers):
        comp = {"name": {"value": "Composition"}, "master": self._rng(1.0),
                "tempocontroller": {"tempo": self._rng(120.0, 20, 500), "tempo_tap": self._p("ParamEvent"),
                                    "resync": self._p("ParamEvent")},
                "columns": [{"name": {"value": f"Column {c}"}, "connected": self._p("ParamState", "Disconnected")}
                            for c in range(1, cols + 1)],
                "video": {"effects": [{"name": "Colorize", "display_name": {"value": "Colorize"}, "id": next(self.ids),
                                       "bypassed": self._p("ParamBoolean", True),
                                       "params": {"Opacity": self._rng(1.0),
                                                  "Color": self._p("ParamColor", "#ffffffff", palette=[])}}]},
                "layers": [self._layer(n, cols) for n in range(1, layers + 1)]}
        for L, track in chasers.items():
            if 1 <= L <= layers:
                comp["layers"][L - 1]["video"]["effects"].append(self._chaser(track))
        return comp

    # ---- index -------------------------------------------------------------------------- #
    def reindex(self):
        self.byid = {}
        stack = [self.comp]
        while stack:
            n = stack.pop()
            if isinstance(n, dict):
                if "valuetype" in n and "id" in n:
                    self.byid[n["id"]] = n
                stack.extend(v for v in n.values() if isinstance(v, (dict, list)))
            elif isinstance(n, list):
                stack.extend(n)

    def chasers(self):
        """[(layer 1-based, clip or None, effect)] in the current composition."""
        out = []
        for L, layer in enumerate(self.comp["layers"], 1):
            for fx in (layer.get("video") or {}).get("effects") or []:
                if fx.get("name") == EFFECT:
                    out.append((L, None, fx))
            for C, clip in enumerate(layer.get("clips") or [], 1):
                for fx in ((clip or {}).get("video") or {}).get("effects") or []:
                    if fx.get("name") == EFFECT:
                        out.append((L, C, fx))
        return out

    def pads(self, fx):
        return [fx["params"][f"Pad {k + 1}"]["value"] for k in range(24)]

    def levels(self):
        """{level param id: value} of every Bar Chaser."""
        return {p["id"]: p["value"] for _, _, fx in self.chasers()
                for k, p in fx["params"].items() if k.startswith("Level ")}

    def lit(self):
        return {pid: v for pid, v in self.levels().items() if v and v > 0.0}

    # ---- REST, as the bridge calls it --------------------------------------------------- #
    def composition(self, session=None):
        return copy.deepcopy(self.comp)

    def set_param(self, pid, body):
        p = self.byid.get(pid)
        if p is None:
            self.unknown.append((pid, body))
            return
        if p["valuetype"] == "ParamEvent":
            return
        if "value" in body:
            if p["valuetype"] == "ParamChoice" and body["value"] not in p.get("options", []):
                return                                         # Arena ignores an unknown option
            p["value"] = body["value"]
            self.writes.append((pid, body["value"]))
            self.changed.add(pid)

    def connect_clip(self, layer, column, down):
        self.triggers.append(("clip", layer, column, down))
        clip = self._clip_at(layer, column)
        if clip is None:
            self.unknown.append(("clip", layer, column))
            return
        if down and clip["connected"]["value"] != "Empty":
            for c in self.comp["layers"][layer - 1]["clips"]:
                if c["connected"]["value"].startswith("Connected"):
                    c["connected"]["value"] = "Disconnected"
            clip["connected"]["value"] = "Connected"

    def select_clip(self, layer, column):
        self.triggers.append(("select", layer, column))

    def connect_column(self, column, down):
        self.triggers.append(("column", column, down))

    def clear_layer(self, layer):
        self.triggers.append(("clear", layer))

    def add_effect(self, layer, name):
        if name != EFFECT or not 1 <= layer <= len(self.comp["layers"]):
            return 400
        self.comp["layers"][layer - 1]["video"]["effects"].append(self._chaser(1))
        self.reindex()
        return 204

    def delete_effect(self, layer, offset):
        fx = self.comp["layers"][layer - 1]["video"]["effects"]
        if offset >= len(fx):
            return 404
        fx.pop(offset)
        self.reindex()
        return 204

    def _post_text(self, path, body=""):
        return 204

    def _clip_at(self, L, C):
        layers = self.comp["layers"]
        if not 1 <= L <= len(layers):
            return None
        clips = layers[L - 1]["clips"]
        return clips[C - 1] if 1 <= C <= len(clips) else None

    # ---- what a user does in Arena ---------------------------------------------------------- #
    def user_add_layer(self, at=None):
        cols = len(self.comp["columns"])
        layer = self._layer(len(self.comp["layers"]) + 1, cols)
        if at is None:
            self.comp["layers"].append(layer)
        else:
            self.comp["layers"].insert(at - 1, layer)
        self.reindex()

    def user_remove_layer(self, L):
        self.comp["layers"].pop(L - 1)
        self.reindex()

    def user_move_layer(self, a, b):
        layer = self.comp["layers"].pop(a - 1)
        self.comp["layers"].insert(b - 1, layer)
        self.reindex()

    def user_add_chaser(self, L, track=1, clip=None):
        fx = self._chaser(track)
        if clip is None:
            self.comp["layers"][L - 1]["video"]["effects"].append(fx)
        else:
            c = self.comp["layers"][L - 1]["clips"][clip - 1]
            if c["video"] is None:
                return None
            c["video"]["effects"].append(fx)
        self.reindex()
        return fx

    def user_remove_chaser(self, L):
        effects = self.comp["layers"][L - 1]["video"]["effects"]
        for i, fx in enumerate(effects):
            if fx.get("name") == EFFECT:
                effects.pop(i)
                self.reindex()
                return True
        return False

    def user_set_track(self, fx, track):
        fx["params"]["Track"]["value"] = str(track)
        self.changed.add(fx["params"]["Track"]["id"])

    def user_set_pad(self, fx, k, value):
        p = fx["params"][f"Pad {k + 1}"]
        if value in p["options"]:
            p["value"] = value
            self.changed.add(p["id"])

    def user_set_fixtures(self, names, lengths=None):
        """The Advanced Output changed and every Bar Chaser reloaded its preset: new dropdowns; names
        that still exist stay, others become "—"; an instance with none left gets the default."""
        self.fixtures = list(names)
        self.lengths = list(lengths or [460] * len(names))
        opts = self._options()
        for _, _, fx in self.chasers():
            vals = self.pads(fx)
            keep = [v if v in opts else UN for v in vals]
            if not any(v != UN for v in keep):
                keep = [self.fixtures[k] if k < len(self.fixtures) else UN for k in range(24)]
            for k in range(24):
                p = fx["params"][f"Pad {k + 1}"]
                p["options"], p["value"] = list(opts), keep[k]
            if "Geometry" in fx["params"]:
                fx["params"]["Geometry"]["value"] = self._geometry_text()

    def user_break_preset(self):
        """The effect can't read its preset: only "—" in every dropdown, every pad "—"."""
        for _, _, fx in self.chasers():
            for k in range(24):
                p = fx["params"][f"Pad {k + 1}"]
                p["options"], p["value"] = [UN], UN
            if "Geometry" in fx["params"]:
                fx["params"]["Geometry"]["value"] = ""

    def user_new_composition(self, layers=1, cols=4, chasers=None):
        self.comp = self._composition(layers, cols, chasers or {})
        self.reindex()

    def user_set_master(self, v):
        self.comp["master"]["value"] = v
        self.changed.add(self.comp["master"]["id"])

    # ---- delivery to the bridge -------------------------------------------------------------- #
    def deliver_comp(self, br):
        br.set_comp(self.composition())

    def deliver_params(self, br):
        """parameter_update for every changed id the bridge subscribes to (Arena sends no others)."""
        want = set(br.desired_ids())
        for pid in sorted(self.changed):
            if pid in want and pid in self.byid:
                br.on_param(pid, copy.deepcopy(self.byid[pid].get("value")))
        self.changed.clear()

    def process(self, br):
        """The bridge's Sender queue reaches Arena: ordered triggers first, then coalesced params."""
        snd = br.sender
        while not snd.triggers.empty():
            fn, args = snd.triggers.get_nowait()
            getattr(self, fn.__name__)(*args)
        with snd.lock:
            batch, snd.params = snd.params, {}
        for pid, body in batch.items():
            self.set_param(pid, body)

    def write_preset(self, folder):
        """An Advanced Output preset with the current fixtures, as Arena saves it."""
        xml = ['<XmlState><ScreenSetup><CurrentCompositionTextureSize width="1920" height="1080"/><screens>']
        for i, f in enumerate(self.fixtures):
            screen, slice_ = f.split(" / ", 1)
            x = 100 + 150 * i
            xml.append(f'<DmxScreen name="{screen}"><layers><DmxSlice><Params name="Common">'
                       f'<Param name="Name" value="{slice_}"/></Params><InputRect>'
                       f'<v x="{x}" y="70"/><v x="{x + 30}" y="70"/><v x="{x + 30}" y="{70 + self.lengths[i]}"/>'
                       f'<v x="{x}" y="{70 + self.lengths[i]}"/></InputRect></DmxSlice></layers></DmxScreen>')
        xml.append("</screens></ScreenSetup></XmlState>")
        path = Path(folder) / "sim.xml"
        path.write_text("".join(xml))
        os.utime(path, (CLOCK[0], CLOCK[0]))
        return path


def make_bridge(arena, **cfg_over):
    """A real Bridge on the simulated Arena, with its own data folder and Advanced Output preset."""
    import push_resolume_bridge as B
    tmp = Path(tempfile.mkdtemp())
    os.environ["PUSH2_DATA_DIR"] = str(tmp / "data")
    cfg = B.load_config(ROOT / "config.yaml")
    for k in ("pins_file", "colors_file", "chases_file"):
        cfg[k] = str(tmp / f"{k}.yaml")
    folder = tmp / "presets"
    folder.mkdir()
    arena.write_preset(folder)
    cfg["sequencer"]["preset_folder"] = str(folder)
    for k, v in cfg_over.items():
        cfg["sequencer"][k] = v
    br = B.Bridge(cfg, arena)
    br.preset_folder = folder
    arena.deliver_comp(br)
    return br


def settle(arena, br, seconds=3.0, ticks=30):
    """Let time pass with everything flowing: clock ticks, Sender → Arena, Arena → bridge."""
    dt = seconds / ticks
    for _ in range(ticks):
        advance(dt)
        br.seq_step()
        arena.process(br)
        arena.deliver_params(br)
    arena.deliver_comp(br)
    br.seq_step()
    arena.process(br)
