"""Render the 5 presentation images (1080x1350) into docs/promo/. Needs `pip install segno` (QR code).

    python docs/promo/make_promo.py                 # composition from the running Arena
    python docs/promo/make_promo.py comp.json       # or from a saved GET /composition

The display strips are drawn by the bridge's own render(). The composition is padded out
(clips copied into more columns, a few set to playing) so the pad grid looks like a real deck.
"""
import copy
import json
import math
import sys
import tempfile
from pathlib import Path

import cairo
import requests

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))
import push_resolume_bridge as B  # noqa: E402

W, H = 1080, 1350
FONT = "Helvetica Neue"
MONO = "Menlo"
REPO = "https://github.com/Mastanka/Resolume_Push2"
TOTAL = 5

# --------------------------------------------------------------------------- #
# Push 2 geometry, in the coordinates of PUSH2_LAYOUT.png (1393 x 1123)
# --------------------------------------------------------------------------- #
PUSH_W, PUSH_H = 1393, 1123
COLS = [255 + 107 * j for j in range(8)]          # pad / BU / BD column x
ENC_X = {"K10": 63, "K9": 174, "K11": 1299, **{f"K{j + 1}": COLS[j] + 50 for j in range(8)}}
CTRL = {
    "Tap Tempo": (24, 107, 118, 147), "Metronome": (119, 107, 214, 147),
    "Setup": (1230, 107, 1299, 147), "User": (1300, 107, 1369, 147),
    "Delete": (24, 190, 93, 258), "Undo": (24, 259, 93, 328),
    "Add Device": (1138, 190, 1207, 258), "Add Track": (1138, 259, 1207, 328),
    "Device": (1230, 190, 1299, 258), "Mix": (1300, 190, 1369, 258),
    "Browse": (1230, 259, 1299, 328), "Clip": (1300, 259, 1369, 328),
    "Mute": (24, 376, 87, 414), "Solo": (88, 376, 150, 414), "Stop Clip": (151, 376, 214, 414),
    "Master": (1138, 376, 1207, 414),
    "Convert": (24, 445, 93, 512), "Double Loop": (24, 513, 93, 579), "Quantize": (24, 580, 93, 646),
    "Duplicate": (24, 658, 93, 727), "New": (24, 728, 93, 797),
    "Fixed Length": (24, 810, 93, 872), "Automate": (24, 873, 93, 936),
    "Record": (24, 937, 93, 999), "Play": (24, 1012, 93, 1081),
    "Repeat": (1230, 688, 1299, 757), "Accent": (1300, 688, 1369, 757),
    "Scale": (1230, 769, 1299, 808), "Layout": (1300, 769, 1369, 808),
    "Note": (1230, 809, 1299, 878), "Session": (1300, 809, 1369, 878),
    "Shift": (1230, 1041, 1299, 1081), "Select": (1300, 1041, 1369, 1081),
}
for j in range(8):
    CTRL[f"Upper Row {j + 1}"] = (COLS[j], 107, COLS[j] + 96, 147)
    CTRL[f"Lower Row {j + 1}"] = (COLS[j], 376, COLS[j] + 96, 414)
SCENES = ["1/32t", "1/32", "1/16t", "1/16", "1/8t", "1/8", "1/4t", "1/4"]
for i, n in enumerate(SCENES):
    CTRL[n] = (1138, 445 + 81 * i, 1207, 515 + 81 * i)
DISPLAY = (248, 183, 1103, 335)
STRIP = (134, 445, 214, 1081)
NAV = (1230, 376, 1369, 515)
OCT = (1230, 890, 1369, 1029)

NICE = {"Record": "REC", "Play": "PLAY", "Convert": "CONVERT", "Tap Tempo": "TAP TEMPO", "Mix": "MIX",
        "Upper Row 1": "PARAMS", "Upper Row 2": "COLOR"}


def rgb(c):
    return tuple(v / 255 for v in c)


