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


def _bridge():
    import tempfile
    import push_resolume_bridge as B
    cfg = B.load_config(Path(__file__).resolve().parent.parent / "config.yaml")
    for k in ("pins_file", "colors_file", "chases_file"):
        cfg[k] = str(Path(tempfile.mkdtemp()) / (k + ".yaml"))
    br = B.Bridge(cfg, Resolume("127.0.0.1", 8080))
    br.set_comp(rest.composition())
    return br


def test_pad_memory():
    """Each track keeps its last pad assignment; an instance joining the track gets it."""
    import tempfile
    from sequencer import Sequencer
    from chaser_engine import PluginEngine
    store = Sequencer(Path(tempfile.mkdtemp()) / "chases.yaml")
    writes = []

    def send(pid, body):
        writes.append(pid)
        rest.set_param(pid, body)

    eng = PluginEngine(rest, comp, None, lambda pid, v: None, store=store, send_param=send)

    def fxp(L):
        return [e for e in comp()["layers"][L - 1]["video"]["effects"] if e["name"] == "Bar Chaser"][0]["params"]

    eng.sync_pads(comp(), now=100.0)                                     # first composition: taken as it is
    n0 = len(comp()["layers"])
    rest.add_layer(); rest.add_layer()
    A, B = n0 + 1, n0 + 2
    assert rest.add_effect(A, "Bar Chaser") == 204
    eng.sync_pads(comp(), now=101.0)                                     # joins track 1
    assert store.pad_configs[0] == eng.pads_of({"fx": {"params": fxp(A)}})
    rest.set_param(fxp(A)["Pad 1"]["id"], {"value": "Bar C"})           # edited in Arena
    eng.sync_pads(comp(), now=102.0)
    assert store.pad_configs[0][0] == "Bar C" and Sequencer(store.path).pad_configs[0][0] == "Bar C"
    assert rest.add_effect(B, "Bar Chaser") == 204                      # a second layer joins track 1
    stale = comp()
    eng.sync_pads(stale, now=103.0)
    assert fxp(B)["Pad 1"]["value"] == "Bar C", "the new instance must get track 1's pads"
    eng.sync_pads(stale, now=103.5)                                      # old values still in a refresh
    assert store.pad_configs[0][0] == "Bar C", "our own pending writes must not count as an edit"
    eng.sync_pads(comp(), now=104.0)
    rest.set_param(fxp(B)["Track"]["id"], {"value": "2"})              # B moves to track 2 (no pads yet)
    eng.sync_pads(comp(), now=105.0)
    assert store.pad_configs[1][0] == "Bar C"
    rest.set_param(fxp(B)["Pad 2"]["id"], {"value": "\u2014"})
    eng.sync_pads(comp(), now=106.0)
    assert store.pad_configs[1][1] == "\u2014" and store.pad_configs[0][1] == "Bar B"
    rest.set_param(fxp(A)["Track"]["id"], {"value": "2"})              # A moves to track 2 → gets its pads
    eng.sync_pads(comp(), now=107.0)
    assert fxp(A)["Pad 2"]["value"] == "\u2014"
    eng.sync_pads(comp(), now=110.0)
    loaded = comp()                                                      # another composition is loaded
    loaded["master"]["id"] = -1
    fx_b = [e for e in loaded["layers"][B - 1]["video"]["effects"] if e["name"] == "Bar Chaser"][0]
    fx_b["params"]["Pad 3"]["value"] = "Bar A"                           # B = the track's last instance
    n_writes = len(writes)
    eng.sync_pads(loaded, now=111.0)
    assert len(writes) == n_writes and store.pad_configs[1][2] == "Bar A", "a loaded composition is kept"
    assert rest.delete_effect(A, 0) in (200, 204) and rest.delete_effect(B, 0) in (200, 204)   # leave no instance


def test_fixtures():
    """MAPPING's fixture list: L<lumiverse>F<fixture> in Arena's order; pads mapped on every instance."""
    import tempfile
    from sequencer import Sequencer
    from chaser_engine import PluginEngine, fixture_list
    new = ["\u2014", "Lumiverse 1 / 1 - 423 141 RGB", "Lumiverse 1 / 424 - 846 141 RGB 2",
           "Lumiverse 2 / 1 - 855 h3 2m grb", "Lumiverse 3 / 1 - 423 141 RGB", "Lumiverse 1", "Lumiverse 2", "Lumiverse 3"]
    fx = fixture_list(new)
    assert [f["label"] for f in fx] == ["L1F1", "L1F2", "L2F1", "L3F1"], fx
    assert fx[1]["name"] == "Lumiverse 1 / 424 - 846 141 RGB 2" and fx[1]["screen"] == "Lumiverse 1"
    old = ["\u2014", "Lumiverse 2", "Lumiverse 1", "Lumiverse 3", "Lumiverse 1 / a", "Lumiverse 1 / b"]  # older build
    assert [(f["label"], f["name"]) for f in fixture_list(old)] == \
        [("L1F1", "Lumiverse 2"), ("L2F1", "Lumiverse 1 / a"), ("L2F2", "Lumiverse 1 / b"), ("L3F1", "Lumiverse 3")]
    store = Sequencer(Path(tempfile.mkdtemp()) / "chases.yaml")
    eng = PluginEngine(rest, comp, None, lambda pid, v: None, store=store, send_param=rest.set_param)
    n0 = len(comp()["layers"])
    rest.add_layer(); rest.add_layer()
    A, B = n0 + 1, n0 + 2
    assert rest.add_effect(A, "Bar Chaser") == 204 and rest.add_effect(B, "Bar Chaser") == 204   # both track 1
    assert [f["label"] for f in eng.fixtures(0)] == ["L1F1", "L1F2", "L2F1", "L3F1"]
    assert eng.short(0, "Bar B") == "L2F1" and eng.short(0, "Bar A") == "Bar A" and eng.short(0, "\u2014") == ""
    assert eng.fixtures_of_value(0, "Bar A") == [0, 1] and eng.fixtures_of_value(0, "Bar C / 1 - 423 141 RGB") == [3]
    target = "Bar A / 424 - 846 141 RGB 2"
    assert eng.set_pad(0, 3, target)
    pads = lambda L: [e for e in comp()["layers"][L - 1]["video"]["effects"] if e["name"] == "Bar Chaser"][0]["params"]
    assert pads(A)["Pad 4"]["value"] == target and pads(B)["Pad 4"]["value"] == target, "every instance of the track"
    assert store.pad_configs[0][3] == target and Sequencer(store.path).pad_configs[0][3] == target
    assert not eng.set_pad(0, 4, "No such fixture") and not eng.set_pad(2, 0, target)        # unknown / no instance
    assert rest.delete_effect(A, 0) in (200, 204) and rest.delete_effect(B, 0) in (200, 204)   # leave no instance


