"""End-to-end test without hardware: fake Push 2 object + mock Resolume.

    python tests/mock_resolume.py &      # terminal 1 (or background)
    python tests/test_fake_push.py       # terminal 2

Replaces push2_python.Push2 with a recorder, runs the real run() loop in a thread,
fires handlers through push2-python's action registry, and prints what was sent.
Check the mock's output for the matching POST/PUT requests.
"""
import os
import sys
import tempfile
import threading
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import push2_python
import requests
import yaml
from push2_python import action_handler_registry as REG

calls = []


class FakePush:
    def __init__(self, run_simulator=False):
        rec = lambda name: (lambda *a, **k: calls.append((name, a)))
        self.pads = types.SimpleNamespace(set_pad_color=rec("pad"), set_all_pads_to_color=rec("allpads"))
        self.buttons = types.SimpleNamespace(set_button_color=rec("btn"))
        self.display = types.SimpleNamespace(display_frame=rec("frame"))

    def midi_is_configured(self): return True
    def configure_midi(self): pass
    def set_color_palette_entry(self, *a, **k): calls.append(("palette", a))
    def reapply_color_palette(self): calls.append(("reapply", ()))
    def stop_active_sensing_thread(self): pass


push2_python.Push2 = FakePush
import push_resolume_bridge as B  # noqa: E402

cfg = B.load_config(Path(B.__file__).with_name("config.yaml"))
pins = Path(tempfile.mkdtemp()) / "pins.yaml"       # never touch the real pins.yaml
cfg["pins_file"] = str(pins)
colors_file = pins.with_name("colors.yaml")          # never touch the real colors.yaml
cfg["colors_file"] = str(colors_file)
cfg["resolume"]["ws_refresh"] = 30.0     # REST safety refresh rare → everything below runs on live updates
POLL_ONLY = os.environ.get("TEST_POLL") == "1"      # TEST_POLL=1: WebSocket off, test the polling fallback
cfg["resolume"]["websocket"] = not POLL_ONLY
rest = B.Resolume("127.0.0.1", 8080)
threading.Thread(target=B.run, args=(cfg, rest), daemon=True).start()
time.sleep(1.0)


def fire(action, *args):
    REG[action][0](None, *args)   # push2-python only calls the first handler per action


def state(comp, L, C):
    return comp["layers"][L - 1]["clips"][C - 1]["connected"]["value"]


fire("on_pad_pressed", 60, (7, 1), 100)          # plain press = select only
fire("on_pad_released", 60, (7, 1), 0)
time.sleep(0.5)
assert state(rest.composition(), 1, 2) == "Disconnected", "plain press must not launch"
assert rest.composition()["layers"][0]["clips"][1].get("selected", {}).get("value"), "press must select in Resolume"

fire("on_button_pressed", "Play")                 # B_1 held + pad = launch
fire("on_pad_pressed", 60, (7, 3), 100)          # bottom row = layer 1, column 4
fire("on_pad_released", 60, (7, 3), 0)
time.sleep(0.3)                                   # let a frame light Play
fire("on_button_released", "Play")
time.sleep(0.3)
assert state(rest.composition(), 1, 4) == "Connected", "Play + pad must launch"
assert ("btn", ("Play", "green")) in calls, "Play not lit while held"

fire("on_button_pressed", "Record")               # B_2 held + pad = stop layer
fire("on_pad_pressed", 60, (6, 0), 100)          # layer 2, any column
fire("on_pad_released", 60, (6, 0), 0)
fire("on_button_released", "Record")
time.sleep(0.5)
assert state(rest.composition(), 2, 2) == "Disconnected", "Record + pad must stop the layer"
fire("on_button_pressed", "Shift")
fire("on_encoder_rotated", "Track1 Encoder", -1)  # fine step
fire("on_button_released", "Shift")
fire("on_encoder_rotated", "Master Encoder", 5)   # selected layer opacity
fire("on_encoder_touched", "Track2 Encoder")
time.sleep(0.5)

