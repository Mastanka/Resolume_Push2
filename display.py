"""Push 2 display (960 x 160): draws a Bridge.snapshot() with cairo."""

from __future__ import annotations

import colorsys

# One colour per layer (repeats every 8 layers). Used for pads and the display.
LAYER_RGB = [
    (0, 190, 255),   # cyan
    (255, 0, 150),   # magenta
    (255, 100, 0),   # orange
    (0, 255, 90),    # green
    (150, 60, 255),  # purple
    (255, 210, 0),   # yellow
    (255, 25, 25),   # red
    (40, 90, 255),   # blue
]


# --------------------------------------------------------------------------- #
# Display (960 x 160)
# --------------------------------------------------------------------------- #

def render(snap, bgr=True):
    """Draw the display. Push 2 wants BGR565; cairo draws RGB565, so with bgr=True
    red and blue are swapped at draw time and the frame needs no conversion."""
    import cairo
    import numpy as np

    W, H = 960, 160
    surf = cairo.ImageSurface(cairo.FORMAT_RGB16_565, W, H)
    ctx = cairo.Context(surf)

    def col(rgb):
        r, g, b = (c / 255 for c in rgb)
        ctx.set_source_rgb(b, g, r) if bgr else ctx.set_source_rgb(r, g, b)

    def font(size, bold=False):
        ctx.select_font_face("Sans", cairo.FONT_SLANT_NORMAL,
                             cairo.FONT_WEIGHT_BOLD if bold else cairo.FONT_WEIGHT_NORMAL)
        ctx.set_font_size(size)

    def fit(s, maxw):
        if ctx.text_extents(s).x_advance <= maxw:
            return s
        while s and ctx.text_extents(s + "…").x_advance > maxw:
            s = s[:-1]
        return s + "…"

    def say(x, y, s, maxw=None, right=False):
        s = fit(s, maxw) if maxw else s
        if right:
            x -= ctx.text_extents(s).x_advance
        ctx.move_to(x, y)
        ctx.show_text(s)

    col((0, 0, 0))
    ctx.paint()

    if not snap["online"]:
        col((255, 255, 255)); font(22, True)
        say(24, 70, f"Waiting for Resolume at {snap['url']}")
        col((150, 150, 150)); font(16)
        say(24, 104, "Arena → Preferences → Webserver → Enable Webserver & REST API")
    elif snap["mode"] == "color":
        c = snap["color"]
        for k in range(8):
            x = k * 120
            if snap["touched"] == k:
                col((45, 45, 45)); ctx.rectangle(x, 0, 120, 98); ctx.fill()
            if k:
                col((35, 35, 35)); ctx.rectangle(x, 6, 1, 86); ctx.fill()
        if c is None:
            col((200, 200, 200)); font(18, True)
            say(24, 56, "No colour parameter on this clip")
        else:
            r, g, b = c["rgb"]
            bars = [(255, 40, 40), (40, 220, 40), (60, 90, 255)]
            for k, (label, v) in enumerate(c["knobs"]):
                x = k * 120
                col((150, 150, 150)); font(15)
                say(x + 8, 24, label, 104)
                col((255, 255, 255)); font(22, True)
                if k < 3:
                    say(x + 8, 58, f"{v:.0f}"); frac, bar = v / 255, bars[k]
                elif k == 3:
                    say(x + 8, 58, f"{v:.0f}°"); frac = v / 360
                    bar = [round(q * 255) for q in colorsys.hsv_to_rgb(v / 360, 1, 1)]
                else:
                    say(x + 8, 58, f"{v:.0f}%"); frac, bar = v / 100, (r, g, b)
                col((45, 45, 45)); ctx.rectangle(x + 8, 72, 104, 8); ctx.fill()
                col(bar); ctx.rectangle(x + 8, 72, 104 * frac, 8); ctx.fill()
            x = 7 * 120
            col((150, 150, 150)); font(15)
            say(x + 8, 24, f"Param {c['idx'] + 1}/{c['n']}", 104)
            col((255, 255, 255)); font(17, True)
            say(x + 8, 58, c["label"], 104)

        col((60, 60, 60)); ctx.rectangle(0, 100, W, 1); ctx.fill()
        if c is not None:
            col(tuple(c["rgb"])); ctx.rectangle(10, 108, 90, 44); ctx.fill()
            col((80, 80, 80)); ctx.set_line_width(1); ctx.rectangle(10.5, 108.5, 89, 43); ctx.stroke()
        col((255, 255, 255)); font(18, True)
        say(114, 128, "COLOR" + (f"   {c['label']}   {c['hex']}" if c else ""), 520)
        col((200, 200, 200)); font(16)
        say(114, 151, f"L{snap['L']} C{snap['C']}   {snap['clip_name'] or '—'}", 520)
        col((140, 140, 140)); font(13)
        say(950, 127, snap["bpm"], right=True)
        say(950, 150, "FINE" if snap["shift"] else "PALETTE BELOW", right=True)
    elif snap["mode"] == "mix":
        for k in range(8):
            x = k * 120
            if snap["touched"] == k:
                col((45, 45, 45)); ctx.rectangle(x, 0, 120, 98); ctx.fill()
            if k:
                col((35, 35, 35)); ctx.rectangle(x, 6, 1, 86); ctx.fill()
            if k >= len(snap["mix"]):
                continue
            L, name, value, frac, missing, muted, solo = snap["mix"][k]
            accent = (90, 90, 90) if muted else LAYER_RGB[(L - 1) % 8]
            col(accent); ctx.rectangle(x + 8, 6, 104, 3); ctx.fill()
            col((255, 90, 90) if missing else (170, 170, 170)); font(15)
            say(x + 8, 28, name, 104)
            col((255, 255, 255)); font(22, True)
            say(x + 8, 60, value, 104)
            col((45, 45, 45)); ctx.rectangle(x + 8, 72, 104, 8); ctx.fill()
            col(accent); ctx.rectangle(x + 8, 72, 104 * frac, 8); ctx.fill()
            col((110, 110, 110)); font(12)
            say(x + 8, 93, f"L{L}")
            if muted or solo:
                col((255, 60, 60) if muted else (255, 210, 0)); font(12, True)
                say(x + 112, 93, "MUTED" if muted else "SOLO", right=True)

        col((60, 60, 60)); ctx.rectangle(0, 100, W, 1); ctx.fill()
        col((255, 255, 255)); font(18, True)
        say(24, 137, "MIX")
        if snap["comp_master"]:
            value, frac = snap["comp_master"]
            col((150, 150, 150)); font(14)
            say(300, 124, "COMPOSITION MASTER")
            col((255, 255, 255)); font(20, True)
            say(660, 126, value, right=True)
            col((45, 45, 45)); ctx.rectangle(300, 134, 360, 12); ctx.fill()
            col((255, 255, 255)); ctx.rectangle(300, 134, 360 * frac, 12); ctx.fill()
        col((140, 140, 140)); font(13)
        say(950, 127, snap["bpm"], right=True)
        say(950, 150, "FINE" if snap["shift"] else f"LAYERS {snap['layers'][0]}–{snap['layers'][1]}",
            right=True)
    else:
        accent = LAYER_RGB[(snap["L"] - 1) % 8]
        for k in range(8):
            x = k * 120
            if snap["touched"] == k:
                col((45, 45, 45)); ctx.rectangle(x, 0, 120, 98); ctx.fill()
            if k:
                col((35, 35, 35)); ctx.rectangle(x, 6, 1, 86); ctx.fill()
            if k >= len(snap["rows"]):
                continue
            label, value, frac, missing = snap["rows"][k]
            mv = snap["move"]
            if mv and mv["here"] and mv["col"] == k:
                col((255, 210, 0)); ctx.set_line_width(2); ctx.rectangle(x + 2, 2, 116, 94); ctx.stroke()
            col((255, 90, 90) if missing else (150, 150, 150)); font(15)
            say(x + 8, 24, label, 104)
            col((255, 255, 255)); font(22, True)
            say(x + 8, 58, value, 104)
            col((45, 45, 45)); ctx.rectangle(x + 8, 72, 104, 8); ctx.fill()
            col(accent); ctx.rectangle(x + 8, 72, 104 * frac, 8); ctx.fill()

        col((60, 60, 60)); ctx.rectangle(0, 100, W, 1); ctx.fill()
        mv = snap["move"]
        if mv:
            col((255, 210, 0)); ctx.rectangle(10, 110, 8, 42); ctx.fill()
            font(20, True)
            say(28, 130, "SELECT NEW POSITION")
            col((200, 200, 200)); font(15)
            say(28, 151, f"Moving {mv['label']}  (page {mv['page'] + 1}, knob {mv['col'] + 1})"
                         "   ·   touch it again or Convert = cancel", 560)
        else:
            col(accent); ctx.rectangle(10, 110, 8, 42); ctx.fill()
            col((255, 255, 255)); font(18, True)
            say(28, 128, f"L{snap['L']}   {snap['layer_name']}", 560)
            col((200, 200, 200)); font(16)
            say(28, 151, f"C{snap['C']}   {snap['clip_name'] or '—'}", 560)

        col((140, 140, 140)); font(13)
        top = "   ·   ".join(t for t in (f"PAGE {snap['page'] + 1}/{snap['pages']}", snap["bpm"],
                                          "FINE" if snap["shift"] else "") if t)
        say(950, 127, top, right=True)
        bottom = snap["touched_path"] or (f"LAYERS {snap['layers'][0]}–{snap['layers'][1]}   ·   "
                                          f"COLS {snap['cols'][0]}–{snap['cols'][1]}")
        say(950, 150, bottom, 360, right=True)

    if snap["online"] and snap.get("blackout"):
        col((150, 0, 0)); ctx.rectangle(0, 101, W, 59); ctx.fill()
        col((255, 255, 255)); font(26, True)
        say(24, 140, "BLACKOUT")
        col((255, 200, 200)); font(15)
        say(950, 138, "press Stop Clip to restore", right=True)

    surf.flush()
    stride = surf.get_stride() // 2
    frame = np.ndarray(shape=(H, stride), dtype=np.uint16, buffer=surf.get_data())[:, :W]
    return frame.transpose(), surf
