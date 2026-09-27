"""Render the Push display to PNG files (no hardware needed). Needs the mock running.

    python tests/render_preview.py   → writes preview_*.png next to this file
"""
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import push_resolume_bridge as B  # noqa: E402

cfg = B.load_config(HERE.parent / "config.yaml")
cfg["pins_file"] = str(Path(tempfile.mkdtemp()) / "pins.yaml")
cfg["colors_file"] = str(Path(tempfile.mkdtemp()) / "colors.yaml")
cfg["chases_file"] = str(Path(tempfile.mkdtemp()) / "chases.yaml")
br = B.Bridge(cfg, B.Resolume("127.0.0.1", 8080))
br.sender.start()
threading.Thread(target=br.poll_loop, daemon=True).start()
time.sleep(0.6)



def shot(name):
    _, surf = B.render(br.snapshot(), bgr=False)
    surf.write_to_png(str(HERE / f"preview_{name}.png"))


br.pad_pressed((7, 0)); br.pad_released((7, 0))   # select layer 1, clip 1
br.touched = 1
time.sleep(0.3)
_, surf = B.render(br.snapshot(), bgr=False)          # bgr=False → correct colours in PNG
surf.write_to_png(str(HERE / "preview_auto.png"))

br.touched = None
br.button("Upper Row 2", True)                  # COLOR menu
time.sleep(0.1)
_, surf = B.render(br.snapshot(), bgr=False)
surf.write_to_png(str(HERE / "preview_color.png"))
br.button("Duplicate", True)
shot("paste")
br.button("Duplicate", False)
br.button("Master", True)
time.sleep(0.1)
shot("master_color")
br.button("Master", True)
br.button("Upper Row 1", True)

br.button("Upper Row 3", True)                  # FX menu
shot("fx")
br.button("Upper Row 1", True)

br.button("Convert", True); br.touch(1); br.untouch(1); br.button("Convert", False)
br.button("Lower Row 2", True)                  # move mode, looking at page 2
_, surf = B.render(br.snapshot(), bgr=False)
surf.write_to_png(str(HERE / "preview_move.png"))
br.button("Convert", True); br.button("Convert", False); br.page = 0

br.button("Mix", True)                         # mix mode
br.touched = None
_, surf = B.render(br.snapshot(), bgr=False)
surf.write_to_png(str(HERE / "preview_mix.png"))
br.toggle_layer(2, "bypassed"); br.toggle_layer(3, "solo")
shot("mix_mute_solo")
br.toggle_layer(2, "bypassed"); br.toggle_layer(3, "solo")
br.button("Mix", True)

br.toggle_blackout()
shot("blackout")
br.toggle_blackout()

from sequencer import Envelope  # noqa: E402
br.engine.add_to_layer(1)                       # a Bar Chaser on layer 1 = track 1
br.mode = "seq"
br.sel_pads = {1}
br.seq.toggle_step("pad 2", 0); br.seq.toggle_step("pad 2", 8)
br.seq.pattern.tracks[0].envelope = Envelope(attack=0.2, decay=0.3, sustain=0.6, release=0.5)
br.sel_pads = {0, 1}; br.seq.store_group(2, br.sel_pads); br.cur_group = 2; br.multi = True   # G3, multi-select
br.seq.start(br.beat_time() or 0.0)
br.seq.tick(br.beat_time() or 0.0)
shot("seq")
br.seq.stop(); br.mode = "params"

br.online = False
_, surf = B.render(br.snapshot(), bgr=False)
surf.write_to_png(str(HERE / "preview_offline.png"))
print("wrote", HERE / "preview_auto.png", "and the other preview_*.png")
