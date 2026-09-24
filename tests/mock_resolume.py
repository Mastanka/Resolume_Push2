"""Minimal fake of Resolume Arena's REST API for offline testing.
Serves a small composition on http://127.0.0.1:8080/api/v1 and logs every POST/PUT.
Run:  python tests/mock_resolume.py
"""
import json, itertools, base64, hashlib, struct, threading, time
from urllib.parse import unquote
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
ids = itertools.count(1000)
def rng(v, lo=0.0, hi=1.0): return {"id": next(ids), "valuetype": "ParamRange", "value": v, "min": lo, "max": hi}
PALETTE = ["#000000ff", "#ff0000ff", "#00ff00ff", "#ffff00ff", "#0000ffff", "#ff00ffff", "#ffffffff", "#ffb17bff"]
def color(v): return {"id": next(ids), "valuetype": "ParamColor", "value": v, "palette": PALETTE, "view": {"suffix": ""}}
def b_(v): return {"id": next(ids), "valuetype": "ParamBoolean", "value": v}
def ev(): return {"id": next(ids), "valuetype": "ParamEvent"}
def s(v): return {"id": next(ids), "valuetype": "ParamString", "value": v}
def clip(name, state="Disconnected", gen=False):
    if name is None:
        return {"name": s(""), "connected": {"id": next(ids), "valuetype": "ParamState", "value": "Empty"},
                "transport": None, "video": None}          # like Arena 7.23: empty slots have null sections
    c = {"name": s(name), "connected": {"id": next(ids), "valuetype": "ParamState", "value": state},
         "transport": {"position": rng(0.3, 0, 5000), "controls": {"speed": rng(1, 0, 10)}},
         "video": {"opacity": rng(1), "effects": [{"name": "Transform", "id": next(ids), "bypassed": {"id": next(ids), "valuetype": "ParamBoolean", "value": False},
                   "params": {"Position X": rng(0, -16384, 16384), "Scale": rng(100, 0, 1000)}}]}}
    if gen:
        c["video"]["description"] = name                   # Arena: generator clips carry the source name
        c["video"]["sourceparams"] = {"Frequency": rng(0.32), "Fade": {"id": next(ids), "valuetype": "ParamBoolean", "value": True},
                                      "Width": rng(0.5), "Height": rng(0.5), "Offset": rng(0.0),
                                      "Color": color("#ff8b58ff"), "BG Color": color("#00000000")}
    return c
SOURCE_PARAMS = {"Metaballs": lambda: {"Grid": rng(20, 1, 20), "Color": color("#ff0000ff")},
                 "Checkered": lambda: {"Size": rng(0.5)},
                 "Solid Color": lambda: {"Color": color("#ffffffff")}}
def source_clip(name):
    c = clip(name)
    c["video"]["description"] = name
    c["video"]["fileinfo"] = None
    c["video"]["sourceparams"] = (SOURCE_PARAMS.get(name) or (lambda: {"Opacity": rng(1.0)}))()
    return c
def file_clip(path):
    c = clip(path.rsplit("/", 1)[-1])
    c["video"]["fileinfo"] = {"path": path}
    return c
def crop_effect():
    return {"name": "Crop", "display_name": "Crop", "id": next(ids), "bypassed": b_(False),
            "params": {"Opacity": rng(1.0), "Left": rng(0.0, 0, 16384), "Right": rng(1920.0, 0, 16384),
                       "Top": rng(0.0, 0, 16384), "Bottom": rng(1080.0, 0, 16384), "Invert": b_(False), "Black BG": b_(False)}}
def new_layer(n):
    return {"name": s(f"Layer {n}"), "bypassed": b_(False), "solo": b_(False), "master": rng(1.0),
            "video": {"opacity": rng(1.0), "mixer": {"Blend Mode": {"id": next(ids), "valuetype": "ParamChoice", "value": "Alpha", "index": 0, "options": ["Alpha", "Add", "Multiply", "Screen"]}}, "effects": []},
            "clips": [clip(None) for _ in range(4)]}
OPACITY_LOG = []   # [t, pid, value] for every write to a layer's video/opacity (GET /api/v1/_opacity_log)
def note_opacity(pid, value):
    for layer in COMP["layers"]:
        if layer["video"]["opacity"]["id"] == pid:
            OPACITY_LOG.append([time.time(), pid, value])