def rrect(ctx, x0, y0, x1, y1, r):
    r = min(r, (x1 - x0) / 2, (y1 - y0) / 2)
    ctx.new_sub_path()
    ctx.arc(x1 - r, y0 + r, r, -math.pi / 2, 0)
    ctx.arc(x1 - r, y1 - r, r, 0, math.pi / 2)
    ctx.arc(x0 + r, y1 - r, r, math.pi / 2, math.pi)
    ctx.arc(x0 + r, y0 + r, r, math.pi, 3 * math.pi / 2)
    ctx.close_path()


def font(ctx, size, bold=False):
    ctx.select_font_face(FONT, cairo.FONT_SLANT_NORMAL,
                         cairo.FONT_WEIGHT_BOLD if bold else cairo.FONT_WEIGHT_NORMAL)
    ctx.set_font_size(size)


def text(ctx, x, y, s, color, size, bold=False, align="left", spacing=0.0):
    font(ctx, size, bold)
    w = ctx.text_extents(s).x_advance + spacing * max(0, len(s) - 1)
    x = x - w if align == "right" else x - w / 2 if align == "center" else x
    ctx.set_source_rgb(*rgb(color))
    if not spacing:
        ctx.move_to(x, y); ctx.show_text(s)
        return w
    for ch in s:
        ctx.move_to(x, y); ctx.show_text(ch)
        x += ctx.text_extents(ch).x_advance + spacing
    return w


def glow(ctx, draw_path, color, width=2.5):
    for k, a in ((14, 0.06), (9, 0.10), (5, 0.18)):
        draw_path(); ctx.set_source_rgba(*rgb(color), a); ctx.set_line_width(width + k); ctx.stroke()
    draw_path(); ctx.set_source_rgb(*rgb(color)); ctx.set_line_width(width); ctx.stroke()