before = rest.composition()
fire("on_button_pressed", "Mix")          # B_3 → mix mode
fire("on_encoder_rotated", "Track1 Encoder", -5)  # top layer (3) master
fire("on_encoder_rotated", "Master Encoder", -5)  # composition master
time.sleep(0.3)                                   # let a frame light B_3
fire("on_button_pressed", "Mix")          # back to params
time.sleep(1.0)
after = rest.composition()
assert after["layers"][2]["master"]["value"] < before["layers"][2]["master"]["value"], "layer 3 master unchanged"
assert after["layers"][0]["master"]["value"] == before["layers"][0]["master"]["value"], "wrong layer changed"
assert after["master"]["value"] < before["master"]["value"], "composition master unchanged"
assert ("btn", ("Mix", "white")) in calls, "B_3 not lit in mix mode"


# --- move a param: L1 C1 has 10 auto slots → 2 pages. Frequency (p1 K2) ↔ Speed (p2 K2)
fire("on_pad_pressed", 60, (7, 0), 100); fire("on_pad_released", 60, (7, 0), 0)
time.sleep(0.2)
fire("on_button_pressed", "Convert")
fire("on_encoder_touched", "Track2 Encoder")
fire("on_encoder_released", "Track2 Encoder")
fire("on_button_released", "Convert")
fire("on_button_pressed", "Lower Row 2")          # BD2 → page 2
time.sleep(0.3)
assert ("btn", ("Lower Row 2", "white")) in calls, "BD2 not lit on page 2"
assert ("btn", ("Convert", "white")) in calls, "Convert not lit while moving"
fire("on_encoder_touched", "Track2 Encoder")      # target: page 2, K2
fire("on_encoder_released", "Track2 Encoder")
fire("on_button_pressed", "Lower Row 1")
order = yaml.safe_load(pins.read_text())["order"]
assert order[1] == "transport/controls/speed" and order[9] == "video/sourceparams/frequency", order

# order carries over: L2 C2 (Comets down) natural = Opacity, Position X, Scale, Speed → K2 = Speed
fire("on_pad_pressed", 60, (6, 1), 100); fire("on_pad_released", 60, (6, 1), 0)
before = rest.composition()
fire("on_encoder_rotated", "Track2 Encoder", 10)
time.sleep(0.5)
after = rest.composition()
spd = lambda c: c["layers"][1]["clips"][1]["transport"]["controls"]["speed"]["value"]
assert spd(after) > spd(before), "K2 on L2 C2 should now be Speed"

# --- F8 tempo: taps → Resolume's own tap event, Shift + Tap → resync; K10 +3 BPM
events = lambda: requests.get("http://127.0.0.1:8080/api/v1/_events").json()
tc = rest.composition()["tempocontroller"]
tap_id, resync_id = str(tc["tempo_tap"]["id"]), str(tc["resync"]["id"])
for _ in range(3):
    fire("on_button_pressed", "Tap Tempo"); fire("on_button_released", "Tap Tempo")
    time.sleep(0.25)
fire("on_button_pressed", "Shift"); fire("on_button_pressed", "Tap Tempo")
fire("on_button_released", "Tap Tempo"); fire("on_button_released", "Shift")
time.sleep(0.4)
assert events().get(tap_id) == 3 and events().get(resync_id) == 1, events()
bpm = rest.composition()["tempocontroller"]["tempo"]["value"]
fire("on_encoder_rotated", "Tempo Encoder", 3)
time.sleep(0.4)
bpm2 = rest.composition()["tempocontroller"]["tempo"]["value"]
assert abs(bpm2 - bpm - 3) < 0.01, (bpm, bpm2)

# --- F9 beat: Tap Tempo flashes on the beat, playing pads pulse between full and mid
time.sleep(1.2)                                   # > 2 beats at 123 BPM
assert ("btn", ("Tap Tempo", "white")) in calls and ("btn", ("Tap Tempo", "dark_gray")) in calls
assert any(n == "pad" and str(a[1]).endswith("_mid") for n, a in calls), "playing pads don't pulse"
fire("on_button_pressed", "Metronome")            # pulse off
time.sleep(0.2)
assert ("btn", ("Metronome", "dark_gray")) in calls
fire("on_button_pressed", "Metronome")

