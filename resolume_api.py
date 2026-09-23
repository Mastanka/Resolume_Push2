"""Resolume Arena REST access: JSON helpers, REST client and the background sender."""

from __future__ import annotations

import json
import queue
import sys
import threading
import time

import requests

EDITABLE = {"ParamRange", "ParamChoice", "ParamBoolean"}
WALK_SKIP = {"clips", "name", "connected", "selected", "thumbnail", "audio"}
MASTER_PATHS = ("master", "video/opacity")  # layer/composition master fader, fallback opacity


# --------------------------------------------------------------------------- #
# Resolume JSON helpers
# --------------------------------------------------------------------------- #

def text(v, default=""):
    """Resolume wraps most strings as {"value": "..."}; unwrap either form."""
    if isinstance(v, dict):
        v = v.get("value")
    return v if isinstance(v, str) else default


def is_param(node):
    return isinstance(node, dict) and "valuetype" in node and "id" in node


def names_of(item):
    return [n for n in (text(item.get("display_name")), text(item.get("name"))) if n]


def label_of(item):
    names = names_of(item)
    return names[0] if names else None


def master_param(node):
    """The master fader of a layer or the composition."""
    for path in MASTER_PATHS:
        p = resolve_node(node, path) if node else None
        if is_param(p):
            return p
    return None


def clip_state(clip):
    """'Empty', 'Disconnected', 'Previewing', 'Connected', 'Connected & previewing'."""
    return text(clip.get("connected"), "Empty") if clip else "Empty"


def resolve_node(node, path):
    """Follow a path like 'video/effects/Transform/params/Scale' through the JSON.
    Dict keys match case-insensitively; list items match by name or by index."""
    for seg in (s for s in path.split("/") if s):
        s = seg.lower()
        if isinstance(node, dict):
            key = next((k for k in node if k.lower() == s), None)
            if key is None:
                return None
            node = node[key]
        elif isinstance(node, list):
            match = next((it for it in node if isinstance(it, dict)
                          and s in (n.lower() for n in names_of(it))), None)
            if match is None and seg.isdigit() and int(seg) < len(node):
                match = node[int(seg)]
            if match is None:
                return None
            node = match
        else:
            return None
    return node


def walk(node, prefix="", skip=WALK_SKIP, types=EDITABLE):
    """Yield (path, param) for every parameter of the given value types below node.
    Paths are built so resolve_node() can find them again."""
    if is_param(node):
        if node.get("valuetype") in types:
            yield prefix, node
        return
    if isinstance(node, dict):
        for k, v in node.items():
            if k.lower() in skip:
                continue
            yield from walk(v, f"{prefix}/{k}" if prefix else k, skip, types)
    elif isinstance(node, list):
        seen = set()
        for i, item in enumerate(node):
            label = label_of(item) if isinstance(item, dict) else None
            if not label or "/" in label or label.lower() in seen:
                label = str(i)
            seen.add(label.lower())
            yield from walk(item, f"{prefix}/{label}" if prefix else label, skip, types)


def fmt_value(p, v, spec):
    """Return (display text, bar fraction 0..1)."""
    vt = p.get("valuetype")
    if vt == "ParamBoolean":
        return ("ON" if v else "OFF"), (1.0 if v else 0.0)
    if vt == "ParamChoice":
        opts = p.get("options") or []
        i = opts.index(v) if v in opts else 0
        return str(v), (i / (len(opts) - 1) if len(opts) > 1 else 0.0)
    lo, hi = spec.get("range") or (p.get("min", 0.0), p.get("max", 1.0))
    v = float(v or 0.0)
    span = (hi - lo) or 1.0
    frac = min(1.0, max(0.0, (v - lo) / span))
    if (p.get("min"), p.get("max")) == (0, 1):
        txt = f"{v * 100:.0f}%"
    elif abs(hi - lo) >= 20:
        txt = f"{v:.0f}"
    else:
        txt = f"{v:.2f}"
    return txt, frac


