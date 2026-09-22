"""Minimal fake of Resolume Arena's REST API for offline testing.
Serves a small composition on http://127.0.0.1:8080/api/v1 and logs every POST/PUT.
Run:  python tests/mock_resolume.py
"""
import json, itertools
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
ids = itertools.count(1000)
def rng(v, lo=0.0, hi=1.0): return {"id": next(ids), "valuetype": "ParamRange", "value": v, "min": lo, "max": hi}
def s(v): return {"id": next(ids), "valuetype": "ParamString", "value": v}
def clip(name, state="Disconnected", gen=False):
    if name is None:
        return {"name": s(""), "connected": {"id": next(ids), "valuetype": "ParamState", "value": "Empty"}}
    c = {"name": s(name), "connected": {"id": next(ids), "valuetype": "ParamState", "value": state},
         "transport": {"position": rng(0.3, 0, 5000), "controls": {"speed": rng(1, 0, 10)}},
         "video": {"opacity": rng(1), "effects": [{"name": "Transform", "id": next(ids), "bypassed": {"id": next(ids), "valuetype": "ParamBoolean", "value": False},
                   "params": {"Position X": rng(0, -16384, 16384), "Scale": rng(100, 0, 1000)}}]}}
    if gen:
        c["video"]["sourceparams"] = {"Frequency": rng(0.32), "Fade": {"id": next(ids), "valuetype": "ParamBoolean", "value": True}}
    return c
COMP = {"master": rng(0.9), "video": {"opacity": rng(1.0)}, "layers": [
  {"name": s("Strobe"), "master": rng(1.0), "video": {"opacity": rng(0.8), "mixer": {"Blend Mode": {"id": next(ids), "valuetype": "ParamChoice", "value": "Add", "index": 1, "options": ["Alpha", "Add", "Multiply", "Screen"]}},
     "effects": [{"name": "HueRotate", "display_name": "Hue Rotate", "id": next(ids), "params": {"Hue Rotate": rng(0.2)}}]},
   "audio": {"volume": rng(0, -60, 6)},
   "clips": [clip("Stroboscope", "Connected", gen=True), clip("Rolling strobe"), clip(None), clip("Odd/Even")]},
  {"name": s("Comets"), "master": rng(0.75), "video": {"opacity": rng(1.0), "effects": []}, "clips": [clip(None), clip("Comets down", "Connected"), clip(None), clip(None)]},
  {"name": s("Ambient clouds"), "master": rng(0.5), "video": {"opacity": rng(0.4), "effects": []}, "clips": [clip("Clouds"), clip(None), clip(None), clip(None)]},
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
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _body(self): return self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
    def do_GET(self):
        d = json.dumps(COMP).encode(); self.send_response(200); self.send_header("Content-Type","application/json"); self.end_headers(); self.wfile.write(d)
    def do_POST(self):
        b = self._body(); LOG.append(("POST", self.path, b)); print("POST", self.path, b, flush=True)
        parts = self.path.split("/")
        if parts[-1] == "clear":
            for c in COMP["layers"][int(parts[5])-1]["clips"]:
                if c["connected"]["value"].startswith("Connected"): c["connected"]["value"] = "Disconnected"
        if parts[-1] == "connect" and b == "true":
            L, C = int(parts[5]), int(parts[7])
            for c in COMP["layers"][L-1]["clips"]:
                if c["connected"]["value"].startswith("Connected"): c["connected"]["value"] = "Disconnected"
            COMP["layers"][L-1]["clips"][C-1]["connected"]["value"] = "Connected"
        self.send_response(204); self.end_headers()
    def do_PUT(self):
        b = self._body(); print("PUT", self.path, b, flush=True)
        pid = int(self.path.rsplit("/",1)[-1]); body = json.loads(b)
        if "value" in body: BYID[pid]["value"] = body["value"]
        self.send_response(204); self.end_headers()
ThreadingHTTPServer(("127.0.0.1", 8080), H).serve_forever()
