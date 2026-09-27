"""Resolume Arena REST access: JSON helpers, REST client and the background sender."""

from __future__ import annotations

import json
import queue
import sys
import threading
import time
import traceback

import requests
import urllib3

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
    """'#rrggbbaa' (Resolume ParamColor) or '#rrggbb' (a hand-edited palette) -> [r, g, b, a] ints."""
    v = (v or "").lstrip("#")
    try:
        if len(v) not in (6, 8):
            raise ValueError(v)
        vals = [int(v[i:i + 2], 16) for i in range(0, len(v), 2)]
        return vals if len(vals) == 4 else vals + [255]
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

RETRIES = 2           # extra attempts when Resolume drops a connection without answering
OUTBOX = 256          # WebSocket sets waiting for the socket; older ones make room for newer ones


def _dropped(e):
    """True for a connection Arena closed without answering (safe to repeat), not for a refusal
    (would only be refused again) and not for a timeout (the request may have run)."""
    inner = e.args[0] if e.args else None
    return isinstance(inner, urllib3.exceptions.ProtocolError) or "without response" in str(e)


class Resolume:
    def __init__(self, host, port):
        self.url = f"http://{host}:{port}"
        self.api = self.url + "/api/v1"
        self.session = requests.Session()   # the sender thread, plus one-off setup calls

    def _req(self, method, url, session=None, **kw):
        """One HTTP request. When Arena closes a kept-alive connection before answering ("Remote
        end closed connection without response"), the request did not run, so it goes out again on
        a fresh connection. Nothing else is repeated: a refused connection stays refused (Arena is
        not running), and a timed-out request may still have run (a trigger must not fire twice)."""
        s = session or self.session
        for attempt in range(RETRIES + 1):
            try:
                return s.request(method, url, **kw)
            except requests.exceptions.ConnectionError as e:
                if attempt == RETRIES or not _dropped(e):
                    raise

    def composition(self, session=None):
        r = self._req("GET", self.api + "/composition", session=session, timeout=2)
        r.raise_for_status()
        return r.json()

    def connect_clip(self, layer, column, down):
        # true = press, false = release (same as mouse down/up on the clip)
        self._req("POST", f"{self.api}/composition/layers/{layer}/clips/{column}/connect",
                  data=json.dumps(bool(down)), headers={"Content-Type": "application/json"}, timeout=1)

    def select_clip(self, layer, column):
        self._req("POST", f"{self.api}/composition/layers/{layer}/clips/{column}/select", timeout=1)

    def connect_column(self, column, down):
        self._req("POST", f"{self.api}/composition/columns/{column}/connect",
                  data=json.dumps(bool(down)), headers={"Content-Type": "application/json"}, timeout=1)

    def clear_layer(self, layer):
        self._req("POST", f"{self.api}/composition/layers/{layer}/clear", timeout=1)

    def set_param(self, param_id, body):
        self._req("PUT", f"{self.api}/parameter/by-id/{param_id}", json=body, timeout=1)

    # ---- composition editing (used by the step sequencer's layer engine) ---------- #
    def _post_text(self, path, body=""):
        r = self._req("POST", self.api + path, data=body.encode("utf-8"),
                      headers={"Content-Type": "text/plain"}, timeout=3)
        return r.status_code

    def add_layer(self, before=None):
        """Append a layer on top, or insert before the 1-based layer index `before`."""
        return self._post_text("/composition/layers/add", f"/composition/layers/{before}" if before else "")

    def add_effect(self, layer, name):
        return self._post_text(f"/composition/layers/{layer}/effects/video/add",
                               "effect:///video/" + name.replace(" ", "%20"))

    def delete_effect(self, layer, offset):
        return self._req("DELETE", f"{self.api}/composition/layers/{layer}/effects/video/{offset}",
                         timeout=3).status_code

    def set_effect_display_name(self, layer, index, name):
        return self._post_text(f"/composition/layers/{layer}/effects/video/{index}/set-display-name", name)

    def open_clip(self, layer, column, uri):
        """uri: 'source:///video/Metaballs' or 'file:///path/with%20spaces.mov'."""
        return self._post_text(f"/composition/layers/{layer}/clips/{column}/open", uri)

    def clear_clip(self, layer, column):
        return self._post_text(f"/composition/layers/{layer}/clips/{column}/clear")


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



class ResolumeWS(threading.Thread):
    """Live updates from Resolume's WebSocket (ws://host:port/api/v1).

    Arena sends the whole composition on connect (and when its structure changes), then a
    `parameter_update` for every subscribed parameter. Subscriptions go by id
    (`/parameter/by-id/<id>`); subscribing by path is rejected by Arena 7.23.
    The wanted ids come from `desired()` and are re-synced twice a second."""

    def __init__(self, host, port, on_comp, on_param, desired):
        super().__init__(daemon=True)
        self.url = f"ws://{host}:{port}/api/v1"
        self.on_comp, self.on_param, self.desired = on_comp, on_param, desired
        self.live = False
        self._warned = False
        self.outbox = queue.Queue(maxsize=OUTBOX)

    def set(self, pid, value):
        """Set a parameter over the socket (cheaper than a PUT). Dropped when not live; when the
        socket stalls, the oldest waiting set gives way to the newest (the levels are 100/s)."""
        if not self.live:
            return
        msg = {"action": "set", "parameter": f"/parameter/by-id/{pid}", "value": value}
        while True:
            try:
                self.outbox.put_nowait(msg)
                return
            except queue.Full:
                try:
                    self.outbox.get_nowait()
                except queue.Empty:
                    pass

    def run(self):
        import websocket   # websocket-client; only needed when the bridge runs
        while True:
            ws, subs = None, set()
            try:
                ws = websocket.create_connection(self.url, timeout=3)
                ws.settimeout(0.02)
                self.live, self._warned = True, False
                print("[resolume] live updates on (WebSocket)")
                next_sync = 0.0
                while True:
                    try:
                        msg = json.loads(ws.recv())
                        if isinstance(msg, dict) and "layers" in msg and "type" not in msg:
                            self.on_comp(msg)
                        elif isinstance(msg, dict) and msg.get("type") in ("parameter_update", "parameter_subscribed"):
                            self.on_param(msg.get("id"), msg.get("value"))
                    except websocket.WebSocketTimeoutException:
                        pass
                    while True:                                   # parameter sets from the bridge
                        try:
                            ws.send(json.dumps(self.outbox.get_nowait()))
                        except queue.Empty:
                            break
                    if time.time() >= next_sync:
                        next_sync = time.time() + 0.5
                        want = set(self.desired())
                        for pid in want - subs:
                            ws.send(json.dumps({"action": "subscribe", "parameter": f"/parameter/by-id/{pid}"}))
                        for pid in subs - want:
                            ws.send(json.dumps({"action": "unsubscribe", "parameter": f"/parameter/by-id/{pid}"}))
                        subs = want
            except Exception as e:
                if self.live or not self._warned:
                    print(f"[resolume] live updates off, polling instead ({type(e).__name__}: {e})")
                    if not isinstance(e, (OSError, websocket.WebSocketException)):
                        traceback.print_exc()            # a bug, not a network problem
                    self._warned = True
                self.live = False
                try:
                    ws and ws.close()
                except Exception:
                    pass
                time.sleep(2.0)