COMP = {"master": rng(0.9),
  "tempocontroller": {"tempo": rng(120.0, 20.0, 500.0), "tempo_tap": ev(), "resync": ev()},
  "video": {"opacity": rng(1.0), "effects": [
      {"name": "Colorize", "id": next(ids), "bypassed": b_(True),
       "params": {"Opacity": rng(1.0), "Color": color("#ffffffff")}}]},
  "columns": [{"name": s(f"Column {n}"), "connected": {"id": next(ids), "valuetype": "ParamState", "value": "Disconnected"}}
              for n in range(1, 5)],
  "layers": [
  {"name": s("Strobe"), "bypassed": b_(False), "solo": b_(False), "master": rng(1.0), "video": {"opacity": rng(0.8), "mixer": {"Blend Mode": {"id": next(ids), "valuetype": "ParamChoice", "value": "Add", "index": 1, "options": ["Alpha", "Add", "Multiply", "Screen"]}},
     "effects": [{"name": "HueRotate", "display_name": "Hue Rotate", "id": next(ids), "params": {"Hue Rotate": rng(0.2)}}]},
   "audio": {"volume": rng(0, -60, 6)},
   "clips": [clip("Stroboscope", "Connected", gen=True), clip("Rolling strobe"), clip(None), clip("Odd/Even")]},
  {"name": s("Comets"), "bypassed": b_(False), "solo": b_(False), "master": rng(0.75), "video": {"opacity": rng(1.0), "effects": []}, "clips": [clip(None), clip("Comets down", "Connected"), clip(None), clip(None)]},
  {"name": s("Ambient clouds"), "bypassed": b_(False), "solo": b_(False), "master": rng(0.5), "video": {"opacity": rng(0.4), "effects": []}, "clips": [clip("Clouds", gen=True), clip(None), clip(None), clip(None)]},
]}
BYID = {}
def index(n):
    if isinstance(n, dict):
        if "valuetype" in n and "id" in n: BYID[n["id"]] = n
        for v in n.values(): index(v)
    elif isinstance(n, list):
        for v in n: index(v)
index(COMP)
LOG = []
EVENTS = {}   # ParamEvent id -> times triggered (GET /api/v1/_events)
# ---- WebSocket (ws://127.0.0.1:8080/api/v1), like Arena 7.23: full composition on connect,
# subscribe / unsubscribe by "/parameter/by-id/<id>", then parameter_update on every change.
WS_CLIENTS = []          # [handler], each with .subs {id: last value sent} and .ws_lock
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

def ws_send(h, obj):
    data = json.dumps(obj).encode()
    n = len(data)
    head = bytes([0x81, n]) if n < 126 else (bytes([0x81, 126]) + struct.pack(">H", n) if n < 65536
                                            else bytes([0x81, 127]) + struct.pack(">Q", n))
    with h.ws_lock:
        h.wfile.write(head + data); h.wfile.flush()

def ws_recv(h):
    b1, b2 = h.rfile.read(2)
    n = b2 & 0x7F
    if n == 126: n = struct.unpack(">H", h.rfile.read(2))[0]
    elif n == 127: n = struct.unpack(">Q", h.rfile.read(8))[0]
    mask = h.rfile.read(4) if b2 & 0x80 else b"\0\0\0\0"
    data = bytes(c ^ mask[i % 4] for i, c in enumerate(h.rfile.read(n)))
    return b1 & 0x0F, data

def ws_broadcast_comp():
    """Like Arena: the whole composition goes to every socket when its structure changes."""
    for h in list(WS_CLIENTS):
        try: ws_send(h, COMP)
        except Exception: pass

def ws_broadcast():
    for h in list(WS_CLIENTS):
        for pid, last in list(h.subs.items()):
            v = BYID[pid].get("value")
            if v != last:
                h.subs[pid] = v
                try: ws_send(h, {**BYID[pid], "type": "parameter_update"})
                except Exception: pass

