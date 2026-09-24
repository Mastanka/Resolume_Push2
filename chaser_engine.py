"""Layer engine for the step sequencer: one Resolume layer per (track, bar).

A bar layer is recognised by its Crop effect's display name `CH:T<track>:<bar name>` (the
marker; effect display names are settable through the API). The layer itself is named
`CH: T<track> <bar name>` when Resolume accepts the rename. Levels 0-1 become the layer's
video/opacity."""

from __future__ import annotations

import time
from urllib.parse import quote

from resolume_api import is_param, resolve_node, text, walk

MARKER = "CH:"
MIN_DT = 0.02          # s between two sends of the same layer (50/s); 30/s when many change
MANY = 16
PARAM_TYPES = {"ParamRange", "ParamChoice", "ParamBoolean", "ParamColor"}


class LayerEngine:
    def __init__(self, rest, get_comp, refresh, send_level, prefix="CH: ", clip_column=1):
        self.rest, self.get_comp, self.refresh, self.send_level = rest, get_comp, refresh, send_level
        self.prefix, self.clip_column = prefix, int(clip_column)
        self._last = {}            # opacity param id -> (time, quantised value)
        self._pids = {}            # (track, bar) -> opacity param id
        self._pids_comp = None

    def marker(self, track, bar):
        return f"{MARKER}T{track + 1}:{bar}"

    @staticmethod
    def _marker_of(layer):
        for fx in (layer.get("video") or {}).get("effects") or []:
            dn = text(fx.get("display_name"))
            if dn.startswith(MARKER + "T") and ":" in dn[len(MARKER) + 1:]:
                return dn
        return None

    @staticmethod
    def is_bar_layer(layer):
        return LayerEngine._marker_of(layer) is not None

    def find_layers(self, comp=None):
        comp = comp or self.get_comp() or {}
        out = {}
        for i, layer in enumerate(comp.get("layers") or [], 1):
            m = self._marker_of(layer)
            if m:
                t, bar = m[len(MARKER) + 1:].split(":", 1)
                if t.isdigit():
                    out[(int(t) - 1, bar)] = (i, layer)
        return out

    def ready(self, track):
        return any(t == track for t, _ in self.find_layers())

    # ---- setup ------------------------------------------------------------ #
    def _crop_of(self, layer):
        for k, fx in enumerate((layer.get("video") or {}).get("effects") or []):
            if fx.get("name") == "Crop" and self._marker_of({"video": {"effects": [fx]}}):
                return k, fx
        return None, None

    def _set_rect(self, crop, bar):
        for key, val in (("Left", bar.left), ("Right", bar.right), ("Top", bar.top), ("Bottom", bar.bottom)):
            p = ((crop or {}).get("params") or {}).get(key)
            if is_param(p) and p.get("value") != val:
                self.rest.set_param(p["id"], {"value": val})

    def setup(self, bars, tracks, dry_run=False):
        """Ensure a layer per (track, bar). Returns report lines."""
        report = []
        found = self.find_layers()
        for t in range(tracks):
            for bar in bars:
                key = (t, bar.name)
                if key in found:
                    L, layer = found[key]
                    if dry_run:
                        report.append(f"keep   layer {L} {self.marker(t, bar.name)}")
                        continue
                    _, crop = self._crop_of(layer)
                    self._set_rect(crop, bar)
                    self._finish_layer(layer, t, bar)
                    report.append(f"update layer {L} {self.marker(t, bar.name)}")
                    continue
                if dry_run:
                    report.append(f"create layer {self.marker(t, bar.name)} crop "
                                  f"{bar.left},{bar.top}-{bar.right},{bar.bottom}")
                    continue
                code = self.rest.add_layer()
                if code != 204:
                    report.append(f"FAIL add layer for {bar.name}: HTTP {code}")
                    continue
                self.refresh()
                L = len(self.get_comp()["layers"])
                code = self.rest.add_effect(L, "Crop")
                if code != 204:
                    report.append(f"FAIL add Crop on layer {L}: HTTP {code}")
                    continue
                self.refresh()
                layer = self.get_comp()["layers"][L - 1]
                idx = len(layer["video"]["effects"]) - 1
                self.rest.set_effect_display_name(L, idx, self.marker(t, bar.name))
                self.refresh()
                layer = self.get_comp()["layers"][L - 1]
                self._set_rect(layer["video"]["effects"][idx], bar)
                self._finish_layer(layer, t, bar)
                report.append(f"create layer {L} {self.marker(t, bar.name)}")
        if not dry_run:
            self.refresh()
            self._pids_comp = None
        return report

    def _finish_layer(self, layer, t, bar):
        """Blend Add, opacity 0, best-effort name."""
        bm = resolve_node(layer, "video/mixer/Blend Mode")
        if is_param(bm) and bm.get("value") != "Add" and "Add" in (bm.get("options") or []):
            self.rest.set_param(bm["id"], {"value": "Add"})
        op = resolve_node(layer, "video/opacity")
        if is_param(op) and op.get("value") != 0.0:
            self.rest.set_param(op["id"], {"value": 0.0})
        name = layer.get("name")
        want = f"{self.prefix}T{t + 1} {bar.name}"
        if is_param(name) and text(name) != want:
            try:
                self.rest.set_param(name["id"], {"value": want})
            except Exception:
                pass

    # ---- texture ---------------------------------------------------------- #
    def load_texture(self, track, clip_json, bars=None):
        if not clip_json or not (clip_json.get("video") or {}):
            return "select a clip first"
        video = clip_json["video"]
        desc = text(video.get("description"))
        info = video.get("fileinfo") if isinstance(video.get("fileinfo"), dict) else {}
        path = info.get("path") if info else None
        if path:
            uri = "file://" + quote(path)
        elif desc:
            uri = "source:///video/" + quote(desc)
        else:
            return "file clips: not supported yet"
        targets = [(k, v) for k, v in self.find_layers().items()
                   if k[0] == track and (bars is None or k[1] in bars)]
        if not targets:
            return f"T{track + 1}: no bar layers — press Shift + Note"
        for _, (L, _) in targets:
            self.rest.open_clip(L, self.clip_column, uri)
        self.refresh()
        src_params = dict(walk(video.get("sourceparams") or {}, "", types=PARAM_TYPES))
        for _, (L, _) in targets:
            layer = self.get_comp()["layers"][L - 1]
            clip = layer["clips"][self.clip_column - 1]
            dst = dict(walk((clip.get("video") or {}).get("sourceparams") or {}, "", types=PARAM_TYPES))
            for p, prm in src_params.items():
                if p in dst and dst[p].get("value") != prm.get("value"):
                    self.rest.set_param(dst[p]["id"], {"value": prm.get("value")})
            self.rest.connect_clip(L, self.clip_column, True)
            self.rest.connect_clip(L, self.clip_column, False)
        self.refresh()
        n = len(targets)
        return f"T{track + 1}: {text(clip_json.get('name')) or desc} → {n} bar{'s' * (n != 1)}"

    # ---- levels ------------------------------------------------------------ #
    def _pid(self, track, bar):
        comp = self.get_comp()
        if comp is not self._pids_comp:
            self._pids, self._pids_comp = {}, comp
            for key, (_, layer) in self.find_layers(comp).items():
                op = resolve_node(layer, "video/opacity")
                if is_param(op):
                    self._pids[key] = op["id"]
        return self._pids.get((track, bar))

    def set_level(self, track, bar, value, now=None):
        pid = self._pid(track, bar)
        if pid is None:
            return
        now = now or time.time()
        q = round(max(0.0, min(1.0, float(value))) * 255) / 255
        last = self._last.get(pid)
        if last and last[1] == q:
            return
        busy = sum(1 for t, _ in self._last.values() if now - t < 0.1) > MANY
        if last and q != 0.0 and now - last[0] < (0.033 if busy else MIN_DT):
            return
        self._last[pid] = (now, q)
        self.send_level(pid, q)

    def all_dark(self):
        for key in list(self.find_layers()):
            self._last.pop(self._pid(*key), None)
            self.set_level(key[0], key[1], 0.0)