def draw_push(ctx, ox, oy, s, hl, pads, display_surf, badges, labels=None):
    """hl: control name -> colour (buttons, 'K1'…, 'pads', 'display')."""
    ctx.save()
    ctx.translate(ox, oy); ctx.scale(s, s)

    rrect(ctx, 0, 0, PUSH_W, PUSH_H, 26)
    ctx.set_source_rgb(*rgb((22, 22, 26))); ctx.fill_preserve()
    ctx.set_source_rgb(*rgb((52, 52, 58))); ctx.set_line_width(3); ctx.stroke()

    def button(name, box, lit=None, label=None):
        x0, y0, x1, y1 = box
        rrect(ctx, x0, y0, x1, y1, 7)
        if lit:
            ctx.set_source_rgba(*rgb(lit), 0.28); ctx.fill()
            glow(ctx, lambda: rrect(ctx, x0, y0, x1, y1, 7), lit, 3)
            if label:
                size = 19
                font(ctx, size, True)
                while size > 9 and ctx.text_extents(label).x_advance > x1 - x0 - 10:
                    size -= 1
                    font(ctx, size, True)
                text(ctx, (x0 + x1) / 2, (y0 + y1) / 2 + size / 2.6, label, (255, 255, 255), size, True, "center")
        else:
            ctx.set_source_rgb(*rgb((36, 36, 41))); ctx.fill_preserve()
            ctx.set_source_rgb(*rgb((58, 58, 64))); ctx.set_line_width(1.5); ctx.stroke()
            if label:
                text(ctx, x0 + 8, y0 + 20, label, (105, 105, 112), 13)

    for name, box in CTRL.items():
        lit = hl.get(name)
        if lit:
            label = (labels or {}).get(name, NICE.get(name))
        else:
            label = None if name.startswith(("Upper", "Lower")) else name
        button(name, box, lit, label)

    # encoders
    for name, x in ENC_X.items():
        lit = hl.get(name)
        ctx.arc(x, 55, 27, 0, 2 * math.pi)
        ctx.set_source_rgb(*rgb((44, 44, 50))); ctx.fill()
        ctx.arc(x, 55, 19, 0, 2 * math.pi)
        ctx.set_source_rgb(*rgb((28, 28, 32))); ctx.fill()
        ctx.move_to(x, 55 - 19); ctx.line_to(x, 55 - 8)
        ctx.set_source_rgb(*rgb((150, 150, 160))); ctx.set_line_width(3); ctx.stroke()
        if lit:
            glow(ctx, lambda x=x: (ctx.new_path(), ctx.arc(x, 55, 29, 0, 2 * math.pi)), lit, 4)

    # Ableton logo
    ctx.set_source_rgb(*rgb((140, 140, 148)))
    for k in range(4):
        ctx.rectangle(1140 + k * 9, 40, 4, 32); ctx.fill()
    for k in range(4):
        ctx.rectangle(1178, 41 + k * 9, 28, 4); ctx.fill()

    # display
    x0, y0, x1, y1 = DISPLAY
    rrect(ctx, x0, y0, x1, y1, 8); ctx.set_source_rgb(0, 0, 0); ctx.fill()
    if display_surf is not None:
        sc = (x1 - x0 - 16) / 960
        ctx.save()
        ctx.translate(x0 + 8, (y0 + y1) / 2 - 80 * sc); ctx.scale(sc, sc)
        ctx.set_source_surface(display_surf, 0, 0); ctx.paint()
        ctx.restore()
    if hl.get("display"):
        glow(ctx, lambda: rrect(ctx, x0, y0, x1, y1, 8), hl["display"], 3)
    else:
        rrect(ctx, x0, y0, x1, y1, 8); ctx.set_source_rgb(*rgb((58, 58, 64))); ctx.set_line_width(2); ctx.stroke()

    # touch strip + arrow crosses
    rrect(ctx, *STRIP, 8); ctx.set_source_rgb(*rgb((30, 30, 34))); ctx.fill_preserve()
    ctx.set_source_rgb(*rgb((58, 58, 64))); ctx.set_line_width(1.5); ctx.stroke()
    for (a, b_, c, d) in (NAV, OCT):
        rrect(ctx, a, b_, c, d, 7); ctx.set_source_rgb(*rgb((36, 36, 41))); ctx.fill_preserve()
        ctx.set_source_rgb(*rgb((58, 58, 64))); ctx.stroke()
        ctx.move_to(a, b_); ctx.line_to(c, d); ctx.move_to(c, b_); ctx.line_to(a, d); ctx.stroke()

    # pads
    for i in range(8):
        for j in range(8):
            px, py = COLS[j], 445 + 81 * i
            rrect(ctx, px, py, px + 96, py + 70, 7)
            c = pads.get((i, j))
            if c:
                ctx.set_source_rgb(*rgb(c)); ctx.fill()
            else:
                ctx.set_source_rgb(*rgb((33, 33, 38))); ctx.fill_preserve()
                ctx.set_source_rgb(*rgb((52, 52, 58))); ctx.set_line_width(1.5); ctx.stroke()
    if hl.get("pads"):
        glow(ctx, lambda: rrect(ctx, COLS[0] - 10, 435, COLS[7] + 106, 445 + 81 * 7 + 80, 12), hl["pads"], 3)

    ctx.restore()

    # numbered badges (drawn unscaled so they stay readable)
    for (bx, by, n, color) in badges:
        cx, cy = ox + bx * s, oy + by * s
        ctx.arc(cx, cy, 19, 0, 2 * math.pi); ctx.set_source_rgb(*rgb(color)); ctx.fill()
        ctx.arc(cx, cy, 19, 0, 2 * math.pi); ctx.set_source_rgb(*rgb((13, 13, 16))); ctx.set_line_width(3); ctx.stroke()
        text(ctx, cx, cy + 8, str(n), (13, 13, 16), 22, True, "center")


# --------------------------------------------------------------------------- #
# Page
# --------------------------------------------------------------------------- #

def page(path, no, title, subtitle, accent, hl, pads, disp_small, disp_big, badges, bullets, labels=None):
    surf, ctx = header(no, title, subtitle, accent)
    _page_body(surf, ctx, path, accent, hl, pads, disp_small, disp_big, badges, bullets, labels)