def hex_to_rgba(v):
    """'#rrggbbaa' (Resolume ParamColor) -> [r, g, b, a] ints."""
    v = (v or "").lstrip("#")
    try:
        vals = [int(v[i:i + 2], 16) for i in range(0, 8, 2)]
        return vals if len(v) >= 8 else vals[:3] + [255]
    except ValueError:
        return [0, 0, 0, 255]


def rgba_to_hex(rgba):
    return "#" + "".join(f"{int(round(min(255, max(0, c)))):02x}" for c in rgba)


def color_label(path):
    """'video/effects/Colorize/params/Color' -> 'Colorize Color'; 'video/sourceparams/BG Color' -> 'BG Color'."""
    parts = path.split("/")
    if "effects" in parts and len(parts) > parts.index("effects") + 1:
        fx = parts[parts.index("effects") + 1]
        return f"{fx} {parts[-1]}" if fx.lower() != parts[-1].lower() else fx
    return parts[-1]


# --------------------------------------------------------------------------- #
# Resolume REST client + background sender
# --------------------------------------------------------------------------- #

class Resolume:
    def __init__(self, host, port):
        self.url = f"http://{host}:{port}"
        self.api = self.url + "/api/v1"
        self.session = requests.Session()   # used by the sender thread only

    def composition(self, session=None):
        r = (session or self.session).get(self.api + "/composition", timeout=2)
        r.raise_for_status()
        return r.json()

    def connect_clip(self, layer, column, down):
        # true = press, false = release (same as mouse down/up on the clip)
        self.session.post(f"{self.api}/composition/layers/{layer}/clips/{column}/connect",
                          data=json.dumps(bool(down)),
                          headers={"Content-Type": "application/json"}, timeout=1)

    def select_clip(self, layer, column):
        self.session.post(f"{self.api}/composition/layers/{layer}/clips/{column}/select", timeout=1)

    def connect_column(self, column, down):
        self.session.post(f"{self.api}/composition/columns/{column}/connect",
                          data=json.dumps(bool(down)),
                          headers={"Content-Type": "application/json"}, timeout=1)

    def clear_layer(self, layer):
        self.session.post(f"{self.api}/composition/layers/{layer}/clear", timeout=1)

    def set_param(self, param_id, body):
        self.session.put(f"{self.api}/parameter/by-id/{param_id}", json=body, timeout=1)


class Sender(threading.Thread):
    """Sends clip triggers in order and parameter changes coalesced (latest value
    wins), so fast encoder turns never queue up behind the network."""

    def __init__(self, rest):
        super().__init__(daemon=True)
        self.rest = rest
        self.triggers = queue.Queue()
        self.params = {}
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self._last_err = 0.0

    def trigger(self, layer, column, down):
        self.triggers.put((self.rest.connect_clip, (layer, column, down)))
        self.wake.set()

    def select(self, layer, column):
        self.triggers.put((self.rest.select_clip, (layer, column)))
        self.wake.set()

    def event(self, param_id):
        """Trigger a ParamEvent (tap, resync). Ordered like clip triggers, never coalesced."""
        self.triggers.put((self.rest.set_param, (param_id, {"value": True})))
        self.wake.set()

    def column(self, column, down):
        self.triggers.put((self.rest.connect_column, (column, down)))
        self.wake.set()

    def clear(self, layer):
        self.triggers.put((self.rest.clear_layer, (layer,)))
        self.wake.set()

    def param(self, param_id, body):
        with self.lock:
            self.params[param_id] = body
        self.wake.set()

    def _err(self, e):
        if time.time() - self._last_err > 2:
            print(f"[resolume] {e}", file=sys.stderr)
            self._last_err = time.time()

    def run(self):
        while True:
            self.wake.wait(0.5)
            self.wake.clear()
            while not self.triggers.empty():
                try:
                    fn, args = self.triggers.get_nowait()
                    fn(*args)
                except Exception as e:
                    self._err(e)
            with self.lock:
                batch, self.params = self.params, {}
            for pid, body in batch.items():
                try:
                    self.rest.set_param(pid, body)
                except Exception as e:
                    self._err(e)

