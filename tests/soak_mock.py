"""Soak test with every thread running: the real run() loop (fake Push, as in test_fake_push.py) against
tests/mock_resolume.py over HTTP and its WebSocket, while a driver presses random controls and changes
the composition through the mock: Bar Chaser added / removed, Track and Pad changes, layers added and
deleted, another composition opened, fewer or more fixtures, tempo changes.

At the end: no traceback in the bridge's output, the run loop still drawing frames, and after stopping
everything no Bar Chaser level left lit and every Bar Chaser showing the same mapping.

    python tests/soak_mock.py            # 60 s (starts its own mock on a free port)
    python tests/soak_mock.py 20         # 20 s
"""
from __future__ import annotations

import os
import random
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SECONDS = float(sys.argv[1]) if len(sys.argv) > 1 else 60.0
with socket.socket() as s_:
    s_.bind(("127.0.0.1", 0))
    PORT = s_.getsockname()[1]
os.environ["MOCK_PORT"] = str(PORT)
os.environ["PUSH2_DATA_DIR"] = tempfile.mkdtemp()
API = f"http://127.0.0.1:{PORT}/api/v1"

mock_log = open(Path(tempfile.mkdtemp()) / "mock.log", "w")
mock = subprocess.Popen([sys.executable, "-W", "ignore", str(ROOT / "tests" / "mock_resolume.py")],
                        stdout=mock_log, stderr=subprocess.STDOUT, env=dict(os.environ))
time.sleep(1.0)

import requests  # noqa: E402
import push2_python  # noqa: E402
from push2_python import action_handler_registry as REG  # noqa: E402

OUT = []


class Tee:
    def __init__(self, out):
        self.out = out

    def write(self, s):
        OUT.append(s)
        return self.out.write(s)

    def flush(self):
        self.out.flush()


sys.stdout, sys.stderr = Tee(sys.stdout), Tee(sys.stderr)
FRAMES = [0]


class FakePush:
    def __init__(self, run_simulator=False):
        nop = lambda *a, **k: None
        self.pads = types.SimpleNamespace(set_pad_color=nop, set_all_pads_to_color=nop)
        self.buttons = types.SimpleNamespace(set_button_color=nop)
        self.display = types.SimpleNamespace(display_frame=lambda *a, **k: FRAMES.__setitem__(0, FRAMES[0] + 1))

    def midi_is_configured(self): return True
    def configure_midi(self): pass
    def set_color_palette_entry(self, *a, **k): pass
    def reapply_color_palette(self): pass
    def stop_active_sensing_thread(self): pass


push2_python.Push2 = FakePush
import push_resolume_bridge as B  # noqa: E402

CAP = []
_Bridge = B.Bridge


class Captured(_Bridge):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        CAP.append(self)


B.Bridge = Captured

try:
    if requests.get(API + "/product", timeout=2).json().get("name") != "Mock Resolume":
        sys.exit("not the mock")
except Exception as e:
    mock.kill()
    sys.exit(f"mock did not start: {e}")

cfg = B.load_config(ROOT / "config.yaml")
cfg["resolume"]["port"] = PORT
cfg["resolume"]["ws_refresh"] = 2.0
cfg["sequencer"]["preset_folder"] = str(ROOT / "tests" / "fixtures" / "rig")
runner = threading.Thread(target=B.run, args=(cfg, B.Resolume("127.0.0.1", PORT)), daemon=True)
runner.start()
time.sleep(1.5)
br = CAP[0]


def fire(action, *args):
    try:
        REG[action][0](None, *args)
    except Exception:
        traceback.print_exc()


def comp():
    return requests.get(API + "/composition", timeout=3).json()


def chasers(c):
    return [(L, i, fx) for L, layer in enumerate(c["layers"], 1)
            for i, fx in enumerate(layer["video"].get("effects") or []) if fx.get("name") == "Bar Chaser"]


def put(pid, value):
    requests.put(f"{API}/parameter/by-id/{pid}", json={"value": value}, timeout=3)


def post(path, body=""):
    requests.post(API + path, data=body, timeout=3)


BUTTONS = ["Shift", "Play", "Record", "Mix", "Mute", "Solo", "Convert", "Tap Tempo", "Stop", "Master", "Duplicate",
           "Note", "Session", "Browse", "Repeat", "Accent", "Delete", "Select", "Layout", "Scale", "Up", "Down",
           "Left", "Right", "Page Left", "Page Right", "Octave Up", "Octave Down", "Double Loop", "Fixed Length"] \
    + [f"Upper Row {i}" for i in range(1, 9)] + [f"Lower Row {i}" for i in range(1, 9)] \
    + ["1/32t", "1/32", "1/16t", "1/16", "1/8t", "1/8", "1/4t", "1/4"]
ENC = [f"Track{i} Encoder" for i in range(1, 9)] + ["Master Encoder", "Tempo Encoder", "Swing Encoder"]