def header(no, title, subtitle, accent):
    surf = cairo.ImageSurface(cairo.FORMAT_RGB24, W, H)
    ctx = cairo.Context(surf)
    g = cairo.LinearGradient(0, 0, 0, H)
    g.add_color_stop_rgb(0, *rgb((14, 14, 18))); g.add_color_stop_rgb(1, *rgb((6, 6, 8)))
    ctx.set_source(g); ctx.paint()
    # accent glow top-left
    rg = cairo.RadialGradient(120, 60, 10, 120, 60, 700)
    rg.add_color_stop_rgba(0, *rgb(accent), 0.16); rg.add_color_stop_rgba(1, *rgb(accent), 0)
    ctx.set_source(rg); ctx.paint()

    w = text(ctx, 60, 70, "PUSH 2", (150, 150, 160), 20, True, spacing=2.5)
    ax = 60 + w + 16
    ctx.set_source_rgb(*rgb(accent)); ctx.set_line_width(3)
    ctx.move_to(ax, 63); ctx.line_to(ax + 30, 63); ctx.stroke()
    ctx.move_to(ax + 22, 55); ctx.line_to(ax + 31, 63); ctx.line_to(ax + 22, 71); ctx.stroke()
    text(ctx, ax + 48, 70, "RESOLUME ARENA", (150, 150, 160), 20, True, spacing=2.5)
    text(ctx, W - 60, 70, f"{no:02d} / {TOTAL:02d}", (110, 110, 120), 20, True, "right", spacing=2)
    text(ctx, 58, 138, title, (255, 255, 255), 66, True)
    ctx.set_source_rgb(*rgb(accent)); ctx.rectangle(60, 156, 70, 5); ctx.fill()
    text(ctx, 146, 164, subtitle, (190, 190, 198), 26)
    return surf, ctx


def _page_body(surf, ctx, path, accent, hl, pads, disp_small, disp_big, badges, bullets, labels):
    s = 880 / PUSH_W
    draw_push(ctx, (W - 880) / 2, 196, s, hl, pads, disp_small, badges, labels)

    # enlarged display
    dy = 196 + PUSH_H * s + 22
    text(ctx, 40, dy + 6, "ON THE PUSH DISPLAY", (120, 120, 130), 15, True, spacing=2)
    x0, y0, dw = 40, dy + 18, W - 80
    dh = dw * 160 / 960
    rrect(ctx, x0 - 6, y0 - 6, x0 + dw + 6, y0 + dh + 6, 10)
    ctx.set_source_rgb(0, 0, 0); ctx.fill_preserve()
    ctx.set_source_rgb(*rgb((60, 60, 68))); ctx.set_line_width(2); ctx.stroke()
    ctx.save(); ctx.translate(x0, y0); ctx.scale(dw / 960, dw / 960)
    ctx.set_source_surface(disp_big, 0, 0); ctx.paint(); ctx.restore()

    # bullets
    y = y0 + dh + 58
    for n, line in bullets:
        ctx.arc(76, y - 9, 17, 0, 2 * math.pi); ctx.set_source_rgb(*rgb(accent)); ctx.fill()
        text(ctx, 76, y - 1, str(n), (13, 13, 16), 20, True, "center")
        x = 110
        for k, part in enumerate(line.split("|")):          # alternating bold / regular
            bold = k % 2 == 0
            x += text(ctx, x, y, part, (255, 255, 255) if bold else (185, 185, 195), 25, bold)
            x += 8 if bold else 0
        y += 50
    surf.write_to_png(str(path))
    print("wrote", path)


def arrow(ctx, x0, x1, y, color):
    ctx.set_source_rgb(*rgb(color)); ctx.set_line_width(3)
    ctx.move_to(x0 + 10, y); ctx.line_to(x1 - 10, y); ctx.stroke()
    for tip, d in ((x1 - 10, 1), (x0 + 10, -1)):                 # both ways
        ctx.move_to(tip - 10 * d, y - 8); ctx.line_to(tip, y); ctx.line_to(tip - 10 * d, y + 8); ctx.stroke()


def box(ctx, x0, y0, x1, y1, title, lines, color):
    rrect(ctx, x0, y0, x1, y1, 16)
    ctx.set_source_rgb(*rgb((24, 24, 30))); ctx.fill_preserve()
    ctx.set_source_rgb(*rgb(color)); ctx.set_line_width(2.5); ctx.stroke()
    text(ctx, (x0 + x1) / 2, y0 + 44, title, (255, 255, 255), 25, True, "center")
    for k, ln in enumerate(lines):
        text(ctx, (x0 + x1) / 2, y0 + 78 + 26 * k, ln, (170, 170, 180), 18, align="center")


