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


class Tee:
    """Keep a copy of everything printed, to check the bridge's log at the end."""
    def __init__(self, out):
        self.out, self.text = out, []
    def write(self, s):
        self.text.append(s); return self.out.write(s)
    def flush(self):
        self.out.flush()


sys.stdout = Tee(sys.stdout)


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

# Never run against a real Arena: only the mock answers /product with this name.
try:
    _name = requests.get("http://127.0.0.1:8080/api/v1/product", timeout=2).json().get("name")
except Exception as e:
    sys.exit(f"mock not running on 127.0.0.1:8080 ({e}) — start tests/mock_resolume.py first")
if _name != "Mock Resolume":
    sys.exit(f"refusing to run: 127.0.0.1:8080 is {_name!r}, not the mock")

cfg = B.load_config(Path(B.__file__).with_name("config.yaml"))
pins = Path(tempfile.mkdtemp()) / "pins.yaml"       # never touch the real pins.yaml
cfg["pins_file"] = str(pins)
colors_file = pins.with_name("colors.yaml")          # never touch the real colors.yaml
cfg["colors_file"] = str(colors_file)
cfg["sequencer"] = {"tracks": 4}
cfg["chases_file"] = str(pins.with_name("chases.yaml"))
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


def tap(name):
    fire("on_button_pressed", name); fire("on_button_released", name)


def since(n0, *items):
    return all(("btn", it) in calls[n0:] for it in items)


def last(name):
    """Last colour sent to a button."""
    return next((a[1] for n, a in reversed(calls) if n == "btn" and a[0] == name), None)


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

time.sleep(0.3)
assert ("btn", ("Upper Row 1", "white")) in calls and ("btn", ("Upper Row 6", "dark_gray")) in calls \
    and ("btn", ("Upper Row 4", "black")) in calls, "CLIP view menus: 1-3, 6, 7 lit, CLIP PARAMS white"
before = rest.composition()
tap("Mix")                                        # click → MIX stays open
fire("on_encoder_rotated", "Track1 Encoder", -5)  # top layer (3) master
fire("on_encoder_rotated", "Master Encoder", -5)  # composition master
time.sleep(0.3)                                   # let a frame light B_3
assert ("btn", ("Mix", "white")) in calls, "B_3 not lit in mix mode"
n0 = len(calls)
tap("Mix")                                        # click again → previous screen
time.sleep(1.0)
after = rest.composition()
assert after["layers"][2]["master"]["value"] < before["layers"][2]["master"]["value"], "layer 3 master unchanged"
assert after["layers"][0]["master"]["value"] == before["layers"][0]["master"]["value"], "wrong layer changed"
assert after["master"]["value"] < before["master"]["value"], "composition master unchanged"
assert since(n0, ("Mix", "dark_gray"), ("Upper Row 1", "white")), "second click must go back to CLIP PARAMS"
n0 = len(calls)
fire("on_button_pressed", "Mix"); time.sleep(0.6); fire("on_button_released", "Mix")   # hold → back on release
time.sleep(0.2)
assert since(n0, ("Mix", "white")) and last("Mix") == "dark_gray", "hold: MIX while held, back on release"


# --- move a param in CLIP PARAMS: L1 C1 = Frequency, Fade, Width, Height, Offset, Position X, Scale, Speed
fire("on_pad_pressed", 60, (7, 0), 100); fire("on_pad_released", 60, (7, 0), 0)
time.sleep(0.2)
fire("on_button_pressed", "Convert")
fire("on_encoder_touched", "Track1 Encoder")
fire("on_encoder_released", "Track1 Encoder")
fire("on_button_released", "Convert")
time.sleep(0.3)
assert ("btn", ("Convert", "white")) in calls, "Convert not lit while moving"
assert ("btn", ("Lower Row 1", "white")) in calls and ("btn", ("Lower Row 2", "black")) in calls, "one page"
fire("on_encoder_touched", "Track8 Encoder")      # target: K8 (Speed)
fire("on_encoder_released", "Track8 Encoder")
order = yaml.safe_load(pins.read_text())["order"]
assert order[0] == "transport/controls/speed" and order[7] == "video/sourceparams/frequency", order

# order carries over: L2 C2 (Comets down) clip params natural = Position X, Scale, Speed → K1 = Speed
fire("on_pad_pressed", 60, (6, 1), 100); fire("on_pad_released", 60, (6, 1), 0)
before = rest.composition()
fire("on_encoder_rotated", "Track1 Encoder", 10)
time.sleep(0.5)
after = rest.composition()
spd = lambda c: c["layers"][1]["clips"][1]["transport"]["controls"]["speed"]["value"]
assert spd(after) > spd(before), "K1 on L2 C2 should now be Speed"

