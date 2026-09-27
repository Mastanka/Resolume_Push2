"""LayerEngine + new REST calls against the mock.   python tests/mock_resolume.py &   then
python tests/test_engine.py → OK"""
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from resolume_api import Resolume, resolve_node  # noqa: E402

API = "http://127.0.0.1:8080/api/v1"
rest = Resolume("127.0.0.1", 8080)


def require_mock():
    """Never run against a real Arena: the mock answers /product with its own name."""
    try:
        name = requests.get(API + "/product", timeout=2).json().get("name")
    except Exception as e:
        sys.exit(f"mock not running on 127.0.0.1:8080 ({e}) — start tests/mock_resolume.py first")
    if name != "Mock Resolume":
        sys.exit(f"refusing to run: 127.0.0.1:8080 is {name!r}, not the mock")


def comp():
    return requests.get(API + "/composition", timeout=3).json()


def test_rest_additions():
    n0 = len(comp()["layers"])
    assert rest.add_layer() == 204
    c = comp()
    assert len(c["layers"]) == n0 + 1 and c["layers"][-1]["name"]["value"].startswith("Layer")
    L = n0 + 1
    assert rest.add_effect(L, "Crop") == 204
    assert rest.add_effect(L, "No Such Effect") == 400
    fx = comp()["layers"][L - 1]["video"]["effects"]
    assert [e["name"] for e in fx] == ["Crop"] and fx[0]["params"]["Right"]["value"] == 1920.0
    assert rest.set_effect_display_name(L, 0, "CH:T1:Bar A") == 204
    assert comp()["layers"][L - 1]["video"]["effects"][0]["display_name"] == "CH:T1:Bar A"
    assert rest.open_clip(L, 1, "source:///video/Metaballs") == 204
    clip = comp()["layers"][L - 1]["clips"][0]
    assert clip["name"]["value"] == "Metaballs" and clip["video"]["description"] == "Metaballs"
    assert "Color" in clip["video"]["sourceparams"]
    assert rest.open_clip(L, 2, "file:///Users/x/fire%201.mov") == 204
    assert comp()["layers"][L - 1]["clips"][1]["video"]["fileinfo"]["path"] == "/Users/x/fire 1.mov"
    assert rest.clear_clip(L, 2) == 204
    assert comp()["layers"][L - 1]["clips"][1]["connected"]["value"] == "Empty"
    op = resolve_node(comp()["layers"][L - 1], "video/opacity")
    before = len(requests.get(API + "/_opacity_log", timeout=3).json())
    rest.set_param(op["id"], {"value": 0.25})
    log = requests.get(API + "/_opacity_log", timeout=3).json()
    assert len(log) == before + 1 and log[-1][1] == op["id"] and log[-1][2] == 0.25
    assert rest.delete_effect(L, 0) == 204 and comp()["layers"][L - 1]["video"]["effects"] == []
    assert rest.delete_effect(L, 5) == 404
    assert rest.add_layer(before=1) == 204 and comp()["layers"][0]["name"]["value"].startswith("Layer")


def test_plugin_engine():
    from chaser_engine import PluginEngine, pad_key, pad_from_key
    assert pad_key(4) == "pad 5" and pad_from_key("pad 5") == 4 and pad_from_key("Bar A") is None
    state = {"comp": comp()}
    sent = []
    eng = PluginEngine(rest, lambda: state["comp"], lambda: state.update(comp=comp()),
                       lambda pid, v: (sent.append((pid, v)), rest.set_param(pid, {"value": v})))
    assert eng.instances() == [] and not eng.ready(0)
    assert eng.add_to_layer(1) == 204 and eng.add_to_layer(1) == 200          # second call: already there
    assert eng.set_track(2, 2)                                              # adds to layer 2, Track = 2
    inst = eng.instances()
    assert [(i["track"], i["layer"], i["clip"]) for i in inst] == [(0, 1, None), (1, 2, None)], inst
    assert eng.ready(0) and eng.ready(1) and not eng.ready(2)
    assert eng.layers_of(1) == [2] and eng.layer_name(0) == "Strobe"
    assert eng.pad_name(0, 0) == "Bar A" and eng.pad_name(0, 2) == "Bar C" and eng.pad_name(0, 5) == ""
    assert eng.pad_assigned(1, 1) and not eng.pad_assigned(2, 0)
    fx1 = state["comp"]["layers"][0]["video"]["effects"][-1]
    fx2 = state["comp"]["layers"][1]["video"]["effects"][-1]
    eng.set_level(0, "pad 1", 0.5)
    assert sent == [(fx1["params"]["Level 1"]["id"], 128 / 255)], sent          # layer 1 only, quantised
    assert comp()["layers"][0]["video"]["effects"][-1]["params"]["Level 1"]["value"] == 128 / 255
    assert comp()["layers"][1]["video"]["effects"][-1]["params"]["Level 1"]["value"] == 0.0
    eng.set_level(0, 0, 0.5); eng.set_level(0, 0, 0.7)                        # same value / too soon → skipped
    assert len(sent) == 1
    eng.set_level(0, 0, 0.0); assert len(sent) == 2 and sent[-1][1] == 0.0    # edge always sent
    eng.set_level(1, "pad 3", 1.0)
    assert sent[-1] == (fx2["params"]["Level 3"]["id"], 1.0)
    eng.set_level(3, "pad 1", 1.0); eng.set_level(0, "pad 99", 1.0)          # no instance / bad pad → nothing
    assert len(sent) == 3
    eng.all_dark()
    assert len(sent) == 3 + 48 and all(v == 0.0 for _, v in sent[3:])


def test_seq_pads_current_track_only():
    """SEQ pads flash only for the selected track, never for other tracks that are playing."""
    import tempfile
    import push_resolume_bridge as B
    cfg = B.load_config(Path(__file__).resolve().parent.parent / "config.yaml")
    for k in ("pins_file", "colors_file", "chases_file"):
        cfg[k] = str(Path(tempfile.mkdtemp()) / (k + ".yaml"))
    br = B.Bridge(cfg, Resolume("127.0.0.1", 8080))
    br.set_comp(rest.composition())
    br.seq.track = 0
    br.seq.levels = {(1, "pad 1"): 1.0, (0, "pad 2"): 1.0, (2, "pad 2"): 1.0}
    grid = br._seq_pad_colors()
    assert grid[(7, 0)] not in ("L1", "white"), grid[(7, 0)]              # pad 1 plays on track 2 only
    assert grid[(7, 1)] == "L0", grid[(7, 1)]                              # pad 2 plays on track 1
    br.seq.track = 1
    grid = br._seq_pad_colors()
    assert grid[(7, 0)] == "L1" and grid[(7, 1)] != "L1", (grid[(7, 0)], grid[(7, 1)])


if __name__ == "__main__":
    require_mock()
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("pass", name)
    print("OK")
