"""Render the Push display to PNG files (no hardware needed). Needs the mock running.

    python tests/render_preview.py   → writes preview_*.png next to this file
"""
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import push_resolume_bridge as B  # noqa: E402

cfg = B.load_config(HERE.parent / "config.yaml")
br = B.Bridge(cfg, B.Resolume("127.0.0.1", 8080))
br.sender.start()
threading.Thread(target=br.poll_loop, daemon=True).start()
time.sleep(0.6)

br.pad_pressed((7, 0)); br.pad_released((7, 0))   # select layer 1, clip 1
br.touched = 1
time.sleep(0.3)
_, surf = B.render(br.snapshot(), bgr=False)          # bgr=False → correct colours in PNG
surf.write_to_png(str(HERE / "preview_auto.png"))

br.button("Upper Row 3", True)                         # mix mode
br.touched = None
_, surf = B.render(br.snapshot(), bgr=False)
surf.write_to_png(str(HERE / "preview_mix.png"))
br.button("Upper Row 3", True)

br.online = False
_, surf = B.render(br.snapshot(), bgr=False)
surf.write_to_png(str(HERE / "preview_offline.png"))
print("wrote", HERE / "preview_auto.png", "preview_mix.png and preview_offline.png")