# --- LAYER PARAMS (button 6): K1 = layer Opacity; the clip's params are not there
fire("on_pad_pressed", 60, (7, 0), 100); fire("on_pad_released", 60, (7, 0), 0)     # L1 C1
tap("Upper Row 6")
op = lambda: rest.composition()["layers"][0]["video"]["opacity"]["value"]
op0 = op()
fire("on_encoder_rotated", "Track1 Encoder", -10)
time.sleep(0.4)
assert op() < op0, "LAYER PARAMS K1 must be the layer's Opacity"
time.sleep(0.2)
assert ("btn", ("Upper Row 6", "white")) in calls and ("btn", ("Upper Row 1", "dark_gray")) in calls
tap("Upper Row 1")

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
tap("Mix")                                         # MIX: K1 = layer 3, K2 = layer 2, K3 = layer 1
fire("on_button_pressed", "Lower Row 3")           # unmute layer 1
fire("on_button_pressed", "Solo"); fire("on_button_pressed", "Lower Row 2"); fire("on_button_released", "Solo")
time.sleep(0.4)
assert flag(1, "bypassed") is False and flag(2, "solo") is False, "MIX Lower Row mute / Solo+Lower Row solo"
assert last("Mix") == "white", "holding Solo over MIX must come back to MIX"
tap("Mix")
n0 = len(calls)
tap("Mute")                                        # click → MUTE screen stays
fire("on_button_pressed", "Lower Row 3"); fire("on_button_released", "Lower Row 3")   # mute layer 1
time.sleep(0.4)
assert flag(1, "bypassed") is True, "MUTE screen: button below must mute"
assert since(n0, ("Mute", "white")), "Mute lit while its screen is open"
tap("Solo")                                        # click → SOLO screen on top of MUTE
fire("on_button_pressed", "Lower Row 1"); fire("on_button_released", "Lower Row 1")   # solo layer 3
time.sleep(0.4)
assert flag(3, "solo") is True, "SOLO screen: button below must solo"
n0 = len(calls)
tap("Solo")                                        # back to MUTE
time.sleep(0.2)
fire("on_button_pressed", "Lower Row 3"); fire("on_button_released", "Lower Row 3")   # unmute layer 1
tap("Mute")                                        # back to CLIP PARAMS
time.sleep(0.4)
assert flag(1, "bypassed") is False, "click Solo again must return to MUTE"
assert since(n0, ("Mute", "white"), ("Upper Row 1", "white")), "back through MUTE to the menu"
fire("on_button_pressed", "Solo"); fire("on_pad_pressed", 60, (5, 0), 100)   # hold Solo + pad: un-solo layer 3
fire("on_pad_released", 60, (5, 0), 0); fire("on_button_released", "Solo")
time.sleep(0.4)
assert flag(3, "solo") is False

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

# --- F13 CLIP EFFECTS (button 3) on L1 C1 = [Transform]; LAYER EFFECTS (button 7) = [Hue Rotate, no bypass]
fire("on_pad_pressed", 60, (7, 0), 100); fire("on_pad_released", 60, (7, 0), 0)
fire("on_button_pressed", "Upper Row 3")
fire("on_button_pressed", "Lower Row 1")          # Transform off
time.sleep(0.4)
tr = rest.composition()["layers"][0]["clips"][0]["video"]["effects"][0]["bypassed"]["value"]
assert tr is True, "BD1 must bypass the clip's Transform"
assert ("btn", ("Lower Row 2", "black")) in calls, "no second clip effect"
n0 = len(calls)
fire("on_button_pressed", "Upper Row 7")
time.sleep(0.3)
assert since(n0, ("Lower Row 1", "black"), ("Upper Row 7", "white")), "Hue Rotate has no bypass: BD1 unlit"
fire("on_button_pressed", "Upper Row 1")

# --- empty pad: Arena sends empty slots with transport / video = null (crashed live updates once)
fire("on_pad_pressed", 60, (7, 2), 100); fire("on_pad_released", 60, (7, 2), 0)          # L1 C3 empty
time.sleep(1.0)

# --- F16 live updates: a change made elsewhere (e.g. mouse in Arena) reaches the pads without polling
L3 = rest.composition()["layers"][2]
if not POLL_ONLY:
    requests.put(f"http://127.0.0.1:8080/api/v1/parameter/by-id/{L3['bypassed']['id']}", json={"value": True})
    n0 = len(calls)
    time.sleep(0.6)
    assert any(n == "pad" and a[0][0] == 5 and a[1] in ("dark_gray", "light_gray") for n, a in calls[n0:]), \
        "external mute of layer 3 didn't reach the pads via WebSocket"
    requests.put(f"http://127.0.0.1:8080/api/v1/parameter/by-id/{L3['bypassed']['id']}", json={"value": False})