class H(BaseHTTPRequestHandler):
    def ws_session(self):
        accept = base64.b64encode(hashlib.sha1((self.headers["Sec-WebSocket-Key"] + WS_GUID).encode()).digest()).decode()
        self.send_response(101); self.send_header("Upgrade", "websocket"); self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept); self.end_headers()
        self.subs, self.ws_lock = {}, threading.Lock()
        ws_send(self, COMP); ws_send(self, {"type": "sources_update", "value": []})
        WS_CLIENTS.append(self)
        try:
            while True:
                op, data = ws_recv(self)
                if op == 8: break
                if op != 1: continue
                msg = json.loads(data); pid = int(msg.get("parameter", "").rsplit("/", 1)[-1] or 0)
                if msg.get("action") == "subscribe" and pid in BYID:
                    self.subs[pid] = BYID[pid].get("value")
                    ws_send(self, {**BYID[pid], "type": "parameter_subscribed"})
                elif msg.get("action") == "unsubscribe":
                    self.subs.pop(pid, None)
                elif msg.get("action") == "set" and pid in BYID:
                    BYID[pid]["value"] = msg.get("value"); note_opacity(pid, msg.get("value")); ws_broadcast()
        except Exception:
            pass
        finally:
            WS_CLIENTS.remove(self)
            self.close_connection = True
    def log_message(self, *a): pass
    def _body(self): return self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
    def do_GET(self):
        if self.headers.get("Upgrade", "").lower() == "websocket":
            return self.ws_session()
        if self.path.endswith("/product"):
            d = json.dumps({"name": "Mock Resolume", "major": 7, "minor": 23}).encode(); self.send_response(200); self.send_header("Content-Type","application/json"); self.end_headers(); self.wfile.write(d); return
        if self.path.endswith("/_opacity_log"):
            d = json.dumps(OPACITY_LOG).encode(); self.send_response(200); self.send_header("Content-Type","application/json"); self.end_headers(); self.wfile.write(d); return
        if self.path.endswith("/_events"):
            d = json.dumps(EVENTS).encode(); self.send_response(200); self.send_header("Content-Type","application/json"); self.end_headers(); self.wfile.write(d); return
        d = json.dumps(COMP).encode(); self.send_response(200); self.send_header("Content-Type","application/json"); self.end_headers(); self.wfile.write(d)
    def do_POST(self):
        b = self._body(); LOG.append(("POST", self.path, b)); print("POST", self.path, b, flush=True)
        parts = self.path.split("/")
        if parts[3:] == ["composition", "layers", "add"]:
            L = new_layer(len(COMP["layers"]) + 1); index(L)
            if b.strip().startswith("/composition/layers/"):
                COMP["layers"].insert(int(b.strip().rsplit("/", 1)[-1]) - 1, L)
            else:
                COMP["layers"].append(L)
            self.send_response(204); self.end_headers(); ws_broadcast_comp(); return
        if len(parts) > 8 and parts[6] == "effects" and parts[7] == "video":
            layer = COMP["layers"][int(parts[5]) - 1]
            if parts[8] == "add":
                name = unquote(b.strip().rsplit("/", 1)[-1])
                if name == "Crop": fx = crop_effect()
                elif name == "Slice Transform": fx = {"name": "ScreenLayerTransform", "display_name": "Slice Transform", "id": next(ids), "bypassed": b_(False), "params": {"Opacity": rng(1.0)}}
                else: self.send_response(400); self.end_headers(); return
                index(fx); layer["video"]["effects"].append(fx)
            elif len(parts) > 9 and parts[9] == "set-display-name":
                layer["video"]["effects"][int(parts[8])]["display_name"] = b.strip()
            self.send_response(204); self.end_headers(); ws_broadcast_comp(); return
        if len(parts) > 8 and parts[6] == "clips" and parts[8] in ("open", "clear"):
            layer, C = COMP["layers"][int(parts[5]) - 1], int(parts[7]) - 1
            if parts[8] == "clear": c = clip(None)
            elif b.startswith("source:///video/"): c = source_clip(unquote(b[len("source:///video/"):].strip()))
            else: c = file_clip(unquote(b[len("file://"):].strip()))
            index(c); layer["clips"][C] = c
            self.send_response(204); self.end_headers(); ws_broadcast_comp(); return
        if parts[-2:-1] and parts[-3] == "columns" and parts[-1] == "connect" and b == "true":
            n = int(parts[-2])
            for layer in COMP["layers"]:
                c = layer["clips"][n-1]
                if c["connected"]["value"] == "Empty": continue
                for o in layer["clips"]:
                    if o["connected"]["value"].startswith("Connected"): o["connected"]["value"] = "Disconnected"
                c["connected"]["value"] = "Connected"
            for i, col in enumerate(COMP["columns"]):
                col["connected"]["value"] = "Connected" if i == n-1 else "Disconnected"
        if parts[-1] == "select":
            for layer in COMP["layers"]:
                for c in layer["clips"]: c["selected"] = {"value": False}
            COMP["layers"][int(parts[5])-1]["clips"][int(parts[7])-1]["selected"] = {"value": True}
        if parts[-1] == "clear":
            for c in COMP["layers"][int(parts[5])-1]["clips"]:
                if c["connected"]["value"].startswith("Connected"): c["connected"]["value"] = "Disconnected"
        if parts[-1] == "connect" and "clips" in parts and b == "true":
            L, C = int(parts[5]), int(parts[7])
            for c in COMP["layers"][L-1]["clips"]:
                if c["connected"]["value"].startswith("Connected"): c["connected"]["value"] = "Disconnected"
            COMP["layers"][L-1]["clips"][C-1]["connected"]["value"] = "Connected"
        self.send_response(204); self.end_headers()
        ws_broadcast()
    def do_DELETE(self):
        parts = self.path.split("/")
        if len(parts) > 8 and parts[6] == "effects" and parts[7] == "video":
            fx = COMP["layers"][int(parts[5]) - 1]["video"]["effects"]; i = int(parts[8])
            if i >= len(fx): self.send_response(404); self.end_headers(); return
            fx.pop(i); self.send_response(204); self.end_headers(); ws_broadcast_comp(); return
        self.send_response(404); self.end_headers()
    def do_PUT(self):
        b = self._body(); print("PUT", self.path, b, flush=True)
        pid = int(self.path.rsplit("/",1)[-1]); body = json.loads(b)
        if BYID[pid]["valuetype"] == "ParamEvent":
            EVENTS[str(pid)] = EVENTS.get(str(pid), 0) + 1
        elif "value" in body: BYID[pid]["value"] = body["value"]; note_opacity(pid, body["value"])
        elif "index" in body: BYID[pid]["value"] = BYID[pid]["options"][body["index"]]
        self.send_response(204); self.end_headers()
        ws_broadcast()
ThreadingHTTPServer(("127.0.0.1", 8080), H).serve_forever()