def push_driver(stop, rng):
    held, pads = set(), set()
    while not stop.is_set():
        r = rng.random()
        if r < 0.4:
            if held and rng.random() < 0.5:
                b = rng.choice(sorted(held)); held.discard(b); fire("on_button_released", b)
            else:
                b = rng.choice(BUTTONS); held.add(b); fire("on_button_pressed", b)
        elif r < 0.75:
            if pads and rng.random() < 0.5:
                ij = rng.choice(sorted(pads)); pads.discard(ij); fire("on_pad_released", 0, ij, 0)
            else:
                ij = (rng.randrange(8), rng.randrange(8)); pads.add(ij); fire("on_pad_pressed", 0, ij, rng.randrange(1, 128))
        else:
            fire("on_encoder_rotated", rng.choice(ENC), rng.choice([-3, -1, 1, 3]))
        time.sleep(rng.choice([0.005, 0.02, 0.05]))
    for b in held:
        fire("on_button_released", b)
    for ij in pads:
        fire("on_pad_released", 0, ij, 0)


def arena_driver(stop, rng, log):
    while not stop.is_set():
        try:
            c = comp()
            n = len(c["layers"])
            ch = chasers(c)
            op = rng.choice(["add chaser", "add chaser", "remove chaser", "track", "track", "pad", "add layer",
                             "delete layer", "new composition", "options", "tempo", "steps"])
            if op == "add chaser" and n:
                post(f"/composition/layers/{rng.randrange(1, n + 1)}/effects/video/add", "effect:///video/Bar%20Chaser")
            elif op == "remove chaser" and ch:
                L, i, _ = rng.choice(ch)
                requests.delete(f"{API}/composition/layers/{L}/effects/video/{i}", timeout=3)
            elif op == "track" and ch:
                put(rng.choice(ch)[2]["params"]["Track"]["id"], str(rng.randrange(1, 5)))
            elif op == "pad" and ch:
                p = rng.choice(ch)[2]["params"][f"Pad {rng.randrange(1, 25)}"]
                put(p["id"], rng.choice(p["options"]))
            elif op == "add layer" and n < 7:
                post("/composition/layers/add")
            elif op == "delete layer" and n > 1:
                post("/_delete_layer", str(rng.randrange(1, n + 1)))
            elif op == "new composition" and rng.random() < 0.3:
                post("/_new_composition")
            elif op == "options":
                post("/_options", str(rng.choice([3, 5, 11])))
            elif op == "tempo":
                put(c["tempocontroller"]["tempo"]["id"], rng.choice([90.0, 120.0, 140.0]))
            elif op == "steps":
                with br.lock:
                    pat = br.seq.pattern
                    for _ in range(6):
                        pat.tracks[rng.randrange(4)].steps.setdefault(f"pad {rng.randrange(1, 6)}", {})[rng.randrange(16)] = [1.0, None]
                    if not br.seq.running:
                        br.seq.start(br.beat_time() or 0.0)
            log.append(op)
        except Exception as e:
            log.append(f"driver error {type(e).__name__}: {e}")
        time.sleep(rng.choice([0.1, 0.3, 0.6]))


stop = threading.Event()
log = []
rng = random.Random(7)
drivers = [threading.Thread(target=push_driver, args=(stop, random.Random(1)), daemon=True),
           threading.Thread(target=arena_driver, args=(stop, rng, log), daemon=True)]
for d in drivers:
    d.start()
t_end = time.time() + SECONDS
frames0 = FRAMES[0]
while time.time() < t_end:
    time.sleep(1.0)
stop.set()
for d in drivers:
    d.join(timeout=5)

# ---- wind down and check ----
frames_during = FRAMES[0] - frames0
with br.lock:
    br.seq.stop()
    bt = br.beat_time() or 0.0
    for (t, bar), v in list(br.seq.voices.items()):
        if v.gate is None:
            br.seq.release(t, bar, bt)
br.release_all()
time.sleep(5.0)                                  # releases end, the Sender and the WebSocket deliver
c = comp()
lit = {(L, k): v for L, _, fx in chasers(c) for k, v in ((k, p["value"]) for k, p in fx["params"].items()
                                                        if k.startswith("Level ")) if v}
want = br.seq.pad_config or []
mismatch = []
for L, _, fx in chasers(c):
    for k, v in enumerate(want):
        p = fx["params"][f"Pad {k + 1}"]
        if v in p["options"] and p["value"] != v and any(o != "—" for o in p["options"]):
            mismatch.append((L, k + 1, v, p["value"]))
text = "".join(OUT)
tracebacks = text.count("Traceback")
ops = {}
for op in log:
    ops[op] = ops.get(op, 0) + 1
mock.kill()
sys.stdout, sys.stderr = sys.stdout.out, sys.stderr.out
print(f"\n{SECONDS:.0f} s · driver ops {ops}")
print(f"frames drawn during the run: {frames_during} · run loop alive: {runner.is_alive()} · "
      f"live updates at the end: {br.live()} · WebSocket drops: {text.count('live updates off')}")
print(f"tracebacks: {tracebacks} · bars lit after stop: {lit} · mapping mismatches: {mismatch[:5]}")
if tracebacks:
    i = text.find("Traceback")
    print(text[i:i + 2500])
ok = not tracebacks and runner.is_alive() and frames_during > SECONDS * 5 and not lit and not mismatch and br.live()
print("OK" if ok else "FAILED")
sys.exit(0 if ok else 1)