# --- F18 SEQ with the Bar Chaser effect: setup, multi-select, steps, run, LEDs
fire("on_pad_pressed", 60, (7, 0), 100); fire("on_pad_released", 60, (7, 0), 0)     # select L1 C1
fire("on_button_pressed", "Shift"); fire("on_button_pressed", "Note")               # Shift + Note = add the effect
fire("on_button_released", "Note"); fire("on_button_released", "Shift")
time.sleep(1.0)
fx1 = [e for e in rest.composition()["layers"][0]["video"]["effects"] if e["name"] == "Bar Chaser"]
assert len(fx1) == 1 and fx1[0]["params"]["Track"]["value"] == "1", "Shift + Note must add Bar Chaser to layer 1"
fire("on_pad_pressed", 60, (6, 1), 100); fire("on_pad_released", 60, (6, 1), 0)     # select L2 C2 in the grid
fire("on_button_pressed", "Note")                                                    # SEQ mode
time.sleep(0.3)
assert ("btn", ("Note", "white")) in calls and ("btn", ("Lower Row 1", "L0")) in calls, "SEQ LEDs"
assert ("btn", ("Upper Row 1", "L6")) in calls and ("btn", ("Upper Row 2", "L6_dim")) in calls, "SEQ menus red"
fire("on_button_pressed", "Browse"); fire("on_button_pressed", "Lower Row 2")       # Browse + BD2 = layer 2 → track 2
fire("on_button_released", "Lower Row 2"); fire("on_button_released", "Browse")
time.sleep(1.0)
fx2 = [e for e in rest.composition()["layers"][1]["video"]["effects"] if e["name"] == "Bar Chaser"]
assert len(fx2) == 1 and fx2[0]["params"]["Track"]["value"] == "2", "Browse + BD2 must make layer 2 track 2"
assert ("pad", ((7, 3), "black")) in calls, "unassigned pad 4 must be dark"
assert ("btn", ("Lower Row 2", "L1_dim")) in calls, "BD2 dim: track 2 has a Bar Chaser"