# --- COLOR menu on L1 C1 (Color #ff8b58ff, BG Color #00000000)
fire("on_pad_pressed", 60, (7, 0), 100); fire("on_pad_released", 60, (7, 0), 0)
fire("on_button_pressed", "Upper Row 2")
colr = lambda: rest.composition()["layers"][0]["clips"][0]["video"]["sourceparams"]["Color"]["value"]
bg = lambda: rest.composition()["layers"][0]["clips"][0]["video"]["sourceparams"]["BG Color"]["value"]
fire("on_encoder_rotated", "Track3 Encoder", 10)  # blue 0x58 + 30 = 0x76
time.sleep(0.4)
assert colr() == "#ff8b76ff", colr()
fire("on_encoder_rotated", "Track6 Encoder", -100)  # brightness → 0 = black, alpha kept
time.sleep(0.4)
assert colr() == "#000000ff", colr()
fire("on_encoder_rotated", "Track6 Encoder", 100)   # hue/sat remembered → back to the same hue
time.sleep(0.4)
assert colr()[:3] == "#ff", colr()
fire("on_button_pressed", "Lower Row 5")           # palette 5 = blue
time.sleep(0.4)
assert colr() == "#0000ffff", colr()
assert ("btn", ("Lower Row 5", "P4")) in calls, "BD not lit with palette colours"
fire("on_encoder_rotated", "Track8 Encoder", 4)    # K8 → BG Color
fire("on_button_pressed", "Lower Row 2")           # red
time.sleep(0.4)
assert bg() == "#ff0000ff" and colr() == "#0000ffff", (bg(), colr())
fire("on_button_pressed", "Upper Row 1")

# --- F2 blackout: Stop Clip → composition master 0, again → back
m0 = rest.composition()["master"]["value"]
fire("on_button_pressed", "Stop")
time.sleep(0.6)
assert rest.composition()["master"]["value"] == 0.0, "blackout must zero the master"
assert ("btn", ("Stop", "red")) in calls, "Stop Clip must blink red in blackout"
fire("on_button_pressed", "Stop")
time.sleep(0.4)
assert rest.composition()["master"]["value"] == m0, "blackout off must restore the master"

# --- F3 flash: button right of pad row 6 (= layer 2) → 100 % while held
lm = lambda L: rest.composition()["layers"][L - 1]["master"]["value"]
before2 = lm(2)
fire("on_button_pressed", "1/4t")
time.sleep(0.4)
assert lm(2) == 1.0, "flash must set the layer master to 1"
fire("on_button_released", "1/4t")
time.sleep(0.4)
assert lm(2) == before2, "flash release must restore the layer master"

# --- F4 column launch: Play + Lower Row 1 → column 1
fire("on_button_pressed", "Play")
time.sleep(0.2)
fire("on_button_pressed", "Lower Row 1"); fire("on_button_released", "Lower Row 1")
time.sleep(0.2)
fire("on_button_released", "Play")
time.sleep(0.4)
assert rest.composition()["columns"][0]["connected"]["value"] == "Connected", "column 1 not launched"
assert ("btn", ("Lower Row 1", "green")) in calls or ("btn", ("Lower Row 1", "dark_gray")) in calls

# --- F5 mute / solo
flag = lambda L, k: rest.composition()["layers"][L - 1][k]["value"]
fire("on_button_pressed", "Mute")
fire("on_pad_pressed", 60, (7, 0), 100); fire("on_pad_released", 60, (7, 0), 0)   # layer 1
fire("on_button_released", "Mute")
time.sleep(0.4)
assert flag(1, "bypassed") is True, "Mute + pad must mute the layer"
assert any(n == "pad" and a[0][0] == 7 and a[1] == "dark_gray" for n, a in calls), "muted layer pads not greyed"
fire("on_button_pressed", "Solo")
fire("on_pad_pressed", 60, (6, 1), 100); fire("on_pad_released", 60, (6, 1), 0)   # layer 2
fire("on_button_released", "Solo")
time.sleep(0.4)
assert flag(2, "solo") is True, "Solo + pad must solo the layer"
fire("on_button_pressed", "Mix")                   # MIX: K1 = layer 3, K2 = layer 2, K3 = layer 1
fire("on_button_pressed", "Lower Row 3")           # unmute layer 1
fire("on_button_pressed", "Solo"); fire("on_button_pressed", "Lower Row 2"); fire("on_button_released", "Solo")
time.sleep(0.4)
assert flag(1, "bypassed") is False and flag(2, "solo") is False, "MIX Lower Row mute / Solo+Lower Row solo"
fire("on_button_pressed", "Mix")

