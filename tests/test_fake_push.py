"""End-to-end test without hardware: fake Push 2 object + mock Resolume.

    python tests/mock_resolume.py &      # terminal 1 (or background)
    python tests/test_fake_push.py       # terminal 2

Replaces push2_python.Push2 with a recorder, runs the real run() loop in a thread,
fires handlers through push2-python's action registry, and prints what was sent.
Check the mock's output for the matching POST/PUT requests.
"""
import sys
import threading
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import push2_python
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
rest = B.Resolume("127.0.0.1", 8080)
threading.Thread(target=B.run, args=(cfg, rest), daemon=True).start()
time.sleep(1.0)


def fire(action, *args):
    REG[action][0](None, *args)   # push2-python only calls the first handler per action


fire("on_pad_pressed", 60, (7, 3), 100)          # bottom row = layer 1, column 4
fire("on_pad_released", 60, (7, 3), 0)
fire("on_button_pressed", "Shift")
fire("on_encoder_rotated", "Track1 Encoder", -1)  # fine step
fire("on_button_released", "Shift")
fire("on_encoder_rotated", "Master Encoder", 5)   # selected layer opacity
fire("on_encoder_touched", "Track2 Encoder")
time.sleep(1.0)

counts = {}
for name, _ in calls:
    counts[name] = counts.get(name, 0) + 1
print("calls:", counts)
print("last pad colours:", [a for n, a in calls if n == "pad"][-6:])
assert counts.get("palette") == 16 and counts.get("frame", 0) > 10, "loop did not run as expected"
print("OK")