def page_how(path):
    import segno
    OR = (255, 120, 20)
    surf, ctx = header(5, "HOW IT WORKS", "A small Python app between Push 2 and Resolume", OR)

    # diagram
    y0, y1 = 205, 385
    box(ctx, 40, y0, 300, y1, "PUSH 2", ["pads, knobs, buttons", "960×160 display", "no Live needed"], (0, 190, 255))
    box(ctx, 410, y0, 670, y1, "THE BRIDGE", ["Python, runs on your Mac", "one file + config.yaml", "draws the display"], OR)
    box(ctx, 780, y0, 1040, y1, "RESOLUME", ["Arena 7", "built-in REST API", "your deck, unchanged"], (0, 255, 90))
    arrow(ctx, 300, 410, 290, (200, 200, 210))
    arrow(ctx, 670, 780, 290, (200, 200, 210))
    text(ctx, 355, 272, "USB", (200, 200, 210), 17, True, "center")
    text(ctx, 355, 318, "MIDI + screen", (140, 140, 150), 14, align="center")
    text(ctx, 725, 272, "HTTP", (200, 200, 210), 17, True, "center")
    text(ctx, 725, 318, "port 8080", (140, 140, 150), 14, align="center")
    text(ctx, W / 2, 425, "Reads the composition 4× per second, sends clip triggers and parameter changes back.",
         (170, 170, 180), 19, align="center")
    text(ctx, W / 2, 452, "Your other MIDI controllers keep working in Resolume as before.",
         (170, 170, 180), 19, align="center")

    # commands
    def section(y, label):
        text(ctx, 40, y, label, (120, 120, 130), 16, True, spacing=2)

    section(512, "RUN IT")
    cy0, rows = 528, [
        ("python push_resolume_bridge.py", "start the bridge"),
        ("python push_resolume_bridge.py --sim", "browser simulator, no Push needed"),
        ("python push_resolume_bridge.py --dump 3", "list layer 3's parameters"),
        ("python push_resolume_bridge.py --dump 3 --clip 2", "same, for clip 2"),
        ("python push_resolume_bridge.py --config show.yaml", "use another config file"),
    ]
    rrect(ctx, 40, cy0, W - 40, cy0 + 40 + 44 * len(rows), 14)
    ctx.set_source_rgb(*rgb((18, 18, 22))); ctx.fill_preserve()
    ctx.set_source_rgb(*rgb((55, 55, 62))); ctx.set_line_width(1.5); ctx.stroke()
    for k, (cmd, note) in enumerate(rows):
        y = cy0 + 50 + 44 * k
        ctx.select_font_face(MONO, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL); ctx.set_font_size(19)
        ctx.set_source_rgb(*rgb(OR)); ctx.move_to(64, y); ctx.show_text("$")
        prog, _, args = cmd.partition(".py")
        ctx.set_source_rgb(*rgb((235, 235, 240))); ctx.move_to(88, y); ctx.show_text(prog + ".py")
        ctx.set_source_rgb(*rgb((255, 190, 120))); ctx.show_text(args)
        text(ctx, W - 64, y, note, (130, 130, 140), 17, align="right")

    # dependencies + QR
    y = 868
    section(y, "YOU NEED")
    deps = [("macOS", " (tested on Apple Silicon), Python 3.9+"),
            ("Resolume Arena 7", ", Webserver + REST API on"),
            ("Ableton Push 2", ", Ableton Live closed"),
            ("brew install", " libusb cairo"),
            ("pip install -r requirements.txt", ""),
            ("", "push2-python, pycairo, numpy, requests, PyYAML")]
    for k, (b, r) in enumerate(deps):
        yy = y + 50 + 48 * k
        if b or k < 5:
            ctx.arc(52, yy - 8, 5, 0, 2 * math.pi); ctx.set_source_rgb(*rgb(OR)); ctx.fill()
        x = 70
        if b:
            x += text(ctx, x, yy, b, (255, 255, 255), 22, True)
        text(ctx, x, yy, r, (175, 175, 185), 22 if b else 18)

    # QR
    qr = segno.make(REPO, error="m")
    matrix = [list(row) for row in qr.matrix]
    n = len(matrix)
    pad = 26                                                     # quiet zone for scanners
    size = 222
    qx, qy = W - 40 - pad - size, 900
    rrect(ctx, qx - pad, qy - pad, qx + size + pad, qy + size + pad, 16)
    ctx.set_source_rgb(1, 1, 1); ctx.fill()
    cell = size / n
    ctx.set_source_rgb(*rgb((10, 10, 14)))
    for r, row in enumerate(matrix):
        for c, v in enumerate(row):
            if v:
                ctx.rectangle(qx + c * cell, qy + r * cell, cell + 0.4, cell + 0.4)
    ctx.fill()
    text(ctx, qx + size / 2, qy + size + 66, "SOURCE ON GITHUB", (255, 255, 255), 17, True, "center", spacing=2)
    text(ctx, qx + size / 2, qy + size + 92, REPO.replace("https://", ""), (160, 160, 170), 15, align="center")

    surf.write_to_png(str(path))
    print("wrote", path)


