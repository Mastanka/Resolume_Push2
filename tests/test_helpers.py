"""Pure tests: Resolume JSON helpers, one display render per screen, the REST retry scope and the
WebSocket outbox bound. No mock needed.   python tests/test_helpers.py"""
from __future__ import annotations

import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
import requests  # noqa: E402
import urllib3  # noqa: E402
import fuzz_states as F  # noqa: E402
from display import render  # noqa: E402
from resolume_api import (Resolume, ResolumeWS, color_label, fmt_value, hex_to_rgba, resolve_node,  # noqa: E402
                          rgba_to_hex, walk)

NODE = {"video": {"Opacity": {"id": 1, "valuetype": "ParamRange", "value": 0.5, "min": 0, "max": 1},
                  "effects": [{"name": "Transform", "params": {"Scale": {"id": 2, "valuetype": "ParamRange", "value": 3, "min": 0, "max": 100}}},
                              {"name": "Transform", "params": {"Scale": {"id": 3, "valuetype": "ParamRange", "value": 4, "min": 0, "max": 100}}},
                              {"display_name": {"value": "Glow"}, "name": "x", "params": {"On": {"id": 4, "valuetype": "ParamBoolean", "value": True}}}]}}


def test_resolve_node_matches_keys_case_insensitively_and_lists_by_name_or_index():
    assert resolve_node(NODE, "VIDEO/opacity")["id"] == 1
    assert resolve_node(NODE, "video/effects/transform/params/scale")["id"] == 2
    assert resolve_node(NODE, "video/effects/glow/params/On")["id"] == 4
    assert resolve_node(NODE, "video/effects/1/params/Scale")["id"] == 3
    assert resolve_node(NODE, "video/nothing") is None and resolve_node(None, "a/b") is None


def test_walk_paths_resolve_back_to_the_same_param():
    seen = {}
    for path, p in walk(NODE):
        assert resolve_node(NODE, path) is p, path
        seen[p["id"]] = path
    assert seen[3] == "video/effects/1/params/Scale", seen        # duplicate names fall back to the index
    assert set(seen) == {1, 2, 3, 4}


def test_fmt_value_formats_each_type():
    assert fmt_value({"valuetype": "ParamBoolean"}, True, {}) == ("ON", 1.0)
    assert fmt_value({"valuetype": "ParamChoice", "options": ["a", "b", "c"]}, "c", {}) == ("c", 1.0)
    assert fmt_value({"valuetype": "ParamRange", "min": 0, "max": 1}, 0.25, {}) == ("25%", 0.25)
    assert fmt_value({"valuetype": "ParamRange", "min": -960, "max": 960}, 480, {}) == ("480", 0.75)
    assert fmt_value({"valuetype": "ParamRange", "min": 0, "max": 10}, 2.5, {}) == ("2.50", 0.25)
    assert fmt_value({"valuetype": "ParamRange", "min": 0, "max": 10}, 8, {"range": [0, 4]}) == ("8.00", 1.0)


def test_colour_helpers():
    assert color_label("video/effects/Colorize/params/Color") == "Colorize Color"
    assert color_label("video/sourceparams/BG Color") == "BG Color"
    assert color_label("video/effects/Tint/params/Tint") == "Tint"
    assert hex_to_rgba("#ff800080") == [255, 128, 0, 128] and hex_to_rgba("#ff8000") == [255, 128, 0, 255]
    assert hex_to_rgba("junk") == [0, 0, 0, 255]
    assert rgba_to_hex([255, 128, 0, 128]) == "#ff800080" and rgba_to_hex([300, -5, 0.4, 255]) == "#ff0000ff"


def test_every_screen_renders():
    br = F.new_bridge()
    br.set_comp(F.make_comp(random.Random(1), 6, 8, {1: 1, 2: 2}))
    frame, _ = render(br.snapshot())
    assert frame.shape == (960, 160)
    modes = ["clip_params", "layer_params", "color", "clip_fx", "layer_fx", "seq_env", "seq_settings",
             "seq_presets", "seq_mapping", "mix", "mute", "solo"]
    for mode in modes:
        br.mode = mode
        for extra in ({}, {"shift": True}, {"touched": 2}):
            for k, v in extra.items():
                setattr(br, k, v)
            frame, _ = render(br.snapshot())
            assert frame.shape == (960, 160), mode
            for k in extra:
                setattr(br, k, False if k == "shift" else None)
    br.mode = "color"; br.color_target = "master"
    render(br.snapshot())
    br.blackout = 0.5; render(br.snapshot()); br.blackout = None
    br.note_msg("hello"); br.paste_held = True; render(br.snapshot())
    render({**br.snapshot(), "online": False})


# ---- Resolume._req: retry a connection Arena closed without answering, nothing else ------------ #
class _Session:
    def __init__(self, errors):
        self.errors, self.calls = list(errors), 0

    def request(self, method, url, **kw):
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return "ok"


def test_req_retries_only_a_connection_closed_without_answer():
    dropped = requests.exceptions.ConnectionError(urllib3.exceptions.ProtocolError("Connection aborted.", ConnectionResetError(54, "Connection reset by peer")))
    rest = Resolume("127.0.0.1", 1)
    s = _Session([dropped])
    assert rest._req("PUT", "u", session=s) == "ok" and s.calls == 2
    refused = requests.exceptions.ConnectionError(urllib3.exceptions.MaxRetryError(None, "u", urllib3.exceptions.NewConnectionError(None, "refused")))
    s = _Session([refused, refused, refused])
    try:
        rest._req("GET", "u", session=s)
        raise AssertionError("expected the ConnectionError")
    except requests.exceptions.ConnectionError:
        pass
    assert s.calls == 1, f"a refused connection was retried {s.calls} times"
    s = _Session([requests.exceptions.ReadTimeout()])
    try:
        rest._req("PUT", "u", session=s)
        raise AssertionError("expected the timeout")
    except requests.exceptions.Timeout:
        pass
    assert s.calls == 1


# ---- ResolumeWS.set: the outbox is bounded and keeps the newest values ------------------------- #
def test_ws_outbox_is_bounded():
    ws = ResolumeWS("127.0.0.1", 1, None, None, lambda: [])
    ws.live = True
    for i in range(1000):
        ws.set(i, i / 1000)
    assert ws.outbox.qsize() <= 256, ws.outbox.qsize()
    items = []
    while not ws.outbox.empty():
        items.append(ws.outbox.get_nowait())
    assert items[-1]["parameter"].endswith("/999"), items[-1]


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            try:
                fn()
                print("pass", name)
            except Exception as e:
                failed += 1
                print("FAIL", name, "→", f"{type(e).__name__}: {e}")
    print("OK" if not failed else f"{failed} FAILED")
    sys.exit(1 if failed else 0)