assert ("btn", ("Select", "dark_gray")) in calls and ("btn", ("Layout", "white")) in calls, "SEQ: Select dim, Layout lit"
n0 = len(calls)
tap("Scale"); tap("1/16")                                                           # Scale: side buttons = grid
time.sleep(0.2)
assert since(n0, ("Scale", "white"), ("Layout", "dark_gray"), ("1/16", "white"), ("1/32t", "dark_gray")), "grid LEDs"
n0 = len(calls)
tap("Layout")                                                                       # Layout: side buttons = groups
time.sleep(0.2)
assert since(n0, ("1/32t", "black"), ("1/16", "black")), "empty groups must be unlit"
fire("on_pad_pressed", 60, (7, 0), 100); fire("on_pad_released", 60, (7, 0), 0)     # pad 1 (select + audition)
n0 = len(calls)
tap("Select")                                                                       # multi-select on (latched)
time.sleep(0.2)
assert since(n0, ("Select", "white")), "Select lit while multi-select is on"
fire("on_pad_pressed", 60, (7, 1), 100); fire("on_pad_released", 60, (7, 1), 0)     # pad 2 added to the selection
fire("on_pad_pressed", 60, (0, 0), 127); fire("on_pad_released", 60, (0, 0), 0)     # step 1 on both pads
n0 = len(calls)
fire("on_button_pressed", "Select"); tap("1/32t"); fire("on_button_released", "Select")   # store group 1
time.sleep(0.2)
assert since(n0, ("1/32t", "L0")), "stored + current group lit fully in the track colour"
assert ("btn", ("Select", "dark_gray")) not in calls[n0:], "Select + group must not toggle multi-select"
n0 = len(calls)
tap("Select")                                                                       # multi-select off
fire("on_pad_pressed", 60, (7, 1), 100); fire("on_pad_released", 60, (7, 1), 0)     # pad 2 only
time.sleep(0.2)
assert since(n0, ("Select", "dark_gray"), ("1/32t", "L0_dim")), "selection changed: group 1 dim"
fire("on_button_pressed", "Select"); fire("on_button_pressed", "Shift"); tap("1/32")   # Select + Shift = GG2
fire("on_button_released", "Shift"); fire("on_button_released", "Select")
time.sleep(0.2)
assert last("1/32") == "white" and last("1/32t") == "L0_dim", "global group lit white, track group dim"
fire("on_pad_pressed", 60, (0, 2), 127); fire("on_pad_released", 60, (0, 2), 0)     # step 3 on pad 2
fire("on_pad_pressed", 60, (0, 2), 64); fire("on_pad_released", 60, (0, 2), 0)      # step 3 off again
fire("on_pad_pressed", 60, (0, 2), 64); fire("on_pad_released", 60, (0, 2), 0)      # step 3 on, half level
time.sleep(0.3)
chases = yaml.safe_load(open(cfg["chases_file"]).read())
steps = chases["patterns"][0]["tracks"][0]["steps"]
assert steps["pad 1"] == [[0, 1.0, None]] and steps["pad 2"][0] == [0, 1.0, None] and steps["pad 2"][1][1] < 0.6, steps
assert ("pad", ((0, 2), "L0_mid")) in calls or ("pad", ((0, 2), "L0_dim")) in calls, "half-level step pad"
assert chases["track_groups"] == {1: [[1, 2]] + [None] * 7}, chases["track_groups"]
assert chases["groups"] == [None, [2]] + [None] * 6, chases["groups"]
assert chases["pads"][1][:4] == ["Bar A", "Bar B", "Bar C", "\u2014"] and 2 in chases["pads"], chases["pads"]
tap("Upper Row 2")                                                                  # SETTINGS: K2 = Length
fire("on_encoder_rotated", "Track2 Encoder", 2); fire("on_encoder_rotated", "Track2 Encoder", -2)
time.sleep(0.2)
assert ("btn", ("Upper Row 2", "L6")) in calls, "SETTINGS lit red"
tap("Upper Row 1")                                                                  # ENVELOPE
n0 = len(calls)
tap("Mute")                                                                         # MUTE on top of SEQ
time.sleep(0.2)
assert last("Lower Row 1") == "L2" and last("Mute") == "white", "MUTE on SEQ: BD1 = top layer (3)"
assert not any(n == "pad" for n, _ in calls[n0:]), "MUTE on SEQ must not change the pads"
tap("Mute")
time.sleep(0.2)
assert ("btn", ("Lower Row 1", "L0")) in calls[n0:], "back in SEQ: BD1 = track 1"
log0 = len(requests.get("http://127.0.0.1:8080/api/v1/_opacity_log").json())
fire("on_button_pressed", "Play")                                                    # run
time.sleep(2.6)
fire("on_button_pressed", "Play")                                                    # stop
time.sleep(0.5)
log = requests.get("http://127.0.0.1:8080/api/v1/_opacity_log").json()[log0:]
fx1 = [e for e in rest.composition()["layers"][0]["video"]["effects"] if e["name"] == "Bar Chaser"][0]
l1, l2 = fx1["params"]["Level 1"]["id"], fx1["params"]["Level 2"]["id"]
a_on = [t for t, pid, v in log if pid == l1 and v > 0.9]
b_on = [t for t, pid, v in log if pid == l2 and v > 0.4]
assert a_on and b_on, (a_on, b_on)
step = 60.0 / rest.composition()["tempocontroller"]["tempo"]["value"] / 4
assert any(1.5 * step < b - a < 2.5 * step for a in a_on for b in b_on), "pad 2 step 3 must follow pad 1 step 1 by 2 steps"
assert any(pid == l1 and v == 0.0 for _, pid, v in log), "release must reach 0"
fx2 = [e for e in rest.composition()["layers"][1]["video"]["effects"] if e["name"] == "Bar Chaser"][0]
assert not any(pid == fx2["params"]["Level 1"]["id"] for _, pid, _ in log), "track 2's layer must stay dark"
assert ("btn", ("Play", "green")) in calls
fire("on_pad_pressed", 60, (4, 1), 100); fire("on_pad_released", 60, (4, 1), 0)     # row 5 = pattern 2 (queued)
time.sleep(0.2)
assert ("pad", ((4, 1), "white")) in calls, "queued pattern blinks white"
n0 = len(calls)
tap("1/32t")                                                                        # recall group 1
fire("on_button_pressed", "Delete"); tap("1/32"); fire("on_button_released", "Delete")   # clear empty group 2: no-op
time.sleep(0.2)
assert since(n0, ("1/32t", "L0")), "recalled group lit fully"
assert ("pad", ((7, 0), "light_gray")) in calls[n0:], "recall must select pad 1 again (group 1 = pads 1 + 2)"
n1 = len(calls)
fire("on_button_pressed", "Session"); time.sleep(0.3)
assert ("btn", ("Note", "dark_gray")) in calls[n1:], "Session leaves SEQ"

if not POLL_ONLY:
    assert "live updates off" not in "".join(sys.stdout.text), "WebSocket thread crashed during the test"

counts = {}
for name, _ in calls:
    counts[name] = counts.get(name, 0) + 1
print("calls:", counts)
print("last pad colours:", [a for n, a in calls if n == "pad"][-6:])
assert counts.get("palette", 0) >= 16 and counts.get("frame", 0) > 10, "loop did not run as expected"
print("OK")