# --- F10 master colour: Master button → composition Colorize (white, bypassed, opacity 1)
fx = lambda: rest.composition()["video"]["effects"][0]
fire("on_button_pressed", "Master")
fire("on_encoder_rotated", "Track1 Encoder", -10)   # red 255 - 30
fire("on_encoder_rotated", "Track7 Encoder", -10)   # amount 1.0 → 0.9
fire("on_encoder_rotated", "Track8 Encoder", 1)     # effect on
time.sleep(0.4)
assert fx()["params"]["Color"]["value"] == "#e1ffffff", fx()["params"]["Color"]["value"]
assert abs(fx()["params"]["Opacity"]["value"] - 0.9) < 1e-6 and fx()["bypassed"]["value"] is False, fx()
assert ("btn", ("Master", "white")) in calls
fire("on_button_pressed", "Master")                 # back to the clip

# --- F11 own palette: Shift + Lower Row 3 saves the current clip colour, Lower Row 3 applies it
fire("on_pad_pressed", 60, (7, 0), 100); fire("on_pad_released", 60, (7, 0), 0)     # L1 C1
fire("on_button_pressed", "Upper Row 2")
time.sleep(0.2)
cur = rest.composition()["layers"][0]["clips"][0]["video"]["sourceparams"]["Color"]["value"]
fire("on_button_pressed", "Shift"); fire("on_button_pressed", "Lower Row 3"); fire("on_button_released", "Shift")
assert yaml.safe_load(colors_file.read_text())["palette"][2] == cur, colors_file.read_text()

# --- F12 paste: Duplicate + Lower Row 1 = column 1 → L3 C1 (Clouds) gets L1 C1's colour
fire("on_button_pressed", "Duplicate")
fire("on_button_pressed", "Lower Row 1"); fire("on_button_released", "Lower Row 1")
fire("on_button_released", "Duplicate")
time.sleep(0.4)
clouds = rest.composition()["layers"][2]["clips"][0]["video"]["sourceparams"]["Color"]["value"]
assert clouds == cur, (clouds, cur)
fire("on_button_pressed", "Upper Row 1")

# --- F13 FX menu on L1 C1: [Clip Transform, Layer Hue Rotate (no bypass), Comp Colorize]
fire("on_pad_pressed", 60, (7, 0), 100); fire("on_pad_released", 60, (7, 0), 0)
fire("on_button_pressed", "Upper Row 3")
op0 = fx()["params"]["Opacity"]["value"]
fire("on_button_pressed", "Lower Row 1")          # Transform off
fire("on_encoder_rotated", "Track3 Encoder", -10) # Colorize amount -10 %
fire("on_button_pressed", "Lower Row 3")          # Colorize off
time.sleep(0.4)
tr = rest.composition()["layers"][0]["clips"][0]["video"]["effects"][0]["bypassed"]["value"]
assert tr is True, "BD1 must bypass the clip's Transform"
assert abs(fx()["params"]["Opacity"]["value"] - (op0 - 0.1)) < 1e-6 and fx()["bypassed"]["value"] is True, fx()
assert ("btn", ("Lower Row 2", "black")) in calls, "effect without bypass must stay unlit"
fire("on_button_pressed", "Upper Row 1")

# --- F16 live updates: a change made elsewhere (e.g. mouse in Arena) reaches the pads without polling
L3 = rest.composition()["layers"][2]
if not POLL_ONLY:
    requests.put(f"http://127.0.0.1:8080/api/v1/parameter/by-id/{L3['bypassed']['id']}", json={"value": True})
    n0 = len(calls)
    time.sleep(0.6)
    assert any(n == "pad" and a[0][0] == 5 and a[1] in ("dark_gray", "light_gray") for n, a in calls[n0:]), \
        "external mute of layer 3 didn't reach the pads via WebSocket"
    requests.put(f"http://127.0.0.1:8080/api/v1/parameter/by-id/{L3['bypassed']['id']}", json={"value": False})

counts = {}
for name, _ in calls:
    counts[name] = counts.get(name, 0) + 1
print("calls:", counts)
print("last pad colours:", [a for n, a in calls if n == "pad"][-6:])
assert counts.get("palette", 0) >= 16 and counts.get("frame", 0) > 10, "loop did not run as expected"
print("OK")
