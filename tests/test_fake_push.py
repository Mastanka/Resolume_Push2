"""End-to-end test without hardware: fake Push 2 object + mock Resolume.

    python tests/mock_resolume.py &      # terminal 1 (or background)
    python tests/test_fake_push.py       # terminal 2

Replaces push2_python.Push2 with a recorder, runs the real run() loop in a thread,
fires handlers through push2-python's action registry, and prints what was sent.
Check the mock's output for the matching POST/PUT requests.
"""
import sys
import tempfile
import threading
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import push2_python
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

# --- tempo: 3 taps 0.25 s apart ≈ 240 BPM, then K10 +3
for _ in range(3):
    fire("on_button_pressed", "Tap Tempo"); fire("on_button_released", "Tap Tempo")
    time.sleep(0.25)
time.sleep(0.4)
bpm = rest.composition()["tempocontroller"]["tempo"]["value"]
assert 225 < bpm < 255, bpm
fire("on_encoder_rotated", "Tempo Encoder", 3)
time.sleep(0.4)
bpm2 = rest.composition()["tempocontroller"]["tempo"]["value"]
assert abs(bpm2 - bpm - 3) < 0.01, (bpm, bpm2)

counts = {}
for name, _ in calls:
    counts[name] = counts.get(name, 0) + 1
print("calls:", counts)
print("last pad colours:", [a for n, a in calls if n == "pad"][-6:])
assert counts.get("palette") == 16 and counts.get("frame", 0) > 10, "loop did not run as expected"
print("OK")