# --------------------------------------------------------------------------- #
# Demo state from a real composition
# --------------------------------------------------------------------------- #

def demo_comp(comp):
    comp = copy.deepcopy(comp)
    extra = {3: ("CHASE", 6), 8: ("CLOUDS", 5)}               # empty / test layers get content
    for L, layer in enumerate(comp["layers"], 1):
        clips = layer["clips"]
        if L in extra:
            layer["name"]["value"] = extra[L][0]
            src = [c for c in comp["layers"][extra[L][1] - 1]["clips"] if B.clip_state(c) != "Empty"]
        else:
            src = [c for c in clips if B.clip_state(c) != "Empty"]
        n = min(len(clips), 3 + (L * 5) % 4)                    # 3–6 loaded clips per layer
        for C in range(n):
            if src and B.clip_state(clips[C]) == "Empty" or L in extra:
                clips[C] = copy.deepcopy(src[C % len(src)])
            clips[C]["connected"]["value"] = "Disconnected"
    for L, C in ((1, 2), (2, 1), (4, 1), (6, 3), (7, 1), (8, 2)):
        comp["layers"][L - 1]["clips"][C - 1]["connected"]["value"] = "Connected"
    return comp


def pad_rgb(name):
    if name.startswith("L"):
        k = int(name[1])
        return tuple(int(c * (0.32 if name.endswith("_dim") else 1)) for c in B.LAYER_RGB[k])
    return {"white": (255, 255, 255), "light_gray": (200, 200, 200), "dark_gray": (80, 80, 80)}.get(name)