def test_global_group_isolation():
    """A global group used on track 2 only puts steps on track 2: it never plays track 1's layers."""
    br = _bridge()
    br.mode = "seq"
    br.sel_pads = {0, 4}
    br.button("Select", True); br.button("Shift", True)
    br.button("1/32t", True); br.button("1/32t", False)                 # Select + Shift + button 1 = GG1
    br.button("Shift", False)
    br.sel_pads = {1}
    br.button("1/32", True); br.button("1/32", False)                   # Select + button 2 = T1 G2
    br.button("Select", False)
    assert br.seq.groups[0] == [0, 4] and br.seq.track_groups[0][1] == [1] and not br.multi
    leds = br.button_colors()
    assert leds["1/32t"] == "dark_gray" and leds["1/32"] == "L0", leds      # GG1 stored, T1 G2 current
    br.button("Lower Row 2", True); br.button("Lower Row 2", False)      # track 2
    leds = br.button_colors()
    assert leds["1/32t"] == "dark_gray" and leds["1/32"] == "black", "T1's group must not show on T2"
    br.button("1/32t", True); br.button("1/32t", False)                 # recall GG1 on track 2
    assert br.sel_pads == {0, 4} and br.button_colors()["1/32t"] == "white"
    br.pad_pressed((0, 0), 127); br.pad_released((0, 0))               # step 1 on pads 1 + 5
    assert set(br.seq.pattern.tracks[1].steps) == {"pad 1", "pad 5"} and not br.seq.pattern.tracks[0].steps
    br.seq.start(0.0)
    levels = br.seq.tick(0.01)
    assert levels and all(t == 1 for t, _ in levels), levels


def test_screens():
    """Two views with their own menus; Mix / Mute / Solo: click = open, click again = back, hold = peek."""
    br = _bridge()

    def click(name):
        br.button(name, True); br.button(name, False)

    assert br.view == "clip" and br.mode == "clip_params"
    br.mode = "seq"; assert br.view == "seq" and br.mode == "seq_env"              # legacy names still work
    br.mode = "params"; assert br.view == "clip" and br.mode == "clip_params"
    br.page = 1; br.mode = "layer_params"; assert br.page == 0                        # page per menu
    br.mode = "clip_params"; assert br.page == 1
    click("Mix"); assert br.mode == "mix"                                             # click: stays
    click("Mix"); assert br.mode == "clip_params"                                     # click again: back
    br.button("Mix", True); br.ov_press["mix"][0] -= 1; br.button("Mix", False)       # held 1 s: back
    assert br.mode == "clip_params"
    br.button("Mute", True); assert br.mode == "mute" and br.mute_held
    br.turn(0, 1); br.button("Mute", False)                                           # short, but used = hold
    assert br.mode == "clip_params" and not br.mute_held
    click("Mix"); click("Mute"); assert br.mode == "mute"                             # MUTE on top of MIX
    click("Mute"); assert br.mode == "mix"
    click("Mix"); assert br.mode == "clip_params" and br.overlay is None
    click("Mix"); br.button("Upper Row 2", True); assert br.mode == "color" and br.overlay is None
    br.button("Upper Row 4", True); assert br.mode == "color"                         # empty menu button
    br.button("Note", True); assert br.view == "seq" and br.mode == "seq_env"
    br.button("Upper Row 2", True); assert br.mode == "seq_settings"
    br.button("Upper Row 6", True); assert br.mode == "seq_settings"                  # no 6th SEQ menu
    click("Solo"); assert br.mode == "solo" and br.view == "seq"                      # SOLO on top of SEQ
    br.button("Session", True); assert br.mode == "color" and br.overlay is None      # each view keeps its menu
    br.button("Note", True); assert br.mode == "seq_settings"
    leds = br.button_colors()
    assert leds["Upper Row 2"] == "L6" and leds["Upper Row 1"] == "L6_dim" and leds["Upper Row 4"] == "L6_dim"
    assert leds["Upper Row 5"] == "black"
    br.button("Session", True)
    leds = br.button_colors()
    assert leds["Upper Row 2"] == "white" and leds["Upper Row 6"] == "dark_gray" and leds["Upper Row 8"] == "black"


def test_seq_pads_current_track_only():
    """SEQ pads flash only for the selected track, never for other tracks that are playing."""
    br = _bridge()
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
