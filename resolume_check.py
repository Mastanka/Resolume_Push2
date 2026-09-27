"""`--check LAYER`: try every Resolume call the bridge uses on one (spare) layer and report OK / FAIL.

Each step changes something on that layer, reads it back from Resolume and puts it back.
Column launch touches every layer, so it only runs with --check-columns.
"""

from __future__ import annotations

import json
import time

import requests

from resolume_api import clip_state, master_param, resolve_node, text, walk

WAIT = 0.35   # s for Resolume to apply a change before reading it back


class Checker:
    def __init__(self, host, port, layer):
        self.base = f"http://{host}:{port}/api/v1"
        self.ws_url = f"ws://{host}:{port}/api/v1"
        self.L = layer
        self.s = requests.Session()
        self.results = []

    # ---- http helpers ------------------------------------------------------ #
    def req(self, method, path, body=None):
        r = self.s.request(method, self.base + path, timeout=3,
                           data=None if body is None else json.dumps(body),
                           headers={"Content-Type": "application/json"})
        return r.status_code, (r.json() if r.content and "json" in r.headers.get("Content-Type", "") else None)

    def comp(self):
        return self.req("GET", "/composition")[1]

    def layer(self, comp=None):
        return ((comp or self.comp()).get("layers") or [])[self.L - 1]

    def param_by_id(self, pid):
        """Find a param in a fresh composition by id (works on every Resolume version)."""
        stack = [self.comp()]
        while stack:
            n = stack.pop()
            if isinstance(n, dict):
                if n.get("id") == pid and "valuetype" in n:
                    return n
                stack.extend(n.values())
            elif isinstance(n, list):
                stack.extend(n)
        return None

    def put(self, pid, body):
        return self.req("PUT", f"/parameter/by-id/{pid}", body)[0]

    def add(self, name, ok, detail=""):
        self.results.append((name, ok, detail))
        mark = {True: "OK  ", False: "FAIL", None: "SKIP", "info": "INFO"}[ok]
        print(f"  {mark}  {name:<34} {detail}")

    # ---- steps ------------------------------------------------------------- #
    def step(self, name, fn):
        try:
            fn(name)
        except Exception as e:
            self.add(name, False, f"{type(e).__name__}: {e}")

    def set_and_back(self, name, p, new, body_of=lambda v: {"value": v}, read=lambda p: p.get("value"),
                     info=False):
        old = p.get("value")
        code = self.put(p["id"], body_of(new))
        time.sleep(WAIT)
        got = read(self.param_by_id(p["id"]) or {})
        self.put(p["id"], {"value": old})
        time.sleep(WAIT)
        back = (self.param_by_id(p["id"]) or {}).get("value")
        ok = got == new and back == old
        self.add(name, "info" if info else ok, f"HTTP {code}, sent {new!r}, read {got!r}, restored {back == old}")
        return got == new

    def s_range(self, name):
        p = master_param(self.layer())
        v = 0.25 if abs(float(p["value"]) - 0.25) > 0.01 else 0.5
        self.set_and_back(name, p, v)

    def s_bool(self, key):
        def f(name):
            p = self.layer().get(key)
            self.set_and_back(name, p, not p["value"])
        return f

    def s_choice(self, name):
        p = resolve_node(self.layer(), "video/mixer/Blend Mode")
        if not p:
            return self.add(name, None, "layer has no Blend Mode")
        opts = p.get("options") or []
        other = next(o for o in opts if o != p["value"])
        by_value = self.set_and_back(name + " (value)", p, other)
        p = resolve_node(self.layer(), "video/mixer/Blend Mode")
        i = opts.index(other)
        # informational only: the bridge sends the option name (Arena 7.23 rejects index, HTTP 400)
        by_index = self.set_and_back(name + " (index)", p, other, body_of=lambda v: {"index": i}, info=True)
        self.add(name, by_value, f"by value: {by_value}, by index: {by_index}")

    def first_clip(self, layer):
        clips = layer.get("clips") or []
        return next(((i + 1, c) for i, c in enumerate(clips) if clip_state(c) != "Empty"), (None, None))

    def s_color(self, name):
        layer = self.layer()
        for c in layer.get("clips") or []:
            for _, p in walk(c.get("video") or {}, "video", types={"ParamColor"}):
                new = "#12ab34ff" if p["value"] != "#12ab34ff" else "#ab1234ff"
                return self.set_and_back(name, p, new)
        self.add(name, None, "no colour param on this layer's clips")

    def playing(self, layer):
        return next((i + 1 for i, c in enumerate(layer.get("clips") or [])
                     if clip_state(c).startswith("Connected")), None)

    def restore_playing(self, was):
        if was:
            self.req("POST", f"/composition/layers/{self.L}/clips/{was}/connect", True)
            self.req("POST", f"/composition/layers/{self.L}/clips/{was}/connect", False)
        else:
            self.req("POST", f"/composition/layers/{self.L}/clear")
        time.sleep(WAIT)

    def s_select(self, name):
        layer = self.layer()
        C, clip = self.first_clip(layer)
        if not C:
            return self.add(name, None, "no loaded clip on this layer")
        code = self.req("POST", f"/composition/layers/{self.L}/clips/{C}/select")[0]
        time.sleep(WAIT)
        sel = (self.layer()["clips"][C - 1].get("selected") or {}).get("value")
        self.add(name, bool(sel), f"HTTP {code}, clip {C} selected = {sel!r}")

    def s_connect(self, name):
        layer = self.layer()
        was = self.playing(layer)
        C, _ = self.first_clip(layer)
        if not C:
            return self.add(name, None, "no loaded clip on this layer")
        c1 = self.req("POST", f"/composition/layers/{self.L}/clips/{C}/connect", True)[0]
        c2 = self.req("POST", f"/composition/layers/{self.L}/clips/{C}/connect", False)[0]
        time.sleep(WAIT)
        st = clip_state(self.layer()["clips"][C - 1])
        self.add(name, st.startswith("Connected"), f"HTTP {c1}/{c2}, clip {C} is {st!r}")
        code = self.req("POST", f"/composition/layers/{self.L}/clear")[0]
        time.sleep(WAIT)
        now = self.playing(self.layer())
        self.add("Stop layer (/clear)", now is None, f"HTTP {code}, playing after clear: {now}")
        self.restore_playing(was)

    def s_event(self, name):
        p = resolve_node(self.comp(), "tempocontroller/resync")
        if not p:
            return self.add(name, None, "no tempocontroller/resync")
        code = self.put(p["id"], {"value": True})
        self.add(name, code < 300, f"HTTP {code} (Resolume gives no read-back for events)")

    def s_tempo(self, name):
        p = resolve_node(self.comp(), "tempocontroller/tempo")
        if not p:
            return self.add(name, None, "no tempocontroller/tempo")
        self.set_and_back(name, p, round(float(p["value"]) + 1.0, 2))

    def s_columns(self, name):
        comp = self.comp()
        cols = comp.get("columns") or []
        n = next((i + 1 for i, c in enumerate(cols) if text(c.get("connected")) != "Empty"), None)
        if not n:
            return self.add(name, None, "no column with clips")
        c1 = self.req("POST", f"/composition/columns/{n}/connect", True)[0]
        c2 = self.req("POST", f"/composition/columns/{n}/connect", False)[0]
        time.sleep(WAIT)
        st = text((self.comp()["columns"][n - 1]).get("connected"))
        self.add(name, st == "Connected", f"HTTP {c1}/{c2}, column {n} is {st!r} (not restored)")

    def s_ws(self, name):
        try:
            import websocket
        except ImportError:
            return self.add(name, None, "pip install websocket-client")
        ws = websocket.create_connection(self.ws_url, timeout=3)
        kinds, first = [], None
        end = time.time() + 1.5
        while time.time() < end:
            try:
                msg = json.loads(ws.recv())
            except Exception:
                break
            first = first or msg
            kinds.append(msg.get("type") or ("composition" if "layers" in msg else ",".join(list(msg)[:3])))
        self.add(name + " connect", True, f"got {len(kinds)} messages: {sorted(set(kinds))}")
        # subscribe by id (subscribing by path is rejected by Arena 7.23): expect the current value back
        p = master_param(self.layer())
        ws.send(json.dumps({"action": "subscribe", "parameter": f"/parameter/by-id/{p['id']}"}))
        got = None
        end = time.time() + 1.5
        while time.time() < end and got is None:
            try:
                msg = json.loads(ws.recv())
            except Exception:
                break
            if msg.get("id") == p["id"]:
                got = msg
        ws.send(json.dumps({"action": "unsubscribe", "parameter": f"/parameter/by-id/{p['id']}"}))
        ws.close()
        self.add(name + " subscribe", bool(got),
                 f"{got.get('type')} {got.get('path')} = {got.get('value')}" if got else "no reply")

    def s_ws_set(self, name):
        try:
            import websocket
        except ImportError:
            return self.add(name, None, "pip install websocket-client")
        p = master_param(self.layer())
        old = p["value"]
        new = 0.37 if abs(old - 0.37) > 0.01 else 0.61
        ws = websocket.create_connection(self.ws_url, timeout=3)
        ws.send(json.dumps({"action": "set", "parameter": f"/parameter/by-id/{p['id']}", "value": new}))
        time.sleep(WAIT)
        got = (self.param_by_id(p["id"]) or {}).get("value")
        ws.send(json.dumps({"action": "set", "parameter": f"/parameter/by-id/{p['id']}", "value": old}))
        time.sleep(WAIT)
        ws.close()
        self.add(name, got == new, f"sent {new}, read {got}")

    def _post_text(self, path, body):
        return self.s.post(self.base + path, data=body.encode(), headers={"Content-Type": "text/plain"},
                           timeout=3).status_code

    def s_open_clip(self, name):
        layer = self.layer()
        C = next((i + 1 for i, c in enumerate(layer.get("clips") or []) if clip_state(c) == "Empty"), None)
        if not C:
            return self.add(name, None, "no empty clip slot on this layer")
        code = self._post_text(f"/composition/layers/{self.L}/clips/{C}/open", "source:///video/Checkered")
        time.sleep(WAIT)
        clip = self.layer()["clips"][C - 1]
        desc = (clip.get("video") or {}).get("description")
        code2 = self.req("POST", f"/composition/layers/{self.L}/clips/{C}/clear")[0]
        time.sleep(WAIT)
        cleared = clip_state(self.layer()["clips"][C - 1]) == "Empty"
        self.add(name, code == 204 and desc == "Checkered" and cleared,
                 f"open HTTP {code}, description {desc!r}, clear HTTP {code2}, empty again {cleared}")

    def s_crop(self, name):
        before = len(self.layer()["video"].get("effects") or [])
        code = self._post_text(f"/composition/layers/{self.L}/effects/video/add", "effect:///video/Crop")
        time.sleep(WAIT)
        fx = self.layer()["video"].get("effects") or []
        if len(fx) != before + 1:
            return self.add(name, False, f"add HTTP {code}, effects {before} → {len(fx)}")
        idx = len(fx) - 1
        code2 = self._post_text(f"/composition/layers/{self.L}/effects/video/{idx}/set-display-name", "CH:T9:check")
        time.sleep(WAIT)
        dn = text(self.layer()["video"]["effects"][idx].get("display_name"))
        left = self.layer()["video"]["effects"][idx]["params"].get("Left")
        code3 = self.put(left["id"], {"value": 100.0}) if left else None
        time.sleep(WAIT)
        lv = (self.param_by_id(left["id"]) or {}).get("value") if left else None
        code4 = self.s.delete(f"{self.base}/composition/layers/{self.L}/effects/video/{idx}", timeout=3).status_code
        time.sleep(WAIT)
        gone = len(self.layer()["video"].get("effects") or []) == before
        self.add(name, dn == "CH:T9:check" and lv == 100.0 and gone,
                 f"add {code}, display name {code2} → {dn!r}, Left {code3} → {lv}, delete {code4}, removed {gone}")

    def s_layer_name(self, name):
        p = self.layer().get("name")
        old = p["value"]
        code = self.put(p["id"], {"value": old + " ✓"})
        time.sleep(WAIT)
        got = (self.param_by_id(p["id"]) or {}).get("value")
        self.put(p["id"], {"value": old})
        time.sleep(WAIT)
        self.add(name, "info" if got != old + " ✓" else True,
                 f"HTTP {code}, read {got!r} (informational: the bridge names bar layers best-effort)")

    # ---- run --------------------------------------------------------------- #
    def run(self, columns=False):
        comp = self.comp()
        layers = comp.get("layers") or []
        if not 1 <= self.L <= len(layers):
            print(f"Composition has {len(layers)} layers; --check needs one of them.")
            return False
        code, product = self.req("GET", "/product")
        info = {k: v for k, v in (product or {}).items() if k in ("name", "major", "minor", "micro", "revision")}
        print(f"\nResolume {info or '(no /product)'}")
        print(f'Checking on layer {self.L} "{text(layers[self.L - 1].get("name"))}" — '
              "changes are undone after each step.\n")
        self.step("Read composition", lambda n: self.add(n, True, f"{len(layers)} layers"))
        self.step("Set range (layer master)", self.s_range)
        self.step("Set boolean (layer bypassed)", self.s_bool("bypassed"))
        self.step("Set boolean (layer solo)", self.s_bool("solo"))
        self.step("Set choice (blend mode)", self.s_choice)
        self.step("Set colour", self.s_color)
        self.step("Set tempo", self.s_tempo)
        self.step("Trigger event (resync)", self.s_event)
        self.step("Select clip", self.s_select)
        self.step("Launch + release clip", self.s_connect)
        if columns:
            self.step("Launch column", self.s_columns)
        else:
            self.add("Launch column", None, "affects all layers; run with --check-columns")
        self.step("WebSocket", self.s_ws)
        self.step("WebSocket set", self.s_ws_set)
        self.step("Open source into a clip", self.s_open_clip)
        self.step("Add Crop + display name + delete", self.s_crop)
        self.step("Set string (layer name)", self.s_layer_name)
        fails = [r for r in self.results if r[1] is False]
        print(f"\n{len(fails)} failed." if fails else "\nAll OK.")
        return not fails