def main():
    if len(sys.argv) > 1:
        comp = json.loads(Path(sys.argv[1]).read_text())
    else:
        comp = requests.get("http://127.0.0.1:8080/api/v1/composition", timeout=3).json()
    cfg = B.load_config(HERE.parent.parent / "config.yaml")
    cfg["pins_file"] = str(Path(tempfile.mkdtemp()) / "pins.yaml")
    cfg["colors_file"] = str(Path(tempfile.mkdtemp()) / "colors.yaml")
    br = B.Bridge(cfg, B.Resolume("127.0.0.1", 1))                # never sends anything
    br.comp, br.online = demo_comp(comp), True
    for L in range(1, 9):                                          # nice mixer values
        p = B.master_param(br.layer_json(L))
        if p:
            p["value"] = [1.0, 0.85, 0.6, 1.0, 0.45, 0.75, 0.9, 0.3][L - 1]
    tp = br.tempo_param()
    if tp:
        tp["value"] = 128.0

    def disp():
        return B.render(br.snapshot(), bgr=False)[1]

    def pads():
        return {ij: pad_rgb(c) for ij, c in br.pad_colors().items() if pad_rgb(c)}

    CY, YE, MA, GR = (0, 190, 255), (255, 210, 0), (255, 0, 150), (0, 255, 90)
    GREEN, RED = (40, 230, 90), (255, 50, 50)

    # 1 — CLIPS
    br.sel = (7, 1)
    d = disp()
    page(HERE / "01_clips.png", 1, "CLIPS", "Your Resolume deck on 64 pads", CY,
         {"pads": CY, "Play": GREEN, "Record": RED}, pads(), d, d,
         [(235, 470, 1, CY), (235, 526 + 45, 2, CY), (-6, 1046, 3, GREEN), (-6, 968, 4, RED)],
         [(1, "Clips load from Resolume| onto the pads, one colour per layer"),
          (2, "Tap a pad| to select the clip (Resolume follows)"),
          (3, "Hold PLAY + pad| to launch the clip"),
          (4, "Hold REC + pad| to stop that layer")])

    # 2 — PARAMETERS
    br.sel, br.mode, br.page = (7, 1), "params", 0
    small = disp()
    br.move_src = 1
    big = disp()
    br.move_src = None
    _, pages = br.page_slots()
    bd = {f"Lower Row {k + 1}": (255, 255, 255) if k == 0 else (120, 120, 130) for k in range(min(8, pages))}
    page(HERE / "02_parameters.png", 2, "PARAMETERS", "Every knob, live on the display", YE,
         {"display": YE, "Upper Row 1": YE, "Convert": YE, **{f"K{k}": YE for k in range(1, 9)}, **bd},
         pads(), small, big,
         badges=[(222, 259, 1, YE), (240, 55, 2, YE), (232, 395, 3, YE), (-6, 478, 4, YE)],
         labels={f"Lower Row {k + 1}": f"PAGE {k + 1}" for k in range(8)},
         bullets=[(1, "PARAMS button| shows the clip's parameters, live"),
          (2, "8 knobs| control them, hold Shift for fine steps"),
          (3, "Page 1, Page 2…| buttons switch between pages"),
          (4, "Hold CONVERT + touch a knob| to move a parameter")])

    # 3 — COLORS
    br.sel, br.mode, br.color_idx = (7, 1), "color", 0
    cp = br.color_params()
    if cp:
        cp[0][1]["value"] = "#ff3c8cff"
    d = disp()
    sw = br.swatches()
    hl = {"Upper Row 2": MA, "K1": (255, 60, 60), "K2": (60, 230, 60), "K3": (70, 110, 255),
          "K4": MA, "K5": MA, "K6": MA, "K8": (255, 255, 255)}
    for k, c in enumerate(sw):
        hl[f"Lower Row {k + 1}"] = tuple(c) if sum(c) > 60 else (90, 90, 96)
    page(HERE / "03_colors.png", 3, "COLORS", "Dial in clip colour by hand", MA,
         hl, pads(), d, d,
         [(COLS[1] + 48, 88, 1, MA), (240, 55, 2, MA), (COLS[7] + 50, 104, 3, MA), (232, 395, 4, MA)],
         [(1, "COLOR button| opens colour control for the clip"),
          (2, "Knobs| Red / Green / Blue  +  Hue / Sat / Brightness"),
          (3, "Last knob| picks the colour: Color, BG Color…"),
          (4, "Palette| buttons, one press for a preset colour")])

    # 4 — MIX & TEMPO
    br.mode = "mix"
    d = disp()
    page(HERE / "04_mix_tempo.png", 4, "MIX & TEMPO", "Layer masters, composition master, BPM", GR,
         {"Mix": GR, "K11": GR, "K10": YE, "Tap Tempo": YE, **{f"K{k}": GR for k in range(1, 9)}},
         pads(), d, d,
         [(1334, 170, 1, GR), (240, 55, 2, GR), (1238, 55, 3, GR), (-6, 90, 4, YE)],
         [(1, "MIX button| opens the mixer"),
          (2, "8 knobs| one master per layer, top layer first"),
          (3, "Master knob| composition master"),
          (4, "TAP TEMPO| sets the BPM,  |Tempo knob| fine-tunes it")])

    page_how(HERE / "05_how_it_works.png")


if __name__ == "__main__":
    main()
