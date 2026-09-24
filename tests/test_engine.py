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


if __name__ == "__main__":
    require_mock()
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("pass", name)
    print("OK")
